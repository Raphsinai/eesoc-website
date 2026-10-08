"""The .ics endpoints: single event, all-events feed, and the category filter.

NEEDS THE COMPOSE TEST STACK — this file runs in CI only (docker compose
-f docker-compose.yml -f docker-compose.test.yml; see tests/README.md). It talks
to the api through the same /api origin the browser uses.

It creates events through the admin write path, then reads the calendar
endpoints with a fresh, cookie-less client so the "no session needed" property
is actually exercised — the shared session-scoped ``api`` fixture could have
picked up cookies, so a local client guarantees anonymity.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

MEETING_URL = "https://meet.example.com/private-ics-room"


def _anon() -> httpx.Client:
    """A fresh, cookie-less client over the api base URL.

    Built per test so nothing a previous test signed in carries over; the
    calendar endpoints must serve calendar clients that hold no session.
    """
    return httpx.Client(
        base_url=os.environ.get("EESOC_API_BASE", "http://localhost:8080/api"),
        timeout=10.0,
    )


def _parse(text: str):
    """Parse ICS text with icalendar, returning the list of VEVENTs."""
    pytest.importorskip(
        "icalendar",
        reason="icalendar is not installed; it is pinned in backend/api/requirements-dev.txt",
    )
    from icalendar import Calendar

    cal = Calendar.from_ical(text)
    return [c for c in cal.subcomponents if c.name == "VEVENT"]


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _unique_slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _titles(text: str) -> list[str]:
    return [str(e["summary"]) for e in _parse(text)]


def test_single_event_ics_public_and_leak_free(admin_client):
    # Published events serve their .ics without a session, and that .ics never
    # carries the meeting link even when one is set and marked private.
    slug = _unique_slug("ics-single")
    title = f"ICS single {slug[-6:]}"
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": title,
            "category": "Workshops",
            "venue": "EEE 403",
            "starts_at": _future_iso(),
            "ends_at": _future_iso(),
            "published": True,
            "map_url": "https://example.com/map",
            "meeting_url": MEETING_URL,
            "meeting_url_private": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]

    try:
        response = _anon().get(f"/events/{slug}/calendar.ics")
        assert response.status_code == 200, response.text
        # Calendar clients are not browsers: the content type must say calendar.
        assert response.headers["content-type"].startswith("text/calendar")
        assert "charset=utf-8" in response.headers["content-type"]

        events = _parse(response.text)
        assert len(events) == 1
        assert str(events[0]["summary"]) == title
        # The venue map is public, but the meet room is exactly what must stay
        # out of a shareable .ics.
        assert MEETING_URL not in response.text
    finally:
        # The create response is an httpx.Response, not the event dict, so the
        # id is read off its JSON body rather than subscripted directly.
        admin_client.delete(f"/admin/events/{created_id}")


def test_feed_and_category_filter_scope(admin_client):
    # Three events: one published in Workshops, one unpublished, one published
    # in another category. The all-events feed shows the published ones and
    # hides the draft; the category filter narrows to the requested category.
    workshop = _unique_slug("ics-ws")
    draft = _unique_slug("ics-draft")
    social = _unique_slug("ics-social")
    ws_title = f"ICS ws {workshop[-6:]}"
    draft_title = f"ICS draft {draft[-6:]}"
    social_title = f"ICS social {social[-6:]}"

    created = []
    for slug, title, published, category in [
        (workshop, ws_title, True, "Workshops"),
        (draft, draft_title, False, "Workshops"),
        (social, social_title, True, "Socials"),
    ]:
        resp = admin_client.post(
            "/admin/events",
            json={
                "slug": slug,
                "title": title,
                "category": category,
                "venue": "EEE 403",
                "starts_at": _future_iso(),
                "published": published,
                "meeting_url": MEETING_URL,
                "meeting_url_private": True,
            },
        )
        assert resp.status_code in (200, 201), resp.text
        created.append(resp.json()["id"])

    try:
        # No session at all: a fresh client hits the feed.
        feed = _anon().get("/events.ics")
        assert feed.status_code == 200
        assert feed.headers["content-type"].startswith("text/calendar")
        feed_titles = _titles(feed.text)
        assert ws_title in feed_titles
        assert social_title in feed_titles
        # The negative asserts: a draft never appears, and neither does a
        # meeting link, in the public feed.
        assert draft_title not in feed_titles
        assert MEETING_URL not in feed.text

        # The category filter is an exact match on the publicly-shown category.
        workshops = _anon().get("/events.ics", params={"category": "Workshops"})
        assert workshops.status_code == 200
        ws_titles = _titles(workshops.text)
        assert ws_title in ws_titles
        assert social_title not in ws_titles
        assert draft_title not in ws_titles
        assert MEETING_URL not in workshops.text

        socials = _anon().get("/events.ics", params={"category": "Socials"})
        social_titles = _titles(socials.text)
        assert social_title in social_titles
        assert ws_title not in social_titles
    finally:
        for event_id in created:
            admin_client.delete(f"/admin/events/{event_id}")


def test_unpublished_event_ics_is_404(admin_client):
    # Calendar clients cannot fetch a draft by guessing its slug: unpublished
    # events are 404 on the single-event endpoint too.
    slug = _unique_slug("ics-draft404")
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"ICS draft 404 {slug[-6:]}",
            "category": "Workshops",
            "starts_at": _future_iso(),
            "published": False,
        },
    )
    assert created.status_code in (200, 201), created.text
    created_id = created.json()["id"]

    try:
        response = _anon().get(f"/events/{slug}/calendar.ics")
        assert response.status_code == 404
    finally:
        # Same as the single-event test: the id lives in the response's JSON
        # body, not as a key on the httpx.Response itself.
        admin_client.delete(f"/admin/events/{created_id}")
