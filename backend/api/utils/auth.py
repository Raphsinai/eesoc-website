"""Token, code and cookie helpers.

These are pure CPU and deliberately synchronous — making them `async` bought
nothing and only forced every call site into an await.
"""

import hashlib
import hmac
import secrets

from fastapi import Response

from config import settings


def generate_token() -> str:
    """256 bits, URL-safe. Returned to the browser; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """sha256 hex — 64 chars, matching sessions.token_hash CHAR(64)."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_login_code() -> str:
    """A numeric code of the configured length.

    Digits rather than hex: these get read off a phone screen and typed, and
    `secrets.token_hex` produces a–f which people confuse with each other.
    """
    upper = 10 ** settings.login_code_digits
    return str(secrets.randbelow(upper)).zfill(settings.login_code_digits)


def codes_equal(supplied: str, stored_hash: str) -> bool:
    """Constant-time compare, so a wrong code cannot be narrowed down by timing."""
    return hmac.compare_digest(hash_token(supplied.strip()), stored_hash)


def set_session_cookies(response: Response, token: str, csrf_token: str) -> None:
    """Session cookie is HttpOnly so script cannot read it. The CSRF cookie
    deliberately is NOT — the SPA reads it and echoes it in a header, which is
    what makes the double-submit check meaningful."""
    max_age = settings.session_ttl_days * 24 * 3600
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")
    response.delete_cookie(settings.csrf_cookie, path="/")


def normalise_email(email: str) -> str:
    return email.strip().lower()


def login_from_email(email: str) -> str | None:
    """Imperial logins are the local part of the college address, so an address
    we have not seen in the members feed can still be matched on login.

    This is a *fallback for lookup only*. Membership is never inferred from the
    domain: alumni and staff hold @imperial.ac.uk addresses without being ICU
    members, and a member's ICU record may carry a personal address.
    """
    email = normalise_email(email)
    if "@" not in email:
        return None
    local, domain = email.split("@", 1)
    if domain in {"ic.ac.uk", "imperial.ac.uk"}:
        return local or None
    return None
