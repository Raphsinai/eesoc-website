"""Event selection, short-name snapshotting and last-print state safeguards."""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_ADAPTER_URL", "http://localhost:8081")
sys.path.insert(0, str(ROOT / "backend/api"))

from routes import photos, admin
from services import photo_print
from models import auth
from utils.roles import PERMISSIONS


def person(*grants):
    allowed = set(grants)
    return SimpleNamespace(user=SimpleNamespace(id=42), can=allowed.__contains__, is_admin=True)


def sample_event(id=81, *, published=True, industry=False):
    return {
        "id": id, "title": "EESoc Halloween Social 2026",
        "photo_short_name": "Halloween 2026", "published": published,
        "industry": industry, "starts_at": "2026-10-31T19:00:00Z",
    }


def sample_set():
    return {
        "id": 91, "event_id": 81, "token": "abcdefghijabcdefghijabcdefghijab",
        "title": "EESoc Halloween Social 2026", "print_label": "Halloween 2026",
        "printed_at": None, "orientation": "portrait",
        "status": "draft", "created_by": 42,
        "created_at": "2026-10-26T16:00:00Z", "published_at": None,
        "images": [],
    }


def test_photo_event_permission_is_independent_of_event_visibility():
    assert "events.photo_short_name.edit" in PERMISSIONS
    assert "photos.print" in PERMISSIONS
    published = sample_event()
    hidden = sample_event(published=False)
    assert photos._can_select_event(person(), published)
    assert not photos._can_select_event(person(), hidden)
    assert photos._can_select_event(person("events.view", "events.scope.all"), hidden)
    assert not photos._can_select_event(person("events.view", "events.scope.industry"), hidden)
    assert photos._can_select_event(
        person("events.view", "events.scope.industry"),
        sample_event(published=False, industry=True),
    )


def test_event_list_last_print_preselect_and_scope_filter(monkeypatch):
    async def fake_get(path):
        if path == "/photo-sets/events":
            return [sample_event(81), sample_event(82, published=False),
                    sample_event(83, published=True)]
        assert path == "/photo-sets/last-printed"
        return {"event_id": 83}

    monkeypatch.setattr(photos.database, "get", fake_get)
    response = asyncio.run(photos.photo_event_choices(session=person("photos.view")))
    assert [event["id"] for event in response["events"]] == [81, 83]
    assert response["lastPrintedEventId"] == 83
    assert response["events"][0]["photoShortName"] == "Halloween 2026"
    assert not response["events"][0]["canEditShortName"]


def test_last_print_hidden_event_not_returned(monkeypatch):
    async def fake_get(path):
        return [sample_event(81)] if path == "/photo-sets/events" else {"event_id": 82}
    monkeypatch.setattr(photos.database, "get", fake_get)
    response = asyncio.run(photos.photo_event_choices(session=person("photos.view")))
    assert response["lastPrintedEventId"] is None


def test_photo_create_uses_event_id_not_arbitrary_title(monkeypatch):
    calls = []

    async def fake_get(path):
        assert path == "/events/81"
        return sample_event()

    async def fake_post(path, body):
        assert path == "/photo-sets"
        calls.append(body)
        return sample_set()

    monkeypatch.setattr(photos.database, "get", fake_get)
    monkeypatch.setattr(photos.database, "post", fake_post)
    payload = photos.CreatePhotos(event_id=81, image_ids=[5, 7], orientation="portrait")
    result = asyncio.run(photos.create_set(payload, session=person("photos.create")))
    assert calls[0]["event_id"] == 81
    assert "title" not in calls[0]
    assert result["title"] == "EESoc Halloween Social 2026"
    assert result["printLabel"] == "Halloween 2026"
    assert result["eventId"] == 81


def test_photo_create_cannot_use_inaccessible_draft(monkeypatch):
    async def fake_get(path):
        return sample_event(published=False)

    async def forbidden_post(*args, **kwargs):
        raise AssertionError("Database must not create an unauthorised photo set")

    monkeypatch.setattr(photos.database, "get", fake_get)
    monkeypatch.setattr(photos.database, "post", forbidden_post)
    with pytest.raises(HTTPException) as error:
        asyncio.run(photos.create_set(
            photos.CreatePhotos(event_id=81, image_ids=[5]),
            session=person("photos.create"),
        ))
    assert error.value.status_code == 404


def test_short_name_editor_enforces_event_scope(monkeypatch):
    async def fake_get(path):
        return sample_event(industry=False)

    async def forbidden_patch(*args, **kwargs):
        raise AssertionError("No writes outside the event scope")

    monkeypatch.setattr(admin.database, "get", fake_get)
    monkeypatch.setattr(admin.database, "patch", forbidden_patch)
    with pytest.raises(HTTPException) as error:
        asyncio.run(admin.edit_photo_short_name(
            81, photo_short_name="Halloween",
            session=person("events.photo_short_name.edit", "events.scope.industry"),
        ))
    assert error.value.status_code == 403


def test_short_name_editor_modifies_only_one_event_field(monkeypatch):
    async def fake_get(path):
        return sample_event(industry=True)

    calls = []
    async def fake_patch(path, data):
        calls.append((path, data))
        return {**sample_event(), "photo_short_name": data["photo_short_name"]}

    monkeypatch.setattr(admin.database, "get", fake_get)
    monkeypatch.setattr(admin.database, "patch", fake_patch)
    response = asyncio.run(admin.edit_photo_short_name(
        81, photo_short_name="  Halloween  ",
        session=person("events.photo_short_name.edit", "events.scope.industry"),
    ))
    assert calls == [("/events/81/photo-short-name", {"photo_short_name": "Halloween"})]
    assert response["photo_short_name"] == "Halloween"


def test_print_label_passed_to_renderer(monkeypatch):
    from io import BytesIO

    photo = Image.new("RGB", (1000, 800), "#647e9f")
    output = BytesIO()
    photo.save(output, "JPEG")
    with patch.object(photo_print, "_header", wraps=photo_print._header) as spy:
        photo_print.render([output.getvalue()], "Full Event Title",
                           "https://eesoc.com/photos/abcdef123456",
                           print_label="Short Event")
        assert spy.call_args.args[1] == "Short Event"
        photo_print.render([output.getvalue()], "Full Event Title",
                           "https://eesoc.com/photos/abcdef123456")
        assert spy.call_args.args[1] == "Full Event Title"


def test_brand_pins_raster_is_not_old_mark():
    assert photo_print.LOGO.name == "eesoc-pins-light.png"
    with Image.open(photo_print.LOGO) as img:
        assert img.width > img.height * 3
        assert img.mode == "RGBA"
        assert img.getbbox()


def test_photo_short_name_has_independent_action_route():
    photo_routes = {
        (route.path, frozenset(route.methods)): route
        for route in admin.router.routes if hasattr(route, "methods")
    }
    route = photo_routes[("/admin/events/{event_id}/photo-short-name", frozenset({"PATCH"}))]
    assert len(route.dependencies) >= 2
