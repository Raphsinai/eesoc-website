"""Persistence for opportunity submissions and atomic committee decisions.

The adapter has no external access. All auth and moderation permissions are
checked by the public API, never trusted to query parameters from the browser.
"""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, HTTPException

from models.opportunities import (
    Opportunity, OpportunityCreate, OpportunityReview,
    OpportunityEdit, OpportunityWithdraw, AnnouncementDraft, AnnouncementSubmit,
)
from postgres import get_pool

router = APIRouter(prefix="/opportunities")

# The image metadata is looked up from uploads, never taken from arbitrary
# client-supplied URLs. Join against the stored ids to preserve their order.
COLUMNS = """
    o.id, o.submitted_by, o.title, o.description, o.links,
    o.contact_email, o.contact_phone, o.target_years, o.image_ids,
    o.status, o.review_notes, o.reviewed_by, o.reviewed_at,
    o.approved_at, o.withdrawn_at, o.email_campaign_id, o.created_at,
    o.editable_after_rejection, o.revision, o.updated_at,
    o.announcement_subject, o.announcement_body,
    o.posted_until, o.pending_revision, o.pending_revision_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'published_at', period.published_at,
                'unpublished_at', period.unpublished_at,
                'end_reason', period.end_reason,
                'posted_until', period.posted_until)
                ORDER BY period.published_at DESC, period.id DESC)
              FROM public.opportunity_publication_periods period
              WHERE period.opportunity_id = o.id), '[]'::jsonb) AS publication_history,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
                'id', asset.id, 'stored_name', asset.stored_name,
                'content_type', asset.content_type)
                ORDER BY array_position(ARRAY(
                   SELECT requested::bigint FROM jsonb_array_elements_text(
                     COALESCE(o.pending_revision->'image_ids', '[]'::jsonb)
                   ) AS requested
                ), asset.id))
              FROM public.uploads asset
              WHERE asset.id IN (SELECT requested::bigint FROM jsonb_array_elements_text(
                  COALESCE(o.pending_revision->'image_ids', '[]'::jsonb)
              ) AS requested)), '[]'::jsonb) AS pending_images,
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
    for key in ("links", "target_years", "images", "pending_images", "pending_revision", "publication_history"):
        if isinstance(data.get(key), str):
            data[key] = json.loads(data[key])
    return Opportunity(**data)


@router.get("")
async def list_opportunities(
    status: str | None = None, submitted_by: int | None = None, limit: int = 200,
) -> list[Opportunity]:
    if status is not None and status not in ("draft", "pending", "approved", "rejected", "withdrawn"):
        raise HTTPException(400, "Unknown opportunity status")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM {FROM}"
            " WHERE ($1::varchar IS NULL OR o.status = $1)"
            " AND ($2::bigint IS NULL OR o.submitted_by = $2)"
            " AND ($1::varchar IS DISTINCT FROM 'approved' OR o.posted_until IS NULL "
            " OR o.posted_until >= (NOW() AT TIME ZONE 'Europe/London')::date)"
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
                   contact_phone, target_years, image_ids, posted_until)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::jsonb, $8, $9)
               RETURNING id;""",
            item.submitted_by, item.title, item.description,
            json.dumps([str(link) for link in item.links]),
            str(item.contact_email) if item.contact_email else None,
            item.contact_phone, json.dumps(item.target_years), item.image_ids, item.posted_until,
        )
    return await get_opportunity(row["id"])


def _review_conflict() -> HTTPException:
    return HTTPException(409, "This opportunity has changed. Refresh and try again.")


@router.put("/{opportunity_id}")
async def resubmit_opportunity(opportunity_id: int, item: OpportunityEdit) -> Opportunity:
    """Owner edits of approved listings are isolated until approved.

    Rejected editable submissions can be resubmitted for initial approval.
    """
    if item.owner_id is None:
        raise HTTPException(400, "An owner is required")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if item.image_ids:
            count = await conn.fetchval(
                """SELECT count(*) FROM public.uploads
                     WHERE id = ANY($1::bigint[]) AND uploaded_by = $2
                       AND purpose = 'opportunity' AND content_type LIKE 'image/%';""",
                item.image_ids, item.owner_id,
            )
            if count != len(item.image_ids):
                raise HTTPException(400, "One or more images do not belong to your account")
        current = await conn.fetchrow("SELECT status, pending_revision, editable_after_rejection FROM public.opportunities WHERE id=$1 AND submitted_by=$2 FOR UPDATE", opportunity_id, item.owner_id)
        if not current or current['pending_revision'] is not None or current['status'] == 'pending' or (current['status'] == 'rejected' and not current['editable_after_rejection']):
            raise _review_conflict()
        if current['status'] == 'draft':
            # Saving a private draft never queues it for committee review.
            await conn.execute("""UPDATE public.opportunities
                  SET title=$3, description=$4, links=$5::jsonb,
                      contact_email=$6, contact_phone=$7,
                      target_years=$8::jsonb, image_ids=$9, posted_until=$10,
                      updated_at=NOW()
                WHERE id=$1 AND submitted_by=$2 AND status='draft'""",
                opportunity_id, item.owner_id, item.title, item.description,
                json.dumps([str(url) for url in item.links]),
                str(item.contact_email) if item.contact_email else None,
                item.contact_phone, json.dumps(item.target_years), item.image_ids,
                item.posted_until)
            row = await conn.fetchrow(f"SELECT {COLUMNS} FROM {FROM} WHERE o.id=$1", opportunity_id)
            return _model(row)
        if current['status'] == 'approved':
            revision_data = item.model_dump(mode='json', exclude={'owner_id', 'submitted_by'})
            await conn.execute("UPDATE public.opportunities SET pending_revision=$3::jsonb, pending_revision_at=NOW(), updated_at=NOW() WHERE id=$1 AND submitted_by=$2", opportunity_id, item.owner_id, json.dumps(revision_data))
            row = await conn.fetchrow(f"SELECT {COLUMNS} FROM {FROM} WHERE o.id=$1", opportunity_id)
            return _model(row)
        row = await conn.fetchrow(
            """UPDATE public.opportunities
                  SET title=$3, description=$4, links=$5::jsonb,
                      contact_email=$6, contact_phone=$7,
                      target_years=$8::jsonb, image_ids=$9, posted_until=$10,
                      status='pending', editable_after_rejection=FALSE,
                      revision=revision+1, updated_at=NOW(),
                      reviewed_by=NULL, reviewed_at=NULL,
                      announcement_subject=NULL, announcement_body=NULL
                WHERE id=$1 AND submitted_by=$2 AND status='rejected'
                  AND editable_after_rejection=TRUE
                RETURNING id;""",
            opportunity_id, item.owner_id, item.title, item.description,
            json.dumps([str(url) for url in item.links]),
            str(item.contact_email) if item.contact_email else None,
            item.contact_phone, json.dumps(item.target_years), item.image_ids, item.posted_until,
        )
        if row is None:
            raise _review_conflict()
    return await get_opportunity(opportunity_id)


@router.post("/{opportunity_id}/submit")
async def submit_draft(opportunity_id: int, payload: OpportunityWithdraw) -> Opportunity:
    """Submitting a saved draft needs a fresh committee review, never auto-publish."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("""UPDATE public.opportunities
              SET status='pending', review_notes=NULL, reviewed_by=NULL,
                  reviewed_at=NULL, approved_at=NULL, revision=revision+1,
                  updated_at=NOW()
              WHERE id=$1 AND submitted_by=$2 AND status='draft'
                AND (posted_until IS NULL
                     OR posted_until >= (NOW() AT TIME ZONE 'Europe/London')::date)
              RETURNING id""", opportunity_id, payload.owner_id)
        if row is None:
            raise _review_conflict()
    return await get_opportunity(opportunity_id)


@router.post("/{opportunity_id}/withdraw")
async def withdraw_opportunity(opportunity_id: int, payload: OpportunityWithdraw) -> Opportunity:
    """Withdraw an approved listing atomically, only for its submitter.

    Never delete the submission or its original approval history. Clear pending
    edits so a committee review cannot accidentally republish the listing, and
    dismiss a queued/scheduled announcement in the same transaction.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        prior = await conn.fetchrow("""SELECT email_campaign_id FROM public.opportunities
              WHERE id=$1 AND submitted_by=$2 AND status='approved' FOR UPDATE""",
              opportunity_id, payload.owner_id)
        if prior is None:
            raise _review_conflict()
        row = await conn.fetchrow(
            """UPDATE public.opportunities
                  SET status='draft', withdrawn_at=NOW(), updated_at=NOW(),
                      approved_at=NULL, email_campaign_id=NULL,
                      announcement_subject=NULL, announcement_body=NULL,
                      pending_revision=NULL, pending_revision_at=NULL,
                      review_notes=NULL, editable_after_rejection=FALSE
                WHERE id=$1 AND submitted_by=$2 AND status='approved'
                RETURNING id, email_campaign_id""",
            opportunity_id, payload.owner_id,
        )
        if row is None:
            raise _review_conflict()
        await conn.execute("""UPDATE public.opportunity_publication_periods
              SET unpublished_at=NOW(), end_reason='submitter'
              WHERE opportunity_id=$1 AND unpublished_at IS NULL""", opportunity_id)
        # Drop the old queue entry's ability to send, preserving campaign
        # history and preventing stale announcements after future reapproval.
        if prior["email_campaign_id"] is not None:
            await conn.execute(
                """UPDATE public.email_campaigns
                      SET status='dismissed',
                          error='Opportunity withdrawn by its submitter.'
                    WHERE id=$1 AND opportunity_id=$2
                      AND kind='opportunity_announcement'
                      AND status IN ('awaiting_approval', 'scheduled')""",
                prior["email_campaign_id"], opportunity_id,
            )
    return await get_opportunity(opportunity_id)


@router.post("/{opportunity_id}/takedown")
async def takedown_opportunity(opportunity_id: int, review: OpportunityReview) -> Opportunity:
    if review.reviewed_by is None:
        raise HTTPException(400, "A reviewer is required")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        result = await conn.execute("""UPDATE public.opportunities SET status='rejected',
            pending_revision=NULL, pending_revision_at=NULL, review_notes=$3,
            editable_after_rejection=$4, reviewed_by=$2, reviewed_at=NOW(), updated_at=NOW()
            WHERE id=$1 AND status='approved'""", opportunity_id, review.reviewed_by,
            (review.notes or '').strip() or None, review.allow_edit)
        if result != 'UPDATE 1':
            raise _review_conflict()
        await conn.execute("""UPDATE public.opportunity_publication_periods
              SET unpublished_at=NOW(), end_reason='committee'
              WHERE opportunity_id=$1 AND unpublished_at IS NULL""", opportunity_id)
        await conn.execute("""UPDATE public.email_campaigns
              SET status='dismissed', error='Linked opportunity taken down by committee.'
              WHERE opportunity_id=$1 AND kind='opportunity_announcement'
                AND status IN ('awaiting_approval', 'scheduled')""", opportunity_id)
    return await get_opportunity(opportunity_id)


@router.post("/{opportunity_id}/review")
async def review_opportunity(opportunity_id: int, review: OpportunityReview) -> dict:
    if review.reviewed_by is None:
        raise HTTPException(400, "A reviewer account is required")
    if review.allow_edit and review.decision != "rejected":
        raise HTTPException(400, "Edits are only offered on rejection")
    if review.decision == "rejected" and review.allow_edit and not (review.notes or "").strip():
        raise HTTPException(400, "Write feedback when offering revisions")

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        current = await conn.fetchrow("SELECT status, pending_revision FROM public.opportunities WHERE id=$1 FOR UPDATE", opportunity_id)
        if current and current['status'] == 'approved' and current['pending_revision'] is not None:
            if review.decision == 'approved':
                raw_revision = current['pending_revision']
                # asyncpg decodes jsonb as text unless a codec was registered.
                revision = json.loads(raw_revision) if isinstance(raw_revision, str) else raw_revision
                await conn.execute("""UPDATE public.opportunities SET
                    title=$2, description=$3, links=$4::jsonb, contact_email=$5,
                    contact_phone=$6, target_years=$7::jsonb, image_ids=$8, posted_until=$9,
                    pending_revision=NULL, pending_revision_at=NULL,
                    revision=revision+1, reviewed_by=$10, reviewed_at=NOW(),
                    review_notes=$11, updated_at=NOW(),
                    announcement_subject=CASE WHEN email_campaign_id IS NULL THEN $12 ELSE announcement_subject END,
                    announcement_body=CASE WHEN email_campaign_id IS NULL THEN $13 ELSE announcement_body END
                    WHERE id=$1""", opportunity_id, revision['title'], revision['description'],
                    json.dumps(revision['links']), revision.get('contact_email'), revision.get('contact_phone'),
                    json.dumps(revision['target_years']), revision['image_ids'], date.fromisoformat(revision['posted_until']) if revision.get('posted_until') else None,
                    review.reviewed_by, (review.notes or '').strip() or None,
                    review.campaign_subject, review.campaign_body)
                await conn.execute("""UPDATE public.opportunity_publication_periods
                      SET posted_until=$2 WHERE opportunity_id=$1
                        AND unpublished_at IS NULL""", opportunity_id,
                    date.fromisoformat(revision['posted_until']) if revision.get('posted_until') else None)
            else:
                await conn.execute("""UPDATE public.opportunities SET pending_revision=NULL,
                    pending_revision_at=NULL, reviewed_by=$2, reviewed_at=NOW(),
                    review_notes=$3, updated_at=NOW() WHERE id=$1""", opportunity_id,
                    review.reviewed_by, (review.notes or '').strip() or None)
            updated = await conn.fetchrow(f"SELECT {COLUMNS} FROM {FROM} WHERE o.id=$1", opportunity_id)
            return {"opportunity": _model(updated).model_dump(mode='json'), "email_campaign_id": None}
        row = await conn.fetchrow(
            """UPDATE public.opportunities
                  SET status=$2::varchar, reviewed_by=$3, review_notes=$4,
                      reviewed_at=NOW(), updated_at=NOW(),
                      approved_at=CASE WHEN $2::varchar = 'approved' THEN NOW() ELSE NULL END,
                      editable_after_rejection=CASE WHEN $2::varchar = 'rejected' THEN $5 ELSE FALSE END,
                      announcement_subject=CASE WHEN $2::varchar = 'approved' THEN $6 ELSE NULL END,
                      announcement_body=CASE WHEN $2::varchar = 'approved' THEN $7 ELSE NULL END
                WHERE id=$1 AND status='pending'
                RETURNING id, approved_at, posted_until;""",
            opportunity_id, review.decision, review.reviewed_by,
            review.notes.strip() if review.notes and review.notes.strip() else None,
            review.allow_edit,
            review.campaign_subject if review.decision == "approved" else None,
            review.campaign_body if review.decision == "approved" else None,
        )
        if row is None:
            exists = await conn.fetchval("SELECT id FROM public.opportunities WHERE id=$1", opportunity_id)
            if exists is None:
                raise HTTPException(404, "No such opportunity")
            raise _review_conflict()
        if review.decision == "approved":
            await conn.execute("""INSERT INTO public.opportunity_publication_periods
                  (opportunity_id, published_at, posted_until) VALUES ($1,$2,$3)""",
                opportunity_id, row["approved_at"], row["posted_until"])
    return {"opportunity": (await get_opportunity(opportunity_id)).model_dump(mode="json"),
            "email_campaign_id": None}


@router.put("/{opportunity_id}/announcement")
async def save_announcement(opportunity_id: int, draft: AnnouncementDraft) -> Opportunity:
    if draft.owner_id is None:
        raise HTTPException(400, "An owner is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE public.opportunities
                  SET announcement_subject=$3, announcement_body=$4, updated_at=NOW()
                WHERE id=$1 AND submitted_by=$2 AND status='approved'
                  AND email_campaign_id IS NULL
                RETURNING id;""",
            opportunity_id, draft.owner_id, draft.subject, draft.body,
        )
    if row is None:
        raise _review_conflict()
    return await get_opportunity(opportunity_id)


@router.post("/{opportunity_id}/announcement/submit")
async def submit_announcement(opportunity_id: int, payload: AnnouncementSubmit) -> dict:
    """Owner's sign-off atomically creates one committee email queue entry.

    The audiences are derived by the public API from the stored year groups;
    the member cannot select arbitrary mailing lists. A conditional UPDATE
    serializes competing submissions before the INSERT and unique index.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """UPDATE public.opportunities
                  SET updated_at=NOW()
                WHERE id=$1 AND submitted_by=$2 AND status='approved'
                  AND email_campaign_id IS NULL
                  AND length(trim(coalesce(announcement_subject, ''))) >= 3
                  AND length(trim(coalesce(announcement_body, ''))) >= 20
                RETURNING announcement_subject, announcement_body;""",
            opportunity_id, payload.owner_id,
        )
        if row is None:
            raise _review_conflict()
        campaign_id = await conn.fetchval(
            """INSERT INTO public.email_campaigns
                 (subject, heading, body, audiences, status, kind,
                  opportunity_id, created_by)
               VALUES ($1, $1, $2, $3::jsonb, 'awaiting_approval',
                       'opportunity_announcement', $4, $5)
               RETURNING id;""",
            row["announcement_subject"], row["announcement_body"],
            json.dumps(payload.audiences), opportunity_id, payload.owner_id,
        )
        await conn.execute(
            "UPDATE public.opportunities SET email_campaign_id=$2 WHERE id=$1",
            opportunity_id, campaign_id,
        )
    return {"email_campaign_id": campaign_id}
