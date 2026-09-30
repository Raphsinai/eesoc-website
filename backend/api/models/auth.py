"""Auth shapes for the public API.

Mirrors the adapter's models — the two services share no code, so the contract
between them is these definitions rather than an import.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class User(BaseModel):
    id: int | None = None
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    member_id: int | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    email_verified: bool = False
    shortcode: str | None = None


class Member(BaseModel):
    id: int | None = None
    cid: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    login: str | None = None
    order_no: str | None = None
    member_type: str | None = None
    academic_year: str | None = None


class Session(BaseModel):
    token_hash: str
    csrf_token: str
    user_id: int
    expires_at: datetime
    created_at: datetime | None = None
    last_seen_at: datetime | None = None


class AuthSession(BaseModel):
    session: Session
    user: User
    is_admin: bool = False
    member: Member | None = None
    sponsor_ids: list[int] = []

    @property
    def is_member_now(self) -> bool:
        """Membership is annual: holding a member record is not the same as
        being a member *this* year. Everything that gates on membership must use
        this rather than `member_id is not None`."""
        from utils.year import academic_year

        return self.member is not None and self.member.academic_year == academic_year()


# --------------------------------------------------------------------- requests


class CodeRequest(BaseModel):
    email: EmailStr
    # Only used when creating a brand-new external account; an existing
    # account's name is never overwritten from this.
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class CodeVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class GuestDetails(BaseModel):
    """Who a not-signed-in person is, collected at the point of booking.

    `shortcode` is the Imperial login (the local part of the college address).
    It is what lets us match their Union purchase automatically, exactly as we
    do for signed-in members — without it a student would have to chase down an
    order number they may never have seen.
    """

    email: EmailStr
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    shortcode: str | None = Field(default=None, max_length=32)


# -------------------------------------------------------------------- responses


class SessionView(BaseModel):
    """What `GET /auth/session` returns, shaped for the site header."""

    authenticated: bool = False
    # True when we still need a name from this person: they are external (no ICU
    # record to take it from), hold no sponsor account, and have not given one.
    # Asking *before* verification would require telling the client whether an
    # address already has an account, which is an enumeration oracle.
    needs_name: bool = False
    user: dict | None = None
    roles: list[str] = []
    is_member: bool = False
    is_admin: bool = False
    is_sponsor: bool = False
    sponsor_ids: list[int] = []
    academic_year: str | None = None
    csrf_token: str | None = None
    # True only in development. Drives the dev sign-in panel in the login
    # dialog; in production this is always false and the panel never renders.
    dev_login: bool = False
