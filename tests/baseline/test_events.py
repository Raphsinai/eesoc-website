"""The public event listing and one event's detail, plus the union sync.

Two things are asserted together because they share one dependency chain:

1. `/events` lists events and `/events/{slug}` renders one. The events live in
   our own `events` table, which is empty on a fresh stack -- the union mirror
   carries members/committee/products/sales, *not* events. So this test creates
   one published event through the admin write path first, then reads it back
   the way the SPA does.
2. `member_client` reports `is_member: true`. That is false until the startup
   sync has copied the stub member (member1@ic.ac.uk) into the mirror and the
   api has matched the account to it, so the assertion polls rather than
   assuming the sync finished before pytest started.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

# How long to wait for the startup sync to surface the stub member. The sync
# runs at boot and can still be in flight when the tests begin.
MEMBER_SYNC_TIMEOUT_SECONDS = 60


def _future_iso(days: int = 30) -> str:
    """An event start well in the future, so the "upcoming" list keeps it."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _unique_slug() -> str:
    """A slug that cannot collide with a previous run or a parallel test."""
    return f"baseline-event-{uuid.uuid4().hex[:12]}"


def _make_event(admin_client, slug, title):
    """Create a published, upcoming event through the admin endpoint."""
    response = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": title,
            "category": "Workshops",
            "venue": "EEE 403",
            "starts_at": _future_iso(),
            "published": True,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_event_list_detail_and_member_sync(api, admin_client, member_client):
    slug = _unique_slug()
    title = f"Baseline test event {slug[-6:]}"
    created = _make_event(admin_client, slug, title)

    try:
        # The calendar is served to everyone, unauthenticated.
        listing = api.get("/events")
        assert listing.status_code == 200
        payload = listing.json()
        assert isinstance(payload["events"], list)
        assert payload["count"] == len(payload["events"])
        assert any(event["slug"] == slug for event in payload["events"])

        # Detail renders the title back through the SPA's shape.
        detail = api.get(f"/events/{slug}")
        assert detail.status_code == 200
        assert detail.json()["title"] == title
        assert detail.json()["slug"] == slug
    finally:
        # Remove the test event so repeated runs do not accumulate calendar
        # entries. Both the create and delete go through the admin API.
        admin_client.delete(f"/admin/events/{created['id']}")

    # The union stub member must have flowed through the sync into the api's
    # membership answer. Polling: SYNC_ON_STARTUP runs in the background and
    # this test may simply be faster than it.
    deadline = time.monotonic() + MEMBER_SYNC_TIMEOUT_SECONDS
    last = None
    while time.monotonic() < deadline:
        last = member_client.get("/auth/session").json()
        if last.get("is_member") is True:
            break
        time.sleep(2)
    assert last is not None and last.get("is_member") is True, (
        "member_client never became a member within "
        f"{MEMBER_SYNC_TIMEOUT_SECONDS}s; last session view: {last}"
    )