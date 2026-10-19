"""Student opportunity submissions and committee moderation.

Public requests NEVER see pending/rejected submissions or reviewer notes;
submitters see only their own drafts via their session's user id. Uploaded
images are managed by the existing S3/local media pipeline and attached only
when the adapter verifies ownership. Publication queues, but never sends, mail.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from clients import database
from config import settings
from models.auth import AuthSession
from models.opportunities import OpportunityCreate
from services import campaigns, storage
from utils.deps import require_session, require_site, verify_csrf

log = logging.getLogger("api.opportunities")
router = APIRouter()

YEAR_AUDIENCES = {
    "1": ("dept_ee1",),
    "2": ("dept_ee2", "dept_eie2"),
    "3": ("dept_ee3", "dept_eie3"),
    "4": ("dept_ee4", "dept_eie4"),
    "msc": ("dept_msc",),
    "phd": ("dept_phd",),
}
YEAR_LABELS = {
    "1": "Year 1", "2": "Year 2", "3": "Year 3", "4": "Year 4",
    "msc": "MSc", "phd": "PhD",
}


class ReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    notes: str | None = Field(default=None, max_length=3000)


def _view(row: dict, *, private: bool = False, committee: bool = False) -> dict:
    """Explicitly select public fields. Never leak private notes or identities."""
    out = {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "links": [str(url) for url in row.get("links") or []],
        "contactEmail": row.get("contact_email"),
        "contactPhone": row.get("contact_phone"),
        "targetYears": row.get("target_years") or [],
        "images": [
            {"id": image["id"], "url": storage.public_url(image["stored_name"])}
            for image in row.get("images") or []
            if image.get("content_type", "").startswith("image/")
        ],
        "createdAt": row.get("created_at"),
        "approvedAt": row.get("approved_at"),
    }
    if private or committee:
        out.update({
            "status": row["status"],
            "reviewNotes": row.get("review_notes"),
            "reviewedAt": row.get("reviewed_at"),
            "emailCampaignId": row.get("email_campaign_id"),
        })
    if committee:
        out["submittedBy"] = row.get("submitter_email")
    return out


def _announcement(row: dict) -> tuple[str, str, list[str]]:
    """Draft email from the stored (not browser-provided) opportunity."""
    title = row["title"]
    opportunity_url = f"{settings.site_origin.rstrip('/')}/opportunities/{row['id']}"
    years = ", ".join(YEAR_LABELS[y] for y in row["target_years"])
    subject = f"Opportunity: {title}"
    body = [
        f"## {title}",
        row["description"],
        f"**For:** {years}",
        f"[View this opportunity on EESoc]({opportunity_url})",
    ]
    if row.get("links"):
        body.append("**Links**\n" + "\n".join(
            f"- [Open link]({url})" for url in row["links"]
        ))
    if row.get("contact_email"):
        body.append(f"**Contact email:** {row['contact_email']}")
    if row.get("contact_phone"):
        body.append(f"**Contact phone:** {row['contact_phone']}")
    audiences = list(dict.fromkeys(
        key for year in row["target_years"] for key in YEAR_AUDIENCES[year]
    ))
    return subject, "\n\n".join(body), audiences


@router.get("/opportunities")
async def published_opportunities() -> dict:
    rows = await database.get("/opportunities", {"status": "approved", "limit": 300}) or []
    return {"opportunities": [_view(r) for r in rows if r.get("status") == "approved"]}


@router.get("/opportunities/{opportunity_id}")
async def published_opportunity(opportunity_id: int) -> dict:
    row = await database.get(f"/opportunities/{opportunity_id}")
    if row is None or row.get("status") != "approved":
        raise HTTPException(404, "No published opportunity with that id")
    return _view(row)


@router.get("/me/opportunities")
async def my_opportunities(session: AuthSession = Depends(require_session)) -> dict:
    rows = await database.get(
        "/opportunities", {"submitted_by": session.user.id, "limit": 300}
    ) or []
    # Defence in depth: query params must never turn into another user's drafts.
    return {"opportunities": [
        _view(r, private=True) for r in rows if r.get("submitted_by") == session.user.id
    ]}


@router.post("/opportunities", status_code=201, dependencies=[Depends(verify_csrf)])
async def submit_opportunity(
    payload: OpportunityCreate,
    session: AuthSession = Depends(require_session),
) -> dict:
    if not session.user.id:
        raise HTTPException(401, "Sign in to submit an opportunity")
    # Client cannot choose submitted_by, moderation status, or review notes.
    row = await database.post(
        "/opportunities", {**payload.model_dump(mode="json"), "submitted_by": session.user.id}
    )
    return _view(row, private=True)


@router.post("/opportunities/images", status_code=201, dependencies=[Depends(verify_csrf)])
async def upload_opportunity_image(
    file: UploadFile = File(...),
    session: AuthSession = Depends(require_session),
) -> dict:
    if not session.user.id:
        raise HTTPException(401, "Sign in to upload an image")
    if (file.content_type or "").split(";")[0].lower() not in {
        "image/jpeg", "image/png", "image/webp", "image/gif",
    }:
        raise HTTPException(400, "Upload a JPEG, PNG, WebP or GIF image")
    meta = await storage.save(file, "opportunity")
    try:
        row = await database.post(
            "/uploads", {**meta, "uploaded_by": session.user.id}
        )
    except Exception:
        await storage.delete(meta["stored_name"])
        raise
    return {"id": row["id"], "url": storage.public_url(row["stored_name"])}


@router.get("/admin/opportunities")
async def moderation_queue(_: AuthSession = Depends(require_site)) -> dict:
    rows = await database.get("/opportunities", {"limit": 500}) or []
    return {"opportunities": [_view(r, committee=True) for r in rows]}


@router.post(
    "/admin/opportunities/{opportunity_id}/review",
    dependencies=[Depends(verify_csrf)],
)
async def moderate_opportunity(
    opportunity_id: int,
    request: ReviewRequest,
    session: AuthSession = Depends(require_site),
) -> dict:
    row = await database.get(f"/opportunities/{opportunity_id}")
    if row is None:
        raise HTTPException(404, "No such submission")
    if row.get("status") != "pending":
        raise HTTPException(409, "That submission has already been reviewed")

    body = {
        "decision": request.decision,
        "notes": request.notes,
        "reviewed_by": session.user.id,
    }
    if request.decision == "approved":
        subject, message, audiences = _announcement(row)
        body.update({
            "campaign_subject": subject,
            "campaign_body": message,
            "campaign_audiences": audiences,
        })
    # One adapter transaction changes status AND inserts the campaign. A
    # concurrent review is refused by its WHERE status='pending' predicate.
    result = await database.post(f"/opportunities/{opportunity_id}/review", body)
    campaign_id = result.get("email_campaign_id")
    if campaign_id:
        try:
            campaign = await database.get(f"/email/campaigns/{campaign_id}")
            recipients, _ = await campaigns.recipients_for(campaign)
            campaign = await database.patch(
                f"/email/campaigns/{campaign_id}", params={"recipients": len(recipients)}
            ) or campaign
            await campaigns._notify(campaign)
        except Exception:
            # The draft is persisted in the same transaction as publication;
            # a preview count or notification failure never re-approves it.
            log.exception("Opportunity %s approved, but email count/notification failed", opportunity_id)
    return {
        "opportunity": _view(result["opportunity"], committee=True),
        "emailCampaignId": campaign_id,
    }
