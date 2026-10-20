"""Users, sessions, login codes and admin grants.

This service is the only thing that speaks SQL. It is unauthenticated and must
never be reachable from outside the compose network — the api service is its only
client, and the api service is what enforces authorisation.
"""

from datetime import datetime, timedelta, timezone

from asyncpg.exceptions import CheckViolationError, UniqueViolationError
from fastapi import Body, APIRouter
from fastapi.exceptions import HTTPException

from models.auth import (
    AuthSession,
    LoginCode,
    LoginCodeCreate,
    Member,
    Session,
    User,
    UserCreate,
)
from postgres import get_pool

router = APIRouter(prefix="/auth")

USER_COLUMNS = (
    "id, email, first_name, last_name, member_id, created_at, "
    "last_login_at, email_verified, shortcode"
)
MEMBER_COLUMNS = "id, cid, first_name, last_name, email, login, order_no, member_type, academic_year"


# --------------------------------------------------------------------------- users


@router.post("/users", status_code=201)
async def create_user(user: UserCreate) -> User:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.users
                    (email, first_name, last_name, member_id, shortcode, email_verified)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {USER_COLUMNS};
                """,
                str(user.email),
                user.first_name,
                user.last_name,
                user.member_id,
                user.shortcode,
                user.email_verified,
            )
        except UniqueViolationError:
            raise HTTPException(409, "A user with that email already exists")
    return User(**dict(row))


@router.post("/users/upsert")
async def upsert_user(user: UserCreate) -> User:
    """Resolve-or-create.

    Two things can identify the same person: the (canonicalised) email address,
    and the ICU member they are linked to. Both must be checked, because
    `users.member_id` is UNIQUE — an account created under
    `firstname.lastname24@imperial.ac.uk` before canonicalisation holds the
    member link, and inserting `ab1234@ic.ac.uk` for the same member would
    violate that constraint rather than recognising them.

    So: match on email, else on member, else insert. When an existing row is
    found by member, its address is *moved* to the canonical form, which quietly
    migrates old accounts as their owners sign in.

    Fields are only ever filled in, never nulled, and verification only goes
    false -> true.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            f"SELECT {USER_COLUMNS} FROM public.users WHERE lower(email) = lower($1) FOR UPDATE;",
            str(user.email),
        )
        if existing is None and user.member_id is not None:
            existing = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM public.users WHERE member_id = $1 FOR UPDATE;",
                user.member_id,
            )

        if existing is not None:
            row = await conn.fetchrow(
                f"""
                UPDATE public.users SET
                    email      = $2,
                    first_name = COALESCE($3, first_name),
                    last_name  = COALESCE($4, last_name),
                    member_id  = COALESCE($5, member_id),
                    shortcode  = COALESCE($6, shortcode),
                    email_verified = email_verified OR $7
                 WHERE id = $1
                RETURNING {USER_COLUMNS};
                """,
                existing["id"],
                str(user.email),
                user.first_name,
                user.last_name,
                user.member_id,
                user.shortcode,
                user.email_verified,
            )
            return User(**dict(row))

        row = await conn.fetchrow(
            f"""
            INSERT INTO public.users
                (email, first_name, last_name, member_id, shortcode, email_verified)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {USER_COLUMNS};
            """,
            str(user.email),
            user.first_name,
            user.last_name,
            user.member_id,
            user.shortcode,
            user.email_verified,
        )
    return User(**dict(row))


@router.get("/users/by-email")
async def get_user_by_email(email: str) -> User:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {USER_COLUMNS} FROM public.users WHERE lower(email) = lower($1);",
            email,
        )
    if row is None:
        raise HTTPException(404, "No such user")
    return User(**dict(row))


@router.get("/users/{user_id}")
async def get_user(user_id: int) -> User:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {USER_COLUMNS} FROM public.users WHERE id = $1;", user_id
        )
    if row is None:
        raise HTTPException(404, "No such user")
    return User(**dict(row))


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    first_name: str | None = None,
    last_name: str | None = None,
    member_id: int | None = None,
) -> User:
    """COALESCE means an omitted field is left alone rather than nulled."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.users SET
                first_name = COALESCE($2, first_name),
                last_name  = COALESCE($3, last_name),
                member_id  = COALESCE($4, member_id)
            WHERE id = $1
            RETURNING {USER_COLUMNS};
            """,
            user_id,
            first_name,
            last_name,
            member_id,
        )
    if row is None:
        raise HTTPException(404, "No such user")
    return User(**dict(row))


@router.post("/users/{user_id}/touch-login", status_code=204)
async def touch_login(user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.users SET last_login_at = NOW(), email_verified = true "
            "WHERE id = $1;",
            user_id,
        )


# ------------------------------------------------------------------------- sessions


@router.post("/sessions", status_code=201)
async def create_session(session: Session) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO public.sessions
                    (token_hash, csrf_token, user_id, expires_at, impersonator_id)
                VALUES ($1, $2, $3, $4, $5);
                """,
                session.token_hash,
                session.csrf_token,
                session.user_id,
                session.expires_at,
                session.impersonator_id,
            )
        except UniqueViolationError:
            # 256 bits of entropy: a "cosmic ray" branch, not a reason to retry.
            raise HTTPException(409, "Session token collision")


@router.get("/sessions/user")
async def get_user_session(token_hash: str) -> AuthSession:
    """Resolve a session token to its user, or 404.

    Expiry is enforced here rather than by the caller: an expired row must never
    authenticate, and keeping the check in the same statement as the lookup means
    no call site can forget it. `last_seen_at` is bumped as a side effect.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.sessions s
               SET last_seen_at = NOW()
             WHERE s.token_hash = $1
               AND s.expires_at > NOW()
            RETURNING s.token_hash, s.csrf_token, s.user_id, s.expires_at,
                      s.created_at, s.last_seen_at, s.impersonator_id;
            """,
            token_hash,
        )
        if row is None:
            raise HTTPException(404, "Session does not exist or has expired")

        user_row = await conn.fetchrow(
            f"SELECT {USER_COLUMNS} FROM public.users WHERE id = $1;", row["user_id"]
        )
        if user_row is None:
            raise HTTPException(404, "Session user no longer exists")

        member_row = None
        if user_row["member_id"] is not None:
            member_row = await conn.fetchrow(
                f"SELECT {MEMBER_COLUMNS} FROM public.members WHERE id = $1;",
                user_row["member_id"],
            )

        access = await conn.fetchrow(
            "SELECT role, permissions FROM public.admins WHERE user_id = $1;", row["user_id"]
        )
        role = access["role"] if access else None
        permissions = access["permissions"] if access else None

        # Who opened this session, when it is an impersonation. Returned in full
        # so the api can name them in the banner without a second round trip.
        impersonator_row = None
        if row["impersonator_id"] is not None:
            impersonator_row = await conn.fetchrow(
                f"SELECT {USER_COLUMNS} FROM public.users WHERE id = $1;",
                row["impersonator_id"],
            )
        sponsor_ids = [
            r["sponsor_id"]
            for r in await conn.fetch(
                "SELECT sponsor_id FROM public.sponsor_users WHERE user_id = $1;",
                row["user_id"],
            )
        ]

    return AuthSession(
        session=Session(**dict(row)),
        user=User(**dict(user_row)),
        role=role,
        permissions=permissions,
        is_admin=role is not None,
        member=Member(**dict(member_row)) if member_row else None,
        sponsor_ids=sponsor_ids,
        impersonator=User(**dict(impersonator_row)) if impersonator_row else None,
    )


@router.delete("/sessions", status_code=204)
async def delete_session(token_hash: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.sessions WHERE token_hash = $1;", token_hash
        )


@router.delete("/sessions/for-user/{user_id}", status_code=204)
async def delete_sessions_for_user(user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.sessions WHERE user_id = $1;", user_id)


@router.post("/sessions/sweep")
async def sweep_sessions() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM public.sessions WHERE expires_at < NOW();"
        )
    return int(result.split()[-1])


# ---------------------------------------------------------------------- login codes


@router.post("/login-codes", status_code=201)
async def create_login_code(code: LoginCodeCreate) -> int:
    """Invalidates outstanding codes for the address before issuing a new one, so
    a resend cannot leave two working codes in circulation."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE public.login_codes SET consumed_at = NOW()
                 WHERE lower(email) = lower($1) AND consumed_at IS NULL;
                """,
                code.email,
            )
            return await conn.fetchval(
                """
                INSERT INTO public.login_codes (email, code_hash, expires_at, request_ip)
                VALUES ($1, $2, $3, $4)
                RETURNING id;
                """,
                code.email,
                code.code_hash,
                code.expires_at,
                code.request_ip,
            )


@router.get("/login-codes/active")
async def get_active_login_code(email: str) -> LoginCode:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, code_hash, created_at, expires_at, consumed_at, attempts
              FROM public.login_codes
             WHERE lower(email) = lower($1)
               AND consumed_at IS NULL
               AND expires_at > NOW()
             ORDER BY created_at DESC
             LIMIT 1;
            """,
            email,
        )
    if row is None:
        raise HTTPException(404, "No live code for that address")
    return LoginCode(**dict(row))


@router.post("/login-codes/{code_id}/attempt")
async def register_attempt(code_id: int) -> int:
    """Returns the new attempt count so the caller can lock out after N tries."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        attempts = await conn.fetchval(
            "UPDATE public.login_codes SET attempts = attempts + 1 WHERE id = $1 RETURNING attempts;",
            code_id,
        )
    if attempts is None:
        raise HTTPException(404, "No such code")
    return attempts


@router.post("/login-codes/{code_id}/consume", status_code=204)
async def consume_login_code(code_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.login_codes SET consumed_at = NOW() WHERE id = $1;", code_id
        )


@router.get("/login-codes/recent-count")
async def recent_code_count(email: str, seconds: int = 3600) -> int:
    """Backs per-address rate limiting. Counts codes issued, not attempts made."""
    pool = await get_pool()
    since = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM public.login_codes WHERE lower(email) = lower($1) AND created_at > $2;",
            email,
            since,
        )


# ------------------------------------------------------------------------- admins


@router.get("/admins")
async def list_admins() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.user_id, a.role, a.permissions, a.granted_at, a.note,
                   u.email, u.first_name, u.last_name
              FROM public.admins a
              JOIN public.users u ON u.id = a.user_id
             ORDER BY a.granted_at;
            """
        )
    return [dict(r) for r in rows]


@router.post("/admins/{user_id}", status_code=204)
async def add_admin(
    user_id: int,
    role: str = "admin",
    permissions: list[str] | None = Body(default=None, embed=True),
    granted_by: int | None = None,
    note: str | None = None,
) -> None:
    """Grant a role, or change the one already held.

    ON CONFLICT updates rather than doing nothing: changing somebody's role is
    the same gesture as granting it, and a DO NOTHING here would silently leave
    an events officer on their old role when the committee promoted them.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO public.admins (user_id, role, permissions, granted_by, note)
                VALUES ($1, $2, $3::text[], $4, $5)
                ON CONFLICT (user_id) DO UPDATE SET
                    role        = EXCLUDED.role,
                    permissions = EXCLUDED.permissions,
                    granted_by  = EXCLUDED.granted_by,
                    granted_at  = NOW(),
                    note        = COALESCE(EXCLUDED.note, public.admins.note);
                """,
                user_id,
                role,
                permissions,
                granted_by,
                note,
            )
        except CheckViolationError:
            raise HTTPException(400, f"'{role}' is not a role")


@router.delete("/admins/{user_id}", status_code=204)
async def remove_admin(user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.admins WHERE user_id = $1;", user_id)


@router.get("/admins/count")
async def admin_count() -> int:
    """Bootstraps the first admin: when this is zero the api service promotes the
    first committee member who logs in, so a fresh deploy is not locked out."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM public.admins;")
