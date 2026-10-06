"""Events, ticket types, product links and reservations.

The interesting part is `reserve()`. Everything else is CRUD.
"""

from datetime import datetime, timezone

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.events import (
    Event,
    EventDetail,
    EventWrite,
    ProductLink,
    Reservation,
    ReservationDetail,
    ReservationRequest,
    ReservationResult,
    TicketType,
    TicketTypeAvailability,
    WaitlistDetail,
    WaitlistEntry,
)
from postgres import get_pool

router = APIRouter()

EVENT_COLUMNS = """
    id, slug, title, category, description, image_url, venue, starts_at, ends_at,
    capacity, hold_hours, published, sponsorship_claimable,
    requested_by_sponsor_id, academic_year, created_by, created_at, updated_at,
    sales_open_at, sales_announced_at, purchase_url, industry, signup_url
"""

TT_COLUMNS = """
    id, event_id, name, description, price_pence, capacity, requires_purchase,
    members_only, max_per_user, sort_order, purchase_url
"""

# A reservation consumes stock while it is confirmed, or held/draft and not yet
# expired. `draft` is the 15-minute hold a guest gets while filling in the form:
# it must count against capacity, or two guests could fill the same last seat.
LIVE = "(status = 'confirmed' OR (status IN ('held','draft') AND expires_at > NOW()))"

# A hold on a ticket with no Union product linked can never be confirmed by
# the matcher, so its purchase window would lapse with nobody able to act on
# it — and an expired hold stops counting against capacity, which oversells.
# Those holds last until the event starts instead, and the committee checks
# them by hand. Event times are London wall-clock (migration 031), hence the
# AT TIME ZONE at the call sites. Same link rules as the matcher: the ticket
# type's own link, or
# an event-level one.
HOLD_EXPIRES = """
    CASE WHEN EXISTS (
            SELECT 1 FROM public.product_links pl
             WHERE pl.purpose = 'event_ticket'
               AND (pl.ticket_type_id = {tt}
                    OR (pl.ticket_type_id IS NULL AND pl.event_id = {ev})))
         THEN NOW() + make_interval(hours => {hours})
         ELSE GREATEST({starts}, NOW() + make_interval(hours => {hours}))
    END
"""

WAITLIST_COLUMNS = """
    id, event_id, user_id, preferred_ticket_type_id, quantity, created_at,
    offered_at, offer_expires_at, reservation_id, claimed_at, released_at,
    notified_at
"""

RESERVATION_COLUMNS = """
    id, event_id, ticket_type_id, user_id, quantity, status, created_at,
    expires_at, confirmed_at, cancelled_at, order_number, matched_sale_id, note,
    booked_as_guest, reminder_sent_at
"""


async def _availability(conn, event_id: int) -> tuple[list[TicketTypeAvailability], int]:
    """Ticket types for an event with live demand folded in, plus the event total.

    Computed in the database rather than by summing in Python so the number the
    UI shows comes from the same source as the number `reserve()` enforces.
    """
    rows = await conn.fetch(
        f"""
        SELECT {TT_COLUMNS}, COALESCE(live.taken, 0) AS taken
          FROM public.ticket_types tt
          LEFT JOIN (
                SELECT ticket_type_id, SUM(quantity) AS taken
                  FROM public.reservations
                 WHERE event_id = $1 AND {LIVE}
                 GROUP BY ticket_type_id
               ) live ON live.ticket_type_id = tt.id
         WHERE tt.event_id = $1
         ORDER BY tt.sort_order, tt.id;
        """,
        event_id,
    )
    event_taken = await conn.fetchval(
        f"SELECT COALESCE(SUM(quantity), 0) FROM public.reservations WHERE event_id = $1 AND {LIVE};",
        event_id,
    )

    types: list[TicketTypeAvailability] = []
    for r in rows:
        d = dict(r)
        taken = d.pop("taken")
        tt = TicketTypeAvailability(**d, taken=taken)
        if tt.capacity is not None:
            tt.remaining = max(tt.capacity - taken, 0)
            tt.sold_out = tt.remaining == 0
        types.append(tt)
    return types, int(event_taken)


async def _detail(conn, row) -> EventDetail:
    types, taken = await _availability(conn, row["id"])
    detail = EventDetail(**dict(row), ticket_types=types, taken=taken)
    if detail.capacity is not None:
        detail.remaining = max(detail.capacity - taken, 0)
        detail.sold_out = detail.remaining == 0
    # An event with an event-wide seat left is still sold out if every type is.
    if types and all(t.sold_out for t in types):
        detail.sold_out = True
    return detail


# -------------------------------------------------------------------------- events


@router.get("/events")
async def list_events(
    published_only: bool = True,
    upcoming: bool = False,
    academic_year: str | None = None,
    limit: int = 100,
) -> list[EventDetail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {EVENT_COLUMNS} FROM public.events
             WHERE ($1::bool IS NOT TRUE OR published)
               AND ($2::bool IS NOT TRUE OR starts_at >= (NOW() AT TIME ZONE 'Europe/London'))
               AND ($3::varchar IS NULL OR academic_year = $3)
             ORDER BY starts_at
             LIMIT $4;
            """,
            published_only,
            upcoming,
            academic_year,
            limit,
        )
        return [await _detail(conn, r) for r in rows]


@router.get("/events/by-slug/{slug}")
async def get_event_by_slug(slug: str) -> EventDetail:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {EVENT_COLUMNS} FROM public.events WHERE slug = $1;", slug
        )
        if row is None:
            raise HTTPException(404, "No such event")

        return await _detail(conn, row)


@router.get("/events/due-announcement")
async def events_due_announcement() -> list[EventDetail]:
    """Published events whose sale time has arrived and which nobody has been
    told about yet.

    Ordered oldest-first so a backlog (the process was down over the window) is
    worked in the order the events actually opened, not newest first.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {EVENT_COLUMNS} FROM public.events
             WHERE published
               AND sales_announced_at IS NULL
               AND sales_open_at IS NOT NULL
               AND sales_open_at <= (NOW() AT TIME ZONE 'Europe/London')
             ORDER BY sales_open_at;
            """
        )
        return [await _detail(conn, row) for row in rows]


@router.post("/events/{event_id}/announced")
async def mark_event_announced(event_id: int) -> dict:
    """Claim an event for announcement.

    Returns whether THIS call was the one that claimed it. The update is
    conditional on the column still being null, so two workers racing cannot
    both decide they are the ones sending — which is the difference between one
    announcement and two.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.events SET sales_announced_at = NOW()
             WHERE id = $1 AND sales_announced_at IS NULL
            RETURNING id;
            """,
            event_id,
        )
    return {"claimed": row is not None}


@router.get("/events/{event_id}")
async def get_event(event_id: int) -> EventDetail:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {EVENT_COLUMNS} FROM public.events WHERE id = $1;", event_id
        )
        if row is None:
            raise HTTPException(404, "No such event")
        return await _detail(conn, row)


@router.post("/events", status_code=201)
async def create_event(event: EventWrite, created_by: int | None = None) -> EventDetail:
    """Event and its ticket types are created together, in one transaction: an
    event with no way to book it is not a useful intermediate state."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.events
                    (slug, title, category, description, image_url, venue, starts_at,
                     ends_at, capacity, hold_hours, published, sponsorship_claimable,
                     requested_by_sponsor_id, academic_year, created_by, sales_open_at,
                     purchase_url, industry, signup_url)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
                RETURNING {EVENT_COLUMNS};
                """,
                event.slug, event.title, event.category, event.description,
                event.image_url, event.venue, event.starts_at, event.ends_at,
                event.capacity, event.hold_hours, event.published,
                event.sponsorship_claimable, event.requested_by_sponsor_id,
                event.academic_year or "", created_by, event.sales_open_at,
                event.purchase_url, event.industry, event.signup_url,
            )
        except UniqueViolationError:
            raise HTTPException(409, f"An event with slug '{event.slug}' already exists")

        for tt in event.ticket_types:
            await conn.execute(
                """
                INSERT INTO public.ticket_types
                    (event_id, name, description, price_pence, capacity,
                     requires_purchase, members_only, max_per_user, sort_order,
                     purchase_url)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10);
                """,
                row["id"], tt.name, tt.description, tt.price_pence, tt.capacity,
                tt.requires_purchase, tt.members_only, tt.max_per_user, tt.sort_order,
                tt.purchase_url,
            )
        return await _detail(conn, row)


@router.put("/events/{event_id}")
async def update_event(event_id: int, event: EventWrite) -> EventDetail:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            UPDATE public.events SET
                slug=$2, title=$3, category=$4, description=$5, image_url=$6,
                venue=$7, starts_at=$8, ends_at=$9, capacity=$10, hold_hours=$11,
                published=$12, sponsorship_claimable=$13,
                requested_by_sponsor_id=$14,
                academic_year=COALESCE($15, academic_year),
                sales_open_at=$16,
                purchase_url=$17,
                industry=$18,
                signup_url=$19,
                updated_at=NOW()
             WHERE id=$1
            RETURNING {EVENT_COLUMNS};
            """,
            event_id, event.slug, event.title, event.category, event.description,
            event.image_url, event.venue, event.starts_at, event.ends_at,
            event.capacity, event.hold_hours, event.published,
            event.sponsorship_claimable, event.requested_by_sponsor_id,
            event.academic_year, event.sales_open_at, event.purchase_url,
            event.industry, event.signup_url,
        )
        if row is None:
            raise HTTPException(404, "No such event")
        # Ticket types travel with the event, as they do on create. Without
        # this the handler updated the events row and returned, so every edit
        # to a price, cap or name made in the event form was silently thrown
        # away — the save reported success and nothing changed.
        existing = {
            r["id"]: r
            for r in await conn.fetch(
                "SELECT id FROM public.ticket_types WHERE event_id = $1;", event_id
            )
        }
        seen: set[int] = set()

        for tt in event.ticket_types:
            if tt.id in existing:
                seen.add(tt.id)
                await conn.execute(
                    """
                    UPDATE public.ticket_types SET
                        name=$2, description=$3, price_pence=$4, capacity=$5,
                        requires_purchase=$6, members_only=$7, max_per_user=$8,
                        sort_order=$9, purchase_url=$10
                     WHERE id=$1;
                    """,
                    tt.id, tt.name, tt.description, tt.price_pence, tt.capacity,
                    tt.requires_purchase, tt.members_only, tt.max_per_user,
                    tt.sort_order, tt.purchase_url,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO public.ticket_types
                        (event_id, name, description, price_pence, capacity,
                         requires_purchase, members_only, max_per_user, sort_order,
                         purchase_url)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10);
                    """,
                    event_id, tt.name, tt.description, tt.price_pence, tt.capacity,
                    tt.requires_purchase, tt.members_only, tt.max_per_user,
                    tt.sort_order, tt.purchase_url,
                )

        # Anything the form no longer lists has been removed deliberately —
        # but not if somebody holds a ticket of that type. Refusing the whole
        # save is the honest answer: dropping the type would orphan their
        # booking, and quietly keeping it is how the bug above went unnoticed.
        for removed in set(existing) - seen:
            held = await conn.fetchval(
                """
                SELECT count(*) FROM public.reservations
                 WHERE ticket_type_id = $1 AND status <> 'cancelled';
                """,
                removed,
            )
            if held:
                name = await conn.fetchval(
                    "SELECT name FROM public.ticket_types WHERE id = $1;", removed
                )
                raise HTTPException(
                    409,
                    f"{held} booking(s) exist for ticket type '{name}'. "
                    "Cancel them before removing it.",
                )
            await conn.execute(
                "DELETE FROM public.ticket_types WHERE id = $1;", removed
            )

        return await _detail(conn, row)


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.events WHERE id = $1;", event_id)


# ------------------------------------------------------------------- ticket types


@router.post("/events/{event_id}/ticket-types", status_code=201)
async def add_ticket_type(event_id: int, tt: TicketType) -> TicketType:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.ticket_types
                    (event_id, name, description, price_pence, capacity,
                     requires_purchase, members_only, max_per_user, sort_order,
                     purchase_url)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING {TT_COLUMNS};
                """,
                event_id, tt.name, tt.description, tt.price_pence, tt.capacity,
                tt.requires_purchase, tt.members_only, tt.max_per_user, tt.sort_order,
                tt.purchase_url,
            )
        except UniqueViolationError:
            raise HTTPException(409, f"'{tt.name}' already exists on this event")
    return TicketType(**dict(row))


@router.get("/ticket-types/{ticket_type_id}")
async def get_ticket_type(ticket_type_id: int) -> TicketType:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {TT_COLUMNS} FROM public.ticket_types WHERE id = $1;", ticket_type_id
        )
    if row is None:
        raise HTTPException(404, "No such ticket type")
    return TicketType(**dict(row))


@router.put("/ticket-types/{ticket_type_id}")
async def update_ticket_type(ticket_type_id: int, tt: TicketType) -> TicketType:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.ticket_types SET
                name=$2, description=$3, price_pence=$4, capacity=$5,
                requires_purchase=$6, members_only=$7, max_per_user=$8, sort_order=$9,
                purchase_url=$10
             WHERE id=$1
            RETURNING {TT_COLUMNS};
            """,
            ticket_type_id, tt.name, tt.description, tt.price_pence, tt.capacity,
            tt.requires_purchase, tt.members_only, tt.max_per_user, tt.sort_order,
            tt.purchase_url,
        )
    if row is None:
        raise HTTPException(404, "No such ticket type")
    return TicketType(**dict(row))


@router.delete("/ticket-types/{ticket_type_id}", status_code=204)
async def delete_ticket_type(ticket_type_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.ticket_types WHERE id = $1;", ticket_type_id
        )


# --------------------------------------------------------------------- reservations


@router.post("/reservations")
async def reserve(req: ReservationRequest) -> ReservationResult:
    """Reserve tickets without overselling.

    The capacity check and the insert must not interleave with a competing
    request. Locking the event row is what makes that true, and it has to be its
    OWN statement: under READ COMMITTED each statement takes a fresh snapshot, so
    a request that waited on the lock only sees the winner's committed row if it
    counts afterwards. Doing the lock and the count in one statement would reuse
    the pre-lock snapshot and oversell — measured at 22 sold against a capacity of
    10 with 40 concurrent requests.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # 1. Serialise every reservation attempt for this event.
        event = await conn.fetchrow(
            "SELECT id, capacity, hold_hours, published, starts_at "
            "FROM public.events WHERE id = $1 FOR UPDATE;",
            req.event_id,
        )
        if event is None:
            raise HTTPException(404, "No such event")

        tt = await conn.fetchrow(
            f"SELECT {TT_COLUMNS} FROM public.ticket_types WHERE id = $1 AND event_id = $2;",
            req.ticket_type_id,
            req.event_id,
        )
        if tt is None:
            raise HTTPException(404, "No such ticket type for this event")

        # 2. Fresh snapshot — includes anything committed while we waited above.
        counts = await conn.fetchrow(
            f"""
            SELECT COALESCE(SUM(quantity), 0)                                        AS event_taken,
                   COALESCE(SUM(quantity) FILTER (WHERE ticket_type_id = $2), 0)     AS type_taken,
                   COALESCE(SUM(quantity) FILTER (WHERE user_id = $3
                                              AND ticket_type_id = $2), 0)           AS user_taken
              FROM public.reservations
             WHERE event_id = $1 AND {LIVE};
            """,
            req.event_id,
            req.ticket_type_id,
            req.user_id,
        )

        if tt["capacity"] is not None and counts["type_taken"] + req.quantity > tt["capacity"]:
            return ReservationResult(ok=False, reason="type_sold_out")
        if event["capacity"] is not None and counts["event_taken"] + req.quantity > event["capacity"]:
            return ReservationResult(ok=False, reason="sold_out")
        if counts["user_taken"] + req.quantity > tt["max_per_user"]:
            return ReservationResult(ok=False, reason="over_user_limit")

        row = await conn.fetchrow(
            f"""
            INSERT INTO public.reservations
                (event_id, ticket_type_id, user_id, quantity, expires_at,
                 order_number, booked_as_guest)
            VALUES ($1, $2, $3, $4,
                    {HOLD_EXPIRES.format(tt="$2", ev="$1", hours="$5", starts="($8::timestamp AT TIME ZONE 'Europe/London')")},
                    $6, $7)
            RETURNING id, event_id, ticket_type_id, user_id, quantity, status,
                      created_at, expires_at, confirmed_at, cancelled_at,
                      order_number, matched_sale_id, note, booked_as_guest;
            """,
            req.event_id,
            req.ticket_type_id,
            req.user_id,
            req.quantity,
            req.hold_hours_override or event["hold_hours"],
            req.order_number,
            req.booked_as_guest,
            event["starts_at"],
        )
    return ReservationResult(ok=True, reservation=Reservation(**dict(row)))


@router.get("/reservations")
async def list_reservations(
    user_id: int | None = None,
    event_id: int | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[ReservationDetail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.user_id, r.quantity, r.status,
                   r.created_at, r.expires_at, r.confirmed_at, r.cancelled_at,
                   r.order_number, r.matched_sale_id, r.note, r.booked_as_guest,
                   e.title AS event_title, e.slug AS event_slug,
                   e.starts_at AS event_starts_at,
                   tt.name AS ticket_type_name, tt.price_pence, tt.requires_purchase,
                   u.email AS user_email
              FROM public.reservations r
              JOIN public.events e       ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
              JOIN public.users u        ON u.id  = r.user_id
             WHERE ($1::bigint  IS NULL OR r.user_id  = $1)
               AND ($2::bigint  IS NULL OR r.event_id = $2)
               AND ($3::varchar IS NULL OR r.status   = $3)
             ORDER BY r.created_at DESC
             LIMIT $4;
            """,
            user_id,
            event_id,
            status,
            limit,
        )
    return [ReservationDetail(**dict(r)) for r in rows]


@router.get("/reservations/overdue")
async def overdue_reservations() -> list[ReservationDetail]:
    """Holds past their purchase window with no matching sale.

    The committee's worklist: chase, cancel, or confirm by hand.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.user_id, r.quantity, r.status,
                   r.created_at, r.expires_at, r.confirmed_at, r.cancelled_at,
                   r.order_number, r.matched_sale_id, r.note, r.booked_as_guest,
                   e.title AS event_title, e.slug AS event_slug,
                   e.starts_at AS event_starts_at,
                   tt.name AS ticket_type_name, tt.price_pence, tt.requires_purchase,
                   tt.members_only,
                   u.email AS user_email,
                   u.member_id IS NOT NULL AS has_member_record
              FROM public.reservations r
              JOIN public.events e        ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
              JOIN public.users u         ON u.id  = r.user_id
             WHERE r.status = 'held'
               AND r.expires_at < NOW()
               AND tt.requires_purchase
             ORDER BY r.expires_at;
            """
        )
    return [ReservationDetail(**dict(r)) for r in rows]


@router.get("/reservations/needing-reminder")
async def reservations_needing_reminder(within_hours: int = 24) -> list[ReservationDetail]:
    """Unpaid holds about to run out, which have not been chased yet.

    `requires_purchase` is the filter that matters: a free ticket has nothing to
    pay for, and chasing somebody for money they do not owe is the kind of email
    that makes people stop reading all of them.

    Deliberately excludes anything already past `expires_at` — that is the
    overdue list, which a human works through. This is the last chance BEFORE
    the deadline, which is the only point at which it can still be acted on.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.user_id, r.quantity,
                   r.status, r.created_at, r.expires_at, r.confirmed_at,
                   r.cancelled_at, r.order_number, r.matched_sale_id, r.note,
                   r.booked_as_guest, r.reminder_sent_at,
                   e.title AS event_title, e.slug AS event_slug,
                   e.starts_at AS event_starts_at,
                   t.name AS ticket_type_name, t.price_pence, t.requires_purchase,
                   t.members_only, u.email AS user_email,
                   (m.id IS NOT NULL) AS has_member_record
              FROM public.reservations r
              JOIN public.events e       ON e.id = r.event_id
              JOIN public.ticket_types t ON t.id = r.ticket_type_id
              JOIN public.users u        ON u.id = r.user_id
              LEFT JOIN public.members m ON m.id = u.member_id
             WHERE r.status = 'held'
               AND r.reminder_sent_at IS NULL
               AND t.requires_purchase
               AND r.expires_at > NOW()
               AND r.expires_at <= NOW() + make_interval(hours => $1)
             ORDER BY r.expires_at;
            """,
            within_hours,
        )
    return [ReservationDetail(**dict(r)) for r in rows]


@router.post("/reservations/{reservation_id}/reminded")
async def mark_reminded(reservation_id: int) -> dict:
    """Claim a reservation for its one automated chase.

    Conditional, like the event announcement, so a retry or a second worker
    cannot send the same person the same warning twice.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.reservations SET reminder_sent_at = NOW()
             WHERE id = $1 AND reminder_sent_at IS NULL
            RETURNING id;
            """,
            reservation_id,
        )
    return {"claimed": row is not None}


@router.get("/reservations/{reservation_id}")
async def get_reservation(reservation_id: int) -> Reservation:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, event_id, ticket_type_id, user_id, quantity, status, created_at,
                   expires_at, confirmed_at, cancelled_at, order_number,
                   matched_sale_id, note, booked_as_guest
              FROM public.reservations WHERE id = $1;
            """,
            reservation_id,
        )
    if row is None:
        raise HTTPException(404, "No such reservation")
    return Reservation(**dict(row))


@router.post("/reservations/{reservation_id}/cancel")
async def cancel_reservation(reservation_id: int) -> Reservation:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.reservations
               SET status = 'cancelled', cancelled_at = NOW()
             WHERE id = $1 AND status IN ('held', 'confirmed')
            RETURNING id, event_id, ticket_type_id, user_id, quantity, status,
                      created_at, expires_at, confirmed_at, cancelled_at,
                      order_number, matched_sale_id, note, booked_as_guest;
            """,
            reservation_id,
        )
    if row is None:
        raise HTTPException(404, "No such reservation, or it is already closed")
    return Reservation(**dict(row))


@router.patch("/reservations/{reservation_id}/order-number")
async def set_order_number(reservation_id: int, order_number: str) -> Reservation:
    """External users type their Union order number in after paying."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.reservations SET order_number = $2
             WHERE id = $1 AND status = 'held'
            RETURNING id, event_id, ticket_type_id, user_id, quantity, status,
                      created_at, expires_at, confirmed_at, cancelled_at,
                      order_number, matched_sale_id, note, booked_as_guest;
            """,
            reservation_id,
            order_number.strip(),
        )
    if row is None:
        raise HTTPException(404, "No such held reservation")
    return Reservation(**dict(row))


@router.post("/reservations/expire")
async def expire_reservations() -> int:
    """Expire abandoned *drafts* only.

    Held reservations are deliberately left alone even once their purchase
    window has passed. Cancelling somebody's ticket automatically is not
    recoverable if they did pay and the sale simply has not synced, so an
    overdue hold is surfaced to the committee instead — see
    `GET /reservations/overdue`. A draft is different: nobody owns it, and it
    only exists to stop an oversell while a form is being filled in.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            # DELETE, not status='expired': reservations_owner_check only lets
            # a row without a user be a draft, so expiring one raised and the
            # whole sweep (and the sync that calls it) failed. A draft has no
            # owner and nothing worth keeping.
            "DELETE FROM public.reservations "
            "WHERE status = 'draft' AND expires_at < NOW();"
        )
    return int(result.split()[-1])


@router.post("/reservations/{reservation_id}/confirm")
async def confirm_manually(reservation_id: int, order_number: str | None = None) -> Reservation:
    """Confirm a booking by hand.

    For the case the automatic matcher cannot cover: a purchase made under a
    different account, paid in cash, or comped. The order number is recorded
    when there is one so the decision is auditable, but it is not verified —
    that is the point of a manual override.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.reservations
               SET status = 'confirmed',
                   confirmed_at = NOW(),
                   order_number = COALESCE($2, order_number),
                   note = COALESCE(note || ' | ', '') || 'confirmed manually'
             WHERE id = $1 AND status IN ('held', 'expired')
            RETURNING {RESERVATION_COLUMNS};
            """,
            reservation_id,
            (order_number or "").strip() or None,
        )
    if row is None:
        raise HTTPException(404, "No such reservation, or it is already confirmed")
    return Reservation(**dict(row))


@router.post("/reservations/match")
async def match_reservations(academic_year: str | None = None) -> dict:
    """Promote holds to confirmed where a matching Union sale has appeared.

    Members are matched on their ICU login or CID, which is present on the sale.
    External users have no ICU identity in the feed, so they are matched on the
    order number they typed in.

    Candidates are applied one at a time rather than in a single UPDATE because
    one sale may only pay for one reservation — the partial unique index on
    matched_sale_id is the guard, and applying row by row lets a losing candidate
    fall through to the next sale instead of failing the whole batch.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        candidates = await conn.fetch(
            """
            WITH links AS (
                SELECT ticket_type_id, event_id, union_product_id, union_product_line_id
                  FROM public.product_links
                 WHERE purpose = 'event_ticket'
            ),
            held AS (
                SELECT r.id, r.event_id, r.ticket_type_id, r.order_number,
                       u.member_id, m.login, m.cid,
                       tt.members_only,
                       -- Membership is annual, so holding a member record is not
                       -- the same as being a member this year.
                       (u.member_id IS NOT NULL
                        AND ($1::varchar IS NULL OR m.academic_year = $1)) AS is_member
                  FROM public.reservations r
                  JOIN public.users u        ON u.id  = r.user_id
                  JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
                  LEFT JOIN public.members m ON m.id  = u.member_id
                 WHERE r.status = 'held' AND tt.requires_purchase
            ),
            -- Resolve ONE product/line per reservation, most specific first.
            -- The old version OR-ed the two link levels together, which let a
            -- sale on the "with alcohol" line confirm a "no alcohol" booking as
            -- soon as an event-level link also existed.
            resolved AS (
                SELECT h.*,
                       COALESCE(tl.union_product_id, el.union_product_id) AS product_id,
                       -- A line assigned to this ticket type wins; NULL means
                       -- "any line of the event's product".
                       COALESCE(tl.union_product_line_id, el.union_product_line_id)
                           AS line_id
                  FROM held h
                  LEFT JOIN links tl ON tl.ticket_type_id = h.ticket_type_id
                  LEFT JOIN links el ON el.ticket_type_id IS NULL
                                    AND el.event_id = h.event_id
            )
            SELECT h.id AS reservation_id, s.id AS sale_id
              FROM resolved h
              JOIN public.union_sales s
                ON s.product_id = h.product_id
               AND (h.line_id IS NULL OR s.product_line_id = h.line_id)
             WHERE h.product_id IS NOT NULL
               -- An unassigned ticket type accepts any line of the product, but
               -- must not swallow a line that belongs to a sibling type.
               AND (
                     h.line_id IS NOT NULL
                     OR NOT EXISTS (
                          SELECT 1 FROM links sib
                           WHERE sib.ticket_type_id IS NOT NULL
                             AND sib.ticket_type_id <> h.ticket_type_id
                             AND sib.union_product_id = s.product_id
                             AND sib.union_product_line_id = s.product_line_id
                        )
                   )
                   -- A members-only ticket is held, not confirmed, until the feed
                   -- shows the person is a member this year. Somebody who joined
                   -- today only appears after the morning sync, so this resolves
                   -- itself rather than needing them to book again.
               AND (NOT h.members_only OR h.is_member)
               AND (
                     (h.member_id IS NOT NULL AND h.login IS NOT NULL
                      AND lower(s.customer_login) = lower(h.login))
                  OR (h.member_id IS NOT NULL AND h.cid IS NOT NULL
                      AND s.customer_cid = h.cid)
                  OR (h.order_number IS NOT NULL
                      AND upper(s.order_number) = upper(h.order_number))
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM public.reservations r2 WHERE r2.matched_sale_id = s.id
                   )
             ORDER BY h.id, s.sale_datetime NULLS LAST;
            """,
            academic_year,
        )

        confirmed, used_sales, done = 0, set(), set()
        for c in candidates:
            if c["sale_id"] in used_sales or c["reservation_id"] in done:
                continue
            try:
                updated = await conn.fetchval(
                    """
                    UPDATE public.reservations
                       SET status = 'confirmed', confirmed_at = NOW(), matched_sale_id = $2
                     WHERE id = $1 AND status = 'held'
                    RETURNING id;
                    """,
                    c["reservation_id"],
                    c["sale_id"],
                )
            except UniqueViolationError:
                continue  # another reservation claimed this sale first
            if updated is not None:
                confirmed += 1
                used_sales.add(c["sale_id"])
                done.add(c["reservation_id"])

    return {"confirmed": confirmed, "candidates": len(candidates)}


# -------------------------------------------------------------------- product links


@router.get("/product-links")
async def list_product_links(
    purpose: str | None = None, event_id: int | None = None
) -> list[ProductLink]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, purpose, event_id, ticket_type_id, union_product_id,
                   union_product_line_id, academic_year, created_by, created_at
              FROM public.product_links
             WHERE ($1::varchar IS NULL OR purpose  = $1)
               AND ($2::bigint  IS NULL OR event_id = $2)
             ORDER BY id;
            """,
            purpose,
            event_id,
        )
    return [ProductLink(**dict(r)) for r in rows]


@router.post("/product-links", status_code=201)
async def create_product_link(link: ProductLink) -> ProductLink:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.product_links
                    (purpose, event_id, ticket_type_id, union_product_id,
                     union_product_line_id, academic_year, created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                RETURNING id, purpose, event_id, ticket_type_id, union_product_id,
                          union_product_line_id, academic_year, created_by, created_at;
                """,
                link.purpose, link.event_id, link.ticket_type_id,
                link.union_product_id, link.union_product_line_id,
                link.academic_year, link.created_by,
            )
        except UniqueViolationError:
            raise HTTPException(409, "That product is already linked to this target")
    return ProductLink(**dict(row))


@router.delete("/product-links/{link_id}", status_code=204)
async def delete_product_link(link_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.product_links WHERE id = $1;", link_id)


# ----------------------------------------------------------------- draft holds


@router.post("/reservations/draft")
async def create_draft(req: ReservationRequest, minutes: int = 15) -> ReservationResult:
    """Hold a seat for an anonymous visitor while they fill in the guest form.

    Same locking as `reserve()` — capacity checked under a row lock on the event,
    in its own statement — so a draft cannot oversell either. The differences are
    that nobody owns it yet and it lasts minutes rather than days.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        event = await conn.fetchrow(
            "SELECT id, capacity FROM public.events WHERE id = $1 FOR UPDATE;",
            req.event_id,
        )
        if event is None:
            raise HTTPException(404, "No such event")

        tt = await conn.fetchrow(
            f"SELECT {TT_COLUMNS} FROM public.ticket_types WHERE id = $1 AND event_id = $2;",
            req.ticket_type_id, req.event_id,
        )
        if tt is None:
            raise HTTPException(404, "No such ticket type for this event")

        counts = await conn.fetchrow(
            f"""
            SELECT COALESCE(SUM(quantity), 0)                                    AS event_taken,
                   COALESCE(SUM(quantity) FILTER (WHERE ticket_type_id = $2), 0) AS type_taken
              FROM public.reservations WHERE event_id = $1 AND {LIVE};
            """,
            req.event_id, req.ticket_type_id,
        )
        if tt["capacity"] is not None and counts["type_taken"] + req.quantity > tt["capacity"]:
            return ReservationResult(ok=False, reason="type_sold_out")
        if event["capacity"] is not None and counts["event_taken"] + req.quantity > event["capacity"]:
            return ReservationResult(ok=False, reason="sold_out")

        row = await conn.fetchrow(
            f"""
            INSERT INTO public.reservations
                (event_id, ticket_type_id, user_id, quantity, status, expires_at,
                 booked_as_guest, draft_token)
            VALUES ($1, $2, NULL, $3, 'draft', NOW() + make_interval(mins => $4), true, $5)
            RETURNING {RESERVATION_COLUMNS};
            """,
            req.event_id, req.ticket_type_id, req.quantity, minutes, req.draft_token,
        )
    return ReservationResult(ok=True, reservation=Reservation(**dict(row)))


@router.post("/reservations/draft/{draft_token}/claim")
async def claim_draft(
    draft_token: str, user_id: int, hold_hours: int | None = None
) -> ReservationResult:
    """Turn a draft into a real hold once the guest form is submitted.

    Keyed on the token rather than the row id, so one visitor cannot claim
    another's draft by guessing a number.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        draft = await conn.fetchrow(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.expires_at, r.status,
                   e.hold_hours, e.starts_at
              FROM public.reservations r
              JOIN public.events e ON e.id = r.event_id
             WHERE r.draft_token = $1
             FOR UPDATE OF r;
            """,
            draft_token,
        )
        if draft is None:
            raise HTTPException(404, "No such draft")

        if draft["status"] != "draft":
            # Already claimed: return it rather than erroring, so a
            # double-submitted form is harmless.
            row = await conn.fetchrow(
                f"SELECT {RESERVATION_COLUMNS} FROM public.reservations WHERE id = $1;",
                draft["id"],
            )
            return ReservationResult(ok=True, reservation=Reservation(**dict(row)))

        if draft["expires_at"] <= datetime.now(timezone.utc):
            return ReservationResult(ok=False, reason="draft_expired")

        row = await conn.fetchrow(
            f"""
            UPDATE public.reservations
               SET user_id = $2, status = 'held', draft_token = NULL,
                   expires_at = {HOLD_EXPIRES.format(tt="$4", ev="$5", hours="$3", starts="($6::timestamp AT TIME ZONE 'Europe/London')")}
             WHERE id = $1
            RETURNING {RESERVATION_COLUMNS};
            """,
            draft["id"], user_id, hold_hours or draft["hold_hours"],
            draft["ticket_type_id"], draft["event_id"], draft["starts_at"],
        )
    return ReservationResult(ok=True, reservation=Reservation(**dict(row)))


@router.post("/reservations/draft/{draft_token}/release", status_code=204)
async def release_draft(draft_token: str) -> None:
    """Return the seat immediately when a guest abandons the form."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            # Deleted for the same reason as the sweep above.
            "DELETE FROM public.reservations "
            "WHERE draft_token = $1 AND status = 'draft';",
            draft_token,
        )


# -------------------------------------------------------------------- waitlist


@router.post("/waitlist", status_code=201)
async def join_waitlist(entry: WaitlistEntry) -> WaitlistEntry:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.waitlist_entries
                    (event_id, user_id, preferred_ticket_type_id, quantity)
                VALUES ($1,$2,$3,$4)
                RETURNING {WAITLIST_COLUMNS};
                """,
                entry.event_id, entry.user_id, entry.preferred_ticket_type_id,
                entry.quantity,
            )
        except UniqueViolationError:
            raise HTTPException(409, "Already on the waitlist for this event")
    return WaitlistEntry(**dict(row))


@router.get("/waitlist")
async def list_waitlist(
    event_id: int | None = None, user_id: int | None = None
) -> list[WaitlistDetail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.event_id, w.user_id, w.preferred_ticket_type_id, w.quantity,
                   w.created_at, w.offered_at, w.offer_expires_at, w.reservation_id,
                   w.claimed_at, w.released_at, w.notified_at,
                   u.email, e.title AS event_title, e.slug AS event_slug,
                   -- Queue position, counting only people still waiting.
                   (SELECT COUNT(*) + 1 FROM public.waitlist_entries w2
                     WHERE w2.event_id = w.event_id
                       AND w2.released_at IS NULL AND w2.claimed_at IS NULL
                       AND w2.created_at < w.created_at) AS position
              FROM public.waitlist_entries w
              JOIN public.users u  ON u.id = w.user_id
              JOIN public.events e ON e.id = w.event_id
             WHERE ($1::bigint IS NULL OR w.event_id = $1)
               AND ($2::bigint IS NULL OR w.user_id  = $2)
               AND w.released_at IS NULL
             ORDER BY w.created_at;
            """,
            event_id, user_id,
        )
    return [WaitlistDetail(**dict(r)) for r in rows]


@router.post("/waitlist/{entry_id}/release", status_code=204)
async def leave_waitlist(entry_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.waitlist_entries SET released_at = NOW() WHERE id = $1;",
            entry_id,
        )


@router.post("/waitlist/promote")
async def promote_waitlist() -> dict:
    """Offer freed seats to the front of each queue.

    Called after anything that can free capacity — a cancellation, the expiry
    sweep, the nightly reconcile. Oldest waiter first, stopping as soon as the
    event is full again.
    """
    pool = await get_pool()
    offered: list[dict] = []
    async with pool.acquire() as conn:
        # An offer nobody took up returns its seat to the pool.
        await conn.execute(
            """
            UPDATE public.waitlist_entries SET released_at = NOW()
             WHERE offered_at IS NOT NULL AND claimed_at IS NULL
               AND released_at IS NULL AND offer_expires_at < NOW();
            """
        )

        events = await conn.fetch(
            """
            SELECT DISTINCT event_id FROM public.waitlist_entries
             WHERE released_at IS NULL AND claimed_at IS NULL AND offered_at IS NULL;
            """
        )

        for record in events:
            event_id = record["event_id"]
            async with conn.transaction():
                event = await conn.fetchrow(
                    "SELECT id, capacity, hold_hours FROM public.events WHERE id=$1 FOR UPDATE;",
                    event_id,
                )
                if event is None:
                    continue

                while True:
                    waiter = await conn.fetchrow(
                        """
                        SELECT id, user_id, preferred_ticket_type_id, quantity
                          FROM public.waitlist_entries
                         WHERE event_id = $1 AND released_at IS NULL
                           AND claimed_at IS NULL AND offered_at IS NULL
                         ORDER BY created_at LIMIT 1;
                        """,
                        event_id,
                    )
                    if waiter is None:
                        break

                    # Their preferred type if it has room, otherwise any type
                    # that does — the queue is event-wide, so the offer should
                    # be too.
                    ticket = await conn.fetchrow(
                        f"""
                        SELECT tt.id
                          FROM public.ticket_types tt
                          LEFT JOIN (
                                SELECT ticket_type_id, SUM(quantity) AS taken
                                  FROM public.reservations
                                 WHERE event_id = $1 AND {LIVE}
                                 GROUP BY ticket_type_id
                               ) live ON live.ticket_type_id = tt.id
                         WHERE tt.event_id = $1
                           AND (tt.capacity IS NULL
                                OR tt.capacity - COALESCE(live.taken,0) >= $2)
                         ORDER BY (tt.id = $3) DESC, tt.sort_order
                         LIMIT 1;
                        """,
                        event_id, waiter["quantity"], waiter["preferred_ticket_type_id"],
                    )
                    if ticket is None:
                        break

                    if event["capacity"] is not None:
                        taken = await conn.fetchval(
                            f"SELECT COALESCE(SUM(quantity),0) FROM public.reservations "
                            f"WHERE event_id=$1 AND {LIVE};",
                            event_id,
                        )
                        if taken + waiter["quantity"] > event["capacity"]:
                            break

                    reservation = await conn.fetchrow(
                        f"""
                        INSERT INTO public.reservations
                            (event_id, ticket_type_id, user_id, quantity, status,
                             expires_at, note)
                        VALUES ($1,$2,$3,$4,'held',
                                NOW() + make_interval(hours => $5), 'from waitlist')
                        RETURNING {RESERVATION_COLUMNS};
                        """,
                        event_id, ticket["id"], waiter["user_id"], waiter["quantity"],
                        event["hold_hours"],
                    )
                    await conn.execute(
                        """
                        UPDATE public.waitlist_entries
                           SET offered_at = NOW(), offer_expires_at = $2, reservation_id = $3
                         WHERE id = $1;
                        """,
                        waiter["id"], reservation["expires_at"], reservation["id"],
                    )
                    offered.append({
                        "waitlist_id": waiter["id"],
                        "user_id": waiter["user_id"],
                        "event_id": event_id,
                        "reservation_id": reservation["id"],
                        "expires_at": reservation["expires_at"].isoformat(),
                    })
    return {"offered": offered, "count": len(offered)}


@router.post("/waitlist/{entry_id}/notified", status_code=204)
async def mark_notified(entry_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.waitlist_entries SET notified_at = NOW() WHERE id = $1;",
            entry_id,
        )
