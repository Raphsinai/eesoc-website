"""Lockers and locker bookings.

Lockers are members-only and require a locker purchase on the Union shop. A
member may buy more than one, so entitlement is a count, not a flag.
"""

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.lockers import (
    Locker,
    LockerBooking,
    LockerBookingDetail,
    LockerBookingRequest,
    LockerBookingResult,
    LockerState,
)
from postgres import get_pool

router = APIRouter(prefix="/lockers")

BOOKING_COLUMNS = """
    id, locker_id, user_id, academic_year, order_number, matched_sale_id,
    created_at, released_at
"""


@router.get("")
async def list_lockers(user_id: int | None = None) -> list[LockerState]:
    """The bank map. Other people's bookings show as `taken` without revealing
    who holds them; the caller's own show as `mine`."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT l.id, l.bank, l.number, l.floor, l.is_active, l.note,
                   b.id AS booking_id, b.user_id AS booked_by
              FROM public.lockers l
              LEFT JOIN public.locker_bookings b
                     ON b.locker_id = l.id AND b.released_at IS NULL
             ORDER BY l.bank, l.number;
            """
        )

    out: list[LockerState] = []
    for r in rows:
        d = dict(r)
        booked_by = d["booked_by"]
        mine = user_id is not None and booked_by == user_id
        if not d["is_active"]:
            state = "inactive"
        elif booked_by is None:
            state = "free"
        else:
            state = "mine" if mine else "taken"
        # Never leak the holder of someone else's locker to the client.
        if not mine:
            d["booked_by"] = None
        out.append(LockerState(**d, mine=mine, state=state))
    return out


@router.get("/entitlement")
async def entitlement(user_id: int, academic_year: str) -> dict:
    """How many lockers this user has paid for, and how many they already hold.

    Purchases are counted from the synced sales feed against whichever products
    an admin has linked with purpose='locker'.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        purchased = await conn.fetchval(
            """
            SELECT COALESCE(SUM(s.quantity), 0)
              FROM public.union_sales s
              JOIN public.product_links pl
                ON pl.purpose = 'locker'
               AND pl.union_product_id = s.product_id
               AND (pl.union_product_line_id IS NULL
                    OR pl.union_product_line_id = s.product_line_id)
              JOIN public.users u ON u.id = $1
              LEFT JOIN public.members m ON m.id = u.member_id
             WHERE s.academic_year = $2
               AND (
                     (m.login IS NOT NULL AND lower(s.customer_login) = lower(m.login))
                  OR (m.cid   IS NOT NULL AND s.customer_cid = m.cid)
                   );
            """,
            user_id,
            academic_year,
        )
        # Free allocations, e.g. for committee. Matched on the shortcode, which
        # is the stable handle for an Imperial person and is set whether or not
        # they appear in the members feed.
        granted = await conn.fetchval(
            """
            SELECT COALESCE(SUM(g.free_count), 0)
              FROM public.locker_grants g
              JOIN public.users u ON lower(u.shortcode) = lower(g.shortcode)
             WHERE u.id = $1 AND g.academic_year = $2;
            """,
            user_id,
            academic_year,
        )
        held = await conn.fetchval(
            "SELECT COUNT(*) FROM public.locker_bookings "
            "WHERE user_id = $1 AND released_at IS NULL;",
            user_id,
        )
        is_member = await conn.fetchval(
            "SELECT member_id IS NOT NULL FROM public.users WHERE id = $1;", user_id
        )
    entitled = int(purchased or 0) + int(granted or 0)
    return {
        "purchased": int(purchased or 0),
        "granted": int(granted or 0),
        "entitled": entitled,
        "held": int(held or 0),
        "remaining": max(entitled - int(held or 0), 0),
        "is_member": bool(is_member),
    }


@router.post("/book")
async def book_locker(req: LockerBookingRequest) -> LockerBookingResult:
    """Claim a locker.

    Two people clicking the same locker at the same moment is resolved by the
    partial unique index on (locker_id) WHERE released_at IS NULL — the loser
    gets a unique violation rather than a second booking. Checking first and then
    inserting would leave a window between the two.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        user = await conn.fetchrow(
            "SELECT id, member_id FROM public.users WHERE id = $1;", req.user_id
        )
        if user is None:
            raise HTTPException(404, "No such user")
        if user["member_id"] is None:
            return LockerBookingResult(ok=False, reason="not_member")

        locker = await conn.fetchrow(
            "SELECT id, is_active FROM public.lockers WHERE id = $1;", req.locker_id
        )
        if locker is None:
            raise HTTPException(404, "No such locker")
        if not locker["is_active"]:
            return LockerBookingResult(ok=False, reason="inactive")

        ent = await entitlement(req.user_id, req.academic_year)
        if ent["remaining"] < 1:
            return LockerBookingResult(ok=False, reason="no_entitlement")

        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.locker_bookings
                    (locker_id, user_id, academic_year, order_number, matched_sale_id)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING {BOOKING_COLUMNS};
                """,
                req.locker_id,
                req.user_id,
                req.academic_year,
                req.order_number,
                req.matched_sale_id,
            )
        except UniqueViolationError:
            return LockerBookingResult(ok=False, reason="taken")
    return LockerBookingResult(ok=True, booking=LockerBooking(**dict(row)))


@router.get("/bookings")
async def list_bookings(
    user_id: int | None = None, active_only: bool = True
) -> list[LockerBookingDetail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.id, b.locker_id, b.user_id, b.academic_year, b.order_number,
                   b.matched_sale_id, b.created_at, b.released_at,
                   l.bank, l.number
              FROM public.locker_bookings b
              JOIN public.lockers l ON l.id = b.locker_id
             WHERE ($1::bigint IS NULL OR b.user_id = $1)
               AND ($2::bool IS NOT TRUE OR b.released_at IS NULL)
             ORDER BY b.created_at DESC;
            """,
            user_id,
            active_only,
        )
    return [LockerBookingDetail(**dict(r)) for r in rows]


@router.post("/bookings/{booking_id}/release")
async def release_booking(booking_id: int) -> LockerBooking:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.locker_bookings SET released_at = NOW()
             WHERE id = $1 AND released_at IS NULL
            RETURNING {BOOKING_COLUMNS};
            """,
            booking_id,
        )
    if row is None:
        raise HTTPException(404, "No such active booking")
    return LockerBooking(**dict(row))


@router.post("", status_code=201)
async def create_locker(locker: Locker) -> Locker:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.lockers (bank, number, floor, is_active, note)
                VALUES ($1,$2,$3,$4,$5)
                RETURNING id, bank, number, floor, is_active, note;
                """,
                locker.bank, locker.number, locker.floor, locker.is_active, locker.note,
            )
        except UniqueViolationError:
            raise HTTPException(409, f"Locker {locker.bank}-{locker.number} already exists")
    return Locker(**dict(row))


@router.patch("/{locker_id}")
async def update_locker(
    locker_id: int, is_active: bool | None = None, note: str | None = None
) -> Locker:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.lockers
               SET is_active = COALESCE($2, is_active), note = COALESCE($3, note)
             WHERE id = $1
            RETURNING id, bank, number, floor, is_active, note;
            """,
            locker_id, is_active, note,
        )
    if row is None:
        raise HTTPException(404, "No such locker")
    return Locker(**dict(row))


# ------------------------------------------------------------------- grants


@router.get("/grants")
async def list_grants(academic_year: str | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT g.shortcode, g.academic_year, g.free_count, g.note, g.granted_at,
                   u.email, u.first_name, u.last_name
              FROM public.locker_grants g
              LEFT JOIN public.users u ON lower(u.shortcode) = lower(g.shortcode)
             WHERE ($1::varchar IS NULL OR g.academic_year = $1)
             ORDER BY g.shortcode;
            """,
            academic_year,
        )
    return [dict(r) for r in rows]


@router.put("/grants/{shortcode}")
async def upsert_grant(
    shortcode: str,
    academic_year: str,
    free_count: int = 1,
    note: str | None = None,
    granted_by: int | None = None,
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.locker_grants
                (shortcode, academic_year, free_count, note, granted_by)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (shortcode, academic_year) DO UPDATE SET
                free_count = EXCLUDED.free_count,
                note       = EXCLUDED.note,
                granted_by = EXCLUDED.granted_by,
                granted_at = NOW()
            RETURNING shortcode, academic_year, free_count, note, granted_at;
            """,
            shortcode.strip().lower(), academic_year, free_count, note, granted_by,
        )
    return dict(row)


@router.delete("/grants/{shortcode}", status_code=204)
async def delete_grant(shortcode: str, academic_year: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.locker_grants WHERE lower(shortcode)=lower($1) "
            "AND academic_year=$2;",
            shortcode, academic_year,
        )


# --------------------------------------------------------------- year rollover


@router.get("/holders")
async def current_holders() -> list[dict]:
    """Everyone holding a locker, for the end-of-year email."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.id, b.academic_year, b.created_at, l.bank, l.number,
                   u.id AS user_id, u.email, u.first_name, u.last_name
              FROM public.locker_bookings b
              JOIN public.lockers l ON l.id = b.locker_id
              JOIN public.users u   ON u.id = b.user_id
             WHERE b.released_at IS NULL
             ORDER BY l.bank, l.number;
            """
        )
    return [dict(r) for r in rows]


@router.post("/release-year")
async def release_year(academic_year: str) -> dict:
    """Release every live booking from a given year.

    Takes the year explicitly rather than assuming "everything before now": an
    accidental call must not wipe the bookings of the year that has just started.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE public.locker_bookings SET released_at = NOW() "
            "WHERE released_at IS NULL AND academic_year = $1;",
            academic_year,
        )
    return {"released": int(result.split()[-1]), "academic_year": academic_year}
