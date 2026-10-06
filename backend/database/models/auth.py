"""Auth models. Field names match the SQL columns exactly (snake_case), so a
Record maps to a model with `Model(**dict(row))` and there is no naming layer to
get wrong."""

from datetime import datetime

from pydantic import BaseModel, EmailStr


class User(BaseModel):
    id: int | None = None
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    # NULL member_id is the definition of an external (non-ICU) user.
    member_id: int | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    # False until the person has completed the code flow. Guest bookings create
    # an account that owns a reservation but is not yet a login.
    email_verified: bool = False
    shortcode: str | None = None


class UserCreate(BaseModel):
    """Only ever built server-side, after an email has been proven. `member_id`
    comes from the Union lookup, never from a client."""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    member_id: int | None = None
    shortcode: str | None = None
    email_verified: bool = False


class Member(BaseModel):
    """Mirror of an eActivities /reports/members row."""

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
    # Set when a superuser opened this session as somebody else. NULL is the
    # normal case: the person signed in as themselves.
    impersonator_id: int | None = None


class AuthSession(BaseModel):
    session: Session
    user: User
    # One of superuser / admin / events / industry, or None for an account with
    # no committee role. `is_admin` is kept as the "holds some role" shorthand
    # the older call sites read.
    role: str | None = None
    is_admin: bool = False
    member: Member | None = None
    sponsor_ids: list[int] = []
    # The account a superuser opened this session from, if they are
    # impersonating. Carried so the api can offer a way back.
    impersonator: User | None = None


class LoginCodeCreate(BaseModel):
    email: str
    code_hash: str
    expires_at: datetime
    request_ip: str | None = None


class LoginCode(BaseModel):
    id: int
    email: str
    code_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    attempts: int
