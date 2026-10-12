"""Sale windows and end-of-day holds against the live test stack (section 17).

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var.

Covers acceptance criteria 2–7:

2. a hold on a linked ticket gets exactly the deadline api/utils/holds.py
   computes — SQL and Python agree — including when the Union close caps it;
3. a hold on an unlinked ticket still lasts until the event starts;
4. once reservations close, members, guests and the waitlist are refused,
   in the api and in the adapter itself; a committee-added ticket still works;
5. bringing the Union close forward clamps live holds, not confirmed ones;
6. the matcher still confirms a hold whose deadline has passed;
7. reservations closing after the Union close, or either after the event, is
   refused with a 422.
"""

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from utils.holds import hold_deadline  # noqa: E402

# The stub's Winter Show product (tests/union_stub/fixtures.py).
PRODUCT, LINE = 502, 5021
CLOSED = "Booking for this event has closed."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture()
def make_event(admin_client):
    """Create an on-sale event; returns (event, payload) and deletes it after.

    The payload is returned so a test can PUT it back with changes — the admin
    update replaces the whole event.
    """
    made = []

    def _make(*, linked=True, hold_hours=24, **changes):
        slug = f"window-{uuid.uuid4().hex[:10]}"
        starts = _now() + timedelta(days=20)
        payload = {
            "slug": slug,
            "title": f"Window {slug[-6:]}",
            "category": "Socials",
            "venue": "EEE 403",
            "starts_at": _iso(starts),
            "ends_at": _iso(starts + timedelta(hours=3)),
            "sales_open_at": _iso(_now() - timedelta(days=1)),
            "published": True,
            "hold_hours": hold_hours,
            "ticket_types": [{
                "name": "Paid seat", "price_pence": 800, "capacity": 20,
                "requires_purchase": True, "max_per_user": 4,
            }],
            **changes,
        }
        created = admin_client.post("/admin/events", json=payload)
        assert created.status_code in (200, 201), created.text
        event = created.json()
        made.append(event["id"])
        ticket = event["ticket_types"][0]["id"]
        if linked:
            link = admin_client.put(
                f"/admin/ticket-types/{ticket}/product",
                json={"union_product_id": PRODUCT, "union_product_line_id": LINE},
            )
            assert link.status_code in (200, 201), link.text
        # Keep ids on the payload so a PUT updates the ticket rather than
        # replacing it.
        payload["ticket_types"] = [{**payload["ticket_types"][0], "id": ticket}]
        return event, payload

    yield _make
    for event_id in made:
        admin_client.delete(f"/admin/events/{event_id}")


def _reserve(client, event, **extra):
    return client.post(
        f"/events/{event['slug']}/reserve",
        json={"ticket_type_id": event["ticket_types"][0]["id"], "quantity": 1, **extra},
    )


def _my_hold(client, event) -> dict:
    mine = client.get(f"/events/{event['slug']}").json()["myReservations"]
    held = [r for r in mine if r["status"] == "held"]
    assert held, mine
    return held[-1]


def _adapter(compose_exec, method: str, path: str, body: dict) -> dict:
    """Call the database adapter directly, from inside its own container — it
    is not published to the host. The api's own checks are bypassed, which is
    the point: the gate has to hold in the adapter too."""
    program = (
        "import json,urllib.request;"
        f"r=urllib.request.Request('http://localhost:8080{path}',"
        f"data=json.dumps({json.dumps(body)!r}).encode(),method='{method}',"
        "headers={'Content-Type':'application/json'});"
        "print(urllib.request.urlopen(r).read().decode())"
    )
    return json.loads(compose_exec("database", "python", "-c", program).strip())


def _sql(compose_exec, statement: str) -> str:
    return compose_exec(
        "postgres", "psql", "-U", "eesoc", "-d", "eesoc", "-v", "ON_ERROR_STOP=1",
        "-tA", "-c", statement,
    ).strip()


# ----------------------------------------------------- 2, 3: the deadline


def test_a_linked_hold_ends_at_midnight_as_the_reference_says(make_event, member_client):
    event, _ = make_event(hold_hours=24)
    before = _now()
    res = _reserve(member_client, event)
    after = _now()
    assert res.status_code == 200, res.text
    assert res.json()["reservation"]["status"] == "held"

    expires = _at(res.json()["reservation"]["expiresAt"])
    # Unless the request straddled 23:00 or midnight, both ends agree.
    assert expires in {hold_deadline(before, 24), hold_deadline(after, 24)}


def test_the_union_close_caps_the_deadline(make_event, member_client):
    close = (_now() + timedelta(minutes=30)).replace(microsecond=0)
    event, _ = make_event(hold_hours=72, union_sales_close_at=_iso(close))
    res = _reserve(member_client, event)
    assert res.status_code == 200, res.text
    assert _at(res.json()["reservation"]["expiresAt"]) == close
    assert hold_deadline(_now(), 72, close) == close


def test_an_unlinked_hold_still_lasts_until_the_event(make_event, member_client):
    event, payload = make_event(linked=False)
    res = _reserve(member_client, event)
    assert res.status_code == 200, res.text
    assert _at(res.json()["reservation"]["expiresAt"]) == _at(payload["starts_at"])


# --------------------------------------------------------- 4: the gate


def test_after_reservations_close_nobody_can_book(make_event, member_client, api):
    event, _ = make_event(reservations_close_at=_iso(_now() - timedelta(minutes=1)))

    member = _reserve(member_client, event)
    assert member.status_code == 409 and member.json()["detail"] == CLOSED

    guest_hold = api.post(
        f"/events/{event['slug']}/hold",
        json={"ticket_type_id": event["ticket_types"][0]["id"], "quantity": 1},
    )
    assert guest_hold.status_code == 409, guest_hold.text
    assert guest_hold.json()["detail"] == CLOSED

    waitlist = member_client.post(f"/events/{event['slug']}/waitlist", json={})
    assert waitlist.status_code == 409 and waitlist.json()["detail"] == CLOSED

    page = api.get(f"/events/{event['slug']}").json()
    assert page["salesState"] == "closed"
    assert page["status"] == "Booking closed"


def test_the_adapter_refuses_too(make_event, member_client, compose_exec):
    event, _ = make_event(reservations_close_at=_iso(_now() - timedelta(minutes=1)))
    user_id = member_client.get("/auth/session").json()["user"]["id"]
    body = {"event_id": event["id"], "ticket_type_id": event["ticket_types"][0]["id"],
            "user_id": user_id, "quantity": 1}
    assert _adapter(compose_exec, "POST", "/reservations", body) == {
        "ok": False, "reason": "reservations_closed", "reservation": None,
    }
    draft = _adapter(compose_exec, "POST", "/reservations/draft",
                     {**body, "user_id": None, "draft_token": uuid.uuid4().hex})
    assert draft["reason"] == "reservations_closed"


def test_a_committee_added_ticket_still_works(make_event, admin_client):
    event, _ = make_event(reservations_close_at=_iso(_now() - timedelta(minutes=1)))
    added = admin_client.post(
        f"/admin/events/{event['id']}/tickets",
        json={"ticket_type_id": event["ticket_types"][0]["id"],
              "email": f"door-{uuid.uuid4().hex[:6]}@example.com",
              "first_name": "Door", "last_name": "Guest", "quantity": 1,
              "send_confirmation": False},
    )
    assert added.status_code in (200, 201), added.text


def test_the_page_says_when_booking_closes(make_event, api):
    closes = _now() + timedelta(days=2)
    event, _ = make_event(reservations_close_at=_iso(closes))
    page = api.get(f"/events/{event['slug']}").json()
    assert page["salesState"] == "open"
    assert _at(page["bookingClosesAt"]) == closes.replace(microsecond=0)


# ------------------------------------------------- 5: closing earlier clamps


def test_bringing_the_union_close_forward_clamps_live_holds(
    make_event, member_client, admin_client
):
    event, payload = make_event(hold_hours=72)
    res = _reserve(member_client, event)
    assert res.status_code == 200, res.text
    assert _at(res.json()["reservation"]["expiresAt"]) > _now() + timedelta(days=1)

    close = (_now() + timedelta(minutes=20)).replace(microsecond=0)
    updated = admin_client.put(
        f"/admin/events/{event['id']}", json={**payload, "union_sales_close_at": _iso(close)}
    )
    assert updated.status_code == 200, updated.text
    assert _at(_my_hold(member_client, event)["expiresAt"]) == close


def test_clamping_leaves_confirmed_bookings_alone(make_event, admin_client):
    event, payload = make_event(hold_hours=72)
    added = admin_client.post(
        f"/admin/events/{event['id']}/tickets",
        json={"ticket_type_id": event["ticket_types"][0]["id"],
              "email": f"kept-{uuid.uuid4().hex[:6]}@example.com",
              "first_name": "Kept", "last_name": "Booking", "quantity": 1,
              "send_confirmation": False},
    )
    assert added.status_code in (200, 201), added.text

    def bookings() -> dict:
        lists = admin_client.get(f"/admin/events/{event['id']}/reservations").json()
        rows = [r for group in lists.values() for r in group]
        return {r["id"]: (r["status"], r["expires_at"]) for r in rows}

    before = bookings()
    assert any(status == "confirmed" for status, _ in before.values()), before

    close = _now() + timedelta(minutes=20)
    updated = admin_client.put(
        f"/admin/events/{event['id']}", json={**payload, "union_sales_close_at": _iso(close)}
    )
    assert updated.status_code == 200, updated.text
    assert bookings() == before


# ------------------------------------------ 6: the overnight purchase still confirms


def test_the_matcher_confirms_a_hold_whose_deadline_has_passed(
    make_event, member_client, admin_client, compose_exec
):
    event, _ = make_event(hold_hours=24)
    order = f"W{uuid.uuid4().hex[:8].upper()}"
    res = _reserve(member_client, event, order_number=order)
    assert res.status_code == 200, res.text
    hold_id = res.json()["reservation"]["id"]

    # Bought before midnight; the sync ran at 06:05, after the deadline.
    _sql(compose_exec, (
        "UPDATE reservations SET expires_at = NOW() - interval '6 hours' "
        f"WHERE id = {int(hold_id)};"
    ))
    year = admin_client.get("/admin/dashboard").json()["academicYear"]
    _sql(compose_exec, (
        "INSERT INTO union_sales (order_number, academic_year, sale_datetime, "
        "product_id, product_line_id, price, quantity) VALUES "
        f"('{order}', '{year}', NOW() - interval '7 hours', {PRODUCT}, {LINE}, 8.00, 1);"
    ))

    assert admin_client.post("/admin/reconcile").status_code == 200
    status = _sql(compose_exec, f"SELECT status FROM reservations WHERE id = {int(hold_id)};")
    assert status == "confirmed"


# ------------------------------------------------------------ 7: validation


@pytest.mark.parametrize("windows, says", [
    ({"reservations_close_at": 2, "union_sales_close_at": 1}, "after Union sales close"),
    ({"reservations_close_at": 30}, "Reservations close cannot be after the event ends"),
    ({"union_sales_close_at": 30}, "Union sales close cannot be after the event ends"),
])
def test_impossible_windows_are_refused(admin_client, windows, says):
    starts = _now() + timedelta(days=20)
    payload = {
        "slug": f"window-bad-{uuid.uuid4().hex[:8]}",
        "title": "Bad window", "starts_at": _iso(starts),
        "ends_at": _iso(starts + timedelta(hours=3)), "published": False,
        **{k: _iso(_now() + timedelta(days=d)) for k, d in windows.items()},
    }
    refused = admin_client.post("/admin/events", json=payload)
    assert refused.status_code == 422, refused.text
    assert says in refused.json()["detail"]
