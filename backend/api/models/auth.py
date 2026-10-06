"""Auth shapes for the public API.

Mirrors the adapter's models — the two services share no code, so the contract
between them is these definitions rather than an import.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, model_validator

from config import settings
from utils import roles


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
    impersonator_id: int | None = None


class AuthSession(BaseModel):
    session: Session
    user: User
    role: str | None = None
    is_admin: bool = False
    member: Member | None = None
    sponsor_ids: list[int] = []
    impersonator: User | None = None

    @model_validator(mode="after")
    def _pin_superuser(self) -> "AuthSession":
        """The configured address is a superuser whatever the database says.

        Deliberately enforced here rather than only in the admins table: this
        runs on every construction of a session, so the role cannot be lost to a
        bad migration, a hand-run DELETE, or somebody with admin access trying
        to demote the person who granted it to them. The table is still written
        — the row is what makes them visible in the People tab — but it is a
        record of the fact, not the source of it.

        It does not fire for an impersonated session: the user there is the
        person being impersonated, which is the whole point.
        """
        if _is_standing_superuser(self.user.email):
            self.role = "superuser"
        self.is_admin = self.role is not None
        return self

    @property
    def capabilities(self) -> frozenset[str]:
        return roles.capabilities(self.role)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def is_impersonating(self) -> bool:
        return self.impersonator is not None

    @property
    def is_member_now(self) -> bool:
        """Membership is annual: holding a member record is not the same as
        being a member *this* year. Everything that gates on membership must use
        this rather than `member_id is not None`."""
        from utils.year import academic_year

        return self.member is not None and self.member.academic_year == academic_year()


def _is_standing_superuser(email: str | None) -> bool:
    configured = (settings.superuser_email or "").strip().lower()
    return bool(configured) and str(email or "").strip().lower() == configured


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
    # The committee role held, and what it unlocks. The SPA hides the tabs it
    # has no capability for; the server refuses them regardless, so this is
    # presentation, not security.
    role: str | None = None
    capabilities: list[str] = []
    # The committee card this person may edit, if any — by login. Present for
    # ordinary committee members who hold no role at all.
    committee_login: str | None = None
    # Set while a superuser is signed in as somebody else, so the page can say
    # so in a banner that does not go away.
    impersonating: bool = False
    impersonator_email: str | None = None
    academic_year: str | None = None
    csrf_token: str | None = None
    # True only in development. Drives the dev sign-in panel in the login
    # dialog; in production this is always false and the panel never renders.
    dev_login: bool = False
