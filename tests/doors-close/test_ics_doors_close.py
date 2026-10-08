"""The per-event .ics announces the doors-close cutoff (acceptance criterion 5).

Pure unit tests — no stack, no HTTP. ``backend/api/utils/ics.py`` imports only
the standard library by contract, loaded by path exactly as the sibling ics
suite does. When an event has a ``doors_close_at``, the calendar DESCRIPTION
must tell the attendee "Doors close HH:MM — no entry after" (London time);
without one, no doors-close wording appears.

``icalendar`` is pinned in CI via ``backend/api/requirements-dev.txt``; these
tests skip cleanly (not error) whenever that library is missing, so collection
passes while the implementation is still in flight.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ICS_MODULE_PATH = REPO_ROOT / "backend" / "api" / "utils" / "ics.py"

if not ICS_MODULE_PATH.is_file():
    pytest.skip(
        f"backend/api/utils/ics.py does not exist yet at {ICS_MODULE_PATH}",
        allow_module_level=True,
    )
_spec = importlib.util.spec_from_file_location("utils.ics", ICS_MODULE_PATH)
ics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ics)


def _parse_description(text: str):
    """Parse one-event ICS text and return its DESCRIPTION string (or None)."""
    pytest.importorskip(
        "icalendar",
        reason="icalendar is not installed; it is pinned in backend/api/requirements-dev.txt",
    )
    from icalendar import Calendar

    cal = Calendar.from_ical(text)
    events = [c for c in cal.subcomponents if c.name == "VEVENT"]
    assert len(events) == 1
    description = events[0].get("description")
    return str(description) if description is not None else None


def _event_dict(**overrides) -> dict:
    payload = {
        "slug": "my-event",
        "title": "My event",
        "description": "A description.",
        "venue": "EEE 403",
        "starts_at": "2026-10-07T18:00:00+00:00",
        "ends_at": "2026-10-07T20:00:00+00:00",
        "published": True,
    }
    payload.update(overrides)
    return payload


def test_ics_description_announces_doors_close_in_london_time():
    # 17:15 UTC on the chosen October day is 18:15 in London (BST). The calendar
    # must state the wall-clock cutoff, not the raw UTC value.
    text = ics.build_event_ics(
        _event_dict(doors_close_at="2026-10-07T17:15:00+00:00"),
        site_origin="https://eesoc.com",
    )
    description = _parse_description(text)
    assert description is not None
    assert "Doors close 18:15" in description
    assert "no entry after" in description
    assert "A description." in description  # appended to, not replacing, the copy


def test_ics_description_omits_doors_close_when_unset():
    # No doors_close_at -> no doors-close wording in the DESCRIPTION at all.
    text = ics.build_event_ics(
        _event_dict(doors_close_at=None),
        site_origin="https://eesoc.com",
    )
    description = _parse_description(text)
    assert description is not None
    assert "Doors close" not in description


def test_ics_doors_close_uses_london_not_utc_wall_clock():
    # Guard against formatting the stored UTC value directly — that showed every
    # time an hour early during BST. 17:15Z must render as 18:15, never 17:15.
    text = ics.build_event_ics(
        _event_dict(doors_close_at="2026-10-07T17:15:00+00:00"),
        site_origin="https://eesoc.com",
    )
    description = _parse_description(text)
    assert "Doors close 17:15" not in description
    assert "Doors close 18:15" in description