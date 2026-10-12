"""Free tickets are places, not holds.

A booking on a ticket with nothing to buy (`requires_purchase` false) is
confirmed the moment it is made — by a member, a guest, or a waitlist offer —
and gets its confirmation email with the QR. The one exception: a members-only
ticket for somebody the feed does not show as a member this year waits, held,
until the matcher's membership pass sees them. Paid tickets still hold.

NEEDS THE COMPOSE TEST STACK (see tests/README.md).
"""

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_072 = REPO_ROOT / "backend/database/migrations/072_free_tickets_confirmed.sql"


def _iso(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


FREE = {"name": "Free seat", "price_pence": 0, "capacity": 20, "requires_purchase": False}
PAID = {"name": "Paid seat", "price_pence": 500, "capacity": 20, "requires_purchase": True}


@pytest.fixture()
def make_event(admin_client):
    """Create published, on-sale events and delete them afterwards."""
    made = []

    def _make(*ticket_types, **overrides) -> dict:
        slug = _slug(overrides.pop("prefix", "free"))
        payload = {
            "slug": slug,
            "title": f"Free tickets {slug[-10:]}",
            "category": "Workshops",
            "starts_at": _iso(20),
            "published": True,
            "sales_open_at": _iso(-1),
            "ticket_types": list(ticket_types) or [FREE],
            **overrides,
        }
        created = admin_client.post("/admin/events", json=payload)
        assert created.status_code in (200, 201), created.text
        made.append(created.json()["id"])
        detail = httpx.get(f"{admin_client.base_url}/events/{slug}", timeout=10).json()
        return {
            "id": created.json()["id"],
            "slug": slug,
            "title": payload["title"],
            "types": {t["name"]: t["id"] for t in detail["ticketTypes"]},
        }

    yield _make
    for event_id in made:
        admin_client.delete(f"/admin/events/{event_id}")


def _signed_in(api: httpx.Client, email: str, make_admin: bool = False) -> tuple[httpx.Client, dict]:
    """A client signed in through dev-login as `email` (the same cookie-header
    shape as conftest's fixtures), with the session view."""
    res = api.post("/auth/dev-login", json={"email": email, "make_admin": make_admin})
    res.raise_for_status()
    client = httpx.Client(
        base_url=str(api.base_url),
        timeout=10.0,
        headers={
            "Cookie": f"session={res.cookies.get('session')}; csrf={res.cookies.get('csrf')}",
            "X-CSRF-Token": res.json()["csrf_token"],
        },
    )
    return client, res.json()


def _outsider(api: httpx.Client) -> tuple[httpx.Client, dict]:
    """Signed in, but not a Union member: an address the stub roster lacks."""
    return _signed_in(api, f"free-outsider-{uuid.uuid4().hex[:8]}@example.com")


def _sql(compose_exec, statement: str) -> str:
    return compose_exec(
        "postgres", "psql", "-U", "eesoc", "-d", "eesoc", "-v", "ON_ERROR_STOP=1",
        "-tA", "-c", statement,
    ).strip()


def _row(compose_exec, reservation_id: int) -> dict:
    out = _sql(
        compose_exec,
        "SELECT status, COALESCE(confirmation_email, ''), "
        "confirmed_at IS NOT NULL, confirmed_at = created_at "
        f"FROM reservations WHERE id = {int(reservation_id)};",
    )
    status, email, has_confirmed_at, at_created = out.split("|")
    return {
        "status": status,
        "confirmation_email": email or None,
        "confirmed_at": has_confirmed_at == "t",
        "confirmed_at_created": at_created == "t",
    }


def _reserve(client: httpx.Client, event: dict, ticket: str) -> httpx.Response:
    return client.post(
        f"/events/{event['slug']}/reserve",
        json={"ticket_type_id": event["types"][ticket], "quantity": 1},
    )


def _confirmation_subjects(mailpit: httpx.Client, title: str, timeout: float = 20.0) -> list[str]:
    """Subjects of mail about this event, polled: the sender runs in the
    background after the booking returns."""
    deadline = time.monotonic() + timeout
    while True:
        subjects = [
            m.get("Subject", "")
            for m in mailpit.get("/api/v1/messages", params={"limit": 200}).json()["messages"]
            if title in m.get("Subject", "")
        ]
        if subjects or time.monotonic() > deadline:
            return subjects
        time.sleep(1)


# ----------------------------------------------------------------- bookings


def test_a_free_booking_is_confirmed_and_emailed(make_event, member_client, mailpit):
    event = make_event(FREE)
    res = _reserve(member_client, event, "Free seat")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reservation"]["status"] == "confirmed"
    assert "Nothing to pay" in body["next"]["message"]

    mine = member_client.get(f"/events/{event['slug']}").json()["myReservations"]
    assert [r["status"] for r in mine] == ["confirmed"]
    assert mine[0]["ticketToken"]

    # The "you're confirmed" email with the ticket — not a "pay by" email.
    subjects = _confirmation_subjects(mailpit, event["title"])
    assert f"You're confirmed for {event['title']}" in subjects


def test_a_paid_booking_is_still_a_hold(make_event, member_client):
    event = make_event(PAID)
    res = _reserve(member_client, event, "Paid seat")
    assert res.status_code == 200, res.text
    assert res.json()["reservation"]["status"] == "held"


def test_a_guest_free_booking_is_confirmed(make_event, api):
    event = make_event(FREE)
    guest = httpx.Client(base_url=str(api.base_url), timeout=10.0)
    held = guest.post(
        f"/events/{event['slug']}/hold",
        json={"ticket_type_id": event["types"]["Free seat"], "quantity": 1},
    )
    assert held.status_code == 200, held.text

    booked = guest.post(
        f"/events/{event['slug']}/reserve",
        json={
            "ticket_type_id": event["types"]["Free seat"],
            "quantity": 1,
            "draft_token": held.json()["draftToken"],
            "guest": {"email": f"free-guest-{uuid.uuid4().hex[:8]}@example.com",
                      "first_name": "Guest"},
        },
    )
    assert booked.status_code == 200, booked.text
    assert booked.json()["reservation"]["status"] == "confirmed"


def test_members_only_free_waits_for_a_non_member(make_event, api, member_client):
    members_free = {**FREE, "name": "Members seat", "members_only": True}
    event = make_event(members_free)

    outsider, _ = _outsider(api)
    waiting = _reserve(outsider, event, "Members seat")
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["reservation"]["status"] == "held"
    assert "membership" in waiting.json()["next"]["message"]

    member = _reserve(member_client, event, "Members seat")
    assert member.status_code == 200, member.text
    assert member.json()["reservation"]["status"] == "confirmed"


def test_the_matcher_confirms_free_bookings_still_waiting(
    make_event, api, admin_client, member_client, compose_exec
):
    """Stragglers an older pod made as holds, and members-only ones once the
    feed shows the person — confirmed by the membership pass. A non-member's
    members-only booking keeps waiting."""
    event = make_event(FREE, {**FREE, "name": "Members seat", "members_only": True})
    member_id = member_client.get("/auth/session").json()["user"]["id"]
    _, outsider = _outsider(api)
    outsider_id = outsider["user"]["id"]

    def held(ticket: str, user_id: int) -> int:
        return int(_sql(compose_exec, (
            "INSERT INTO reservations (event_id, ticket_type_id, user_id, quantity, "
            "status, expires_at) VALUES "
            f"({event['id']}, {event['types'][ticket]}, {user_id}, 1, 'held', "
            "NOW() + interval '10 days') RETURNING id;"
        )).splitlines()[0])

    straggler = held("Free seat", outsider_id)
    member_waiting = held("Members seat", member_id)
    outsider_waiting = held("Members seat", outsider_id)

    res = admin_client.post("/admin/reconcile")
    assert res.status_code == 200, res.text
    assert res.json()["confirmed_free"] >= 2

    assert _row(compose_exec, straggler)["status"] == "confirmed"
    assert _row(compose_exec, member_waiting)["status"] == "confirmed"
    assert _row(compose_exec, outsider_waiting)["status"] == "held"


def test_a_free_waitlist_place_is_confirmed_not_offered(make_event, api, member_client):
    """Before: a free offer was a hold nothing could confirm, so it lapsed and
    the place moved on. Now the freed place is simply theirs."""
    event = make_event({**FREE, "capacity": 1})
    taker, _ = _outsider(api)
    taken = _reserve(taker, event, "Free seat")
    assert taken.status_code == 200, taken.text
    assert taken.json()["reservation"]["status"] == "confirmed"

    joined = member_client.post(
        f"/events/{event['slug']}/waitlist",
        json={"ticket_type_id": event["types"]["Free seat"], "quantity": 1},
    )
    assert joined.status_code == 200, joined.text

    cancelled = taker.post(f"/reservations/{taken.json()['reservation']['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text

    mine = member_client.get(f"/events/{event['slug']}").json()["myReservations"]
    assert [r["status"] for r in mine] == ["confirmed"]


def test_moving_a_hold_onto_a_free_ticket_confirms_it(make_event, admin_client, member_client):
    event = make_event(PAID, FREE)
    held = _reserve(member_client, event, "Paid seat")
    assert held.json()["reservation"]["status"] == "held"

    moved = admin_client.post(
        f"/admin/reservations/{held.json()['reservation']['id']}/ticket-type",
        json={"ticket_type_id": event["types"]["Free seat"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["reservation"]["status"] == "confirmed"


# ---------------------------------------------------------------- migration


def test_migration_072_converts_live_free_holds_without_queuing_email(
    make_event, api, compose_exec
):
    """Existing free holds become confirmed places, marked 'skipped' so the
    deploy does not send a burst of emails through the shared mailbox."""
    event = make_event(
        FREE, PAID, {**FREE, "name": "Members seat", "members_only": True}
    )
    _, someone = _outsider(api)
    user_id = someone["user"]["id"]

    def held(ticket: str, expires: str) -> int:
        return int(_sql(compose_exec, (
            "INSERT INTO reservations (event_id, ticket_type_id, user_id, quantity, "
            "status, expires_at, created_at) VALUES "
            f"({event['id']}, {event['types'][ticket]}, {user_id}, 1, 'held', "
            f"{expires}, NOW() - interval '2 days') RETURNING id;"
        )).splitlines()[0])

    free_live = held("Free seat", "NOW() + interval '10 days'")
    free_expired = held("Free seat", "NOW() - interval '1 hour'")
    paid_live = held("Paid seat", "NOW() + interval '10 days'")
    members_live = held("Members seat", "NOW() + interval '10 days'")

    _sql(compose_exec, MIGRATION_072.read_text())

    assert _row(compose_exec, free_live) == {
        "status": "confirmed", "confirmation_email": "skipped",
        "confirmed_at": True, "confirmed_at_created": True,
    }
    assert _row(compose_exec, free_expired)["status"] == "held"
    assert _row(compose_exec, free_expired)["confirmation_email"] is None
    assert _row(compose_exec, paid_live)["status"] == "held"
    assert _row(compose_exec, paid_live)["confirmation_email"] is None
    # Waits for the membership pass, but will not email when it is confirmed.
    assert _row(compose_exec, members_live)["status"] == "held"
    assert _row(compose_exec, members_live)["confirmation_email"] == "skipped"
