"""The email approval queue: automated bulk mail, waiting for a person.

The site may write an email to a crowd but never send one by itself. What it
writes lands here as a campaign `awaiting_approval` — the on-sale email as soon
as an event has a sale time — and a committee member with the `site`
capability (the same people who may send a newsletter) reads it, edits the
words and who it goes to, sends themselves a test, and then:

  * sends it now,
  * approves it for its time (`scheduled`) — the on-sale email goes the
    moment sales open, with nobody needing to be at a keyboard then — or
  * dismisses it.

A scheduled email can be taken back off the schedule, edited and approved
again, right up until it goes.

Sending from here is the ordinary bulk send (`emailing.send_bulk`): BCC'd
batches from the publicity mailbox with a working unsubscribe, recorded per
recipient and resumable across a pod replacement.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from clients import database
from models.auth import AuthSession
from services import campaigns, emailing
from utils import email as mailer
from utils.deps import require_admin, require_feature, require_site, verify_csrf

log = logging.getLogger("api.approvals")

router = APIRouter(
    prefix="/admin/email/queue",
    dependencies=[Depends(require_admin), require_feature("admin")],
)

# How many addresses the review screen is sent. Enough for any real audience
# short of the bulk cap; the count is always the true one.
LISTED_MAX = 5000

# The states a queued email is in until it goes or is dismissed.
QUEUED = ("awaiting_approval", "scheduled")


async def _queued(campaign_id: int, allowed: tuple[str, ...] = QUEUED) -> dict:
    row = await database.get(f"/email/campaigns/{campaign_id}")
    if row is None:
        raise HTTPException(404, "No such email.")
    if row["status"] not in allowed:
        if row["status"] == "scheduled":
            raise HTTPException(
                409, "It's approved and scheduled. Take it off the schedule to change it."
            )
        raise HTTPException(409, f"That email is no longer in the queue ({row['status']}).")
    return row


async def _event_or_none(row: dict) -> dict | None:
    if row.get("kind") not in campaigns.EVENT_KINDS or row.get("event_id") is None:
        return None
    return await database.get(f"/events/{row['event_id']}")


def _event_summary(event: dict | None) -> dict | None:
    if event is None:
        return None
    return {
        "id": event["id"],
        "title": event["title"],
        "slug": event["slug"],
        "startsAt": event.get("starts_at"),
        "published": event.get("published"),
    }


def _summary(row: dict, event: dict | None) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "kind": row.get("kind"),
        "kindLabel": campaigns.KIND_LABELS.get(row.get("kind"), row.get("kind")),
        "subject": row["subject"],
        "recipients": row.get("recipients", 0),
        "createdAt": row.get("created_at"),
        "sendAt": row.get("send_at"),
        "sendAtText": campaigns.when_text(row.get("send_at")) if row.get("send_at") else None,
        "editedAt": row.get("edited_at"),
        # Why the system took it back off the schedule, if it did.
        "note": row.get("error"),
        "event": _event_summary(event),
        "warning": campaigns.warning(row, event),
        # The WhatsApp groups it is also posted into when it is sent.
        "whatsapp": bool(row.get("whatsapp_groups")),
        "whatsappGroups": row.get("whatsapp_groups") or [],
    }


async def _event_audiences(event: dict | None) -> list[dict]:
    """The "This event" boxes a draft about an event can tick, with counts.

    The people who asked to hear about this event come first: they are the
    on-sale email's own audience, and the one not offered anywhere else.
    """
    if event is None:
        return []
    key = f"alerts:{event['id']}"
    asked = {
        "key": key,
        "label": "Asked about this event",
        "group": "This event",
        "description": "Pressed “tell me when tickets go on sale” on the event page.",
        "count": len(await emailing.resolve([key])),
    }
    return [asked] + await emailing.event_audiences(event["id"])


async def _detail(row: dict) -> dict:
    """Everything the review screen shows, worked out fresh."""
    event = await _event_or_none(row)
    recipients, skipped = await campaigns.recipients_for(row)

    preview, preview_error = None, None
    try:
        preview = await campaigns.preview(row)
    except campaigns.Unbuildable as exc:
        preview_error = f"This email can't be built: {exc}."

    return {
        **_summary(row, event),
        "heading": row.get("heading") or "",
        "body": row.get("body") or "",
        "audiences": row.get("audiences") or [],
        "extra": row.get("extra_recipients") or [],
        "excluded": row.get("excluded_recipients") or [],
        "eventAudiences": await _event_audiences(event),
        "count": len(recipients),
        "recipientList": [
            {"email": r["email"], "source": r.get("source") or "", "list": r.get("list", False)}
            for r in recipients[:LISTED_MAX]
        ],
        "skipped": skipped,
        "preview": preview and {"subject": preview["subject"], "html": preview["html"]},
        "previewError": preview_error,
        # What went to WhatsApp with it, once it has been sent.
        "whatsappPosts": [
            {"group": p.get("group_name") or p["jid"], "status": p["status"], "error": p.get("error")}
            for p in await database.get("/whatsapp/posts", {"campaign_id": row["id"]}) or []
        ],
    }


async def _decide(row: dict, status: str, reviewed_by: int | None, **extra) -> dict:
    decided = await database.post(
        f"/email/campaigns/{row['id']}/decide",
        {"status": status, "expect": row["status"], "reviewed_by": reviewed_by, **extra},
    )
    if decided is None:
        raise HTTPException(404, "No such email.")
    campaigns.wake()
    return decided


# ---------------------------------------------------------------- routes


@router.get("")
async def list_queue(_: AuthSession = Depends(require_site)) -> dict:
    """Everything waiting or scheduled, the soonest to go first."""
    rows = await database.get(
        "/email/campaigns", {"status": ",".join(QUEUED), "limit": 100}
    ) or []
    # Filtered here too: an adapter from before 073, mid-rollout, ignores the
    # status parameter and would list every campaign ever sent.
    rows = [r for r in rows if r.get("status") in QUEUED]
    rows.sort(key=lambda r: (str(r.get("send_at") or "9999"), str(r.get("created_at") or "")))
    return {"items": [_summary(r, await _event_or_none(r)) for r in rows]}


@router.get("/{campaign_id}")
async def review(campaign_id: int, _: AuthSession = Depends(require_site)) -> dict:
    row = await database.get(f"/email/campaigns/{campaign_id}")
    if row is None:
        raise HTTPException(404, "No such email.")
    return await _detail(row)


@router.put("/{campaign_id}", dependencies=[Depends(verify_csrf)])
async def edit(
    campaign_id: int,
    subject: str | None = Body(default=None, embed=True),
    heading: str | None = Body(default=None, embed=True),
    body: str | None = Body(default=None, embed=True),
    audiences: list[str] | None = Body(default=None, embed=True),
    extra: list[str] | None = Body(default=None, embed=True),
    excluded: list[str] | None = Body(default=None, embed=True),
    whatsapp_groups: list[str] | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_site),
) -> dict:
    """Change the words, or who it goes to — `whatsapp_groups` are the
    WhatsApp groups it is also posted into when it is sent, picked from the
    ones enabled on the Email tab. Fields left out are kept.

    Only while it waits for approval — a scheduled email is taken off the
    schedule first, so nothing approved changes without being approved again.
    Returns the review screen afresh — recipients re-resolved, preview
    re-rendered — so what is on screen is always what Send would do.
    """
    await _queued(campaign_id, ("awaiting_approval",))
    if subject is not None and not subject.strip():
        raise HTTPException(400, "Give the email a subject.")
    edited = await database.put(
        f"/email/campaigns/{campaign_id}/draft",
        {
            "subject": subject.strip() if subject is not None else None,
            "heading": heading.strip() if heading is not None else None,
            "body": body,
            "audiences": (
                list(dict.fromkeys(a for a in audiences if a)) if audiences is not None else None
            ),
            "extra_recipients": (
                campaigns.clean_addresses(extra) if extra is not None else None
            ),
            "excluded_recipients": (
                campaigns.clean_addresses(excluded) if excluded is not None else None
            ),
            "whatsapp_groups": (
                list(dict.fromkeys(g for g in whatsapp_groups if g))
                if whatsapp_groups is not None else None
            ),
        },
    )
    if edited is None:
        raise HTTPException(404, "No such email.")
    detail = await _detail(edited)
    # Kept on the row so the queue list shows a current number without
    # resolving every draft's audience on every load.
    await database.patch(
        f"/email/campaigns/{campaign_id}", params={"recipients": detail["count"]}
    )
    detail["recipients"] = detail["count"]
    return detail


@router.post("/{campaign_id}/test", dependencies=[Depends(verify_csrf)])
async def send_test(campaign_id: int, _: AuthSession = Depends(require_site)) -> dict:
    """The email, as it stands, to the test list — and to nobody else.

    "[Test]" on the subject so nobody on the list mistakes it for the real
    one. No alerts are marked, no campaign is recorded, and the email stays
    where it was in the queue.
    """
    row = await _queued(campaign_id)
    test = await emailing.test_recipients()
    if not test:
        raise HTTPException(400, "The test list is empty. Add an address to it on this tab.")
    if not mailer.configured("publicity"):
        raise HTTPException(503, "No mail credentials, so nothing would be sent.")
    try:
        build = await campaigns.builder_for(
            {**row, "subject": f"[Test] {row['subject']}"}, mark_alerts=False
        )
    except campaigns.Unbuildable as exc:
        raise HTTPException(409, f"This email can't be built: {exc}.") from exc
    reached = await build([{"email": e} for e in test], emailing.general_unsubscribe_url())
    log.info("Test of queued email %s sent to %d of %d", campaign_id, len(reached), len(test))
    if not reached:
        raise HTTPException(502, "The test didn't go — check the mailbox settings above.")
    return {"ok": True, "sent": len(reached), "message": f"Test sent to {', '.join(reached)}."}


@router.post("/{campaign_id}/send", dependencies=[Depends(verify_csrf)])
async def send_now(campaign_id: int, session: AuthSession = Depends(require_site)) -> dict:
    """Send it now — whether it was waiting, or approved for later.

    A second press, or the scheduler reaching it at the same moment, gets
    "already dealt with", never a second send.
    """
    row = await _queued(campaign_id)
    try:
        count = await campaigns.start_send(
            row, expect=row["status"], reviewed_by=session.user.id
        )
    except campaigns.NotSendable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    campaigns.wake()
    log.info(
        "Queued email %s sent now by user %s: %d recipient(s)",
        campaign_id, session.user.id, count,
    )
    return {
        "ok": True,
        "recipients": count,
        "message": f"Sending to {count} recipient(s). The history below updates as it goes.",
    }


@router.post("/{campaign_id}/schedule", dependencies=[Depends(verify_csrf)])
async def schedule(campaign_id: int, session: AuthSession = Depends(require_site)) -> dict:
    """Approve it for its time. It then goes by itself, at that moment.

    Everything Send would refuse is refused now, so a problem surfaces while
    somebody is looking rather than at the send time — and checked again then,
    because who is subscribed changes in between.
    """
    row = await _queued(campaign_id, ("awaiting_approval",))
    at = campaigns.parse_time(row.get("send_at"))
    if at is None:
        raise HTTPException(400, "This email has no send time of its own. Send it now instead.")
    if at <= campaigns.utcnow():
        raise HTTPException(400, "Its time has already passed. Send it now instead.")

    recipients, _skipped = await campaigns.recipients_for(row)
    try:
        campaigns.check_sendable(row["subject"], recipients)
        await campaigns.builder_for(row, mark_alerts=False)
    except campaigns.NotSendable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except campaigns.Unbuildable as exc:
        raise HTTPException(409, f"This email can't be sent: {exc}.") from exc

    await _decide(row, "scheduled", session.user.id, recipients=len(recipients), note=None)
    when = campaigns.when_text(at)
    log.info("Queued email %s scheduled for %s by user %s", campaign_id, at, session.user.id)
    return {
        "ok": True,
        "message": (
            f"Approved. It goes out {when}, to whoever is on the list then — "
            f"{len(recipients)} recipient(s) as of now."
        ),
    }


@router.post("/{campaign_id}/unschedule", dependencies=[Depends(verify_csrf)])
async def unschedule(campaign_id: int, session: AuthSession = Depends(require_site)) -> dict:
    """Take an approval back. It waits in the queue again, to edit or dismiss."""
    row = await _queued(campaign_id, ("scheduled",))
    await _decide(row, "awaiting_approval", session.user.id, note=None)
    return {"ok": True, "message": "Taken off the schedule. Nothing was sent."}


@router.post("/{campaign_id}/dismiss", dependencies=[Depends(verify_csrf)])
async def dismiss(campaign_id: int, session: AuthSession = Depends(require_site)) -> dict:
    """Decide it should not go. Kept, as a record of what was not sent and who
    decided; an on-sale email never comes back by itself — its event is marked
    announced."""
    row = await _queued(campaign_id)
    await _decide(row, "dismissed", session.user.id)
    return {"ok": True, "message": "Dismissed. Nothing was sent."}
