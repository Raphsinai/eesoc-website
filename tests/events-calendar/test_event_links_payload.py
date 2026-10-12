"""The event-detail payload: field allowlist and meeting-link gating.

NEEDS THE COMPOSE TEST STACK — this file runs in CI only (see tests/README.md).
It checks the API contract for GET /events/{slug}: the payload picks up the new
link/target fields and nothing unexpected, and the private meeting link is only
surfaced to the event's booked attendees (or an admin), never to a stranger.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx

MEETING_URL = "https://meet.example.com/private-room"


# The explicit allowlist for GET /events/{slug}: the existing event-card fields,
# the detail-only fields, and the four new fields this section adds. Anything
# else in the payload is a leak (acceptance criterion 6).
ALLOWED_FIELDS = {
    # event card
    "id", "slug", "category", "title", "date", "time", "venue",
    "status", "statusTone", "imageUrl", "signupUrl", "startsAt", "endsAt",
    "soldOut", "published", "salesState", "salesOpenAt",
    # detail-only
    "description", "capacity", "remaining", "holdHours", "ticketTypes",
    "myReservations",
    # new in this section
    "mapUrl", "meetingUrl", "meetingUrlPrivate", "targetYears",
    # added since, public by design: the doors-close time (section 14) and the
    # sponsors confirmed for the event (the 2026-27 agreement, 359badf — only
    # active sponsors, name and logo)
    "doorsClose", "doorsCloseAt", "sponsors",
}


def _anon() -> httpx.Client:
    """A fresh, cookie-less client so "anonymous" is really anonymous."""
    return httpx.Client(
        base_url=os.environ.get("EESOC_API_BASE", "http://localhost:8080/api"),
        timeout=10.0,
    )


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _unique_slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _create_event(admin_client, slug, **overrides) -> dict:
    payload = {
        "slug": slug,
        "title": f"Links {slug[-6:]}",
        "category": "Workshops",
        "starts_at": _future_iso(),
        "published": True,
    }
    payload.update(overrides)
    response = admin_client.post("/admin/events", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_event_payload_within_field_allowlist(admin_client):
    # Whatever the implementation adds later, the detail payload must not drift
    # beyond the fields the client is documented to read.
    created = _create_event(admin_client, _unique_slug("links-allow"))
    try:
        detail = _anon().get(f"/events/{created['slug']}")
        assert detail.status_code == 200
        assert set(detail.json()) <= ALLOWED_FIELDS
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_private_meeting_link_gated_to_booked_attendee(admin_client, member_client):
    # A private meeting link is hidden from a stranger, then shown to the member
    # who actually holds a booking on the event.
    slug = _unique_slug("links-private")
    created = _create_event(
        admin_client,
        slug,
        meeting_url=MEETING_URL,
        meeting_url_private=True,
        sales_open_at=_past_iso(),
        ticket_types=[
            {
                "name": "Free seat",
                "price_pence": 0,
                "capacity": 10,
                "requires_purchase": False,
            }
        ],
    )
    try:
        anonymous = _anon().get(f"/events/{slug}")
        assert anonymous.status_code == 200
        assert anonymous.json()["meetingUrl"] is None
        assert anonymous.json()["meetingUrlPrivate"] is True

        # Read the ticket type id from the public payload, then book it as the
        # member (the member_client already carries the CSRF header the reserve
        # endpoint requires).
        ticket_type_id = anonymous.json()["ticketTypes"][0]["id"]
        booked = member_client.post(
            f"/events/{slug}/reserve",
            json={"ticket_type_id": ticket_type_id, "quantity": 1},
        )
        assert booked.status_code == 200, booked.text
        assert booked.json()["ok"] is True
        # A free seat is a place outright (migration 072), not a hold.
        assert booked.json()["reservation"]["status"] == "confirmed"

        # The holder now sees the link the stranger could not.
        as_member = member_client.get(f"/events/{slug}")
        assert as_member.status_code == 200
        assert as_member.json()["meetingUrl"] == MEETING_URL
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_signed_in_member_without_booking_does_not_see_private_link(
    admin_client, member_client
):
    # A signed-in member who simply has not booked THIS event is no more
    # entitled to the private link than a stranger: the gate is a live booking
    # on the event, not membership. This is a separate, fresh event so the
    # member_client (always member1) has no reservation on it.
    slug = _unique_slug("links-member-nobook")
    created = _create_event(
        admin_client,
        slug,
        meeting_url=MEETING_URL,
        meeting_url_private=True,
    )
    try:
        as_member = member_client.get(f"/events/{slug}")
        assert as_member.status_code == 200
        assert as_member.json()["meetingUrl"] is None
        assert as_member.json()["meetingUrlPrivate"] is True
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_non_private_meeting_link_visible_to_anonymous(admin_client):
    # A meeting link the committee chose not to hide is public, so a stranger
    # sees it in the payload.
    created = _create_event(
        admin_client,
        _unique_slug("links-public"),
        meeting_url="https://meet.example.com/open-room",
        meeting_url_private=False,
    )
    try:
        detail = _anon().get(f"/events/{created['slug']}")
        assert detail.json()["meetingUrl"] == "https://meet.example.com/open-room"
        assert detail.json()["meetingUrlPrivate"] is False
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_signup_url_present_when_set_and_absent_when_unset(admin_client):
    # The external sign-up link is a first-class card field: present when
    # configured, null when not (acceptance criterion 4).
    with_link = _create_event(
        admin_client, _unique_slug("links-signup"),
        signup_url="https://example.com/signup",
    )
    without_link = _create_event(admin_client, _unique_slug("links-nosignup"))
    try:
        with_payload = _anon().get(f"/events/{with_link['slug']}").json()
        assert with_payload["signupUrl"] == "https://example.com/signup"

        without_payload = _anon().get(f"/events/{without_link['slug']}").json()
        assert without_payload["signupUrl"] is None
    finally:
        admin_client.delete(f"/admin/events/{with_link['id']}")
        admin_client.delete(f"/admin/events/{without_link['id']}")