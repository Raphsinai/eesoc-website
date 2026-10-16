"""Ticket types on sale at different times (migration 080) — early bird, then
general — against the live test stack.

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var.

Covers:

1. the times round-trip through the admin write path, and an event with none
   behaves as before;
2. a type before its window, or after it, cannot be reserved or held — in the
   api, with a message saying when, and in the adapter itself;
3. a type inside its window books as normal while another is outside;
4. a committee-added ticket on a type outside its window still works;
5. the waitlist is not offered a type outside its window;
6. the event page and card say what is on sale and when the next type opens;
7. windows that could never be open are refused with a 422.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

NOT_YET = "goes on sale on"
ENDED = "is no longer on sale."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _free(name: str, **window) -> dict:
    """A free ticket type: free tickets confirm outright, so no Union product
    is needed for these tests."""
    return {
        "name": name, "price_pence": 0, "capacity": 20, "requires_purchase": False,
        "max_per_user": 4, **{k: _iso(v) for k, v in window.items()},
    }


@pytest.fixture()
def make_event(admin_client):
    """Create a published event on sale since two days ago with the given
    ticket types; deletes it afterwards."""
    made = []

    def _make(ticket_types, **changes):
        slug = f"tt-window-{uuid.uuid4().hex[:10]}"
        starts = _now() + timedelta(days=20)
        payload = {
            "slug": slug,
            "title": f"Ticket windows {slug[-6:]}",
            "category": "Socials",
            "venue": "EEE 403",
            "starts_at": _iso(starts),
            "ends_at": _iso(starts + timedelta(hours=3)),
            "sales_open_at": _iso(_now() - timedelta(days=2)),
            "published": True,
            "ticket_types": ticket_types,
            **changes,
        }
        created = admin_client.post("/admin/events", json=payload)
        assert created.status_code in (200, 201), created.text
        event = created.json()
        made.append(event["id"])
        return event, payload

    yield _make
    for event_id in made:
        admin_client.delete(f"/admin/events/{event_id}")


def _type(event: dict, name: str) -> dict:
    return next(t for t in event["ticket_types"] if t["name"] == name)


def _early_bird_then_general(make_event, *, early_ended: bool):
    """Early bird until a cut-over, general from it. `early_ended` puts the
    cut-over an hour ago (general on sale now), otherwise in two days (early
    bird on sale now)."""
    cut = _now() - timedelta(hours=1) if early_ended else _now() + timedelta(days=2)
    return make_event([
        _free("Early bird", sales_close_at=cut),
        _free("General", sales_open_at=cut),
    ])


def _reserve(client, event, ticket):
    return client.post(
        f"/events/{event['slug']}/reserve",
        json={"ticket_type_id": ticket["id"], "quantity": 1},
    )


def _adapter(compose_exec, method: str, path: str, body: dict) -> dict:
    """Call the database adapter directly, from inside its own container — the
    api's checks are bypassed, which is the point: the gate holds there too."""
    program = (
        "import json,urllib.request;"
        f"r=urllib.request.Request('http://localhost:8080{path}',"
        f"data={json.dumps(body)!r}.encode(),method='{method}',"
        "headers={'Content-Type':'application/json'});"
        "print(urllib.request.urlopen(r).read().decode())"
    )
    return json.loads(compose_exec("database", "python", "-c", program).strip())


# ------------------------------------------------------------ 1: round trip


def test_the_times_round_trip_and_survive_an_unchanged_save(make_event, admin_client):
    event, payload = _early_bird_then_general(make_event, early_ended=False)
    early, general = _type(event, "Early bird"), _type(event, "General")
    assert early["sales_open_at"] is None and early["sales_close_at"] is not None
    assert general["sales_open_at"] is not None and general["sales_close_at"] is None
    assert _at(early["sales_close_at"]) == _at(general["sales_open_at"])

    # Saved back with ids, as the admin form does: the times stay put.
    payload["ticket_types"] = [
        {**payload["ticket_types"][0], "id": early["id"]},
        {**payload["ticket_types"][1], "id": general["id"]},
    ]
    saved = admin_client.put(f"/admin/events/{event['id']}", json=payload)
    assert saved.status_code == 200, saved.text
    again = saved.json()
    assert _type(again, "General")["sales_open_at"] == general["sales_open_at"]

    # And cleared when the form sends none.
    payload["ticket_types"] = [
        {**t, "sales_open_at": None, "sales_close_at": None} for t in payload["ticket_types"]
    ]
    cleared = admin_client.put(f"/admin/events/{event['id']}", json=payload).json()
    assert all(
        t["sales_open_at"] is None and t["sales_close_at"] is None
        for t in cleared["ticket_types"]
    )


def test_an_event_without_windows_is_unchanged(make_event, api, member_client):
    event, _ = make_event([_free("Seat")])
    page = api.get(f"/events/{event['slug']}").json()
    seat = page["ticketTypes"][0]
    assert seat["salesState"] == "open" and seat["selectable"] is True
    assert page["status"] in ("20 places", "Free entry")
    assert _reserve(member_client, event, _type(event, "Seat")).status_code == 200


# ----------------------------------------------------- 2, 3: booking gates


def test_general_cannot_be_booked_before_it_opens(make_event, member_client, api):
    event, _ = _early_bird_then_general(make_event, early_ended=False)
    general = _type(event, "General")

    refused = _reserve(member_client, event, general)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"].startswith("“General” " + NOT_YET)

    guest_hold = api.post(
        f"/events/{event['slug']}/hold", json={"ticket_type_id": general["id"]}
    )
    assert guest_hold.status_code == 409, guest_hold.text
    assert NOT_YET in guest_hold.json()["detail"]

    # Early bird, inside its window, books as normal.
    booked = _reserve(member_client, event, _type(event, "Early bird"))
    assert booked.status_code == 200, booked.text


def test_early_bird_cannot_be_booked_after_it_ends(make_event, member_client, api):
    event, _ = _early_bird_then_general(make_event, early_ended=True)
    early = _type(event, "Early bird")

    refused = _reserve(member_client, event, early)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "“Early bird” " + ENDED

    guest_hold = api.post(
        f"/events/{event['slug']}/hold", json={"ticket_type_id": early["id"]}
    )
    assert guest_hold.status_code == 409, guest_hold.text

    booked = _reserve(member_client, event, _type(event, "General"))
    assert booked.status_code == 200, booked.text


def test_the_adapter_refuses_too(make_event, member_client, compose_exec):
    event, _ = _early_bird_then_general(make_event, early_ended=True)
    early = _type(event, "Early bird")
    user_id = member_client.get("/auth/session").json()["user"]["id"]
    body = {"event_id": event["id"], "ticket_type_id": early["id"],
            "user_id": user_id, "quantity": 1}
    assert _adapter(compose_exec, "POST", "/reservations", body) == {
        "ok": False, "reason": "type_not_on_sale", "reservation": None,
    }
    draft = _adapter(compose_exec, "POST", "/reservations/draft",
                     {**body, "user_id": None, "draft_token": uuid.uuid4().hex})
    assert draft["reason"] == "type_not_on_sale"


# ------------------------------------------------- 4: the committee's override


def test_a_committee_added_ticket_still_works(make_event, admin_client):
    event, _ = _early_bird_then_general(make_event, early_ended=True)
    added = admin_client.post(
        f"/admin/events/{event['id']}/tickets",
        json={"ticket_type_id": _type(event, "Early bird")["id"],
              "email": f"late-bird-{uuid.uuid4().hex[:6]}@example.com",
              "first_name": "Late", "last_name": "Bird", "quantity": 1,
              "send_confirmation": False},
    )
    assert added.status_code in (200, 201), added.text


# ------------------------------------------------------------- 5: waitlist


def test_the_waitlist_is_not_offered_a_type_that_has_ended(
    make_event, member_client, admin_client
):
    # Early bird ended with places left; general has one place.
    cut = _now() - timedelta(hours=1)
    event, _ = make_event([
        _free("Early bird", sales_close_at=cut),
        {**_free("General", sales_open_at=cut), "capacity": 1},
    ])
    mine = _reserve(member_client, event, _type(event, "General"))
    assert mine.status_code == 200, mine.text

    joined = admin_client.post(f"/events/{event['slug']}/waitlist", json={})
    assert joined.status_code == 200, joined.text

    # Cancelling frees the general place and runs the promote pass.
    cancelled = member_client.post(f"/reservations/{mine.json()['reservation']['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text

    offered = admin_client.get(f"/events/{event['slug']}").json()["myReservations"]
    assert [r["ticketTypeName"] for r in offered] == ["General"], offered


# ---------------------------------------------------------- 6: what it says


def test_the_page_says_what_is_on_sale(make_event, api):
    event, _ = _early_bird_then_general(make_event, early_ended=False)
    page = api.get(f"/events/{event['slug']}").json()
    early = next(t for t in page["ticketTypes"] if t["name"] == "Early bird")
    general = next(t for t in page["ticketTypes"] if t["name"] == "General")
    assert early["salesState"] == "open" and early["selectable"] is True
    assert early["salesCloseAt"] is not None
    assert general["salesState"] == "scheduled" and general["selectable"] is False
    assert general["salesOpenAt"] is not None
    # The event itself is on sale, as before.
    assert page["salesState"] == "open"


def test_between_early_bird_and_general_the_card_says_when(make_event, api, member_client):
    # Early bird sold out; general not on sale yet.
    opens = _now() + timedelta(days=2)
    event, _ = make_event([
        {**_free("Early bird"), "capacity": 1},
        _free("General", sales_open_at=opens),
    ])
    booked = _reserve(member_client, event, _type(event, "Early bird"))
    assert booked.status_code == 200, booked.text

    card = next(e for e in api.get("/events").json()["events"] if e["slug"] == event["slug"])
    assert card["status"].startswith("On sale "), card["status"]
    assert card["statusTone"] == "accent"


# ------------------------------------------------------------ 7: validation


@pytest.mark.parametrize("window, says", [
    # Opens before the event's own sale (two days ago).
    ({"sales_open_at": -3}, "goes on sale before the event's tickets do"),
    ({"sales_open_at": 2, "sales_close_at": 1}, "stops selling before it goes on sale"),
    ({"sales_close_at": -3}, "stops selling before the event's tickets go on sale"),
    ({"sales_close_at": 30}, "cannot stop selling after the event ends"),
])
def test_impossible_windows_are_refused(admin_client, window, says):
    starts = _now() + timedelta(days=20)
    payload = {
        "slug": f"tt-window-bad-{uuid.uuid4().hex[:8]}",
        "title": "Bad ticket window", "starts_at": _iso(starts),
        "ends_at": _iso(starts + timedelta(hours=3)), "published": False,
        "sales_open_at": _iso(_now() - timedelta(days=2)),
        "ticket_types": [_free("Early bird", **{
            k: _now() + timedelta(days=d) for k, d in window.items()
        })],
    }
    refused = admin_client.post("/admin/events", json=payload)
    assert refused.status_code == 422, refused.text
    assert says in refused.json()["detail"]
    assert "“Early bird”" in refused.json()["detail"]


def test_opening_after_reservations_close_is_refused(admin_client):
    starts = _now() + timedelta(days=20)
    payload = {
        "slug": f"tt-window-bad-{uuid.uuid4().hex[:8]}",
        "title": "Bad ticket window", "starts_at": _iso(starts),
        "ends_at": _iso(starts + timedelta(hours=3)), "published": False,
        "sales_open_at": _iso(_now() - timedelta(days=2)),
        "reservations_close_at": _iso(_now() + timedelta(days=5)),
        "ticket_types": [_free("General", sales_open_at=_now() + timedelta(days=6))],
    }
    refused = admin_client.post("/admin/events", json=payload)
    assert refused.status_code == 422, refused.text
    assert "after reservations for the event close" in refused.json()["detail"]
