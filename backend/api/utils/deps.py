"""Request dependencies: who is calling, and may they do this.

The database adapter has no authorisation of its own, so every rule lives here.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from clients import database
from config import settings
from models.auth import AuthSession
from utils.auth import hash_token
from utils.year import academic_year


async def current_session(request: Request) -> AuthSession | None:
    """Resolve the session cookie, or None. Never raises — public endpoints use
    this to vary their output for signed-in users."""
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return None
    data = await database.get(
        "/auth/sessions/user", {"token_hash": hash_token(token)}
    )
    if data is None:
        return None
    return AuthSession(**data)


async def require_session(
    session: AuthSession | None = Depends(current_session),
) -> AuthSession:
    if session is None:
        raise HTTPException(401, "Sign in to continue")
    return session


async def require_member(
    session: AuthSession = Depends(require_session),
) -> AuthSession:
    """Members-only.

    Membership is `member_id IS NOT NULL` *and* a membership record for the
    current academic year — last year's member is not this year's. The year check
    happens in the routes that care, using the member row carried on the session.
    """
    if session.user.member_id is None or session.member is None:
        raise HTTPException(
            403, "This is for Union members. Log in with the email on your ICU membership."
        )
    return session


async def require_admin(
    session: AuthSession = Depends(require_session),
) -> AuthSession:
    """Holds *some* committee role.

    The coarse gate: it says the admin area is theirs to open, not that any
    particular thing in it is. Every write behind it names the capability it
    needs with `require_capability`.
    """
    if not session.is_admin:
        raise HTTPException(403, "Committee access only")
    return session


def require_capability(capability: str):
    """Refuse unless the caller's role carries this capability.

    The message names the capability rather than the role, because the roles
    will be reshuffled and "you need the events role" ages badly the first time
    somebody is given a different one that also covers it.
    """

    async def guard(
        session: AuthSession = Depends(require_session),
    ) -> AuthSession:
        if not session.is_admin:
            raise HTTPException(403, "Committee access only")
        if not session.can(capability):
            raise HTTPException(403, _DENIED.get(capability, "Not your area of the site"))
        return session

    return guard


def require_any_capability(*capabilities: str):
    """Refuse unless the caller holds at least one of these.

    For the handful of things two roles genuinely share — pressing "sync now"
    is the events officers' business as much as an admin's, because it is how
    an unconfirmed booking gets confirmed.
    """

    async def guard(
        session: AuthSession = Depends(require_session),
    ) -> AuthSession:
        if not session.is_admin:
            raise HTTPException(403, "Committee access only")
        if not any(session.can(c) for c in capabilities):
            raise HTTPException(403, "Not your area of the site")
        return session

    return guard


_DENIED = {
    "users": "Only a superuser can change who has access.",
    "events": "You do not have access to events.",
    "sponsors": "You do not have access to sponsors.",
    "site": (
        "That is a site-wide setting, and your role covers one part of the site. "
        "Ask an admin."
    ),
}


# `require_capability` returns a function, so these are the Depends-ready
# versions the route decorators use. Named rather than inlined so the call sites
# read as a sentence.
require_users = require_capability("users")
require_events = require_capability("events")
require_sponsors = require_capability("sponsors")
require_site = require_capability("site")


async def require_event_editor(
    session: AuthSession = Depends(require_session),
) -> AuthSession:
    """Can edit at least *some* events — all of them, or industry ones only.

    Which ones is decided per event by `assert_may_edit_event`; this is the
    gate on the listing and on create.
    """
    if not session.is_admin:
        raise HTTPException(403, "Committee access only")
    if not (session.can("events") or session.can("industry")):
        raise HTTPException(403, "You do not have access to events.")
    return session


def may_edit_event(session: AuthSession, event: dict | None) -> bool:
    """`events` covers everything; `industry` covers only industry events."""
    if session.can("events"):
        return True
    if session.can("industry"):
        return bool((event or {}).get("industry"))
    return False


def assert_may_edit_event(session: AuthSession, event: dict | None) -> None:
    if not may_edit_event(session, event):
        raise HTTPException(
            403,
            "That is not an industry event, and your role only covers industry "
            "events. Ask an admin or the events officers.",
        )


async def require_sponsor(
    session: AuthSession = Depends(require_session),
) -> AuthSession:
    if not session.sponsor_ids and not session.can("sponsors"):
        raise HTTPException(403, "Sponsor access only")
    return session


def sponsor_scope(session: AuthSession, sponsor_id: int) -> None:
    """Committee with the sponsors capability may act for any sponsor; a sponsor
    user only for their own.

    Without this an authenticated sponsor could edit a competitor's listings just
    by changing the id in the URL. Note it is the *capability* and not merely
    `is_admin`: an events officer holds a committee role and still has no
    business inside a sponsor's portal.
    """
    if session.can("sponsors"):
        return
    if sponsor_id not in session.sponsor_ids:
        raise HTTPException(403, "Not your sponsor account")


# ------------------------------------------------------- committee own card


async def committee_card(session: AuthSession | None) -> dict | None:
    """The committee entry this person owns, or None.

    Three ways to match, in descending order of trust:

      1. an address linked to the card by hand (`user_email`) — the override for
         the person whose account does not look like their ICU identity;
      2. the shortcode on their account, or the login on their ICU member
         record, against the roster's login;
      3. the address ICU holds for them on the roster.

    Owning a card is not a role. A committee member with no admin access at all
    can still fix the typo in their own blurb, and that is the whole point.
    """
    if session is None:
        return None

    email = (str(session.user.email) or "").strip().lower()
    logins = {
        (session.user.shortcode or "").strip().lower(),
        (session.member.login or "").strip().lower() if session.member else "",
    } - {""}

    roster = await database.get(
        "/union/committee",
        {"academic_year": academic_year(), "include_hidden": True},
    ) or []

    for entry in roster:
        if (entry.get("user_email") or "").strip().lower() == email and email:
            return entry
    for entry in roster:
        if (entry.get("login") or "").strip().lower() in logins:
            return entry
    for entry in roster:
        if email and (entry.get("email") or "").strip().lower() == email:
            return entry
    return None


async def verify_csrf(request: Request) -> None:
    """Double-submit check on unsafe methods.

    SameSite=Lax already blocks cross-site POSTs in current browsers; this is the
    second lock. The cookie is readable by script *on this origin only*, so an
    attacker's page cannot read it to forge the header.
    """
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return

    # CSRF is about a third-party page *riding an existing session*. A caller
    # with no session cookie has nothing to ride, so requiring a token would
    # only break guest booking — which is deliberately open to the signed-out.
    if not request.cookies.get(settings.session_cookie):
        return

    cookie = request.cookies.get(settings.csrf_cookie)
    header = request.headers.get(settings.csrf_header)
    if not cookie or not header or cookie != header:
        raise HTTPException(403, "Missing or invalid CSRF token")


def client_ip(request: Request) -> str:
    """Behind nginx, REMOTE_ADDR is the proxy. Trust X-Forwarded-For's first hop
    because nginx sets it (see frontend/nginx.conf) and the api is not reachable
    from anywhere else on the compose network."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_feature(feature: str):
    """Refuse a request for something currently under maintenance.

    503 rather than 404 or 403: the route exists and the caller is not at
    fault, and 503 is the one status that says "try later" to a browser, a
    monitor and a search engine alike. `Retry-After` is deliberately omitted —
    we genuinely do not know when the dependency returns.
    """

    async def guard() -> None:
        from services import maintenance

        down = maintenance.unavailable()
        if feature in down:
            raise HTTPException(503, down[feature])

    return Depends(guard)
