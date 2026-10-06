"""Sign in / sign up.

One flow for everybody. A code proves control of an email address; whether that
person is an ICU member is then decided purely by looking the *verified* address
up in the synced membership feed. There is no separate signup endpoint — an
account is created as a side effect of the first successful verification.

Membership is never inferred from the email domain: alumni and staff hold
@imperial.ac.uk addresses without being members, and a member's ICU record may
carry a personal address.
"""

from __future__ import annotations

import asyncio

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from clients import database
from config import settings
from models.auth import AuthSession, CodeRequest, CodeVerify, SessionView
from utils import email as mailer
from utils.auth import (
    clear_session_cookies,
    codes_equal,
    generate_login_code,
    generate_token,
    hash_token,
    normalise_email,
    set_session_cookies,
)
from services.identity import resolve_or_create_user
from utils.deps import (
    client_ip,
    committee_card,
    current_session,
    require_feature,
    require_session,
    verify_csrf,
)
from utils.year import academic_year

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/auth")


def _accepted() -> dict:
    return {
        "ok": True,
        "message": "If that address can sign in, a code is on its way.",
        "expires_in_minutes": settings.login_code_ttl_minutes,
    }


# asyncio only holds a weak reference to a running task, so a fire-and-forget
# coroutine can be garbage collected mid-flight. Keeping the reference until it
# finishes is the documented way to avoid that.
_background: set[asyncio.Task] = set()


def _dispatch(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


@router.post(
    "/request-code",
    status_code=202,
    dependencies=[require_feature("login")],
)
async def request_code(body: CodeRequest, request: Request) -> dict:
    """Email a sign-in code.

    Always 202, whatever happens. Any other answer turns this into an oracle for
    "does this address have an account", and worse "is this person an EESoc
    member" — which is personal information about somebody else.
    """
    address = normalise_email(str(body.email))

    issued = await database.get(
        "/auth/login-codes/recent-count", {"email": address, "seconds": 3600}
    )
    if issued is not None and issued >= settings.login_code_max_per_hour:
        log.warning("Rate limit hit for %s from %s", address, client_ip(request))
        return _accepted()

    # A resend inside the cooldown succeeds without sending, so the button
    # cannot be used to bombard an address.
    active = await database.get("/auth/login-codes/active", {"email": address})
    if active is not None:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(active["created_at"])
        if age < timedelta(seconds=settings.login_code_resend_seconds):
            return _accepted()

    code = generate_login_code()
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.login_code_ttl_minutes
    )
    await database.post(
        "/auth/login-codes",
        {
            "email": address,
            "code_hash": hash_token(code),
            "expires_at": expires.isoformat(),
            "request_ip": client_ip(request),
        },
    )
    # Dispatched, not awaited. This endpoint's contract is an immediate 202
    # whatever happens — that is what stops it being an oracle for "does this
    # address have an account". Awaiting the send breaks that: Exchange being
    # slow or unreachable would hang the request for the full retry budget
    # (~45s) and surface as a gateway timeout, so the one path that must never
    # vary would be the slowest thing on the site. `send` already swallows its
    # own errors and logs them.
    _dispatch(mailer.send_login_code(address, code, settings.login_code_ttl_minutes))
    return _accepted()


@router.post("/verify-code", dependencies=[require_feature("login")])
async def verify_code(
    body: CodeVerify, response: Response, request: Request
) -> SessionView:
    """Check the code, then resolve-or-create the account and open a session."""
    address = normalise_email(str(body.email))

    active = await database.get("/auth/login-codes/active", {"email": address})
    if active is None:
        raise HTTPException(400, "That code has expired. Ask for a new one.")

    if active["attempts"] >= settings.login_code_max_attempts:
        await database.post(f"/auth/login-codes/{active['id']}/consume")
        raise HTTPException(429, "Too many attempts. Ask for a new code.")

    if not codes_equal(body.code, active["code_hash"]):
        await database.post(f"/auth/login-codes/{active['id']}/attempt")
        raise HTTPException(400, "That code is not right.")

    # Single-use: burn it before anything else, so replaying the same request
    # cannot open a second session.
    await database.post(f"/auth/login-codes/{active['id']}/consume")

    # Verified: they have just proved they control this address.
    user = await resolve_or_create_user(
        address, body.first_name, body.last_name, verified=True
    )
    tokens = await _open_session(user["id"], response)

    await database.post(f"/auth/users/{user['id']}/touch-login")
    await _record_standing_superuser(user)
    await _bootstrap_first_admin(user["id"])

    full = await database.get(
        "/auth/sessions/user", {"token_hash": tokens["token_hash"]}
    )
    return await _view(AuthSession(**full), csrf_token=tokens["csrf_token"])


async def _open_session(user_id: int, response: Response) -> dict:
    token = generate_token()
    csrf_token = generate_token()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days)

    await database.post(
        "/auth/sessions",
        {
            "token_hash": hash_token(token),
            "csrf_token": csrf_token,
            "user_id": user_id,
            "expires_at": expires.isoformat(),
            "impersonator_id": None,
        },
    )
    set_session_cookies(response, token, csrf_token)
    return {"token_hash": hash_token(token), "csrf_token": csrf_token}


async def _record_standing_superuser(user: dict) -> None:
    """Write the row for the configured superuser the first time they sign in.

    The migration seeds this, but only if the account already exists — and on a
    fresh database it does not: the account is created by the sign-in that is
    happening right now. The api would treat them as a superuser either way
    (the pin in `models.auth`), but with no row they are missing from the
    People tab, which reads as "nobody has the keys".

    One write, for one address, once per sign-in.
    """
    configured = (settings.superuser_email or "").strip().lower()
    if not configured or str(user.get("email", "")).strip().lower() != configured:
        return
    await database.post(
        f"/auth/admins/{user['id']}",
        params={"role": "superuser", "note": "standing superuser"},
    )


async def _bootstrap_first_admin(user_id: int) -> None:
    """A fresh deployment has no admins and no way to create one through the UI.

    The first *committee member* to sign in is promoted — gated on the committee
    feed, so it is not simply "whoever logs in first", and it stops as soon as
    one admin exists.
    """
    count = await database.get("/auth/admins/count")
    if count:
        return

    user = await database.get(f"/auth/users/{user_id}")
    if not user or user.get("member_id") is None:
        return

    # Look the member up by the id already on the account, not by re-searching
    # on email: the stored address is canonicalised to shortcode@ic.ac.uk and no
    # longer matches what the members feed holds.
    member = await database.get(f"/union/members/by-id/{user['member_id']}")
    if member is None:
        return

    committee = (
        await database.get("/union/committee", {"academic_year": academic_year()}) or []
    )
    if any(c["cid"] == member["cid"] for c in committee):
        # Superuser, not admin: on a fresh deployment this is the only account
        # there is, and an admin cannot grant roles — it would be a committee
        # with the keys locked inside. The standing superuser address overrides
        # this on every request anyway, so in practice this only matters for a
        # deployment configured with a different one.
        await database.post(
            f"/auth/admins/{user_id}",
            params={"role": "superuser", "note": "auto: first committee sign-in"},
        )
        log.warning("Promoted user %s to superuser (first committee sign-in)", user_id)


async def _view(session: AuthSession, csrf_token: str | None = None) -> SessionView:
    """Flatten a session into what the header needs.

    `roles` is a list rather than API.md's single `role`, because the three are
    not mutually exclusive: a committee member is usually also a member, and an
    admin may hold a sponsor account too. The *committee* role — superuser,
    admin, events, industry — is the separate `role` field, and is at most one.
    """
    year = academic_year()
    is_member = session.member is not None and session.member.academic_year == year

    roles = []
    if is_member:
        roles.append("member")
    if session.is_admin:
        roles.append("committee")
    if session.sponsor_ids:
        roles.append("sponsor")

    # Which committee card is theirs to edit, if any. Costs one cached roster
    # read and is what lets an ordinary committee member — no role at all —
    # reach the editor for their own entry.
    card = await committee_card(session)

    user = session.user
    name = " ".join(p for p in [user.first_name, user.last_name] if p) or None
    return SessionView(
        authenticated=True,
        user={
            "id": user.id,
            "email": user.email,
            "name": name,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "cid": session.member.cid if session.member else None,
            "login": session.member.login if session.member else None,
        },
        roles=roles,
        is_member=is_member,
        is_admin=session.is_admin,
        is_sponsor=bool(session.sponsor_ids),
        sponsor_ids=session.sponsor_ids,
        role=session.role,
        capabilities=sorted(session.capabilities),
        committee_login=(card or {}).get("login"),
        impersonating=session.is_impersonating,
        impersonator_email=(
            str(session.impersonator.email) if session.impersonator else None
        ),
        academic_year=year,
        # Members get their name from the Union feed and sponsors are set up by
        # the committee, so neither is ever asked.
        needs_name=not is_member
        and not session.sponsor_ids
        and not (user.first_name or "").strip(),
        csrf_token=csrf_token,
        dev_login=settings.dev_login_enabled and settings.environment == "development",
    )


@router.get("/session")
async def get_session(
    session: AuthSession | None = Depends(current_session),
) -> SessionView:
    if session is None:
        return SessionView(
            authenticated=False,
            academic_year=academic_year(),
            dev_login=(
                settings.dev_login_enabled and settings.environment == "development"
            ),
        )
    return await _view(session, csrf_token=session.session.csrf_token)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(response: Response, request: Request) -> dict:
    token = request.cookies.get(settings.session_cookie)
    if token:
        await database.delete("/auth/sessions", {"token_hash": hash_token(token)})
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/logout-everywhere", dependencies=[Depends(verify_csrf)])
async def logout_everywhere(
    response: Response, session: AuthSession = Depends(require_session)
) -> dict:
    await database.delete(f"/auth/sessions/for-user/{session.user.id}")
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/stop-impersonating", dependencies=[Depends(verify_csrf)])
async def stop_impersonating(
    response: Response,
    request: Request,
    session: AuthSession = Depends(require_session),
) -> SessionView:
    """Hand the browser back to whoever started this.

    Lives on /auth rather than /admin because the session it is called from is
    the *impersonated* one — which, by design, has none of the superuser's
    access. Gating it on anything the superuser holds would strand them as a
    first-year with no way back but clearing cookies.

    The impersonated session is deleted rather than left to expire: leaving a
    live token for somebody else's account lying around for a week is the one
    thing this feature must not do.
    """
    if not session.is_impersonating or session.impersonator is None:
        raise HTTPException(400, "You are not impersonating anybody.")

    was = session.user.email
    back_to = session.impersonator.id

    token = request.cookies.get(settings.session_cookie)
    if token:
        await database.delete("/auth/sessions", {"token_hash": hash_token(token)})

    tokens = await _open_session(back_to, response)
    full = await database.get(
        "/auth/sessions/user", {"token_hash": tokens["token_hash"]}
    )
    log.warning(
        "IMPERSONATION ENDED: %s is no longer signed in as %s",
        session.impersonator.email,
        was,
    )
    return await _view(AuthSession(**full), csrf_token=tokens["csrf_token"])


@router.patch("/me", dependencies=[Depends(verify_csrf)])
async def update_me(
    first_name: str | None = None,
    last_name: str | None = None,
    session: AuthSession = Depends(require_session),
) -> dict:
    """Lets somebody supply the name we never had."""
    return await database.patch(
        f"/auth/users/{session.user.id}",
        params={
            "first_name": first_name,
            "last_name": last_name,
        },
    )


# --------------------------------------------------------------- development


@router.post("/dev-login")
async def dev_login(
    response: Response,
    email: str = Body(embed=True),
    make_admin: bool = Body(default=False, embed=True),
) -> SessionView:
    """Mint a session from an email with no code. **Development only.**

    This exists so the rest of the site is testable before SMTP credentials are
    in place. It is a complete authentication bypass, so it is gated twice: the
    flag must be on AND the environment must be "development". `main.py` refuses
    to start if the flag is set anywhere else.
    """
    if not (settings.dev_login_enabled and settings.environment == "development"):
        raise HTTPException(404, "Not found")

    address = normalise_email(email)
    log.warning("DEV LOGIN used for %s — this must never be enabled in production", address)

    user = await resolve_or_create_user(address, verified=True)
    tokens = await _open_session(user["id"], response)
    await database.post(f"/auth/users/{user['id']}/touch-login")
    await _record_standing_superuser(user)

    if make_admin:
        await database.post(f"/auth/admins/{user['id']}", params={"note": "dev-login"})

    full = await database.get("/auth/sessions/user", {"token_hash": tokens["token_hash"]})
    return await _view(AuthSession(**full), csrf_token=tokens["csrf_token"])


@router.get("/dev-code")
async def dev_last_code(email: str) -> dict:
    """Reveal the live code for an address, so the real flow can be exercised
    end to end without a mail server. Same double gate as dev-login."""
    if not (settings.dev_login_enabled and settings.environment == "development"):
        raise HTTPException(404, "Not found")
    active = await database.get("/auth/login-codes/active", {"email": normalise_email(email)})
    if active is None:
        raise HTTPException(404, "No live code for that address")
    return {
        "email": email,
        "expires_at": active["expires_at"],
        "note": "The code itself is hashed at rest; read it from the api log.",
    }
