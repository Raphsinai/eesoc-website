"""Public event listing and the reservation flow.

Booking is reservation-style: payment happens on the Union shop and the sales
feed only refreshes at 06:00, so a booking is a hold that gets promoted to
confirmed once a matching sale appears. Members are matched automatically on
their ICU login; external users type their order number in.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from clients import database
from config import settings
from models.auth import AuthSession, GuestDetails
from services import sync as sync_service
from services.identity import is_imperial, resolve_or_create_user
from utils import email as mailer
from utils.auth import generate_login_code, generate_token, hash_token
from utils.deps import (
    current_session,
    require_feature,
    require_session,
    verify_csrf,
)
from utils.present import event_card, format_price
from utils.year import academic_year

log = logging.getLogger("api.events")

router = APIRouter()

# Why a hold failed, in words a member can act on.
REASONS = {
    "sold_out": "This event is full.",
    "type_sold_out": "That ticket type has sold out.",
    "over_user_limit": "You already hold the maximum number of tickets for that type.",
}


def _ticket_view(tt: dict, is_member: bool) -> dict:
    return {
        "id": tt["id"],
        "name": tt["name"],
        "description": tt.get("description"),
        "price": format_price(tt.get("price_pence", 0)),
        "pricePence": tt.get("price_pence", 0),
        "capacity": tt.get("capacity"),
        "taken": tt.get("taken", 0),
        "remaining": tt.get("remaining"),
        "soldOut": bool(tt.get("sold_out")),
        "membersOnly": bool(tt.get("members_only")),
        "maxPerUser": tt.get("max_per_user", 1),
        "requiresPurchase": bool(tt.get("requires_purchase", True)),
        # Precomputed so the dropdown can disable an option without duplicating
        # the eligibility rule in the client.
        # Selectable even when members-only and we cannot see a membership:
        # eligibility is confirmed later, so the option stays open and the UI
        # shows a warning instead of a locked dropdown.
        "selectable": not tt.get("sold_out"),
        "needsMembership": bool(tt.get("members_only")) and not is_member,
    }


@router.get("/events")
async def list_events(
    upcoming: bool = True,
    limit: int = 100,
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """The calendar. Admins additionally see unpublished drafts."""
    is_admin = bool(session and session.is_admin)
    events = await database.get(
        "/events",
        {"published_only": not is_admin, "upcoming": upcoming, "limit": limit},
    ) or []
    return {"count": len(events), "events": [event_card(e) for e in events]}


@router.get("/events/{slug}")
async def get_event(
    slug: str, session: AuthSession | None = Depends(current_session)
) -> dict:
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None:
        raise HTTPException(404, "No such event")

    is_admin = bool(session and session.is_admin)
    if not event["published"] and not is_admin:
        raise HTTPException(404, "No such event")

    year = academic_year()
    is_member = bool(
        session and session.member and session.member.academic_year == year
    )

    mine = []
    if session:
        mine = await database.get(
            "/reservations", {"user_id": session.user.id, "event_id": event["id"]}
        ) or []

    card = event_card(event)
    card.update(
        {
            "description": event.get("description", ""),
            "capacity": event.get("capacity"),
            "remaining": event.get("remaining"),
            "holdHours": event.get("hold_hours"),
            "ticketTypes": [
                _ticket_view(t, is_member) for t in event.get("ticket_types", [])
            ],
            "myReservations": [
                {
                    "id": r["id"],
                    "ticketTypeId": r["ticket_type_id"],
                    "ticketTypeName": r.get("ticket_type_name"),
                    "quantity": r["quantity"],
                    "status": r["status"],
                    "expiresAt": r.get("expires_at"),
                    "orderNumber": r.get("order_number"),
                    "requiresPurchase": r.get("requires_purchase", True),
                }
                for r in mine
                if r["status"] in ("held", "confirmed")
            ],
        }
    )
    return card


@router.post(
    "/events/{slug}/reserve",
    dependencies=[Depends(verify_csrf), require_feature("bookings")],
)
async def reserve(
    slug: str,
    ticket_type_id: int = Body(embed=True),
    quantity: int = Body(default=1, embed=True),
    order_number: str | None = Body(default=None, embed=True),
    guest: GuestDetails | None = Body(default=None, embed=True),
    draft_token: str | None = Body(default=None, embed=True),
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Hold tickets.

    Open to people who are not signed in: they supply an email and, if they are
    an Imperial student, their shortcode. An unverified account is created to own
    the booking, and the hold is deliberately shorter than a signed-in one —
    long enough to finish paying, not long enough to squat on a seat nobody has
    proved they want.
    """
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None or not event["published"]:
        raise HTTPException(404, "No such event")

    ticket = next(
        (t for t in event.get("ticket_types", []) if t["id"] == ticket_type_id), None
    )
    if ticket is None:
        raise HTTPException(404, "No such ticket type for this event")

    if quantity < 1:
        raise HTTPException(400, "Quantity must be at least 1.")

    as_guest = session is None
    if as_guest:
        if guest is None:
            raise HTTPException(400, "Tell us your email so we can hold the ticket.")
        # An Imperial address without a shortcode is almost certainly a mistake,
        # and the shortcode is what lets us match their purchase automatically.
        if is_imperial(str(guest.email)) and not guest.shortcode:
            raise HTTPException(
                400,
                "Imperial students: add your shortcode (the bit before @ic.ac.uk) "
                "so we can match your Union purchase.",
            )
        user = await resolve_or_create_user(
            str(guest.email),
            guest.first_name,
            guest.last_name,
            shortcode=guest.shortcode,
            verified=False,
        )
        user_id = user["id"]
        is_member = user.get("member_id") is not None
    else:
        user_id = session.user.id
        is_member = session.is_member_now

    # Members-only tickets are NOT refused here. Somebody who joined EESoc today
    # does not appear in the members feed until the next morning's sync, and
    # turning them away would be wrong. The booking is taken and only *confirmed*
    # once the feed shows they are a member this year — see the matcher.
    membership_warning = None
    if ticket["members_only"] and not is_member:
        membership_warning = (
            "This ticket is for Union members only. We can't see an EESoc "
            "membership against you yet, so we'll hold your place and check "
            "again after tonight's sync. If you've only just joined, that's "
            "expected. If you haven't, join on the Union shop or this booking "
            "won't be confirmed."
        )

    # A guest already holds the seat from the moment they opened the form, so
    # this converts that draft rather than competing for capacity a second time.
    if draft_token:
        result = await database.post(
            f"/reservations/draft/{draft_token}/claim",
            params={"user_id": user_id},
        )
        if not result["ok"] and result["reason"] == "draft_expired":
            raise HTTPException(
                409,
                "Your 15 minutes ran out and the ticket went back on sale. "
                "Please try again.",
            )
    else:
        result = await database.post(
            "/reservations",
            {
                "event_id": event["id"],
                "ticket_type_id": ticket_type_id,
                "user_id": user_id,
                "quantity": quantity,
                "order_number": (order_number or "").strip() or None,
                "booked_as_guest": as_guest,
                # A guest who has not proved the address gets the shorter
                # window; a signed-in user gets the event's own.
                "hold_hours_override": settings.guest_hold_hours if as_guest else None,
            },
        )

    if not result["ok"]:
        # 409, not 400: the request was valid, the world changed under it.
        raise HTTPException(409, REASONS.get(result["reason"], "Could not reserve."))

    reservation = result["reservation"]

    # Guests get a code so they can come back, see the booking and confirm the
    # address is really theirs. Sending it here rather than making them ask
    # means one email covers both.
    if as_guest:
        await _send_guest_code(str(guest.email))

    return {
        "ok": True,
        "guest": as_guest,
        "reservation": {
            "id": reservation["id"],
            "status": reservation["status"],
            "quantity": reservation["quantity"],
            "expiresAt": reservation["expires_at"],
        },
        "next": _next_step(ticket, is_member, as_guest),
        "membershipWarning": membership_warning,
    }


async def _send_guest_code(address: str) -> None:
    """Issue a sign-in code to a guest who has just booked.

    Failures are swallowed: the hold already exists and is the thing that
    mattered, so a mail outage must not turn a successful booking into a 500.
    """
    from datetime import datetime, timedelta, timezone

    try:
        code = generate_login_code()
        expires = datetime.now(timezone.utc) + timedelta(
            minutes=settings.login_code_ttl_minutes
        )
        await database.post(
            "/auth/login-codes",
            {
                "email": address,
                "code_hash": hash_token(code),
                "expires_at": expires.isoformat(),
            },
        )
        await mailer.send_login_code(address, code, settings.login_code_ttl_minutes)
    except Exception:  # noqa: BLE001
        log.exception("Could not send guest sign-in code to %s", address)


def _next_step(ticket: dict, is_member: bool, as_guest: bool = False) -> dict:
    if not ticket.get("requires_purchase", True):
        return {"action": "none", "message": "You're on the list. Nothing to pay."}
    if as_guest and is_member:
        return {
            "action": "buy",
            "message": (
                "Held for you. Buy the ticket on the Union shop with the same "
                "Imperial account and we'll confirm it automatically. We've "
                "emailed you a code so you can check on it."
            ),
        }
    if as_guest:
        return {
            "action": "buy_and_supply_order",
            "message": (
                "Held for you. Buy the ticket on the Union shop, then use the "
                "code we've just emailed to sign in and enter your order number."
            ),
        }
    if is_member:
        return {
            "action": "buy",
            "message": (
                "Buy the ticket on the Union shop with the same Imperial account. "
                "We check purchases each morning and confirm your place "
                "automatically — no need to send us anything."
            ),
        }
    return {
        "action": "buy_and_supply_order",
        "message": (
            "Buy the ticket on the Union shop, then come back and enter your "
            "order number so we can match it to this booking."
        ),
    }


@router.get("/me/reservations")
async def my_reservations(session: AuthSession = Depends(require_session)) -> dict:
    rows = await database.get("/reservations", {"user_id": session.user.id}) or []
    return {
        "reservations": [
            {
                "id": r["id"],
                "eventTitle": r.get("event_title"),
                "eventSlug": r.get("event_slug"),
                "startsAt": r.get("event_starts_at"),
                "ticketType": r.get("ticket_type_name"),
                "price": format_price(r.get("price_pence", 0)),
                "quantity": r["quantity"],
                "status": r["status"],
                "expiresAt": r.get("expires_at"),
                "orderNumber": r.get("order_number"),
                "needsOrderNumber": (
                    r["status"] == "held"
                    and r.get("requires_purchase", True)
                    and session.user.member_id is None
                    and not r.get("order_number")
                ),
            }
            for r in rows
        ]
    }


@router.post("/reservations/{reservation_id}/cancel", dependencies=[Depends(verify_csrf)])
async def cancel(
    reservation_id: int, session: AuthSession = Depends(require_session)
) -> dict:
    await _own_reservation(reservation_id, session)
    await database.post(f"/reservations/{reservation_id}/cancel")
    # The seat is free now, so offer it now rather than waiting for the nightly
    # pass — a waitlisted person should not lose a day to somebody else's change
    # of mind.
    promoted = await database.post("/waitlist/promote")
    await sync_service.notify_waitlist_offers((promoted or {}).get("offered", []))
    return {"ok": True}


@router.patch("/reservations/{reservation_id}/order-number", dependencies=[Depends(verify_csrf)])
async def set_order_number(
    reservation_id: int,
    order_number: str = Body(embed=True),
    session: AuthSession = Depends(require_session),
) -> dict:
    """External users supply the order number from their Union receipt.

    Order numbers contain letters, so this is text and is compared
    case-insensitively when matching. The number is not verified here — the
    nightly matcher decides, because the sale may not have synced yet.
    """
    await _own_reservation(reservation_id, session)
    cleaned = order_number.strip()
    if not cleaned:
        raise HTTPException(400, "Enter the order number from your Union receipt.")

    await database.patch(
        f"/reservations/{reservation_id}/order-number",
        params={"order_number": cleaned},
    )
    # Try immediately: if the sale is already synced this confirms on the spot
    # rather than making them wait until tomorrow morning.
    await database.post(
        "/reservations/match", params={"academic_year": academic_year()}
    )
    updated = await database.get(f"/reservations/{reservation_id}")
    return {
        "ok": True,
        "status": updated["status"],
        "message": (
            "Confirmed — that order matched."
            if updated["status"] == "confirmed"
            else "Saved. We check new purchases each morning and will confirm then."
        ),
    }


async def _own_reservation(reservation_id: int, session: AuthSession) -> dict:
    reservation = await database.get(f"/reservations/{reservation_id}")
    if reservation is None:
        raise HTTPException(404, "No such reservation")
    if reservation["user_id"] != session.user.id and not session.is_admin:
        # 404 rather than 403 — do not confirm that somebody else's booking id
        # exists.
        raise HTTPException(404, "No such reservation")
    return reservation


# --------------------------------------------------------------- draft holds


@router.post("/events/{slug}/hold", dependencies=[require_feature("bookings")])
async def hold_seat(
    slug: str,
    ticket_type_id: int = Body(embed=True),
    quantity: int = Body(default=1, embed=True),
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Reserve the seat the moment somebody presses Reserve.

    A guest has to type their details in, and that takes time; without this the
    last ticket could be sold to somebody else mid-form. The draft counts
    against capacity immediately and expires in 15 minutes, which the page
    counts down.
    """
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None or not event["published"]:
        raise HTTPException(404, "No such event")

    ticket = next(
        (t for t in event.get("ticket_types", []) if t["id"] == ticket_type_id), None
    )
    if ticket is None:
        raise HTTPException(404, "No such ticket type for this event")

    # Deliberately not refused: membership is verified at confirmation, so a
    # brand-new member is not turned away by a feed that is a few hours stale.
    members_only_warning = ticket["members_only"] and not (
        session and session.is_member_now
    )

    token = generate_token()
    result = await database.post(
        "/reservations/draft",
        {
            "event_id": event["id"],
            "ticket_type_id": ticket_type_id,
            "quantity": quantity,
            "draft_token": token,
        },
        params={"minutes": settings.draft_hold_minutes},
    )
    if not result["ok"]:
        raise HTTPException(409, REASONS.get(result["reason"], "Could not hold a seat."))

    return {
        "ok": True,
        "draftToken": token,
        "expiresAt": result["reservation"]["expires_at"],
        "holdMinutes": settings.draft_hold_minutes,
        "membersOnly": bool(ticket["members_only"]),
        "membershipWarning": members_only_warning,
    }


@router.post("/events/{slug}/release-hold")
async def release_hold(slug: str, draft_token: str = Body(embed=True)) -> dict:
    """Give the seat straight back when somebody closes the form."""
    await database.post(f"/reservations/draft/{draft_token}/release")
    return {"ok": True}


# ------------------------------------------------------------------ waitlist


@router.post(
    "/events/{slug}/waitlist",
    dependencies=[Depends(verify_csrf), require_feature("bookings")],
)
async def join_waitlist(
    slug: str,
    ticket_type_id: int | None = Body(default=None, embed=True),
    quantity: int = Body(default=1, embed=True),
    guest: GuestDetails | None = Body(default=None, embed=True),
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Join the queue for a full event.

    Event-wide by design: somebody who wants to come wants to come, and letting
    one person hold four queue positions (one per ticket type) would push three
    other people down the list.
    """
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None or not event["published"]:
        raise HTTPException(404, "No such event")

    if session:
        user_id = session.user.id
    else:
        if guest is None:
            raise HTTPException(400, "Tell us your email so we can hold your place.")
        if is_imperial(str(guest.email)) and not guest.shortcode:
            raise HTTPException(
                400, "Imperial students: add your shortcode so we can find you."
            )
        user = await resolve_or_create_user(
            str(guest.email), guest.first_name, guest.last_name,
            shortcode=guest.shortcode, verified=False,
        )
        user_id = user["id"]

    try:
        entry = await database.post(
            "/waitlist",
            {
                "event_id": event["id"],
                "user_id": user_id,
                "preferred_ticket_type_id": ticket_type_id,
                "quantity": quantity,
            },
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            return {"ok": True, "already": True, "message": "You're already on the list."}
        raise

    return {
        "ok": True,
        "id": entry["id"],
        "message": (
            "You're on the waitlist. If a place frees up we'll email you and hold "
            "it while you buy the ticket."
        ),
    }


@router.delete("/waitlist/{entry_id}", dependencies=[Depends(verify_csrf)])
async def leave_waitlist(
    entry_id: int, session: AuthSession = Depends(require_session)
) -> dict:
    mine = await database.get("/waitlist", {"user_id": session.user.id}) or []
    if not any(e["id"] == entry_id for e in mine) and not session.is_admin:
        raise HTTPException(404, "No such waitlist entry")
    await database.post(f"/waitlist/{entry_id}/release")
    return {"ok": True}


@router.get("/me/waitlist")
async def my_waitlist(session: AuthSession = Depends(require_session)) -> dict:
    rows = await database.get("/waitlist", {"user_id": session.user.id}) or []
    return {
        "waitlist": [
            {
                "id": e["id"],
                "eventTitle": e.get("event_title"),
                "eventSlug": e.get("event_slug"),
                "position": e.get("position"),
                "offered": e.get("offered_at") is not None,
                "offerExpiresAt": e.get("offer_expires_at"),
            }
            for e in rows
        ]
    }
