"""A stored campaign row, turned back into mail — and the queue of rows that
wait for a person to say yes, or for the moment they were approved for.

All of it works from the row alone:

  * rebuilding the message: the preview a committee member reads, the `build`
    closure `emailing.send_bulk` runs, and that same closure again when a pod
    replaced mid-send resumes it. One builder for all three, so what is
    approved is what is sent, and what is resumed is what was approved;
  * the approval queue: an automated bulk email is written as a campaign
    `awaiting_approval` (migration 073). A committee member sends it now, or
    approves it for its time (`scheduled`, 074) — nothing else moves it on;
  * the on-sale email's draft, kept in step with its event: written the moment
    an event has a sale time, rewritten when the event changes (unless a
    person has edited it), and timed to go when sales open;
  * the scheduler, which sends what was approved for later at its time.

The rule: nothing addressed to a crowd goes without a person's approval.
Mail about one person's own booking or sign-in — the hold, the confirmation,
the waitlist offer, the last-chance reminder, the sign-in code — is the answer
to something they did, is not here, and still goes at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from clients import database
from config import settings
from services import emailing, purchase
from utils import clock
from utils import email as mailer
from utils.auth import normalise_email
from utils.present import (
    format_date,
    format_doors_close,
    format_price,
    format_time_range,
    london,
    ticket_sale_state,
)

log = logging.getLogger("api.campaigns")

# What each kind is called on the admin screen.
KIND_LABELS = {
    "newsletter": "Newsletter",
    "opportunity_announcement": "Opportunity announcement",
    "event_announcement": "Email about an event",
    "on_sale": "Tickets on sale",
}

# The kinds whose message is built around one event's details.
EVENT_KINDS = ("event_announcement", "on_sale")


class Unbuildable(Exception):
    """The row cannot be turned back into a message. The text says why."""


class NotSendable(Exception):
    """A send was refused before anything went. `status` is the HTTP answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_time(value) -> datetime | None:
    """A stored time, timezone-aware. The adapter's JSON carries offsets; a
    naive value is UTC, which is what the database stores."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def when_text(value) -> str:
    """A send time as the committee reads it: London, to the minute."""
    at = parse_time(value)
    if at is None:
        return "as soon as it is approved"
    return f"{format_date(at)} at {london(at).strftime('%H:%M')}"


def event_when(event: dict) -> str:
    starts, ends = parse_time(event.get("starts_at")), parse_time(event.get("ends_at"))
    if starts is None:
        return "TBC"
    return f"{format_date(starts)}, {format_time_range(starts, ends)}"


def event_tickets(event: dict) -> list[tuple[str, str]]:
    """(name, price) for each ticket somebody could still get, as of sending.

    A ticket type with its own sale window (080) that has ended — early bird —
    is left out, and one that is not on sale yet says when it will be, so an
    on-sale email sent at early bird's start does not read as if general
    admission were on sale too.
    """
    tickets = []
    for t in event.get("ticket_types", []):
        state = ticket_sale_state(t)
        if t.get("sold_out") or state == "ended":
            continue
        price = format_price(t.get("price_pence", 0))
        if state == "scheduled":
            price = f"{price} · on sale {when_text(t.get('sales_open_at'))}"
        tickets.append((t["name"], price))
    return tickets


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

    if kind == "opportunity_announcement" and row.get("opportunity_id"):
        # A human's earlier email approval must not advertise a listing that
        # has since been taken down or has expired.
        opportunity = await database.get(f"/opportunities/{row['opportunity_id']}")
        until = opportunity.get("posted_until") if opportunity else None
        if not opportunity or opportunity.get("status") != "approved" or (
            row.get("id") is not None and opportunity.get("email_campaign_id") != row["id"]
        ) or (
            until and date.fromisoformat(str(until)) < datetime.now(ZoneInfo("Europe/London")).date()
        ):
            raise Unbuildable("the linked opportunity is no longer published")

    if kind in ("newsletter", "opportunity_announcement"):
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

    audiences = list(row.get("audiences") or [])
    # External committee sends retain their tenant ID as an immutable provenance
    # marker. Resume is allowed to rebuild only that tenant's addresses, never
    # arbitrary lists that may have been inserted into a queued campaign.
    markers = [a for a in audiences if a.startswith('society-sent:')]
    scope = None
    if markers:
        if len(markers) != 1:
            raise NotSendable('Invalid society campaign scope', 403)
        try:
            scope = int(markers[0].split(':', 1)[1])
        except ValueError as exc:
            raise NotSendable('Invalid society campaign scope', 403) from exc
        if scope < 1 or any(a.startswith('society:') and a != f'society:{scope}' for a in audiences):
            raise NotSendable('A campaign cannot use another society mailing list', 403)
        if any(not (a in ('test', markers[0], f'society:{scope}') or a.startswith('whatsapp:')) for a in audiences):
            raise NotSendable('Outside society audience in campaign', 403)
    recipients = await emailing.resolve(audiences, allowed_society_ids={scope} if scope is not None else set())
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
    send_at: str | None = None,
) -> dict:
    """Write an automated bulk email as a draft waiting for approval.

    The only way the site itself starts mail to a crowd. `send_at` is when it
    is meant to go — a committee member can approve it for then, or send it
    sooner. The recipient count is worked out now so the queue can show it,
    and again at the send — it is a preview, not a promise.
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
            "send_at": send_at,
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
    starts = parse_time(event.get("starts_at"))
    if starts is not None and starts <= utcnow():
        return "The event has already started."
    if row.get("kind") == "on_sale" and not event.get("sales_open_at"):
        return "The event has no sale time any more."
    send_at = parse_time(row.get("send_at"))
    if row.get("status") == "awaiting_approval" and send_at and send_at <= utcnow():
        return (
            f"Sales opened {when_text(send_at)} and this hasn't gone yet. "
            "Send it now, or dismiss it."
        )
    return None


# ------------------------------------------------------------- sending


def check_sendable(subject: str, recipients: list, whatsapp: bool = False) -> None:
    """The newsletter's guards (routes/admin.py), for a queued email.

    `whatsapp`: it also goes into WhatsApp groups — enough on its own, with
    nobody to email.
    """
    if not (subject or "").strip():
        raise NotSendable("Give the email a subject.")
    if not recipients:
        if whatsapp:
            return
        raise NotSendable("Nobody would receive this — tick an audience or add an address.")
    if len(recipients) > settings.bulk_max_recipients:
        raise NotSendable(
            f"{len(recipients)} recipients is more than the {settings.bulk_max_recipients} "
            "cap. That is almost certainly an audience picked by accident."
        )
    if not mailer.configured("publicity"):
        raise NotSendable(
            "No mail credentials, so nothing would be sent. Check the publicity "
            "mailbox settings.",
            503,
        )


async def start_send(row: dict, *, expect: str, reviewed_by: int | None) -> int:
    """Send a queued email now: from the queue's Send, or the scheduler at its
    time. Returns how many it is going to.

    Who it goes to is worked out again here, not taken from the screen or the
    moment of approval: an unsubscribe that landed while it waited is
    honoured. Moving the row to `sending` is one conditional write, so a
    double press — or two pods' schedulers reaching the same email — starts
    one send; the loser gets the adapter's 409.
    """
    # Here rather than at the top: services/whatsapp builds on this module.
    from services import whatsapp

    groups = whatsapp.groups_of(row)
    recipients, _skipped = await recipients_for(row)
    check_sendable(row.get("subject") or "", recipients, whatsapp=bool(groups))
    try:
        build = await builder_for(row) if recipients else None
    except Unbuildable as exc:
        raise NotSendable(f"This email can't be sent: {exc}. Dismiss it.", 409) from exc

    decided = await database.post(
        f"/email/campaigns/{row['id']}/decide",
        {"status": "sending", "expect": expect, "reviewed_by": reviewed_by,
         "recipients": len(recipients)},
    )
    if decided is None:
        raise NotSendable("No such email.", 404)
    if recipients:
        # Seeded before returning rather than inside the background send, so a
        # pod lost in the next second leaves a campaign the startup resume can
        # finish, not one marked "sending" with nobody to send to.
        await database.post(
            "/campaign-recipients",
            {"campaign_id": row["id"], "emails": [r["email"] for r in recipients]},
        )
        dispatch(
            emailing.send_bulk(
                row["id"], recipients, build,
                kind=row.get("kind") or "newsletter", event_id=row.get("event_id"),
            )
        )
    else:
        # WhatsApp only: nobody to mail, so the record is finished now rather
        # than left "sending" for a resume to wonder about.
        await database.patch(f"/email/campaigns/{row['id']}", params={"status": "sent"})
    if groups:
        # Alongside the email, not after it: a paced send of a few hundred
        # takes minutes, and "tickets are on sale" is news at the moment of
        # sale. Each group is claimed before it is posted, so a second pod's
        # scheduler reaching this email never posts it twice.
        dispatch(whatsapp.post_campaign({**row, "reviewed_by": reviewed_by}))
    return len(recipients)


async def hold(row: dict, note: str) -> None:
    """Take a scheduled email back off the schedule, saying why.

    It goes back to waiting for approval rather than being dismissed: the
    words and recipients may be exactly right, and only the moment wrong. A
    person decides again; nothing is sent in the meantime.
    """
    try:
        await database.post(
            f"/email/campaigns/{row['id']}/decide",
            {"status": "awaiting_approval", "expect": "scheduled", "note": note},
        )
        log.warning("Held scheduled email %s: %s", row["id"], note)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # Already moved on — sent, dismissed, or held by another pod.


# ------------------------------------------------- the on-sale email's draft
#
# Written when an event has a sale time, not when the sale opens: a draft that
# appears only at the moment it is needed is a draft that goes out late. The
# event is the source; the draft follows it until a person edits the draft.


def wants_on_sale(event: dict | None) -> bool:
    """Whether an event should have an on-sale email waiting: published, with
    a sale time, and not already under way."""
    if not event or not event.get("published") or not event.get("sales_open_at"):
        return False
    starts = parse_time(event.get("starts_at"))
    return starts is None or starts > utcnow()


def on_sale_content(event: dict) -> dict:
    """The words and audiences an untouched on-sale draft has.

    The date, venue, prices and booking link are not here: they are rendered
    from the event at send time, so they are current whenever it goes.
    """
    subject = f"Tickets for {event['title']} are on sale"
    audiences = [f"alerts:{event['id']}", "topic:tickets_on_sale"]
    if event.get("category"):
        audiences.append(f"topic:{emailing.category_topic(event['category'])}")
    return {
        "subject": subject,
        "heading": subject,
        "body": f"Tickets for **{event['title']}** are on sale now.",
        "audiences": audiences,
    }


async def _live_on_sale(event_id: int) -> dict | None:
    rows = await database.get(
        "/email/campaigns",
        {"status": "awaiting_approval,scheduled", "kind": "on_sale",
         "event_id": event_id, "limit": 5},
    ) or []
    live = [r for r in rows if r.get("status") in ("awaiting_approval", "scheduled")]
    return live[0] if live else None


async def sync_on_sale(event_id: int) -> dict | None:
    """Bring one event's on-sale email into line with the event. Idempotent.

    Called after every save of an event, and for every event at startup.

      * No email yet, and the event wants one → write the draft, timed for
        the sale. Once per event: an event whose email was sent or dismissed
        (or announced before this existed) never gets another by itself.
      * A draft nobody has edited → rewritten to match (title, category).
      * The sale time moved → the send time moves with it, approved or not.
      * The approved words would change, or the event stopped wanting an
        email (unpublished, sale time cleared, deleted) → taken back off the
        schedule for a person to look at again. Never sent on a stale yes.
    """
    event = await database.get(f"/events/{event_id}")
    live = await _live_on_sale(event_id)
    if live is not None:
        return await _sync_live(live, event)

    if not wants_on_sale(event) or event.get("sales_announced_at"):
        return None
    if await database.get(
        "/email/campaigns", {"kind": "on_sale", "event_id": event_id, "limit": 1}
    ):
        return None

    try:
        campaign = await queue(
            **on_sale_content(event), kind="on_sale", event_id=event_id,
            send_at=event["sales_open_at"],
        )
    except HTTPException as exc:
        # The one-live-draft index refusing a twin: another save of the same
        # event got there first, and its draft is the one.
        log.info("On-sale draft for event %s not written: %s", event_id, exc.detail)
        return None
    # Marked so nothing else announces this event — including the previous
    # image's on-sale timer, were the deploy rolled back.
    await database.post(f"/events/{event_id}/announced")
    wake()
    return campaign


async def _sync_live(live: dict, event: dict | None) -> dict:
    if not wants_on_sale(event):
        if live["status"] == "scheduled":
            await hold(live, (
                "Taken off the schedule: the event is unpublished, deleted, under "
                "way, or has no sale time any more."
            ))
            wake()
        return live

    changes: dict = {}
    sale = parse_time(event["sales_open_at"])
    if parse_time(live.get("send_at")) != sale:
        changes["send_at"] = sale.isoformat()

    words_changed = False
    if not live.get("edited_at"):
        for key, value in on_sale_content(event).items():
            if live.get(key) != value:
                changes[key] = value
                words_changed = True

    if not changes:
        return live
    if words_changed and live["status"] == "scheduled":
        # What was approved is about to change; the approval does not carry
        # over to words nobody has read.
        await hold(live, "The event changed after this was approved, so it needs approving again.")

    updated = await database.put(
        f"/email/campaigns/{live['id']}/draft", {**changes, "by_hand": False}
    ) or live
    if "audiences" in changes:
        recipients, _ = await recipients_for(updated)
        updated = await database.patch(
            f"/email/campaigns/{live['id']}", params={"recipients": len(recipients)}
        ) or updated
    wake()
    return updated


async def reconcile_on_sale() -> int:
    """Run `sync_on_sale` for every event that might need it.

    At startup, so events saved before this code existed — or while it was
    down — get their drafts, and drafts from before 074 get their send times.
    """
    events = await database.get("/events", {"published_only": False, "limit": 1000}) or []
    live = await database.get(
        "/email/campaigns",
        {"status": "awaiting_approval,scheduled", "kind": "on_sale", "limit": 500},
    ) or []
    ids = {e["id"] for e in events if wants_on_sale(e) and not e.get("sales_announced_at")}
    ids |= {d["event_id"] for d in live if d.get("event_id") is not None}
    for event_id in sorted(ids):
        try:
            await sync_on_sale(event_id)
        except Exception:  # noqa: BLE001 — one bad event must not stop the rest
            log.exception("Could not sync the on-sale email for event %s", event_id)
    return len(ids)


# ---------------------------------------------------------------- scheduler
#
# Sleeps until the next approved email is due, and sends it then — at that
# moment, not on a polling interval. Woken early whenever a schedule changes in
# this process, so a sale moved forward is not missed.
#
# It also looks once an hour, on the hour, whatever it is waiting for. That is
# a safety net for changes this process did not see — another pod's, mid-
# rollout — not the way anything is found on time; recurring jobs here run
# hourly on the hour and never more often (utils/clock.py).

_wake = asyncio.Event()


def wake() -> None:
    """Something about the schedule changed: look again now."""
    _wake.set()


async def fire_due() -> datetime | None:
    """Send everything that is due. Returns when the next one is."""
    rows = await database.get("/email/campaigns", {"status": "scheduled", "limit": 200}) or []
    upcoming: datetime | None = None
    for row in rows:
        if row.get("status") != "scheduled":
            continue
        at = parse_time(row.get("send_at"))
        if at is not None and at > utcnow():
            upcoming = at if upcoming is None or at < upcoming else upcoming
            continue
        await _fire(row)
    return upcoming


async def _fire(row: dict) -> None:
    if row.get("kind") in EVENT_KINDS:
        event = await database.get(f"/events/{row['event_id']}")
        if event is None or not event.get("published"):
            await hold(row, (
                "Held at its send time: the event was deleted or unpublished, so "
                "this did not go out."
            ))
            return
    try:
        count = await start_send(row, expect="scheduled", reviewed_by=row.get("reviewed_by"))
    except NotSendable as exc:
        if exc.status == 503:
            # No mailbox — the annual password reset. Left scheduled, so it
            # goes as soon as mail works rather than being lost.
            log.warning("Scheduled email %s is due but mail is down: %s", row["id"], exc)
            return
        await hold(row, f"Held at its send time: {exc}")
        return
    except HTTPException as exc:
        if exc.status_code == 409:
            return  # another pod's scheduler is already sending it
        raise
    log.info("Scheduled email %s is going to %d recipient(s)", row["id"], count)


async def scheduler() -> None:
    """Background loop. Cancelled on shutdown by the lifespan handler."""
    reconciled = False
    failures = 0
    while True:
        _wake.clear()
        delay = clock.seconds_until_next_hour()
        try:
            if not reconciled:
                count = await reconcile_on_sale()
                reconciled = True
                log.info("On-sale drafts checked for %d event(s)", count)
            upcoming = await fire_due()
            failures = 0
            if upcoming is not None:
                delay = min(delay, max(0.5, (upcoming - utcnow()).total_seconds()))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — retried below
            # Usually the adapter not being up yet at startup, or a blip. An
            # email due now must not wait an hour for that, so the retry backs
            # off from 30 seconds — but never past the hour it would look anyway.
            failures += 1
            log.exception("Scheduled-email pass failed (attempt %d)", failures)
            delay = min(delay, 30 * 2 ** min(failures - 1, 6))
        try:
            await asyncio.wait_for(_wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


# asyncio keeps only a weak reference to a running task; holding it here until
# it finishes is what stops a send being garbage-collected half way through.
_background: set = set()


def dispatch(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
