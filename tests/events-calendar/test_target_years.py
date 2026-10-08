"""Target year groups: create, read, update, and the admin row.

NEEDS THE COMPOSE TEST STACK — this file runs in CI only (see tests/README.md).
The target-year list is an additive event setting (comma-joined years in the
database, surfaced as a list of ints on the public payload); these tests walk
it through the admin write path and both read paths (acceptance criterion 5).
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


def _unique_slug() -> str:
    return f"target-years-{uuid.uuid4().hex[:12]}"


def _normalise_years(value) -> list[int]:
    """Reduce whatever shape the admin row stores to a list of ints.

    The database column is comma-separated text ("1,2") while the public model
    is a list of ints; accepting both here checks the data round-tripped rather
    than the wire representation, which the lead's implementation may expose as
    either form on the raw admin listing.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [int(v) for v in value]
    text = str(value).strip()
    if not text:
        return []
    return [int(part) for part in text.split(",") if part.strip()]


def test_create_string_input_reads_back_as_list(admin_client):
    # The write validator accepts a comma string ("1,2") and the public payload
    # returns it as a list of ints.
    slug = _unique_slug()
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Target years {slug[-6:]}",
            "category": "Workshops",
            "starts_at": _future_iso(),
            "published": True,
            "target_years": "1,2",
        },
    )
    assert created.status_code in (200, 201), created.text

    try:
        detail = _anon().get(f"/events/{slug}")
        assert detail.status_code == 200
        assert detail.json()["targetYears"] == [1, 2]
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_create_list_input_update_and_admin_row(admin_client):
    # The whole round-trip: create with a list, update to a new list, and
    # confirm both the public payload and the admin listing carry the value.
    slug = _unique_slug()
    title = f"Target years {slug[-6:]}"
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": title,
            "category": "Workshops",
            "starts_at": _future_iso(),
            "published": True,
            "target_years": [1, 2],
        },
    )
    assert created.status_code in (200, 201), created.text

    try:
        detail = _anon().get(f"/events/{slug}")
        assert detail.json()["targetYears"] == [1, 2]

        # The admin form saves the whole event, so the PUT mirrors a real save:
        # the required write fields plus the changed years.
        updated = admin_client.put(
            f"/admin/events/{created['id']}",
            json={
                "slug": slug,
                "title": title,
                "category": "Workshops",
                "starts_at": _future_iso(),
                "published": True,
                "target_years": [3],
            },
        )
        assert updated.status_code in (200, 201), updated.text

        after = _anon().get(f"/events/{slug}")
        assert after.json()["targetYears"] == [3]

        # The admin listing (raw DB rows) carries the same value, in whichever
        # representation the database route exposes it.
        listing = admin_client.get("/admin/events")
        assert listing.status_code == 200
        row = next(e for e in listing.json() if e.get("slug") == slug)
        assert _normalise_years(row.get("target_years")) == [3]
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")


def test_unset_target_years_defaults_to_empty_list(admin_client):
    # Aimed at everyone by default: no target_years means an empty list, not a
    # missing key or null.
    slug = _unique_slug()
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Target years {slug[-6:]}",
            "category": "Workshops",
            "starts_at": _future_iso(),
            "published": True,
        },
    )
    assert created.status_code in (200, 201), created.text

    try:
        detail = _anon().get(f"/events/{slug}")
        assert detail.json()["targetYears"] == []
    finally:
        admin_client.delete(f"/admin/events/{created['id']}")