"""Events, ticket types, product links and reservations.

The interesting part is `reserve()`. Everything else is CRUD.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, Body
from fastapi.exceptions import HTTPException

from models.events import (
    CommitteeTicket,
    ConfirmationResult,
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


def _target_years_text(years: list[int]) -> str:
    """The column's canonical form: a comma-separated string of year numbers.
    Empty (not NULL — the migration treats both as "everyone") so a cleared
    checkbox round-trips back as an empty list."""
    return ",".join(str(y) for y in years)


EVENT_COLUMNS = """
    id, slug, title, photo_short_name, category, description, image_url, venue, starts_at, ends_at,
    doors_close_at,
    capacity, availability_badge, waitlist_enabled, hold_hours, published, sponsorship_claimable,
    requested_by_sponsor_id, academic_year, created_by, created_at, updated_at,
    sales_open_at, sales_announced_at, purchase_url, industry, signup_url,
    map_url, meeting_url, meeting_url_private, target_years,
    reservations_close_at, union_sales_close_at
"""

TT_COLUMNS = """
    id, event_id, name, description, price_pence, capacity, requires_purchase,
    members_only, max_per_user, sort_order, purchase_url,
    sales_open_at, sales_close_at
"""

# A reservation consumes stock while it is confirmed, or held/draft and not yet
# expired. `draft` is the 15-minute hold a guest gets while filling in the form:
# it must count against capacity, or two guests could fill the same last seat.
LIVE = "(status = 'confirmed' OR (status IN ('held','draft') AND expires_at > NOW()))"

# A hold on a ticket with no Union product linked can never be confirmed by
# the matcher, so its purchase window would lapse with nobody able to act on
# it — and an expired hold stops counting against capacity, which oversells.
# Those holds last until the event starts instead, and the committee checks
# them by hand. Same link rules as the matcher: the ticket type's own link, or
# an event-level one.
# One person's live bookings of one ticket type, for the per-person limit.
# "Person" is the account OR the browser: a guest has no account until the form
# is submitted, and a guest who books twice usually does it from the same page.
# Drafts are not counted — they are the request being decided, not a booking.
MINE_OF_TYPE = """
    SELECT COALESCE(SUM(quantity), 0)
      FROM public.reservations
     WHERE ticket_type_id = $1
       AND status IN ('held', 'confirmed')
       AND (status = 'confirmed' OR expires_at > NOW())
       AND (user_id = $2 OR (visitor_hash IS NOT NULL AND visitor_hash = $3))
       AND id <> $4;
"""

# The pay-by deadline of a hold on a ticket to be bought (section 17).
#
# The end of a London calendar day, not "now + N hours": the Union's sales
# reach us at the 06:05 sync, so a hold that ends at midnight is one whose
# purchase is matched the next morning (the matcher confirms a held booking
# whatever its deadline, as long as the sale was made). A hold of N days
# (`hold_hours` rounded up to whole days, at least one) ends 23:59:59 on the
# day it was made plus N-1 — and booked at 23:00 or later, a day on, so
# nobody gets a deadline minutes away. Never later than the Union stops
# selling (`union_sales_close_at`).
#
# The arithmetic is on London wall-clock time, converted back at the end, so
# a hold made on a clock-change day still ends at local midnight. Mirrored in
# api/utils/holds.py, and tested to agree.
#
# A hold on a ticket with no Union product linked is different, and unchanged:
# nothing can confirm it but the committee, so it lasts until the event.
HOLD_EXPIRES = """
    CASE WHEN EXISTS (
            SELECT 1 FROM public.product_links pl
             WHERE pl.purpose = 'event_ticket'
               AND (pl.ticket_type_id = {tt}
                    OR (pl.ticket_type_id IS NULL AND pl.event_id = {ev})))
         THEN LEAST(
                (date_trunc('day', NOW() AT TIME ZONE 'Europe/London')
                   + CASE WHEN extract(hour FROM NOW() AT TIME ZONE 'Europe/London') >= 23
                          THEN interval '1 day' ELSE interval '0' END
                   + make_interval(days => GREATEST(1, ({hours}::int + 23) / 24) - 1)
                   + interval '1 day' - interval '1 second'
                ) AT TIME ZONE 'Europe/London',
                COALESCE((SELECT e.union_sales_close_at FROM public.events e
                           WHERE e.id = {ev}), 'infinity'::timestamptz))
         ELSE GREATEST({starts}, NOW() + make_interval(hours => {hours}))
    END
"""

# When a new booking's place lapses, by kind:
#   confirmed outright (a free ticket)   — never: NOW() only fills the column;
#   free, waiting on membership          — when the event starts: there is
#                                          nothing to buy, so the event is the
#                                          only clock (as for an unlinked hold);
#   to be paid for                       — the purchase window, HOLD_EXPIRES.
BOOKING_EXPIRES = (
    "CASE WHEN {confirm}::boolean THEN NOW()"
    " WHEN NOT {paid}::boolean THEN GREATEST({starts}, NOW() + make_interval(hours => {hours}))"
    " ELSE " + HOLD_EXPIRES + " END"
)

MEMBER_THIS_YEAR = """
    SELECT EXISTS (
        SELECT 1 FROM public.users u
          JOIN public.members m ON m.id = u.member_id
         WHERE u.id = $1 AND m.academic_year = $2
    );
"""


async def _confirms_outright(conn, tt, user_id: int | None, academic_year: str | None) -> bool:
    """Is a booking of this ticket type a place the moment it is made?

    A free ticket (`requires_purchase` false) has nothing to buy, so there is
    nothing to hold it for: it is confirmed straight away, and its confirmation
    email and QR go out like any other confirmed booking's. A hold exists to
    give somebody time to pay; on a free ticket it only ever lapsed, or sat
    "held" until the event looking like something was still owed.

    The one exception is a members-only ticket for somebody the feed does not
    show as a member this year — whoever joined today is not in it until the
    morning sync. That booking waits, held, and the matcher's membership pass
    confirms it once they appear (see `match_reservations`), exactly as a paid
    members-only ticket waits. No year given reads as "not shown": the safe
    side is to wait, never to confirm on a guess.
    """
    if tt["requires_purchase"]:
        return False
    if not tt["members_only"]:
        return True
    if not academic_year or user_id is None:
        return False
    return bool(await conn.fetchval(MEMBER_THIS_YEAR, user_id, academic_year))


WAITLIST_COLUMNS = """
    id, event_id, user_id, preferred_ticket_type_id, quantity, created_at,
    offered_at, offer_expires_at, reservation_id, claimed_at, released_at,
    notified_at
"""

RESERVATION_COLUMNS = """
    id, event_id, ticket_type_id, user_id, quantity, status, created_at,
    expires_at, confirmed_at, cancelled_at, order_number, matched_sale_id, note,
    booked_as_guest, reminder_sent_at, source, added_by, confirmation_email,
    confirmation_sent_at, confirmation_attempts
"""

def _utc(value: datetime) -> datetime:
    """Naive values are taken as UTC, the convention everywhere else here."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _check_doors_close(event: EventWrite) -> None:
    """Doors close no later than the event ends, nor days before it starts.

    A doors-close before starts_at is legitimate (doors close at 18:00 for an
    18:30 talk); after ends_at is a typo nobody would notice on the form until
    somebody is refused at a door that never shut. So is one a day or more
    early — usually a year typed short (0006 rather than 2026), which would
    otherwise be saved and printed on the event page as it stands.
    """
    if event.doors_close_at is None:
        return
    doors = _utc(event.doors_close_at)
    if event.ends_at is not None and doors > _utc(event.ends_at):
        raise HTTPException(
            422,
            "Doors close cannot be after the event ends. "
            "It may be before the event starts.",
        )
    if doors <= _utc(event.starts_at) - timedelta(days=1):
        raise HTTPException(
            422,
            "Doors close is a day or more before the event starts — "
            "check its date, and the year in particular.",
        )


def _check_sale_windows(event: EventWrite) -> None:
    """Booking closes no later than selling does, and neither after the event.

    The database holds the first as a check too (066); here it gets a message
    somebody can act on rather than a constraint name.
    """
    reserve = _utc(event.reservations_close_at) if event.reservations_close_at else None
    union = _utc(event.union_sales_close_at) if event.union_sales_close_at else None
    end = _utc(event.ends_at or event.starts_at)
    if reserve and union and reserve > union:
        raise HTTPException(
            422,
            "Reservations close cannot be after Union sales close — nobody could "
            "pay for a ticket reserved in between.",
        )
    for label, when in (("Reservations close", reserve), ("Union sales close", union)):
        if when and when > end:
            raise HTTPException(422, f"{label} cannot be after the event ends.")

    # Each ticket type's own window (080) sits inside the event's: the event's
    # sale time is when the event goes on sale at all, and is what the
    # on-sale email announces, so a type cannot open before it. A window that
    # can never be open is a typo, refused here rather than found when
    # somebody cannot book.
    opens = _utc(event.sales_open_at) if event.sales_open_at else None
    for tt in event.ticket_types:
        t_open = _utc(tt.sales_open_at) if tt.sales_open_at else None
        t_close = _utc(tt.sales_close_at) if tt.sales_close_at else None
        name = f"“{tt.name.strip()}”"
        if t_open and t_close and t_open >= t_close:
            raise HTTPException(422, f"{name} stops selling before it goes on sale.")
        if t_open and opens and t_open < opens:
            raise HTTPException(
                422,
                f"{name} goes on sale before the event's tickets do. Set the "
                "event's sale time to the earliest any ticket goes on sale.",
            )
        if t_close and opens and t_close <= opens:
            raise HTTPException(
                422, f"{name} stops selling before the event's tickets go on sale."
            )
        if t_open and reserve and t_open >= reserve:
            raise HTTPException(
                422, f"{name} goes on sale after reservations for the event close."
            )
        if t_close and t_close > end:
            raise HTTPException(422, f"{name} cannot stop selling after the event ends.")


def _reservations_closed(event) -> bool:
    """Whether booking has closed for this event row."""
    closes = event["reservations_close_at"]
    return closes is not None and closes <= datetime.now(timezone.utc)


def _type_off_sale(tt) -> bool:
    """Whether this ticket type is outside its own sale window right now (080).

    Only the type's own times: the event's window is checked separately, and a
    type with no times of its own sells whenever the event does.
    """
    now = datetime.now(timezone.utc)
    opens, closes = tt["sales_open_at"], tt["sales_close_at"]
    return (opens is not None and opens > now) or (closes is not None and closes <= now)


# A failed confirmation email is retried this many times in all, then left for
# the committee to resend by hand from the bookings page.
CONFIRMATION_MAX_ATTEMPTS = 5


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
    # `target_years` arrives here as the stored comma string ("1,2"); the
    # model's before-validator turns it back into the sorted list of ints the
    # API speaks, so every reader of this row gets the same normalised form.
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
    offset: int = 0,
) -> list[EventDetail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # `id` breaks `starts_at` ties so `OFFSET` paging is deterministic:
        # two events can share a start time, and without it a page boundary
        # could repeat or skip a row.
        rows = await conn.fetch(
            f"""
            SELECT {EVENT_COLUMNS} FROM public.events
             WHERE ($1::bool IS NOT TRUE OR published)
               AND ($2::bool IS NOT TRUE OR starts_at >= NOW())
               AND ($3::varchar IS NULL OR academic_year = $3)
             ORDER BY starts_at, id
             LIMIT $4 OFFSET $5;
            """,
            published_only,
            upcoming,
            academic_year,
            limit,
            offset,
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
               AND sales_open_at <= NOW()
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


@router.patch("/events/{event_id}/photo-short-name")
async def update_photo_short_name(
    event_id: int,
    photo_short_name: str | None = Body(embed=True, max_length=64),
) -> EventDetail:
    """Independent photo label edit; no event/ticket fields are rewritten."""
    pool = await get_pool()
    label = photo_short_name.strip() if photo_short_name else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""UPDATE public.events
                   SET photo_short_name=$2, updated_at=NOW()
                 WHERE id=$1
             RETURNING {EVENT_COLUMNS};""",
            event_id, label or None,
        )
        if row is None:
            raise HTTPException(404, "No such event")
        return await _detail(conn, row)


@router.patch("/events/{event_id}/waitlist-enabled")
async def update_event_waitlist_enabled(
    event_id: int,
    waitlist_enabled: bool = Body(embed=True),
) -> EventDetail:
    """Stop/start new queue joins without touching existing queue positions."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""UPDATE public.events SET waitlist_enabled=$2, updated_at=NOW()
                  WHERE id=$1 RETURNING {EVENT_COLUMNS};""",
            event_id, waitlist_enabled,
        )
        if row is None:
            raise HTTPException(404, "No such event")
        return await _detail(conn, row)


@router.patch("/events/{event_id}/availability-badge")
async def update_event_availability_badge(
    event_id: int,
    availability_badge: Literal["auto", "not_many_left", "selling_fast"] = Body(embed=True),
) -> EventDetail:
    """Change only the display label; do not rewrite the event or ticket rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""UPDATE public.events SET availability_badge=$2, updated_at=NOW()
                  WHERE id=$1 RETURNING {EVENT_COLUMNS};""",
            event_id, availability_badge,
        )
        if row is None:
            raise HTTPException(404, "No such event")
        return await _detail(conn, row)


@router.post("/events", status_code=201)
async def create_event(event: EventWrite, created_by: int | None = None) -> EventDetail:
    """Event and its ticket types are created together, in one transaction: an
    event with no way to book it is not a useful intermediate state."""
    pool = await get_pool()
    _check_doors_close(event)
    _check_sale_windows(event)
    async with pool.acquire() as conn, conn.transaction():
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.events
                    (slug, title, category, description, image_url, venue, starts_at,
                     ends_at, doors_close_at,
                     capacity, hold_hours, published, sponsorship_claimable,
                     requested_by_sponsor_id, academic_year, created_by, sales_open_at,
                     purchase_url, industry, signup_url,
                     map_url, meeting_url, meeting_url_private, target_years,
                     reservations_close_at, union_sales_close_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                        $19,$20,$21,$22,$23,$24,$25,$26)
                RETURNING {EVENT_COLUMNS};
                """,
                event.slug, event.title, event.category, event.description,
                event.image_url, event.venue, event.starts_at, event.ends_at,
                event.doors_close_at,
                event.capacity, event.hold_hours, event.published,
                event.sponsorship_claimable, event.requested_by_sponsor_id,
                event.academic_year or "", created_by, event.sales_open_at,
                event.purchase_url, event.industry, event.signup_url,
                event.map_url, event.meeting_url, event.meeting_url_private,
                _target_years_text(event.target_years),
                event.reservations_close_at, event.union_sales_close_at,
            )
        except UniqueViolationError:
            raise HTTPException(409, f"An event with slug '{event.slug}' already exists")

        for tt in event.ticket_types:
            await conn.execute(
                """
                INSERT INTO public.ticket_types
                    (event_id, name, description, price_pence, capacity,
                     requires_purchase, members_only, max_per_user, sort_order,
                     purchase_url, sales_open_at, sales_close_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12);
                """,
                row["id"], tt.name, tt.description, tt.price_pence, tt.capacity,
                tt.requires_purchase, tt.members_only, tt.max_per_user, tt.sort_order,
                tt.purchase_url, tt.sales_open_at, tt.sales_close_at,
            )
        return await _detail(conn, row)


@router.put("/events/{event_id}")
async def update_event(event_id: int, event: EventWrite) -> EventDetail:
    _check_doors_close(event)
    _check_sale_windows(event)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            UPDATE public.events SET
                slug=$2, title=$3, category=$4, description=$5, image_url=$6,
                venue=$7, starts_at=$8, ends_at=$9, doors_close_at=$10,
                capacity=$11, hold_hours=$12,
                published=$13, sponsorship_claimable=$14,
                requested_by_sponsor_id=$15,
                academic_year=COALESCE($16, academic_year),
                sales_open_at=$17,
                purchase_url=$18,
                industry=$19,
                signup_url=$20,
                map_url=$21,
                meeting_url=$22,
                meeting_url_private=$23,
                target_years=$24,
                reservations_close_at=$25,
                union_sales_close_at=$26,
                updated_at=NOW()
             WHERE id=$1
            RETURNING {EVENT_COLUMNS};
            """,
            event_id, event.slug, event.title, event.category, event.description,
            event.image_url, event.venue, event.starts_at, event.ends_at,
            event.doors_close_at,
            event.capacity, event.hold_hours, event.published,
            event.sponsorship_claimable, event.requested_by_sponsor_id,
            event.academic_year, event.sales_open_at, event.purchase_url,
            event.industry, event.signup_url,
            event.map_url, event.meeting_url, event.meeting_url_private,
            _target_years_text(event.target_years),
            event.reservations_close_at, event.union_sales_close_at,
        )
        if row is None:
            raise HTTPException(404, "No such event")
        # Union sales closing earlier than some live holds' deadlines: those
        # deadlines come forward to it, in this transaction, so nobody is told
        # they have until Friday to buy what the shop stops selling on
        # Wednesday. Linked tickets only — an unlinked hold was never on a
        # Union clock. Confirmed bookings are untouched.
        clamped = await conn.fetch(
            """
            UPDATE public.reservations r
               SET expires_at = e.union_sales_close_at
              FROM public.events e
             WHERE e.id = $1 AND r.event_id = e.id
               AND e.union_sales_close_at IS NOT NULL
               AND r.status = 'held'
               AND r.expires_at > e.union_sales_close_at
               AND EXISTS (
                   SELECT 1 FROM public.product_links pl
                    WHERE pl.purpose = 'event_ticket'
                      AND (pl.ticket_type_id = r.ticket_type_id
                           OR (pl.ticket_type_id IS NULL AND pl.event_id = e.id)))
            RETURNING r.id, r.expires_at;
            """,
            event_id,
        )
        for hold in clamped:
            # A waitlist offer's deadline is its hold's; keep them saying the same.
            await conn.execute(
                "UPDATE public.waitlist_entries SET offer_expires_at = $2 "
                "WHERE reservation_id = $1 AND claimed_at IS NULL;",
                hold["id"], hold["expires_at"],
            )
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
                        sort_order=$9, purchase_url=$10,
                        sales_open_at=$11, sales_close_at=$12
                     WHERE id=$1;
                    """,
                    tt.id, tt.name, tt.description, tt.price_pence, tt.capacity,
                    tt.requires_purchase, tt.members_only, tt.max_per_user,
                    tt.sort_order, tt.purchase_url, tt.sales_open_at, tt.sales_close_at,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO public.ticket_types
                        (event_id, name, description, price_pence, capacity,
                         requires_purchase, members_only, max_per_user, sort_order,
                         purchase_url, sales_open_at, sales_close_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12);
                    """,
                    event_id, tt.name, tt.description, tt.price_pence, tt.capacity,
                    tt.requires_purchase, tt.members_only, tt.max_per_user,
                    tt.sort_order, tt.purchase_url, tt.sales_open_at, tt.sales_close_at,
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
                     purchase_url, sales_open_at, sales_close_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                RETURNING {TT_COLUMNS};
                """,
                event_id, tt.name, tt.description, tt.price_pence, tt.capacity,
                tt.requires_purchase, tt.members_only, tt.max_per_user, tt.sort_order,
                tt.purchase_url, tt.sales_open_at, tt.sales_close_at,
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
                purchase_url=$10, sales_open_at=$11, sales_close_at=$12
             WHERE id=$1
            RETURNING {TT_COLUMNS};
            """,
            ticket_type_id, tt.name, tt.description, tt.price_pence, tt.capacity,
            tt.requires_purchase, tt.members_only, tt.max_per_user, tt.sort_order,
            tt.purchase_url, tt.sales_open_at, tt.sales_close_at,
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
            "SELECT id, capacity, hold_hours, published, starts_at, reservations_close_at "
            "FROM public.events WHERE id = $1 FOR UPDATE;",
            req.event_id,
        )
        if event is None:
            raise HTTPException(404, "No such event")
        # Here as well as in the api: the adapter is the one place every way of
        # booking passes through.
        if _reservations_closed(event):
            return ReservationResult(ok=False, reason="reservations_closed")

        tt = await conn.fetchrow(
            f"SELECT {TT_COLUMNS} FROM public.ticket_types WHERE id = $1 AND event_id = $2;",
            req.ticket_type_id,
            req.event_id,
        )
        if tt is None:
            raise HTTPException(404, "No such ticket type for this event")
        # Early bird after it ends, general before it starts.
        if _type_off_sale(tt):
            return ReservationResult(ok=False, reason="type_not_on_sale")

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

        # A free ticket is a place outright ($10); anything else is a hold until
        # a Union sale confirms it ($11 is whether it needs one). `expires_at` is
        # NOT NULL, so a confirmed row carries NOW() there — nothing reads it
        # once a booking is confirmed. One CASE rather than a statement per
        # branch, so every $n is always referenced (Postgres refuses a gap).
        confirm = await _confirms_outright(conn, tt, req.user_id, req.academic_year)
        expires = BOOKING_EXPIRES.format(
            confirm="$10", paid="$11", tt="$2", ev="$1", hours="$5", starts="$8::timestamptz"
        )
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.reservations
                (event_id, ticket_type_id, user_id, quantity, status, expires_at,
                 confirmed_at, order_number, booked_as_guest, visitor_hash)
            VALUES ($1, $2, $3, $4,
                    CASE WHEN $10::boolean THEN 'confirmed' ELSE 'held' END,
                    {expires},
                    CASE WHEN $10::boolean THEN NOW() END,
                    $6, $7, $9)
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
            req.visitor_hash,
            confirm,
            tt["requires_purchase"],
        )
    return ReservationResult(ok=True, reservation=Reservation(**dict(row)))


@router.get("/reservations")
async def list_reservations(
    user_id: int | None = None,
    event_id: int | None = None,
    status: str | None = None,
    limit: int = 200,
    visitor_hash: str | None = None,
) -> list[ReservationDetail]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.user_id, r.quantity, r.status,
                   r.created_at, r.expires_at, r.confirmed_at, r.cancelled_at,
                   r.order_number, r.matched_sale_id, r.note, r.booked_as_guest,
                   r.source, r.added_by, r.confirmation_email,
                   r.confirmation_sent_at, r.confirmation_attempts,
                   e.title AS event_title, e.slug AS event_slug,
                   e.starts_at AS event_starts_at,
                   -- Doors close and venue, so "My tickets" (section 15) can
                   -- render each booking's event without a second query.
                   e.doors_close_at AS event_doors_close_at,
                   e.venue AS event_venue,
                   tt.name AS ticket_type_name, tt.price_pence, tt.requires_purchase,
                   tt.members_only,
                   u.email AS user_email, u.first_name AS user_first_name,
                   u.last_name AS user_last_name, u.shortcode AS user_shortcode,
                   (u.member_id IS NOT NULL) AS has_member_record
              FROM public.reservations r
              JOIN public.events e       ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
              JOIN public.users u        ON u.id  = r.user_id
             WHERE ($1::bigint  IS NULL OR r.user_id  = $1)
               AND ($2::bigint  IS NULL OR r.event_id = $2)
               AND ($3::varchar IS NULL OR r.status   = $3)
               AND ($5::varchar IS NULL OR r.visitor_hash = $5)
             ORDER BY r.created_at DESC
             LIMIT $4;
            """,
            user_id,
            event_id,
            status,
            limit,
            visitor_hash,
        )
    return [ReservationDetail(**dict(r)) for r in rows]


@router.get("/reservations/overdue")
async def overdue_reservations() -> list[ReservationDetail]:
    """Bookings requiring a committee cancellation decision.

    Includes expired purchase holds and rejected Union order claims, even on
    manually confirmed bookings. A pending claim never enters the queue
    before a complete successful daily sales refresh has checked it.
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
                   u.member_id IS NOT NULL AS has_member_record,
                   v.status AS order_verification_status,
                   v.order_number AS verification_order_number,
                   v.checked_at AS review_due_at
              FROM public.reservations r
              JOIN public.events e        ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
              JOIN public.users u         ON u.id  = r.user_id
              LEFT JOIN public.reservation_order_verification v ON v.reservation_id=r.id
             WHERE tt.requires_purchase
               AND (
                    (r.status='held' AND r.expires_at<NOW()
                     AND (v.status IS NULL OR v.status!='pending'))
                    OR (r.status IN ('held','confirmed') AND v.status='rejected')
                   )
             ORDER BY CASE WHEN v.status='rejected' THEN 0 ELSE 1 END,
                      COALESCE(v.checked_at,r.expires_at);
            """
        )
    return [ReservationDetail(**dict(r)) for r in rows]


@router.get("/reservations/needing-reminder")
async def reservations_needing_reminder(
    within_hours: int = 24, min_age_hours: int = 0
) -> list[ReservationDetail]:
    """Unpaid holds about to run out, which have not been chased yet.

    `min_age_hours` leaves alone a hold made only just now: somebody who booked
    at 20:00 for a midnight deadline was told that deadline by the "we're
    holding your ticket" email minutes ago, and a "last chance" straight after
    it is noise.

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
               AND r.created_at <= NOW() - make_interval(hours => $2)
             ORDER BY r.expires_at;
            """,
            within_hours, min_age_hours,
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
                   matched_sale_id, note, booked_as_guest, source, added_by,
                   confirmation_email, confirmation_sent_at, confirmation_attempts
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


@router.patch("/reservations/{reservation_id}/committee-order-number")
async def committee_set_order_number(reservation_id: int, order_number: str | None = Body(default=None, embed=True), actor_user_id: int | None = Body(default=None, embed=True)) -> Reservation:
    """Edit a reservation order and schedule verification for new paid claims.

    Before this, editing the number only updated an existing *pending* claim.
    A first-time number on an ordinary hold was never checked for rejection.
    The reservation, verification and audit are now one transaction.
    """
    value = (order_number or '').strip() or None
    if value is not None and len(value) > 128:
        raise HTTPException(400, 'Order number too long')
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        old = await conn.fetchrow("""SELECT r.order_number,r.status,tt.requires_purchase
            FROM public.reservations r JOIN public.ticket_types tt ON tt.id=r.ticket_type_id
            WHERE r.id=$1 FOR UPDATE OF r""", reservation_id)
        if old is None or old['status'] == 'draft':
            raise HTTPException(404, 'No such editable reservation')
        row = await conn.fetchrow(
            f"UPDATE public.reservations SET order_number=$2 WHERE id=$1 RETURNING {RESERVATION_COLUMNS};",
            reservation_id, value,
        )
        if value is None:
            await conn.execute("""DELETE FROM public.reservation_order_verification
                WHERE reservation_id=$1 AND status='pending'""", reservation_id)
        elif old['requires_purchase'] and row['status'] in ('held','confirmed'):
            current = await conn.fetchrow("""SELECT order_number,status
                FROM public.reservation_order_verification WHERE reservation_id=$1 FOR UPDATE""", reservation_id)
            if (current is None or current['order_number'] != value
                    or current['status'] not in ('pending', 'matched')):
                await conn.execute("""INSERT INTO public.reservation_order_verification
                    (reservation_id,order_number,submitted_by,check_after)
                    VALUES($1,$2,$3,(date_trunc('day',NOW() AT TIME ZONE 'UTC')
                        + interval '1 day 6 hours 5 minutes') AT TIME ZONE 'UTC')
                    ON CONFLICT(reservation_id) DO UPDATE SET
                        order_number=EXCLUDED.order_number,
                        submitted_by=EXCLUDED.submitted_by,
                        submitted_at=NOW(),check_after=EXCLUDED.check_after,
                        status='pending',checked_at=NULL""",
                    reservation_id,value,actor_user_id)
        await conn.execute("""INSERT INTO public.action_audit(actor_user_id, action, entity_type, entity_id, before_data, after_data)
            VALUES ($1, 'reservations.order_number.edit', 'reservation', $2,
                jsonb_build_object('order_number', $3::text),
                jsonb_build_object('order_number', $4::text))""",
            actor_user_id, reservation_id, old['order_number'], value)
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


@router.post("/reservations/{reservation_id}/ticket-type")
async def change_ticket_type(reservation_id: int, ticket_type_id: int) -> Reservation:
    """Move a live booking to another ticket type of the same event.

    For the committee correcting a booking — the wrong option picked, a change of
    mind agreed by message. The status is left alone: a held booking stays held
    (and now needs the new type's Union product to confirm), a confirmed one
    stays confirmed — except that a held booking moved onto a free ticket is
    confirmed, because a free ticket is never a hold (see
    `_confirms_outright`). The committee chose the move, so no membership
    check: that is theirs to judge.

    Serialised on the event row exactly as `reserve` is, so a move cannot
    oversell the target type: the capacity check and the update see the same
    snapshot of everybody else's bookings. The event total does not change —
    the places move between types, they are not added.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        current = await conn.fetchrow(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.quantity, r.status,
                   r.expires_at > NOW() AS unexpired, tt.name AS ticket_type_name
              FROM public.reservations r
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
             WHERE r.id = $1;
            """,
            reservation_id,
        )
        if current is None:
            raise HTTPException(404, "No such reservation")
        if not (current["status"] == "confirmed"
                or (current["status"] == "held" and current["unexpired"])):
            raise HTTPException(409, "Only a live booking — held or bought — can be moved.")
        if current["ticket_type_id"] == ticket_type_id:
            raise HTTPException(409, "It is already that ticket type.")

        await conn.execute(
            "SELECT 1 FROM public.events WHERE id = $1 FOR UPDATE;", current["event_id"]
        )
        target = await conn.fetchrow(
            f"SELECT {TT_COLUMNS} FROM public.ticket_types WHERE id = $1 AND event_id = $2;",
            ticket_type_id,
            current["event_id"],
        )
        if target is None:
            raise HTTPException(404, "No such ticket type for this event")

        taken = await conn.fetchval(
            f"""
            SELECT COALESCE(SUM(quantity), 0) FROM public.reservations
             WHERE event_id = $1 AND ticket_type_id = $2 AND id <> $3 AND {LIVE};
            """,
            current["event_id"],
            ticket_type_id,
            reservation_id,
        )
        if target["capacity"] is not None and taken + current["quantity"] > target["capacity"]:
            raise HTTPException(
                409,
                f"{target['name']} is full ({taken} of {target['capacity']} taken).",
            )

        row = await conn.fetchrow(
            f"""
            UPDATE public.reservations
               SET ticket_type_id = $2,
                   note = COALESCE(note || ' | ', '') || $3,
                   -- The right-hand sides see the row as it was.
                   status = CASE WHEN status = 'held' AND NOT $4 THEN 'confirmed'
                                 ELSE status END,
                   confirmed_at = CASE WHEN status = 'held' AND NOT $4 THEN NOW()
                                       ELSE confirmed_at END
             WHERE id = $1
            RETURNING {RESERVATION_COLUMNS};
            """,
            reservation_id,
            ticket_type_id,
            f"ticket changed from {current['ticket_type_name']} to {target['name']}",
            target["requires_purchase"],
        )
    return Reservation(**dict(row))


@router.post("/reservations/committee")
async def committee_ticket(req: CommitteeTicket) -> Reservation:
    """Create a capacity-counted committee booking with atomic payment proof.

    Paid manually added bookings start held with a pending Union order claim;
    only a sale match or explicitly authorised override confirms them. A
    separate imported sales row is already verified evidence and can confirm
    directly. Free tickets also confirm without a purchase order.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        import_row = None
        if req.import_row_id is not None:
            import_row = await conn.fetchrow("""SELECT r.status, r.quantity,
                    r.ticket_type_id, r.order_number, i.event_id
                    FROM public.product_sales_import_rows r
                    JOIN public.product_sales_imports i ON i.id=r.import_id
                    WHERE r.id=$1 FOR UPDATE OF r""", req.import_row_id)
            if (import_row is None or import_row['status'] != 'unreserved' or
                import_row['ticket_type_id'] != req.ticket_type_id or
                import_row['event_id'] != req.event_id or
                import_row['quantity'] != req.quantity):
                raise HTTPException(409, 'Imported sale already handled, or ticket details do not match')
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

        order_number = (req.order_number or '').strip() or None
        if import_row and not order_number:
            order_number = (import_row['order_number'] or '').strip() or None
        if order_number and len(order_number) > 128:
            raise HTTPException(400, 'Order number must be at most 128 characters')
        pending_payment = bool(tt['requires_purchase'] and not req.import_row_id
                               and not req.override_confirmation)
        held_free = bool(not tt['requires_purchase'] and not req.confirm_immediately
                         and not req.import_row_id and not req.override_confirmation)
        starts_held = pending_payment or held_free
        if pending_payment and not order_number:
            raise HTTPException(400, 'A Union order number is required unless payment verification is overridden')

        counts = await conn.fetchrow(
            f"""
            SELECT COALESCE(SUM(quantity), 0)                                    AS event_taken,
                   COALESCE(SUM(quantity) FILTER (WHERE ticket_type_id = $2), 0) AS type_taken
              FROM public.reservations
             WHERE event_id = $1 AND {LIVE};
            """, req.event_id, req.ticket_type_id,
        )
        if not req.override_capacity and tt['capacity'] is not None and counts['type_taken'] + req.quantity > tt['capacity']:
            raise HTTPException(409, f"{tt['name']} is full ({counts['type_taken']} of {tt['capacity']} taken).")
        if not req.override_capacity and event['capacity'] is not None and counts['event_taken'] + req.quantity > event['capacity']:
            raise HTTPException(409, f"The event is full ({counts['event_taken']} of {event['capacity']} taken).")

        row = await conn.fetchrow(
            f"""
            INSERT INTO public.reservations
                (event_id,ticket_type_id,user_id,quantity,status,confirmed_at,
                 expires_at,note,booked_as_guest,source,added_by,
                 confirmation_email,order_number)
            VALUES ($1,$2,$3,$4,
                    CASE WHEN $9::boolean THEN 'held' ELSE 'confirmed' END,
                    CASE WHEN $9::boolean THEN NULL ELSE NOW() END,
                    CASE WHEN $9::boolean THEN
                        (date_trunc('day',NOW() AT TIME ZONE 'UTC')
                         + interval '1 day 6 hours 5 minutes') AT TIME ZONE 'UTC'
                    ELSE NOW() END,
                    $5,$6,'committee',$7,
                    CASE WHEN $8::boolean THEN NULL ELSE 'skipped' END,$10)
            RETURNING {RESERVATION_COLUMNS};
            """,
            req.event_id, req.ticket_type_id, req.user_id, req.quantity,
            req.note, req.booked_as_guest, req.added_by, req.send_confirmation,
            starts_held, order_number,
        )
        if pending_payment:
            # Claim created in the very same transaction as the hold. Never
            # expose an apparently confirmed seat while proof is pending.
            await conn.execute("""INSERT INTO public.reservation_order_verification
                (reservation_id,order_number,submitted_by,check_after)
                VALUES($1,$2,$3,$4)""",
                row['id'], order_number, req.added_by, row['expires_at'])
        if req.import_row_id is not None:
            await conn.execute("""UPDATE public.product_sales_import_rows
                SET status='ticket_added', reservation_id=$2, reviewed_by=$3,
                    reviewed_at=NOW(), detail='Committee-created ticket from imported sale'
                WHERE id=$1""", req.import_row_id, row['id'], req.added_by)
        await conn.execute("""INSERT INTO public.action_audit
            (actor_user_id,action,entity_type,entity_id,after_data)
            VALUES($1,'reservations.committee.add','reservation',$2,
                jsonb_build_object('event_id',$3::bigint,'ticket_type_id',$4::bigint,
                                   'quantity',$5::integer,'override_capacity',$6::boolean,
                                   'override_confirmation',$7::boolean,
                                   'payment_status',$8::text,'order_number',$9::text,
                                   'confirm_immediately',$10::boolean))""",
            req.added_by, row['id'], req.event_id, req.ticket_type_id,
            req.quantity, req.override_capacity, req.override_confirmation,
            'pending' if pending_payment else ('held' if held_free else 'confirmed'),
            order_number, req.confirm_immediately,
        )
    return Reservation(**dict(row))


@router.post("/reservations/confirmations/claim")
async def claim_confirmations(limit: int = 50) -> list[dict]:
    """Claim confirmed bookings whose "you're confirmed" email has not gone.

    Claimed by marking them 'sending' under SKIP LOCKED, so two senders — the
    ten-minute loop and an immediate send after a confirm, or two api pods —
    never email the same person twice. A claim that sits in 'sending' for half
    an hour is assumed dead (the pod stopped mid-send) and reclaimed; a failure
    waits fifteen minutes before it is retried.

    A booking confirmed after its event is over gets no email: it would only
    confuse. Marked 'skipped' so the bookings page says why.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE public.reservations r
               SET confirmation_email = 'skipped', confirmation_updated_at = NOW()
              FROM public.events e
             WHERE e.id = r.event_id AND r.status = 'confirmed'
               AND (r.confirmation_email IS NULL OR r.confirmation_email = 'failed')
               AND e.starts_at < NOW() - interval '12 hours';
            """
        )
        claimed = await conn.fetch(
            """
            WITH picked AS (
                SELECT r.id
                  FROM public.reservations r
                  JOIN public.events e ON e.id = r.event_id
                 WHERE r.status = 'confirmed'
                   AND e.starts_at >= NOW() - interval '12 hours'
                   AND (r.confirmation_email IS NULL
                        OR (r.confirmation_email = 'failed'
                            AND r.confirmation_attempts < $2
                            AND r.confirmation_updated_at < NOW() - interval '15 minutes')
                        OR (r.confirmation_email = 'sending'
                            AND r.confirmation_updated_at < NOW() - interval '30 minutes'))
                 ORDER BY r.confirmed_at NULLS LAST, r.id
                 LIMIT $1
                   FOR UPDATE OF r SKIP LOCKED
            )
            UPDATE public.reservations r
               SET confirmation_email = 'sending',
                   confirmation_attempts = r.confirmation_attempts + 1,
                   confirmation_updated_at = NOW()
              FROM picked
             WHERE r.id = picked.id
            RETURNING r.id;
            """,
            limit,
            CONFIRMATION_MAX_ATTEMPTS,
        )
        ids = [r["id"] for r in claimed]
        if not ids:
            return []
        rows = await conn.fetch(
            """
            SELECT r.id, r.quantity, r.confirmation_attempts, r.source,
                   u.email, u.first_name,
                   e.title AS event_title, e.slug AS event_slug,
                   e.starts_at, e.ends_at, e.venue, e.doors_close_at,
                   tt.name AS ticket_type_name
              FROM public.reservations r
              JOIN public.users u         ON u.id = r.user_id
              JOIN public.events e        ON e.id = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
             WHERE r.id = ANY($1::bigint[])
             ORDER BY r.id;
            """,
            ids,
        )
    return [dict(r) for r in rows]


@router.post("/reservations/confirmations/result", status_code=204)
async def record_confirmations(results: list[ConfirmationResult]) -> None:
    """Record how each claimed confirmation email went."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        for r in results:
            if r.outcome not in ("sent", "failed", "suppressed"):
                continue
            await conn.execute(
                """
                UPDATE public.reservations
                   SET confirmation_email = $2::varchar,
                       confirmation_sent_at = CASE WHEN $2::varchar = 'sent' THEN NOW()
                                                   ELSE confirmation_sent_at END,
                       confirmation_updated_at = NOW()
                 WHERE id = $1 AND confirmation_email = 'sending';
                """,
                r.id,
                r.outcome,
            )


@router.post("/reservations/{reservation_id}/confirmation/resend")
async def resend_confirmation(reservation_id: int) -> Reservation:
    """Queue a confirmed booking's email to go again, attempts reset."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.reservations
               SET confirmation_email = NULL, confirmation_attempts = 0,
                   confirmation_updated_at = NOW()
             WHERE id = $1 AND status = 'confirmed'
            RETURNING {RESERVATION_COLUMNS};
            """,
            reservation_id,
        )
    if row is None:
        raise HTTPException(409, "Only a confirmed booking has a confirmation to send.")
    return Reservation(**dict(row))


@router.post("/reservations/{reservation_id}/submit-order")
async def committee_submit_order(
    reservation_id: int,
    order_number: str = Body(embed=True),
    actor_user_id: int = Body(embed=True),
) -> dict:
    """Link a claimed order to a hold, never confirming without sale evidence."""
    value = order_number.strip()
    if not value or len(value) > 128:
        raise HTTPException(400, 'Enter a valid Union order number (up to 128 characters)')
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        previous = await conn.fetchrow("""SELECT r.status, r.order_number, tt.requires_purchase
            FROM public.reservations r JOIN public.ticket_types tt ON tt.id=r.ticket_type_id
            WHERE r.id=$1 FOR UPDATE OF r""", reservation_id)
        if previous is None or previous['status'] != 'held':
            raise HTTPException(409, 'Only held bookings can submit a purchase order')
        if not previous['requires_purchase']:
            raise HTTPException(409, 'This is a free ticket; no purchase order is needed')
        row = await conn.fetchrow(
            f"UPDATE public.reservations SET order_number=$2 WHERE id=$1 RETURNING {RESERVATION_COLUMNS}",
            reservation_id, value,
        )
        await conn.execute("""INSERT INTO public.reservation_order_verification
            (reservation_id,order_number,submitted_by,check_after)
            VALUES ($1,$2,$3,(date_trunc('day',NOW() AT TIME ZONE 'UTC')
                + interval '1 day 6 hours 5 minutes') AT TIME ZONE 'UTC')
            ON CONFLICT(reservation_id) DO UPDATE SET
                order_number=EXCLUDED.order_number,
                submitted_by=EXCLUDED.submitted_by,
                submitted_at=NOW(), check_after=EXCLUDED.check_after,
                status='pending', checked_at=NULL""",
            reservation_id, value, actor_user_id,
        )
        await conn.execute("""INSERT INTO public.action_audit
            (actor_user_id,action,entity_type,entity_id,before_data,after_data)
            VALUES ($1,'reservations.order.submit','reservation',$2,
                jsonb_build_object('order_number',$3::text),
                jsonb_build_object('order_number',$4::text,'status','pending'))""",
            actor_user_id, reservation_id, previous['order_number'], value,
        )
    return {'ok': True, 'reservation': Reservation(**dict(row)).model_dump(mode='json')}


@router.get('/order-verification/reservations')
async def list_order_verifications(event_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT v.reservation_id,v.order_number,v.status,
                   v.submitted_at,v.check_after,v.checked_at
            FROM public.reservation_order_verification v
            JOIN public.reservations r ON r.id=v.reservation_id
            WHERE r.event_id=$1 ORDER BY v.submitted_at DESC""", event_id)
    return [dict(row) for row in rows]


@router.post('/reservations/order-verification/settle')
async def settle_order_verifications(
    allow_reject: bool = Body(default=False, embed=True),
) -> dict:
    """Match immediately; reject only after a successful fresh sales sync.

    The caller sends allow_reject only after a successful complete sales import
    and match pass. Database locking keeps concurrent resubmits from being lost.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        pending = await conn.fetch("""SELECT v.reservation_id,v.order_number,v.check_after,
                  r.status AS reservation_status,r.order_number AS current_order,
                  r.matched_sale_id,s.order_number AS matched_order
            FROM public.reservation_order_verification v
            JOIN public.reservations r ON r.id=v.reservation_id
            LEFT JOIN public.union_sales s ON s.id=r.matched_sale_id
            WHERE v.status='pending' AND (
                  (r.status='confirmed' AND r.matched_sale_id IS NOT NULL)
                  OR ($1::boolean AND v.check_after<=NOW()))
            FOR UPDATE OF v,r""", allow_reject)
        matched = rejected = 0
        for pending_row in pending:
            approved = (pending_row['reservation_status'] == 'confirmed' and
                        pending_row['matched_order'] is not None and
                        pending_row['matched_order'].strip().upper() == pending_row['order_number'].strip().upper())
            if approved:
                outcome = 'matched'
                matched += 1
            elif allow_reject and pending_row['check_after'] <= datetime.now(timezone.utc):
                # If the user re-edited their order while it was awaiting sync,
                # do not clear a newer number. Never cancel their reservation.
                await conn.execute("""UPDATE public.reservations SET order_number=NULL
                   WHERE id=$1 AND status='held' AND order_number=$2""",
                    pending_row['reservation_id'], pending_row['order_number'])
                outcome = 'rejected'
                rejected += 1
            else:
                continue
            await conn.execute("""UPDATE public.reservation_order_verification
                SET status=$2,checked_at=NOW() WHERE reservation_id=$1""",
                pending_row['reservation_id'], outcome)
            await conn.execute("""INSERT INTO public.action_audit
                (action,entity_type,entity_id,after_data)
                VALUES($1,'reservation',$2,
                       jsonb_build_object('order_number',$3::text,'status',$4::text))""",
                f'reservations.order.{outcome}',pending_row['reservation_id'],
                pending_row['order_number'],outcome)
    return {'matched': matched, 'rejected': rejected}


@router.post("/reservations/{reservation_id}/confirm")
async def confirm_manually(reservation_id: int, order_number: str | None = None, actor_user_id: int | None = None) -> Reservation:
    """Confirm a booking by hand.

    For the case the automatic matcher cannot cover: a purchase made under a
    different account, paid in cash, or comped. The order number is recorded
    when there is one so the decision is auditable, but it is not verified —
    that is the point of a manual override.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        previous = await conn.fetchrow(
            'SELECT status,order_number FROM public.reservations WHERE id=$1 FOR UPDATE', reservation_id,
        )
        row = await conn.fetchrow(
            f"""
            UPDATE public.reservations
               SET status = 'confirmed',
                   confirmed_at = CASE WHEN status='confirmed' THEN confirmed_at ELSE NOW() END,
                   order_number = COALESCE($2, order_number),
                   note = COALESCE(note || ' | ', '') || 'confirmed manually'
             WHERE id = $1 AND status IN ('held', 'expired', 'confirmed')
            RETURNING {RESERVATION_COLUMNS};
            """,
            reservation_id,
            (order_number or "").strip() or None,
        )
        if row is not None:
            await conn.execute("""DELETE FROM public.reservation_order_verification
                WHERE reservation_id=$1 AND status IN ('pending', 'rejected')""", reservation_id)
            await conn.execute("""INSERT INTO public.action_audit
                (actor_user_id,action,entity_type,entity_id,before_data,after_data)
                VALUES($1,'reservations.confirm.override','reservation',$2,
                    jsonb_build_object('status',$3::text,'order_number',$4::text),
                    jsonb_build_object('status','confirmed','order_number',$5::text))""",
                actor_user_id, reservation_id, previous['status'],
                previous['order_number'], row['order_number'])
    if row is None:
        raise HTTPException(404, "No such active reservation")
    return Reservation(**dict(row))


@router.post('/reservations/{reservation_id}/confirm-free')
async def confirm_free_hold(reservation_id: int, actor_user_id: int | None = None) -> Reservation:
    """Authorised committee confirmation of a deliberately unconfirmed free ticket.

    The API checks events.bookings.confirm. This DB safeguard ensures the free
    path cannot be used to bypass paid ticket verification.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        previous = await conn.fetchrow("""SELECT r.status,tt.requires_purchase
            FROM public.reservations r JOIN public.ticket_types tt ON tt.id=r.ticket_type_id
            WHERE r.id=$1 FOR UPDATE OF r""", reservation_id)
        if previous is None:
            raise HTTPException(404, 'No such reservation')
        if previous['requires_purchase']:
            raise HTTPException(409, 'Paid tickets need Union proof or a separately authorised override')
        if previous['status'] != 'held':
            raise HTTPException(409, 'Only held free tickets can be confirmed')
        row = await conn.fetchrow(f"""UPDATE public.reservations
            SET status='confirmed',confirmed_at=NOW()
            WHERE id=$1 RETURNING {RESERVATION_COLUMNS}""", reservation_id)
        await conn.execute("""INSERT INTO public.action_audit
            (actor_user_id,action,entity_type,entity_id,before_data,after_data)
            VALUES($1,'reservations.confirm.free','reservation',$2,
                   jsonb_build_object('status','held'),
                   jsonb_build_object('status','confirmed'))""",
            actor_user_id, reservation_id)
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
              LEFT JOIN public.reservation_order_verification ov
                ON ov.reservation_id=h.id AND ov.status='pending'
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
                    (ov.reservation_id IS NOT NULL
                         AND upper(s.order_number)=upper(ov.order_number))
                 OR (ov.reservation_id IS NULL AND (
                       (h.member_id IS NOT NULL AND h.login IS NOT NULL
                        AND lower(s.customer_login)=lower(h.login))
                    OR (h.member_id IS NOT NULL AND h.cid IS NOT NULL
                        AND s.customer_cid=h.cid)
                    OR (h.order_number IS NOT NULL
                        AND upper(s.order_number)=upper(h.order_number))
                 )))
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

        # Free bookings still waiting: a members-only one whose holder the
        # (just refreshed) feed now shows as a member this year, and any free
        # hold an older pod made mid-rollout. Nothing to match a sale to —
        # being allowed the ticket is the whole of the confirmation.
        freed = await conn.fetch(
            """
            UPDATE public.reservations r
               SET status = 'confirmed', confirmed_at = NOW()
              FROM public.ticket_types tt
             WHERE tt.id = r.ticket_type_id
               AND NOT tt.requires_purchase
               AND r.status = 'held' AND r.expires_at > NOW()
               AND (NOT tt.members_only OR EXISTS (
                      SELECT 1 FROM public.users u
                        JOIN public.members m ON m.id = u.member_id
                       WHERE u.id = r.user_id AND m.academic_year = $1))
            RETURNING r.id;
            """,
            academic_year,
        )

    return {
        # Free ones included, so callers that trigger the confirmation email on
        # "anything confirmed" do so for these too.
        "confirmed": confirmed + len(freed),
        "confirmed_free": len(freed),
        "candidates": len(candidates),
    }


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
            "SELECT id, capacity, reservations_close_at FROM public.events "
            "WHERE id = $1 FOR UPDATE;",
            req.event_id,
        )
        if event is None:
            raise HTTPException(404, "No such event")
        if _reservations_closed(event):
            return ReservationResult(ok=False, reason="reservations_closed")

        tt = await conn.fetchrow(
            f"SELECT {TT_COLUMNS} FROM public.ticket_types WHERE id = $1 AND event_id = $2;",
            req.ticket_type_id, req.event_id,
        )
        if tt is None:
            raise HTTPException(404, "No such ticket type for this event")
        if _type_off_sale(tt):
            return ReservationResult(ok=False, reason="type_not_on_sale")

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

        # Checked here as well as at claim so a browser that has already booked
        # is told before typing its details in, not after.
        if req.visitor_hash:
            mine = await conn.fetchval(
                MINE_OF_TYPE, req.ticket_type_id, None, req.visitor_hash, 0
            )
            if mine + req.quantity > tt["max_per_user"]:
                return ReservationResult(ok=False, reason="over_user_limit")

        row = await conn.fetchrow(
            f"""
            INSERT INTO public.reservations
                (event_id, ticket_type_id, user_id, quantity, status, expires_at,
                 booked_as_guest, draft_token, visitor_hash)
            VALUES ($1, $2, NULL, $3, 'draft', NOW() + make_interval(mins => $4), true, $5, $6)
            RETURNING {RESERVATION_COLUMNS};
            """,
            req.event_id, req.ticket_type_id, req.quantity, minutes, req.draft_token,
            req.visitor_hash,
        )
    return ReservationResult(ok=True, reservation=Reservation(**dict(row)))


@router.post("/reservations/draft/{draft_token}/claim")
async def claim_draft(
    draft_token: str,
    user_id: int,
    hold_hours: int | None = None,
    visitor_hash: str | None = None,
    academic_year: str | None = None,
) -> ReservationResult:
    """Turn a draft into a real booking once the guest form is submitted —
    confirmed outright for a free ticket, a hold for anything to be paid for.

    Keyed on the token rather than the row id, so one visitor cannot claim
    another's draft by guessing a number.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        draft = await conn.fetchrow(
            """
            SELECT r.id, r.event_id, r.ticket_type_id, r.quantity, r.expires_at,
                   r.status, r.visitor_hash, e.hold_hours, e.starts_at, tt.max_per_user,
                   tt.requires_purchase, tt.members_only, e.reservations_close_at,
                   tt.sales_open_at, tt.sales_close_at
              FROM public.reservations r
              JOIN public.events e        ON e.id  = r.event_id
              JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
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
        if _reservations_closed(draft):
            # Booking closed while they filled the form in. The seat goes back.
            await conn.execute("DELETE FROM public.reservations WHERE id = $1;", draft["id"])
            return ReservationResult(ok=False, reason="reservations_closed")
        if _type_off_sale(draft):
            # The same, for this ticket type alone: early bird ended mid-form.
            await conn.execute("DELETE FROM public.reservations WHERE id = $1;", draft["id"])
            return ReservationResult(ok=False, reason="type_not_on_sale")

        # The per-person limit, which this path used to skip entirely: a guest
        # could submit the form again and again and hold a ticket each time.
        # Counted against the account the email resolved to AND the browser.
        vh = visitor_hash or draft["visitor_hash"]
        mine = await conn.fetchval(
            MINE_OF_TYPE, draft["ticket_type_id"], user_id, vh, draft["id"]
        )
        if mine + draft["quantity"] > draft["max_per_user"]:
            # Give the seat straight back rather than leave it parked.
            await conn.execute(
                "DELETE FROM public.reservations WHERE id = $1;", draft["id"]
            )
            return ReservationResult(ok=False, reason="over_user_limit")

        # Same rule as `reserve`: a free ticket is a place outright ($8).
        confirm = await _confirms_outright(conn, draft, user_id, academic_year)
        expires = BOOKING_EXPIRES.format(
            confirm="$8", paid="$9", tt="$4", ev="$5", hours="$3", starts="$6::timestamptz"
        )
        row = await conn.fetchrow(
            f"""
            UPDATE public.reservations
               SET user_id = $2, draft_token = NULL,
                   status = CASE WHEN $8::boolean THEN 'confirmed' ELSE 'held' END,
                   visitor_hash = COALESCE(visitor_hash, $7),
                   expires_at = {expires},
                   confirmed_at = CASE WHEN $8::boolean THEN NOW() END
             WHERE id = $1
            RETURNING {RESERVATION_COLUMNS};
            """,
            draft["id"], user_id, hold_hours or draft["hold_hours"],
            draft["ticket_type_id"], draft["event_id"], draft["starts_at"], vh,
            confirm, draft["requires_purchase"],
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
async def promote_waitlist(academic_year: str | None = None) -> dict:
    """Offer freed seats to the front of each queue.

    Called after anything that can free capacity — a cancellation, the expiry
    sweep, the nightly reconcile. Oldest waiter first, stopping as soon as the
    event is full again.

    On a free ticket the "offer" is simply the place: confirmed and claimed at
    once (see `_confirms_outright`), and the caller sends the ordinary
    confirmation email rather than a "buy it by Thursday" offer. Before, a free
    offer was a hold that nothing could ever confirm, so it lapsed after the
    event's hold time and the place went to the next person in the queue.
    `academic_year` lets a free members-only ticket check membership; without
    it such an offer waits, held, until the event.
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
                    "SELECT id, capacity, hold_hours, starts_at, reservations_close_at "
                    "FROM public.events WHERE id=$1 FOR UPDATE;",
                    event_id,
                )
                # Booking has closed: no more offers, the queue just stops.
                if event is not None and _reservations_closed(event):
                    continue
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
                    # be too. Only a type on sale right now (080): an early-bird
                    # place freed after early bird ended is not offered.
                    ticket = await conn.fetchrow(
                        f"""
                        SELECT tt.id, tt.requires_purchase, tt.members_only
                          FROM public.ticket_types tt
                          LEFT JOIN (
                                SELECT ticket_type_id, SUM(quantity) AS taken
                                  FROM public.reservations
                                 WHERE event_id = $1 AND {LIVE}
                                 GROUP BY ticket_type_id
                               ) live ON live.ticket_type_id = tt.id
                         WHERE tt.event_id = $1
                           AND (tt.sales_open_at IS NULL OR tt.sales_open_at <= NOW())
                           AND (tt.sales_close_at IS NULL OR tt.sales_close_at > NOW())
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

                    confirm = await _confirms_outright(
                        conn, ticket, waiter["user_id"], academic_year
                    )
                    reservation = await conn.fetchrow(
                        f"""
                        INSERT INTO public.reservations
                            (event_id, ticket_type_id, user_id, quantity, status,
                             expires_at, confirmed_at, note)
                        VALUES ($1,$2,$3,$4,
                                CASE WHEN $6::boolean THEN 'confirmed' ELSE 'held' END,
                                CASE WHEN $6::boolean THEN NOW()
                                     WHEN NOT $7::boolean
                                         THEN GREATEST($8::timestamptz,
                                                       NOW() + make_interval(hours => $5))
                                     -- An offer is a hold like any other: the
                                     -- same end-of-day deadline, capped by the
                                     -- Union close.
                                     ELSE {HOLD_EXPIRES.format(tt="$2", ev="$1", hours="$5", starts="$8::timestamptz")} END,
                                CASE WHEN $6::boolean THEN NOW() END,
                                'from waitlist')
                        RETURNING {RESERVATION_COLUMNS};
                        """,
                        event_id, ticket["id"], waiter["user_id"], waiter["quantity"],
                        event["hold_hours"], confirm, ticket["requires_purchase"],
                        event["starts_at"],
                    )
                    # A confirmed place is claimed the moment it is offered, so
                    # the release sweep above never hands it to somebody else.
                    await conn.execute(
                        """
                        UPDATE public.waitlist_entries
                           SET offered_at = NOW(), reservation_id = $3,
                               -- Cast: inside a CASE Postgres cannot infer a
                               -- parameter's type and takes it as text.
                               offer_expires_at = CASE WHEN $4 THEN NULL ELSE $2::timestamptz END,
                               claimed_at = CASE WHEN $4 THEN NOW() END
                         WHERE id = $1;
                        """,
                        waiter["id"], reservation["expires_at"], reservation["id"], confirm,
                    )
                    offered.append({
                        "waitlist_id": waiter["id"],
                        "user_id": waiter["user_id"],
                        "event_id": event_id,
                        "reservation_id": reservation["id"],
                        "expires_at": reservation["expires_at"].isoformat(),
                        "confirmed": confirm,
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
