"""Persistence for opportunity submissions and atomic committee decisions.

The adapter has no external access. All auth and moderation permissions are
checked by the public API, never trusted to query parameters from the browser.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from models.opportunities import Opportunity, OpportunityCreate, OpportunityReview
from postgres import get_pool

router = APIRouter(prefix="/opportunities")

# The image metadata is looked up from uploads, never taken from arbitrary
# client-supplied URLs. Join against the stored ids to preserve their order.
COLUMNS = """
    o.id, o.submitted_by, o.title, o.description, o.links,
    o.contact_email, o.contact_phone, o.target_years, o.image_ids,
    o.status, o.review_notes, o.reviewed_by, o.reviewed_at,
    o.approved_at, o.email_campaign_id, o.created_at,
    submitter.email AS submitter_email,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'id', asset.id, 'stored_name', asset.stored_name,
                'content_type', asset.content_type)
                ORDER BY array_position(o.image_ids, asset.id))
              FROM public.uploads asset WHERE asset.id = ANY(o.image_ids)),
             '[]'::jsonb) AS images
"""
FROM = """public.opportunities o
    LEFT JOIN public.users submitter ON submitter.id = o.submitted_by"""


def _model(row) -> Opportunity:
    data = dict(row)
    for key in ("links", "target_years", "images"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    return Opportunity(**data)


@router.get("")
async def list_opportunities(
    status: str | None = None, submitted_by: int | None = None, limit: int = 200,
) -> list[Opportunity]:
    if status is not None and status not in ("pending", "approved", "rejected"):
        raise HTTPException(400, "Unknown opportunity status")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM {FROM}"
            " WHERE ($1::varchar IS NULL OR o.status = $1)"
            " AND ($2::bigint IS NULL OR o.submitted_by = $2)"
            " ORDER BY CASE WHEN o.status = 'pending' THEN 0 ELSE 1 END,"
            " o.created_at DESC, o.id DESC LIMIT $3;",
            status, submitted_by, max(1, min(limit, 500)),
        )
    return [_model(row) for row in rows]


@router.get("/{opportunity_id}")
async def get_opportunity(opportunity_id: int) -> Opportunity:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {COLUMNS} FROM {FROM} WHERE o.id = $1;", opportunity_id,
        )
    if row is None:
        raise HTTPException(404, "No such opportunity")
    return _model(row)


@router.post("", status_code=201)
async def create_opportunity(item: OpportunityCreate) -> Opportunity:
    if item.submitted_by is None:
        raise HTTPException(400, "A submitter account is required")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # A member must not attach somebody else's unapproved upload or refer
        # to a file outside the image store; check within the write transaction.
        if item.image_ids:
            count = await conn.fetchval(
                """SELECT count(*) FROM public.uploads
                     WHERE id = ANY($1::bigint[]) AND uploaded_by = $2
                       AND purpose = 'opportunity' AND content_type LIKE 'image/%';""",
                item.image_ids, item.submitted_by,
            )
            if count != len(item.image_ids):
                raise HTTPException(400, "One or more images do not belong to your account")
        row = await conn.fetchrow(
            """INSERT INTO public.opportunities
                  (submitted_by, title, description, links, contact_email,
                   contact_phone, target_years, image_ids)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::jsonb, $8)
               RETURNING id;""",
            item.submitted_by, item.title, item.description,
            json.dumps([str(link) for link in item.links]),
            str(item.contact_email) if item.contact_email else None,
            item.contact_phone, json.dumps(item.target_years), item.image_ids,
        )
    return await get_opportunity(row["id"])


@router.post("/{opportunity_id}/review")
async def review_opportunity(opportunity_id: int, review: OpportunityReview) -> dict:
    if review.reviewed_by is None:
        raise HTTPException(400, "A reviewer account is required")
    if review.decision == "approved" and not (
        review.campaign_subject and review.campaign_body and review.campaign_audiences
    ):
        raise HTTPException(400, "An approved opportunity needs an email draft")

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """UPDATE public.opportunities
                  SET status = $2, reviewed_by = $3, review_notes = $4,
                      reviewed_at = NOW(),
                      approved_at = CASE WHEN $2 = 'approved' THEN NOW() ELSE NULL END
                WHERE id = $1 AND status = 'pending'
                RETURNING id;""",
            opportunity_id, review.decision, (review.reviewed_by),
            review.notes.strip() if review.notes and review.notes.strip() else None,
        )
        if row is None:
            existing = await conn.fetchval(
                "SELECT status FROM public.opportunities WHERE id = $1;", opportunity_id
            )
            if existing is None:
                raise HTTPException(404, "No such opportunity")
            raise HTTPException(409, f"Already reviewed ({existing}); refresh the queue")

        campaign_id = None
        if review.decision == "approved":
            # Approval and insertion are a single commit: no orphaned
            # approved listing, and no double draft on competing reviews.
            campaign_id = await conn.fetchval(
                """INSERT INTO public.email_campaigns
                       (subject, heading, body, audiences, status, kind,
                        opportunity_id, created_by)
                   VALUES ($1, $2, $3, $4::jsonb, 'awaiting_approval',
                           'opportunity_announcement', $5, $6)
                   RETURNING id;""",
                review.campaign_subject, review.campaign_subject,
                review.campaign_body, json.dumps(review.campaign_audiences),
                opportunity_id, review.reviewed_by,
            )
            await conn.execute(
                "UPDATE public.opportunities SET email_campaign_id = $2 WHERE id = $1;",
                opportunity_id, campaign_id,
            )
    return {"opportunity": (await get_opportunity(opportunity_id)).model_dump(mode="json"),
            "email_campaign_id": campaign_id}
