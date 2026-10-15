"""Committee-only administration.

Everything here is behind `require_admin` — "holds some committee role" — and
then each endpoint names the capability it actually needs. The roles and what
they unlock are in `utils/roles.py`; the enforcement is `utils/deps.py`. A role
is a row in `admins` keyed on user id, and everyone signs in through the same
code flow as everybody else.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from clients import database, union
from models.auth import AuthSession
from config import settings
from services import agreement as agreement_service
from services import announce as announce_service
from services import campaigns as campaigns_service
from services import confirmations, emailing, purchase, sponsor_notes, storage, whatsapp
from services.identity import clean_shortcode, find_member, is_shortcode, resolve_or_create_user
from utils.auth import (
    generate_token,
    hash_token,
    normalise_email,
    set_session_cookies,
)
from utils import email as mailer
from utils import lockers as lockers_util
from utils.present import (
    format_date,
    format_doors_close,
    format_price,
    format_time_range,
)
from services import sync as sync_service
from utils.deps import (
    assert_may_edit_event,
    may_edit_event,
    require_admin,
    require_event_editor,
    require_feature,
    require_any_capability,
    require_locker_allowances,
    require_site,
    require_sponsors,
    require_users,
    verify_csrf,
)
from utils import roles as role_table
from utils.year import academic_year

log = logging.getLogger("api.admin")

# The maintenance gate is never set automatically for admin — only by hand.
# Deriving it from email would take the dashboard away at exactly the moment
# an admin needs it: the webmaster password lapses yearly, and fixing that is
# an admin job.
router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin), require_feature("admin")],
)


@router.get("/dashboard")
async def dashboard(session: AuthSession = Depends(require_admin)) -> dict:
    """Everything the admin landing page shows, in one request.

    Deliberately one endpoint: the page needs all of it together, and the
    adapter answers the counts with a single query.
    """
    stats = await database.get("/union/stats") or {}
    runs = await database.get("/union/sync-runs/latest") or []

    union_health = None
    if union.configured():
        try:
            union_health = await union.health()
        except union.UnionUnavailable as exc:
            union_health = {"ok": False, "error": str(exc)}

    counts = stats.get("counts", {})
    # Drafts in the email approval queue. Counted here so the landing page can
    # say so: a timer wrote them, and nothing goes out until somebody acts.
    awaiting = sum(
        1 for c in await database.get(
            "/email/campaigns", {"status": "awaiting_approval", "limit": 100}
        ) or []
        if c.get("status") == "awaiting_approval"
    )
    return {
        "academicYear": academic_year(),
        "counts": counts,
        "needsAttention": stats.get("needsAttention", []),
        "recentBookings": stats.get("recentBookings", []),
        "syncRuns": runs,
        "union": union_health,
        "storage": {
            **storage.describe(),
            "files": counts.get("uploads", 0),
            "bytes": counts.get("upload_bytes", 0),
        },
        "environment": settings.environment,
        "devLoginEnabled": settings.dev_login_enabled,
        # What this particular person may do, so the page can render only the
        # tabs they can use. The server refuses the rest regardless.
        "role": session.role,
        "capabilities": sorted(session.capabilities),
        # Surfaced so the committee sees what still needs setting up, rather
        # than finding out when a booking silently fails to confirm.
        "warnings": _warnings(counts, union_health, awaiting),
        "emailsAwaitingApproval": awaiting,
    }


def _warnings(counts: dict, union_health: dict | None, awaiting: int = 0) -> list[dict]:
    out = []
    if settings.dev_login_enabled:
        out.append({
            "level": "error",
            "message": "DEV LOGIN is enabled — anyone can sign in as anyone. Development only.",
        })
    if union_health and union_health.get("ok") is False:
        out.append({"level": "error",
                    "message": f"ICU bridge unreachable: {union_health.get('error')}"})
    if not counts.get("members"):
        out.append({"level": "warn",
                    "message": "No membership data has synced, so nobody is recognised as a member.",
                    "action": "sync"})
    if not counts.get("product_links"):
        out.append({"level": "warn",
                    "message": "No Union products are linked, so no purchase can confirm a booking.",
                    "action": "links"})
    if not counts.get("events_upcoming"):
        out.append({"level": "info", "message": "No upcoming events are published."})
    if counts.get("res_overdue"):
        out.append({
            "level": "warn",
            "message": (
                f"{counts['res_overdue']} booking(s) are past their purchase "
                "window and need a decision — remind, cancel or confirm."
            ),
            "action": "overdue",
        })
    if awaiting:
        out.append({
            "level": "warn",
            "message": (
                f"{awaiting} email(s) waiting for approval. Nothing goes out "
                "until somebody sends or dismisses them."
            ),
            "action": "email",
        })
    return out


@router.get("/overview")
async def overview() -> dict:
    runs = await database.get("/union/sync-runs/latest") or []
    year = academic_year()
    members = await database.get("/union/members/count", {"academic_year": year})
    events = await database.get("/events", {"published_only": False, "limit": 500}) or []

    union_health = None
    if union.configured():
        try:
            union_health = await union.health()
        except union.UnionUnavailable as exc:
            union_health = {"ok": False, "error": str(exc)}

    return {
        "academicYear": year,
        "members": members or 0,
        "events": {
            "total": len(events),
            "published": sum(1 for e in events if e["published"]),
            "drafts": sum(1 for e in events if not e["published"]),
        },
        "syncRuns": runs,
        "union": union_health,
    }


@router.post(
    "/sync",
    dependencies=[Depends(verify_csrf), Depends(require_any_capability("site", "events"))],
)
async def trigger_sync(force: bool = True) -> dict:
    """'Sync now'. `force=True` bypasses the bridge's response cache — which is
    the point of pressing it, but it does spend a real upstream request.

    Open to the events officers as well as admins: pulling the sales feed is how
    a booking somebody has already paid for gets confirmed, and that is their
    job rather than an admin's."""
    return await sync_service.sync_all(force=force)


@router.post(
    "/sync/committee",
    dependencies=[Depends(verify_csrf), Depends(require_site)],
)
async def sync_committee() -> dict:
    """'Sync committee now'.

    The scheduled job only pulls the roster on 1 August, so this is the way to
    pick up a mid-year change. Safe to press: intros, photos, display roles and
    ordering are keyed on login and are not touched.
    """
    result = await sync_service.sync_committee_now(force=True)
    members = await database.get(
        "/union/committee", {"academic_year": academic_year()}
    ) or []
    return {**result, "members": len(members)}


@router.post(
    "/reconcile",
    dependencies=[Depends(verify_csrf), Depends(require_any_capability("site", "events"))],
)
async def reconcile() -> dict:
    """Expire stale holds and match reservations against already-synced sales,
    without calling upstream."""
    return await sync_service.reconcile_only()


# -------------------------------------------------------------------- events


@router.get("/events")
async def list_all_events(
    session: AuthSession = Depends(require_event_editor),
) -> list[dict]:
    """Every event, annotated with whether this caller may change it.

    The industry officers see the whole calendar rather than only their own
    slice: a careers talk is scheduled around the rest of the term, and hiding
    the rest would make that impossible. They just cannot edit it, and the row
    says so.
    """
    events = await database.get("/events", {"published_only": False, "limit": 500}) or []
    # Which sponsors each event is linked to, for the badge in the list.
    sponsors: dict[int, list[str]] = {}
    for link in await database.get("/sponsors/links/confirmed") or []:
        sponsors.setdefault(link["event_id"], []).append(link["sponsor_name"])
    return [
        {**e, "can_edit": may_edit_event(session, e), "sponsors": sponsors.get(e["id"], [])}
        for e in events
    ]


async def _editable_event(event_id: int, session: AuthSession) -> dict:
    """Load an event and refuse if this role does not cover it."""
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_may_edit_event(session, event)
    return event


async def _editable_ticket(ticket_type_id: int, session: AuthSession) -> dict:
    ticket = await database.get(f"/ticket-types/{ticket_type_id}")
    if ticket is None:
        raise HTTPException(404, "No such ticket type")
    await _editable_event(ticket["event_id"], session)
    return ticket


@router.post("/events", dependencies=[Depends(verify_csrf)])
async def create_event(
    body: dict = Body(...), session: AuthSession = Depends(require_event_editor)
) -> dict:
    body.setdefault("academic_year", academic_year())
    if not (body.get("slug") or "").strip():
        raise HTTPException(400, "An event needs a slug")
    # Somebody who may only edit industry events can only create one, or they
    # would make an event and immediately lose the ability to fix it.
    if not session.can("events"):
        body["industry"] = True
    created = await database.post(
        "/events", body, params={"created_by": session.user.id}
    )
    await _sync_on_sale_email(created["id"])
    return created


@router.put("/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def update_event(
    event_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    existing = await _editable_event(event_id, session)
    if not session.can("events"):
        # Clearing the flag would hand the event to the events officers and
        # lock its own author out of it, which is never what the save button
        # was meant to do.
        body["industry"] = True
    else:
        body.setdefault("industry", existing.get("industry", False))
    updated = await database.put(f"/events/{event_id}", body)
    await _sync_on_sale_email(event_id)
    return updated


@router.delete("/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def delete_event(
    event_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    await _editable_event(event_id, session)
    await database.delete(f"/events/{event_id}")
    await _sync_on_sale_email(event_id)
    return {"ok": True}


async def _sync_on_sale_email(event_id: int) -> None:
    """Keep the event's "tickets are on sale" email in step with the event.

    Written the moment an event is published with a sale time, so it can be
    approved well before sales open; moved when the sale moves; taken off the
    schedule if the event is unpublished or deleted. Never fails the save that
    triggered it — the startup pass catches up anything missed.
    """
    try:
        await campaigns_service.sync_on_sale(event_id)
    except Exception:  # noqa: BLE001
        log.exception("Could not update the on-sale email for event %s", event_id)


@router.post("/events/{event_id}/ticket-types", dependencies=[Depends(verify_csrf)])
async def add_ticket_type(
    event_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    await _editable_event(event_id, session)
    return await database.post(f"/events/{event_id}/ticket-types", body)


@router.put("/ticket-types/{ticket_type_id}", dependencies=[Depends(verify_csrf)])
async def update_ticket_type(
    ticket_type_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    await _editable_ticket(ticket_type_id, session)
    return await database.put(f"/ticket-types/{ticket_type_id}", body)


@router.delete("/ticket-types/{ticket_type_id}", dependencies=[Depends(verify_csrf)])
async def delete_ticket_type(
    ticket_type_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    await _editable_ticket(ticket_type_id, session)
    await database.delete(f"/ticket-types/{ticket_type_id}")
    return {"ok": True}


@router.get("/events/{event_id}/reservations")
async def event_reservations(
    event_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """The door list. Held and confirmed are shown separately because only
    confirmed rows have actually been paid for."""
    await _editable_event(event_id, session)
    rows = await database.get("/reservations", {"event_id": event_id, "limit": 1000}) or []
    return {
        "confirmed": [r for r in rows if r["status"] == "confirmed"],
        "held": [r for r in rows if r["status"] == "held"],
        "closed": [r for r in rows if r["status"] in ("cancelled", "expired")],
    }


@router.get("/events/{event_id}/detail")
async def event_detail(
    event_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """Everything about one event's bookings, for its admin page: a summary per
    ticket type, every reservation in every state, and the waitlist."""
    event = await _editable_event(event_id, session)
    rows = await database.get("/reservations", {"event_id": event_id, "limit": 5000}) or []
    waiting = await database.get("/waitlist", {"event_id": event_id}) or []

    summary = []
    for t in event.get("ticket_types", []):
        mine = [r for r in rows if r["ticket_type_id"] == t["id"]]
        qty = lambda *states: sum(r["quantity"] for r in mine if r["status"] in states)
        summary.append({
            "ticketTypeId": t["id"],
            "name": t["name"],
            "price": format_price(t.get("price_pence", 0)),
            "capacity": t.get("capacity"),
            "unpaid": qty("held"),
            "bought": qty("confirmed"),
            "closed": qty("cancelled", "expired"),
            "waitlist": sum(
                w.get("quantity", 1) for w in waiting
                if w.get("preferred_ticket_type_id") == t["id"] and not w.get("claimed_at")
            ),
        })

    def person(r: dict) -> dict:
        name = " ".join(p for p in [r.get("user_first_name"), r.get("user_last_name")] if p)
        return {
            "id": r["id"],
            "name": name or None,
            "email": r.get("user_email"),
            "shortcode": r.get("user_shortcode"),
            "member": bool(r.get("has_member_record")),
            "ticketTypeId": r["ticket_type_id"],
            "ticketType": r.get("ticket_type_name"),
            "quantity": r["quantity"],
            "status": r["status"],
            "requiresPurchase": r.get("requires_purchase", True),
            "membersOnly": r.get("members_only", False),
            "orderNumber": r.get("order_number"),
            "guest": r.get("booked_as_guest", False),
            "bookedAt": r.get("created_at"),
            "holdUntil": r.get("expires_at"),
            "confirmedAt": r.get("confirmed_at"),
            "cancelledAt": r.get("cancelled_at"),
            "source": r.get("source") or "booking",
            "note": r.get("note"),
            "confirmationEmail": r.get("confirmation_email"),
            "confirmationSentAt": r.get("confirmation_sent_at"),
            "confirmationAttempts": r.get("confirmation_attempts") or 0,
        }

    return {
        "event": {
            "id": event["id"],
            "title": event["title"],
            "slug": event.get("slug"),
            "startsAt": event.get("starts_at"),
            "date": format_date(_parse_dt(event.get("starts_at"))),
            "time": format_time_range(
                _parse_dt(event.get("starts_at")), _parse_dt(event.get("ends_at"))
            ),
            "venue": event.get("venue"),
            "capacity": event.get("capacity"),
            "published": event.get("published"),
        },
        "summary": summary,
        "reservations": [person(r) for r in rows if r["status"] != "draft"],
        "waitlist": [
            {
                "id": w["id"],
                "email": w.get("email"),
                "position": w.get("position"),
                "quantity": w.get("quantity", 1),
                "ticketTypeId": w.get("preferred_ticket_type_id"),
                "joinedAt": w.get("created_at"),
                "offeredAt": w.get("offered_at"),
            }
            for w in waiting
            if not w.get("claimed_at")
        ],
    }


@router.get("/events/{event_id}/audiences")
async def event_email_audiences(
    event_id: int, _: AuthSession = Depends(require_site)
) -> dict:
    """The per-event audiences for the "Email about this" dialog."""
    return {"audiences": await emailing.event_audiences(event_id)}


@router.get("/reservations/overdue")
async def overdue_reservations(
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Holds whose purchase window has passed with no matching sale.

    Nothing is cancelled automatically. A sale can be late, bought under a
    different account, or paid in cash, and silently voiding somebody's ticket
    is not recoverable — so the committee decides, per booking.
    """
    rows = await database.get("/reservations/overdue") or []
    # An industry-only role sees the unpaid bookings for its own events and
    # nothing else — these rows carry people's names and what they bought.
    if not session.can("events"):
        allowed = {
            e["id"]
            for e in (await database.get("/events", {"published_only": False, "limit": 500}) or [])
            if e.get("industry")
        }
        rows = [r for r in rows if r.get("event_id") in allowed]
    return {
        "overdue": [
            {
                "id": r["id"],
                "email": r.get("user_email"),
                "eventTitle": r.get("event_title"),
                "eventSlug": r.get("event_slug"),
                "ticketType": r.get("ticket_type_name"),
                "quantity": r["quantity"],
                "price": format_price(r.get("price_pence", 0)),
                "orderNumber": r.get("order_number"),
                "guest": r.get("booked_as_guest", False),
                "heldSince": r.get("created_at"),
                "dueSince": r.get("expires_at"),
                "membersOnly": r.get("members_only", False),
                # A members-only ticket held by somebody with no membership
                # record will never confirm on its own, which is a different
                # problem from an unpaid ticket.
                "blockedOnMembership": bool(
                    r.get("members_only") and not r.get("has_member_record")
                ),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.post("/reservations/{reservation_id}/remind", dependencies=[Depends(verify_csrf)])
async def remind(
    reservation_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """Nudge somebody who has booked but not bought."""
    reservation = await database.get(f"/reservations/{reservation_id}")
    if reservation is None:
        raise HTTPException(404, "No such reservation")
    user = await database.get(f"/auth/users/{reservation['user_id']}")
    event = await _editable_event(reservation["event_id"], session)
    if not user:
        raise HTTPException(404, "No such reservation")

    sent = await mailer.send_purchase_reminder(
        str(user["email"]),
        event["title"],
        reservation["quantity"],
        f"{settings.site_origin}/events/{event['slug']}",
        # Without this the nudge says "buy it on the Union shop" and leaves
        # them to find it, which is the reason the booking is unpaid often
        # enough to be worth fixing here.
        purchase_url=await purchase.link_for(event, reservation["ticket_type_id"]),
        doors_close=format_doors_close(event.get("starts_at"), event.get("doors_close_at")),
    )
    return {
        "ok": True,
        "sent": sent,
        "message": "Reminder sent." if sent else "No mail server configured — logged instead.",
    }


@router.post("/reservations/{reservation_id}/confirm", dependencies=[Depends(verify_csrf)])
async def confirm_manually(
    reservation_id: int,
    order_number: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Confirm a booking the matcher could not."""
    await _reservation_in_scope(reservation_id, session)
    row = await database.post(
        f"/reservations/{reservation_id}/confirm",
        params={"order_number": order_number},
    )
    confirmations.trigger()
    return {"ok": True, "reservation": row}


@router.post("/reservations/{reservation_id}/ticket-type", dependencies=[Depends(verify_csrf)])
async def change_ticket_type(
    reservation_id: int,
    ticket_type_id: int = Body(embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Move a booking to another ticket type of the same event.

    Sends nothing: this is the committee correcting its own records, usually
    after agreeing the change with the person already. The exception is a hold
    moved onto a free ticket, which the move confirms (a free ticket is never a
    hold): like every confirmation, that sends the "you're confirmed" email,
    because the person now holds a ticket they did not have before.
    """
    reservation = await _reservation_in_scope(reservation_id, session)
    row = await database.post(
        f"/reservations/{reservation_id}/ticket-type",
        params={"ticket_type_id": ticket_type_id},
    )
    # The adapter's 404 arrives as None, not an exception.
    if row is None:
        raise HTTPException(404, "No such ticket type for this event")
    if reservation.get("status") == "held" and row.get("status") == "confirmed":
        confirmations.trigger()
    names = {
        t["id"]: t["name"]
        for t in (await database.get(f"/events/{reservation['event_id']}") or {}).get("ticket_types", [])
    }
    log.warning(
        "Reservation %s moved to ticket type %s by %s",
        reservation_id, ticket_type_id, session.user.email,
    )
    return {
        "ok": True,
        "reservation": row,
        "message": f"Moved to {names.get(ticket_type_id, 'the new ticket type')}.",
    }


@router.post("/events/{event_id}/tickets", dependencies=[Depends(verify_csrf)])
async def add_ticket(
    event_id: int,
    ticket_type_id: int = Body(embed=True),
    email: str = Body(embed=True),
    first_name: str | None = Body(default=None, embed=True),
    last_name: str | None = Body(default=None, embed=True),
    quantity: int = Body(default=1, embed=True),
    note: str | None = Body(default=None, embed=True),
    send_confirmation: bool = Body(default=True, embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Add a ticket for somebody — paid in cash or by transfer, or a comp.

    It lands confirmed, takes a real place (refused when the type or the event
    is full, as a booking would be), and is never expired or paired with a
    Union sale by the sync. The holder is found or created exactly as a guest
    booking would be, so a member is recognised by their address or shortcode
    and an outsider gets an unverified account that owns the ticket.

    `events` or `industry` on this event, like every other change to its
    bookings. No Union calls.
    """
    await _editable_event(event_id, session)
    address = normalise_email(email)
    if "@" not in address:
        raise HTTPException(400, "That does not look like an email address.")
    if not 1 <= quantity <= 20:
        raise HTTPException(400, "Between 1 and 20 tickets at a time.")

    holder = await resolve_or_create_user(
        address,
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        verified=False,
    )
    who = session.user.email
    text = (note or "").strip()
    row = await database.post(
        "/reservations/committee",
        {
            "event_id": event_id,
            "ticket_type_id": ticket_type_id,
            "user_id": holder["id"],
            "quantity": quantity,
            # The note says who added it as well as why, so the bookings page
            # answers "where did this come from" without a second lookup.
            "note": f"added by {who}" + (f": {text}" if text else ""),
            "added_by": session.user.id,
            "send_confirmation": send_confirmation,
            "booked_as_guest": holder.get("member_id") is None,
        },
    )
    # The adapter's 404s (no such event or ticket type) arrive as None.
    if row is None:
        raise HTTPException(404, "No such ticket type for this event")
    log.warning(
        "Committee ticket %s added for user %s by %s", row["id"], holder["id"], who
    )
    if send_confirmation:
        confirmations.trigger()
    return {
        "ok": True,
        "reservation": row,
        "message": (
            f"Ticket added for {holder['email']}."
            + (" A confirmation email is on its way." if send_confirmation else "")
        ),
    }


@router.post("/reservations/{reservation_id}/confirmation", dependencies=[Depends(verify_csrf)])
async def resend_confirmation(
    reservation_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """Send a confirmed booking's "you're confirmed" email again."""
    await _reservation_in_scope(reservation_id, session)
    await database.post(f"/reservations/{reservation_id}/confirmation/resend")
    confirmations.trigger()
    return {"ok": True, "message": "Confirmation email queued."}


@router.post("/reservations/{reservation_id}/cancel", dependencies=[Depends(verify_csrf)])
async def cancel_reservation(
    reservation_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """Cancel a booking and hand the seat to the waitlist."""
    await _reservation_in_scope(reservation_id, session)
    await database.post(f"/reservations/{reservation_id}/cancel")
    promoted = await database.post(
        "/waitlist/promote", params={"academic_year": academic_year()}
    )
    # A free place handed on from the waitlist is already confirmed; send its
    # email now rather than on the sender's next pass.
    if any(o.get("confirmed") for o in (promoted or {}).get("offered", [])):
        confirmations.trigger()
    return {"ok": True, "promoted": (promoted or {}).get("count", 0)}


async def _reservation_in_scope(reservation_id: int, session: AuthSession) -> dict:
    reservation = await database.get(f"/reservations/{reservation_id}")
    if reservation is None:
        raise HTTPException(404, "No such reservation")
    await _editable_event(reservation["event_id"], session)
    return reservation


@router.get("/sponsor-requests")
async def sponsor_requests(
    _: AuthSession = Depends(require_sponsors),
) -> list[dict]:
    """Requests across all sponsors, newest first, for the committee to work
    through. One query: this used to fetch every sponsor's list in turn."""
    return await database.get("/sponsors/requests/all") or []


@router.get("/waitlist")
async def waitlist(
    event_id: int | None = None,
    session: AuthSession = Depends(require_event_editor),
) -> list[dict]:
    if event_id is not None:
        await _editable_event(event_id, session)
    elif not session.can("events"):
        raise HTTPException(403, "Ask for one event's waitlist at a time.")
    return await database.get("/waitlist", {"event_id": event_id}) or []


# ------------------------------------------------------------- product linking


@router.get("/union/products")
async def union_products(
    year: str | None = None, _: AuthSession = Depends(require_admin)
) -> list[dict]:
    """Feeds the 'link a product' dropdown. Served from the local mirror, never
    from upstream — this is a UI interaction and must not spend API quota."""
    return await database.get(
        "/union/products", {"academic_year": year or academic_year()}
    ) or []


@router.put("/events/{event_id}/product", dependencies=[Depends(verify_csrf)])
async def set_event_product(
    event_id: int,
    union_product_id: int | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Link the whole Union product to the whole event.

    One product per event, mirroring eActivities: a product with lines. Passing
    null unlinks. Replaces any existing event-level link rather than adding a
    second, which would make matching ambiguous.
    """
    await _editable_event(event_id, session)
    existing = await database.get("/product-links", {"event_id": event_id}) or []
    for link in existing:
        if link["ticket_type_id"] is None:
            await database.delete(f"/product-links/{link['id']}")

    if union_product_id is None:
        return {"ok": True, "linked": None}

    row = await database.post(
        "/product-links",
        {
            "purpose": "event_ticket",
            "event_id": event_id,
            "ticket_type_id": None,
            "union_product_id": union_product_id,
            # Line is left open at event level; each ticket type may pin one.
            "union_product_line_id": None,
            "academic_year": academic_year(),
            "created_by": session.user.id,
        },
    )
    return {"ok": True, "linked": row}


@router.put("/ticket-types/{ticket_type_id}/product", dependencies=[Depends(verify_csrf)])
async def set_ticket_product(
    ticket_type_id: int,
    union_product_id: int | None = Body(default=None, embed=True),
    union_product_line_id: int | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Point one ticket type at its own Union product.

    Each ticket type is sold as a separate product in eActivities, so the
    product is chosen per ticket type rather than once for the whole event.
    A line may also be given, for the case where one product does carry several
    lines; leaving it null means any line of that product counts.

    The event-level link still works and acts as the fallback — the matcher
    prefers the more specific link, so a type with its own product is never
    confirmed by a sale against a sibling's.

    Passing a null product unlinks, and replaces rather than adds: two links
    for one ticket type would make matching ambiguous.
    """
    ticket = await _editable_ticket(ticket_type_id, session)

    if union_product_id is None and union_product_line_id is not None:
        raise HTTPException(
            400, "A line belongs to a product — choose the product as well."
        )

    existing = await database.get(
        "/product-links", {"event_id": ticket["event_id"]}
    ) or []
    for link in existing:
        if link["ticket_type_id"] == ticket_type_id:
            await database.delete(f"/product-links/{link['id']}")

    if union_product_id is None:
        return {"ok": True, "linked": None}

    row = await database.post(
        "/product-links",
        {
            "purpose": "event_ticket",
            "event_id": ticket["event_id"],
            "ticket_type_id": ticket_type_id,
            "union_product_id": union_product_id,
            "union_product_line_id": union_product_line_id,
            "academic_year": academic_year(),
            "created_by": session.user.id,
        },
    )
    return {"ok": True, "linked": row}


@router.get("/product-links")
async def list_links(
    purpose: str | None = None,
    event_id: int | None = None,
    _: AuthSession = Depends(require_admin),
) -> list[dict]:
    return await database.get(
        "/product-links", {"purpose": purpose, "event_id": event_id}
    ) or []


@router.post("/product-links", dependencies=[Depends(verify_csrf)])
async def create_link(
    body: dict = Body(...), session: AuthSession = Depends(require_site)
) -> dict:
    """Map a Union product (or a specific line of it) to a ticket type, a locker,
    or membership.

    Leaving `union_product_line_id` null means "any line of this product", which
    is the safe default: line ids are not documented as unique across products,
    so pinning one is only worth doing when a product genuinely has several
    lines you need to tell apart.
    """
    body.setdefault("academic_year", academic_year())
    body["created_by"] = session.user.id
    if not body.get("purpose"):
        raise HTTPException(400, "purpose must be event_ticket, locker or membership")
    return await database.post("/product-links", body)


@router.delete("/product-links/{link_id}", dependencies=[Depends(verify_csrf)])
async def delete_link(link_id: int, _: AuthSession = Depends(require_site)) -> dict:
    await database.delete(f"/product-links/{link_id}")
    return {"ok": True}


# ------------------------------------------------------- people and roles
#
# Superuser only, which is the whole point of having a superuser: an admin runs
# the site, and handing out the keys to it is a separate decision from running
# it. The standing superuser address in settings cannot be demoted here, and
# would be re-granted on its next request even if the row were deleted by hand.


def _is_standing(email: str | None) -> bool:
    configured = (settings.superuser_email or "").strip().lower()
    return bool(configured) and str(email or "").strip().lower() == configured


@router.get("/admins")
async def list_admins(session: AuthSession = Depends(require_admin)) -> dict:
    """Who holds what.

    Readable by any committee role — knowing who to ask is not privileged, and
    the alternative is people guessing. Only a superuser can change it, and the
    page only offers the controls when `canManage` says so.
    """
    people = await database.get("/auth/admins") or []
    rows = [{**row, "standing": _is_standing(row.get("email"))} for row in people]

    # The standing superuser holds the role whether or not there is a row for
    # them — they may simply never have signed in here. Showing the address
    # anyway is the difference between "nobody has the keys" and "that person
    # does, they just have not been here yet".
    if settings.superuser_email and not any(r["standing"] for r in rows):
        rows.insert(0, {
            "user_id": None,
            "email": settings.superuser_email,
            "role": "superuser",
            "first_name": None,
            "last_name": None,
            "granted_at": None,
            "note": "standing superuser — has not signed in yet",
            "standing": True,
        })

    return {
        "admins": rows,
        "roles": role_table.describe(),
        "canManage": session.can("users"),
        "superuserEmail": settings.superuser_email,
    }


@router.post("/admins", dependencies=[Depends(verify_csrf)])
async def add_admin(
    email: str = Body(embed=True),
    role: str = Body(default="admin", embed=True),
    session: AuthSession = Depends(require_users),
) -> dict:
    """Grant a role, or change the one somebody already holds.

    The account is created if it does not exist yet, *unverified* — the same as
    granting sponsor access — so a new committee member can be set up before
    they have ever visited. Unverified is what keeps that safe: the role does
    nothing until somebody completes the emailed-code sign-in, which proves they
    control the address. The remaining risk is a typo that lands on a real
    address belonging to somebody else, so the response says when the account
    has never signed in and the page asks the granter to check it.
    """
    if role not in role_table.ROLE_NAMES:
        raise HTTPException(400, f"'{role}' is not a role")

    address = normalise_email(email)
    if "@" not in address:
        raise HTTPException(400, "That does not look like an email address.")
    user = await resolve_or_create_user(address, verified=False)
    if _is_standing(user.get("email")) and role != "superuser":
        raise HTTPException(
            400,
            f"{settings.superuser_email} is the standing superuser and cannot be "
            "given a lesser role.",
        )

    await database.post(
        f"/auth/admins/{user['id']}",
        params={"role": role, "granted_by": session.user.id},
    )
    log.warning(
        "Role %s granted to user %s by %s", role, user["id"], session.user.email
    )
    return {
        "ok": True,
        "userId": user["id"],
        # The address the role actually went to: an Imperial address collapses
        # to shortcode@ic.ac.uk, which is worth seeing.
        "email": user["email"],
        "role": role,
        "neverSignedIn": user.get("last_login_at") is None,
    }


@router.delete("/admins/{user_id}", dependencies=[Depends(verify_csrf)])
async def remove_admin(
    user_id: int, session: AuthSession = Depends(require_users)
) -> dict:
    if user_id == session.user.id:
        raise HTTPException(400, "You cannot remove your own access.")

    people = await database.get("/auth/admins") or []
    target = next((a for a in people if a["user_id"] == user_id), None)
    if target is None:
        raise HTTPException(404, "They do not have any access to remove.")
    if _is_standing(target.get("email")):
        raise HTTPException(
            400,
            f"{settings.superuser_email} is the standing superuser. Change "
            "SUPERUSER_EMAIL and redeploy if that is really what you want.",
        )
    if sum(1 for a in people if a.get("role") == "superuser") <= 1 and target.get("role") == "superuser":
        raise HTTPException(
            400, "Someone has to keep the keys — make another superuser first."
        )

    await database.delete(f"/auth/admins/{user_id}")
    log.warning("Access removed from user %s by %s", user_id, session.user.email)
    return {"ok": True}


# ------------------------------------------------------------ impersonation


@router.post("/impersonate", dependencies=[Depends(verify_csrf)])
async def impersonate(
    response: Response,
    email: str = Body(embed=True),
    session: AuthSession = Depends(require_users),
) -> dict:
    """Open a session as somebody else, to see what they see.

    A real session for the real account, not a pretend one: that is the only
    version of this that actually reproduces "it does not work for me". What
    makes it honest is that the session records who opened it — the banner says
    so on every page, the way back is `POST /auth/stop-impersonating`, and this
    line in the log names both people.

    Two things it will not do: impersonate the standing superuser (there is
    nothing to learn, and it would be a way to launder an action as theirs), and
    nest, because unwinding two layers is how somebody ends up stranded.

    An address with no account gets one, unverified — what a brand-new visitor
    with that address would see is exactly what this is for.
    """
    if session.is_impersonating:
        raise HTTPException(400, "Stop impersonating before starting again.")

    address = normalise_email(email)
    if _is_standing(address):
        raise HTTPException(400, "That is the standing superuser account.")

    if "@" not in address:
        raise HTTPException(400, "That does not look like an email address.")
    user = await resolve_or_create_user(address, verified=False)
    # Checked again on the resolved address: an Imperial alias collapses to
    # shortcode@ic.ac.uk, which could be the standing superuser's.
    if _is_standing(user.get("email")):
        raise HTTPException(400, "That is the standing superuser account.")

    token = generate_token()
    csrf_token = generate_token()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days)
    await database.post(
        "/auth/sessions",
        {
            "token_hash": hash_token(token),
            "csrf_token": csrf_token,
            "user_id": user["id"],
            "expires_at": expires.isoformat(),
            "impersonator_id": session.user.id,
        },
    )
    # Replaces the superuser's own cookie. Their session row is left alone and
    # simply stops being the one the browser presents; stopping mints a fresh
    # one rather than reviving it, so there is nothing to go stale.
    set_session_cookies(response, token, csrf_token)

    log.warning(
        "IMPERSONATION: %s is now signed in as %s",
        session.user.email,
        user["email"],
    )
    return {
        "ok": True,
        "email": user["email"],
        "message": f"You are now signed in as {user['email']}.",
    }


# ------------------------------------------------------------------ sponsors


@router.get("/sponsors")
async def list_sponsors(_: AuthSession = Depends(require_sponsors)) -> dict:
    """Full sponsor list with the committee-only fields and portal access.

    This is the one place `amount_pence` is returned — the public endpoint
    builds its response field by field precisely so it cannot leak here.
    """
    sponsors = await database.get("/sponsors", {"active_only": False}) or []
    totals = await database.get("/sponsors/internal/totals") or {}
    out = []
    for sponsor in sponsors:
        users = await database.get(f"/sponsors/{sponsor['id']}/users") or []
        out.append({
            **sponsor,
            "amount": format_price(sponsor.get("amount_pence", 0)),
            "users": users,
        })
    return {
        "sponsors": out,
        "totals": {
            **totals,
            "total": format_price(totals.get("total_pence", 0)),
        },
        "package": await agreement_service.standard_package(),
    }


@router.put("/sponsor-package/{clause}", dependencies=[Depends(verify_csrf)])
async def set_package_allowance(
    clause: str,
    allowance: int = Body(embed=True),
    session: AuthSession = Depends(require_sponsors),
) -> dict:
    """Set the standard package's allowance for one countable clause.

    Every sponsor without their own figure for it moves to the new number at
    once — in the portal and in what the API will accept. A sponsor given their
    own allowance on their agreement page keeps it.
    """
    spec = agreement_service.BY_KEY.get(clause)
    if spec is None or not spec.get("countable"):
        raise HTTPException(404, "No such countable clause")
    if allowance < 0:
        raise HTTPException(400, "An allowance can't be negative.")
    await database.put(
        f"/sponsors/terms/defaults/{clause}",
        {"allowance": allowance, "updated_by": session.user.id},
    )
    return {
        "ok": True,
        "package": await agreement_service.standard_package(),
        "message": (
            f"Standard package: {allowance} {spec['unit']}"
            f"{'' if allowance == 1 else 's'} a year."
        ),
    }


@router.delete("/sponsors/{sponsor_id}", dependencies=[Depends(verify_csrf)])
async def delete_sponsor(
    sponsor_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    """Remove a sponsor entirely, with their listings, claims and access."""
    await database.delete(f"/sponsors/{sponsor_id}")
    return {"ok": True}


@router.post("/sponsors", dependencies=[Depends(verify_csrf)])
async def create_sponsor(
    body: dict = Body(...), _: AuthSession = Depends(require_sponsors)
) -> dict:
    return await database.post("/sponsors", body)


@router.put("/sponsors/{sponsor_id}", dependencies=[Depends(verify_csrf)])
async def update_sponsor(
    sponsor_id: int,
    body: dict = Body(...),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    return await database.put(f"/sponsors/{sponsor_id}", body)


@router.post("/sponsors/reorder", dependencies=[Depends(verify_csrf)])
async def reorder_sponsors(
    order: list[int] = Body(embed=True),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    """Set the running order of the sponsor list.

    Committee-only: sponsors can edit their own card but not push themselves up
    the page. Takes the full list of ids in the order wanted.
    """
    for index, sponsor_id in enumerate(order):
        current = await database.get(f"/sponsors/{sponsor_id}")
        if current is None:
            continue
        await database.put(
            f"/sponsors/{sponsor_id}", {**current, "sort_order": (index + 1) * 10}
        )
    return {"ok": True, "ordered": len(order)}


@router.post("/sponsors/{sponsor_id}/users", dependencies=[Depends(verify_csrf)])
async def link_sponsor_user(
    sponsor_id: int,
    email: str = Body(embed=True),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    """Give an address access to a sponsor's portal.

    Deliberately on the `sponsors` capability and not on `users`: this creates
    an account that can see one company's portal and nothing else, which is
    sponsorship work, not access control. An admin who cannot grant committee
    roles can still set a sponsor up.

    The account is created if it does not exist yet, *unverified* — so access can
    be set up before the person has ever visited. Requiring them to sign in first
    made the committee wait on the sponsor to do something before it could be
    configured, which is the wrong way round.

    Unverified costs nothing here: the account cannot be used until they complete
    the code flow, which is what proves they control the address. Until then the
    row is just a permission waiting to be claimed.
    """
    address = normalise_email(email)
    if "@" not in address:
        raise HTTPException(400, "That does not look like an email address.")

    user = await resolve_or_create_user(address, verified=False)
    await database.post(f"/sponsors/{sponsor_id}/users/{user['id']}")
    return {
        "ok": True,
        "userId": user["id"],
        "invited": not user.get("email_verified", False),
        "message": (
            "Access granted. They can sign in with that address whenever they like."
        ),
    }


@router.delete("/sponsors/{sponsor_id}/users/{user_id}", dependencies=[Depends(verify_csrf)])
async def unlink_sponsor_user(
    sponsor_id: int, user_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    await database.delete(f"/sponsors/{sponsor_id}/users/{user_id}")
    return {"ok": True}


@router.get("/sponsors/{sponsor_id}/users")
async def sponsor_users(
    sponsor_id: int, _: AuthSession = Depends(require_sponsors)
) -> list[dict]:
    return await database.get(f"/sponsors/{sponsor_id}/users") or []


# ------------------------------------------------------- sponsor agreements
#
# The per-sponsor page: which clauses they signed, the events linked to them,
# what they have asked for, and the marketing waiting to go out. All of it on
# `sponsors`, except actually emailing members, which is bulk mail and so stays
# on `site` like every other send.


@router.get("/sponsors/inbox")
async def sponsor_inbox(_: AuthSession = Depends(require_sponsors)) -> dict:
    """Everything across all sponsors that is waiting on the committee."""
    marketing = await database.get("/sponsors/marketing/all", {"status": "pending"}) or []
    requests = await database.get("/sponsors/requests/all", {"open_only": True}) or []
    interest = await database.get("/sponsors/interest/open") or []
    listings = await database.get("/sponsors/listings/unannounced") or []
    notes = await database.get("/sponsors/notes/unread") or []
    # One line per thread, not per note: three notes about one event are one
    # thing to answer.
    threads: dict[tuple[int, str], dict] = {}
    for n in notes:
        key = (n["sponsor_id"], n["topic"])
        t = threads.setdefault(key, {
            "sponsorId": n["sponsor_id"], "sponsor": n["sponsor_name"],
            "topic": n["topic"], "label": n.get("topic_label") or "", "count": 0,
        })
        t["count"] += 1
        t["latest"] = " ".join((n.get("body") or "").split())[:120]
    return {
        "notes": list(threads.values()),
        "marketing": [
            {"id": m["id"], "sponsorId": m["sponsor_id"], "sponsor": m["sponsor_name"],
             "subject": m["subject"], "created": m.get("created_at")}
            for m in marketing
        ],
        "requests": [
            {"id": r["id"], "sponsorId": r["sponsor_id"], "sponsor": r["sponsor_name"],
             "format": r.get("format"), "status": r["status"], "created": r.get("created_at")}
            for r in requests
        ],
        "interest": [
            {"id": i["id"], "sponsorId": i["sponsor_id"], "sponsor": i["sponsor_name"],
             "event": i["event_title"], "eventId": i["event_id"],
             "date": format_date(_parse_dt(i.get("event_starts_at")))}
            for i in interest
        ],
        "listings": [
            {"id": l["id"], "title": l["title"], "sponsor": l["sponsor_name"],
             "location": l.get("location")}
            for l in listings
        ],
    }


async def _sponsor_or_404(sponsor_id: int) -> dict:
    sponsor = await database.get(f"/sponsors/{sponsor_id}")
    if sponsor is None:
        raise HTTPException(404, "No such sponsor")
    return sponsor


@router.get("/sponsors/{sponsor_id}/detail")
async def sponsor_detail(
    sponsor_id: int, session: AuthSession = Depends(require_sponsors)
) -> dict:
    sponsor = await _sponsor_or_404(sponsor_id)
    claims = await database.get(f"/sponsors/{sponsor_id}/claims") or []
    events = await database.get("/events", {"published_only": False, "limit": 500}) or []
    year = academic_year()
    notes = await database.get(f"/sponsors/{sponsor_id}/notes") or []
    note_threads = sponsor_notes.threads(notes, "committee")
    # Opening the sponsor's page is reading their notes: it is where they are
    # shown in full and answered.
    if any(t["unread"] for t in note_threads):
        await database.post(
            f"/sponsors/{sponsor_id}/notes/read", params={"from_committee": False}
        )
    return {
        "notes": note_threads,
        "sponsor": {
            **sponsor,
            "amount": format_price(sponsor.get("amount_pence", 0)),
        },
        "year": year,
        "terms": await agreement_service.terms(sponsor_id),
        # What used each clause this year, itemised: linked events, marketing,
        # and anything recorded by hand.
        "counted": await agreement_service.counted(sponsor_id, year),
        "claims": [
            {
                "eventId": c["event_id"],
                "slug": c["slug"],
                "title": c["title"],
                "date": format_date(_parse_dt(c.get("starts_at"))),
                "status": c["status"],
                "clause": c.get("clause"),
                "note": c.get("note"),
                "published": c.get("published"),
                "thisYear": c.get("academic_year") == year,
            }
            for c in claims
        ],
        "requests": await database.get(f"/sponsors/{sponsor_id}/requests") or [],
        "marketing": await database.get(f"/sponsors/{sponsor_id}/marketing") or [],
        "contacts": await database.get(f"/sponsors/{sponsor_id}/contacts") or [],
        "users": await database.get(f"/sponsors/{sponsor_id}/users") or [],
        # Every event, newest first, for the "link an event" picker.
        "events": [
            {
                "id": e["id"],
                "title": e["title"],
                "date": format_date(_parse_dt(e.get("starts_at"))),
                "published": e.get("published"),
            }
            for e in sorted(events, key=lambda e: str(e.get("starts_at") or ""), reverse=True)
        ],
        "eventClauses": [
            {"key": k, "label": agreement_service.BY_KEY[k]["label"]}
            for k in agreement_service.EVENT_CLAUSES
        ],
        "audiences": agreement_service.MARKETING_AUDIENCES,
        "canEmail": session.can("site"),
    }


@router.post("/sponsors/{sponsor_id}/notes", dependencies=[Depends(verify_csrf)])
async def reply_to_sponsor(
    sponsor_id: int,
    topic: str = Body(default="general", embed=True),
    body: str = Body(embed=True),
    session: AuthSession = Depends(require_sponsors),
) -> dict:
    """Reply in a thread, or start one. The sponsor's portal users are emailed
    to say there is a reply; the reply itself is read in the portal."""
    await _sponsor_or_404(sponsor_id)
    label = await sponsor_notes.resolve_topic(sponsor_id, topic, committee=True)
    await database.post(
        f"/sponsors/{sponsor_id}/notes",
        {
            "topic": topic,
            "topic_label": label,
            "body": sponsor_notes.clean_body(body),
            "from_committee": True,
            "author_id": session.user.id,
        },
    )
    sponsor_notes.alert_sponsor(sponsor_id, topic, label)
    return {"ok": True, "message": "Reply sent. The sponsor has been emailed to say so."}


@router.put("/sponsors/{sponsor_id}/terms/{clause}", dependencies=[Depends(verify_csrf)])
async def set_sponsor_term(
    sponsor_id: int,
    clause: str,
    agreed: bool = Body(embed=True),
    allowance: int | None = Body(default=None, embed=True),
    note: str = Body(default="", embed=True),
    done: bool = Body(default=False, embed=True),
    session: AuthSession = Depends(require_sponsors),
) -> dict:
    """Record what this sponsor's agreement says about one clause."""
    spec = agreement_service.BY_KEY.get(clause)
    if spec is None:
        raise HTTPException(404, "No such clause")
    await _sponsor_or_404(sponsor_id)
    if allowance is not None and allowance < 0:
        raise HTTPException(400, "An allowance can't be negative.")
    existing = {
        r["clause"]: r for r in await database.get(f"/sponsors/{sponsor_id}/terms") or []
    }.get(clause)
    # Keep the original date when something already marked done is saved again,
    # so "done on 3 Nov" does not become "done today" on every save.
    done_at = None
    if done:
        done_at = (existing or {}).get("done_at") or datetime.now(timezone.utc).isoformat()
    await database.put(
        f"/sponsors/{sponsor_id}/terms/{clause}",
        {
            "clause": clause,
            "agreed": agreed,
            "allowance": allowance if spec.get("countable") else None,
            "note": note.strip(),
            "done_at": done_at,
            "updated_by": session.user.id,
        },
    )
    return {"ok": True, "terms": await agreement_service.terms(sponsor_id)}


@router.post("/sponsors/{sponsor_id}/usage", dependencies=[Depends(verify_csrf)])
async def record_sponsor_usage(
    sponsor_id: int,
    clause: str = Body(embed=True),
    title: str = Body(embed=True),
    happened_on: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_sponsors),
) -> dict:
    """Record a use of a countable clause that happened outside the portal — a
    workshop arranged by email, a post shared straight to Instagram.

    It counts against the clause for the academic year it happened in (this
    year when no date is given) and shows in the sponsor's portal with the rest
    of what used it. Over the allowance is allowed: the committee's record of
    what happened is the record, and the agreement page shows it in red.
    """
    spec = agreement_service.BY_KEY.get(clause)
    if spec is None or not spec.get("countable"):
        raise HTTPException(404, "No such countable clause")
    await _sponsor_or_404(sponsor_id)
    title = title.strip()
    if not title:
        raise HTTPException(400, "Say what it was — the sponsor sees this.")
    when = None
    if happened_on:
        try:
            when = datetime.fromisoformat(str(happened_on)[:10])
        except ValueError:
            raise HTTPException(400, "That date doesn't look right.")
    year = academic_year(when)
    await database.post(
        f"/sponsors/{sponsor_id}/usage-records",
        {
            "clause": clause,
            "academic_year": year,
            "title": title[:200],
            "happened_on": when.date().isoformat() if when else None,
            "created_by": session.user.id,
        },
    )
    current = academic_year()
    return {
        "ok": True,
        "terms": await agreement_service.terms(sponsor_id),
        "counted": await agreement_service.counted(sponsor_id, current),
        "message": (
            f"Recorded against {spec['label']}."
            if year == current
            else f"Recorded against {spec['label']} for {year}, the year it happened — "
            f"it does not count towards {current}."
        ),
    }


@router.delete("/sponsors/{sponsor_id}/usage/{record_id}", dependencies=[Depends(verify_csrf)])
async def remove_sponsor_usage(
    sponsor_id: int, record_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    """Take back a use recorded by hand. Uses that came through the site are
    undone where they came from: unlink the event, decline the request."""
    removed = await database.delete(f"/sponsors/{sponsor_id}/usage-records/{record_id}")
    if removed is None:
        raise HTTPException(404, "No such record")
    return {
        "ok": True,
        "terms": await agreement_service.terms(sponsor_id),
        "counted": await agreement_service.counted(sponsor_id),
        "message": "Removed. That use is back in their allowance.",
    }


@router.put("/sponsors/{sponsor_id}/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def link_sponsor_event(
    sponsor_id: int,
    event_id: int,
    clause: str | None = Body(default=None, embed=True),
    note: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    """Link an existing event to a sponsor — or confirm the interest they
    registered in it — recording which clause of the agreement it delivers.

    A confirmed link takes the event off every other sponsor's sponsorable list
    and, once the sponsor is published, shows them on the event page.
    """
    await _sponsor_or_404(sponsor_id)
    if await database.get(f"/events/{event_id}") is None:
        raise HTTPException(404, "No such event")
    if clause and clause not in agreement_service.EVENT_CLAUSES:
        raise HTTPException(400, "An event can't deliver that clause.")
    await database.put(
        f"/sponsors/{sponsor_id}/links/{event_id}",
        {"clause": clause or None, "note": (note or "").strip() or None},
    )
    return {"ok": True, "message": "Linked."}


@router.delete("/sponsors/{sponsor_id}/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def unlink_sponsor_event(
    sponsor_id: int, event_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    await database.delete(f"/sponsors/{sponsor_id}/claims/{event_id}")
    return {"ok": True}


@router.post("/sponsors/{sponsor_id}/events/{event_id}/decline", dependencies=[Depends(verify_csrf)])
async def decline_sponsor_interest(
    sponsor_id: int, event_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    """Say no to a sponsor's interest. Kept as a row rather than deleted, so
    the sponsor sees an answer instead of their request silently vanishing."""
    claims = await database.get(f"/sponsors/{sponsor_id}/claims") or []
    claim = next((c for c in claims if c["event_id"] == event_id), None)
    if claim is None:
        raise HTTPException(404, "No such interest")
    await database.patch(f"/sponsors/claims/{claim['id']}", params={"status": "declined"})
    return {"ok": True}


REQUEST_STATUSES = {"open", "in_progress", "closed"}


@router.patch("/sponsor-requests/{request_id}", dependencies=[Depends(verify_csrf)])
async def update_sponsor_request(
    request_id: int,
    status: str | None = Body(default=None, embed=True),
    response: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    if status is not None and status not in REQUEST_STATUSES:
        raise HTTPException(400, "A request is open, in progress or closed.")
    row = await database.patch(
        f"/sponsors/requests/{request_id}",
        {"status": status, "response": response.strip() if response is not None else None},
    )
    if row is None:
        raise HTTPException(404, "No such request")
    return {"ok": True, "request": row}


async def _marketing_or_404(marketing_id: int) -> dict:
    item = await database.get(f"/sponsors/marketing/{marketing_id}")
    if item is None:
        raise HTTPException(404, "No such marketing request")
    return item


async def _finish_marketing(item: dict, session: AuthSession) -> dict:
    """Mark a request shared once every channel it asked for is done."""
    done_email = "email" not in item["channels"] or item.get("emailed_at")
    done_social = "social" not in item["channels"] or item.get("posted_at")
    if done_email and done_social and item["status"] == "pending":
        item = await database.patch(
            f"/sponsors/marketing/{item['id']}",
            {"status": "shared", "decided_by": session.user.id},
        )
    return item


@router.patch("/sponsor-marketing/{marketing_id}", dependencies=[Depends(verify_csrf)])
async def edit_sponsor_marketing(
    marketing_id: int,
    subject: str | None = Body(default=None, embed=True),
    body: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    """Tidy a request before it goes out — a typo, a broken link."""
    item = await _marketing_or_404(marketing_id)
    if item["status"] != "pending":
        raise HTTPException(409, "That request has already been dealt with.")
    if subject is not None and not subject.strip():
        raise HTTPException(400, "It needs a subject.")
    row = await database.patch(
        f"/sponsors/marketing/{marketing_id}",
        {"subject": subject.strip() if subject else None, "body": body},
    )
    return {"ok": True, "item": row}


@router.post("/sponsor-marketing/{marketing_id}/preview", dependencies=[Depends(verify_csrf)])
async def preview_sponsor_marketing(
    marketing_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    item = await _marketing_or_404(marketing_id)
    sponsor = await _sponsor_or_404(item["sponsor_id"])
    html, text = agreement_service.marketing_email(item, sponsor["name"])
    recipients = await emailing.resolve(item["audiences"])
    return {
        "html": html,
        "text": text,
        "recipients": len(recipients),
        "lists": [r["email"] for r in recipients if r.get("list")],
    }


@router.post("/sponsor-marketing/{marketing_id}/send", dependencies=[Depends(verify_csrf)])
async def send_sponsor_marketing(
    marketing_id: int, session: AuthSession = Depends(require_site)
) -> dict:
    """Email a sponsor's marketing to the audience they chose.

    `site`, not `sponsors`: this is a bulk send from the publicity mailbox, and
    who may email the whole membership is decided in one place for every kind
    of message. Same guards as a newsletter — cap, empty audience, mailbox.
    """
    item = await _marketing_or_404(marketing_id)
    if item["status"] != "pending":
        raise HTTPException(409, "That request has already been dealt with.")
    if "email" not in item["channels"]:
        raise HTTPException(400, "This one was asked for on social media only.")
    if item.get("emailed_at"):
        raise HTTPException(409, "This has already been emailed.")
    sponsor = await _sponsor_or_404(item["sponsor_id"])

    recipients = await emailing.resolve(item["audiences"])
    _check_sendable(item["subject"], item["audiences"], recipients)
    html, text = agreement_service.marketing_email(item, sponsor["name"])

    campaign = await database.post(
        "/email/campaigns",
        {
            "subject": item["subject"],
            "heading": item["subject"],
            "body": item.get("body") or "",
            # Recorded with the sponsor, so the email history says whose
            # material it was.
            "audiences": item["audiences"] + [f"sponsor:{sponsor['slug']}"],
            "created_by": session.user.id,
        },
    )
    # Marked before the send runs rather than after: a send takes minutes, and
    # a second press in that window must not mail everybody twice.
    item = await database.patch(
        f"/sponsors/marketing/{marketing_id}",
        {"campaign_id": campaign["id"], "mark_emailed": True},
    )

    async def build(batch: list[dict], unsubscribe: str) -> list[str]:
        return await mailer.send_campaign(
            [r["email"] for r in batch], item["subject"], item["subject"],
            text, html, unsubscribe,
        )

    _dispatch(emailing.send_bulk(campaign["id"], recipients, build))
    item = await _finish_marketing(item, session)
    return {
        "ok": True,
        "status": item["status"],
        "message": (
            f"Sending to {len(recipients)} recipient(s). This takes about "
            f"{_estimate_minutes(len(recipients))}."
        ),
    }


@router.post("/sponsor-marketing/{marketing_id}/posted", dependencies=[Depends(verify_csrf)])
async def mark_sponsor_marketing_posted(
    marketing_id: int, session: AuthSession = Depends(require_sponsors)
) -> dict:
    """Record that it went out on social media — posted by hand, outside the site."""
    item = await _marketing_or_404(marketing_id)
    if item["status"] != "pending":
        raise HTTPException(409, "That request has already been dealt with.")
    item = await database.patch(
        f"/sponsors/marketing/{marketing_id}", {"mark_posted": True}
    )
    item = await _finish_marketing(item, session)
    return {"ok": True, "status": item["status"]}


@router.post("/sponsor-marketing/{marketing_id}/complete", dependencies=[Depends(verify_csrf)])
async def complete_sponsor_marketing(
    marketing_id: int,
    response: str = Body(default="", embed=True),
    session: AuthSession = Depends(require_sponsors),
) -> dict:
    """Close a request as shared without doing every channel it asked for —
    when it went in the newsletter instead, say. The note tells the sponsor."""
    item = await _marketing_or_404(marketing_id)
    if item["status"] != "pending":
        raise HTTPException(409, "That request has already been dealt with.")
    await database.patch(
        f"/sponsors/marketing/{marketing_id}",
        {"status": "shared", "response": response.strip(), "decided_by": session.user.id},
    )
    return {"ok": True}


@router.post("/sponsor-marketing/{marketing_id}/decline", dependencies=[Depends(verify_csrf)])
async def decline_sponsor_marketing(
    marketing_id: int,
    response: str = Body(default="", embed=True),
    session: AuthSession = Depends(require_sponsors),
) -> dict:
    """Decline, with the reason the sponsor will read. Gives the share back."""
    item = await _marketing_or_404(marketing_id)
    if item["status"] != "pending":
        raise HTTPException(409, "That request has already been dealt with.")
    if item.get("emailed_at"):
        raise HTTPException(409, "This has already been emailed, so it can't be declined.")
    if not response.strip():
        raise HTTPException(400, "Tell them why — they'll see this.")
    await database.patch(
        f"/sponsors/marketing/{marketing_id}",
        {"status": "declined", "response": response.strip(), "decided_by": session.user.id},
    )
    return {"ok": True}


# ---------------------------------------------------- new roles to members
#
# The portal tells sponsors members are notified of what they post. This is
# how: one email listing every live role members have not yet heard about, to
# the people who opted in to "Jobs and internships". Pressed by the committee
# rather than sent on post, so a sponsor posting six roles in an afternoon
# sends one email, not six — and nothing reaches members unseen.


def _roles_digest(listings: list[dict]) -> str:
    lines = ["New roles from the companies that sponsor EESoc:", ""]
    for l in listings:
        bits = [f"**{l['title']}** — {l['sponsor_name']}"]
        if l.get("location"):
            bits.append(l["location"])
        if l.get("closes_on"):
            bits.append(f"closes {format_date(_parse_dt(l['closes_on']))}")
        line = " · ".join(bits)
        if l.get("apply_url"):
            line += f" · [apply]({l['apply_url']})"
        lines.append(f"- {line}")
    lines += ["", "Every open role is at [eesoc.com/jobs](/jobs)."]
    return "\n".join(lines)


@router.get("/sponsors/roles-digest")
async def roles_digest_preview(_: AuthSession = Depends(require_sponsors)) -> dict:
    listings = await database.get("/sponsors/listings/unannounced") or []
    recipients = await emailing.resolve(["topic:roles"]) if listings else []
    html, _text = emailing.render_body(_roles_digest(listings)) if listings else ("", "")
    return {"count": len(listings), "recipients": len(recipients), "html": html}


@router.post("/sponsors/roles-digest", dependencies=[Depends(verify_csrf)])
async def send_roles_digest(session: AuthSession = Depends(require_site)) -> dict:
    listings = await database.get("/sponsors/listings/unannounced") or []
    if not listings:
        raise HTTPException(400, "There are no new roles to tell members about.")
    audiences = ["topic:roles"]
    recipients = await emailing.resolve(audiences)
    subject = (
        f"New role: {listings[0]['title']} at {listings[0]['sponsor_name']}"
        if len(listings) == 1
        else f"{len(listings)} new roles from our sponsors"
    )
    _check_sendable(subject, audiences, recipients)

    body = _roles_digest(listings)
    html, text = emailing.render_body(body)
    campaign = await database.post(
        "/email/campaigns",
        {"subject": subject, "heading": subject, "body": body,
         "audiences": audiences + ["jobs-digest"], "created_by": session.user.id},
    )
    # Marked now for the same reason as marketing: a second press during a
    # minutes-long send must not repeat it.
    await database.post("/sponsors/listings/announced", [l["id"] for l in listings])

    async def build(batch: list[dict], unsubscribe: str) -> list[str]:
        return await mailer.send_campaign(
            [r["email"] for r in batch], subject, subject, text, html, unsubscribe
        )

    _dispatch(emailing.send_bulk(campaign["id"], recipients, build))
    return {
        "ok": True,
        "message": f"Sending {len(listings)} role(s) to {len(recipients)} subscriber(s).",
    }


# ------------------------------------------------------------------- lockers


@router.get("/lockers")
async def admin_lockers(_: AuthSession = Depends(require_site)) -> list[dict]:
    return await database.get("/lockers") or []


@router.patch("/lockers/{locker_id}", dependencies=[Depends(verify_csrf)])
async def update_locker(
    locker_id: int,
    is_active: bool | None = Body(default=None, embed=True),
    note: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_site),
) -> dict:
    return await database.patch(
        f"/lockers/{locker_id}", params={"is_active": is_active, "note": note}
    )


@router.get("/locker-holders")
async def locker_holders(_: AuthSession = Depends(require_site)) -> dict:
    """Everyone currently holding a locker, with their year."""
    rows = await database.get("/lockers/holders") or []
    return {
        "holders": [
            {
                "bookingId": r["id"],
                "locker": lockers_util.name(r),
                "email": r["email"],
                "name": " ".join(
                    p for p in [r.get("first_name"), r.get("last_name")] if p
                ),
                "academicYear": r["academic_year"],
                "since": r["created_at"],
            }
            for r in rows
        ],
        "count": len(rows),
        "currentYear": academic_year(),
    }


@router.post("/lockers/notify-clearout", dependencies=[Depends(verify_csrf)])
async def notify_clearout(
    deadline: str = Body(embed=True), _: AuthSession = Depends(require_site)
) -> dict:
    """Tell every current holder to empty their locker by a date.

    Deliberately separate from releasing them: the handover's first locker
    instruction is to warn people first, because anything left behind is cut off.
    """
    if not (deadline or "").strip():
        raise HTTPException(400, "Give a deadline to put in the email.")

    rows = await database.get("/lockers/holders") or []
    sent = 0
    for row in rows:
        if await mailer.send_locker_clearout(
            str(row["email"]), lockers_util.name(row), deadline.strip()
        ):
            sent += 1
    return {
        "ok": True,
        "holders": len(rows),
        "sent": sent,
        "message": (
            f"Emailed {sent} of {len(rows)} holders."
            if sent
            else f"{len(rows)} holders found, but no mail was sent — check SMTP."
        ),
    }


@router.post("/lockers/release-year", dependencies=[Depends(verify_csrf)])
async def release_locker_year(
    year: str = Body(embed=True), _: AuthSession = Depends(require_site)
) -> dict:
    """Release every live booking from one academic year.

    The year is required rather than inferred: guessing "everything before now"
    would wipe the year that has just started if this were run a day late.
    """
    if year == academic_year():
        raise HTTPException(
            400,
            f"{year} is the current academic year. Releasing it would clear "
            "everybody's live locker. Pass the year that has just ended.",
        )
    result = await database.post("/lockers/release-year", params={"academic_year": year})
    return {"ok": True, **(result or {})}


# ------------------------------------------------------- free locker grants


# A typo guard, not a policy: nobody needs a dozen lockers, and an allowance of
# 100 would let one person take a third of the building.
MAX_FREE_LOCKERS = 10


async def _allowance_shortcode(who: str) -> str:
    """The shortcode an allowance is keyed on, from what a person typed.

    'ab1234', 'AB1234' and 'ab1234@ic.ac.uk' are the shortcode as given. A
    name-style address (jane.doe24@imperial.ac.uk) is looked up in the members
    feed, which records each member's login. Anything still not shaped like a
    shortcode is refused: stored, it would match nobody's account, and the
    allowance would silently never apply.
    """
    typed = (who or "").strip()
    shortcode = clean_shortcode(typed)
    if "@" in typed and not is_shortcode(shortcode):
        member = await find_member(typed)
        shortcode = clean_shortcode(member.get("login")) if member else None
    if not is_shortcode(shortcode):
        raise HTTPException(
            400,
            f"Couldn't find a shortcode for “{typed}”. Give their college "
            "shortcode, like ab1234.",
        )
    return shortcode


@router.get("/locker-grants")
async def locker_grants(
    year: str | None = None, _: AuthSession = Depends(require_locker_allowances)
) -> dict:
    """Everyone given free lockers this year, and how many they now hold."""
    year = year or academic_year()
    rows = await database.get("/lockers/grants", {"academic_year": year}) or []
    return {
        "year": year,
        "max": MAX_FREE_LOCKERS,
        "allowances": [
            {
                "shortcode": r["shortcode"],
                "name": " ".join(
                    p for p in [r.get("first_name"), r.get("last_name")] if p
                ),
                "email": r.get("email"),
                "isMember": bool(r.get("is_member")),
                "free": r["free_count"],
                "held": r.get("held") or 0,
                "note": r.get("note"),
                "givenBy": r.get("granted_by_email"),
                "givenAt": r["granted_at"],
            }
            for r in rows
        ],
    }


@router.put("/locker-grants/{shortcode}", dependencies=[Depends(verify_csrf)])
async def set_locker_grant(
    shortcode: str,
    free_count: int = Body(default=1, embed=True),
    note: str | None = Body(default=None, embed=True),
    year: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_locker_allowances),
) -> dict:
    """Give somebody free lockers, as the old system did for committee.

    Keyed on shortcode so an allowance can be made before that person has ever
    signed in, and per academic year so it lapses at handover instead of
    following somebody who has left the committee. Setting it again replaces
    the number rather than adding to it, so the form reads the same whether
    the allowance is new or being changed.
    """
    if not 1 <= free_count <= MAX_FREE_LOCKERS:
        raise HTTPException(
            400, f"An allowance is between 1 and {MAX_FREE_LOCKERS} lockers."
        )
    cleaned = await _allowance_shortcode(shortcode)
    return await database.put(
        f"/lockers/grants/{cleaned}",
        params={
            "academic_year": year or academic_year(),
            "free_count": free_count,
            "note": (note or "").strip() or None,
            "granted_by": session.user.id,
        },
    )


@router.delete("/locker-grants/{shortcode}", dependencies=[Depends(verify_csrf)])
async def delete_locker_grant(
    shortcode: str,
    year: str | None = None,
    _: AuthSession = Depends(require_locker_allowances),
) -> dict:
    """Take an allowance away. A locker already booked with it stays booked —
    the allowance is only checked when somebody books."""
    await database.delete(
        f"/lockers/grants/{shortcode}",
        {"academic_year": year or academic_year()},
    )
    return {"ok": True}


@router.get("/locker-bookings")
async def locker_bookings(_: AuthSession = Depends(require_site)) -> list[dict]:
    return await database.get("/lockers/bookings", {"active_only": True}) or []


# --------------------------------------------------------------------- email
#
# Two shapes of bulk send, sharing everything below the surface: a free-form
# newsletter, and a message about one event with that event's details rendered
# underneath. Both pick from the same audience list, both are recorded as
# campaigns, both go from the publicity mailbox with a working unsubscribe.


@router.get("/email/audiences")
async def email_audiences(_: AuthSession = Depends(require_site)) -> dict:
    """Who can be mailed, with live counts, plus the state of both mailboxes.

    The mail status is here rather than on the dashboard because this is the
    screen where it matters: pressing send with a mailbox that cannot
    authenticate produces a campaign that silently fails for every recipient.
    """
    return {
        "audiences": await emailing.audiences(),
        "mail": mailer.describe(),
        "academicYear": academic_year(),
    }


@router.post("/email/preview", dependencies=[Depends(verify_csrf)])
async def preview_email(
    body: str = Body(default="", embed=True),
    audiences: list[str] = Body(default=[], embed=True),
    _: AuthSession = Depends(require_site),
) -> dict:
    """Render exactly what will be sent, and count exactly who will get it.

    Same renderer as the send path, deliberately: a preview produced by
    different code is a preview of something else.
    """
    html, text = emailing.render_body(body)
    recipients = await emailing.resolve(audiences) if audiences else []
    return {
        "html": html,
        "text": text,
        "recipients": len(recipients),
        # Shown so somebody can sanity-check the audience before sending to it,
        # without dumping four hundred addresses into the page.
        "sample": [r["email"] for r in recipients[:10]],
        "lists": [r["email"] for r in recipients if r.get("list")],
    }


def _check_sendable(subject: str, audiences: list[str], recipients: list) -> None:
    if not subject.strip():
        raise HTTPException(400, "Give the email a subject.")
    if not audiences:
        raise HTTPException(400, "Choose who it goes to.")
    if not recipients:
        raise HTTPException(
            400,
            "That audience is empty — nobody would receive this. Check the "
            "membership sync has run.",
        )
    if len(recipients) > settings.bulk_max_recipients:
        raise HTTPException(
            400,
            f"{len(recipients)} recipients is more than the {settings.bulk_max_recipients} "
            "cap. That is almost certainly an audience picked by accident.",
        )
    if not mailer.configured("publicity"):
        raise HTTPException(
            503,
            "No mail credentials, so nothing would be sent. Check the publicity "
            "mailbox settings.",
        )


@router.post("/email/send", dependencies=[Depends(verify_csrf)])
async def send_newsletter(
    subject: str = Body(embed=True),
    body: str = Body(default="", embed=True),
    heading: str | None = Body(default=None, embed=True),
    audiences: list[str] = Body(default=[], embed=True),
    session: AuthSession = Depends(require_site),
) -> dict:
    """Send a newsletter.

    Returns as soon as the campaign is recorded rather than when the last
    message goes out: pacing means a few hundred recipients takes minutes, and
    an admin screen that hangs for that long gets reloaded — which is how
    somebody ends up sending twice.
    """
    recipients = await emailing.resolve(audiences)
    _check_sendable(subject, audiences, recipients)

    html, text = emailing.render_body(body)
    campaign = await database.post(
        "/email/campaigns",
        {
            "subject": subject.strip(),
            "heading": (heading or subject).strip(),
            "body": body,
            "audiences": audiences,
            "created_by": session.user.id,
        },
    )

    async def build(batch: list[dict], unsubscribe: str) -> list[str]:
        return await mailer.send_campaign(
            [r["email"] for r in batch],
            subject.strip(),
            (heading or subject).strip(),
            text,
            html,
            unsubscribe,
        )

    _dispatch(emailing.send_bulk(campaign["id"], recipients, build))
    return {
        "ok": True,
        "campaignId": campaign["id"],
        "recipients": len(recipients),
        "message": (
            f"Sending to {len(recipients)} recipient(s). This takes about "
            f"{_estimate_minutes(len(recipients))}; the history below updates as it goes."
        ),
    }


@router.post("/events/{event_id}/announce", dependencies=[Depends(verify_csrf)])
async def announce_event(
    event_id: int,
    subject: str = Body(embed=True),
    body: str = Body(default="", embed=True),
    heading: str | None = Body(default=None, embed=True),
    audiences: list[str] = Body(default=[], embed=True),
    session: AuthSession = Depends(require_site),
) -> dict:
    """Email people about one event — an announcement, a reminder, a room change.

    The committee writes the message; the event's date, venue, ticket list and
    booking link are rendered underneath from the database. Retyping those into
    a reminder is how a message ends up advertising last week's room.
    """
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")

    recipients = await emailing.resolve(audiences)
    _check_sendable(subject, audiences, recipients)

    html, text = emailing.render_body(body)
    starts = _parse_dt(event.get("starts_at"))
    ends = _parse_dt(event.get("ends_at"))
    when = (
        f"{format_date(starts)}, {format_time_range(starts, ends)}" if starts else "TBC"
    )
    doors = format_doors_close(event.get("starts_at"), event.get("doors_close_at"))
    tickets = [
        (t["name"], format_price(t.get("price_pence", 0)))
        for t in event.get("ticket_types", [])
        if not t.get("sold_out")
    ]
    url = f"{settings.site_origin}/events/{event['slug']}"

    campaign = await database.post(
        "/email/campaigns",
        {
            "subject": subject.strip(),
            "heading": (heading or subject).strip(),
            "body": body,
            # Recorded with the event, so the history says which event a
            # reminder was about rather than just "9 audiences".
            "audiences": audiences + [f"event:{event['slug']}"],
            "created_by": session.user.id,
        },
    )

    async def build(batch: list[dict], unsubscribe: str) -> list[str]:
        return await mailer.send_event_announcement(
            [r["email"] for r in batch],
            subject.strip(),
            event["title"],
            when,
            event.get("venue") or "",
            url,
            unsubscribe,
            body_text=text,
            body_html=html,
            tickets=tickets,
            heading=(heading or subject).strip(),
            doors_close=doors,
        )

    # kind/event_id, so a pod replaced mid-send can resume this specific message
    # rather than leaving the campaign half-sent.
    _dispatch(
        emailing.send_bulk(
            campaign["id"], recipients, build,
            kind="event_announcement", event_id=event_id,
        )
    )
    return {
        "ok": True,
        "campaignId": campaign["id"],
        "recipients": len(recipients),
        "message": (
            f"Sending to {len(recipients)} recipient(s) about {event['title']}. "
            f"This takes about {_estimate_minutes(len(recipients))}."
        ),
    }


@router.get("/email/test-recipients")
async def email_test_list(_: AuthSession = Depends(require_site)) -> dict:
    return {"recipients": await emailing.test_recipients()}


@router.post("/email/test-recipients", dependencies=[Depends(verify_csrf)])
async def email_test_add(
    email: str = Body(embed=True), session: AuthSession = Depends(require_site)
) -> dict:
    address = normalise_email(email)
    if not address or "@" not in address:
        raise HTTPException(400, "That doesn't look like an email address.")
    await database.post(
        "/email/test-recipients", {"email": address, "added_by": session.user.id}
    )
    return {"recipients": await emailing.test_recipients()}


@router.delete("/email/test-recipients", dependencies=[Depends(verify_csrf)])
async def email_test_remove(
    email: str, _: AuthSession = Depends(require_site)
) -> dict:
    await database.delete("/email/test-recipients", {"email": email})
    return {"recipients": await emailing.test_recipients()}


@router.get("/email/campaigns")
async def email_campaigns(
    limit: int = 30, _: AuthSession = Depends(require_site)
) -> list[dict]:
    return await database.get("/email/campaigns", {"limit": limit}) or []


@router.get("/events/{event_id}/sale-alerts")
async def event_sale_alerts(
    event_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """Who is waiting to hear that this event is on sale.

    Worth seeing before publishing a sale date: a hundred people waiting is a
    different decision from three.
    """
    await _editable_event(event_id, session)
    rows = await database.get("/email/sale-alerts", {"event_id": event_id}) or []
    return {
        "count": len(rows),
        "pending": sum(1 for r in rows if r.get("notified_at") is None),
        "alerts": [
            {
                "email": r["email"],
                "since": r.get("created_at"),
                "notified": r.get("notified_at") is not None,
            }
            for r in rows
        ],
    }


@router.get("/email/optouts")
async def email_optouts(_: AuthSession = Depends(require_site)) -> list[dict]:
    """Addresses that have unsubscribed. Read-only on purpose: an admin putting
    somebody back on a list by hand is the one thing an unsubscribe must
    prevent."""
    return await database.get("/email/optouts") or []


@router.post("/email/announce-now", dependencies=[Depends(verify_csrf)])
async def announce_now(_: AuthSession = Depends(require_site)) -> dict:
    """Run the last-chance reminder pass immediately.

    The loop runs every few minutes anyway; this is for when somebody does not
    want to wait for it. On-sale emails are not part of it: they are drafted
    when the event is saved and sent at their approved time.
    """
    return await announce_service.run_once()


# ------------------------------------------------------------------ whatsapp


@router.post("/whatsapp/render", dependencies=[Depends(verify_csrf)])
async def whatsapp_render(
    heading: str = Body(default="", embed=True),
    body: str = Body(default="", embed=True),
    event_id: int | None = Body(default=None, embed=True),
    kind: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_site),
) -> dict:
    """The WhatsApp version of an announcement, and a link that opens WhatsApp
    with it filled in. Sends nothing: a person posts it from their own
    WhatsApp — services/whatsapp.py says why the site never does.

    Same people as email, because it is the same announcement to a crowd.
    """
    event = None
    if event_id is not None:
        event = await database.get(f"/events/{event_id}")
        if event is None:
            raise HTTPException(404, "No such event")
    text = await whatsapp.message(heading, body, event, kind)
    return {
        "text": text,
        "url": whatsapp.share_url(text) if text else None,
        "warning": whatsapp.warning(event, kind),
    }


def _estimate_minutes(count: int) -> str:
    # One BCC'd message per batch, spaced by the interval, plus a few seconds
    # for Exchange to take each one.
    batches = -(-count // max(1, settings.bulk_batch_size))
    seconds = batches * (settings.bulk_send_interval_seconds + 5)
    if seconds < 90:
        return "under a minute"
    return f"{round(seconds / 60)} minutes"


def _parse_dt(value):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# asyncio keeps only a weak reference to a running task, so a fire-and-forget
# coroutine can be garbage collected mid-send. Holding the reference until it
# finishes is the documented way to avoid that — the same pattern the login
# code dispatcher uses.
_background: set = set()


def _dispatch(coro) -> None:
    import asyncio

    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
