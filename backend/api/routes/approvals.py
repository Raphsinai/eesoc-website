"""The email approval queue: automated bulk mail, waiting for a person.

A timer may write an email to a crowd but never send one. What it writes lands
here as a campaign `awaiting_approval`, and a committee member with the `site`
capability — the same people who may send a newsletter — reads it, edits the
words and who it goes to, sends themselves a test, and then sends or dismisses
it. Until then nothing goes anywhere.

Sending from here is the ordinary bulk send (`emailing.send_bulk`): BCC'd
batches from the publicity mailbox with a working unsubscribe, recorded per
recipient and resumable across a pod replacement.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from clients import database
from config import settings
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


async def _waiting(campaign_id: int) -> dict:
    row = await database.get(f"/email/campaigns/{campaign_id}")
    if row is None:
        raise HTTPException(404, "No such email.")
    if row["status"] != "awaiting_approval":
        raise HTTPException(
            409, f"That email is no longer waiting for approval ({row['status']})."
        )
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
        "event": _event_summary(event),
        "warning": campaigns.warning(row, event),
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
    }


def _check_sendable(subject: str, recipients: list) -> None:
    """The newsletter's guards (routes/admin.py), for a draft."""
    if not subject.strip():
        raise HTTPException(400, "Give the email a subject.")
    if not recipients:
        raise HTTPException(
            400, "Nobody would receive this — tick an audience or add an address."
        )
    if len(recipients) > settings.bulk_max_recipients:
        raise HTTPException(
            400,
            f"{len(recipients)} recipients is more than the {settings.bulk_max_recipients} "
            "cap. That is almost certainly an audience picked by accident.",
        )
    if not mailer.configured("publicity"):
        raise HTTPException(
            503,
            "No mail credentials, so nothing would be sent. Check the publicity "
            "mailbox settings.",
        )


# ---------------------------------------------------------------- routes


@router.get("")
async def list_queue(_: AuthSession = Depends(require_site)) -> dict:
    """Everything waiting, oldest first — the one waiting longest is next."""
    rows = await database.get(
        "/email/campaigns", {"status": "awaiting_approval", "limit": 100}
    ) or []
    # Filtered here too: an adapter from before 073, mid-rollout, ignores the
    # status parameter and would list every campaign ever sent.
    rows = [r for r in rows if r.get("status") == "awaiting_approval"]
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
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
    _: AuthSession = Depends(require_site),
) -> dict:
    """Change the words, or who it goes to. Fields left out are kept.

    Returns the review screen afresh — recipients re-resolved, preview
    re-rendered — so what is on screen is always what Send would do.
    """
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
    """The draft, as it stands, to the test list — and to nobody else.

    "[Test]" on the subject so nobody on the list mistakes it for the real
    one. No alerts are marked, no campaign is recorded, and the draft stays
    in the queue untouched.
    """
    row = await _waiting(campaign_id)
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
    log.info("Test of draft %s sent to %d of %d", campaign_id, len(reached), len(test))
    if not reached:
        raise HTTPException(502, "The test didn't go — check the mailbox settings above.")
    return {"ok": True, "sent": len(reached), "message": f"Test sent to {', '.join(reached)}."}


@router.post("/{campaign_id}/send", dependencies=[Depends(verify_csrf)])
async def approve(campaign_id: int, session: AuthSession = Depends(require_site)) -> dict:
    """Approve and send.

    Who it goes to is worked out again now, not taken from the screen: an
    unsubscribe that landed while the draft waited is honoured. The approval
    itself is one conditional write, so a second press — or a colleague
    pressing at the same moment — gets "already dealt with", never a second
    send.
    """
    row = await _waiting(campaign_id)
    recipients, _skipped = await campaigns.recipients_for(row)
    _check_sendable(row["subject"], recipients)
    try:
        build = await campaigns.builder_for(row)
    except campaigns.Unbuildable as exc:
        raise HTTPException(409, f"This email can't be sent: {exc}. Dismiss it.") from exc

    # Raises 409 if somebody else got there first.
    decided = await database.post(
        f"/email/campaigns/{campaign_id}/decide",
        {"status": "sending", "reviewed_by": session.user.id, "recipients": len(recipients)},
    )
    if decided is None:
        raise HTTPException(404, "No such email.")
    # Seeded before the request returns rather than inside the background
    # send, so a pod lost in the next second leaves a campaign the startup
    # resume can finish, not one marked "sending" with nobody to send to.
    await database.post(
        "/campaign-recipients",
        {"campaign_id": campaign_id, "emails": [r["email"] for r in recipients]},
    )
    campaigns.dispatch(
        emailing.send_bulk(
            campaign_id, recipients, build,
            kind=row.get("kind") or "newsletter", event_id=row.get("event_id"),
        )
    )
    log.info(
        "Draft %s approved by user %s: sending to %d recipient(s)",
        campaign_id, session.user.id, len(recipients),
    )
    return {
        "ok": True,
        "recipients": len(recipients),
        "message": f"Sending to {len(recipients)} recipient(s). The history below updates as it goes.",
    }


@router.post("/{campaign_id}/dismiss", dependencies=[Depends(verify_csrf)])
async def dismiss(campaign_id: int, session: AuthSession = Depends(require_site)) -> dict:
    """Decide it should not go. Kept, as a record of what was not sent and who
    decided; it never comes back on its own — the event is already marked
    announced, so the timer will not write it again."""
    decided = await database.post(
        f"/email/campaigns/{campaign_id}/decide",
        {"status": "dismissed", "reviewed_by": session.user.id},
    )
    if decided is None:
        raise HTTPException(404, "No such email.")
    return {"ok": True, "message": "Dismissed. Nothing was sent."}
