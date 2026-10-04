"""Committee-only administration.

Everything here is behind `require_admin`. Admin-ness is a row in `admins`
keyed on user id — admins sign in through the same code flow as everybody else.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from clients import database, union
from models.auth import AuthSession
from config import settings
from services import storage
from services.identity import resolve_or_create_user
from utils.auth import normalise_email
from utils import email as mailer
from utils.present import format_price
from services import sync as sync_service
from utils.deps import require_admin, require_feature, verify_csrf
from utils.year import academic_year

# The maintenance gate is never set automatically for admin — only by hand.
# Deriving it from email would take the dashboard away at exactly the moment
# an admin needs it: the webmaster password lapses yearly, and fixing that is
# an admin job.
router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin), require_feature("admin")],
)


@router.get("/dashboard")
async def dashboard() -> dict:
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


@router.post("/sync", dependencies=[Depends(verify_csrf)])
async def trigger_sync(force: bool = True) -> dict:
    """'Sync now'. `force=True` bypasses the bridge's response cache — which is
    the point of pressing it, but it does spend a real upstream request, so it
    is admin-only."""
    return await sync_service.sync_all(force=force)


@router.post("/sync/committee", dependencies=[Depends(verify_csrf)])
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


@router.post("/reconcile", dependencies=[Depends(verify_csrf)])
async def reconcile() -> dict:
    """Expire stale holds and match reservations against already-synced sales,
    without calling upstream."""
    return await sync_service.reconcile_only()


# -------------------------------------------------------------------- events


@router.get("/events")
async def list_all_events() -> list[dict]:
    return await database.get("/events", {"published_only": False, "limit": 500}) or []


@router.post("/events", dependencies=[Depends(verify_csrf)])
async def create_event(
    body: dict = Body(...), session: AuthSession = Depends(require_admin)
) -> dict:
    body.setdefault("academic_year", academic_year())
    if not (body.get("slug") or "").strip():
        raise HTTPException(400, "An event needs a slug")
    return await database.post(
        "/events", body, params={"created_by": session.user.id}
    )


@router.put("/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def update_event(event_id: int, body: dict = Body(...)) -> dict:
    return await database.put(f"/events/{event_id}", body)


@router.delete("/events/{event_id}", dependencies=[Depends(verify_csrf)])
async def delete_event(event_id: int) -> dict:
    await database.delete(f"/events/{event_id}")
    return {"ok": True}


@router.post("/events/{event_id}/ticket-types", dependencies=[Depends(verify_csrf)])
async def add_ticket_type(event_id: int, body: dict = Body(...)) -> dict:
    return await database.post(f"/events/{event_id}/ticket-types", body)


@router.put("/ticket-types/{ticket_type_id}", dependencies=[Depends(verify_csrf)])
async def update_ticket_type(ticket_type_id: int, body: dict = Body(...)) -> dict:
    return await database.put(f"/ticket-types/{ticket_type_id}", body)


@router.delete("/ticket-types/{ticket_type_id}", dependencies=[Depends(verify_csrf)])
async def delete_ticket_type(ticket_type_id: int) -> dict:
    await database.delete(f"/ticket-types/{ticket_type_id}")
    return {"ok": True}


@router.get("/events/{event_id}/reservations")
async def event_reservations(event_id: int) -> dict:
    """The door list. Held and confirmed are shown separately because only
    confirmed rows have actually been paid for."""
    rows = await database.get("/reservations", {"event_id": event_id, "limit": 1000}) or []
    return {
        "confirmed": [r for r in rows if r["status"] == "confirmed"],
        "held": [r for r in rows if r["status"] == "held"],
        "closed": [r for r in rows if r["status"] in ("cancelled", "expired")],
    }


@router.get("/reservations/overdue")
async def overdue_reservations() -> dict:
    """Holds whose purchase window has passed with no matching sale.

    Nothing is cancelled automatically. A sale can be late, bought under a
    different account, or paid in cash, and silently voiding somebody's ticket
    is not recoverable — so the committee decides, per booking.
    """
    rows = await database.get("/reservations/overdue") or []
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
async def remind(reservation_id: int) -> dict:
    """Nudge somebody who has booked but not bought."""
    reservation = await database.get(f"/reservations/{reservation_id}")
    if reservation is None:
        raise HTTPException(404, "No such reservation")
    user = await database.get(f"/auth/users/{reservation['user_id']}")
    event = await database.get(f"/events/{reservation['event_id']}")
    if not user or not event:
        raise HTTPException(404, "No such reservation")

    sent = await mailer.send_purchase_reminder(
        str(user["email"]),
        event["title"],
        reservation["quantity"],
        f"{settings.site_origin}/events/{event['slug']}",
    )
    return {
        "ok": True,
        "sent": sent,
        "message": "Reminder sent." if sent else "No mail server configured — logged instead.",
    }


@router.post("/reservations/{reservation_id}/confirm", dependencies=[Depends(verify_csrf)])
async def confirm_manually(
    reservation_id: int, order_number: str | None = Body(default=None, embed=True)
) -> dict:
    """Confirm a booking the matcher could not."""
    row = await database.post(
        f"/reservations/{reservation_id}/confirm",
        params={"order_number": order_number},
    )
    return {"ok": True, "reservation": row}


@router.post("/reservations/{reservation_id}/cancel", dependencies=[Depends(verify_csrf)])
async def cancel_reservation(reservation_id: int) -> dict:
    """Cancel a booking and hand the seat to the waitlist."""
    await database.post(f"/reservations/{reservation_id}/cancel")
    promoted = await database.post("/waitlist/promote")
    return {"ok": True, "promoted": (promoted or {}).get("count", 0)}


@router.get("/sponsor-requests")
async def sponsor_requests() -> list[dict]:
    """Open requests across all sponsors, for the committee to work through."""
    sponsors = await database.get("/sponsors", {"active_only": False}) or []
    out = []
    for sponsor in sponsors:
        for req in await database.get(f"/sponsors/{sponsor['id']}/requests") or []:
            out.append({**req, "sponsor_name": sponsor["name"]})
    return sorted(out, key=lambda r: r.get("created_at") or "", reverse=True)


@router.get("/waitlist")
async def waitlist(event_id: int | None = None) -> list[dict]:
    return await database.get("/waitlist", {"event_id": event_id}) or []


# ------------------------------------------------------------- product linking


@router.get("/union/products")
async def union_products(year: str | None = None) -> list[dict]:
    """Feeds the 'link a product' dropdown. Served from the local mirror, never
    from upstream — this is a UI interaction and must not spend API quota."""
    return await database.get(
        "/union/products", {"academic_year": year or academic_year()}
    ) or []


@router.put("/events/{event_id}/product", dependencies=[Depends(verify_csrf)])
async def set_event_product(
    event_id: int,
    union_product_id: int | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    """Link the whole Union product to the whole event.

    One product per event, mirroring eActivities: a product with lines. Passing
    null unlinks. Replaces any existing event-level link rather than adding a
    second, which would make matching ambiguous.
    """
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
    session: AuthSession = Depends(require_admin),
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
    ticket = await database.get(f"/ticket-types/{ticket_type_id}")
    if ticket is None:
        raise HTTPException(404, "No such ticket type")

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
async def list_links(purpose: str | None = None, event_id: int | None = None) -> list[dict]:
    return await database.get(
        "/product-links", {"purpose": purpose, "event_id": event_id}
    ) or []


@router.post("/product-links", dependencies=[Depends(verify_csrf)])
async def create_link(
    body: dict = Body(...), session: AuthSession = Depends(require_admin)
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
async def delete_link(link_id: int) -> dict:
    await database.delete(f"/product-links/{link_id}")
    return {"ok": True}


# -------------------------------------------------------------------- admins


@router.get("/admins")
async def list_admins() -> list[dict]:
    return await database.get("/auth/admins") or []


@router.post("/admins", dependencies=[Depends(verify_csrf)])
async def add_admin(
    email: str = Body(embed=True), session: AuthSession = Depends(require_admin)
) -> dict:
    user = await database.get("/auth/users/by-email", {"email": email})
    if user is None:
        raise HTTPException(
            404, "No account with that email yet — they need to sign in once first."
        )
    await database.post(
        f"/auth/admins/{user['id']}", params={"granted_by": session.user.id}
    )
    return {"ok": True, "userId": user["id"]}


@router.delete("/admins/{user_id}", dependencies=[Depends(verify_csrf)])
async def remove_admin(user_id: int, session: AuthSession = Depends(require_admin)) -> dict:
    if user_id == session.user.id:
        raise HTTPException(400, "You cannot remove your own admin access.")
    admins = await database.get("/auth/admins") or []
    if len(admins) <= 1:
        raise HTTPException(400, "Someone has to keep the keys — add another admin first.")
    await database.delete(f"/auth/admins/{user_id}")
    return {"ok": True}


# ------------------------------------------------------------------ sponsors


@router.get("/sponsors")
async def list_sponsors() -> dict:
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
async def delete_sponsor(sponsor_id: int) -> dict:
    """Remove a sponsor entirely, with their listings, claims and access."""
    await database.delete(f"/sponsors/{sponsor_id}")
    return {"ok": True}


@router.post("/sponsors", dependencies=[Depends(verify_csrf)])
async def create_sponsor(body: dict = Body(...)) -> dict:
    return await database.post("/sponsors", body)


@router.put("/sponsors/{sponsor_id}", dependencies=[Depends(verify_csrf)])
async def update_sponsor(sponsor_id: int, body: dict = Body(...)) -> dict:
    return await database.put(f"/sponsors/{sponsor_id}", body)


@router.post("/sponsors/reorder", dependencies=[Depends(verify_csrf)])
async def reorder_sponsors(order: list[int] = Body(embed=True)) -> dict:
    """Set the running order of the sponsor list.

    Admin-only: sponsors can edit their own card but not push themselves up the
    page. Takes the full list of ids in the order wanted.
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
async def link_sponsor_user(sponsor_id: int, email: str = Body(embed=True)) -> dict:
    """Give an address access to a sponsor's portal.

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
async def unlink_sponsor_user(sponsor_id: int, user_id: int) -> dict:
    await database.delete(f"/sponsors/{sponsor_id}/users/{user_id}")
    return {"ok": True}


@router.get("/sponsors/{sponsor_id}/users")
async def sponsor_users(sponsor_id: int) -> list[dict]:
    return await database.get(f"/sponsors/{sponsor_id}/users") or []


# ------------------------------------------------------------------- lockers


@router.get("/lockers")
async def admin_lockers() -> list[dict]:
    return await database.get("/lockers") or []


@router.patch("/lockers/{locker_id}", dependencies=[Depends(verify_csrf)])
async def update_locker(
    locker_id: int,
    is_active: bool | None = Body(default=None, embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    return await database.patch(
        f"/lockers/{locker_id}", params={"is_active": is_active, "note": note}
    )


@router.get("/locker-holders")
async def locker_holders() -> dict:
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
async def notify_clearout(deadline: str = Body(embed=True)) -> dict:
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
async def release_locker_year(year: str = Body(embed=True)) -> dict:
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
async def locker_grants(year: str | None = None) -> list[dict]:
    return await database.get(
        "/lockers/grants", {"academic_year": year or academic_year()}
    ) or []


@router.put("/locker-grants/{shortcode}", dependencies=[Depends(verify_csrf)])
async def set_locker_grant(
    shortcode: str,
    free_count: int = Body(default=1, embed=True),
    note: str | None = Body(default=None, embed=True),
    year: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_admin),
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
async def delete_locker_grant(shortcode: str, year: str | None = None) -> dict:
    await database.delete(
        f"/lockers/grants/{shortcode}",
        {"academic_year": year or academic_year()},
    )
    return {"ok": True}


@router.get("/locker-bookings")
async def locker_bookings() -> list[dict]:
    return await database.get("/lockers/bookings", {"active_only": True}) or []
