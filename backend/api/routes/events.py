"""Public event listing and the reservation flow.

Booking is reservation-style: payment happens on the Union shop and the sales
feed only refreshes at 06:00, so a booking is a hold that gets promoted to
confirmed once a matching sale appears. Members are matched automatically on
their ICU login; external users type their order number in.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from clients import database
from config import settings
from models.auth import AuthSession, GuestDetails
from services import confirmations, purchase, tickets
from services import sync as sync_service
from services.identity import is_imperial, resolve_or_create_user
from utils import email as mailer
from utils.auth import (
    generate_login_code,
    generate_token,
    hash_token,
    normalise_email,
)
from utils.ics import build_calendar_ics, build_event_ics
from utils.deps import (
    current_session,
    require_feature,
    require_session,
    verify_csrf,
)
from utils.present import (
    event_card,
    format_date,
    format_doors_close,
    format_price,
    format_time_range,
    london,
    sales_state,
)
from utils.year import academic_year

log = logging.getLogger("api.events")

router = APIRouter()

# Why a hold failed, in words a member can act on.
# An anonymous, per-browser token for guests. Set when Reserve is pressed — not
# on page view — so it is strictly necessary for something the visitor asked
# for. Only its hash is stored. It lets the event page show a guest their own
# booking after a refresh, and lets the per-person limit count it. It is NOT a
# credential: cancelling or changing a booking still needs the emailed code.
VISITOR_COOKIE = "eesoc_visitor"
VISITOR_MAX_AGE = 90 * 24 * 3600


def _visitor_hash(request: Request) -> str | None:
    token = request.cookies.get(VISITOR_COOKIE)
    return hash_token(token) if token else None


def _ensure_visitor(request: Request, response: Response) -> str:
    token = request.cookies.get(VISITOR_COOKIE) or generate_token()
    response.set_cookie(
        VISITOR_COOKIE,
        token,
        max_age=VISITOR_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return hash_token(token)


REASONS = {
    "reservations_closed": "Booking for this event has closed.",
    "sold_out": "This event is full.",
    "type_sold_out": "That ticket type has sold out.",
    "over_user_limit": (
        "You've already reserved this ticket. Check your email for the "
        "sign-in code to see or change your booking."
    ),
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


def _require_sales_open(event: dict) -> None:
    """Refuse anything that takes a place before tickets are on sale.

    Enforced here and not only in the UI: the booking endpoints are reachable
    directly, and "the form was hidden" is not a control. Applies to holds and
    the waitlist as well as reservations — all three take a place.
    """
    state, opens = sales_state(event)
    if state == "open":
        return
    if state == "closed":
        raise HTTPException(409, REASONS["reservations_closed"])
    if state == "soon":
        raise HTTPException(409, "Tickets for this event are not on sale yet.")
    when = datetime.fromisoformat(opens)
    raise HTTPException(
        409,
        f"Tickets go on sale on {format_date(when)} at {format_time_range(when, None)}.",
    )



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
    slug: str,
    request: Request,
    session: AuthSession | None = Depends(current_session),
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
    elif (visitor := _visitor_hash(request)) is not None:
        mine = await database.get(
            "/reservations", {"visitor_hash": visitor, "event_id": event["id"]}
        ) or []

    # Where to buy each ticket — shown on the booking itself, so somebody holding
    # a ticket is one click from paying for it.
    links = await purchase.links_for_event(event)

    # The sponsors the committee linked to this event. Published sponsors only:
    # a company still in the pipeline has not signed, and must not appear on a
    # public page because somebody linked an event early.
    sponsors = [
        {
            "name": l["sponsor_name"],
            "slug": l["sponsor_slug"],
            "logoUrl": l.get("sponsor_logo_url"),
        }
        for l in await database.get("/sponsors/links/confirmed", {"event_id": event["id"]}) or []
        if l.get("sponsor_active")
    ]

    card = event_card(event)
    # The calendar/link fields live here and not in `event_card`: cards never
    # need them, and other sections depend on that shape staying put. The map
    # is public (a venue map helps anybody find the room); the meeting link
    # follows the event's own privacy flag — a live booking on THIS event
    # unlocks it, because the person holding one is exactly who the link is
    # for. `mine` is already computed above and covers a signed-in user or,
    # for a guest, the browser that made the booking.
    show_meeting_url = (
        not event.get("meeting_url_private")
        or is_admin
        or any(r["status"] in ("held", "confirmed") for r in mine)
    )
    card.update(
        {
            "sponsors": sponsors,
            "description": event.get("description", ""),
            "capacity": event.get("capacity"),
            "remaining": event.get("remaining"),
            "holdHours": event.get("hold_hours"),
            "mapUrl": event.get("map_url"),
            "meetingUrl": event.get("meeting_url") if show_meeting_url else None,
            "meetingUrlPrivate": bool(event.get("meeting_url_private")),
            "targetYears": event.get("target_years") or [],
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
                    "purchaseUrl": links.get(r["ticket_type_id"]) or links.get(None),
                    # The QR ticket for the door. Only ever the caller's own
                    # bookings reach this list, so the token is theirs to see.
                    "ticketToken": tickets.token_for(r["id"]),
                }
                for r in mine
                if r["status"] in ("held", "confirmed")
            ],
        }
    )
    return card


ICS_MEDIA_TYPE = "text/calendar; charset=utf-8"


@router.get("/events/{slug}/calendar.ics")
async def event_calendar_ics(slug: str) -> Response:
    """One event as a downloadable calendar file.

    No session dependency: calendar clients fetch URLs with no cookie jar, so
    anything behind auth would simply fail to import. What that costs is
    nothing — the file carries only fields already public on the event page,
    and the meeting link never enters it at all (see utils/ics). Unpublished
    events 404, the same rule as the page itself, so a draft's existence is
    not confirmed by its calendar file.
    """
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None or not event["published"]:
        raise HTTPException(404, "No such event")
    return Response(
        build_event_ics(event, settings.site_origin), media_type=ICS_MEDIA_TYPE
    )


@router.get("/events.ics")
async def events_calendar_ics(category: str | None = None) -> Response:
    """The subscribable feed: every published event, any date (past included).

    A calendar client shows history as readily as the future, and cutting the
    feed at "today" would make a subscribed calendar silently drop events the
    moment they happened — so `upcoming=False` here.

    No auth, and the category is a plain query param rather than a secret URL:
    category names are already printed on every event card on the public
    listing, so the parameter leaks nothing a calendar client could not have
    read off the page — and a client that cannot authenticate could not use a
    secret URL anyway. The category comparison is case-insensitive because
    this is a URL somebody pastes by hand.

    The database service cannot filter by category, so the feed pages the whole
    archive out of it and filters here in Python.
    """
    # Page through the archive rather than fetching it under one hard cap: the
    # adapter applies its limit after the order-by, so the day the archive
    # outgrew the cap the feed would start silently omitting its oldest events
    # — and a subscribed calendar would never notice the rows it never
    # received. A full page means more rows may follow; a short page means the
    # archive is exhausted.
    events: list[dict] = []
    page_size = 500
    offset = 0
    while True:
        page = (
            await database.get(
                "/events",
                {
                    "published_only": True,
                    "upcoming": False,
                    "limit": page_size,
                    "offset": offset,
                },
            )
            or []
        )
        events.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    if category:
        wanted = category.strip().lower()
        events = [e for e in events if (e.get("category") or "").lower() == wanted]
    name = "EESoc events" if not category else f"EESoc events ({category.strip()})"
    return Response(
        build_calendar_ics(events, settings.site_origin, name),
        media_type=ICS_MEDIA_TYPE,
    )


@router.post(
    "/events/{slug}/reserve",
    dependencies=[Depends(verify_csrf), require_feature("bookings")],
)
async def reserve(
    slug: str,
    request: Request,
    ticket_type_id: int = Body(embed=True),
    quantity: int = Body(default=1, embed=True),
    order_number: str | None = Body(default=None, embed=True),
    guest: GuestDetails | None = Body(default=None, embed=True),
    draft_token: str | None = Body(default=None, embed=True),
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Book tickets: a hold on a ticket to be bought, a place on a free one.

    Open to people who are not signed in: they supply an email and, if they are
    an Imperial student, their shortcode. An unverified account is created to own
    the booking, and the hold is deliberately shorter than a signed-in one —
    long enough to finish paying, not long enough to squat on a seat nobody has
    proved they want.

    A free ticket (no Union purchase) is never a hold: the booking comes back
    confirmed and its confirmation email, with the QR, goes straight away. The
    adapter decides (`_confirms_outright`), including the one case that still
    waits — a members-only ticket for somebody not yet in the members feed.
    """
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None or not event["published"]:
        raise HTTPException(404, "No such event")
    _require_sales_open(event)

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
    visitor = _visitor_hash(request)
    if draft_token:
        params = {"user_id": user_id, "academic_year": academic_year()}
        if visitor:
            params["visitor_hash"] = visitor
        result = await database.post(
            f"/reservations/draft/{draft_token}/claim",
            params=params,
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
                "visitor_hash": visitor,
                # Lets a free members-only ticket check membership this year.
                "academic_year": academic_year(),
            },
        )

    if not result["ok"]:
        # 409, not 400: the request was valid, the world changed under it.
        raise HTTPException(409, REASONS.get(result["reason"], "Could not reserve."))

    reservation = result["reservation"]

    # A free place is confirmed already: send its "you're confirmed" email (the
    # ticket and QR) now rather than on the sender's next ten-minute pass.
    if reservation["status"] == "confirmed":
        confirmations.trigger()

    # Guests get a code so they can come back, see the booking and confirm the
    # address is really theirs. Sending it here rather than making them ask
    # means one email covers both.
    if as_guest:
        await _send_guest_code(str(guest.email))

    # "We're holding your ticket, here's where to pay." Until this existed the
    # only record of a booking was a line on a web page the person had to
    # remember to come back to — and the deadline for paying lived there too.
    address = str(guest.email) if as_guest else str(session.user.email)
    await _send_held_confirmation(
        address, event, ticket, reservation, is_member, as_guest
    )

    return {
        "ok": True,
        "guest": as_guest,
        "reservation": {
            "id": reservation["id"],
            "status": reservation["status"],
            "quantity": reservation["quantity"],
            "expiresAt": reservation["expires_at"],
        },
        "next": _next_step(ticket, is_member, as_guest, reservation["status"]),
        "membershipWarning": membership_warning,
    }


async def _send_held_confirmation(
    address: str,
    event: dict,
    ticket: dict,
    reservation: dict,
    is_member: bool,
    as_guest: bool,
) -> None:
    """Tell somebody what they are holding and how to pay for it.

    Failures are swallowed, as with the guest code: the hold is the thing that
    mattered and it already exists, so a mail outage must not turn a successful
    booking into a 500. It is logged, and the committee can re-send from the
    unpaid-bookings screen.

    A free ticket gets nothing — there is no deadline and nothing to buy, and an
    email saying "pay by Thursday" for a £0 ticket is how people learn to ignore
    the ones that matter.
    """
    if not ticket.get("requires_purchase", True):
        return

    try:
        link = await purchase.link_for(event, ticket["id"])
        starts = _parse_dt(event.get("starts_at"))
        await mailer.send_booking_held(
            address,
            event["title"],
            ticket["name"],
            reservation["quantity"],
            format_price(ticket.get("price_pence", 0)),
            f"{format_date(starts)} at {format_time_range(starts, None)}" if starts else "TBC",
            _readable_deadline(reservation.get("expires_at")),
            f"{settings.site_origin}/events/{event['slug']}",
            purchase_url=link,
            # A member's Union purchase is matched on their login; everybody
            # else has to tell us the order number, and the email is where they
            # find that out.
            needs_order_number=not is_member,
            doors_close=format_doors_close(starts, event.get("doors_close_at")),
        )
    except Exception:  # noqa: BLE001
        log.exception("Could not send booking confirmation to %s", address)


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _readable_deadline(iso: str | None) -> str:
    """A deadline somebody can act on, not an ISO timestamp."""
    when = _parse_dt(iso)
    if when is None:
        return "soon"
    return f"{format_date(when)} at {london(when).strftime('%H:%M')}"


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


def _next_step(
    ticket: dict, is_member: bool, as_guest: bool = False, status: str = "held"
) -> dict:
    if not ticket.get("requires_purchase", True):
        if status == "confirmed":
            return {
                "action": "none",
                "message": "You're in. Nothing to pay — we've emailed you your ticket.",
            }
        # The one free booking that waits: members-only, membership not yet in
        # the feed. The warning beside this says why.
        return {
            "action": "none",
            "message": (
                "Your place is saved. We'll confirm it and email your ticket as "
                "soon as the Union's records show your membership."
            ),
        }
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
    promoted = await database.post(
        "/waitlist/promote", params={"academic_year": academic_year()}
    )
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
    if updated["status"] == "confirmed":
        confirmations.trigger()
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
    request: Request,
    response: Response,
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
    _require_sales_open(event)

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
            "visitor_hash": _ensure_visitor(request, response),
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
    _require_sales_open(event)

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


# ------------------------------------------------- "tell me when it's on sale"


@router.post("/events/{slug}/notify-me")
async def notify_when_on_sale(
    slug: str,
    email: str | None = Body(default=None, embed=True),
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Ask to be emailed the moment this event's tickets open.

    Open to the signed-out on purpose: this is the one thing somebody can do on
    an event that is not yet bookable, and making them create an account first
    would lose most of them. No account is created either — the address is held
    against the alert and nothing else.

    Deliberately NOT gated on `_require_sales_open`: the whole point is that
    tickets are not on sale yet.
    """
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None or not event["published"]:
        raise HTTPException(404, "No such event")

    address = normalise_email(str(email or (session.user.email if session else "")))
    if "@" not in address:
        raise HTTPException(400, "Tell us where to email you.")

    state, _ = sales_state(event)
    if state == "closed":
        raise HTTPException(409, REASONS["reservations_closed"])
    if state == "open":
        # Not an error: they can book right now, and saying so is more use than
        # a promise to email them about something that has already happened.
        return {
            "ok": True,
            "alreadyOpen": True,
            "message": "Tickets are on sale now — you can book straight away.",
        }

    alert = await database.post(
        "/email/sale-alerts",
        {
            "event_id": event["id"],
            "email": address,
            "user_id": session.user.id if session else None,
        },
    )
    return {
        "ok": True,
        "id": alert["id"],
        "email": address,
        "message": (
            f"We'll email {address} the moment tickets go on sale. "
            "Every alert has a one-click way out."
        ),
    }


@router.delete("/events/{slug}/notify-me", dependencies=[Depends(verify_csrf)])
async def cancel_sale_alert(
    slug: str, session: AuthSession = Depends(require_session)
) -> dict:
    """Signed-in cancellation. Somebody without an account uses the link in the
    email, which carries its own token."""
    event = await database.get(f"/events/by-slug/{slug}")
    if event is None:
        raise HTTPException(404, "No such event")

    alerts = await database.get(
        "/email/sale-alerts",
        {"event_id": event["id"], "email": str(session.user.email)},
    ) or []
    for alert in alerts:
        await database.post(f"/email/sale-alerts/{alert['id']}/cancel")
    return {"ok": True, "cancelled": len(alerts)}
