"""The two jobs that run on a clock rather than on a request.

  * on-sale announcements — an event whose `sales_open_at` has just passed gets
    its "tickets are on sale" email *written*, addressed to everyone who asked
    about that event and the people subscribed to its category, and left in
    the approval queue. A timer never sends to a crowd; a committee member
    does, from the queue (services/campaigns.py);
  * last-chance reminders — a hold that is about to lapse unpaid. One email to
    one person about their own booking, so it still goes on its own.

Both are database-only. They never touch eActivities, so they can run every ten
minutes without spending anything against the Union's rate limit — which
matters, because "tickets are on sale" is worth little an hour late, and the
draft should be waiting for whoever approves it as soon as the sale opens.

Both claim their work in the database *before* acting. A crash mid-pass
therefore loses an email rather than producing it twice, which is the right way
round: somebody who never hears about the sale can still see the event on the
site, while somebody mailed twice by a retry loop unsubscribes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from clients import database
from config import settings
from services import campaigns, confirmations, emailing, purchase
from utils import email as mailer
from utils.present import format_date, format_doors_close, london

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


# ------------------------------------------------------- on-sale announcements


async def announce_sales() -> dict:
    """Write the on-sale email for every event that has just opened for booking.

    Written, not sent: it goes into the approval queue, and a committee member
    reads it, edits it if need be, and presses Send. A sale time typed wrong,
    or a price changed an hour before, used to reach every subscriber the
    moment the clock passed it; now it reaches one person first.
    """
    due = await database.get("/events/due-announcement") or []
    results = []
    for event in due:
        # Claim first. Two workers reaching the same event must not both decide
        # they are the one writing it, and the claim is conditional in SQL —
        # one draft per event, however many passes see it.
        claim = await database.post(f"/events/{event['id']}/announced")
        if not (claim or {}).get("claimed"):
            log.info("Event %s was already claimed for announcement", event["slug"])
            continue
        try:
            results.append(await _queue_one(event))
        except Exception:  # noqa: BLE001
            log.exception("Writing the on-sale email for %s failed", event["slug"])
            results.append({"event": event["slug"], "queued": None, "error": True})
    return {"events": len(results), "results": results}


async def _queue_one(event: dict) -> dict:
    """One event's on-sale email, as a draft waiting for approval.

    Addressed to two audiences, merged and de-duplicated at send: the people
    who pressed "tell me when this is on sale" on this specific event, and the
    standing subscribers to tickets going on sale generally (plus this event's
    own category). Both are ticked boxes on the draft, so the committee can
    add a department list or take one off before it goes.

    Written even when nobody is subscribed, because "nobody asked" is not
    "nobody should be told" — a year list can still be ticked.
    """
    keys = [f"alerts:{event['id']}", "topic:tickets_on_sale"]
    if event.get("category"):
        keys.append(f"topic:{emailing.category_topic(event['category'])}")

    subject = f"Tickets for {event['title']} are on sale"
    campaign = await campaigns.queue(
        subject=subject,
        heading=subject,
        # The opening line, editable. The date, venue, prices and booking link
        # are rendered underneath from the event at send time, so they are
        # current whenever the committee gets round to pressing Send.
        body=f"Tickets for **{event['title']}** are on sale now.",
        audiences=keys,
        kind="on_sale",
        event_id=event["id"],
    )
    return {
        "event": event["slug"],
        "queued": campaign["id"],
        "recipients": campaign.get("recipients", 0),
    }


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
        doors_close=format_doors_close(event.get("starts_at"), event.get("doors_close_at")),
    )


# ---------------------------------------------------------------- the loop


async def run_once() -> dict:
    """Both jobs, guarded so two passes cannot overlap.

    A slow pass is possible — the reminders and confirmations are one SMTP
    exchange each — and without the lock the next tick would start working the
    same backlog alongside it.
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
