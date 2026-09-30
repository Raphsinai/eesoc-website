"""Storage for the eActivities mirror: members, committee, products, sales.

Every sync endpoint is an idempotent bulk upsert — re-running a sync must not
duplicate rows, because the api service re-syncs daily and admins can trigger it
by hand at any time.
"""

from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.auth import Member
from models.union import (
    CommitteeEntry,
    CommitteeMember,
    CommitteeProfile,
    SiteContent,
    SyncRun,
    UnionProduct,
    UnionSale,
)
from postgres import get_pool

router = APIRouter(prefix="/union")

MEMBER_COLUMNS = "id, cid, first_name, last_name, email, login, order_no, member_type, academic_year"


# ------------------------------------------------------------------------- members


@router.post("/members/sync")
async def sync_members(members: list[Member], academic_year: str) -> int:
    """Upsert on CID. `academic_year` records the most recent year ICU reported
    this person as a member, which is how membership expiry is evaluated."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            """
            INSERT INTO public.members
                (cid, first_name, last_name, email, login, order_no, member_type,
                 academic_year, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8, NOW())
            ON CONFLICT (cid) DO UPDATE SET
                first_name    = EXCLUDED.first_name,
                last_name     = EXCLUDED.last_name,
                email         = EXCLUDED.email,
                login         = EXCLUDED.login,
                order_no      = EXCLUDED.order_no,
                member_type   = EXCLUDED.member_type,
                academic_year = EXCLUDED.academic_year,
                synced_at     = NOW();
            """,
            [
                (m.cid, m.first_name, m.last_name, m.email, m.login,
                 m.order_no, m.member_type, academic_year)
                for m in members
            ],
        )
    return len(members)


@router.get("/members/lookup")
async def lookup_member(
    email: str | None = None, login: str | None = None, cid: str | None = None
) -> Member:
    """Find an ICU member by any identifier we might hold.

    Matching on email alone is not enough: a member's ICU record may carry a
    personal address, and plenty of @imperial.ac.uk addresses belong to staff and
    alumni who are not members. The login is derived from the address as a
    fallback because Imperial logins are the local part of the college address.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {MEMBER_COLUMNS} FROM public.members
             WHERE ($1::varchar IS NOT NULL AND lower(email) = lower($1))
                OR ($2::varchar IS NOT NULL AND lower(login) = lower($2))
                OR ($3::varchar IS NOT NULL AND cid = $3)
             LIMIT 1;
            """,
            email,
            login,
            cid,
        )
    if row is None:
        raise HTTPException(404, "No matching member")
    return Member(**dict(row))


@router.get("/members/by-id/{member_id}")
async def get_member(member_id: int) -> Member:
    """Fetch a member by primary key.

    Needed because a user's stored email is canonicalised and no longer matches
    the address in the feed — the link to the member is the id on the account.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {MEMBER_COLUMNS} FROM public.members WHERE id = $1;", member_id
        )
    if row is None:
        raise HTTPException(404, "No such member")
    return Member(**dict(row))


@router.get("/members/count")
async def member_count(academic_year: str | None = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM public.members WHERE ($1::varchar IS NULL OR academic_year = $1);",
            academic_year,
        )


# ----------------------------------------------------------------------- committee


@router.post("/committee/sync")
async def sync_committee(members: list[CommitteeMember], academic_year: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # A post that disappears from ICU's list should disappear here too, so
        # the year's rows are replaced wholesale rather than merged.
        await conn.execute(
            "DELETE FROM public.committee WHERE academic_year = $1;", academic_year
        )
        await conn.executemany(
            """
            INSERT INTO public.committee
                (cid, first_name, last_name, email, login, post_name, phone_no,
                 start_date, end_date, academic_year, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, NOW())
            ON CONFLICT (cid, post_name, academic_year) DO NOTHING;
            """,
            [
                (m.cid, m.first_name, m.last_name, m.email, m.login, m.post_name,
                 m.phone_no, m.start_date, m.end_date, academic_year)
                for m in members
            ],
        )
    return len(members)


@router.get("/committee")
async def list_committee(academic_year: str) -> list[CommitteeEntry]:
    """ICU's roster left-joined with our editorial overlay."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.cid, c.first_name, c.last_name, c.email, c.login,
                   c.post_name, c.phone_no, c.start_date, c.end_date, c.academic_year,
                   COALESCE(p.intro, '')  AS intro,
                   p.photo_url,
                   p.display_role,
                   COALESCE(p.row_number, 1)   AS row_number,
                   COALESCE(p.sort_order, 100) AS sort_order
              FROM public.committee c
              -- Matched on login: a re-sync replaces every roster row, so the
              -- overlay has to attach to the person, not to the row.
              LEFT JOIN public.committee_profiles p
                     ON lower(p.login) = lower(c.login)
             WHERE c.academic_year = $1
             ORDER BY COALESCE(p.row_number, 1),
                      COALESCE(p.sort_order, 100),
                      c.post_name;
            """,
            academic_year,
        )
    return [CommitteeEntry(**dict(r)) for r in rows]


@router.put("/committee/profiles/{login}")
async def upsert_committee_profile(login: str, profile: CommitteeProfile) -> CommitteeProfile:
    """Write the editable half of a committee entry, keyed on login."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.committee_profiles
                (login, cid, intro, photo_url, display_role, row_number, sort_order)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (login) DO UPDATE SET
                cid          = COALESCE(EXCLUDED.cid, public.committee_profiles.cid),
                intro        = EXCLUDED.intro,
                photo_url    = EXCLUDED.photo_url,
                display_role = EXCLUDED.display_role,
                row_number   = EXCLUDED.row_number,
                sort_order   = EXCLUDED.sort_order,
                updated_at   = NOW()
            RETURNING cid, login, intro, photo_url, display_role, row_number, sort_order;
            """,
            login.strip().lower(), profile.cid, profile.intro, profile.photo_url,
            profile.display_role, profile.row_number, profile.sort_order,
        )
    return CommitteeProfile(**dict(row))


# ------------------------------------------------------------------------ products


@router.post("/products/sync")
async def sync_products(products: list[UnionProduct], academic_year: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            """
            INSERT INTO public.union_products
                (product_id, product_line_id, academic_year, product_name, line_name,
                 description, price, active, selling_date_start, selling_date_end,
                 url, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, NOW())
            ON CONFLICT (product_id, product_line_id, academic_year) DO UPDATE SET
                product_name       = EXCLUDED.product_name,
                line_name          = EXCLUDED.line_name,
                description        = EXCLUDED.description,
                price              = EXCLUDED.price,
                active             = EXCLUDED.active,
                selling_date_start = EXCLUDED.selling_date_start,
                selling_date_end   = EXCLUDED.selling_date_end,
                url                = EXCLUDED.url,
                synced_at          = NOW();
            """,
            [
                (p.product_id, p.product_line_id, academic_year, p.product_name,
                 p.line_name, p.description, p.price, p.active,
                 p.selling_date_start, p.selling_date_end, p.url)
                for p in products
            ],
        )
    return len(products)


@router.get("/products")
async def list_products(academic_year: str | None = None) -> list[UnionProduct]:
    """Feeds the admin dropdown for linking a product to a ticket type."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT product_id, product_line_id, academic_year, product_name, line_name,
                   description, price, active, selling_date_start, selling_date_end, url
              FROM public.union_products
             WHERE ($1::varchar IS NULL OR academic_year = $1)
             ORDER BY product_name, line_name;
            """,
            academic_year,
        )
    return [UnionProduct(**dict(r)) for r in rows]


# --------------------------------------------------------------------------- sales


@router.post("/sales/sync")
async def sync_sales(sales: list[UnionSale], academic_year: str) -> int:
    """Upsert keyed on (order_number, product_id, product_line_id).

    Quantities can change between syncs (collections, refunds), so the update
    branch refreshes them rather than ignoring the conflict.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            """
            INSERT INTO public.union_sales
                (order_number, academic_year, sale_datetime, product_id, product_line_id,
                 price, quantity, quantity_collected, customer_cid, customer_first_name,
                 customer_last_name, customer_email, customer_login, synced_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13, NOW())
            ON CONFLICT (order_number, product_id, product_line_id) DO UPDATE SET
                quantity           = EXCLUDED.quantity,
                quantity_collected = EXCLUDED.quantity_collected,
                price              = EXCLUDED.price,
                sale_datetime      = EXCLUDED.sale_datetime,
                customer_cid       = EXCLUDED.customer_cid,
                customer_email     = EXCLUDED.customer_email,
                customer_login     = EXCLUDED.customer_login,
                synced_at          = NOW();
            """,
            [
                (s.order_number, academic_year, s.sale_datetime, s.product_id,
                 s.product_line_id, s.price, s.quantity, s.quantity_collected,
                 s.customer_cid, s.customer_first_name, s.customer_last_name,
                 s.customer_email, s.customer_login)
                for s in sales
            ],
        )
    return len(sales)


@router.get("/sales")
async def list_sales(
    order_number: str | None = None,
    login: str | None = None,
    product_id: int | None = None,
    limit: int = 200,
) -> list[UnionSale]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, order_number, academic_year, sale_datetime, product_id,
                   product_line_id, price, quantity, quantity_collected, customer_cid,
                   customer_first_name, customer_last_name, customer_email, customer_login
              FROM public.union_sales
             WHERE ($1::varchar IS NULL OR upper(order_number) = upper($1))
               AND ($2::varchar IS NULL OR lower(customer_login) = lower($2))
               AND ($3::bigint  IS NULL OR product_id = $3)
             ORDER BY sale_datetime DESC NULLS LAST
             LIMIT $4;
            """,
            order_number,
            login,
            product_id,
            limit,
        )
    return [UnionSale(**dict(r)) for r in rows]


# ---------------------------------------------------------------------- sync runs


@router.post("/sync-runs", status_code=201)
async def start_sync_run(kind: str, academic_year: str | None = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO public.union_sync_runs (kind, academic_year) VALUES ($1,$2) RETURNING id;",
            kind,
            academic_year,
        )


@router.post("/sync-runs/{run_id}/finish", status_code=204)
async def finish_sync_run(
    run_id: int, ok: bool, row_count: int | None = None, message: str | None = None
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.union_sync_runs
               SET finished_at = NOW(), ok = $2, row_count = $3, message = $4
             WHERE id = $1;
            """,
            run_id, ok, row_count, message,
        )


@router.get("/sync-runs")
async def list_sync_runs(limit: int = 20) -> list[SyncRun]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, academic_year, started_at, finished_at, ok, row_count, message
              FROM public.union_sync_runs ORDER BY started_at DESC LIMIT $1;
            """,
            limit,
        )
    return [SyncRun(**dict(r)) for r in rows]


@router.get("/sync-runs/latest")
async def latest_sync_runs() -> list[SyncRun]:
    """Most recent run per kind — what the admin dashboard shows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (kind)
                   id, kind, academic_year, started_at, finished_at, ok, row_count, message
              FROM public.union_sync_runs
             ORDER BY kind, started_at DESC;
            """
        )
    return [SyncRun(**dict(r)) for r in rows]


# ------------------------------------------------------------------- site content


@router.get("/content/{key}")
async def get_content(key: str) -> SiteContent:
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT value FROM public.site_content WHERE key = $1;", key
        )
    return SiteContent(key=key, value=value or "")


@router.put("/content/{key}")
async def set_content(key: str, content: SiteContent, updated_by: int | None = None) -> SiteContent:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.site_content (key, value, updated_by)
            VALUES ($1,$2,$3)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = NOW();
            """,
            key, content.value, updated_by,
        )
    return SiteContent(key=key, value=content.value)


@router.get("/stats")
async def dashboard_stats() -> dict:
    """Everything the admin dashboard counts, in one round trip.

    A single query rather than a dozen endpoints: the dashboard wants all of it
    at once, and twelve sequential hops through the adapter would make it the
    slowest page on the site.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM public.users)                                  AS users,
              (SELECT COUNT(*) FROM public.users WHERE email_verified)             AS users_verified,
              (SELECT COUNT(*) FROM public.users WHERE member_id IS NOT NULL)      AS users_members,
              (SELECT COUNT(*) FROM public.members)                                AS members,
              (SELECT COUNT(*) FROM public.admins)                                 AS admins,
              (SELECT COUNT(*) FROM public.sessions WHERE expires_at > NOW())      AS sessions_live,
              (SELECT COUNT(*) FROM public.events)                                 AS events,
              (SELECT COUNT(*) FROM public.events WHERE published)                 AS events_published,
              (SELECT COUNT(*) FROM public.events
                WHERE published AND starts_at >= NOW())                            AS events_upcoming,
              (SELECT COUNT(*) FROM public.reservations WHERE status='held')       AS res_held,
              (SELECT COUNT(*) FROM public.reservations WHERE status='confirmed')  AS res_confirmed,
              (SELECT COUNT(*) FROM public.reservations WHERE status='expired')    AS res_expired,
              (SELECT COUNT(*) FROM public.reservations WHERE booked_as_guest
                 AND status IN ('held','confirmed'))                               AS res_guest,
              (SELECT COUNT(*) FROM public.reservations
                WHERE status='held' AND expires_at < NOW() + interval '24 hours')  AS res_expiring,
              -- Past the purchase window with nothing matched: the committee's
              -- worklist, since nothing is cancelled automatically.
              (SELECT COUNT(*) FROM public.reservations r
                 JOIN public.ticket_types t ON t.id = r.ticket_type_id
                WHERE r.status='held' AND r.expires_at < NOW()
                  AND t.requires_purchase)                                         AS res_overdue,
              (SELECT COUNT(*) FROM public.waitlist_entries
                WHERE released_at IS NULL AND claimed_at IS NULL)                  AS waitlist,
              (SELECT COUNT(*) FROM public.lockers WHERE is_active)                AS lockers,
              (SELECT COUNT(*) FROM public.locker_bookings
                WHERE released_at IS NULL)                                         AS lockers_taken,
              (SELECT COUNT(*) FROM public.sponsors WHERE active)                  AS sponsors,
              (SELECT COUNT(*) FROM public.job_listings WHERE status='live')       AS jobs_live,
              (SELECT COUNT(*) FROM public.event_sponsorships
                WHERE status='claimed')                                            AS claims_pending,
              (SELECT COUNT(*) FROM public.sponsor_requests WHERE status='open')   AS requests_open,
              (SELECT COUNT(*) FROM public.resource_pages)                         AS resource_pages,
              (SELECT COUNT(*) FROM public.resource_entries)                       AS resource_entries,
              (SELECT COUNT(*) FROM public.uploads)                                AS uploads,
              (SELECT COALESCE(SUM(byte_size),0) FROM public.uploads)              AS upload_bytes,
              (SELECT COUNT(*) FROM public.union_sales)                            AS union_sales,
              (SELECT COUNT(*) FROM public.union_products)                         AS union_products,
              (SELECT COUNT(*) FROM public.product_links)                          AS product_links;
            """
        )

        # Holds about to expire without a matching sale are what an admin most
        # needs to see, so they come back in full rather than as a count.
        attention = await conn.fetch(
            """
            SELECT r.id, r.status, r.quantity, r.expires_at, r.order_number,
                   r.booked_as_guest, u.email, e.title AS event_title,
                   e.slug AS event_slug, tt.name AS ticket_type_name
              FROM public.reservations r
              JOIN public.users u         ON u.id  = r.user_id
              JOIN public.events e        ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
             WHERE r.status = 'held' AND r.expires_at < NOW() + interval '48 hours'
             ORDER BY r.expires_at LIMIT 50;
            """
        )
        recent = await conn.fetch(
            """
            SELECT r.id, r.status, r.created_at, r.booked_as_guest, u.email,
                   e.title AS event_title, tt.name AS ticket_type_name
              FROM public.reservations r
              JOIN public.users u         ON u.id  = r.user_id
              JOIN public.events e        ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
             ORDER BY r.created_at DESC LIMIT 15;
            """
        )
    return {
        "counts": dict(row),
        "needsAttention": [dict(r) for r in attention],
        "recentBookings": [dict(r) for r in recent],
    }
