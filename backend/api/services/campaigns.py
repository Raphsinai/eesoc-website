"""A stored campaign row, turned back into mail — and the queue of rows that
wait for a person to say yes.

Both live here because both work from the row alone:

  * rebuilding the message: the preview a committee member reads, the `build`
    closure `emailing.send_bulk` runs, and that same closure again when a pod
    replaced mid-send resumes it. One builder for all three, so what is
    approved is what is sent, and what is resumed is what was approved;
  * the approval queue: an automated bulk email is written as a campaign
    `awaiting_approval` (migration 073), and nothing but a committee member's
    Send moves it on.

The rule the queue implements: nothing addressed to a crowd is sent by a timer.
Mail about one person's own booking or sign-in — the hold, the confirmation,
the waitlist offer, the last-chance reminder, the sign-in code — is the answer
to something they did, is not here, and still goes at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from clients import database
from config import settings
from services import emailing, purchase
from utils import email as mailer
from utils.auth import normalise_email
from utils.present import format_date, format_doors_close, format_price, format_time_range

log = logging.getLogger("api.campaigns")

# What each kind is called on the admin screen.
KIND_LABELS = {
    "newsletter": "Newsletter",
    "event_announcement": "Email about an event",
    "on_sale": "Tickets on sale",
}

# The kinds whose message is built around one event's details.
EVENT_KINDS = ("event_announcement", "on_sale")


class Unbuildable(Exception):
    """The row cannot be turned back into a message. The text says why."""


def _parse(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def event_when(event: dict) -> str:
    starts, ends = _parse(event.get("starts_at")), _parse(event.get("ends_at"))
    if starts is None:
        return "TBC"
    return f"{format_date(starts)}, {format_time_range(starts, ends)}"


def event_tickets(event: dict) -> list[tuple[str, str]]:
    return [
        (t["name"], format_price(t.get("price_pence", 0)))
        for t in event.get("ticket_types", [])
        if not t.get("sold_out")
    ]


async def _event(row: dict) -> dict:
    event_id = row.get("event_id")
    event = await database.get(f"/events/{event_id}") if event_id is not None else None
    if event is None:
        # Deleted between the draft being written and now. A campaign is an
        # audit record that outlives its subject (no FK, migration 037), so
        # this is a reason to refuse, not a crash.
        raise Unbuildable(f"event {event_id} no longer exists")
    return event


# ----------------------------------------------------------- the message


async def message_for(row: dict) -> Callable[[str], tuple[str, str, str]]:
    """A function from an unsubscribe link to (subject, text, html).

    Everything that needs the database — the event, its purchase links — is
    fetched once here, so the function the sender calls per batch is pure.
    """
    kind = (row.get("kind") or "newsletter").strip()
    subject = (row.get("subject") or "").strip()
    heading = (row.get("heading") or "").strip() or subject
    html, text = emailing.render_body(row.get("body") or "")

    if kind == "newsletter":
        return lambda unsubscribe: mailer.campaign_message(
            subject, heading, text, html, unsubscribe
        )

    if kind in EVENT_KINDS:
        event = await _event(row)
        url = f"{settings.site_origin}/events/{event['slug']}"
        when = event_when(event)
        venue = event.get("venue") or ""
        tickets = event_tickets(event)
        doors = format_doors_close(event.get("starts_at"), event.get("doors_close_at"))

        if kind == "on_sale":
            links = await purchase.links_for_event(event)
            return lambda unsubscribe: mailer.tickets_on_sale_message(
                event["title"], when, venue, url, unsubscribe,
                tickets=tickets, purchase_url=links.get(None), doors_close=doors,
                subject=subject, heading=heading, body_text=text, body_html=html,
            )

        return lambda unsubscribe: mailer.event_announcement_message(
            subject, event["title"], when, venue, url, unsubscribe,
            body_text=text, body_html=html, tickets=tickets, heading=heading,
            doors_close=doors,
        )

    # A kind from a future deploy, or a typo in the column.
    raise Unbuildable(f"unknown campaign kind {kind!r}")


async def builder_for(row: dict, *, mark_alerts: bool = True):
    """The `build(batch, unsubscribe)` closure `emailing.send_bulk` runs.

    Every batch is one message with the whole batch BCC'd — including the
    people who pressed "tell me when this is on sale" on the event itself.
    They used to get a message each, for the sake of a footer link that
    cancelled an alert which had, by then, already fired; that bought nothing
    a shared unsubscribe does not, at the price of one paced send per person.

    Their alert is still marked notified as they are sent to, so it can never
    fire again. `mark_alerts=False` is for a test send, which tells nobody.
    """
    message = await message_for(row)

    alerts: dict[str, dict] = {}
    if mark_alerts and row.get("kind") == "on_sale":
        # Pending only: a resumed send must not re-find the alerts the
        # previous sender already marked.
        pending = await database.get(
            "/email/sale-alerts", {"event_id": row["event_id"], "pending_only": True}
        ) or []
        alerts = {a["email"].strip().lower(): a for a in pending}

    async def build(batch: list[dict], unsubscribe: str) -> list[str]:
        subject, text, html = message(unsubscribe)
        emails = [r["email"] for r in batch]
        reached = await mailer.send_bulk_message(emails, subject, text, html, unsubscribe)
        for email in emails:
            alert = alerts.pop(email.strip().lower(), None)
            if alert is not None:
                # Marked whether or not it was delivered: an address that
                # cannot be reached must not be retried for the rest of the year.
                await database.post(f"/email/sale-alerts/{alert['id']}/notified")
        return reached

    return build


async def preview(row: dict) -> dict:
    """The whole message as it will arrive — same builder as the send."""
    message = await message_for(row)
    subject, text, html = message(emailing.general_unsubscribe_url())
    return {"subject": subject, "text": text, "html": html}


# ------------------------------------------------------------- recipients


def clean_addresses(values: list[str] | None) -> list[str]:
    """Lowercased, de-duplicated, order kept; anything without an @ dropped."""
    out: list[str] = []
    for value in values or []:
        address = normalise_email(str(value or ""))
        if "@" in address and not address.startswith("@") and address not in out:
            out.append(address)
    return out


async def recipients_for(row: dict) -> tuple[list[dict], list[str]]:
    """Who a draft goes to right now: (recipients, skipped).

    The ticked audiences, resolved fresh — so anybody who unsubscribed while
    the draft waited is not on it — plus the addresses added by hand, minus
    the ones taken off by hand.

    An address added by hand that has unsubscribed from everything is
    *skipped*, and said so: putting somebody back on a mailing by typing their
    address is the one thing an unsubscribe has to prevent. The department
    lists are the exception, as everywhere — they are the department's, not
    ours to unsubscribe.
    """
    excluded = set(clean_addresses(row.get("excluded_recipients")))
    department = {d["email"] for d in emailing.DEPARTMENT_LISTS}

    recipients = await emailing.resolve(list(row.get("audiences") or []))
    seen = {r["email"] for r in recipients}

    skipped: list[str] = []
    extras = [e for e in clean_addresses(row.get("extra_recipients")) if e not in seen]
    if extras:
        optouts = await database.get("/email/optouts") or []
        unsubscribed = {
            (o.get("email") or "").strip().lower() for o in optouts if o.get("topic") == "*"
        }
        for address in extras:
            if address in unsubscribed and address not in department:
                skipped.append(address)
                continue
            recipients.append({
                "email": address, "source": "added", "list": address in department,
                "first_name": None, "last_name": None, "user_id": None,
            })
            seen.add(address)

    final = []
    for r in recipients:
        if r["email"] in excluded:
            continue
        final.append({**r, "list": bool(r.get("list")) or r["email"] in department})
    return final, skipped


# ------------------------------------------------------------------ queue


async def queue(
    *,
    subject: str,
    body: str,
    audiences: list[str],
    kind: str,
    heading: str | None = None,
    event_id: int | None = None,
) -> dict:
    """Write an automated bulk email as a draft waiting for approval.

    This is the only thing a timer may do with mail addressed to a crowd. The
    recipient count is worked out now so the queue can show it, and is worked
    out again at Send — it is a preview, not a promise.
    """
    campaign = await database.post(
        "/email/campaigns",
        {
            "subject": subject,
            "heading": heading or subject,
            "body": body,
            "audiences": audiences,
            "status": "awaiting_approval",
            "kind": kind,
            "event_id": event_id,
        },
    )
    try:
        recipients, _ = await recipients_for(campaign)
        campaign = await database.patch(
            f"/email/campaigns/{campaign['id']}", params={"recipients": len(recipients)}
        ) or campaign
    except Exception:  # noqa: BLE001 — a count is not worth losing the draft over
        log.exception("Could not count the recipients of draft %s", campaign["id"])

    log.info(
        "Queued %s %r for approval (%s recipient(s))",
        kind, subject, campaign.get("recipients"),
    )
    await _notify(campaign)
    return campaign


async def _notify(campaign: dict) -> None:
    """Tell whoever approves that something is waiting. Never raises."""
    to = [a.strip() for a in settings.email_approval_notify.split(",") if "@" in a]
    if not to:
        return
    url = f"{settings.site_origin}/admin?tab=email&review={campaign['id']}"
    try:
        await mailer.send_approval_needed(
            to,
            campaign.get("subject") or "",
            KIND_LABELS.get(campaign.get("kind"), campaign.get("kind") or "Email"),
            int(campaign.get("recipients") or 0),
            url,
        )
    except Exception:  # noqa: BLE001 — the draft is safe; the dashboard still shows it
        log.exception("Could not send the approval notice for draft %s", campaign["id"])


def warning(row: dict, event: dict | None) -> str | None:
    """Why somebody should think twice before sending this draft, if anything.

    Shown, not enforced, except for the one case that cannot be sent at all:
    whether a reminder about an event that has already started is still worth
    sending is the committee's call, not this function's.
    """
    if row.get("kind") not in EVENT_KINDS:
        return None
    if event is None:
        return "The event has been deleted, so this can't be sent. Dismiss it."
    if not event.get("published"):
        return "The event is not published any more."
    starts = _parse(event.get("starts_at"))
    if starts is not None:
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        if starts <= datetime.now(timezone.utc):
            return "The event has already started."
    return None


# asyncio keeps only a weak reference to a running task; holding it here until
# it finishes is what stops a send being garbage-collected half way through.
_background: set = set()


def dispatch(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
