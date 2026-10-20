"""Transactional (non-marketing) notices for the opportunities workflow.

Only accounts with the opportunities permission receive
review alerts; we never use the entire committee roster or a broad configured
mailing list. Failure cannot undo an already-committed moderation decision.
"""

from __future__ import annotations

import asyncio
import logging

from clients import database
from config import settings
from utils import email as mailer
from utils import roles

log = logging.getLogger("api.opportunity_notifications")
_background: set[asyncio.Task] = set()


def dispatch(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def admin_recipients() -> list[str]:
    """Only authorised roles, including the standing superuser.

    Role lookup is fresh at send time so revoked access loses alerts too.
    """
    rows = await database.get("/auth/admins") or []
    recipients = {
        str(row["email"]).strip().lower()
        for row in rows
        if "opportunities" in roles.effective_capabilities(row.get("role"), row.get("permissions")) and row.get("email")
    }
    if settings.superuser_email:
        recipients.add(settings.superuser_email.strip().lower())
    return sorted(email for email in recipients if "@" in email)


async def alert_admins(title: str, subject: str, details: str, url: str) -> None:
    try:
        recipients = await admin_recipients()
        if recipients:
            await mailer.send_many(
                recipients, subject,
                f"{title}\n\n{details}\n\nReview: {url}\n",
            )
    except Exception:
        log.exception("Could not send opportunity admin notification")


async def alert_submitter(row: dict, *, resubmitted: bool = False) -> None:
    address = (row.get("submitter_email") or "").strip()
    if not address:
        return
    title = row["title"]
    url = f"{settings.site_origin.rstrip('/')}/opportunities"
    status = row["status"]
    if resubmitted:
        subject = f"Opportunity resubmitted: {title}"
        message = "Your updated opportunity is waiting for another admin review."
    elif status == "approved":
        subject = f"Opportunity approved: {title}"
        message = (
            "Your opportunity has been approved and is now listed on EESoc. "
            "You can now draft and approve the email announcement in My submissions. "
            "It will then require separate final approval by an admin before sending."
        )
    elif status == "rejected":
        subject = f"Opportunity needs changes: {title}"
        can_edit = bool(row.get("editable_after_rejection"))
        message = (
            "The admin team has rejected this submission. "
            + ("You can edit it and resubmit it for review. " if can_edit else
               "This submission cannot be edited. You may submit a new opportunity. ")
            + f"\n\nAdmin notes:\n{row.get('review_notes') or 'No notes provided.'}"
        )
    else:
        return
    try:
        await mailer.send(address, subject, f"{message}\n\nMy submissions: {url}\n")
    except Exception:
        log.exception("Could not notify opportunity submitter for %s", row.get("id"))
