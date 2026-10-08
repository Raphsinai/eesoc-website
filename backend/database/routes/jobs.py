"""Cross-pod job leases and per-recipient bulk-send state.

The api never talks to Postgres directly, so a guard that has to hold across
pods — "only one sender at a time", "only one sync at a time" — has to be a
claim IN the database, claimed THROUGH this adapter. A rolling update starts the
replacement pod before stopping the old one, so for a minute there are two api
processes that each believe they are the only one; an in-process lock cannot
see the other pod, and a row here can.

No authorisation here, as everywhere in the adapter — the api service is the
only client and is where the rules live.
"""

from __future__ import annotations

import json

from fastapi import APIRouter

from models.jobs import (
    JobLeaseClaim,
    JobLeaseRelease,
    RecipientClaim,
    RecipientConfirm,
    RecipientSeed,
)
from postgres import get_pool

router = APIRouter()

# ------------------------------------------------------------------- leases


@router.post("/job-leases/claim")
async def claim_lease(claim: JobLeaseClaim) -> dict:
    """Take a named lease if nobody holds it, or the holder's time ran out.

    The whole takeover protocol is one statement: an absent row inserts, an
    expired row is stolen by the `ON CONFLICT ... DO UPDATE`, and a lease still
    in force comes back as "not yours" without ever being an error. `NOW()` is
    the server's clock on both sides of the comparison, so a lease never
    expires early because two pods disagree about what time it is.

    `OR holder = EXCLUDED.holder` lets the current holder re-claim to extend
    its own lease — the bulk sender renews per batch, because a send paced
    over Exchange's rate limit can outlive any short TTL. Without that arm a
    long send would drop its own lease mid-flight and a second sender would
    start alongside it.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.job_leases (name, holder, expires_at)
            VALUES ($1, $2, NOW() + make_interval(secs => $3))
            ON CONFLICT (name) DO UPDATE
               SET holder = EXCLUDED.holder,
                   claimed_at = NOW(),
                   expires_at = EXCLUDED.expires_at
             WHERE public.job_leases.expires_at < NOW()
                OR public.job_leases.holder = EXCLUDED.holder
            RETURNING holder, expires_at;
            """,
            claim.name, claim.holder, claim.ttl_seconds,
        )
        if row is not None:
            return {
                "claimed": True,
                "holder": row["holder"],
                "expires_at": row["expires_at"],
            }

        # A lease in force. Read it back rather than guessing, so the caller's
        # log says who is mid-job rather than just "somebody".
        existing = await conn.fetchrow(
            "SELECT holder, expires_at FROM public.job_leases WHERE name = $1;",
            claim.name,
        )
        return {
            "claimed": False,
            "holder": existing["holder"] if existing else None,
            "expires_at": existing["expires_at"] if existing else None,
        }


@router.post("/job-leases/release")
async def release_lease(release: JobLeaseRelease) -> dict:
    """Give a lease back.

    The holder must match: a pod that lost the lease to another must never be
    able to delete the row the winner now owns, which is why the claim model
    carries an opaque per-process id rather than relying on the lease name.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM public.job_leases
             WHERE name = $1 AND holder = $2
            RETURNING name;
            """,
            release.name, release.holder,
        )
    return {"released": row is not None}


# --------------------------------------------------- per-recipient send state


@router.post("/campaign-recipients", status_code=201)
async def seed_recipients(seed: RecipientSeed) -> dict:
    """Write this campaign's audience as one row per address, idempotently.

    `DO NOTHING`, not `DO UPDATE`: on a resume the previous pod seeded the same
    addresses, and a row it already confirmed as sent must never become
    claimable again — re-claiming it is how somebody gets the newsletter twice.
    """
    pool = await get_pool()
    inserted = 0
    async with pool.acquire() as conn, conn.transaction():
        # asyncpg has no bulk multi-VALUES with ON CONFLICT for a
        # variable-length list, so one statement per address — inside a single
        # transaction so a seed is all-or-nothing from a reader's point of view.
        for email in seed.emails:
            row = await conn.fetchrow(
                """
                INSERT INTO public.campaign_recipients (campaign_id, email)
                VALUES ($1, lower($2))
                ON CONFLICT (campaign_id, email) DO NOTHING
                RETURNING id;
                """,
                seed.campaign_id, email,
            )
            if row is not None:
                inserted += 1
    return {"inserted": inserted}


@router.post("/campaign-recipients/claim")
async def claim_recipients(claim: RecipientClaim) -> dict:
    """Take the next batch of not-yet-sent rows for one campaign.

    Flipping the rows and returning them is one statement, so two senders can
    never take the same recipient: `FOR UPDATE SKIP LOCKED` keeps the loser of
    a simultaneous claim off rows the winner has in flight. Rows still
    `claimed` but older than `stale_seconds` are included — that is the sender
    that died mid-batch, and taking its rows back is how a killed pod's work
    gets redone rather than lost.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.campaign_recipients
               SET status = 'claimed', claimed_at = NOW()
             WHERE id IN (
                     SELECT id FROM public.campaign_recipients
                      WHERE campaign_id = $1
                        AND (status = 'pending'
                             OR (status = 'claimed'
                                 AND claimed_at < NOW() - make_interval(secs => $2)))
                      ORDER BY id
                      LIMIT $3
                        FOR UPDATE SKIP LOCKED
                 )
            RETURNING email;
            """,
            claim.campaign_id, claim.stale_seconds, claim.batch_size,
        )
        # What the next claim would still find: the sender loops until this is
        # empty, so it counts the work left rather than only this batch.
        remaining = await conn.fetchval(
            """
            SELECT count(*) FROM public.campaign_recipients
             WHERE campaign_id = $1
               AND (status = 'pending'
                    OR (status = 'claimed'
                        AND claimed_at < NOW() - make_interval(secs => $2)));
            """,
            claim.campaign_id, claim.stale_seconds,
        )
    return {"emails": [r["email"] for r in rows], "remaining": int(remaining or 0)}


@router.post("/campaign-recipients/confirm")
async def confirm_recipients(confirm: RecipientConfirm) -> dict:
    """Record a batch's outcome. Every claimed row is confirmed either way.

    A failed row is final, not to be re-claimed: an unreachable address must
    not be re-sent on every pass for the rest of the campaign. `sent_at` keeps
    its old value on a failure — the row says what happened to it, not when the
    verdict landed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.campaign_recipients
               SET status = CASE WHEN $3 THEN 'sent' ELSE 'failed' END,
                   sent_at = CASE WHEN $3 THEN NOW() ELSE sent_at END
             WHERE campaign_id = $1
               AND email = ANY($2::varchar[])
            RETURNING id;
            """,
            confirm.campaign_id, confirm.emails, confirm.ok,
        )
    return {"confirmed": len(rows)}


@router.get("/campaigns/resumable")
async def resumable_campaigns() -> list[dict]:
    """The sends a pod replacement left unfinished: `status = 'sending'`.

    Everything a fresh process needs to rebuild the message has to be in the
    row, which is why the campaign carries `kind` and `event_id` — a restarted
    pod never saw the closure that built the original. Returned as plain dicts
    rather than models: the api is the only caller and shapes these itself.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, event_id, subject, heading, body, audiences,
                   sent, failed
              FROM public.email_campaigns
             WHERE status = 'sending'
             ORDER BY id;
            """
        )

    out = []
    for row in rows:
        data = dict(row)
        raw = data.get("audiences")
        # asyncpg hands JSONB back as a string unless a codec is registered,
        # and one codec for one route is not worth the pool-level setup.
        if isinstance(raw, str):
            data["audiences"] = json.loads(raw or "[]")
        out.append(data)
    return out