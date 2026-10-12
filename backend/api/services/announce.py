"""The job that runs on a clock rather than on a request: last-chance
reminders, for a hold that is about to lapse unpaid. One email to one person
about their own booking, so it goes by itself. The same pass retries any
booking confirmation that failed to send.

The "tickets are on sale" email is not here any more. It used to be sent by
this loop the moment `sales_open_at` passed; now it is drafted as soon as an
event has a sale time, approved by a person, and sent at that time by the
scheduler in services/campaigns.py.

Runs once an hour, on the hour. Database-only: it never touches eActivities,
so it spends nothing against the Union's rate limit. Work is claimed in
the database *before* sending, so a crash mid-pass loses an email rather than
sending it twice — the right way round, because somebody mailed twice
unsubscribes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from clients import database
from config import settings
from services import confirmations, purchase
from utils import clock
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
    """The reminders and the confirmation retries, guarded so two passes
    cannot overlap.

    A slow pass is possible — each is one SMTP exchange — and without the lock
    the next tick would start working the same backlog alongside it.
    """
    if _lock.locked():
        log.info("Previous reminder pass still running — skipping this tick")
        return {"skipped": True}

    async with _lock:
        chased = await chase_unpaid()
        # Retries failed confirmation emails and picks up any confirm path that
        # did not trigger a send itself — the daily sync runs this too.
        confirmed = await confirmations.send_pending()
    return {"chased": chased, "confirmations": confirmed}


async def scheduler() -> None:
    """Background loop: once an hour, on the hour. Cancelled on shutdown by the
    lifespan handler.

    Hourly is plenty for both jobs: the last-chance email is due a day before
    a hold lapses, and a confirmation is sent the moment a booking confirms —
    this pass only retries the ones that failed.
    """
    while True:
        try:
            await asyncio.sleep(clock.seconds_until_next_hour())
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad pass kill the loop — the next hour's retries,
            # and the claim columns mean nothing is sent twice.
            log.exception("Reminder pass failed")
