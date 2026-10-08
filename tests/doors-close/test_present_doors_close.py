"""Doors-close formatting in ``present.py`` (acceptance criteria 3, 4 and 5).

Pure unit tests — no test stack, no HTTP. ``backend/api/utils/present.py``
imports only the standard library by contract (like ``utils/ics.py``), so it is
loaded straight off the filesystem and these tests run anywhere pytest runs.

The public event payload must expose ``doorsCloseAt`` (the ISO timestamp) and
``doorsClose`` (a ready-formatted string): "18:15" when doors close on the same
London day as the start, and "Thu 9 Oct, 18:15" when they do not. ``None`` when
there is no doors-close time at all.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESENT_MODULE_PATH = REPO_ROOT / "backend" / "api" / "utils" / "present.py"

# present.py imports only the standard library, so loading it by path pulls in
# no FastAPI or database client. The same skip-only-if-absent rule as the ics
# tests: a present module with a real bug fails loudly, it does not skip.
if not PRESENT_MODULE_PATH.is_file():
    pytest.skip(
        f"backend/api/utils/present.py does not exist yet at {PRESENT_MODULE_PATH}",
        allow_module_level=True,
    )
_spec = importlib.util.spec_from_file_location("utils.present", PRESENT_MODULE_PATH)
present = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(present)


# Both times on a fixed summer day, in British Summer Time (UTC+1), so the
# London wall clock is unambiguous and does not depend on "today".
DOORS_SAME_DAY = "2026-10-09T18:15:00+01:00"
STARTS_SAME_DAY = "2026-10-09T18:00:00+01:00"
DOORS_NEXT_DAY = "2026-10-09T18:15:00+01:00"
STARTS_PREV_DAY = "2026-10-08T18:00:00+01:00"


# ------------------------------------------------------------------ formatting


def test_doors_close_same_day_is_time_only():
    """Same London day as the start -> just the wall-clock time ("18:15")."""
    assert present.format_doors_close(STARTS_SAME_DAY, DOORS_SAME_DAY) == "18:15"


def test_doors_close_different_day_prepends_the_date():
    """A doors-close on another day than the start carries that day, e.g.
    "Thu 9 Oct, 18:15" — the time alone would read as this day's doors."""
    out = present.format_doors_close(STARTS_PREV_DAY, DOORS_NEXT_DAY)
    assert out, "expected a doors-close string"
    assert out.endswith(", 18:15"), out
    assert "Oct" in out, out  # the day is named, not just the time


def test_doors_close_none_when_unset():
    """No doors-close time -> no string at all, not an empty string."""
    assert present.format_doors_close(STARTS_SAME_DAY, None) is None


def test_event_card_exposes_doors_close_at_and_doors_close():
    """event_card lifts the raw ISO (doorsCloseAt) and the formatted string."""
    card = present.event_card(
        {
            "slug": "my-event",
            "title": "My event",
            "venue": "EEE 403",
            "starts_at": STARTS_SAME_DAY,
            "ends_at": "2026-10-09T21:00:00+01:00",
            "doors_close_at": DOORS_SAME_DAY,
        }
    )
    assert card["doorsCloseAt"] == DOORS_SAME_DAY
    assert card["doorsClose"] == "18:15"


def test_event_card_doors_close_null_when_absent():
    """Without doors_close_at both payload fields are null, so the SPA can
    branch on them directly rather than on an absent key."""
    card = present.event_card(
        {
            "slug": "my-event",
            "title": "My event",
            "venue": "EEE 403",
            "starts_at": STARTS_SAME_DAY,
        }
    )
    assert card["doorsCloseAt"] is None
    assert card["doorsClose"] is None