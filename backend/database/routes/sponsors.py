"""Sponsors, job listings, event claims, requests and assets."""

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.sponsors import (
    EventSponsorship,
    JobListing,
    JobListingWrite,
    Sponsor,
    SponsorAsset,
    SponsorRequest,
)
from postgres import get_pool

router = APIRouter(prefix="/sponsors")

SPONSOR_COLUMNS = """
    id, slug, name, sector, strand, one_line, blurb, with_us, site, logo_url,
    roles, active, sort_order, amount_pence, internal_note, agreement_year,
    cv_contact_email, cv_contact_name, cv_contact_note
"""

LISTING_COLUMNS = """
    id, sponsor_id, title, location, body, apply_url, closes_on, applications,
    status, created_at, updated_at
"""


@router.get("")
async def list_sponsors(active_only: bool = True) -> list[Sponsor]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {SPONSOR_COLUMNS} FROM public.sponsors
             WHERE ($1::bool IS NOT TRUE OR active)
             ORDER BY sort_order, name;
            """,
            active_only,
        )
    return [Sponsor(**dict(r)) for r in rows]


@router.get("/{sponsor_id}")
async def get_sponsor(sponsor_id: int) -> Sponsor:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {SPONSOR_COLUMNS} FROM public.sponsors WHERE id = $1;", sponsor_id
        )
    if row is None:
        raise HTTPException(404, "No such sponsor")
    return Sponsor(**dict(row))


@router.post("", status_code=201)
async def create_sponsor(sponsor: Sponsor) -> Sponsor:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.sponsors
                    (slug, name, sector, strand, one_line, blurb, with_us, site,
                     logo_url, roles, active, sort_order, amount_pence,
                     internal_note, agreement_year, cv_contact_email,
                     cv_contact_name, cv_contact_note)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                RETURNING {SPONSOR_COLUMNS};
                """,
                sponsor.slug, sponsor.name, sponsor.sector, sponsor.strand,
                sponsor.one_line, sponsor.blurb, sponsor.with_us, sponsor.site,
                sponsor.logo_url, sponsor.roles, sponsor.active, sponsor.sort_order,
                sponsor.amount_pence, sponsor.internal_note, sponsor.agreement_year,
                sponsor.cv_contact_email, sponsor.cv_contact_name,
                sponsor.cv_contact_note,
            )
        except UniqueViolationError:
            raise HTTPException(409, f"A sponsor with slug '{sponsor.slug}' already exists")
    return Sponsor(**dict(row))


@router.put("/{sponsor_id}")
async def update_sponsor(sponsor_id: int, sponsor: Sponsor) -> Sponsor:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.sponsors SET
                slug=$2, name=$3, sector=$4, strand=$5, one_line=$6, blurb=$7,
                with_us=$8, site=$9, logo_url=$10, roles=$11, active=$12,
                sort_order=$13, amount_pence=$14, internal_note=$15,
                agreement_year=$16, cv_contact_email=$17, cv_contact_name=$18,
                cv_contact_note=$19
             WHERE id=$1
            RETURNING {SPONSOR_COLUMNS};
            """,
            sponsor_id, sponsor.slug, sponsor.name, sponsor.sector, sponsor.strand,
            sponsor.one_line, sponsor.blurb, sponsor.with_us, sponsor.site,
            sponsor.logo_url, sponsor.roles, sponsor.active, sponsor.sort_order,
            sponsor.amount_pence, sponsor.internal_note, sponsor.agreement_year,
            sponsor.cv_contact_email, sponsor.cv_contact_name, sponsor.cv_contact_note,
        )
    if row is None:
        raise HTTPException(404, "No such sponsor")
    return Sponsor(**dict(row))


# ------------------------------------------------------------------ portal access


@router.post("/{sponsor_id}/users/{user_id}", status_code=204)
async def link_sponsor_user(sponsor_id: int, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.sponsor_users (user_id, sponsor_id) VALUES ($1,$2)
            ON CONFLICT DO NOTHING;
            """,
            user_id, sponsor_id,
        )


@router.delete("/{sponsor_id}/users/{user_id}", status_code=204)
async def unlink_sponsor_user(sponsor_id: int, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.sponsor_users WHERE user_id=$1 AND sponsor_id=$2;",
            user_id, sponsor_id,
        )


@router.get("/{sponsor_id}/users")
async def list_sponsor_users(sponsor_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.email, u.first_name, u.last_name
              FROM public.sponsor_users su
              JOIN public.users u ON u.id = su.user_id
             WHERE su.sponsor_id = $1;
            """,
            sponsor_id,
        )
    return [dict(r) for r in rows]


# -------------------------------------------------------------------- job listings


@router.get("/{sponsor_id}/listings")
async def list_listings(sponsor_id: int) -> list[JobListing]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {LISTING_COLUMNS} FROM public.job_listings "
            "WHERE sponsor_id = $1 ORDER BY created_at DESC;",
            sponsor_id,
        )
    return [JobListing(**dict(r)) for r in rows]


@router.post("/{sponsor_id}/listings", status_code=201)
async def create_listing(sponsor_id: int, listing: JobListingWrite) -> JobListing:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.job_listings
                (sponsor_id, title, location, body, apply_url, closes_on, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING {LISTING_COLUMNS};
            """,
            sponsor_id, listing.title, listing.location, listing.body,
            listing.apply_url, listing.closes_on, listing.status,
        )
    return JobListing(**dict(row))


@router.put("/listings/{listing_id}")
async def update_listing(listing_id: int, listing: JobListingWrite) -> JobListing:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.job_listings SET
                title=$2, location=$3, body=$4, apply_url=$5, closes_on=$6,
                status=$7, updated_at=NOW()
             WHERE id=$1
            RETURNING {LISTING_COLUMNS};
            """,
            listing_id, listing.title, listing.location, listing.body,
            listing.apply_url, listing.closes_on, listing.status,
        )
    if row is None:
        raise HTTPException(404, "No such listing")
    return JobListing(**dict(row))


@router.delete("/{sponsor_id}", status_code=204)
async def delete_sponsor(sponsor_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.sponsors WHERE id = $1;", sponsor_id)


@router.get("/internal/totals")
async def sponsorship_totals() -> dict:
    """Committee-only money view."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(amount_pence), 0) AS total_pence,
                   COUNT(*) FILTER (WHERE amount_pence > 0) AS paying,
                   COUNT(*) AS total
              FROM public.sponsors WHERE active;
            """
        )
    return dict(row)


@router.delete("/listings/{listing_id}", status_code=204)
async def delete_listing(listing_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.job_listings WHERE id = $1;", listing_id)


@router.get("/listings/all")
async def list_all_listings(status: str = "live") -> list[dict]:
    """Live roles across every sponsor, for the public careers list."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT l.id, l.title, l.location, l.closes_on, l.apply_url, l.status,
                   s.name AS sponsor_name, s.slug AS sponsor_slug, s.logo_url
              FROM public.job_listings l
              JOIN public.sponsors s ON s.id = l.sponsor_id
             WHERE l.status = $1 AND s.active
             ORDER BY l.closes_on NULLS LAST, l.created_at DESC;
            """,
            status,
        )
    return [dict(r) for r in rows]


# ------------------------------------------------------------- event sponsorships


@router.get("/{sponsor_id}/claims")
async def list_claims(sponsor_id: int) -> list[EventSponsorship]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, event_id, sponsor_id, status, note, created_at
              FROM public.event_sponsorships WHERE sponsor_id = $1 ORDER BY created_at DESC;
            """,
            sponsor_id,
        )
    return [EventSponsorship(**dict(r)) for r in rows]


@router.post("/{sponsor_id}/claims/{event_id}", status_code=201)
async def claim_event(sponsor_id: int, event_id: int, note: str | None = None) -> EventSponsorship:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.event_sponsorships (event_id, sponsor_id, note)
                VALUES ($1,$2,$3)
                RETURNING id, event_id, sponsor_id, status, note, created_at;
                """,
                event_id, sponsor_id, note,
            )
        except UniqueViolationError:
            raise HTTPException(409, "You have already claimed this event")
    return EventSponsorship(**dict(row))


@router.patch("/claims/{claim_id}")
async def set_claim_status(claim_id: int, status: str) -> EventSponsorship:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.event_sponsorships SET status = $2 WHERE id = $1
            RETURNING id, event_id, sponsor_id, status, note, created_at;
            """,
            claim_id, status,
        )
    if row is None:
        raise HTTPException(404, "No such claim")
    return EventSponsorship(**dict(row))


@router.get("/claims/claimable")
async def claimable_events(sponsor_id: int | None = None) -> list[dict]:
    """Future events the committee has opted in to sponsorship, unclaimed.

    Three conditions, all necessary: published, explicitly marked claimable, and
    not already claimed. An event created from a sponsor's own request is also
    excluded — that arrangement is already being negotiated, and putting it back
    in the queue would let somebody else take it.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.id, e.slug, e.title, e.starts_at, e.venue, e.capacity
              FROM public.events e
             WHERE e.published
               AND e.sponsorship_claimable
               AND e.starts_at >= (NOW() AT TIME ZONE 'Europe/London')
               AND e.requested_by_sponsor_id IS NULL
               AND NOT EXISTS (
                     SELECT 1 FROM public.event_sponsorships es
                      WHERE es.event_id = e.id AND es.status <> 'declined'
                   )
             ORDER BY e.starts_at;
            """
        )
    return [dict(r) for r in rows]


@router.get("/claims/is-claimable/{event_id}")
async def is_claimable(event_id: int) -> dict:
    """Whether a specific event may be claimed, and why not.

    Checked server-side at claim time: the list the sponsor was shown may be
    minutes stale, and a hidden button is not a control.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT e.published, e.sponsorship_claimable, e.starts_at,
                   e.requested_by_sponsor_id,
                   EXISTS (SELECT 1 FROM public.event_sponsorships es
                            WHERE es.event_id = e.id AND es.status <> 'declined') AS taken
              FROM public.events e WHERE e.id = $1;
            """,
            event_id,
        )
    if row is None:
        raise HTTPException(404, "No such event")

    if not row["published"]:
        return {"claimable": False, "reason": "not_published"}
    if not row["sponsorship_claimable"]:
        return {"claimable": False, "reason": "not_offered"}
    if row["requested_by_sponsor_id"] is not None:
        return {"claimable": False, "reason": "already_arranged"}
    if row["taken"]:
        return {"claimable": False, "reason": "taken"}
    return {"claimable": True, "reason": None}


# ----------------------------------------------------------- requests and assets


@router.post("/{sponsor_id}/requests", status_code=201)
async def create_request(sponsor_id: int, req: SponsorRequest) -> SponsorRequest:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.sponsor_requests (sponsor_id, format, body)
            VALUES ($1,$2,$3)
            RETURNING id, sponsor_id, format, body, status, created_at;
            """,
            sponsor_id, req.format, req.body,
        )
    return SponsorRequest(**dict(row))


@router.get("/{sponsor_id}/requests")
async def list_requests(sponsor_id: int) -> list[SponsorRequest]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, sponsor_id, format, body, status, created_at
              FROM public.sponsor_requests WHERE sponsor_id = $1 ORDER BY created_at DESC;
            """,
            sponsor_id,
        )
    return [SponsorRequest(**dict(r)) for r in rows]


@router.post("/{sponsor_id}/assets", status_code=201)
async def add_asset(sponsor_id: int, asset: SponsorAsset) -> SponsorAsset:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.sponsor_assets (sponsor_id, label, file_url)
            VALUES ($1,$2,$3)
            RETURNING id, sponsor_id, label, file_url, uploaded_at;
            """,
            sponsor_id, asset.label, asset.file_url,
        )
    return SponsorAsset(**dict(row))


@router.get("/{sponsor_id}/assets")
async def list_assets(sponsor_id: int) -> list[SponsorAsset]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, sponsor_id, label, file_url, uploaded_at
              FROM public.sponsor_assets WHERE sponsor_id = $1 ORDER BY uploaded_at DESC;
            """,
            sponsor_id,
        )
    return [SponsorAsset(**dict(r)) for r in rows]


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.sponsor_assets WHERE id = $1;", asset_id)


@router.get("/{sponsor_id}/stats")
async def sponsor_stats(sponsor_id: int) -> dict:
    """The four tiles at the top of the portal dashboard."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM public.job_listings
                WHERE sponsor_id = $1 AND status = 'live')                  AS live_listings,
              (SELECT COUNT(*) FROM public.job_listings
                WHERE sponsor_id = $1 AND status = 'live'
                  AND closes_on IS NOT NULL
                  AND closes_on <= (CURRENT_DATE + 7))                      AS closing_soon,
              (SELECT COALESCE(SUM(applications), 0) FROM public.job_listings
                WHERE sponsor_id = $1)                                      AS applications,
              (SELECT COUNT(*) FROM public.event_sponsorships
                WHERE sponsor_id = $1 AND status IN ('claimed','confirmed')) AS events_claimed,
              (SELECT COUNT(*) FROM public.events e
                WHERE e.published AND e.starts_at >= (NOW() AT TIME ZONE 'Europe/London')
                  AND NOT EXISTS (SELECT 1 FROM public.event_sponsorships es
                                   WHERE es.event_id = e.id AND es.status <> 'declined')
              )                                                             AS events_open,
              (SELECT COUNT(*) FROM public.members)                         AS members_reached;
            """,
            sponsor_id,
        )
    return dict(row)
