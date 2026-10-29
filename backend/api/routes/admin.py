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
from typing import Literal

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
    require_action,
    assert_may_edit_event,
    assert_event_scope,
    assert_may_manage_booking,
    may_manage_booking,
    require_booking_editor,
    require_event_viewer,
    require_committee_page,
    require_email,
    require_files,
    require_lockers,
    require_product_links,
    require_resources,
    require_whatsapp,
    may_edit_event,
    require_admin,
    require_any_action,
    require_event_editor,
    require_feature,
    require_any_capability,
    require_locker_allowances,
    require_sponsors,
    require_users,
    verify_csrf,
)
from utils import roles as role_table
from utils.year import academic_year
from utils.shop_products import resolve_product_id

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
    awaiting = 0
    if session.can("email.queue.view"):
        awaiting = sum(
            1 for c in await database.get(
                "/email/campaigns", {"status": "awaiting_approval", "limit": 100}
            ) or []
            if c.get("status") == "awaiting_approval"
        )
    # Ordinary committee roles can use parts of this dashboard, but must not
    # learn the titles, authors or number of private opportunity submissions.
    pending_opportunities = []
    if session.can("opportunities.view"):
        pending_opportunities = [
            {"id": row["id"],
             "title": (row.get("pending_revision") or {}).get("title") or row["title"],
             "submittedBy": row.get("submitter_email"),
             "createdAt": row.get("pending_revision_at") or row.get("created_at"),
             "isRevision": bool(row.get("pending_revision"))}
            for row in (await database.get(
                "/opportunities", {"limit": 500}
            ) or []) if row.get("status") == "pending" or row.get("pending_revision")
        ]
    return {
        "academicYear": academic_year(),
        "counts": {
            **{key: value for key, value in counts.items() if not key.startswith(("res_", "users_", "union_sales", "waitlist"))},
            **({key: value for key, value in counts.items() if key.startswith(("res_", "waitlist"))} if session.can("bookings") else {}),
            **({key: value for key, value in counts.items() if key.startswith(("users_", "union_sales"))} if session.can("users") or session.can("sync") else {}),
        },
        "needsAttention": stats.get("needsAttention", []) if session.can("events.bookings.view") and session.can("events.scope.all") else [],
        "recentBookings": stats.get("recentBookings", []) if session.can("events.bookings.view") and session.can("events.scope.all") else [],
        "syncRuns": runs,
        "union": union_health,
        "storage": {
            **(storage.describe() if session.can("files") else {}),
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
        "warnings": _warnings(counts, union_health, awaiting) if session.can("sync") or session.can("email") else [],
        "emailsAwaitingApproval": awaiting,
        "opportunitiesAwaitingApproval": len(pending_opportunities),
        "pendingOpportunities": pending_opportunities,
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
async def overview(session: AuthSession = Depends(require_admin)) -> dict:
    runs = await database.get("/union/sync-runs/latest") or []
    year = academic_year()
    members = await database.get("/union/members/count", {"academic_year": year})
    readable = session.can("events.view") or session.can("events.bookings.view")
    full_calendar = readable and session.can("events.scope.all")
    industry_calendar = readable and session.can("events.scope.industry")
    events = await database.get("/events", {"published_only": not (full_calendar or industry_calendar), "limit": 500}) or []
    if not full_calendar:
        events = [event for event in events if event.get("published") or
                  (industry_calendar and event.get("industry"))]

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
    dependencies=[Depends(require_action("events.sync.union")), Depends(verify_csrf), Depends(require_any_capability("sync"))],
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
    dependencies=[Depends(require_action("committee.sync")), Depends(verify_csrf), Depends(require_committee_page)],
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
    dependencies=[Depends(require_action("events.sync.reconcile")), Depends(verify_csrf), Depends(require_any_capability("sync"))],
)
async def reconcile() -> dict:
    """Expire stale holds and match reservations against already-synced sales,
    without calling upstream."""
    return await sync_service.reconcile_only()


# -------------------------------------------------------------------- events


@router.get("/events", dependencies=[Depends(require_any_action("events.view", "events.bookings.view", "events.create"))])
async def list_all_events(
    session: AuthSession = Depends(require_event_viewer),
) -> list[dict]:
    """Every event, annotated with whether this caller may change it.

    The industry officers see the whole calendar rather than only their own
    slice: a careers talk is scheduled around the rest of the term, and hiding
    the rest would make that impossible. They just cannot edit it, and the row
    says so.
    """
    events = await database.get("/events", {"published_only": False, "limit": 500}) or []
    # Read-only published events can appear for planning; private drafts must
    # stay inside the caller's explicitly selected event scope.
    events = [e for e in events if e.get("published") or
              (session.can("events.create") and e.get("created_by") == session.user.id) or
              ((session.can("events.view") or session.can("events.bookings.view")) and (
                  session.can("events.scope.all") or
                  (session.can("events.scope.industry") and e.get("industry"))))]
    # Which sponsors each event is linked to, for the badge in the list.
    sponsors: dict[int, list[str]] = {}
    for link in await database.get("/sponsors/links/confirmed") or []:
        sponsors.setdefault(link["event_id"], []).append(link["sponsor_name"])
    return [
        {**e, "can_edit": (session.can("events.edit") or
                      (session.can("events.create") and e.get("created_by") == session.user.id))
                      and may_edit_event(session, e),
         "can_delete": session.can("events.delete") and may_edit_event(session, e),
         "can_badge": session.can("events.availability_badge.edit") and may_edit_event(session, e),
         "can_waitlist": session.can("events.waitlist.manage") and may_edit_event(session, e),
         "can_publish": session.can("events.publish") and may_edit_event(session, e),
         "can_book": session.can("events.bookings.view") and may_manage_booking(session, e), "sponsors": sponsors.get(e["id"], [])}
        for e in events
    ]


async def _editable_event(event_id: int, session: AuthSession) -> dict:
    """Load an event and refuse if this role does not cover it."""
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_may_edit_event(session, event)
    return event


async def _bookable_event(event_id: int, session: AuthSession) -> dict:
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_may_manage_booking(session, event)
    return event


async def _editable_ticket(ticket_type_id: int, session: AuthSession) -> dict:
    ticket = await database.get(f"/ticket-types/{ticket_type_id}")
    if ticket is None:
        raise HTTPException(404, "No such ticket type")
    await _editable_event(ticket["event_id"], session)
    return ticket


def _assert_ticket_type_changes(session: AuthSession, previous: dict, body: dict) -> None:
    """The event form embeds ticket CRUD; prevent bypassing its independent grants.

    Comparison only examines fields the client actually sends. Do not mistake
    computed ticket availability, client local display fields, or ISO 8601
    date-format differences for edits to actual ticket values.
    """
    if "ticket_types" not in body:
        # Partial event updates may modify only dates, publication or other
        # event fields. Omission never changes ticket types and must not
        # require an unrelated ticket permission.
        return
    previous_by_id = {t["id"]: t for t in previous.get("ticket_types", [])}
    submitted_ids = set()
    fields = ("name", "description", "price_pence", "capacity", "requires_purchase",
              "members_only", "max_per_user", "sort_order", "purchase_url",
              "sales_open_at", "sales_close_at")
    for ticket in body["ticket_types"]:
        ticket_id = ticket.get("id")
        if ticket_id is None:
            if not session.can("events.ticket_types.create"):
                raise HTTPException(403, "Permission required: events.ticket_types.create")
            continue
        if ticket_id not in previous_by_id or ticket_id in submitted_ids:
            raise HTTPException(400, "Invalid or duplicate ticket type ID")
        submitted_ids.add(ticket_id)
        prior = previous_by_id[ticket_id]
        def canonical(field, value):
            if field in ("sales_open_at", "sales_close_at") and value:
                try:
                    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
                except ValueError:
                    pass
            return value
        if any(canonical(field, ticket[field]) != canonical(field, prior.get(field))
               for field in fields if field in ticket):
            if not session.can("events.ticket_types.edit"):
                raise HTTPException(403, "Permission required: events.ticket_types.edit")
    if set(previous_by_id) - submitted_ids and not session.can("events.ticket_types.delete"):
        raise HTTPException(403, "Permission required: events.ticket_types.delete")


async def _check_society_ticket_eligibility(session:AuthSession, before:list[dict], after:list[dict]) -> None:
    old={t.get('id'):[int(x) for x in t.get('eligible_society_ids',[])] for t in before}
    changed=any(sorted(t.get('eligible_society_ids') or [])!=sorted(old.get(t.get('id'),[])) for t in after)
    if changed and not session.can('events.ticket_types.eligibility'):
        raise HTTPException(403,'Permission required: events.ticket_types.eligibility')
    if changed:
        from clients import database
        known={s['id'] for s in await database.get('/external-societies') or []}
        if any(not isinstance(x,int) or x not in known for t in after for x in (t.get('eligible_society_ids') or [])):
            raise HTTPException(400,'Unknown society selected for ticket eligibility')


@router.post("/events", dependencies=[Depends(require_action("events.create")), Depends(verify_csrf)])
async def create_event(
    body: dict = Body(...), session: AuthSession = Depends(require_event_editor)
) -> dict:
    body.setdefault("academic_year", academic_year())
    if body.get("ticket_types") and not session.can("events.ticket_types.create"):
        raise HTTPException(403, "Permission required: events.ticket_types.create")
    await _check_society_ticket_eligibility(session,[],body.get('ticket_types',[]))
    if body.get("published") and not session.can("events.publish"):
        raise HTTPException(403, "Permission required: events.publish")
    if not (body.get("slug") or "").strip():
        raise HTTPException(400, "An event needs a slug")
    # Somebody who may only edit industry events can only create one, or they
    # would make an event and immediately lose the ability to fix it.
    if session.can("events.scope.industry") and not session.can("events.scope.all"):
        body["industry"] = True
    created = await database.post(
        "/events", body, params={"created_by": session.user.id}
    )
    await _sync_on_sale_email(created["id"])
    return created


@router.put("/events/{event_id}", dependencies=[Depends(require_any_action("events.edit", "events.create")), Depends(verify_csrf)])
async def update_event(
    event_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    existing = await _editable_event(event_id, session)
    owns_event = (session.can("events.create") and
                  existing.get("created_by") == session.user.id)
    if not session.can("events.edit") and not owns_event:
        raise HTTPException(403, "You can edit only events you created without the events.edit permission")
    # Legacy event editors may omit this new field. Retain the stored list rather
    # than silently wiping ticket eligibility or blocking unrelated edits.
    older={t['id']:t.get('eligible_society_ids') or [] for t in existing.get('ticket_types',[])}
    for t in body.get('ticket_types',[]):
        if 'eligible_society_ids' not in t and t.get('id') in older:
            t['eligible_society_ids']=older[t['id']]
    _assert_ticket_type_changes(session, existing, body)
    if 'ticket_types' in body:
        await _check_society_ticket_eligibility(session,existing.get('ticket_types',[]),body['ticket_types'])
    if "published" in body and bool(body["published"]) != bool(existing.get("published")) and not session.can("events.publish"):
        raise HTTPException(403, "Permission required: events.publish")
    if (session.can("events.scope.industry") and not session.can("events.scope.all")
            and not owns_event):
        # Clearing the flag would hand the event to the events officers and
        # lock its own author out of it, which is never what the save button
        # was meant to do.
        body["industry"] = True
    else:
        body.setdefault("industry", existing.get("industry", False))
    updated = await database.put(f"/events/{event_id}", body)
    await _sync_on_sale_email(event_id)
    return updated


@router.patch(
    "/events/{event_id}/photo-short-name",
    dependencies=[Depends(require_action("events.photo_short_name.edit")), Depends(verify_csrf)],
)
async def edit_photo_short_name(
    event_id: int,
    photo_short_name: str | None = Body(default=None, embed=True, max_length=64),
    session: AuthSession = Depends(require_admin),
) -> dict:
    """One independently protected event field; no general event-edit grant needed."""
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_event_scope(session, event)
    result = await database.patch(
        f"/events/{event_id}/photo-short-name",
        {"photo_short_name": photo_short_name.strip() or None if photo_short_name else None},
    )
    return result


@router.patch(
    "/events/{event_id}/waitlist-enabled",
    dependencies=[Depends(require_action("events.waitlist.manage")), Depends(verify_csrf)],
)
async def set_event_waitlist_enabled(
    event_id: int,
    waitlist_enabled: bool = Body(embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    """Independently revocable per-event waitlist enrolment, scope-protected."""
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_event_scope(session, event)
    return await database.patch(
        f"/events/{event_id}/waitlist-enabled",
        {"waitlist_enabled": waitlist_enabled},
    )


@router.patch(
    "/events/{event_id}/availability-badge",
    dependencies=[Depends(require_action("events.availability_badge.edit")), Depends(verify_csrf)],
)
async def set_event_availability_badge(
    event_id: int,
    availability_badge: Literal["auto", "not_many_left", "selling_fast"] = Body(embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    """Independent badge permission and event scope, without events.edit."""
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")
    assert_event_scope(session, event)
    return await database.patch(
        f"/events/{event_id}/availability-badge",
        {"availability_badge": availability_badge},
    )


@router.patch("/events/{event_id}/published", dependencies=[Depends(require_action("events.publish")), Depends(verify_csrf)])
async def set_event_publication(
    event_id: int, published: bool = Body(embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    existing = await _editable_event(event_id, session)
    payload = {**existing, "published": published}
    if "ticket_types" not in payload:
        raise HTTPException(409, "Ticket types unavailable — publication refused")
    updated = await database.put(f"/events/{event_id}", payload)
    await _sync_on_sale_email(event_id)
    return updated


@router.delete("/events/{event_id}", dependencies=[Depends(require_action("events.delete")), Depends(verify_csrf)])
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


@router.post("/events/{event_id}/ticket-types", dependencies=[Depends(require_action("events.ticket_types.create")), Depends(verify_csrf)])
async def add_ticket_type(
    event_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    await _editable_event(event_id, session)
    await _check_society_ticket_eligibility(session,[],[body])
    return await database.post(f"/events/{event_id}/ticket-types", body)


@router.put("/ticket-types/{ticket_type_id}", dependencies=[Depends(require_action("events.ticket_types.edit")), Depends(verify_csrf)])
async def update_ticket_type(
    ticket_type_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    ticket=await _editable_ticket(ticket_type_id, session)
    if 'eligible_society_ids' not in body:
        body['eligible_society_ids']=ticket.get('eligible_society_ids') or []
    await _check_society_ticket_eligibility(session,[ticket],[body])
    return await database.put(f"/ticket-types/{ticket_type_id}", body)


@router.delete("/ticket-types/{ticket_type_id}", dependencies=[Depends(require_action("events.ticket_types.delete")), Depends(verify_csrf)])
async def delete_ticket_type(
    ticket_type_id: int, session: AuthSession = Depends(require_event_editor)
) -> dict:
    await _editable_ticket(ticket_type_id, session)
    await database.delete(f"/ticket-types/{ticket_type_id}")
    return {"ok": True}


@router.get("/events/{event_id}/reservations", dependencies=[Depends(require_action("events.bookings.view"))])
async def event_reservations(
    event_id: int, session: AuthSession = Depends(require_booking_editor)
) -> dict:
    """The door list. Held and confirmed are shown separately because only
    confirmed rows have actually been paid for."""
    await _bookable_event(event_id, session)
    rows = await database.get("/reservations", {"event_id": event_id, "limit": 1000}) or []
    return {
        "confirmed": [r for r in rows if r["status"] == "confirmed"],
        "held": [r for r in rows if r["status"] == "held"],
        "closed": [r for r in rows if r["status"] in ("cancelled", "expired")],
    }


@router.get("/events/{event_id}/detail", dependencies=[Depends(require_action("events.bookings.view"))])
async def event_detail(
    event_id: int, session: AuthSession = Depends(require_booking_editor)
) -> dict:
    """Everything about one event's bookings, for its admin page: a summary per
    ticket type, every reservation in every state, and the waitlist."""
    event = await _bookable_event(event_id, session)
    rows = await database.get("/reservations", {"event_id": event_id, "limit": 5000}) or []
    verifications = await database.get('/order-verification/reservations', {'event_id': event_id}) or []
    verification_by_id = {v['reservation_id']: v for v in verifications}
    waiting = await database.get("/waitlist", {"event_id": event_id}) or [] if session.can("events.bookings.waitlist") else []

    summary = []
    for t in event.get("ticket_types", []):
        mine = [r for r in rows if r["ticket_type_id"] == t["id"]]
        qty = lambda *states: sum(r["quantity"] for r in mine if r["status"] in states)
        summary.append({
            "ticketTypeId": t["id"],
            "name": t["name"],
            "price": format_price(t.get("price_pence", 0)),
            "capacity": t.get("capacity"),
            "requiresPurchase": t.get("requires_purchase", True),
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
            "orderVerification": verification_by_id.get(r['id']),
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


@router.get("/events/{event_id}/audiences", dependencies=[Depends(require_action("email.audiences"))])
async def event_email_audiences(
    event_id: int, _: AuthSession = Depends(require_email)
) -> dict:
    """The per-event audiences for the "Email about this" dialog."""
    return {"audiences": await emailing.event_audiences(event_id)}


@router.get("/reservations/overdue", dependencies=[Depends(require_action("events.bookings.view"))])
async def overdue_reservations(
    session: AuthSession = Depends(require_booking_editor),
) -> dict:
    """Overdue holds and orders rejected after a successful sales refresh.

    Nothing is cancelled automatically. A failed Union order claim is queued
    for a human cancellation decision, including manually confirmed bookings.
    """
    rows = await database.get("/reservations/overdue") or []
    # An industry-only role sees the unpaid bookings for its own events and
    # nothing else — these rows carry people's names and what they bought.
    if not session.can("bookings"):
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
                "orderNumber": r.get("order_number") or r.get("verification_order_number"),
                "status": r.get("status"),
                "orderVerificationStatus": r.get("order_verification_status"),
                "reviewCancellation": r.get("order_verification_status") == 'rejected',
                "guest": r.get("booked_as_guest", False),
                "heldSince": r.get("created_at"),
                "dueSince": r.get("review_due_at") or r.get("expires_at"),
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


@router.post("/reservations/{reservation_id}/remind", dependencies=[Depends(require_action("events.bookings.remind")), Depends(verify_csrf)])
async def remind(
    reservation_id: int, session: AuthSession = Depends(require_booking_editor)
) -> dict:
    """Nudge somebody who has booked but not bought."""
    reservation = await database.get(f"/reservations/{reservation_id}")
    if reservation is None:
        raise HTTPException(404, "No such reservation")
    user = await database.get(f"/auth/users/{reservation['user_id']}")
    event = await _bookable_event(reservation["event_id"], session)
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


@router.post("/reservations/{reservation_id}/confirm", dependencies=[
    Depends(require_any_action("events.bookings.confirm", "events.bookings.confirm_override")),
    Depends(verify_csrf),
])
async def confirm_manually(
    reservation_id: int,
    order_number: str | None = Body(default=None, embed=True),
    override: bool = Body(default=False, embed=True),
    session: AuthSession = Depends(require_booking_editor),
) -> dict:
    """Submit an order number to the matcher, or explicitly override it.

    The ordinary path never confirms an unverified sale. It stays held until
    matched locally or via the 06:05 UTC sales refresh the next calendar day.
    """
    scoped_reservation = await _reservation_in_scope(reservation_id, session)
    if override:
        if not session.can('events.bookings.confirm_override'):
            raise HTTPException(403, 'Permission required: events.bookings.confirm_override')
        row = await database.post(f'/reservations/{reservation_id}/confirm', params={
            'order_number': (order_number or '').strip() or None,
            'actor_user_id': session.user.id,
        })
        confirmations.trigger()
        return {'ok': True, 'reservation': row,
                'message': 'Booking confirmed using an authorised manual override.'}
    if not session.can('events.bookings.confirm'):
        raise HTTPException(403, 'Permission required: events.bookings.confirm')
    value = (order_number or '').strip()
    # Only a no-order attempt may confirm a free held ticket. Avoid another
    # adapter call for ordinary paid claims (including legacy regression tests).
    if not value and scoped_reservation.get('ticket_type_id'):
        ticket_type = await database.get(
            f'/ticket-types/{scoped_reservation["ticket_type_id"]}')
        if ticket_type and not ticket_type.get('requires_purchase', True):
            confirmed = await database.post(f'/reservations/{reservation_id}/confirm-free',
                                            params={'actor_user_id': session.user.id})
            confirmations.trigger()
            return {'ok': True, 'reservation': confirmed,
                    'message': 'Free ticket confirmed by an authorised committee member.'}
    if not value or len(value) > 128:
        raise HTTPException(400, 'An order number is required (up to 128 characters)')
    await database.post(f'/reservations/{reservation_id}/submit-order', {
        'order_number': value, 'actor_user_id': session.user.id,
    })
    await sync_service.reconcile_only()
    current = await database.get(f'/reservations/{reservation_id}')
    if current and current['status'] == 'confirmed':
        return {'ok': True, 'reservation': current,
                'message': 'The Union order was matched and the ticket is confirmed.'}
    return {'ok': True, 'reservation': current,
            'message': 'Order submitted for verification. It will be checked again after tomorrow’s 06:05 UTC sales sync; if not found, the order number will be rejected but the reservation stays held.'}


@router.patch("/reservations/{reservation_id}/order-number", dependencies=[Depends(require_action("events.bookings.order_number.edit")), Depends(verify_csrf)])
async def committee_order_number(
    reservation_id: int,
    order_number: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_booking_editor),
) -> dict:
    previous = await _reservation_in_scope(reservation_id, session)
    value = (order_number or "").strip() or None
    if value is not None and len(value) > 128:
        raise HTTPException(400, "Order number too long")
    row = await database.patch(f"/reservations/{reservation_id}/committee-order-number", {"order_number": value, "actor_user_id": session.user.id})
    if row and value and row.get('status') == 'held':
        await sync_service.reconcile_only()
        row = await database.get(f'/reservations/{reservation_id}') or row
    elif row and value and row.get('status') == 'confirmed':
        # Existing matched sales can validate an edited claim immediately;
        # an unmatched number waits for a successful next-day sales refresh.
        await database.post('/reservations/order-verification/settle', {'allow_reject': False})
    matched_now = bool(previous and previous.get('status') == 'held'
                       and row and row.get('status') == 'confirmed')
    message = ('Order number removed.' if not value else
               'Union sale matched; booking confirmed.' if matched_now else
               'Order saved for verification. If unmatched after the next successful '
               '06:05 UTC sales sync, it will appear in Review cancellations.')
    return {'ok': True, 'reservation': row, 'message': message}


@router.post("/reservations/{reservation_id}/ticket-type", dependencies=[Depends(require_action("events.bookings.change_ticket")), Depends(verify_csrf)])
async def change_ticket_type(
    reservation_id: int,
    ticket_type_id: int = Body(embed=True),
    session: AuthSession = Depends(require_booking_editor),
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


@router.post("/events/{event_id}/tickets", dependencies=[Depends(require_action("events.bookings.add")), Depends(verify_csrf)])
async def add_ticket(
    event_id: int,
    ticket_type_id: int = Body(embed=True),
    email: str = Body(embed=True),
    first_name: str | None = Body(default=None, embed=True),
    last_name: str | None = Body(default=None, embed=True),
    quantity: int = Body(default=1, embed=True),
    note: str | None = Body(default=None, embed=True),
    order_number: str | None = Body(default=None, embed=True),
    override_confirmation: bool = Body(default=False, embed=True),
    confirm_immediately: bool = Body(default=False, embed=True),
    send_confirmation: bool = Body(default=True, embed=True),
    override_capacity: bool = Body(default=False, embed=True),
    session: AuthSession = Depends(require_booking_editor),
) -> dict:
    """Create a committee booking with the same sale validation as a normal hold.

    Paid tickets are *held*, not confirmed, while their Union order is checked
    against local sales and the next successful daily refresh. Only an actor
    with confirm_override can bypass proof of payment. Free tickets confirm
    immediately; capacity bypass is a separate permission.
    """
    event = await _bookable_event(event_id, session)
    # Calling this coroutine directly in service tests supplies FastAPI's Body
    # descriptor as the default; only a literal True opts into confirmation.
    confirm_immediately = confirm_immediately is True
    # Deny privileged overrides before any user creation or ticket lookup.
    if override_capacity and not session.can('events.bookings.capacity_override'):
        raise HTTPException(403, 'Permission required: events.bookings.capacity_override')
    if override_confirmation and not session.can('events.bookings.confirm_override'):
        raise HTTPException(403, 'Permission required: events.bookings.confirm_override')
    if confirm_immediately and not (session.can('events.bookings.confirm') or
                                   session.can('events.bookings.confirm_override')):
        raise HTTPException(403, 'Permission required: events.bookings.confirm')
    selected = next((t for t in event.get('ticket_types', []) if t['id'] == ticket_type_id), None)
    if selected is None:
        raise HTTPException(404, 'No such ticket type for this event')
    order = (order_number or '').strip() or None
    if order is not None and len(order) > 128:
        raise HTTPException(400, 'Order number must be at most 128 characters')
    needs_proof = selected.get('requires_purchase', True)
    if needs_proof and not order and not override_confirmation:
        raise HTTPException(400, 'A Union order number is required unless you have payment-verification override permission')
    address = normalise_email(email)
    if '@' not in address:
        raise HTTPException(400, 'That does not look like an email address.')
    if not 1 <= quantity <= 20:
        raise HTTPException(400, 'Between 1 and 20 tickets at a time.')

    holder = await resolve_or_create_user(
        address,
        first_name=(first_name or '').strip() or None,
        last_name=(last_name or '').strip() or None,
        verified=False,
    )
    who = session.user.email
    text = (note or '').strip()
    row = await database.post('/reservations/committee', {
        'event_id': event_id, 'ticket_type_id': ticket_type_id,
        'user_id': holder['id'], 'quantity': quantity,
        'note': f'added by {who}' + (f': {text}' if text else ''),
        'added_by': session.user.id, 'send_confirmation': send_confirmation,
        'override_capacity': override_capacity,
        'override_confirmation': override_confirmation,
        'confirm_immediately': confirm_immediately or override_confirmation,
        'order_number': order,
        'booked_as_guest': holder.get('member_id') is None,
    })
    if row is None:
        raise HTTPException(404, 'No such ticket type for this event')
    # Use the exact same local matcher that checks submitted order numbers on
    # an ordinary reservation. It will never confirm a merely claimed order.
    was_held = row['status'] == 'held'
    if was_held and confirm_immediately:
        await sync_service.reconcile_only()
        current = await database.get(f"/reservations/{row['id']}")
        if current:
            row = current
    if not was_held and row['status'] == 'confirmed' and send_confirmation:
        confirmations.trigger()
    log.warning('Committee ticket %s added for user %s by %s (%s)',
                row['id'], holder['id'], who, row['status'])
    return {
        'ok': True, 'reservation': row,
        'message': (
            f'Ticket confirmed for {holder["email"]}.'
            + (' A confirmation email is on its way.' if send_confirmation else '')
            if row['status'] == 'confirmed'
            else ('Booking added as unconfirmed. A Union order will be checked '
                  'during the next daily sales sync; an unmatched order enters '
                  'Review cancellations.' if needs_proof else
                  'Unconfirmed free ticket added. An authorised officer can confirm it.')
        ),
    }


@router.post("/reservations/{reservation_id}/confirmation", dependencies=[Depends(require_action("events.bookings.resend")), Depends(verify_csrf)])
async def resend_confirmation(
    reservation_id: int, session: AuthSession = Depends(require_booking_editor)
) -> dict:
    """Send a confirmed booking's "you're confirmed" email again."""
    await _reservation_in_scope(reservation_id, session)
    await database.post(f"/reservations/{reservation_id}/confirmation/resend")
    confirmations.trigger()
    return {"ok": True, "message": "Confirmation email queued."}


@router.post("/reservations/{reservation_id}/cancel", dependencies=[Depends(require_action("events.bookings.cancel")), Depends(verify_csrf)])
async def cancel_reservation(
    reservation_id: int, session: AuthSession = Depends(require_booking_editor)
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
    await _bookable_event(reservation["event_id"], session)
    return reservation


@router.get("/sponsor-requests", dependencies=[Depends(require_action("sponsors.requests.view"))])
async def sponsor_requests(
    _: AuthSession = Depends(require_sponsors),
) -> list[dict]:
    """Requests across all sponsors, newest first, for the committee to work
    through. One query: this used to fetch every sponsor's list in turn."""
    return await database.get("/sponsors/requests/all") or []


@router.get("/waitlist", dependencies=[Depends(require_action("events.bookings.waitlist"))])
async def waitlist(
    event_id: int | None = None,
    session: AuthSession = Depends(require_booking_editor),
) -> list[dict]:
    if event_id is not None:
        await _bookable_event(event_id, session)
    elif not session.can("bookings"):
        raise HTTPException(403, "Ask for one event's waitlist at a time.")
    return await database.get("/waitlist", {"event_id": event_id}) or []


# ------------------------------------------------------------- product linking


@router.get("/union/products", dependencies=[Depends(require_action("products.view"))])
async def union_products(
    year: str | None = None, _: AuthSession = Depends(require_any_capability("product_links", "events", "industry"))
) -> list[dict]:
    """Feeds the 'link a product' dropdown. Served from the local mirror, never
    from upstream — this is a UI interaction and must not spend API quota."""
    return await database.get(
        "/union/products", {"academic_year": year or academic_year()}
    ) or []


@router.put("/events/{event_id}/product", dependencies=[Depends(require_action("events.ticket_types.product")), Depends(verify_csrf)])
async def set_event_product(
    event_id: int,
    union_product_id: int | None = Body(default=None, embed=True),
    shop_product_ref: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_event_editor),
) -> dict:
    """Link the whole Union product to the whole event.

    One product per event, mirroring eActivities: a product with lines. Passing
    null unlinks. Replaces any existing event-level link rather than adding a
    second, which would make matching ambiguous.
    """
    await _editable_event(event_id, session)
    # Validate BEFORE removing a previous link; malformed shop ids must not unlink.
    union_product_id = resolve_product_id(union_product_id, shop_product_ref)
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


@router.put("/ticket-types/{ticket_type_id}/product", dependencies=[Depends(require_action("events.ticket_types.product")), Depends(verify_csrf)])
async def set_ticket_product(
    ticket_type_id: int,
    union_product_id: int | None = Body(default=None, embed=True),
    union_product_line_id: int | None = Body(default=None, embed=True),
    shop_product_ref: str | None = Body(default=None, embed=True),
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
    union_product_id = resolve_product_id(union_product_id, shop_product_ref)
    if shop_product_ref is not None and union_product_line_id is not None:
        raise HTTPException(400, "Manual shop links use any line of the product.")

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


@router.get("/product-links", dependencies=[Depends(require_action("products.view"))])
async def list_links(
    purpose: str | None = None,
    event_id: int | None = None,
    _: AuthSession = Depends(require_any_capability("product_links", "events", "industry")),
) -> list[dict]:
    return await database.get(
        "/product-links", {"purpose": purpose, "event_id": event_id}
    ) or []


@router.post("/product-links", dependencies=[Depends(require_action("products.link")), Depends(verify_csrf)])
async def create_link(
    body: dict = Body(...), session: AuthSession = Depends(require_product_links)
) -> dict:
    """Map a Union product (or a specific line of it) to a ticket type, a locker,
    or membership.

    Leaving `union_product_line_id` null means "any line of this product", which
    is the safe default: line ids are not documented as unique across products,
    so pinning one is only worth doing when a product genuinely has several
    lines you need to tell apart.
    """
    # Manual references are never passed to the database adapter as extra fields.
    shop_product_ref = body.pop("shop_product_ref", None)
    if shop_product_ref is not None:
        if body.get("union_product_line_id") is not None:
            raise HTTPException(400, "Manual shop links use any line of the product.")
        body["union_product_id"] = resolve_product_id(body.get("union_product_id"), shop_product_ref)
    body.setdefault("academic_year", academic_year())
    body["created_by"] = session.user.id
    if not body.get("purpose"):
        raise HTTPException(400, "purpose must be event_ticket, locker or membership")
    return await database.post("/product-links", body)


@router.delete("/product-links/{link_id}", dependencies=[Depends(require_action("products.unlink")), Depends(verify_csrf)])
async def delete_link(link_id: int, _: AuthSession = Depends(require_product_links)) -> dict:
    await database.delete(f"/product-links/{link_id}")
    return {"ok": True}


# ------------------------------------------------------- people and roles
#
# Access-manager permission only: an admin runs the site, while changes to
# committee grants require an explicit, separately assigned permission. The standing superuser address in settings cannot be demoted here, and
# would be re-granted on its next request even if the row were deleted by hand.


def _is_standing(email: str | None) -> bool:
    configured = (settings.superuser_email or "").strip().lower()
    return bool(configured) and str(email or "").strip().lower() == configured


@router.get('/audit', dependencies=[Depends(require_action('access.audit.view'))])
async def audit_actions(limit: int = 100, include_requests: bool = False,
                        session: AuthSession = Depends(require_admin)) -> dict:
    rows = await database.get('/audit/actions', {
        'limit': min(max(limit, 1), 200), 'include_requests': include_requests,
    }) or []
    return {'actions': rows}


@router.get("/admins", dependencies=[Depends(require_action("access.view"))])
async def list_admins(session: AuthSession = Depends(require_admin)) -> dict:
    """Who holds what.

    Readable by any committee role — knowing who to ask is not privileged, and
    the alternative is people guessing. Only a superuser can change it, and the
    page only offers the controls when `canManage` says so.
    """
    people = await database.get("/auth/admins") or []
    role_presets = await database.get('/committee-roles') or []
    preset_by_id = {r['id']: r for r in role_presets}
    rows = [
        {**row, "standing": _is_standing(row.get("email")),
         "role": "superuser" if _is_standing(row.get("email")) else row["role"],
         "permissions": None if _is_standing(row.get("email")) else row.get("permissions")}
        for row in people
    ]

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
        "admins": [{**row, "capabilities": sorted(
            role_table.PERMISSIONS if row['standing'] else
            role_table.effective_capabilities(
                row.get('role'), row.get('permissions') if row.get('permissions') is not None
                else preset_by_id.get(row.get('role'), {}).get('capabilities', []))
        )} for row in rows],
        "roles": role_presets,
        "permissions": role_table.describe_permissions(),
        "canManage": session.can("access.grant"),
        "superuserEmail": settings.superuser_email,
    }


@router.get('/roles', dependencies=[Depends(require_any_action('access.roles.create', 'access.roles.edit'))])
async def list_role_defaults(_: AuthSession = Depends(require_admin)) -> dict:
    return {'roles': await database.get('/committee-roles') or [],
            'permissions': role_table.describe_permissions()}


@router.post('/roles', dependencies=[Depends(require_action('access.roles.create')), Depends(verify_csrf)])
async def create_role_default(
    id: str = Body(embed=True), label: str = Body(embed=True),
    blurb: str = Body(default='', embed=True),
    capabilities: list[str] = Body(default=[], embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}', id) or id in role_table.ROLES:
        raise HTTPException(400, 'Role ID must be 2-40 lowercase characters and cannot be reserved')
    if not label.strip() or len(label.strip()) > 80:
        raise HTTPException(400, 'Role display name is required and must be at most 80 characters')
    try:
        checked = role_table.validate_permissions(capabilities)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await database.post('/committee-roles', {
        'id': id, 'label': label.strip(), 'blurb': blurb[:2000],
        'capabilities': checked, 'actor_id': session.user.id,
    })


@router.put('/roles/{role_id}', dependencies=[Depends(require_action('access.roles.edit')), Depends(verify_csrf)])
async def update_role_default(
    role_id: str, label: str = Body(embed=True),
    blurb: str = Body(default='', embed=True),
    capabilities: list[str] = Body(default=[], embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    if role_id == 'superuser':
        raise HTTPException(403, 'The superuser recovery role cannot be edited')
    if not label.strip() or len(label.strip()) > 80:
        raise HTTPException(400, 'Role display name is required and must be at most 80 characters')
    try:
        checked = role_table.validate_permissions(capabilities)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if session.role == role_id and session.permissions is None and 'access.roles.edit' not in checked:
        raise HTTPException(400, 'You cannot remove your own ability to edit role defaults')
    return await database.put(f'/committee-roles/{role_id}', {
        'label': label.strip(), 'blurb': blurb[:2000],
        'capabilities': checked, 'actor_id': session.user.id,
    })


@router.post("/admins", dependencies=[Depends(require_action("access.grant")), Depends(verify_csrf)])
async def add_admin(
    email: str = Body(embed=True),
    role: str = Body(default="admin", embed=True),
    permissions: list[str] | None = Body(default=None, embed=True),
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
    try:
        permissions = role_table.validate_permissions(permissions)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    role_presets = await database.get('/committee-roles') or []
    preset_by_id = {r['id']: r for r in role_presets}
    if role not in preset_by_id:
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

    if _is_standing(user.get("email")) and permissions is not None:
        raise HTTPException(400, "Standing superuser permissions cannot be overridden.")
    selected_grants = (permissions if permissions is not None
                       else preset_by_id[role]['capabilities'])
    if user["id"] == session.user.id and "access.grant" not in role_table.effective_capabilities(role, selected_grants):
        raise HTTPException(400, "You cannot remove your own ability to manage access.")

    await database.post(
        f"/auth/admins/{user['id']}",
        json={"permissions": permissions},
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


@router.delete("/admins/{user_id}", dependencies=[Depends(require_action("access.revoke")), Depends(verify_csrf)])
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


@router.post("/impersonate", dependencies=[Depends(require_action("access.impersonate")), Depends(verify_csrf)])
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


@router.get("/sponsors", dependencies=[Depends(require_action("sponsors.view"))])
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


@router.put("/sponsor-package/{clause}", dependencies=[Depends(require_action("sponsors.package")), Depends(verify_csrf)])
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


@router.delete("/sponsors/{sponsor_id}", dependencies=[Depends(require_action("sponsors.delete")), Depends(verify_csrf)])
async def delete_sponsor(
    sponsor_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    """Remove a sponsor entirely, with their listings, claims and access."""
    await database.delete(f"/sponsors/{sponsor_id}")
    return {"ok": True}


@router.post("/sponsors", dependencies=[Depends(require_action("sponsors.create")), Depends(verify_csrf)])
async def create_sponsor(
    body: dict = Body(...), _: AuthSession = Depends(require_sponsors)
) -> dict:
    return await database.post("/sponsors", body)


@router.put("/sponsors/{sponsor_id}", dependencies=[Depends(require_action("sponsors.edit")), Depends(verify_csrf)])
async def update_sponsor(
    sponsor_id: int,
    body: dict = Body(...),
    _: AuthSession = Depends(require_sponsors),
) -> dict:
    return await database.put(f"/sponsors/{sponsor_id}", body)


@router.post("/sponsors/reorder", dependencies=[Depends(require_action("sponsors.reorder")), Depends(verify_csrf)])
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


@router.post("/sponsors/{sponsor_id}/users", dependencies=[Depends(require_action("sponsors.users.grant")), Depends(verify_csrf)])
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


@router.delete("/sponsors/{sponsor_id}/users/{user_id}", dependencies=[Depends(require_action("sponsors.users.revoke")), Depends(verify_csrf)])
async def unlink_sponsor_user(
    sponsor_id: int, user_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    await database.delete(f"/sponsors/{sponsor_id}/users/{user_id}")
    return {"ok": True}


@router.get("/sponsors/{sponsor_id}/users", dependencies=[Depends(require_action("sponsors.users.view"))])
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


@router.get("/sponsors/inbox", dependencies=[Depends(require_action("sponsors.inbox"))])
async def sponsor_inbox(_: AuthSession = Depends(require_sponsors)) -> dict:
    """Everything across all sponsors that is waiting on the committee."""
    marketing = await database.get("/sponsors/marketing/all", {"status": "pending"}) or []
    requests = await database.get("/sponsors/requests/all", {"open_only": True}) or []
    interest = await database.get("/sponsors/interest/open") or []
    # Viewing the sponsor inbox requires sponsors.inbox (on this route), not
    # email.send: the ability to preview work must not grant or require sending.
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


@router.get("/sponsors/{sponsor_id}/detail", dependencies=[Depends(require_action("sponsors.view"))])
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
        "canEmail": session.can("email.send"),
        "canWhatsApp": session.can("whatsapp.post"),
    }


@router.post("/sponsors/{sponsor_id}/notes", dependencies=[Depends(require_action("sponsors.notes.reply")), Depends(verify_csrf)])
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


@router.put("/sponsors/{sponsor_id}/terms/{clause}", dependencies=[Depends(require_action("sponsors.terms.edit")), Depends(verify_csrf)])
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


@router.post("/sponsors/{sponsor_id}/usage", dependencies=[Depends(require_action("sponsors.usage.add")), Depends(verify_csrf)])
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


@router.delete("/sponsors/{sponsor_id}/usage/{record_id}", dependencies=[Depends(require_action("sponsors.usage.delete")), Depends(verify_csrf)])
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


@router.put("/sponsors/{sponsor_id}/events/{event_id}", dependencies=[Depends(require_action("sponsors.events.link")), Depends(verify_csrf)])
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


@router.delete("/sponsors/{sponsor_id}/events/{event_id}", dependencies=[Depends(require_action("sponsors.events.unlink")), Depends(verify_csrf)])
async def unlink_sponsor_event(
    sponsor_id: int, event_id: int, _: AuthSession = Depends(require_sponsors)
) -> dict:
    await database.delete(f"/sponsors/{sponsor_id}/claims/{event_id}")
    return {"ok": True}


@router.post("/sponsors/{sponsor_id}/events/{event_id}/decline", dependencies=[Depends(require_action("sponsors.events.decline")), Depends(verify_csrf)])
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


@router.patch("/sponsor-requests/{request_id}", dependencies=[Depends(require_action("sponsors.requests.edit")), Depends(verify_csrf)])
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


@router.patch("/sponsor-marketing/{marketing_id}", dependencies=[Depends(require_action("sponsors.marketing.edit")), Depends(verify_csrf)])
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


@router.post("/sponsor-marketing/{marketing_id}/preview", dependencies=[Depends(require_action("sponsors.marketing.preview")), Depends(verify_csrf)])
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


def _marketing_post(item: dict, sponsor_name: str) -> str:
    """A sponsor's marketing as a WhatsApp post's words: whose message it is
    first, as in the email — members should never have to guess whether EESoc
    or a company is talking — then theirs, then the files as links."""
    lines = [f"_Shared on behalf of {sponsor_name}, one of EESoc's sponsors._", item.get("body") or ""]
    for url in item.get("media") or []:
        absolute = url if url.startswith("http") else f"{settings.site_origin}{url}"
        lines.append(f"{'PDF' if url.lower().endswith('.pdf') else 'Image'}: {absolute}")
    return "\n\n".join(line for line in lines if line.strip())


@router.post("/sponsor-marketing/{marketing_id}/send", dependencies=[Depends(require_action("sponsors.marketing.send")), Depends(verify_csrf)])
async def send_sponsor_marketing(
    marketing_id: int,
    whatsapp_groups: list[str] = Body(default=[], embed=True, alias="whatsapp"),
    session: AuthSession = Depends(require_any_capability("email", "whatsapp")),
) -> dict:
    """Send a sponsor's marketing: by email to the audience they chose, if they
    asked for email and it has not gone yet — and into the WhatsApp groups
    ticked, if any. Either alone is fine: a request for social media only can
    still go on WhatsApp.

    `email` and `whatsapp`, not `sponsors`: outreach permissions are
    independent of sponsor management. Same
    guards as a newsletter — cap, empty audience, mailbox.
    """
    item = await _marketing_or_404(marketing_id)
    if item["status"] != "pending":
        raise HTTPException(409, "That request has already been dealt with.")
    if whatsapp_groups and not session.can("whatsapp.post"):
        raise HTTPException(403, "You cannot post to WhatsApp groups.")
    groups = await _whatsapp_groups(whatsapp_groups,session)
    by_email = "email" in item["channels"] and not item.get("emailed_at")
    if by_email and not session.can("email.send"):
        raise HTTPException(403, "You cannot send email to members.")
    if not by_email and not groups:
        if "email" not in item["channels"]:
            raise HTTPException(
                400, "This one was asked for on social media only — tick a WhatsApp group to post it there."
            )
        raise HTTPException(409, "This has already been emailed.")
    sponsor = await _sponsor_or_404(item["sponsor_id"])

    recipients: list = []
    campaign = None
    if by_email:
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
        # Marked before the send runs rather than after: a send takes minutes,
        # and a second press in that window must not mail everybody twice.
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

    if groups:
        post = await whatsapp.message(item["subject"], _marketing_post(item, sponsor["name"]))
        _dispatch(whatsapp.post(
            post, groups, campaign_id=campaign and campaign["id"], by=session.user.id,
        ))

    item = await _finish_marketing(item, session)
    return {
        "ok": True,
        "status": item["status"],
        "message": _sending_message(len(recipients), groups),
    }


@router.post("/sponsor-marketing/{marketing_id}/posted", dependencies=[Depends(require_action("sponsors.marketing.posted")), Depends(verify_csrf)])
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


@router.post("/sponsor-marketing/{marketing_id}/complete", dependencies=[Depends(require_action("sponsors.marketing.complete")), Depends(verify_csrf)])
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


@router.post("/sponsor-marketing/{marketing_id}/decline", dependencies=[Depends(require_action("sponsors.marketing.decline")), Depends(verify_csrf)])
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


@router.get("/sponsors/roles-digest", dependencies=[Depends(require_action("sponsors.digest.view"))])
async def roles_digest_preview(_: AuthSession = Depends(require_sponsors)) -> dict:
    listings = await database.get("/sponsors/listings/unannounced") or []
    recipients = await emailing.resolve(["topic:roles"]) if listings else []
    html, _text = emailing.render_body(_roles_digest(listings)) if listings else ("", "")
    return {"count": len(listings), "recipients": len(recipients), "html": html}


@router.post("/sponsors/roles-digest", dependencies=[Depends(require_action("sponsors.digest.send")), Depends(verify_csrf)])
async def send_roles_digest(
    whatsapp_groups: list[str] = Body(default=[], embed=True, alias="whatsapp"),
    session: AuthSession = Depends(require_email),
) -> dict:
    """One email of every new role to the jobs subscribers — and, when ticked,
    the same list posted into WhatsApp groups."""
    listings = await database.get("/sponsors/listings/unannounced") or []
    if not listings:
        raise HTTPException(400, "There are no new roles to tell members about.")
    if whatsapp_groups and not session.can("whatsapp.post"):
        raise HTTPException(403, "You cannot post to WhatsApp groups.")
    groups = await _whatsapp_groups(whatsapp_groups,session)
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
    if groups:
        post = await whatsapp.message(subject, body)
        _dispatch(whatsapp.post(post, groups, campaign_id=campaign["id"], by=session.user.id))
    return {
        "ok": True,
        "message": (
            f"Sending {len(listings)} role(s) to {len(recipients)} subscriber(s)."
            + (f" {_sending_message(0, groups)}" if groups else "")
        ),
    }


# ------------------------------------------------------------------- lockers


@router.get("/lockers", dependencies=[Depends(require_action("lockers.inventory.view"))])
async def admin_lockers(_: AuthSession = Depends(require_lockers)) -> list[dict]:
    return await database.get("/lockers") or []


@router.patch("/lockers/{locker_id}", dependencies=[Depends(require_action("lockers.inventory.edit")), Depends(verify_csrf)])
async def update_locker(
    locker_id: int,
    is_active: bool | None = Body(default=None, embed=True),
    note: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_lockers),
) -> dict:
    return await database.patch(
        f"/lockers/{locker_id}", params={"is_active": is_active, "note": note}
    )


@router.get("/locker-holders", dependencies=[Depends(require_action("lockers.holders.view"))])
async def locker_holders(_: AuthSession = Depends(require_lockers)) -> dict:
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


@router.post("/lockers/notify-clearout", dependencies=[Depends(require_action("lockers.clearout.notify")), Depends(verify_csrf)])
async def notify_clearout(
    deadline: str = Body(embed=True), _: AuthSession = Depends(require_lockers)
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


@router.post("/lockers/release-year", dependencies=[Depends(require_action("lockers.year.release")), Depends(verify_csrf)])
async def release_locker_year(
    year: str = Body(embed=True), _: AuthSession = Depends(require_lockers)
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


@router.get("/locker-grants", dependencies=[Depends(require_action("lockers.allowances.view"))])
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


@router.put("/locker-grants/{shortcode}", dependencies=[Depends(require_action("lockers.allowances.grant")), Depends(verify_csrf)])
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


@router.delete("/locker-grants/{shortcode}", dependencies=[Depends(require_action("lockers.allowances.revoke")), Depends(verify_csrf)])
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


@router.get("/locker-bookings", dependencies=[Depends(require_action("lockers.bookings.view"))])
async def locker_bookings(_: AuthSession = Depends(require_lockers)) -> list[dict]:
    return await database.get("/lockers/bookings", {"active_only": True}) or []


# --------------------------------------------------------------------- email
#
# Two shapes of bulk send, sharing everything below the surface: a free-form
# newsletter, and a message about one event with that event's details rendered
# underneath. Both pick from the same audience list, both are recorded as
# campaigns, both go from the publicity mailbox with a working unsubscribe.


@router.get("/email/audiences", dependencies=[Depends(require_action("email.audiences"))])
async def email_audiences(_: AuthSession = Depends(require_email)) -> dict:
    """Who can be mailed, with live counts, plus the state of both mailboxes.

    The mail status is here rather than on the dashboard because this is the
    screen where it matters: pressing send with a mailbox that cannot
    authenticate produces a campaign that silently fails for every recipient.
    """
    return {
        "audiences": await emailing.audiences(external_societies=_.can("societies.members.mail"), whatsapp_user_id=_.user.id, whatsapp_admin_all=_.can("societies.whatsapp.groups.edit")),
        "mail": mailer.describe(),
        "academicYear": academic_year(),
        # So staging's Send can say its WhatsApp groups are the real ones.
        "environment": settings.environment,
    }


@router.post("/email/preview", dependencies=[Depends(require_action("email.preview")), Depends(verify_csrf)])
async def preview_email(
    body: str = Body(default="", embed=True),
    audiences: list[str] = Body(default=[], embed=True),
    _: AuthSession = Depends(require_email),
) -> dict:
    """Render exactly what will be sent, and count exactly who will get it.

    Same renderer as the send path, deliberately: a preview produced by
    different code is a preview of something else.
    """
    html, text = emailing.render_body(body)
    recipients = await emailing.resolve(audiences,allowed_society_ids='all' if _.can('societies.members.mail') else set()) if audiences else []
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


async def _whatsapp_groups(jids: list[str] | None, session: AuthSession) -> list[dict]:
    """The WhatsApp groups ticked for a send, each one the linked number can
    post in now — or an error saying why not, rather than a send that quietly
    reaches fewer places than the screen showed."""
    jids = list(dict.fromkeys(jids or []))
    if not jids:
        return []
    if not await whatsapp.available(user_id=session.user.id,admin_all=session.can("societies.whatsapp.groups.edit")):
        raise HTTPException(
            503,
            "WhatsApp isn't linked or connected right now, so it can't post into "
            "those groups. Untick them to send the rest, or relink on the Email tab.",
        )
    groups = await whatsapp.pick(jids,user_id=session.user.id,admin_all=session.can("societies.whatsapp.groups.edit"))
    if len(groups) != len(jids):
        raise HTTPException(
            400,
            "The society's number can't post into one of the WhatsApp groups "
            "ticked here any more — it may have left it. Untick it and try again.",
        )
    return groups


async def _whatsapp_targets(audiences: list[str], session: AuthSession) -> list[dict]:
    """The WhatsApp groups ticked among `audiences` (see _whatsapp_groups)."""
    return await _whatsapp_groups(emailing.whatsapp_groups_in(audiences),session)


def _sending_message(recipients: int, groups: list[dict], about: str = "") -> str:
    parts = []
    if recipients:
        parts.append(
            f"Sending to {recipients} recipient(s){about}. This takes about "
            f"{_estimate_minutes(recipients)}; the history below updates as it goes."
        )
    if groups:
        parts.append(
            f"Posting into {', '.join(g.get('name') or g['jid'] for g in groups)} on "
            "WhatsApp — the WhatsApp card lists how each went."
        )
    return " ".join(parts)


@router.post("/email/send", dependencies=[Depends(require_any_action("email.send", "whatsapp.post")), Depends(verify_csrf)])
async def send_newsletter(
    subject: str = Body(default="", embed=True),
    body: str = Body(default="", embed=True),
    heading: str | None = Body(default=None, embed=True),
    audiences: list[str] = Body(default=[], embed=True),
    session: AuthSession = Depends(require_any_capability("email", "whatsapp")),
) -> dict:
    """Send a newsletter — by email, into WhatsApp groups, or both: a
    WhatsApp group is ticked in the same list as everyone else.

    Returns as soon as the campaign is recorded rather than when the last
    message goes out: pacing means a few hundred recipients takes minutes, and
    an admin screen that hangs for that long gets reloaded — which is how
    somebody ends up sending twice.
    """
    mail_audiences = emailing.email_keys(audiences)
    if not mail_audiences and not emailing.whatsapp_groups_in(audiences):
        raise HTTPException(400, "Choose who it goes to.")
    if mail_audiences and not session.can("email.send"):
        raise HTTPException(403, "You cannot send email to members.")
    if emailing.whatsapp_groups_in(audiences) and not session.can("whatsapp.post"):
        raise HTTPException(403, "You cannot post to WhatsApp groups.")
    groups = await _whatsapp_targets(audiences,session)
    recipients = await emailing.resolve(mail_audiences,allowed_society_ids="all" if session.can("societies.members.mail") else set()) if mail_audiences else []
    if mail_audiences:
        # A subject is the email's; a WhatsApp post needs only something to say.
        _check_sendable(subject, mail_audiences, recipients)
    post = await whatsapp.message((heading or subject or "").strip(), body) if groups else ""
    if groups and not post.strip():
        raise HTTPException(400, "Write something to post.")

    campaign = None
    if mail_audiences:
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

    if groups:
        _dispatch(whatsapp.post(
            post, groups, campaign_id=campaign and campaign["id"], by=session.user.id,
        ))

    return {
        "ok": True,
        "campaignId": campaign and campaign["id"],
        "recipients": len(recipients),
        "message": _sending_message(len(recipients), groups),
    }


@router.post("/events/{event_id}/announce", dependencies=[Depends(require_any_action("email.announce_event", "whatsapp.post")), Depends(verify_csrf)])
async def announce_event(
    event_id: int,
    subject: str = Body(default="", embed=True),
    body: str = Body(default="", embed=True),
    heading: str | None = Body(default=None, embed=True),
    audiences: list[str] = Body(default=[], embed=True),
    session: AuthSession = Depends(require_any_capability("email", "whatsapp")),
) -> dict:
    """Email people about one event — an announcement, a reminder, a room change.

    The committee writes the message; the event's date, venue, ticket list and
    booking link are rendered underneath from the database. Retyping those into
    a reminder is how a message ends up advertising last week's room.
    """
    event = await database.get(f"/events/{event_id}")
    if event is None:
        raise HTTPException(404, "No such event")

    # Email audiences and WhatsApp groups, ticked in the same list. A subject
    # is the email's; the event's details are a WhatsApp post by themselves.
    mail_audiences = emailing.email_keys(audiences)
    if not mail_audiences and not emailing.whatsapp_groups_in(audiences):
        raise HTTPException(400, "Choose who it goes to.")
    if mail_audiences and not session.can("email.announce_event"):
        raise HTTPException(403, "You cannot announce events by email.")
    if emailing.whatsapp_groups_in(audiences) and not session.can("whatsapp.post"):
        raise HTTPException(403, "You cannot post to WhatsApp groups.")
    groups = await _whatsapp_targets(audiences,session)
    recipients = await emailing.resolve(mail_audiences,allowed_society_ids="all" if session.can("societies.members.mail") else set()) if mail_audiences else []
    if mail_audiences:
        _check_sendable(subject, mail_audiences, recipients)

    if groups:
        post = await whatsapp.message((heading or subject or "").strip(), body, event)
    if not mail_audiences:
        _dispatch(whatsapp.post(post, groups, by=session.user.id))
        return {
            "ok": True,
            "campaignId": None,
            "recipients": 0,
            "message": _sending_message(0, groups),
        }

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
    if groups:
        _dispatch(whatsapp.post(post, groups, campaign_id=campaign["id"], by=session.user.id))
    return {
        "ok": True,
        "campaignId": campaign["id"],
        "recipients": len(recipients),
        "message": _sending_message(len(recipients), groups, f" about {event['title']}"),
    }


@router.get("/email/test-recipients", dependencies=[Depends(require_action("email.test_recipients.view"))])
async def email_test_list(_: AuthSession = Depends(require_email)) -> dict:
    return {"recipients": await emailing.test_recipients()}


@router.post("/email/test-recipients", dependencies=[Depends(require_action("email.test_recipients.add")), Depends(verify_csrf)])
async def email_test_add(
    email: str = Body(embed=True), session: AuthSession = Depends(require_email)
) -> dict:
    address = normalise_email(email)
    if not address or "@" not in address:
        raise HTTPException(400, "That doesn't look like an email address.")
    await database.post(
        "/email/test-recipients", {"email": address, "added_by": session.user.id}
    )
    return {"recipients": await emailing.test_recipients()}


@router.delete("/email/test-recipients", dependencies=[Depends(require_action("email.test_recipients.remove")), Depends(verify_csrf)])
async def email_test_remove(
    email: str, _: AuthSession = Depends(require_email)
) -> dict:
    await database.delete("/email/test-recipients", {"email": email})
    return {"recipients": await emailing.test_recipients()}


@router.get("/email/campaigns", dependencies=[Depends(require_action("email.campaigns.view"))])
async def email_campaigns(
    limit: int = 30, _: AuthSession = Depends(require_email)
) -> list[dict]:
    return await database.get("/email/campaigns", {"limit": limit}) or []


@router.get("/events/{event_id}/sale-alerts", dependencies=[Depends(require_action("events.bookings.sale_alerts"))])
async def event_sale_alerts(
    event_id: int, session: AuthSession = Depends(require_booking_editor)
) -> dict:
    """Who is waiting to hear that this event is on sale.

    Worth seeing before publishing a sale date: a hundred people waiting is a
    different decision from three.
    """
    await _bookable_event(event_id, session)
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


@router.get("/email/optouts", dependencies=[Depends(require_action("email.optouts.view"))])
async def email_optouts(_: AuthSession = Depends(require_email)) -> list[dict]:
    """Addresses that have unsubscribed. Read-only on purpose: an admin putting
    somebody back on a list by hand is the one thing an unsubscribe must
    prevent."""
    return await database.get("/email/optouts") or []


@router.post("/email/announce-now", dependencies=[Depends(require_action("email.announce_now")), Depends(verify_csrf)])
async def announce_now(_: AuthSession = Depends(require_email)) -> dict:
    """Run the last-chance reminder pass immediately.

    The loop runs every few minutes anyway; this is for when somebody does not
    want to wait for it. On-sale emails are not part of it: they are drafted
    when the event is saved and sent at their approved time.
    """
    return await announce_service.run_once()


# ------------------------------------------------------------------ whatsapp


@router.post("/whatsapp/render", dependencies=[Depends(require_action("whatsapp.render")), Depends(verify_csrf)])
async def whatsapp_render(
    heading: str = Body(default="", embed=True),
    body: str = Body(default="", embed=True),
    event_id: int | None = Body(default=None, embed=True),
    kind: str | None = Body(default=None, embed=True),
    _: AuthSession = Depends(require_whatsapp),
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
