"""Questions and notes between a sponsor and the committee.

A sponsor can leave a note on anything in the portal — the agreement, an event,
a request, a marketing share, a job listing — or a general one. Notes on the
same thing form a thread, and the committee replies in it from the sponsor's
admin page.

Every sponsor note emails the committee (`settings.sponsor_notify_email`), so a
question asked in the portal is not left waiting for somebody to happen to open
the admin page. A reply emails the sponsor's portal users to say there is one.
Both are throttled per thread: a sponsor typing three quick notes about the
same event sends one email, and the rest are in the admin inbox anyway.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import HTTPException

from clients import database
from config import settings
from services import agreement
from utils import email as mailer

log = logging.getLogger("api.sponsor_notes")

MAX_LENGTH = 4000
THROTTLE_SECONDS = 600

# (sponsor id, topic, direction) → when the last email for it went. In-process
# and best-effort: a restart forgets it, which costs at most one extra email.
_last_alert: dict[tuple[int, str, str], float] = {}

# asyncio keeps only a weak reference to a task; hold one until it finishes so a
# send is not garbage collected half way. Same pattern as admin._dispatch.
_background: set = set()


def _dispatch(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def resolve_topic(sponsor_id: int, topic: str, committee: bool = False) -> str:
    """Check a topic names something this sponsor may talk about, and return
    the label to file the note under.

    A sponsor may only open a thread on their own request, share or listing,
    and on an event they can see — published, or one already linked to them.
    Otherwise an id in the request would let them confirm that a competitor's
    request exists, or read a draft event's title back out of the thread.
    """
    topic = (topic or "general").strip()
    if topic == "general":
        return "General question"
    kind, _, ref = topic.partition(":")

    if kind == "clause":
        clause = agreement.BY_KEY.get(ref)
        if clause is None:
            raise HTTPException(400, "No such part of the agreement.")
        return f"Agreement: {clause['label']}"

    if not ref.isdigit():
        raise HTTPException(400, "That isn't something a note can be about.")
    item_id = int(ref)

    if kind == "event":
        event = await database.get(f"/events/{item_id}")
        if event is None:
            raise HTTPException(404, "No such event")
        if not committee and not event.get("published"):
            claims = await database.get(f"/sponsors/{sponsor_id}/claims") or []
            if not any(c["event_id"] == item_id for c in claims):
                raise HTTPException(404, "No such event")
        return f"Event: {event['title']}"

    if kind == "request":
        rows = await database.get(f"/sponsors/{sponsor_id}/requests") or []
        row = next((r for r in rows if r["id"] == item_id), None)
        if row is None:
            raise HTTPException(404, "No such request")
        return f"Request: {row.get('format') or 'event'}"

    if kind == "marketing":
        item = await database.get(f"/sponsors/marketing/{item_id}")
        if item is None or item["sponsor_id"] != sponsor_id:
            raise HTTPException(404, "No such marketing request")
        return f"Marketing: {item['subject']}"

    if kind == "listing":
        rows = await database.get(f"/sponsors/{sponsor_id}/listings") or []
        row = next((r for r in rows if r["id"] == item_id), None)
        if row is None:
            raise HTTPException(404, "No such listing")
        return f"Role: {row['title']}"

    raise HTTPException(400, "That isn't something a note can be about.")


def clean_body(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        raise HTTPException(400, "Write something first.")
    if len(text) > MAX_LENGTH:
        raise HTTPException(400, f"Keep it under {MAX_LENGTH} characters.")
    return text


def _author(n: dict, viewer: str) -> str:
    # A sponsor sees the committee as one voice; who on the committee typed it
    # is the committee's business. The committee sees exactly who wrote what.
    if n["from_committee"] and viewer == "sponsor":
        return "EESoc committee"
    name = " ".join(
        p for p in [n.get("author_first_name"), n.get("author_last_name")] if p
    )
    return name or n.get("author_email") or ("EESoc committee" if n["from_committee"] else "Sponsor")


def threads(notes: list[dict], viewer: str) -> list[dict]:
    """Group notes into threads, most recently active first.

    `unread` counts what the viewer has not seen: replies for a sponsor, the
    sponsor's notes for the committee.
    """
    by_topic: dict[str, dict] = {}
    for n in notes:
        thread = by_topic.setdefault(
            n["topic"], {"topic": n["topic"], "label": "", "notes": [], "unread": 0}
        )
        # The newest label wins, so a renamed event shows its current title.
        thread["label"] = n.get("topic_label") or thread["label"]
        theirs = n["from_committee"] if viewer == "sponsor" else not n["from_committee"]
        unread = theirs and n.get("read_at") is None
        thread["unread"] += int(unread)
        thread["notes"].append(
            {
                "id": n["id"],
                "body": n["body"],
                "fromCommittee": n["from_committee"],
                "author": _author(n, viewer),
                "at": n.get("created_at"),
                "unread": unread,
            }
        )
        thread["last"] = n.get("created_at")
    return sorted(by_topic.values(), key=lambda t: str(t.get("last") or ""), reverse=True)


def _throttled(sponsor_id: int, topic: str, direction: str) -> bool:
    key = (sponsor_id, topic, direction)
    now = time.monotonic()
    if now - _last_alert.get(key, float("-inf")) < THROTTLE_SECONDS:
        return True
    _last_alert[key] = now
    return False


def alert_committee(sponsor: dict, topic: str, label: str, body: str, author: str) -> None:
    """Email the committee about a sponsor's note, in the background."""
    recipients = [
        a.strip() for a in settings.sponsor_notify_email.split(",") if "@" in a
    ]
    if not recipients or _throttled(sponsor["id"], topic, "to-committee"):
        return
    url = f"{settings.site_origin}/admin/sponsors/{sponsor['id']}#notes"

    async def send() -> None:
        try:
            await mailer.send_sponsor_note(
                recipients, sponsor["name"], label, body, author, url
            )
        except Exception:  # noqa: BLE001 — a lost alert must not lose the note
            log.exception("Could not email the committee about a sponsor note")

    _dispatch(send())


def alert_sponsor(sponsor_id: int, topic: str, label: str) -> None:
    """Tell a sponsor's portal users the committee has replied."""
    if _throttled(sponsor_id, topic, "to-sponsor"):
        return

    async def send() -> None:
        try:
            users = await database.get(f"/sponsors/{sponsor_id}/users") or []
            recipients = [u["email"] for u in users if u.get("email")]
            if recipients:
                await mailer.send_sponsor_reply(
                    recipients, label, f"{settings.site_origin}/portal"
                )
        except Exception:  # noqa: BLE001
            log.exception("Could not email sponsor %s about a reply", sponsor_id)

    _dispatch(send())
