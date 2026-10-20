"""Passkeys: signing in with a device instead of an emailed code.

Three ways a passkey is used or made, one WebAuthn ceremony each:

  sign in       POST /auth/passkey/options  ->  POST /auth/passkey/verify
                Anybody. Discoverable credentials, so nobody types an
                address: the passkey says whose it is.
  add your own  POST /auth/passkeys/options ->  POST /auth/passkeys
                Signed in. Offered straight after a code sign-in, and on the
                account page.
  via a link    POST /auth/passkey-link -> /options -> /register
                Whoever holds a link the committee made. For the people our
                codes never reach — a sponsor behind a corporate spam filter,
                mostly. Saving the passkey signs them in.

The committee's half — making, listing and revoking links — is under
/admin/passkey-links, on the `passkey_links` capability (admin, superuser).

Checked every time, by py_webauthn: the answer signs a challenge we issued,
from this site's origin, for this relying party, with the key we hold, and a
hardware key's counter has gone up. Checked here: the challenge is unexpired
and unused (reading it deletes it), and it was issued for this purpose, this
account and — for a link — this link.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    generate_challenge,
    generate_user_handle,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from clients import database
from config import settings
from models.auth import AuthSession, SessionView
from routes.auth import finish_sign_in
from services.identity import refresh_membership, resolve_or_create_user
from utils import roles as role_table
from utils.auth import generate_token, hash_token, normalise_email
from utils.deps import (
    require_action,
    require_admin,
    require_feature,
    require_passkey_links,
    require_session,
    verify_csrf,
)

log = logging.getLogger("api.passkeys")

router = APIRouter(prefix="/auth")

# Same gates as the rest of /admin: some committee role, and the dashboard not
# switched off by hand. The capability itself is checked per endpoint.
admin_router = APIRouter(
    prefix="/admin/passkey-links",
    dependencies=[Depends(require_admin), require_feature("admin")],
)

# A passkey cannot say whether the person in front of it is who it belongs to
# unless the authenticator checks (fingerprint, face, PIN). Most do; asking for
# it rather than demanding it keeps a plain security key usable, and a passkey
# without it is still possession of a device — no weaker than possession of a
# mailbox, which is what a code proves.
_UV = UserVerificationRequirement.PREFERRED

_TRANSPORTS = {t.value for t in AuthenticatorTransport}

_UNREADABLE = "That passkey response could not be read. Try again."
_STALE = "That took too long, or was already used. Try again."


# --------------------------------------------------------------- relying party


def rp_id() -> str:
    """The host passkeys are bound to — site_origin's, unless set explicitly."""
    return settings.webauthn_rp_id or urlsplit(settings.site_origin).hostname or "localhost"


def expected_origins() -> list[str]:
    extra = [o.strip() for o in settings.webauthn_extra_origins.split(",") if o.strip()]
    return [o.rstrip("/") for o in [settings.site_origin, *extra]]


def _timeout_ms() -> int:
    return settings.passkey_challenge_seconds * 1000


# ------------------------------------------------------------------ challenges


async def _issue_challenge(
    purpose: str, user_id: int | None = None, link_id: int | None = None
) -> bytes:
    challenge = generate_challenge()
    expires = datetime.now(timezone.utc) + timedelta(
        seconds=settings.passkey_challenge_seconds
    )
    await database.post(
        "/passkeys/challenges",
        {
            "challenge": bytes_to_base64url(challenge),
            "purpose": purpose,
            "user_id": user_id,
            "link_id": link_id,
            "expires_at": expires.isoformat(),
        },
    )
    return challenge


def _answered_challenge(credential: dict) -> str:
    """The challenge the browser says it signed, read out of clientDataJSON.

    Only used to find the challenge row. Whether the answer really signs it is
    py_webauthn's check, against the stored value, not this one.
    """
    try:
        client_data = json.loads(
            base64url_to_bytes(credential["response"]["clientDataJSON"])
        )
        challenge = client_data["challenge"]
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, _UNREADABLE)
    if not isinstance(challenge, str) or not challenge:
        raise HTTPException(400, _UNREADABLE)
    return challenge


async def _consume(credential: dict, purpose: str) -> dict:
    """Use up the challenge this answer signed, or refuse.

    Consumed before anything else is checked, so an answer is good for one try
    whether or not that try succeeds.
    """
    row = await database.post(
        "/passkeys/challenges/consume",
        {"challenge": _answered_challenge(credential), "purpose": purpose},
    )
    if row is None:
        raise HTTPException(400, _STALE)
    return row


# --------------------------------------------------------------- registration


async def _registration_options(user: dict, challenge: bytes) -> dict:
    """Creation options for an account, in the JSON form the browser takes."""
    handle = await database.post(
        f"/passkeys/handle/{user['id']}",
        params={"candidate": bytes_to_base64url(generate_user_handle())},
    )
    existing = await database.get(f"/passkeys/for-user/{user['id']}") or []
    name = " ".join(
        p for p in [user.get("first_name"), user.get("last_name")] if p
    )
    options = generate_registration_options(
        rp_id=rp_id(),
        rp_name=settings.webauthn_rp_name,
        user_id=base64url_to_bytes(handle),
        user_name=user["email"],
        user_display_name=name or user["email"],
        challenge=challenge,
        timeout=_timeout_ms(),
        attestation=AttestationConveyancePreference.NONE,
        # Discoverable ("resident") keys are what let sign-in skip the email
        # field entirely — the passkey carries the account with it.
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=_UV,
        ),
        # The authenticator refuses to make a second passkey for an account it
        # already holds one for, rather than leaving two that look the same.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(p["credential_id"]),
                transports=[
                    AuthenticatorTransport(t) for t in p.get("transports") or []
                    if t in _TRANSPORTS
                ],
            )
            for p in existing
        ],
    )
    return json.loads(options_to_json(options))


def _verify_registration(credential: dict, challenge: str):
    try:
        return verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id(),
            expected_origin=expected_origins(),
        )
    except (WebAuthnException, KeyError, TypeError, ValueError) as exc:
        log.warning("Passkey registration refused: %s", exc)
        raise HTTPException(400, "That passkey could not be saved. Try again.")


def _clean_name(name: str | None) -> str:
    return " ".join((name or "").split())[:80]


async def _save(
    user_id: int,
    verified,
    credential: dict,
    name: str | None,
    link_id: int | None = None,
) -> dict:
    response = credential.get("response") or {}
    transports = [
        t for t in (response.get("transports") or [])
        if isinstance(t, str) and t in _TRANSPORTS
    ]
    try:
        return await database.post(
            "/passkeys",
            {
                "user_id": user_id,
                "credential_id": bytes_to_base64url(verified.credential_id),
                "public_key": bytes_to_base64url(verified.credential_public_key),
                "sign_count": verified.sign_count,
                "transports": transports,
                "aaguid": str(verified.aaguid) if verified.aaguid else None,
                "device_type": getattr(
                    verified.credential_device_type, "value",
                    verified.credential_device_type,
                ),
                "backed_up": bool(verified.credential_backed_up),
                "name": _clean_name(name),
                "link_id": link_id,
            },
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            raise HTTPException(409, "That passkey is already saved.")
        if exc.status_code == 410:
            raise HTTPException(410, _LINK_GONE)
        raise


def _present(row: dict) -> dict:
    """A passkey as the account page shows it. No key material leaves."""
    maker = " ".join(
        p for p in [
            row.get("link_created_by_first_name"), row.get("link_created_by_last_name")
        ] if p
    ) or row.get("link_created_by_email")
    return {
        "id": row["id"],
        "name": row.get("name") or "Passkey",
        "createdAt": row.get("created_at"),
        "lastUsedAt": row.get("last_used_at"),
        # Synced through a password manager, so it is on their other devices
        # too — worth saying, because deleting it deletes it everywhere.
        "synced": bool(row.get("backed_up")),
        "viaLink": row.get("link_id") is not None,
        "linkMadeBy": maker if row.get("link_id") is not None else None,
    }


def _refuse_while_impersonating(session: AuthSession) -> None:
    """A passkey outlives the session it was made in. Saved while a superuser
    is signed in as somebody else, it would be a way into that account with
    no banner and no end — exactly what impersonation is built not to be."""
    if session.is_impersonating:
        raise HTTPException(
            403, "Not while you are signed in as somebody else. Stop impersonating first."
        )


# -------------------------------------------------------------------- sign in


class PasskeyAnswer(BaseModel):
    # The browser's PublicKeyCredential in its JSON form (base64url fields), as
    # py_webauthn parses it.
    credential: dict
    # "Chrome on macOS" — what the account page will call it.
    name: str | None = Field(default=None, max_length=120)


@router.post(
    "/passkey/options",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def sign_in_options() -> dict:
    """A challenge to sign. No account named: any of this site's passkeys can
    answer it, and the answer says whose it is."""
    challenge = await _issue_challenge("authenticate")
    options = generate_authentication_options(
        rp_id=rp_id(),
        challenge=challenge,
        timeout=_timeout_ms(),
        user_verification=_UV,
    )
    return json.loads(options_to_json(options))


@router.post(
    "/passkey/verify",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def sign_in(body: PasskeyAnswer, response: Response) -> SessionView:
    row = await _consume(body.credential, "authenticate")

    credential_id = body.credential.get("id")
    if not isinstance(credential_id, str) or not credential_id:
        raise HTTPException(400, _UNREADABLE)
    passkey = await database.get("/passkeys/credential", {"credential_id": credential_id})
    if passkey is None:
        # Removed from the account, or saved against a different site (a
        # staging passkey tried on eesoc.com). Either way the device still
        # offers it, so say what to do rather than just "no".
        raise HTTPException(
            400,
            "That passkey isn't set up on this site any more. Sign in with an "
            "emailed code instead — you can save a new passkey afterwards.",
        )

    # The user handle, when the authenticator returns one, must be this
    # account's. py_webauthn does not check it; the credential lookup already
    # pins the account, so this is a second lock, not the first.
    handle = (body.credential.get("response") or {}).get("userHandle")
    if handle and passkey.get("webauthn_handle") and handle != passkey["webauthn_handle"]:
        log.warning("Passkey %s answered with a foreign user handle", passkey["id"])
        raise HTTPException(400, "That passkey could not be verified.")

    try:
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(row["challenge"]),
            expected_rp_id=rp_id(),
            expected_origin=expected_origins(),
            credential_public_key=base64url_to_bytes(passkey["public_key"]),
            credential_current_sign_count=passkey["sign_count"],
        )
    except (WebAuthnException, KeyError, TypeError, ValueError) as exc:
        log.warning("Passkey sign-in refused for user %s: %s", passkey["user_id"], exc)
        raise HTTPException(
            400, "That passkey could not be verified. Try again, or sign in with an emailed code."
        )

    await database.post(
        f"/passkeys/{passkey['id']}/used", {"sign_count": verified.new_sign_count}
    )
    user = await database.get(f"/auth/users/{passkey['user_id']}")
    if user is None:
        raise HTTPException(400, "That account no longer exists.")
    user = await refresh_membership(user)
    return await finish_sign_in(user, response)


# ------------------------------------------------------------ your own passkeys


@router.get("/passkeys")
async def my_passkeys(session: AuthSession = Depends(require_session)) -> dict:
    rows = await database.get(f"/passkeys/for-user/{session.user.id}") or []
    return {"passkeys": [_present(r) for r in rows]}


@router.post(
    "/passkeys/options",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def add_passkey_options(session: AuthSession = Depends(require_session)) -> dict:
    _refuse_while_impersonating(session)
    challenge = await _issue_challenge("register", user_id=session.user.id)
    return await _registration_options(session.user.model_dump(mode="json"), challenge)


@router.post(
    "/passkeys",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def add_passkey(
    body: PasskeyAnswer, session: AuthSession = Depends(require_session)
) -> dict:
    _refuse_while_impersonating(session)
    row = await _consume(body.credential, "register")
    # Issued to this account by the call above — not to a link, and not to
    # whoever was signed in on this browser a minute ago.
    if row["user_id"] != session.user.id or row["link_id"] is not None:
        raise HTTPException(400, _STALE)
    verified = _verify_registration(body.credential, row["challenge"])
    saved = await _save(session.user.id, verified, body.credential, body.name)
    log.info("Passkey %s saved by user %s", saved["id"], session.user.id)
    return {"ok": True, "passkey": _present(saved)}


@router.delete("/passkeys/{passkey_id}", dependencies=[Depends(verify_csrf)])
async def remove_passkey(
    passkey_id: int, session: AuthSession = Depends(require_session)
) -> dict:
    """Always allowed for your own: the emailed code is still there to fall
    back on, so nobody can lock themselves out by tidying up."""
    _refuse_while_impersonating(session)
    mine = await database.get(f"/passkeys/for-user/{session.user.id}") or []
    if not any(p["id"] == passkey_id for p in mine):
        raise HTTPException(404, "No such passkey on your account.")
    await database.delete(f"/passkeys/{passkey_id}", {"user_id": session.user.id})
    log.info("Passkey %s removed by user %s", passkey_id, session.user.id)
    return {"ok": True}


# ---------------------------------------------------------------------- links
#
# Whoever holds a link can put a passkey on the account it names and walk in.
# So the rule for who may make one is checked twice: when it is made, and again
# when it is used — a link is honoured only if its maker could still make it
# today. That closes the slow route around the rule, where an admin links an
# ordinary account and it is given a committee role afterwards.

_LINK_GONE = (
    "This link has expired or has already been used. Ask whoever sent it to "
    "make you a new one."
)


def _is_standing(email: str | None) -> bool:
    configured = (settings.superuser_email or "").strip().lower()
    return bool(configured) and str(email or "").strip().lower() == configured


def _link_refusal(
    maker_role: str | None, target_email: str, target_role: str | None,
    maker_permissions: list[str] | None = None,
) -> str | None:
    """Why this maker may not give a way into this account, or None if they may.

      * making links at all is the `passkey_links` permission;
      * the standing superuser's account takes no links from anybody: it is
        the one account that can hand out every other kind of access;
      * an account holding any committee role needs `users` too — otherwise an
        admin could link a superuser's account and open it themselves.
    """
    caps = role_table.effective_capabilities(maker_role, maker_permissions)
    if "passkeys.create" not in caps:
        return "You do not have permission to make passkey links."
    if _is_standing(target_email):
        return "The standing superuser sets up their own passkeys."
    if target_role and "access.grant" not in caps:
        return (
            "That account has committee access, so permission to manage access is required to make "
            "a passkey link for it."
        )
    return None


async def _committee_role(user_id: int) -> str | None:
    people = await database.get("/auth/admins") or []
    return next((a["role"] for a in people if a["user_id"] == user_id), None)


async def _live_link(token: str) -> dict:
    link = await database.get(
        "/passkeys/links/by-token", {"token_hash": hash_token(token)}
    )
    if link is None:
        raise HTTPException(404, _LINK_GONE)

    maker_role = (
        "superuser" if _is_standing(link.get("created_by_email"))
        else link.get("created_by_role")
    )
    refusal = _link_refusal(
        maker_role, link["email"], link.get("user_role"),
        None if _is_standing(link.get("created_by_email")) else link.get("created_by_permissions"),
    )
    if link.get("created_by") is None or refusal:
        log.warning(
            "Refused passkey link %s for %s made by %s: %s",
            link["id"], link["email"], link.get("created_by_email"),
            refusal or "its maker's account is gone",
        )
        raise HTTPException(
            403, "This link can no longer be used. Ask the committee to make you a new one."
        )
    return link


class LinkToken(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class LinkAnswer(LinkToken):
    credential: dict
    name: str | None = Field(default=None, max_length=120)


@router.post(
    "/passkey-link",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def inspect_link(body: LinkToken) -> dict:
    """What the link is for, so the page can say so before anything happens.

    POST, with the token in the body rather than the URL, so it stays out of
    access logs; the page itself carries it in the fragment, which the browser
    never sends. Reading it changes nothing — a mail scanner that somehow got
    this far still has not used the link.
    """
    link = await _live_link(body.token)
    maker = " ".join(
        p for p in [link.get("created_by_first_name"), link.get("created_by_last_name")] if p
    )
    return {
        "email": link["email"],
        "expiresAt": link["expires_at"],
        # The committee member's name, not their address: names are on the
        # committee page already, and it tells the recipient the link is real.
        "madeBy": maker or None,
    }


@router.post(
    "/passkey-link/options",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def link_options(body: LinkToken) -> dict:
    link = await _live_link(body.token)
    user = await database.get(f"/auth/users/{link['user_id']}")
    if user is None:
        raise HTTPException(404, _LINK_GONE)
    challenge = await _issue_challenge("register", user_id=user["id"], link_id=link["id"])
    return await _registration_options(user, challenge)


@router.post(
    "/passkey-link/register",
    dependencies=[Depends(verify_csrf), require_feature("passkeys")],
)
async def link_register(body: LinkAnswer, response: Response) -> SessionView:
    """Save the passkey, spend the link, and sign them in.

    The challenge must have been issued for this very link: an answer obtained
    through one link cannot be redeemed against another. The link is spent in
    the same transaction that saves the passkey, so two tabs racing get one
    passkey between them.
    """
    link = await _live_link(body.token)
    row = await _consume(body.credential, "register")
    if row["link_id"] != link["id"] or row["user_id"] != link["user_id"]:
        raise HTTPException(400, _STALE)
    verified = _verify_registration(body.credential, row["challenge"])
    await _save(link["user_id"], verified, body.credential, body.name, link_id=link["id"])

    log.warning(
        "PASSKEY LINK USED: a passkey was saved for %s through link %s, made by %s",
        link["email"], link["id"], link.get("created_by_email"),
    )
    user = await database.get(f"/auth/users/{link['user_id']}")
    user = await refresh_membership(user)
    return await finish_sign_in(user, response)


# --------------------------------------------------------- committee: links


def _present_link(row: dict) -> dict:
    status = (
        "live" if row.get("live")
        else "used" if row.get("used_at")
        else "revoked" if row.get("revoked_at")
        else "expired"
    )
    return {
        "id": row["id"],
        "email": row["email"],
        "status": status,
        "createdAt": row.get("created_at"),
        "createdBy": row.get("created_by_email"),
        "expiresAt": row.get("expires_at"),
        "usedAt": row.get("used_at"),
        "revokedAt": row.get("revoked_at"),
        "revokedBy": row.get("revoked_by_email"),
    }


@admin_router.get("", dependencies=[Depends(require_action("passkeys.view"))])
async def list_links(_: AuthSession = Depends(require_passkey_links)) -> dict:
    """Live links, and anything made, used or revoked in the last month."""
    rows = await database.get("/passkeys/links") or []
    return {
        "links": [_present_link(r) for r in rows],
        "ttlHours": settings.passkey_link_ttl_hours,
    }


@admin_router.post("", dependencies=[Depends(require_action("passkeys.create")), Depends(verify_csrf)])
async def create_link(
    email: str = Body(embed=True),
    session: AuthSession = Depends(require_passkey_links),
) -> dict:
    """Make a passkey link for an address, creating the account if need be.

    The link is returned once, in the response, and never again: only its hash
    is stored. Nothing is emailed — the whole point is that our email does not
    reach this person — so the committee member sends it themselves, by
    whatever does reach them.

    An unknown address gets an account, unverified, exactly as granting
    sponsor portal access does. Making a fresh link revokes any live one for
    the same account.
    """
    if session.is_impersonating:
        raise HTTPException(400, "Stop impersonating before making a passkey link.")

    address = normalise_email(email)
    if "@" not in address:
        raise HTTPException(400, "That does not look like an email address.")
    # Checked before the account is resolved as well as after: an address
    # that is refused should not get an account made for it on the way.
    if _is_standing(address):
        raise HTTPException(400, "The standing superuser sets up their own passkeys.")

    user = await resolve_or_create_user(address, verified=False)
    refusal = _link_refusal(
        session.role, user["email"], await _committee_role(user["id"]), session.permissions,
    )
    if refusal:
        raise HTTPException(403, refusal)

    token = generate_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.passkey_link_ttl_hours)
    link = await database.post(
        "/passkeys/links",
        {
            "token_hash": hash_token(token),
            "user_id": user["id"],
            "created_by": session.user.id,
            "expires_at": expires.isoformat(),
        },
    )
    log.warning(
        "PASSKEY LINK MADE: %s made link %s for %s, valid until %s",
        session.user.email, link["id"], user["email"], expires.isoformat(),
    )
    return {
        "ok": True,
        "id": link["id"],
        # The address the link is actually for: an Imperial alias collapses to
        # shortcode@ic.ac.uk, which is worth the committee member seeing.
        "email": user["email"],
        # In the fragment, which browsers never send to a server — so the
        # token is in no access log on the way in.
        "url": f"{settings.site_origin.rstrip('/')}/passkey-setup#{token}",
        "expiresAt": link["expires_at"],
        "neverSignedIn": user.get("last_login_at") is None,
    }


@admin_router.delete("/{link_id}", dependencies=[Depends(require_action("passkeys.revoke")), Depends(verify_csrf)])
async def revoke_link(
    link_id: int, session: AuthSession = Depends(require_passkey_links)
) -> dict:
    """Revoking only ever takes access away, so anybody who can make links can
    revoke any of them. Revoking one that is no longer live is a no-op."""
    await database.post(
        f"/passkeys/links/{link_id}/revoke", params={"revoked_by": session.user.id}
    )
    log.warning("Passkey link %s revoked by %s", link_id, session.user.email)
    return {"ok": True}
