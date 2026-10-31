"""Tickets and door check-in.

Two audiences:

  * the ticket holder — `GET /tickets/{token}` and its QR image, public
    because the token is the credential (see services/tickets.py);
  * the committee on the door — any committee member, role or not, because
    door duty falls to whoever is free. They scan a ticket or find a name and
    let the person in. Every device on the door polls the same state, so a
    person let in on one phone shows as in on the others within a couple of
    seconds, and a second scan of the same ticket says who got there first.

Nothing here talks to the Union.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from clients import database
from models.auth import AuthSession
from services import tickets
from utils.deps import require_action, assert_event_scope, current_session, require_committee, verify_csrf
from utils.present import format_date, format_time_range

log = logging.getLogger("api.checkin")

router = APIRouter()


def _dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _name(a: dict) -> str:
    name = " ".join(p for p in [a.get("first_name"), a.get("last_name")] if p)
    return name or a.get("email") or "Unknown"


def _who(a: dict) -> str | None:
    return a.get("checked_by_first_name") or a.get("checked_by_email")


def _attendee(a: dict) -> dict:
    return {
        "id": a["id"],
        "eventId": a.get("event_id"),
        "name": _name(a),
        "email": a.get("email"),
        "shortcode": a.get("shortcode"),
        "ticketType": a.get("ticket_type_name"),
        "quantity": a.get("quantity") or 1,
        "status": a.get("status"),
        "paid": bool(a.get("paid")),
        "guest": bool(a.get("booked_as_guest")),
        "committeeAdded": a.get("source") == "committee",
        "orderNumber": a.get("order_number"),
        "checkedInAt": a.get("checked_in_at"),
        "checkedInBy": _who(a),
        "method": a.get("checkin_method"),
    }


def _verdict(
    a: dict, event_id: int | None, event_ids: list[int] | None = None
) -> tuple[str, str]:
    """Whether this booking should come in to this event, and why not.

    Ordered by what the person on the door needs to know first: the wrong
    event beats everything, a dead booking beats an unpaid one.
    """
    if (event_ids is not None and a["event_id"] not in event_ids) or (
        event_ids is None and event_id is not None and a["event_id"] != event_id
    ):
        when = format_date(_dt(a.get("event_starts_at")))
        return "wrong_event", f"This ticket is for {a.get('event_title')} ({when})."
    if a["status"] == "cancelled":
        return "invalid", "This booking was cancelled."
    if a["status"] == "expired" or (a["status"] == "held" and not a.get("unexpired")):
        return "invalid", "This booking expired without being paid for."
    if not a.get("paid"):
        return "unpaid", "Not paid — no Union purchase has been matched to this booking."
    return "ok", ""


async def _booking(reservation_id: int) -> dict:
    row = await database.get(f"/checkin/reservations/{reservation_id}")
    if row is None:
        raise HTTPException(404, "No booking matches that ticket.")
    return row


async def _check_scope(session: AuthSession, event_id: int) -> None:
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_event_scope(session, event)


def validate_event_ids(event_ids: list[int]) -> list[int]:
    """Bound and validate a multi-event door selection before any private read."""
    if not 1 <= len(event_ids) <= 20 or any(e <= 0 for e in event_ids) or len(set(event_ids)) != len(event_ids):
        raise HTTPException(422, "Select between 1 and 20 distinct events.")
    return event_ids


def _count(attendees: list[dict]) -> dict:
    live = [a for a in attendees if a["status"] in ("confirmed", "held")]
    return {
        "booked": sum(a["quantity"] for a in live),
        "in": sum(a["quantity"] for a in attendees if a["checkedInAt"]),
    }


# ------------------------------------------------------------ the door


@router.get("/checkin/events", dependencies=[Depends(require_action("events.checkin.view"))])
async def checkin_events(_: AuthSession = Depends(require_committee)) -> dict:
    """Events from yesterday onward, soonest first, with live counts."""
    rows = await database.get("/checkin/events", {"days_back": 1, "days_ahead": 60}) or []
    if not _.can("events.scope.all"):
        rows = [event for event in rows if event.get("industry") and _.can("events.scope.industry")]
    today = datetime.now(timezone.utc).date()
    return {
        "events": [
            {
                "id": e["id"],
                "title": e["title"],
                "date": format_date(_dt(e.get("starts_at"))),
                "time": format_time_range(_dt(e.get("starts_at")), _dt(e.get("ends_at"))),
                "venue": e.get("venue"),
                "published": e.get("published"),
                "booked": e.get("booked", 0),
                "checkedIn": e.get("checked_in", 0),
                "today": bool(_dt(e.get("starts_at")) and _dt(e["starts_at"]).date() == today),
            }
            for e in rows
        ]
    }


@router.get("/checkin/events/{event_id}", dependencies=[Depends(require_action("events.checkin.view"))])
async def checkin_event(event_id: int, _: AuthSession = Depends(require_committee)) -> dict:
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_event_scope(_, event)
    attendees = [_attendee(a) for a in await database.get(f"/checkin/events/{event_id}/attendees") or []]
    await _check_scope(_, event_id)
    state = await database.get(f"/checkin/events/{event_id}/state") or {}
    return {
        "event": {
            "id": event["id"],
            "title": event["title"],
            "date": format_date(_dt(event.get("starts_at"))),
            "time": format_time_range(_dt(event.get("starts_at")), _dt(event.get("ends_at"))),
            "venue": event.get("venue"),
        },
        "attendees": attendees,
        "count": _count(attendees),
        "bookings": state.get("bookings"),
    }


@router.get("/checkin/events/{event_id}/state", dependencies=[Depends(require_action("events.checkin.view"))])
async def checkin_state(event_id: int, _: AuthSession = Depends(require_committee)) -> dict:
    """What every door screen polls. `bookings` changes when somebody books,
    cancels or is confirmed, which is the screen's cue to refetch the list."""
    state = await database.get(f"/checkin/events/{event_id}/state") or {}
    return {
        "checkins": [
            {
                "id": c["reservation_id"],
                "at": c["checked_in_at"],
                "by": _who(c),
                "method": c.get("method"),
            }
            for c in state.get("checkins", [])
        ],
        "bookings": state.get("bookings"),
        "serverTime": datetime.now(timezone.utc).isoformat(),
    }


def _result(row: dict, outcome: str, message: str) -> dict:
    return {"outcome": outcome, "message": message, "attendee": _attendee(row)}


@router.post("/checkin/scan", dependencies=[Depends(require_action("events.checkin.scan")), Depends(verify_csrf)])
async def scan(
    token: str = Body(embed=True),
    event_id: int | None = Body(default=None, embed=True),
    event_ids: list[int] | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_committee),
) -> dict:
    """A scanned ticket: let it in if it belongs here and is good.

    Outcomes the screen colours: `checked_in` (green), `already` (amber —
    with who and when), `unpaid` / `invalid` (red, with Let in anyway),
    `wrong_event` (red, no override — it is somebody else's event).
    """
    if event_ids is not None:
        if event_id is not None:
            raise HTTPException(422, "Use either event_id or event_ids, not both.")
        validate_event_ids(event_ids)
        # Check *every* selected event, not only the ticket being scanned. A
        # client must not be able to enlarge a scoped officer's door selection.
        for selected_id in event_ids:
            await _check_scope(session, selected_id)
    row = await _booking(tickets.parse(token))
    await _check_scope(session, row["event_id"])
    verdict, message = _verdict(row, event_id, event_ids)
    if verdict != "ok":
        return _result(row, verdict, message)
    if row.get("checked_in_at"):
        return _result(row, "already", _already(row))
    done = await database.post(
        f"/checkin/reservations/{row['id']}",
        params={"method": "scan", "checked_by": session.user.id},
    )
    if not done["created"]:
        # Another phone got there between our read and our write.
        return _result(done, "already", _already(done))
    return _result(done, "checked_in", "")


def _already(row: dict) -> str:
    at = _dt(row.get("checked_in_at"))
    when = format_time_range(at, None) if at else "earlier"
    by = _who(row)
    return f"Already checked in at {when}" + (f" by {by}." if by else ".")


@router.post("/checkin/reservations/{reservation_id}", dependencies=[Depends(require_action("events.checkin.manual")), Depends(verify_csrf)])
async def check_in(
    reservation_id: int,
    event_id: int | None = Body(default=None, embed=True),
    override: bool = Body(default=False, embed=True),
    session: AuthSession = Depends(require_committee),
) -> dict:
    """Let somebody in from the list, or after a scan said no.

    An unpaid or lapsed booking needs `override` — the screen asks first —
    and is recorded as such. A ticket for another event is never let in here.
    """
    row = await _booking(reservation_id)
    await _check_scope(session, row["event_id"])
    if override and not session.can("events.checkin.override"):
        raise HTTPException(403, "Permission required: events.checkin.override")
    verdict, message = _verdict(row, event_id)
    if verdict == "wrong_event":
        raise HTTPException(409, message)
    if verdict != "ok" and not override:
        raise HTTPException(409, message)
    done = await database.post(
        f"/checkin/reservations/{reservation_id}",
        params={
            "method": "manual" if verdict == "ok" else "override",
            "checked_by": session.user.id,
        },
    )
    if not done["created"]:
        return _result(done, "already", _already(done))
    if verdict != "ok":
        log.warning(
            "Booking %s let in despite %s by %s", reservation_id, verdict, session.user.email
        )
    return _result(done, "checked_in", "")


@router.delete("/checkin/reservations/{reservation_id}", dependencies=[Depends(require_action("events.checkin.undo")), Depends(verify_csrf)])
async def undo_check_in(
    reservation_id: int, session: AuthSession = Depends(require_committee)
) -> dict:
    row = await _booking(reservation_id)
    await _check_scope(session, row["event_id"])
    await database.delete(f"/checkin/reservations/{reservation_id}")
    log.info("Check-in for booking %s undone by %s", reservation_id, session.user.email)
    return {"ok": True}


# ------------------------------------------------------------ the holder


STATUS_LABEL = {
    "confirmed": "Confirmed",
    "held": "Booked — not yet paid",
    "cancelled": "Cancelled",
    "expired": "Expired",
}


@router.get("/tickets/{token}")
async def ticket(
    token: str, session: AuthSession | None = Depends(current_session)
) -> dict:
    """A ticket, for whoever holds its link. A committee member sees a Check
    in button on the page; nobody else is told anything about the door."""
    row = await _booking(tickets.parse(token))
    free_hold = row["status"] == "held" and row.get("paid")
    may_check_in = False
    if session is not None:
        if session.can("events.checkin.manual"):
            event = await database.get(f"/events/{row['event_id']}")
            may_check_in = session.can("events.scope.all") or (
                session.can("events.scope.industry") and bool((event or {}).get("industry"))
            )
    return {
        "token": token,
        "event": {
            "id": row["event_id"],
            "title": row.get("event_title"),
            "slug": row.get("event_slug"),
            "date": format_date(_dt(row.get("event_starts_at"))),
            "time": format_time_range(_dt(row.get("event_starts_at")), None),
            "venue": row.get("event_venue"),
        },
        "holder": _name(row),
        "ticketType": row.get("ticket_type_name"),
        "quantity": row.get("quantity") or 1,
        "status": row["status"],
        "statusLabel": "Booked" if free_hold else STATUS_LABEL.get(row["status"], row["status"]),
        "valid": _verdict(row, None)[0] == "ok",
        "checkedInAt": row.get("checked_in_at"),
        "qrUrl": f"/api/qr/{token}.png",
        "mayCheckIn": may_check_in,
    }


@router.get("/qr/{token}.png")
async def qr_png(token: str) -> Response:
    """The QR for a ticket. Verified first, so this never draws a code for
    something we did not issue. Immutable: a token never changes."""
    tickets.parse(token)
    return Response(
        content=tickets.render_qr(token),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
