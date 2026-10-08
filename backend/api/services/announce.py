"""The two jobs that run on a clock rather than on a request.

  * on-sale announcements — an event whose `sales_open_at` has just passed,
    emailed to everyone who asked about that event and to the people subscribed
    to its category;
  * last-chance reminders — a hold that is about to lapse unpaid.

Both are database-only. They never touch eActivities, so they can run every ten
minutes without spending anything against the Union's rate limit — which
matters, because "tickets are on sale" is worthless an hour late.

Both claim their work in the database *before* sending. A crash mid-send
therefore loses an email rather than sending it twice, which is the right way
round: somebody who never hears about the sale can still see the event on the
site, while somebody mailed twice by a retry loop unsubscribes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from clients import database
from config import settings
from services import confirmations, emailing, purchase
from utils import email as mailer
from utils.present import format_date, format_price, format_time_range, london

log = logging.getLogger("api.announce")

_lock = asyncio.Lock()


def _parse(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _when(event: dict) -> str:
    starts, ends = _parse(event.get("starts_at")), _parse(event.get("ends_at"))
    if starts is None:
        return "TBC"
    return f"{format_date(starts)}, {format_time_range(starts, ends)}"


def _ticket_lines(event: dict) -> list[tuple[str, str]]:
    return [
        (t["name"], format_price(t.get("price_pence", 0)))
        for t in event.get("ticket_types", [])
        if not t.get("sold_out")
    ]


# ------------------------------------------------------- on-sale announcements


async def announce_sales() -> dict:
    """Tell people about every event that has just opened for booking."""
    due = await database.get("/events/due-announcement") or []
    results = []
    for event in due:
        # Claim first. Two workers reaching the same event must not both decide
        # they are the one sending, and the claim is conditional in SQL.
        claim = await database.post(f"/events/{event['id']}/announced")
        if not (claim or {}).get("claimed"):
            log.info("Event %s was already claimed for announcement", event["slug"])
            continue
        try:
            results.append(await _announce_one(event))
        except Exception:  # noqa: BLE001
            log.exception("Announcing %s failed", event["slug"])
            results.append({"event": event["slug"], "sent": 0, "error": True})
    return {"events": len(results), "results": results}


async def _announce_one(event: dict) -> dict:
    """One event's on-sale email.

    Two audiences, merged and de-duplicated: the people who pressed "tell me
    when this is on sale" on this specific event, and the standing subscribers
    to tickets going on sale generally (plus this event's own category). The
    per-event askers are listed first so their alert row is the one marked
    notified.
    """
    keys = [f"alerts:{event['id']}", "topic:tickets_on_sale"]
    if event.get("category"):
        keys.append(f"topic:{emailing.category_topic(event['category'])}")

    recipients = await emailing.resolve(keys)
    if not recipients:
        log.info("Nothing to send for %s — no subscribers", event["slug"])
        return {"event": event["slug"], "recipients": 0, "sent": 0}

    url = f"{settings.site_origin}/events/{event['slug']}"
    tickets = _ticket_lines(event)
    links = await purchase.links_for_event(event)
    when = _when(event)

    # Recorded as a campaign like any other bulk send, so it appears in the
    # same history the committee reads and a half-finished send is visible.
    campaign = await database.post(
        "/email/campaigns",
        {
            "subject": f"Tickets for {event['title']} are on sale",
            "heading": f"Tickets for {event['title']} are on sale",
            "body": "(automatic on-sale announcement)",
            "audiences": keys,
        },
    )

    # Alerts are marked notified by address, so somebody who both asked about
    # this event and subscribed generally is not left with a live alert that
    # fires again later.
    alerts = await database.get(
        "/email/sale-alerts", {"event_id": event["id"], "pending_only": True}
    ) or []
    by_address = {a["email"].strip().lower(): a for a in alerts}

    async def build(batch: list[dict], unsubscribe: str) -> list[str]:
        """Somebody who asked about this one event gets their own message with a
        link that stops just this alert; offering them a general unsubscribe
        would be a trap. Everybody else is a standing subscriber with nothing
        personal in theirs, so they share one BCC'd message."""
        subscribers = [r["email"] for r in batch if r["email"] not in by_address]
        reached: list[str] = []
        if subscribers:
            reached += await mailer.send_tickets_on_sale(
                subscribers,
                event["title"],
                when,
                event.get("venue") or "",
                url,
                unsubscribe,
                tickets=tickets,
                purchase_url=links.get(None),
            )
        for recipient in batch:
            alert = by_address.get(recipient["email"])
            if alert is None:
                continue
            reached += await mailer.send_tickets_on_sale(
                recipient["email"],
                event["title"],
                when,
                event.get("venue") or "",
                url,
                emailing.alert_cancel_url(alert["token"]),
                tickets=tickets,
                purchase_url=links.get(None),
            )
            # Marked either way: an address that cannot be delivered to must not
            # be retried on every pass for the rest of the year.
            await database.post(f"/email/sale-alerts/{alert['id']}/notified")
            # Still one message each, so still paced under Exchange's limit.
            if settings.bulk_send_interval_seconds > 0:
                await asyncio.sleep(settings.bulk_send_interval_seconds)
        return reached

    result = await emailing.send_bulk(campaign["id"], recipients, build)
    log.info(
        "Announced %s to %d recipient(s): %d sent",
        event["slug"], len(recipients), result["sent"],
    )
    return {"event": event["slug"], "recipients": len(recipients), **result}


# ----------------------------------------------------- last-chance reminders


async def chase_unpaid() -> dict:
    """One warning per unpaid hold, shortly before it lapses."""
    due = await database.get(
        "/reservations/needing-reminder",
        {"within_hours": settings.purchase_reminder_hours_before},
    ) or []

    sent = 0
    for reservation in due:
        claim = await database.post(f"/reservations/{reservation['id']}/reminded")
        if not (claim or {}).get("claimed"):
            continue
        try:
            if await _chase_one(reservation):
                sent += 1
        except Exception:  # noqa: BLE001
            log.exception("Could not chase reservation %s", reservation["id"])

    if due:
        log.info("Last-chance reminders: %d due, %d sent", len(due), sent)
    return {"due": len(due), "sent": sent}


async def _chase_one(reservation: dict) -> bool:
    address = reservation.get("user_email")
    if not address:
        return False

    event = await database.get(f"/events/{reservation['event_id']}")
    if event is None:
        return False

    expires = _parse(reservation.get("expires_at"))
    deadline = (
        f"{format_date(expires)} at {london(expires).strftime('%H:%M')}" if expires else "soon"
    )
    return await mailer.send_last_chance(
        address,
        event["title"],
        reservation.get("ticket_type_name") or "your ticket",
        reservation["quantity"],
        deadline,
        f"{settings.site_origin}/events/{event['slug']}",
        purchase_url=await purchase.link_for(event, reservation["ticket_type_id"]),
    )


# ---------------------------------------------------------------- the loop


async def run_once() -> dict:
    """Both jobs, guarded so two passes cannot overlap.

    A slow bulk send is the normal case — pacing means a few hundred recipients
    takes minutes — and without the lock the next tick would start announcing
    the same backlog alongside it.
    """
    if _lock.locked():
        log.info("Previous announce pass still running — skipping this tick")
        return {"skipped": True}

    async with _lock:
        announced = await announce_sales()
        chased = await chase_unpaid()
        # Retries failed confirmation emails and picks up any confirm path that
        # did not trigger a send itself — the daily sync runs this too.
        confirmed = await confirmations.send_pending()
    return {"announced": announced, "chased": chased, "confirmations": confirmed}


async def scheduler() -> None:
    """Background loop. Cancelled on shutdown by the lifespan handler."""
    interval = max(settings.announce_interval_minutes, 1) * 60
    while True:
        try:
            await asyncio.sleep(interval)
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad pass kill the loop — the next one will retry,
            # and the claim columns mean nothing is sent twice.
            log.exception("Announce pass failed")
            await asyncio.sleep(60)
