"""Subscriptions, opt-outs, sale alerts, campaign records and audience queries.

The interesting part is `audience()`. Everything else is CRUD.

No authorisation here, as everywhere in the adapter — the api service is the
only client and is where the rules live.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body
from fastapi.exceptions import HTTPException

from models.email import (
    Campaign,
    CampaignCreate,
    CampaignDecision,
    CampaignDraftEdit,
    OptOut,
    Recipient,
    SaleAlert,
    SaleAlertDetail,
    Subscription,
    SubscriptionWrite,
)
from postgres import get_pool

router = APIRouter(prefix="/email")

ALERT_COLUMNS = """
    id, event_id, email, user_id, token, created_at, notified_at, cancelled_at
"""

CAMPAIGN_COLUMNS = """
    id, subject, heading, body, audiences, status, recipients, sent, failed,
    error, created_by, created_at, started_at, finished_at, kind, event_id,
    extra_recipients, excluded_recipients, reviewed_by, reviewed_at,
    send_at, edited_at
"""

# The JSONB columns, each a list of strings.
_CAMPAIGN_LISTS = ("audiences", "extra_recipients", "excluded_recipients")


def _campaign(row) -> Campaign:
    """asyncpg hands JSONB back as a string unless a codec is registered, and
    one codec for three columns is not worth the pool-level setup."""
    data = dict(row)
    for column in _CAMPAIGN_LISTS:
        raw = data.get(column)
        if isinstance(raw, str):
            data[column] = json.loads(raw or "[]")
    return Campaign(**data)


# ------------------------------------------------------------- preferences


@router.get("/subscriptions")
async def list_subscriptions(user_id: int) -> list[Subscription]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, topic, subscribed, updated_at
              FROM public.email_subscriptions
             WHERE user_id = $1
             ORDER BY topic;
            """,
            user_id,
        )
    return [Subscription(**dict(r)) for r in rows]


@router.put("/subscriptions/{user_id}")
async def set_subscriptions(
    user_id: int, items: list[SubscriptionWrite] = Body(...)
) -> list[Subscription]:
    """Upsert a batch of topic settings.

    Only the topics named are touched. A preferences screen that posts the whole
    form would otherwise silently reset a topic added by a later release but not
    yet rendered by a cached client.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        for item in items:
            await conn.execute(
                """
                INSERT INTO public.email_subscriptions (user_id, topic, subscribed)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, topic) DO UPDATE
                   SET subscribed = EXCLUDED.subscribed, updated_at = NOW();
                """,
                user_id, item.topic, item.subscribed,
            )
    return await list_subscriptions(user_id)


# ---------------------------------------------------------------- opt-outs


@router.get("/optouts")
async def list_optouts(email: str | None = None) -> list[OptOut]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if email:
            rows = await conn.fetch(
                """
                SELECT email, topic, created_at, reason FROM public.email_optouts
                 WHERE lower(email) = lower($1) ORDER BY topic;
                """,
                email,
            )
        else:
            rows = await conn.fetch(
                "SELECT email, topic, created_at, reason FROM public.email_optouts"
                " ORDER BY created_at DESC LIMIT 1000;"
            )
    return [OptOut(**dict(r)) for r in rows]


@router.post("/optouts", status_code=201)
async def add_optout(opt: OptOut) -> OptOut:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.email_optouts (email, topic, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (lower(email), topic) DO UPDATE
               SET reason = COALESCE(EXCLUDED.reason, public.email_optouts.reason)
            RETURNING email, topic, created_at, reason;
            """,
            opt.email.strip().lower(), opt.topic or "*", opt.reason,
        )
    return OptOut(**dict(row))


@router.delete("/optouts", status_code=204)
async def remove_optout(email: str, topic: str = "*") -> None:
    """Resubscribe. Used by the preferences page, never by a link in an email —
    a one-click *re*-subscribe in a message somebody forwarded would let anyone
    put an address back on the list."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.email_optouts WHERE lower(email) = lower($1) AND topic = $2;",
            email, topic,
        )


# ------------------------------------------------------------- sale alerts


@router.post("/sale-alerts", status_code=201)
async def create_sale_alert(alert: SaleAlert) -> SaleAlert:
    """Ask to be told when an event's tickets open.

    Idempotent: asking twice returns the existing row rather than 409ing, because
    from the person's point of view pressing the button again is not an error and
    the answer either way is "yes, we'll tell you".
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            f"""
            SELECT {ALERT_COLUMNS} FROM public.event_sale_alerts
             WHERE event_id = $1 AND lower(email) = lower($2) AND cancelled_at IS NULL;
            """,
            alert.event_id, alert.email,
        )
        if existing is not None:
            # Attach the account if we have since learned of one.
            if alert.user_id and existing["user_id"] is None:
                existing = await conn.fetchrow(
                    f"UPDATE public.event_sale_alerts SET user_id = $2"
                    f" WHERE id = $1 RETURNING {ALERT_COLUMNS};",
                    existing["id"], alert.user_id,
                )
            return SaleAlert(**dict(existing))

        row = await conn.fetchrow(
            f"""
            INSERT INTO public.event_sale_alerts (event_id, email, user_id)
            VALUES ($1, $2, $3)
            RETURNING {ALERT_COLUMNS};
            """,
            alert.event_id, alert.email.strip().lower(), alert.user_id,
        )
    return SaleAlert(**dict(row))


@router.get("/sale-alerts")
async def list_sale_alerts(
    event_id: int | None = None,
    email: str | None = None,
    user_id: int | None = None,
    pending_only: bool = False,
) -> list[SaleAlertDetail]:
    clauses = ["a.cancelled_at IS NULL"]
    args: list = []
    if event_id is not None:
        args.append(event_id)
        clauses.append(f"a.event_id = ${len(args)}")
    if email:
        args.append(email)
        clauses.append(f"lower(a.email) = lower(${len(args)})")
    if user_id is not None:
        args.append(user_id)
        clauses.append(f"a.user_id = ${len(args)}")
    if pending_only:
        clauses.append("a.notified_at IS NULL")

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT a.id, a.event_id, a.email, a.user_id, a.token, a.created_at,
                   a.notified_at, a.cancelled_at,
                   e.slug AS event_slug, e.title AS event_title,
                   e.starts_at AS event_starts_at, e.sales_open_at
              FROM public.event_sale_alerts a
              JOIN public.events e ON e.id = a.event_id
             WHERE {' AND '.join(clauses)}
             ORDER BY a.created_at;
            """,
            *args,
        )
    return [SaleAlertDetail(**dict(r)) for r in rows]


@router.get("/sale-alerts/by-token/{token}")
async def sale_alert_by_token(token: str) -> SaleAlertDetail:
    """Resolve the handle in a "stop telling me about this" link."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT a.id, a.event_id, a.email, a.user_id, a.token, a.created_at,
                   a.notified_at, a.cancelled_at,
                   e.slug AS event_slug, e.title AS event_title,
                   e.starts_at AS event_starts_at, e.sales_open_at
              FROM public.event_sale_alerts a
              JOIN public.events e ON e.id = a.event_id
             WHERE a.token = $1;
            """,
            token,
        )
    if row is None:
        raise HTTPException(404, "No such alert")
    return SaleAlertDetail(**dict(row))


@router.post("/sale-alerts/{alert_id}/notified", status_code=204)
async def mark_alert_notified(alert_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.event_sale_alerts SET notified_at = NOW() WHERE id = $1;",
            alert_id,
        )


@router.post("/sale-alerts/{alert_id}/cancel", status_code=204)
async def cancel_sale_alert(alert_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.event_sale_alerts SET cancelled_at = NOW() WHERE id = $1;",
            alert_id,
        )


# -------------------------------------------------------------- campaigns


@router.post("/campaigns", status_code=201)
async def create_campaign(campaign: CampaignCreate) -> Campaign:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.email_campaigns
                (subject, heading, body, audiences, created_by, status, kind,
                 event_id, send_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9)
            RETURNING {CAMPAIGN_COLUMNS};
            """,
            campaign.subject, campaign.heading, campaign.body,
            json.dumps(campaign.audiences), campaign.created_by,
            campaign.status, campaign.kind, campaign.event_id, campaign.send_at,
        )
    return _campaign(row)


@router.get("/campaigns")
async def list_campaigns(
    limit: int = 50,
    status: str | None = None,
    kind: str | None = None,
    event_id: int | None = None,
) -> list[Campaign]:
    """Newest first, optionally narrowed.

    `status` takes a comma-separated list — the approval queue asks for
    `awaiting_approval,scheduled`, the sender for `scheduled`. `kind` and
    `event_id` find one event's on-sale email, whatever state it is in.
    """
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {CAMPAIGN_COLUMNS} FROM public.email_campaigns"
            f" WHERE ($2::varchar[] IS NULL OR status = ANY($2::varchar[]))"
            f"   AND ($3::varchar IS NULL OR kind = $3::varchar)"
            f"   AND ($4::bigint IS NULL OR event_id = $4::bigint)"
            f" ORDER BY created_at DESC LIMIT $1;",
            limit, statuses, kind, event_id,
        )
    return [_campaign(r) for r in rows]


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int) -> Campaign:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {CAMPAIGN_COLUMNS} FROM public.email_campaigns WHERE id = $1;",
            campaign_id,
        )
    if row is None:
        raise HTTPException(404, "No such campaign")
    return _campaign(row)


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    status: str | None = None,
    recipients: int | None = None,
    sent: int | None = None,
    failed: int | None = None,
    error: str | None = None,
    kind: str | None = None,
    event_id: int | None = None,
) -> Campaign:
    """Progress, recorded as the send runs.

    `started_at` and `finished_at` are derived from the status rather than
    passed in, so a caller cannot record a finish time for something still
    running.

    `kind` and `event_id` are written once, by the send that starts (not by
    the create) — they are what a *different* process reads back to rebuild an
    interrupted send, and passing them here means the resume path needs no
    second endpoint. `COALESCE` keeps whatever the row already holds whenever
    a caller leaves a field out, which is what makes this additive for the
    callers that never send them.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.email_campaigns SET
                status      = COALESCE($2, status),
                recipients  = COALESCE($3, recipients),
                sent        = COALESCE($4, sent),
                failed      = COALESCE($5, failed),
                error       = COALESCE($6, error),
                kind        = COALESCE($7, kind),
                event_id    = COALESCE($8, event_id),
                started_at  = CASE WHEN $2 = 'sending' THEN NOW() ELSE started_at END,
                finished_at = CASE WHEN $2 IN ('sent','failed') THEN NOW() ELSE finished_at END
             WHERE id = $1
            RETURNING {CAMPAIGN_COLUMNS};
            """,
            campaign_id, status, recipients, sent, failed, error, kind, event_id,
        )
    if row is None:
        raise HTTPException(404, "No such campaign")
    return _campaign(row)


async def _missing_or_conflict(conn, campaign_id: int) -> HTTPException:
    """Why a conditional write on a campaign matched nothing: it is not there,
    or it has already left the approval queue."""
    status = await conn.fetchval(
        "SELECT status FROM public.email_campaigns WHERE id = $1;", campaign_id
    )
    if status is None:
        return HTTPException(404, "No such campaign")
    return HTTPException(409, f"That email is no longer waiting for approval ({status}).")


@router.put("/campaigns/{campaign_id}/draft")
async def edit_draft(campaign_id: int, edit: CampaignDraftEdit) -> Campaign:
    """Change a draft that is waiting for approval.

    Only while it waits: the WHERE carries the status, so an edit landing after
    somebody pressed Send changes nothing about a message already going out —
    and says so with a 409 rather than appearing to have worked.

    A person's edit (`by_hand`) stamps `edited_at`, which is what stops the
    draft being rewritten from its event afterwards. The system's own updates
    do not, and may also move the send time of an email already scheduled —
    the sale moved, so the email moves with it.
    """
    def as_json(values: list[str] | None) -> str | None:
        return None if values is None else json.dumps(values)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.email_campaigns SET
                subject             = COALESCE($2, subject),
                heading             = COALESCE($3, heading),
                body                = COALESCE($4, body),
                audiences           = COALESCE($5::jsonb, audiences),
                extra_recipients    = COALESCE($6::jsonb, extra_recipients),
                excluded_recipients = COALESCE($7::jsonb, excluded_recipients),
                send_at             = COALESCE($8, send_at),
                edited_at           = CASE WHEN $9 THEN NOW() ELSE edited_at END
             WHERE id = $1
               AND (status = 'awaiting_approval' OR (NOT $9 AND status = 'scheduled'))
            RETURNING {CAMPAIGN_COLUMNS};
            """,
            campaign_id, edit.subject, edit.heading, edit.body,
            as_json(edit.audiences), as_json(edit.extra_recipients),
            as_json(edit.excluded_recipients), edit.send_at, edit.by_hand,
        )
        if row is None:
            raise await _missing_or_conflict(conn, campaign_id)
    return _campaign(row)


@router.post("/campaigns/{campaign_id}/decide")
async def decide(campaign_id: int, decision: CampaignDecision) -> Campaign:
    """Move a draft along: approve it to send now (`sending`) or at its time
    (`scheduled`), take an approval back (`awaiting_approval`), or dismiss it.

    One conditional UPDATE is the whole guard against a double approval — the
    second press, or a second pod's sender reaching the same due email, finds
    the status already moved and gets a 409, so it never starts a second send.
    `note` replaces whatever the row said before: why a scheduled email was
    held back, or nothing once somebody approves it again.
    """
    allowed = {
        "awaiting_approval": {"sending", "scheduled", "dismissed"},
        "scheduled": {"sending", "awaiting_approval", "dismissed"},
    }
    if decision.status not in allowed.get(decision.expect, set()):
        raise HTTPException(
            400, f"A draft cannot go from {decision.expect} to {decision.status}."
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.email_campaigns SET
                status      = $2::varchar,
                reviewed_by = $4,
                reviewed_at = NOW(),
                recipients  = COALESCE($5, recipients),
                error       = $6,
                started_at  = CASE WHEN $2::varchar = 'sending' THEN NOW() ELSE started_at END,
                finished_at = CASE WHEN $2::varchar = 'dismissed' THEN NOW() ELSE finished_at END
             WHERE id = $1 AND status = $3::varchar
            RETURNING {CAMPAIGN_COLUMNS};
            """,
            campaign_id, decision.status, decision.expect,
            decision.reviewed_by, decision.recipients, decision.note,
        )
        if row is None:
            raise await _missing_or_conflict(conn, campaign_id)
    return _campaign(row)


# --------------------------------------------------------------- audiences
#
# One endpoint, several shapes, because every one of them ends in the same
# thing: a de-duplicated list of addresses that have not opted out.


@router.get("/audience")
async def audience(
    kind: str,
    topic: str | None = None,
    default_on: bool = False,
    academic_year: str | None = None,
    event_id: int | None = None,
    include_unverified: bool = False,
) -> list[Recipient]:
    """Resolve one audience key to addresses.

    `kind` is one of:

      members   — everyone ICU reports as a member for `academic_year`. Taken
                  from the synced membership feed, not from `users`: most
                  members have never signed in to the site, and they are the
                  people a society newsletter is actually for.
      accounts  — everyone with an account on the site.
      topic     — everyone subscribed to `topic`. `default_on` says what an
                  account with no row for that topic counts as; it lives in the
                  API's topic registry, so changing a default needs no
                  migration.
      alerts    — addresses waiting to hear that `event_id` is on sale.

    Opt-outs are applied here rather than by the caller. Putting them in the
    query is the only way a new audience cannot forget them.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        if kind == "members":
            rows = await conn.fetch(
                """
                SELECT m.email, m.first_name, m.last_name, u.id AS user_id
                  FROM public.members m
                  LEFT JOIN public.users u ON lower(u.email) = lower(m.email)
                 WHERE m.academic_year = $1
                   AND m.email IS NOT NULL AND m.email <> ''
                   AND NOT EXISTS (
                       SELECT 1 FROM public.email_optouts o
                        WHERE lower(o.email) = lower(m.email)
                          AND (o.topic = '*' OR o.topic = $2)
                   )
                 ORDER BY m.email;
                """,
                academic_year or "", topic or "newsletter",
            )
            return [Recipient(**dict(r), source="members") for r in rows]

        if kind == "accounts":
            rows = await conn.fetch(
                """
                SELECT u.email, u.first_name, u.last_name, u.id AS user_id
                  FROM public.users u
                 WHERE ($1 OR u.email_verified)
                   AND NOT EXISTS (
                       SELECT 1 FROM public.email_optouts o
                        WHERE lower(o.email) = lower(u.email)
                          AND (o.topic = '*' OR o.topic = $2)
                   )
                 ORDER BY u.email;
                """,
                include_unverified, topic or "newsletter",
            )
            return [Recipient(**dict(r), source="accounts") for r in rows]

        if kind == "topic":
            if not topic:
                raise HTTPException(400, "kind=topic needs a topic")
            # An account with no row counts as `default_on`. LEFT JOIN rather
            # than an IN list, so "subscribed by default" is one query and not
            # "every user minus the explicit noes".
            rows = await conn.fetch(
                """
                SELECT u.email, u.first_name, u.last_name, u.id AS user_id
                  FROM public.users u
                  LEFT JOIN public.email_subscriptions s
                         ON s.user_id = u.id AND s.topic = $1
                 WHERE ($3 OR u.email_verified)
                   AND COALESCE(s.subscribed, $2) = true
                   AND NOT EXISTS (
                       SELECT 1 FROM public.email_optouts o
                        WHERE lower(o.email) = lower(u.email)
                          AND (o.topic = '*' OR o.topic = $1)
                   )
                 ORDER BY u.email;
                """,
                topic, default_on, include_unverified,
            )
            return [Recipient(**dict(r), source=f"topic:{topic}") for r in rows]

        if kind == "alerts":
            if event_id is None:
                raise HTTPException(400, "kind=alerts needs an event_id")
            rows = await conn.fetch(
                """
                SELECT a.email, u.first_name, u.last_name, a.user_id
                  FROM public.event_sale_alerts a
                  LEFT JOIN public.users u ON u.id = a.user_id
                 WHERE a.event_id = $1
                   AND a.cancelled_at IS NULL
                   AND a.notified_at IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM public.email_optouts o
                        WHERE lower(o.email) = lower(a.email)
                          AND (o.topic = '*' OR o.topic = $2)
                   )
                 ORDER BY a.created_at;
                """,
                event_id, topic or "tickets_on_sale",
            )
            return [Recipient(**dict(r), source="alerts") for r in rows]

    raise HTTPException(400, f"Unknown audience kind '{kind}'")


@router.get("/event-categories")
async def event_categories() -> list[str]:
    """Distinct categories across published events, for the per-category
    subscription topics. Derived rather than configured: a category exists
    because an event uses it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT category FROM public.events
             WHERE category IS NOT NULL AND category <> ''
             ORDER BY category;
            """
        )
    return [r["category"] for r in rows]


# ------------------------------------------------------------- test list


@router.get("/test-recipients")
async def list_test_recipients() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT email, added_by, created_at FROM public.email_test_recipients "
            "ORDER BY created_at, email;"
        )
    return [dict(r) for r in rows]


@router.post("/test-recipients", status_code=201)
async def add_test_recipient(body: dict) -> dict:
    email = (body.get("email") or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "That doesn't look like an email address.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.email_test_recipients (email, added_by) VALUES ($1, $2) "
            "ON CONFLICT (email) DO NOTHING;",
            email, body.get("added_by"),
        )
    return {"email": email}


@router.delete("/test-recipients", status_code=204)
async def remove_test_recipient(email: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.email_test_recipients WHERE email = lower($1);", email
        )
