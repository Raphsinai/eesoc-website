"""Pure ICS generation tests — no test stack, no HTTP, no imports beyond icalendar.

These exercise ``backend/api/utils/ics.py`` directly (RFC 5545 escaping, line
folding, UTC stamping, and the two calendar builders). ``ics.py`` imports only
the standard library by contract, so importing it here does not pull in FastAPI
or the database client; the test therefore runs anywhere pytest runs and does
not need the compose stack.

The icalendar parsing library is pinned in CI via
``backend/api/requirements-dev.txt``; these tests skip cleanly (not error) until
both the module and the library exist, so collection passes while the
implementation is still in flight.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# The repo root is two directories up from tests/events-calendar/, and the ICS
# module lives under backend/api (a plain package — its __init__ is empty).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Importing the module mutates no state, but a missing module must not break
# collection for every test in the suite before the implementation lands.
try:
    import utils.ics as ics
except ImportError:
    pytest.skip(
        "backend/api/utils/ics.py does not exist yet",
        allow_module_level=True,
    )


def _parse(text: str):
    """Parse ICS text with icalendar, returning (calendar, list of VEVENTs).

    Kept in one helper so every test skip for a missing library names the same
    clear reason, and so the "is this actually valid RFC 5545" answer comes from
    a parser rather than from eyeballing strings.
    """
    pytest.importorskip(
        "icalendar",
        reason="icalendar is not installed; it is pinned in backend/api/requirements-dev.txt",
    )
    from icalendar import Calendar

    cal = Calendar.from_ical(text)
    events = [c for c in cal.subcomponents if c.name == "VEVENT"]
    return cal, events


def _event_dict(**overrides) -> dict:
    """A full event dict in the shape the database route returns to the ICS module."""
    payload = {
        "slug": "my-event",
        "title": "My event",
        "description": "A description.",
        "venue": "EEE 403",
        "starts_at": "2026-10-07T18:00:00+00:00",
        "ends_at": "2026-10-07T20:00:00+00:00",
        "map_url": None,
        "meeting_url": None,
        "meeting_url_private": False,
        "published": True,
        "category": "Workshops",
        "signup_url": None,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------- escaping (TEXT)


def test_escape_rfc5545_text():
    # Every character RFC 5545 TEXT marks as special must be backslash-escaped,
    # and a newline becomes the two characters backslash + "n".
    text = "a,b;c\\d\ne"
    assert ics.escape(text) == "a\\,b\\;c\\\\d\\ne"


def test_escape_leaves_plain_text_alone():
    # A string with nothing to escape is returned unchanged, so SUMMARY lines
    # do not grow stray backslashes.
    assert ics.escape("Talks and pizza") == "Talks and pizza"


# -------------------------------------------------------------------- folding


def test_fold_lines_are_within_75_octets():
    # RFC 5545 lines must not exceed 75 octets. A description far longer than
    # that must come back as more than one line.
    long_line = "x" * 300
    lines = ics.fold(long_line)
    assert len(lines) > 1
    assert all(len(line) <= 75 for line in lines)


def test_fold_unfolds_to_the_original():
    # Unfolding is "drop CRLF plus the one continuation space": applying it to
    # the folded lines must reproduce the original content byte for byte, no
    # matter where the implementation chose to break.
    long_line = "the quick brown fox " * 20
    folded = "\r\n".join(ics.fold(long_line))
    assert folded.replace("\r\n ", "") == long_line


# ----------------------------------------------------------------- utc_stamp


def test_utc_stamp_aware_utc_is_identity():
    # An already-UTC instant stamps out in the Z form with no offset shift.
    when = datetime(2026, 7, 4, 12, 30, 0, tzinfo=timezone.utc)
    assert ics.utc_stamp(when) == "20260704T123000Z"


def test_utc_stamp_converts_positive_offset_to_utc():
    # July in Britain is BST (UTC+1), so this noon-local instant is 11:00 UTC.
    # The Z-form output must reflect the conversion, not the wall clock.
    when = datetime(2026, 7, 4, 12, 30, 0, tzinfo=ZoneInfo("Europe/London"))
    assert ics.utc_stamp(when) == "20260704T113000Z"


# -------------------------------------------------------------- build_event_ics


def test_build_event_ics_start_end_summary_location_url_uid():
    # Acceptance criterion 1: a single-event .ics validates and carries the
    # documented fields. UID is "<slug>@<site host>" and URL is the event page
    # on the site.
    text = ics.build_event_ics(
        _event_dict(starts_at="2026-10-07T18:00:00+00:00",
                    ends_at="2026-10-07T20:00:00+00:00"),
        site_origin="https://eesoc.com",
    )
    cal, events = _parse(text)
    assert len(events) == 1
    vevent = events[0]
    assert vevent["dtstart"].to_ical().decode() == "20261007T180000Z"
    assert vevent["dtend"].to_ical().decode() == "20261007T200000Z"
    assert str(vevent["summary"]) == "My event"
    assert str(vevent["location"]) == "EEE 403"
    assert str(vevent["url"]) == "https://eesoc.com/events/my-event"
    assert str(vevent["uid"]) == "my-event@eesoc.com"


def test_build_event_ics_converts_positive_offset_input():
    # The database stores UTC, but the module must still normalise a +01:00
    # input to the Z form rather than emitting the offset wall-clock time.
    text = ics.build_event_ics(
        _event_dict(starts_at="2026-10-07T18:00:00+01:00", ends_at=None),
        site_origin="https://eesoc.com",
    )
    _, events = _parse(text)
    assert events[0]["dtstart"].to_ical().decode() == "20261007T170000Z"


def test_build_event_ics_omits_dtend_when_no_end():
    # An event with no ends_at has no DTEND: an open-ended event is "until you
    # leave", not an instant.
    text = ics.build_event_ics(
        _event_dict(ends_at=None),
        site_origin="https://eesoc.com",
    )
    _, events = _parse(text)
    assert events[0].get("dtend") is None


def test_build_event_ics_omits_location_when_unset():
    # LOCATION is absent, not empty, when the event has no venue.
    text = ics.build_event_ics(
        _event_dict(venue=None),
        site_origin="https://eesoc.com",
    )
    _, events = _parse(text)
    assert events[0].get("location") is None


def test_build_event_ics_escapes_title_roundtrip():
    # The tricky characters (comma, semicolon, backslash, newline) must escape
    # and parse back to exactly what was stored — the parser is the referee.
    title = "Meet, greet; plan\\back\nnewline"
    text = ics.build_event_ics(
        _event_dict(title=title),
        site_origin="https://eesoc.com",
    )
    _, events = _parse(text)
    assert str(events[0]["summary"]) == title


def test_build_event_ics_folds_long_description_roundtrip():
    # A description far past the 75-octet line limit must fold across lines yet
    # parse back intact.
    description = "word " * 60
    text = ics.build_event_ics(
        _event_dict(description=description),
        site_origin="https://eesoc.com",
    )
    _, events = _parse(text)
    assert str(events[0]["description"]) == description


def test_build_event_ics_never_contains_meeting_link():
    # The per-event calendar is for calendar clients, never for leaking where a
    # meeting is hosted — even when the event itself has one.
    meeting = "https://meet.example.com/secret-room"
    text = ics.build_event_ics(
        _event_dict(meeting_url=meeting, meeting_url_private=True),
        site_origin="https://eesoc.com",
    )
    assert meeting not in text


# ------------------------------------------------------------- build_calendar_ics


def test_build_calendar_ics_contains_exactly_the_passed_events():
    # The feed is a straight sum of the events given to it: two in, two VEVENTs out.
    event_a = _event_dict(slug="event-a", title="Event A")
    event_b = _event_dict(slug="event-b", title="Event B")
    text = ics.build_calendar_ics(
        [event_a, event_b], site_origin="https://eesoc.com", name="EESoc events"
    )
    _, events = _parse(text)
    assert len(events) == 2
    titles = {str(e["summary"]) for e in events}
    assert titles == {"Event A", "Event B"}


def test_build_calendar_ics_never_leaks_meeting_links():
    # Even when every listed event carries a meeting link, the feed must not:
    # a feed is public and shareable, so it cannot leak attendee-only URLs.
    meeting = "https://meet.example.com/confidential"
    text = ics.build_calendar_ics(
        [
            _event_dict(slug="a", title="A", meeting_url=meeting),
            _event_dict(slug="b", title="B", meeting_url="https://meet.example.com/other"),
        ],
        site_origin="https://eesoc.com",
        name="EESoc events",
    )
    assert meeting not in text
    # Assert against the join of every meeting snippet, in case the idiom above
    # changes and only one URL leaked.
    assert "meet.example.com" not in text


def test_build_calendar_ics_sets_calname():
    # X-WR-CALNAME is what calendar apps label the whole subscription.
    text = ics.build_calendar_ics(
        [_event_dict()], site_origin="https://eesoc.com", name="EESoc events"
    )
    cal, _ = _parse(text)
    assert str(cal["x-wr-calname"]) == "EESoc events"