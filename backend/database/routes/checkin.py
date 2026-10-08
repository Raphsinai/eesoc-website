"""Door check-in: who is booked for an event and who has come in.

A booking counts at the door if it is confirmed, or still a live hold — free
tickets stay held until the event, and an unpaid hold is shown so the
committee can see it and decide. Whether it is paid is a separate flag.
"""

from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from postgres import get_pool

router = APIRouter(prefix="/checkin")

LIVE = "(r.status = 'confirmed' OR (r.status = 'held' AND r.expires_at > NOW()))"

ATTENDEE_COLUMNS = """
    r.id, r.event_id, r.quantity, r.status, r.expires_at, r.booked_as_guest,
    r.source, r.order_number,
    (r.status = 'confirmed' OR NOT tt.requires_purchase) AS paid,
    tt.name AS ticket_type_name, tt.requires_purchase,
    u.email, u.first_name, u.last_name, u.shortcode,
    c.checked_in_at, c.method AS checkin_method,
    cb.email AS checked_by_email, cb.first_name AS checked_by_first_name
"""

ATTENDEE_FROM = """
      FROM public.reservations r
      JOIN public.ticket_types tt ON tt.id = r.ticket_type_id
      JOIN public.users u         ON u.id = r.user_id
      LEFT JOIN public.reservation_checkins c ON c.reservation_id = r.id
      LEFT JOIN public.users cb   ON cb.id = c.checked_by
"""


@router.get("/events")
async def events(days_back: int = 1, days_ahead: int = 60) -> list[dict]:
    """Events around now, with how many are booked and how many are in."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT e.id, e.slug, e.title, e.starts_at, e.ends_at, e.venue, e.published,
                   COALESCE(SUM(r.quantity) FILTER (WHERE {LIVE}), 0)            AS booked,
                   COALESCE(SUM(r.quantity) FILTER (WHERE c.reservation_id IS NOT NULL), 0)
                                                                                 AS checked_in
              FROM public.events e
              LEFT JOIN public.reservations r ON r.event_id = e.id
              LEFT JOIN public.reservation_checkins c ON c.reservation_id = r.id
             WHERE e.starts_at >= NOW() - make_interval(days => $1)
               AND e.starts_at <= NOW() + make_interval(days => $2)
             GROUP BY e.id
             ORDER BY e.starts_at;
            """,
            days_back,
            days_ahead,
        )
    return [dict(r) for r in rows]


@router.get("/events/{event_id}/attendees")
async def attendees(event_id: int) -> list[dict]:
    """Everybody who can come in, plus anybody already let in whose booking has
    since lapsed — the door should not lose track of a person it admitted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {ATTENDEE_COLUMNS}
            {ATTENDEE_FROM}
             WHERE r.event_id = $1 AND ({LIVE} OR c.reservation_id IS NOT NULL)
             ORDER BY lower(COALESCE(u.last_name, u.first_name, u.email)),
                      lower(COALESCE(u.first_name, '')), r.id;
            """,
            event_id,
        )
    return [dict(r) for r in rows]


@router.get("/events/{event_id}/state")
async def state(event_id: int) -> dict:
    """What a door screen polls: every check-in, plus a fingerprint of the
    bookings so the screen knows when to refetch the attendee list.

    Small on purpose — a few hundred rows of ids and times, every couple of
    seconds, from every phone on the door.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        checkins = await conn.fetch(
            """
            SELECT c.reservation_id, c.checked_in_at, c.method,
                   cb.email AS checked_by_email, cb.first_name AS checked_by_first_name
              FROM public.reservation_checkins c
              LEFT JOIN public.users cb ON cb.id = c.checked_by
             WHERE c.event_id = $1;
            """,
            event_id,
        )
        fingerprint = await conn.fetchval(
            f"""
            SELECT COUNT(*) || ':' || COALESCE(SUM(r.id), 0) || ':'
                   || COALESCE(SUM(r.quantity), 0) || ':'
                   || COALESCE(SUM(r.ticket_type_id), 0) || ':'
                   || COUNT(*) FILTER (WHERE r.status = 'confirmed')
              FROM public.reservations r
             WHERE r.event_id = $1 AND {LIVE};
            """,
            event_id,
        )
    return {"checkins": [dict(c) for c in checkins], "bookings": fingerprint}


@router.get("/reservations/{reservation_id}")
async def reservation(reservation_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {ATTENDEE_COLUMNS},
                   r.expires_at > NOW() AS unexpired,
                   e.title AS event_title, e.slug AS event_slug,
                   e.starts_at AS event_starts_at, e.venue AS event_venue
            {ATTENDEE_FROM}
              JOIN public.events e ON e.id = r.event_id
             WHERE r.id = $1;
            """,
            reservation_id,
        )
    if row is None:
        raise HTTPException(404, "No such booking")
    return dict(row)


@router.post("/reservations/{reservation_id}")
async def check_in(reservation_id: int, method: str = "scan", checked_by: int | None = None) -> dict:
    """Let a booking in. Idempotent: a second scan creates nothing and reports
    the first check-in, so two phones on the door cannot double-count."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        created = await conn.fetchval(
            """
            INSERT INTO public.reservation_checkins (reservation_id, event_id, method, checked_by)
            SELECT r.id, r.event_id, $2, $3 FROM public.reservations r WHERE r.id = $1
            ON CONFLICT (reservation_id) DO NOTHING
            RETURNING true;
            """,
            reservation_id,
            method,
            checked_by,
        )
    return {"created": bool(created), **(await reservation(reservation_id))}


@router.delete("/reservations/{reservation_id}", status_code=204)
async def undo(reservation_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM public.reservation_checkins WHERE reservation_id = $1;",
            reservation_id,
        )
