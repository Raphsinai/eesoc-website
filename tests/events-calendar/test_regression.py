"""Regression asserts: booking, notify-me and waitlist still behave.

NEEDS THE COMPOSE TEST STACK — this file runs in CI only (see tests/README.md).
Section 05 is additive, so the flows that already worked must not have changed
shape: a member can still hold a seat, a stranger can still request a heads-up
on an unopened event, and a full event still takes waitlist joins.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx


def _anon() -> httpx.Client:
    """A fresh, cookie-less client over the api base URL."""
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
        "title": f"Regression {slug[-6:]}",
        "category": "Workshops",
        "starts_at": _future_iso(),
        "published": True,
    }
    payload.update(overrides)
    response = admin_client.post("/admin/events", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_reserve_still_returns_held_in_my_reservations(admin_client, member_client):
    # Booking is reservation-style: the member holds a seat and the detail
    # payload's myReservations shows it as held.
    slug = _unique_slug("reg-reserve")
    created = _create_event(
        admin_client,
        slug,
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
        ticket_type_id = _anon().get(f"/events/{slug}").json()["ticketTypes"][0]["id"]
        reserved = member_client.post(
            f"/events/{slug}/reserve",
            json={"ticket_type_id": ticket_type_id, "quantity": 1},
        )
        assert reserved.status_code == 200, reserved.text
        assert reserved.json()["ok"] is True

        detail = member_client.get(f"/events/{slug}").json()
        mine = detail["myReservations"]
        assert any(
            r["status"] == "held" and r["ticketTypeId"] == ticket_type_id
            for r in mine
        )
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_notify_me_on_unopened_event_still_works(admin_client):
    # A stranger can still ask to be emailed when an unannounced event opens:
    # no account is created and the call returns ok.
    slug = _unique_slug("reg-notify")
    created = _create_event(admin_client, slug)  # no sales_open_at -> "soon"
    try:
        notified = _anon().post(
            f"/events/{slug}/notify-me",
            json={"email": "someone@example.com"},
        )
        assert notified.status_code == 200, notified.text
        assert notified.json()["ok"] is True
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_waitlist_join_on_full_event_still_works(admin_client, member_client):
    # A ticket type with capacity 0 models a full event; joining the waitlist
    # still succeeds (queued behind it) rather than erroring.
    slug = _unique_slug("reg-waitlist")
    created = _create_event(
        admin_client,
        slug,
        sales_open_at=_past_iso(),
        ticket_types=[
            {
                "name": "Full seat",
                "price_pence": 0,
                "capacity": 0,
                "requires_purchase": False,
            }
        ],
    )
    try:
        ticket_type_id = _anon().get(f"/events/{slug}").json()["ticketTypes"][0]["id"]
        joined = member_client.post(
            f"/events/{slug}/waitlist",
            json={"ticket_type_id": ticket_type_id, "quantity": 1},
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["ok"] is True
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")