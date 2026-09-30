"""Request dependencies: who is calling, and may they do this.

The database adapter has no authorisation of its own, so every rule lives here.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from clients import database
from config import settings
from models.auth import AuthSession
from utils.auth import hash_token


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
    if not session.is_admin:
        raise HTTPException(403, "Committee access only")
    return session


async def require_sponsor(
    session: AuthSession = Depends(require_session),
) -> AuthSession:
    if not session.sponsor_ids and not session.is_admin:
        raise HTTPException(403, "Sponsor access only")
    return session


def sponsor_scope(session: AuthSession, sponsor_id: int) -> None:
    """Admins may act for any sponsor; a sponsor user only for their own.

    Without this an authenticated sponsor could edit a competitor's listings just
    by changing the id in the URL.
    """
    if session.is_admin:
        return
    if sponsor_id not in session.sponsor_ids:
        raise HTTPException(403, "Not your sponsor account")


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
