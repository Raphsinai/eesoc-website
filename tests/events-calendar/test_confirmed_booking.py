"""A CONFIRMED booking unlocks the private meeting link (acceptance criterion 3).

NEEDS THE COMPOSE TEST STACK — this file runs in CI only (see tests/README.md).
Every other booking test stops at status "held", because the hold is the part
the request path controls: promotion to "confirmed" only happens once a matching
Union sale has synced in. This test drives that whole chain end to end so the
"confirmed booking sees the private link" rule is actually exercised:

1. create a paid event (requires_purchase, the default is true) with sales open
   and a private meeting link;
2. link the whole event to the stub's Winter Show product (502);
3. reserve as the member, passing an order number;
4. insert a matching union_sales row straight into postgres — the stub's sales
   feed is empty by design, so the sale has to be planted;
5. run the admin sync, which upserts (never deletes) sales and then matches the
   held reservation to the planted sale, promoting it to confirmed.

The sale is planted via the compose_exec fixture because there is deliberately
no API to write a sale: sales only arrive over the bridge, which the tests never
call.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx

MEETING_URL = "https://meet.example.com/confirmed-room"

# The stub product the ticket links to (tests/union_stub/fixtures.py PRODUCTS).
WINTER_SHOW_PRODUCT_ID = 502
WINTER_SHOW_LINE_ID = 5021


def _anon() -> httpx.Client:
    """A fresh, cookie-less client: the private link must stay hidden from it."""
    return httpx.Client(
        base_url=os.environ.get("EESOC_API_BASE", "http://localhost:8080/api"),
        timeout=10.0,
    )


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_confirmed_booking_sees_private_meeting_link(
    admin_client, member_client, union_stub, compose_exec
):
    slug = f"confirmed-{uuid.uuid4().hex[:12]}"
    # Real order numbers contain letters; this one is the join key between the
    # reservation and the planted sale (matched case-insensitively on both sides).
    order_number = f"ORD-{uuid.uuid4().hex[:12].upper()}"

    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Confirmed {slug[-6:]}",
            "category": "Workshops",
            "starts_at": _future_iso(),
            "ends_at": _future_iso(),
            "published": True,
            "sales_open_at": _past_iso(),
            "meeting_url": MEETING_URL,
            "meeting_url_private": True,
            "ticket_types": [
                {
                    "name": "Paid seat",
                    "price_pence": 800,
                    "capacity": 10,
                    "requires_purchase": True,
                }
            ],
        },
    )
    assert created.status_code in (200, 201), created.text
    event = created.json()
    event_id = event["id"]
    # The union_sales.academic_year column is NOT NULL; the event's own row
    # carries the current year, which is the value the sync would have used.
    year = event.get("academic_year")
    if not year:
        year = union_stub.get("/academic-year").json()["academic_year"]

    try:
        # Event-level product link: real admin endpoint, CSRF handled by the
        # admin_client fixture. The line stays open at event level, so the sale
        # on any line of product 502 matches.
        linked = admin_client.put(
            f"/admin/events/{event_id}/product",
            json={"union_product_id": WINTER_SHOW_PRODUCT_ID},
        )
        assert linked.status_code in (200, 201), linked.text

        # The public payload names the ticket type by its id, which is all the
        # reserve endpoint needs.
        ticket_type_id = _anon().get(f"/events/{slug}").json()["ticketTypes"][0]["id"]
        reserved = member_client.post(
            f"/events/{slug}/reserve",
            json={"ticket_type_id": ticket_type_id, "quantity": 1, "order_number": order_number},
        )
        assert reserved.status_code == 200, reserved.text
        assert reserved.json()["reservation"]["status"] == "held"

        # Plant the sale the evening sync would have pulled from eActivities.
        # Inserted straight into postgres because no API can create a sale, and
        # the stub's own sales feed is empty on purpose (fixtures.py SALES = []).
        sale_sql = (
            "INSERT INTO public.union_sales "
            "(order_number, academic_year, sale_datetime, product_id, product_line_id, "
            "price, quantity) "
            f"VALUES ('{order_number}', '{year}', NOW(), {WINTER_SHOW_PRODUCT_ID}, "
            f"{WINTER_SHOW_LINE_ID}, 8.00, 1)"
        )
        compose_exec(
            "postgres",
            "sh",
            "-c",
            f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "{sale_sql}"',
        )

        # The sync upserts (never deletes) sales, so the planted row survives,
        # and then the matcher promotes the matching hold to confirmed. The
        # members/committee/products steps run too, all against the stub.
        synced = admin_client.post("/admin/sync")
        assert synced.status_code == 200, synced.text

        # The member now holds a confirmed booking, which is what unlocks the
        # private meeting link in the detail payload.
        as_member = member_client.get(f"/events/{slug}")
        assert as_member.status_code == 200
        member_payload = as_member.json()
        assert any(
            r["status"] == "confirmed" for r in member_payload["myReservations"]
        ), member_payload["myReservations"]
        assert member_payload["meetingUrl"] == MEETING_URL

        # A stranger, doing the same read with no session, still sees nothing.
        stranger = _anon().get(f"/events/{slug}")
        assert stranger.status_code == 200
        assert stranger.json()["meetingUrl"] is None
    finally:
        admin_client.delete(f"/admin/events/{event_id}")