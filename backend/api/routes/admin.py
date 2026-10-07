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
from services import announce as announce_service
from services import emailing, purchase, storage
from services.identity import resolve_or_create_user
from utils.auth import (
    generate_token,
    hash_token,
    normalise_email,
    set_session_cookies,
)
from utils import email as mailer
from utils.present import format_date, format_price, format_time_range
from services import sync as sync_service
from utils.deps import (
    assert_may_edit_event,
    may_edit_event,
    require_admin,
    require_event_editor,
    require_feature,
    require_any_capability,
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
        "warnings": _warnings(counts, union_health),
    }


def _warnings(counts: dict, union_health: dict | None) -> list[dict]:
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
    return [{**e, "can_edit": may_edit_event(session, e)} for e in events]


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
    return await database.post(
        "/events", body, params={"created_by": session.user.id}
    )


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
    return await database.put(f"/events/{event_id}", body)


@router.delete("/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def delete_event(
    event_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    await _editable_event(event_id, session)
    await database.delete(f"/events/{event_id}")
    return {"ok": True}


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
    return {"ok": True, "reservation": row}


@router.post("/reservations/{reservation_id}/cancel", dependencies=[Depends(verify_csrf)])
async def cancel_reservation(
    reservation_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    """Cancel a booking and hand the seat to the waitlist."""
    await _reservation_in_scope(reservation_id, session)
    await database.post(f"/reservations/{reservation_id}/cancel")
    promoted = await database.post("/waitlist/promote")
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
    """Open requests across all sponsors, for the committee to work through."""
    sponsors = await database.get("/sponsors", {"active_only": False}) or []
    out = []
    for sponsor in sponsors:
        for req in await database.get(f"/sponsors/{sponsor['id']}/requests") or []:
            out.append({**req, "sponsor_name": sponsor["name"]})
    return sorted(out, key=lambda r: r.get("created_at") or "", reverse=True)


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

    The account has to exist, which in practice means they have signed in once.
    Creating one from an address typed here would let a typo become a standing
    grant of committee access to an address nobody controls.
    """
    if role not in role_table.ROLE_NAMES:
        raise HTTPException(400, f"'{role}' is not a role")

    address = normalise_email(email)
    user = await database.get("/auth/users/by-email", {"email": address})
    if user is None:
        raise HTTPException(
            404, "No account with that email yet — they need to sign in once first."
        )
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
    return {"ok": True, "userId": user["id"], "role": role}


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
    """
    if session.is_impersonating:
        raise HTTPException(400, "Stop impersonating before starting again.")

    address = normalise_email(email)
    if _is_standing(address):
        raise HTTPException(400, "That is the standing superuser account.")

    user = await database.get("/auth/users/by-email", {"email": address})
    if user is None:
        raise HTTPException(
            404,
            "No account with that address. They have to have signed in, or been "
            "given sponsor access, at least once.",
        )

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
                "locker": f"{r['bank']}-{r['number']}",
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
            str(row["email"]), f"{row['bank']}-{row['number']}", deadline.strip()
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


@router.get("/locker-grants")
async def locker_grants(
    year: str | None = None, _: AuthSession = Depends(require_site)
) -> list[dict]:
    return await database.get(
        "/lockers/grants", {"academic_year": year or academic_year()}
    ) or []


@router.put("/locker-grants/{shortcode}", dependencies=[Depends(verify_csrf)])
async def set_locker_grant(
    shortcode: str,
    free_count: int = Body(default=1, embed=True),
    note: str | None = Body(default=None, embed=True),
    year: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_site),
) -> dict:
    """Give somebody free lockers, as the old system did for committee.

    Keyed on shortcode so a grant can be made before that person has ever signed
    in, and per academic year so it lapses at handover instead of following
    somebody who has left the committee.
    """
    cleaned = (shortcode or "").strip().lower()
    if not cleaned:
        raise HTTPException(400, "Give a shortcode.")
    return await database.put(
        f"/lockers/grants/{cleaned}",
        params={
            "academic_year": year or academic_year(),
            "free_count": max(1, free_count),
            "note": note,
            "granted_by": session.user.id,
        },
    )


@router.delete("/locker-grants/{shortcode}", dependencies=[Depends(verify_csrf)])
async def delete_locker_grant(
    shortcode: str,
    year: str | None = None,
    _: AuthSession = Depends(require_site),
) -> dict:
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
        )

    _dispatch(emailing.send_bulk(campaign["id"], recipients, build))
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
    """Run the on-sale and last-chance pass immediately.

    The loop runs every few minutes anyway; this is for the case where a sale
    date has just been corrected and nobody wants to watch the clock.
    """
    return await announce_service.run_once()


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
