"""Sponsors, job listings, event claims, requests and assets."""

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException

from models.sponsors import (
    EventSponsorship,
    JobListing,
    JobListingWrite,
    Sponsor,
    SponsorAsset,
    SponsorContact,
    SponsorLink,
    SponsorMarketing,
    SponsorMarketingUpdate,
    SponsorNote,
    SponsorPackageDefault,
    SponsorRequest,
    SponsorRequestUpdate,
    SponsorTerm,
    SponsorUsageRecord,
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
    status, announced_at, created_at, updated_at
"""

CLAIM_COLUMNS = "id, event_id, sponsor_id, status, note, clause, created_at, updated_at"

REQUEST_COLUMNS = (
    "id, sponsor_id, format, body, timing, status, response, created_at, updated_at"
)

MARKETING_COLUMNS = """
    id, sponsor_id, academic_year, subject, body, media, audiences, channels,
    preferred_date, status, response, campaign_id, emailed_at, posted_at,
    created_by, decided_by, created_at, updated_at
"""

# A role is open through its closing day, London time: one closing on the 14th
# is still up at 23:30 on the 14th and gone at midnight. CURRENT_DATE would be
# the server's day, which is UTC and an hour behind for half the year.
LONDON_TODAY = "(NOW() AT TIME ZONE 'Europe/London')::date"


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
            SELECT u.id, u.email, u.first_name, u.last_name,
                   u.email_verified, su.created_at
              FROM public.sponsor_users su
              JOIN public.users u ON u.id = su.user_id
             WHERE su.sponsor_id = $1
             ORDER BY su.created_at, u.id;
            """,
            sponsor_id,
        )
    return [dict(r) for r in rows]


# Sponsor self-service access changes are separate from committee membership
# actions. The API checks the actor's sponsorship first; the adapter checks it
# again while holding a lock on the sponsor row so a concurrently removed user
# cannot grant new access on the strength of a stale request.


def _access_actor(request: Request) -> int:
    raw = request.headers.get("x-audit-actor-id", "")
    if not raw.isdecimal():
        raise HTTPException(403, "Sponsor account required")
    return int(raw)


async def _lock_sponsor_access(conn, sponsor_id: int, actor_id: int) -> None:
    sponsor = await conn.fetchval(
        "SELECT id FROM public.sponsors WHERE id = $1 FOR UPDATE", sponsor_id
    )
    if sponsor is None:
        raise HTTPException(404, "No such sponsor")
    member = await conn.fetchval(
        "SELECT 1 FROM public.sponsor_users WHERE sponsor_id=$1 AND user_id=$2",
        sponsor_id, actor_id,
    )
    if member is None:
        raise HTTPException(403, "You no longer have access to this sponsor")


@router.post("/{sponsor_id}/portal-access/{user_id}")
async def grant_portal_access(sponsor_id: int, user_id: int, request: Request) -> dict:
    actor_id = _access_actor(request)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await _lock_sponsor_access(conn, sponsor_id, actor_id)
        inserted = await conn.fetchval(
            """INSERT INTO public.sponsor_users (user_id, sponsor_id)
               VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING user_id""",
            user_id, sponsor_id,
        )
        if inserted is not None:
            await conn.execute(
                """INSERT INTO public.action_audit
                   (actor_user_id, action, entity_type, entity_id, after_data)
                   VALUES ($1, 'sponsor.access.grant', 'sponsor', $2,
                           jsonb_build_object('user_id', $3::bigint))""",
                actor_id, sponsor_id, user_id,
            )
    return {"created": inserted is not None}


@router.delete("/{sponsor_id}/portal-access/{user_id}")
async def revoke_portal_access(sponsor_id: int, user_id: int, request: Request) -> dict:
    actor_id = _access_actor(request)
    if user_id == actor_id:
        raise HTTPException(400, "You cannot remove your own access")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await _lock_sponsor_access(conn, sponsor_id, actor_id)
        # Serialising against the sponsor row prevents two concurrent removals
        # from each seeing two users and leaving the sponsor without any account.
        count = await conn.fetchval(
            "SELECT count(*) FROM public.sponsor_users WHERE sponsor_id=$1",
            sponsor_id,
        )
        if count <= 1:
            raise HTTPException(409, "The last sponsor account cannot be removed")
        deleted = await conn.fetchval(
            """DELETE FROM public.sponsor_users
               WHERE sponsor_id=$1 AND user_id=$2 RETURNING user_id""",
            sponsor_id, user_id,
        )
        if deleted is None:
            raise HTTPException(404, "That person no longer has access")
        await conn.execute(
            """INSERT INTO public.action_audit
               (actor_user_id, action, entity_type, entity_id, before_data)
               VALUES ($1, 'sponsor.access.revoke', 'sponsor', $2,
                       jsonb_build_object('user_id', $3::bigint))""",
            actor_id, sponsor_id, user_id,
        )
    return {"ok": True}


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
                -- Reopening a closed or draft listing makes it news again, so
                -- it goes back into the next "new roles" email.
                announced_at = CASE WHEN $7 = 'live' AND status <> 'live'
                                    THEN NULL ELSE announced_at END,
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


@router.get("/listings/unannounced")
async def unannounced_listings() -> list[dict]:
    """Live roles members have not yet been emailed about."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT l.id, l.title, l.location, l.closes_on, l.apply_url,
                   l.created_at, s.name AS sponsor_name, s.slug AS sponsor_slug
              FROM public.job_listings l
              JOIN public.sponsors s ON s.id = l.sponsor_id
             WHERE l.status = 'live' AND l.announced_at IS NULL AND s.active
               AND (l.closes_on IS NULL OR l.closes_on >= {LONDON_TODAY})
             ORDER BY l.created_at;
            """
        )
    return [dict(r) for r in rows]


@router.post("/listings/announced", status_code=204)
async def mark_announced(ids: list[int]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.job_listings SET announced_at = NOW() WHERE id = ANY($1::bigint[]);",
            ids,
        )


@router.get("/listings/all")
async def list_all_listings(status: str = "live") -> list[dict]:
    """Live roles across every sponsor, for the public careers list.

    A role past its closing day is left out even though it is still 'live':
    nothing closes a listing when its date passes, and a sponsor who never comes
    back to close it should not leave members applying for a shut role.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT l.id, l.title, l.location, l.closes_on, l.apply_url, l.status,
                   s.name AS sponsor_name, s.slug AS sponsor_slug, s.logo_url
              FROM public.job_listings l
              JOIN public.sponsors s ON s.id = l.sponsor_id
             WHERE l.status = $1 AND s.active
               AND (l.closes_on IS NULL OR l.closes_on >= {LONDON_TODAY})
             ORDER BY l.closes_on NULLS LAST, l.created_at DESC;
            """,
            status,
        )
    return [dict(r) for r in rows]


# ------------------------------------------------------------- event sponsorships


@router.get("/{sponsor_id}/claims")
async def list_claims(sponsor_id: int) -> list[dict]:
    """Every event this sponsor has registered interest in or been linked to,
    with enough of the event to show it — one query rather than one per row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT es.id, es.event_id, es.sponsor_id, es.status, es.note,
                   es.clause, es.created_at, es.updated_at,
                   e.slug, e.title, e.starts_at, e.venue, e.published,
                   e.academic_year
              FROM public.event_sponsorships es
              JOIN public.events e ON e.id = es.event_id
             WHERE es.sponsor_id = $1
             ORDER BY e.starts_at DESC;
            """,
            sponsor_id,
        )
    return [dict(r) for r in rows]


@router.post("/{sponsor_id}/claims/{event_id}", status_code=201)
async def register_interest(
    sponsor_id: int, event_id: int, note: str | None = None
) -> EventSponsorship:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.event_sponsorships (event_id, sponsor_id, note)
                VALUES ($1,$2,$3)
                RETURNING {CLAIM_COLUMNS};
                """,
                event_id, sponsor_id, note,
            )
        except UniqueViolationError:
            raise HTTPException(409, "You have already registered interest in this event")
    return EventSponsorship(**dict(row))


@router.delete("/{sponsor_id}/claims/{event_id}", status_code=204)
async def remove_claim(sponsor_id: int, event_id: int, only_interest: bool = False) -> None:
    """Withdraw interest, or unlink an event.

    `only_interest` is what the sponsor's own withdraw sends: they may take back
    an expression of interest, never undo a link the committee made.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM public.event_sponsorships
             WHERE sponsor_id = $1 AND event_id = $2
               AND ($3::bool IS NOT TRUE OR status = 'interested');
            """,
            sponsor_id, event_id, only_interest,
        )


@router.put("/{sponsor_id}/links/{event_id}")
async def link_event(sponsor_id: int, event_id: int, link: SponsorLink) -> EventSponsorship:
    """Attach an existing event to a sponsor, confirmed.

    An upsert: if the sponsor had registered interest, that row becomes the
    link, so the history of who asked first is not lost to a second row.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.event_sponsorships
                (event_id, sponsor_id, status, clause, note)
            VALUES ($1, $2, 'confirmed', $3, $4)
            ON CONFLICT (event_id, sponsor_id) DO UPDATE
               SET status = 'confirmed', clause = EXCLUDED.clause,
                   note = COALESCE(EXCLUDED.note, public.event_sponsorships.note),
                   updated_at = NOW()
            RETURNING {CLAIM_COLUMNS};
            """,
            event_id, sponsor_id, link.clause, link.note,
        )
    return EventSponsorship(**dict(row))


@router.patch("/claims/{claim_id}")
async def set_claim_status(claim_id: int, status: str) -> EventSponsorship:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.event_sponsorships SET status = $2, updated_at = NOW()
             WHERE id = $1
            RETURNING {CLAIM_COLUMNS};
            """,
            claim_id, status,
        )
    if row is None:
        raise HTTPException(404, "No such claim")
    return EventSponsorship(**dict(row))


@router.get("/links/confirmed")
async def confirmed_links(event_id: int | None = None) -> list[dict]:
    """Confirmed sponsor-event links, with the sponsor's public face.

    Feeds the "with" line on an event page, the events on a sponsor's card and
    the badges in the admin event list. `sponsor_active` is returned so a public
    caller can drop drafts: an unsigned company must not appear on an event.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT es.event_id, es.sponsor_id, es.clause,
                   s.slug AS sponsor_slug, s.name AS sponsor_name,
                   s.logo_url AS sponsor_logo_url, s.active AS sponsor_active,
                   e.slug AS event_slug, e.title AS event_title,
                   e.starts_at AS event_starts_at, e.published AS event_published,
                   e.academic_year AS event_academic_year
              FROM public.event_sponsorships es
              JOIN public.sponsors s ON s.id = es.sponsor_id
              JOIN public.events e   ON e.id = es.event_id
             WHERE es.status = 'confirmed'
               AND ($1::bigint IS NULL OR es.event_id = $1)
             ORDER BY e.starts_at, s.sort_order, s.name;
            """,
            event_id,
        )
    return [dict(r) for r in rows]


@router.get("/interest/open")
async def open_interest() -> list[dict]:
    """Interest waiting on a committee decision, across every sponsor."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT es.id, es.event_id, es.sponsor_id, es.note, es.created_at,
                   s.name AS sponsor_name, e.title AS event_title,
                   e.starts_at AS event_starts_at
              FROM public.event_sponsorships es
              JOIN public.sponsors s ON s.id = es.sponsor_id
              JOIN public.events e   ON e.id = es.event_id
             WHERE es.status = 'interested'
             ORDER BY es.created_at;
            """
        )
    return [dict(r) for r in rows]


@router.get("/claims/claimable")
async def claimable_events(sponsor_id: int | None = None) -> list[dict]:
    """Future events the committee has opened to sponsorship and not yet given
    to anyone.

    Explicitly marked sponsorable, in the future, and with no confirmed
    sponsor. Published or not: most are offered while still an idea — a draft
    with a provisional date and the details unconfirmed — which sponsors may
    see and students may not (every public read filters on `published`).
    Several sponsors may register interest in the same event — the committee
    chooses — so interest does not take an event off the list; a confirmed
    link does. An event created from a sponsor's own request is excluded too:
    that arrangement is already being negotiated.

    With `sponsor_id`, each row says whether that sponsor has already
    registered interest, so the button can say so instead of erroring.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.id, e.slug, e.title, e.starts_at, e.venue, e.capacity,
                   e.category, e.published, e.description,
                   EXISTS (SELECT 1 FROM public.event_sponsorships mine
                            WHERE mine.event_id = e.id
                              AND mine.sponsor_id = $1
                              AND mine.status = 'interested') AS interested
              FROM public.events e
             WHERE e.sponsorship_claimable
               AND e.starts_at >= NOW()
               AND e.requested_by_sponsor_id IS NULL
               AND NOT EXISTS (
                     SELECT 1 FROM public.event_sponsorships es
                      WHERE es.event_id = e.id AND es.status = 'confirmed'
                   )
             ORDER BY e.starts_at;
            """,
            sponsor_id,
        )
    return [dict(r) for r in rows]


@router.get("/claims/is-claimable/{event_id}")
async def is_claimable(event_id: int, sponsor_id: int | None = None) -> dict:
    """Whether a sponsor may register interest in an event, and why not.

    Checked server-side at the moment of asking: the list the sponsor was shown
    may be minutes stale, and a hidden button is not a control.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT e.sponsorship_claimable,
                   e.starts_at < NOW() AS past,
                   e.requested_by_sponsor_id,
                   EXISTS (SELECT 1 FROM public.event_sponsorships es
                            WHERE es.event_id = e.id AND es.status = 'confirmed') AS taken,
                   EXISTS (SELECT 1 FROM public.event_sponsorships es
                            WHERE es.event_id = e.id AND es.sponsor_id = $2) AS asked
              FROM public.events e WHERE e.id = $1;
            """,
            event_id, sponsor_id,
        )
    if row is None:
        raise HTTPException(404, "No such event")

    # Not gated on `published`: a sponsorable draft is an idea sponsors are
    # meant to see (see `claimable_events`).
    if not row["sponsorship_claimable"]:
        return {"claimable": False, "reason": "not_offered"}
    if row["past"]:
        return {"claimable": False, "reason": "past"}
    if row["requested_by_sponsor_id"] is not None:
        return {"claimable": False, "reason": "already_arranged"}
    if row["taken"]:
        return {"claimable": False, "reason": "taken"}
    if row["asked"]:
        return {"claimable": False, "reason": "already_interested"}
    return {"claimable": True, "reason": None}


# ----------------------------------------------------------- requests and assets


@router.post("/{sponsor_id}/requests", status_code=201)
async def create_request(sponsor_id: int, req: SponsorRequest) -> SponsorRequest:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.sponsor_requests (sponsor_id, format, body, timing)
            VALUES ($1,$2,$3,$4)
            RETURNING {REQUEST_COLUMNS};
            """,
            sponsor_id, req.format, req.body, req.timing,
        )
    return SponsorRequest(**dict(row))


@router.get("/{sponsor_id}/requests")
async def list_requests(sponsor_id: int) -> list[SponsorRequest]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {REQUEST_COLUMNS}
              FROM public.sponsor_requests WHERE sponsor_id = $1 ORDER BY created_at DESC;
            """,
            sponsor_id,
        )
    return [SponsorRequest(**dict(r)) for r in rows]


@router.get("/requests/all")
async def all_requests(open_only: bool = False) -> list[dict]:
    """Requests across every sponsor, newest first, with the sponsor's name."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.sponsor_id, r.format, r.body, r.timing, r.status,
                   r.response, r.created_at, r.updated_at, s.name AS sponsor_name
              FROM public.sponsor_requests r
              JOIN public.sponsors s ON s.id = r.sponsor_id
             WHERE ($1::bool IS NOT TRUE OR r.status <> 'closed')
             ORDER BY r.created_at DESC;
            """,
            open_only,
        )
    return [dict(r) for r in rows]


@router.patch("/requests/{request_id}")
async def update_request(request_id: int, update: SponsorRequestUpdate) -> SponsorRequest:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.sponsor_requests SET
                status   = COALESCE($2, status),
                response = COALESCE($3, response),
                updated_at = NOW()
             WHERE id = $1
            RETURNING {REQUEST_COLUMNS};
            """,
            request_id, update.status, update.response,
        )
    if row is None:
        raise HTTPException(404, "No such request")
    return SponsorRequest(**dict(row))


# ---------------------------------------------------------------- contacts


@router.get("/{sponsor_id}/contacts")
async def list_contacts(sponsor_id: int) -> list[SponsorContact]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, sponsor_id, kind, value, label, note, sort_order
              FROM public.sponsor_contacts WHERE sponsor_id = $1
             ORDER BY sort_order, id;
            """,
            sponsor_id,
        )
    return [SponsorContact(**dict(r)) for r in rows]


@router.put("/{sponsor_id}/contacts")
async def replace_contacts(sponsor_id: int, contacts: list[SponsorContact]) -> list[SponsorContact]:
    """Replace the whole list in one transaction.

    The portal edits the list as a list — add, remove, reorder — so storing it
    the same way means there is no partial state where half a save landed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM public.sponsor_contacts WHERE sponsor_id = $1;", sponsor_id
            )
            for index, c in enumerate(contacts):
                await conn.execute(
                    """
                    INSERT INTO public.sponsor_contacts
                        (sponsor_id, kind, value, label, note, sort_order)
                    VALUES ($1,$2,$3,$4,$5,$6);
                    """,
                    sponsor_id, c.kind, c.value, c.label, c.note, index,
                )
    return await list_contacts(sponsor_id)


@router.get("/contacts/all")
async def all_contacts() -> list[SponsorContact]:
    """Every sponsor's contacts, for the public list in one query."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, sponsor_id, kind, value, label, note, sort_order
              FROM public.sponsor_contacts ORDER BY sponsor_id, sort_order, id;
            """
        )
    return [SponsorContact(**dict(r)) for r in rows]


# ------------------------------------------------------------------- terms


@router.get("/{sponsor_id}/terms")
async def list_terms(sponsor_id: int) -> list[SponsorTerm]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT clause, agreed, allowance, note, done_at, updated_at, updated_by
              FROM public.sponsor_terms WHERE sponsor_id = $1;
            """,
            sponsor_id,
        )
    return [SponsorTerm(**dict(r)) for r in rows]


@router.get("/terms/all")
async def all_terms() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sponsor_id, clause, agreed, allowance FROM public.sponsor_terms;"
        )
    return [dict(r) for r in rows]


@router.get("/terms/defaults")
async def package_defaults() -> list[SponsorPackageDefault]:
    """The committee's standard-package allowances. A clause with no row keeps
    the figure in api/services/agreement.py."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT clause, allowance, updated_at, updated_by
              FROM public.sponsor_package_defaults;
            """
        )
    return [SponsorPackageDefault(**dict(r)) for r in rows]


@router.put("/terms/defaults/{clause}")
async def set_package_default(
    clause: str, default: SponsorPackageDefault
) -> SponsorPackageDefault:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.sponsor_package_defaults (clause, allowance, updated_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (clause) DO UPDATE SET
                allowance = EXCLUDED.allowance, updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
            RETURNING clause, allowance, updated_at, updated_by;
            """,
            clause, default.allowance, default.updated_by,
        )
    return SponsorPackageDefault(**dict(row))


@router.put("/{sponsor_id}/terms/{clause}")
async def set_term(sponsor_id: int, clause: str, term: SponsorTerm) -> SponsorTerm:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.sponsor_terms
                (sponsor_id, clause, agreed, allowance, note, done_at, updated_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (sponsor_id, clause) DO UPDATE SET
                agreed = EXCLUDED.agreed, allowance = EXCLUDED.allowance,
                note = EXCLUDED.note, done_at = EXCLUDED.done_at,
                updated_by = EXCLUDED.updated_by, updated_at = NOW()
            RETURNING clause, agreed, allowance, note, done_at, updated_at, updated_by;
            """,
            sponsor_id, clause, term.agreed, term.allowance, term.note,
            term.done_at, term.updated_by,
        )
    return SponsorTerm(**dict(row))


# Everything that counts against a sponsor's clauses in one academic year, one
# row per use. Both the counts and the itemised list come from this, so the
# number a sponsor is shown is always the length of the list beside it.
#
# Events count by the year of the event, not of the link: a workshop agreed in
# July for October belongs to October's year. Marketing counts anything not
# declined or withdrawn — a pending request has already spent the allowance,
# or a sponsor could queue ten and have them all approved. A record is a use
# the committee entered by hand because it happened outside the portal.
USAGE_ITEMS = """
    SELECT es.clause, 'event' AS kind, e.id AS ref_id, e.title,
           (e.starts_at AT TIME ZONE 'Europe/London')::date AS happened_on,
           es.status
      FROM public.event_sponsorships es
      JOIN public.events e ON e.id = es.event_id
     WHERE es.sponsor_id = $1 AND es.status = 'confirmed'
       AND es.clause IS NOT NULL AND e.academic_year = $2
    UNION ALL
    SELECT 'marketing', 'marketing', m.id, m.subject,
           (m.created_at AT TIME ZONE 'Europe/London')::date, m.status
      FROM public.sponsor_marketing m
     WHERE m.sponsor_id = $1 AND m.academic_year = $2
       AND m.status IN ('pending', 'shared')
    UNION ALL
    SELECT r.clause, 'record', r.id, r.title, r.happened_on, 'recorded'
      FROM public.sponsor_usage_records r
     WHERE r.sponsor_id = $1 AND r.academic_year = $2
"""


@router.get("/{sponsor_id}/usage")
async def usage(sponsor_id: int, academic_year: str) -> dict:
    """How much of each clause has been used in a year, as counts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT clause, COUNT(*) AS n FROM ({USAGE_ITEMS}) u GROUP BY clause;",
            sponsor_id, academic_year,
        )
    return {r["clause"]: r["n"] for r in rows}


@router.get("/{sponsor_id}/usage/items")
async def usage_items(sponsor_id: int, academic_year: str) -> list[dict]:
    """The same uses, itemised, oldest first (undated records last)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT * FROM ({USAGE_ITEMS}) u
             ORDER BY happened_on NULLS LAST, kind, ref_id;
            """,
            sponsor_id, academic_year,
        )
    return [dict(r) for r in rows]


@router.post("/{sponsor_id}/usage-records", status_code=201)
async def add_usage_record(sponsor_id: int, record: SponsorUsageRecord) -> SponsorUsageRecord:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.sponsor_usage_records
                (sponsor_id, clause, academic_year, title, happened_on, created_by)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id, sponsor_id, clause, academic_year, title, happened_on,
                      created_by, created_at;
            """,
            sponsor_id, record.clause, record.academic_year, record.title,
            record.happened_on, record.created_by,
        )
    return SponsorUsageRecord(**dict(row))


@router.delete("/{sponsor_id}/usage-records/{record_id}")
async def remove_usage_record(sponsor_id: int, record_id: int) -> dict:
    """Scoped by sponsor as well as id, so a mistyped id cannot remove another
    sponsor's record. Answers with a body rather than a 204, because the api's
    client reads 204 and 404 alike as "nothing"."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM public.sponsor_usage_records WHERE id = $1 AND sponsor_id = $2;",
            record_id, sponsor_id,
        )
    if result == "DELETE 0":
        raise HTTPException(404, "No such record")
    return {"deleted": record_id}


# ------------------------------------------------------------------- notes


@router.post("/{sponsor_id}/notes", status_code=201)
async def add_note(sponsor_id: int, note: SponsorNote) -> SponsorNote:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.sponsor_notes
                (sponsor_id, topic, topic_label, body, from_committee, author_id)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id, sponsor_id, topic, topic_label, body, from_committee,
                      author_id, read_at, created_at;
            """,
            sponsor_id, note.topic, note.topic_label, note.body,
            note.from_committee, note.author_id,
        )
    return SponsorNote(**dict(row))


@router.get("/{sponsor_id}/notes")
async def list_notes(sponsor_id: int, topic: str | None = None) -> list[dict]:
    """A sponsor's notes, oldest first, with who wrote each."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.sponsor_id, n.topic, n.topic_label, n.body,
                   n.from_committee, n.author_id, n.read_at, n.created_at,
                   u.email AS author_email, u.first_name AS author_first_name,
                   u.last_name AS author_last_name
              FROM public.sponsor_notes n
              LEFT JOIN public.users u ON u.id = n.author_id
             WHERE n.sponsor_id = $1 AND ($2::varchar IS NULL OR n.topic = $2)
             ORDER BY n.created_at, n.id;
            """,
            sponsor_id, topic,
        )
    return [dict(r) for r in rows]


@router.post("/{sponsor_id}/notes/read", status_code=204)
async def mark_notes_read(
    sponsor_id: int, from_committee: bool, topic: str | None = None
) -> None:
    """Mark one side's notes as read by the other — the committee reading a
    sponsor's notes passes from_committee=false, and the reverse."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.sponsor_notes SET read_at = NOW()
             WHERE sponsor_id = $1 AND from_committee = $2 AND read_at IS NULL
               AND ($3::varchar IS NULL OR topic = $3);
            """,
            sponsor_id, from_committee, topic,
        )


@router.get("/notes/unread")
async def unread_notes() -> list[dict]:
    """Sponsor notes the committee has not read yet, across every sponsor."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.sponsor_id, n.topic, n.topic_label, n.body,
                   n.created_at, s.name AS sponsor_name
              FROM public.sponsor_notes n
              JOIN public.sponsors s ON s.id = n.sponsor_id
             WHERE n.read_at IS NULL AND NOT n.from_committee
             ORDER BY n.created_at;
            """
        )
    return [dict(r) for r in rows]


# --------------------------------------------------------------- marketing


@router.post("/{sponsor_id}/marketing", status_code=201)
async def create_marketing(sponsor_id: int, item: SponsorMarketing) -> SponsorMarketing:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.sponsor_marketing
                (sponsor_id, academic_year, subject, body, media, audiences,
                 channels, preferred_date, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING {MARKETING_COLUMNS};
            """,
            sponsor_id, item.academic_year, item.subject, item.body, item.media,
            item.audiences, item.channels, item.preferred_date, item.created_by,
        )
    return SponsorMarketing(**dict(row))


@router.get("/{sponsor_id}/marketing")
async def list_marketing(sponsor_id: int) -> list[SponsorMarketing]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {MARKETING_COLUMNS} FROM public.sponsor_marketing
             WHERE sponsor_id = $1 ORDER BY created_at DESC;
            """,
            sponsor_id,
        )
    return [SponsorMarketing(**dict(r)) for r in rows]


@router.get("/marketing/all")
async def all_marketing(status: str | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {", ".join("m." + c.strip() for c in MARKETING_COLUMNS.split(","))},
                   s.name AS sponsor_name
              FROM public.sponsor_marketing m
              JOIN public.sponsors s ON s.id = m.sponsor_id
             WHERE ($1::varchar IS NULL OR m.status = $1)
             ORDER BY m.created_at;
            """,
            status,
        )
    return [dict(r) for r in rows]


@router.get("/marketing/{marketing_id}")
async def get_marketing(marketing_id: int) -> SponsorMarketing:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {MARKETING_COLUMNS} FROM public.sponsor_marketing WHERE id = $1;",
            marketing_id,
        )
    if row is None:
        raise HTTPException(404, "No such marketing request")
    return SponsorMarketing(**dict(row))


@router.patch("/marketing/{marketing_id}")
async def update_marketing(
    marketing_id: int, update: SponsorMarketingUpdate
) -> SponsorMarketing:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.sponsor_marketing SET
                subject     = COALESCE($2, subject),
                body        = COALESCE($3, body),
                status      = COALESCE($4, status),
                response    = COALESCE($5, response),
                campaign_id = COALESCE($6, campaign_id),
                decided_by  = COALESCE($7, decided_by),
                emailed_at  = CASE WHEN $8 THEN NOW() ELSE emailed_at END,
                posted_at   = CASE WHEN $9 THEN NOW() ELSE posted_at END,
                updated_at  = NOW()
             WHERE id = $1
            RETURNING {MARKETING_COLUMNS};
            """,
            marketing_id, update.subject, update.body, update.status,
            update.response, update.campaign_id, update.decided_by,
            update.mark_emailed, update.mark_posted,
        )
    if row is None:
        raise HTTPException(404, "No such marketing request")
    return SponsorMarketing(**dict(row))


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
    """The tiles at the top of the portal dashboard.

    "Live" means on the public careers list, so a role past its closing day is
    not counted however its status reads (see `list_all_listings`).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
              (SELECT COUNT(*) FROM public.job_listings
                WHERE sponsor_id = $1 AND status = 'live'
                  AND (closes_on IS NULL OR closes_on >= {LONDON_TODAY}))   AS live_listings,
              (SELECT COUNT(*) FROM public.job_listings
                WHERE sponsor_id = $1 AND status = 'live'
                  AND closes_on BETWEEN {LONDON_TODAY}
                                    AND {LONDON_TODAY} + 7)                 AS closing_soon,
              (SELECT COALESCE(SUM(applications), 0) FROM public.job_listings
                WHERE sponsor_id = $1)                                      AS applications,
              (SELECT COUNT(*) FROM public.event_sponsorships
                WHERE sponsor_id = $1 AND status = 'confirmed')             AS events_linked,
              (SELECT COUNT(*) FROM public.event_sponsorships
                WHERE sponsor_id = $1 AND status = 'interested')            AS events_interested,
              -- The same conditions as the sponsorable list, so the tile and
              -- the list under it cannot disagree.
              (SELECT COUNT(*) FROM public.events e
                WHERE e.sponsorship_claimable
                  AND e.starts_at >= NOW()
                  AND e.requested_by_sponsor_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM public.event_sponsorships es
                                   WHERE es.event_id = e.id AND es.status = 'confirmed')
              )                                                             AS events_open;
            """,
            sponsor_id,
        )
    return dict(row)
