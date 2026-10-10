"""Passkeys, the links the committee makes for them, and WebAuthn challenges.

Storage only. Every decision — whether a signature is valid, who may make a
link for whom, whether a link's maker could still make it — is the api's. What
lives here is what has to be atomic: a challenge is used once, a link is spent
in the same transaction as the passkey it creates.
"""

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.passkeys import (
    ChallengeConsume,
    ChallengeCreate,
    LinkCreate,
    PasskeyCreate,
    PasskeyUsed,
)
from postgres import get_pool

router = APIRouter(prefix="/passkeys")

PASSKEY_COLUMNS = (
    "p.id, p.user_id, p.credential_id, p.public_key, p.sign_count, p.transports, "
    "p.aaguid, p.device_type, p.backed_up, p.name, p.link_id, p.created_at, "
    "p.last_used_at"
)

# A link is live until it is used, revoked or expires. Spelled once so the
# lookup, the listing and the spend cannot disagree about it.
LIVE_LINK = "(l.used_at IS NULL AND l.revoked_at IS NULL AND l.expires_at > NOW())"


# ------------------------------------------------------------------ challenges


@router.post("/challenges", status_code=201)
async def create_challenge(body: ChallengeCreate) -> None:
    """Store a challenge, sweeping the expired ones while we are here.

    The sign-in options call is unauthenticated, so this table grows at
    whatever rate somebody calls it. Sweeping on every insert keeps it bounded
    by that rate times the challenge lifetime, with no scheduler to forget.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.webauthn_challenges WHERE expires_at < NOW();"
        )
        await conn.execute(
            """
            INSERT INTO public.webauthn_challenges
                (challenge, purpose, user_id, link_id, expires_at)
            VALUES ($1, $2, $3, $4, $5);
            """,
            body.challenge,
            body.purpose,
            body.user_id,
            body.link_id,
            body.expires_at,
        )


@router.post("/challenges/consume")
async def consume_challenge(body: ChallengeConsume) -> dict:
    """Use a challenge up, returning what it was issued for.

    Deleted whether or not it has expired, so a stale one cannot be retried
    either; an expired one then 404s like a missing one. DELETE ... RETURNING
    is the atomic part: two requests racing with the same answer get one row
    between them.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM public.webauthn_challenges
             WHERE challenge = $1 AND purpose = $2
            RETURNING challenge, purpose, user_id, link_id, expires_at > NOW() AS live;
            """,
            body.challenge,
            body.purpose,
        )
    if row is None or not row["live"]:
        raise HTTPException(404, "No such challenge, or it has expired")
    return {k: row[k] for k in ("challenge", "purpose", "user_id", "link_id")}


# --------------------------------------------------------------------- handles


@router.post("/handle/{user_id}")
async def user_handle(user_id: int, candidate: str) -> str:
    """The account's WebAuthn user handle, set to `candidate` if it has none.

    COALESCE in one UPDATE, so two first registrations racing on the same
    account agree on whichever handle won rather than each keeping its own.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        handle = await conn.fetchval(
            """
            UPDATE public.users
               SET webauthn_handle = COALESCE(webauthn_handle, $2)
             WHERE id = $1
            RETURNING webauthn_handle;
            """,
            user_id,
            candidate,
        )
    if handle is None:
        raise HTTPException(404, "No such user")
    return handle


# -------------------------------------------------------------------- passkeys


@router.get("/for-user/{user_id}")
async def passkeys_for_user(user_id: int) -> list[dict]:
    """Every passkey on an account, with who made the link it came through."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {PASSKEY_COLUMNS},
                   c.email AS link_created_by_email,
                   c.first_name AS link_created_by_first_name,
                   c.last_name AS link_created_by_last_name
              FROM public.passkeys p
              LEFT JOIN public.passkey_links l ON l.id = p.link_id
              LEFT JOIN public.users c         ON c.id = l.created_by
             WHERE p.user_id = $1
             ORDER BY p.created_at, p.id;
            """,
            user_id,
        )
    return [dict(r) for r in rows]


@router.get("/count/{user_id}")
async def passkey_count(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM public.passkeys WHERE user_id = $1;", user_id
        )


@router.get("/credential")
async def passkey_by_credential(credential_id: str) -> dict:
    """The passkey a sign-in answer names, with its account's handle."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {PASSKEY_COLUMNS}, u.webauthn_handle
              FROM public.passkeys p
              JOIN public.users u ON u.id = p.user_id
             WHERE p.credential_id = $1;
            """,
            credential_id,
        )
    if row is None:
        raise HTTPException(404, "No such passkey")
    return dict(row)


@router.post("", status_code=201)
async def create_passkey(body: PasskeyCreate) -> dict:
    """Save a passkey — and, if it came through a link, spend the link.

    One transaction, so the two cannot come apart: a link is never marked used
    without the passkey it paid for, and a passkey is never saved off a link
    that somebody else spent (or the committee revoked) a moment earlier.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if body.link_id is not None:
            spent = await conn.fetchval(
                f"""
                UPDATE public.passkey_links l SET used_at = NOW()
                 WHERE l.id = $1 AND l.user_id = $2 AND {LIVE_LINK}
                RETURNING l.id;
                """,
                body.link_id,
                body.user_id,
            )
            if spent is None:
                raise HTTPException(410, "That link has already been used, revoked or has expired")
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.passkeys
                    (user_id, credential_id, public_key, sign_count, transports,
                     aaguid, device_type, backed_up, name, link_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id, user_id, credential_id, sign_count, transports, aaguid,
                          device_type, backed_up, name, link_id, created_at, last_used_at;
                """,
                body.user_id,
                body.credential_id,
                body.public_key,
                body.sign_count,
                body.transports,
                body.aaguid,
                body.device_type,
                body.backed_up,
                body.name,
                body.link_id,
            )
        except UniqueViolationError:
            raise HTTPException(409, "That passkey is already registered")
    return dict(row)


@router.post("/{passkey_id}/used", status_code=204)
async def passkey_used(passkey_id: int, body: PasskeyUsed) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.passkeys SET sign_count = $2, last_used_at = NOW() WHERE id = $1;",
            passkey_id,
            body.sign_count,
        )


@router.delete("/{passkey_id}", status_code=204)
async def delete_passkey(passkey_id: int, user_id: int) -> None:
    """Scoped to the owner in the statement itself, so an id from somebody
    else's account deletes nothing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM public.passkeys WHERE id = $1 AND user_id = $2;",
            passkey_id,
            user_id,
        )
    if result.split()[-1] == "0":
        raise HTTPException(404, "No such passkey")


# ----------------------------------------------------------------------- links


@router.post("/links", status_code=201)
async def create_link(body: LinkCreate) -> dict:
    """Make a link, revoking any still-live one for the same account first.

    Same rule as sign-in codes: a fresh one replaces the old, so there is never
    more than one working link for an account in circulation.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            f"""
            UPDATE public.passkey_links l
               SET revoked_at = NOW(), revoked_by = $2
             WHERE l.user_id = $1 AND {LIVE_LINK};
            """,
            body.user_id,
            body.created_by,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO public.passkey_links (token_hash, user_id, created_by, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, user_id, created_by, created_at, expires_at;
            """,
            body.token_hash,
            body.user_id,
            body.created_by,
            body.expires_at,
        )
    return dict(row)


@router.get("/links/by-token")
async def link_by_token(token_hash: str) -> dict:
    """A live link, with the account it is for and the committee member who
    made it — including their role *now*, which the api checks before honouring
    the link."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT l.id, l.user_id, l.created_by, l.created_at, l.expires_at,
                   u.email, u.first_name, u.last_name,
                   ua.role AS user_role,
                   c.email AS created_by_email,
                   c.first_name AS created_by_first_name,
                   c.last_name AS created_by_last_name,
                   ca.role AS created_by_role
              FROM public.passkey_links l
              JOIN public.users u        ON u.id = l.user_id
              LEFT JOIN public.admins ua ON ua.user_id = l.user_id
              LEFT JOIN public.users c   ON c.id = l.created_by
              LEFT JOIN public.admins ca ON ca.user_id = l.created_by
             WHERE l.token_hash = $1 AND {LIVE_LINK};
            """,
            token_hash,
        )
    if row is None:
        raise HTTPException(404, "No live link with that token")
    return dict(row)


@router.get("/links")
async def list_links(days: int = 30) -> list[dict]:
    """Links still live, plus any made, used or revoked in the last `days`.

    Older history is left out of the listing rather than deleted: the rows are
    the record of who gave whom a way in.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT l.id, l.user_id, l.created_at, l.expires_at, l.used_at, l.revoked_at,
                   {LIVE_LINK} AS live,
                   u.email,
                   c.email AS created_by_email,
                   r.email AS revoked_by_email
              FROM public.passkey_links l
              JOIN public.users u      ON u.id = l.user_id
              LEFT JOIN public.users c ON c.id = l.created_by
              LEFT JOIN public.users r ON r.id = l.revoked_by
             WHERE {LIVE_LINK}
                OR GREATEST(l.created_at, l.used_at, l.revoked_at)
                   > NOW() - make_interval(days => $1)
             ORDER BY {LIVE_LINK} DESC, l.created_at DESC
             LIMIT 200;
            """,
            days,
        )
    return [dict(r) for r in rows]


@router.post("/links/{link_id}/revoke", status_code=204)
async def revoke_link(link_id: int, revoked_by: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"""
            UPDATE public.passkey_links l
               SET revoked_at = NOW(), revoked_by = $2
             WHERE l.id = $1 AND {LIVE_LINK};
            """,
            link_id,
            revoked_by,
        )
    if result.split()[-1] == "0":
        raise HTTPException(404, "That link is not live")
