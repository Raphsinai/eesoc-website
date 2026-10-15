"""WhatsApp posts: the record of each, and the claim that stops a post going
twice. No authorisation, as everywhere in the adapter.

(The groups themselves are read live from the linked number by the api; the
whatsapp_groups table from 077 is no longer used.)"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.whatsapp import (
    WhatsAppPost,
    WhatsAppPostClaim,
    WhatsAppPostResult,
)
from postgres import get_pool

router = APIRouter(prefix="/whatsapp")

POST_COLUMNS = """
    id, campaign_id, jid, group_name, body, status, message_id, error,
    created_by, created_at, sent_at
"""


# ------------------------------------------------------------------- posts


@router.post("/posts", status_code=201)
async def claim_post(claim: WhatsAppPostClaim) -> WhatsAppPost:
    """Record a post before it is made.

    For a post that goes with an email, the unique index makes this the claim:
    a second claim for the same email and group gets a 409, and whoever gets
    it does not post.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.whatsapp_posts
                (campaign_id, jid, group_name, body, created_by)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (campaign_id, jid) WHERE campaign_id IS NOT NULL DO NOTHING
            RETURNING {POST_COLUMNS};
            """,
            claim.campaign_id, claim.jid, claim.group_name, claim.body, claim.created_by,
        )
    if row is None:
        raise HTTPException(409, "Already posted (or being posted) to that group for this email.")
    return WhatsAppPost(**dict(row))


@router.patch("/posts/{post_id}")
async def finish_post(post_id: int, result: WhatsAppPostResult) -> WhatsAppPost:
    if result.status not in ("sent", "failed"):
        raise HTTPException(400, "A post finishes as sent or failed.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.whatsapp_posts SET
                -- Cast in both places: used as a column value and inside the
                -- CASE, an uncast $2 is inferred as two types and refused.
                status     = $2::varchar,
                message_id = COALESCE($3, message_id),
                error      = $4,
                sent_at    = CASE WHEN $2::varchar = 'sent' THEN NOW() ELSE sent_at END
             WHERE id = $1
            RETURNING {POST_COLUMNS};
            """,
            post_id, result.status, result.message_id, result.error,
        )
    if row is None:
        raise HTTPException(404, "No such post")
    return WhatsAppPost(**dict(row))


@router.get("/posts")
async def list_posts(
    campaign_id: int | None = None,
    jid: str | None = None,
    within_minutes: int | None = None,
    limit: int = 30,
) -> list[WhatsAppPost]:
    """Newest first. `jid` and `within_minutes` together answer "was this just
    posted there?" — the guard against a double press."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {POST_COLUMNS} FROM public.whatsapp_posts
             WHERE ($2::bigint IS NULL OR campaign_id = $2::bigint)
               AND ($3::varchar IS NULL OR jid = $3::varchar)
               AND ($4::int IS NULL OR created_at > NOW() - make_interval(mins => $4::int))
             ORDER BY created_at DESC LIMIT $1;
            """,
            limit, campaign_id, jid, within_minutes,
        )
    return [WhatsAppPost(**dict(r)) for r in rows]
