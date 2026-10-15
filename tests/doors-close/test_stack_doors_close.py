"""Doors-close behaviour against the live test stack (criteria 1, 2 and 6).

NEEDS THE COMPOSE TEST STACK — this file runs in CI only (docker compose
-f docker-compose.yml -f docker-compose.test.yml; see tests/README.md). Like the
baseline and events-calendar suites it does not guard on an env var: it talks to
the api fixture and fails with a connection error if the stack is not up.

Covers:

1. Migration 063 applies; an event created without ``doors_close_at`` reads back
   null and one created/updated with it reads the value back.
2. ``doors_close_at`` after ``ends_at`` is refused (422); before ``starts_at``
   is accepted.
6. ``GET /reservations?user_id=`` rows carry ``event_doors_close_at`` and
   ``event_venue`` (the database adapter serves them for section 15's My
   tickets).

The reservations query lives on the internal ``database`` service, which is
reachable only on the compose network — never published to the host — so the
assertion runs the HTTP request inside that container (via ``compose_exec``)
and parses the JSON off stdout.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

MILLER_VENUE = "EEE 403"


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _unique_slug(prefix: str = "doors") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _db_reservations(compose_exec, user_id: int) -> list[dict]:
    """GET /reservations?user_id= on the internal database adapter.

    The adapter is the only service that speaks SQL and is exposed on the
    compose network solely; the api is its only TCP client. There is no host
    route to it, so the request is made *inside* its own container and the
    JSON body is read back off stdout.
    """
    program = (
        "import json,urllib.request;"
        "u=urllib.request.urlopen("
        f"'http://localhost:8080/reservations?user_id={user_id}'"
        ");"
        "print(json.dumps(json.loads(u.read().decode())))"
    )
    stdout = compose_exec("database", "python", "-c", program)
    return json.loads(stdout.strip())


# ------------------------------------------------------------------- criterion 1


def test_migration_063_applies_on_the_stack(compose_exec):
    """Migration 063 is recorded as applied, so the doors_close_at column exists."""
    name = compose_exec(
        "postgres",
        "psql",
        "-U",
        "eesoc",
        "-d",
        "eesoc",
        "-tAc",
        "SELECT name FROM public.migrations WHERE number = 63;",
    ).strip()
    assert name == "event_doors_close", f"migration 63 recorded as {name!r}"


def test_event_created_without_doors_close_reads_back_null(admin_client):
    """The default is no cutoff: an event sent without the field has null."""
    slug = _unique_slug("dc-null")
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Doors null {slug[-6:]}",
            "category": "Workshops",
            "venue": MILLER_VENUE,
            "starts_at": _future_iso(),
            "published": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]

    try:
        # The create response is the stored row: the field is present and null.
        assert "doors_close_at" in created.json(), created.json()
        assert created.json()["doors_close_at"] is None

        # And a later read (the admin list roundtrip) still says null.
        listing = admin_client.get("/admin/events").json()
        row = next(e for e in listing if e["id"] == created_id)
        assert row["doors_close_at"] is None
    finally:
        admin_client.delete(f"/admin/events/{created_id}")


def test_event_created_with_doors_close_reads_it_back(admin_client):
    """A doors-close time set at creation round-trips through the write path."""
    slug = _unique_slug("dc-set")
    doors = (datetime.now(timezone.utc) + timedelta(days=30, hours=1)).replace(
        microsecond=0
    )
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Doors set {slug[-6:]}",
            "category": "Workshops",
            "venue": MILLER_VENUE,
            "starts_at": _future_iso(),
            # A day long, so doors an hour after the start fall inside it —
            # doors closing after the end is refused (criterion 2).
            "ends_at": _future_iso(days=31),
            "doors_close_at": doors.isoformat(),
            "published": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]

    try:
        # Read back the same absolute instant, whatever offset the adapter
        # chose to serialize it with.
        assert datetime.fromisoformat(created.json()["doors_close_at"]) == doors

        listing = admin_client.get("/admin/events").json()
        row = next(e for e in listing if e["id"] == created_id)
        assert datetime.fromisoformat(row["doors_close_at"]) == doors
    finally:
        admin_client.delete(f"/admin/events/{created_id}")


# ------------------------------------------------------------------- criterion 2


def test_update_doors_close_after_ends_is_rejected_422(admin_client):
    """Doors close after the event ends is a typo, refused with a 422."""
    slug = _unique_slug("dc-after")
    starts = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
    ends = starts + timedelta(hours=2)
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Doors after {slug[-6:]}",
            "category": "Workshops",
            "venue": MILLER_VENUE,
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "published": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]

    try:
        updated = admin_client.put(
            f"/admin/events/{created_id}",
            json={
                "slug": slug,
                "title": f"Doors after {slug[-6:]}",
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
                "doors_close_at": (ends + timedelta(hours=1)).isoformat(),
            },
        )
        assert updated.status_code == 422, updated.text
        assert "Doors close" in updated.json().get("detail", "")
    finally:
        admin_client.delete(f"/admin/events/{created_id}")


def test_update_doors_close_before_starts_is_accepted(admin_client):
    """Doors close before the start is legitimate (e.g. 18:00 for an 18:30 talk)."""
    slug = _unique_slug("dc-before")
    starts = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
    ends = starts + timedelta(hours=2)
    doors = starts - timedelta(minutes=30)
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Doors before {slug[-6:]}",
            "category": "Workshops",
            "venue": MILLER_VENUE,
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "published": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]

    try:
        updated = admin_client.put(
            f"/admin/events/{created_id}",
            json={
                "slug": slug,
                "title": f"Doors before {slug[-6:]}",
                "starts_at": starts.isoformat(),
                "ends_at": ends.isoformat(),
                "doors_close_at": doors.isoformat(),
            },
        )
        assert updated.status_code in (200, 201), updated.text
        assert datetime.fromisoformat(updated.json()["doors_close_at"]) == doors
    finally:
        admin_client.delete(f"/admin/events/{created_id}")


def test_create_with_doors_close_years_early_is_rejected_422(admin_client):
    """A year typed short — 0006 for 2026 — is refused, not saved as the year 6.

    A doors-close before the start is allowed, so without this the typo went
    through and the event page printed it.
    """
    slug = _unique_slug("dc-year")
    starts = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Doors year {slug[-6:]}",
            "category": "Social",
            "venue": MILLER_VENUE,
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(hours=3)).isoformat(),
            # What the screenshot showed: 31/10/0006, 20:30.
            "doors_close_at": datetime(6, 10, 31, 20, 30, tzinfo=timezone.utc).isoformat(),
        },
    )
    if created.status_code in (200, 201):
        admin_client.delete(f"/admin/events/{created.json()['id']}")
    assert created.status_code == 422, created.text
    assert "Doors close" in created.json().get("detail", "")


# ------------------------------------------------------------------- criterion 6


def test_reservations_rows_include_doors_close_and_venue(
    admin_client, member_client, compose_exec
):
    """The per-user reservations query surfaces the event's doors-close + venue.

    Section 15 (My tickets) renders each booking without a second query, so the
    ``/reservations`` route must carry both event columns alongside the
    reservation itself.
    """
    slug = _unique_slug("dc-res")
    starts = (datetime.now(timezone.utc) + timedelta(days=30)).replace(microsecond=0)
    ends = starts + timedelta(hours=2)
    doors = starts + timedelta(hours=1)

    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Doors res {slug[-6:]}",
            "category": "Workshops",
            "venue": MILLER_VENUE,
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "doors_close_at": doors.isoformat(),
            "sales_open_at": _past_iso(),
            "published": True,
            "ticket_types": [
                {
                    "name": "Free seat",
                    "price_pence": 0,
                    "capacity": 10,
                    "requires_purchase": False,
                }
            ],
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]
    ticket_type_id = created.json()["ticket_types"][0]["id"]

    try:
        reserved = member_client.post(
            f"/events/{slug}/reserve",
            json={"ticket_type_id": ticket_type_id, "quantity": 1},
        )
        assert reserved.status_code == 200, reserved.text

        user_id = member_client.get("/auth/session").json()["user"]["id"]
        rows = _db_reservations(compose_exec, user_id)
        ours = [r for r in rows if r.get("event_id") == created_id]
        assert ours, f"no reservation rows for event {created_id} and user {user_id}"

        row = ours[0]
        assert "event_doors_close_at" in row, row
        assert datetime.fromisoformat(row["event_doors_close_at"]) == doors
        assert "event_venue" in row, row
        assert row["event_venue"] == MILLER_VENUE
    finally:
        admin_client.delete(f"/admin/events/{created_id}")