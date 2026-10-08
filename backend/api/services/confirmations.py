"""The "you're confirmed" email, for every booking that becomes confirmed.

A booking is confirmed by the matcher (a Union sale appeared), by a committee
member pressing Mark bought, or by the committee adding a ticket outright.
Rather than each of those remembering to send, this sends to every confirmed
booking whose email has not gone (`confirmation_email IS NULL`), and records
how it went on the booking: sent, failed, suppressed, or skipped. A new way of
confirming a booking therefore cannot forget the email.

It runs straight after each confirm path, so the email arrives within seconds,
and on the announcer's ten-minute loop, which retries failures and catches
anything the immediate run missed. Claims are taken in the database with SKIP
LOCKED, so overlapping runs never send twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from clients import database
from config import settings
from services import tickets
from utils import email as mailer
from utils.present import format_date, format_time_range

log = logging.getLogger("api.confirmations")

BATCH = 50

# In-process only; the database claim is what actually prevents double sends.
# This just stops one pod piling up concurrent runs after a burst of confirms.
_lock = asyncio.Lock()
_background: set = set()


def _parse(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def send_pending() -> dict:
    """Send every pending confirmation, in batches. Returns counts by outcome."""
    counts = {"sent": 0, "failed": 0, "suppressed": 0}
    # With no mailbox there is nothing to try. Claiming anyway would burn the
    # retry budget during an outage — the annual password reset — and leave
    # bookings marked failed that would have sent fine an hour later.
    if not mailer.configured():
        return {**counts, "skipped": "mail not configured"}

    async with _lock:
        while True:
            rows = await database.post(
                "/reservations/confirmations/claim", params={"limit": BATCH}
            ) or []
            if not rows:
                break
            results = []
            for r in rows:
                address = r.get("email") or ""
                if not mailer.deliverable(address):
                    outcome = "suppressed"
                else:
                    starts, ends = _parse(r.get("starts_at")), _parse(r.get("ends_at"))
                    when = (
                        f"{format_date(starts)}, {format_time_range(starts, ends)}"
                        if starts else "TBC"
                    )
                    try:
                        ok = await mailer.send_booking_confirmed(
                            address,
                            r["event_title"],
                            r["ticket_type_name"],
                            when,
                            venue=r.get("venue"),
                            quantity=r.get("quantity") or 1,
                            url=f"{settings.site_origin}/events/{r['event_slug']}",
                            ticket_url=tickets.ticket_url(r["id"]),
                            qr_url=tickets.qr_url(r["id"]),
                        )
                    except Exception:  # noqa: BLE001 — recorded, retried
                        log.exception("Confirmation email for booking %s failed", r["id"])
                        ok = False
                    outcome = "sent" if ok else "failed"
                counts[outcome] += 1
                results.append({"id": r["id"], "outcome": outcome})
            await database.post("/reservations/confirmations/result", results)
            if len(rows) < BATCH:
                break
    if any(counts.values()):
        log.info("Confirmation emails: %s", counts)
    return counts


def trigger() -> None:
    """Send pending confirmations now, in the background.

    Called after anything that may have confirmed a booking. Returns at once:
    the caller is answering an HTTP request and should not wait on SMTP.
    """
    async def run() -> None:
        try:
            await send_pending()
        except Exception:  # noqa: BLE001 — the ten-minute loop will retry
            log.exception("Immediate confirmation send failed")

    task = asyncio.create_task(run())
    _background.add(task)
    task.add_done_callback(_background.discard)
