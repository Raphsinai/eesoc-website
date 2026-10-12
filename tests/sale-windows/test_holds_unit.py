"""The end-of-day hold deadline rule (section 17, acceptance criterion 1).

Unit tests of `api/utils/holds.py`, the Python reference for the SQL in
database/routes/events.py (`HOLD_EXPIRES`); the stack tests check the two agree
on real bookings. A hold of N days ends 23:59:59 London time on the day it was
made plus N-1, a day later if made at 23:00 or after, and never later than the
Union stops selling — correct across both clock changes.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from utils.holds import hold_days, hold_deadline  # noqa: E402

LONDON = ZoneInfo("Europe/London")


def london(*args) -> datetime:
    return datetime(*args, tzinfo=LONDON)


def end_of(year, month, day) -> datetime:
    return london(year, month, day, 23, 59, 59).astimezone(timezone.utc)


@pytest.mark.parametrize("made, hours, ends", [
    # One day, booked at noon: the end of that same day.
    (london(2026, 10, 14, 12, 0), 24, end_of(2026, 10, 14)),
    # Just before the 23:00 cut-off: still the same day.
    (london(2026, 10, 14, 22, 59, 59), 24, end_of(2026, 10, 14)),
    # At 23:00 or later: the end of the next day.
    (london(2026, 10, 14, 23, 0, 0), 24, end_of(2026, 10, 15)),
    (london(2026, 10, 14, 23, 45), 24, end_of(2026, 10, 15)),
    # Two days: one more in each case.
    (london(2026, 10, 14, 12, 0), 48, end_of(2026, 10, 15)),
    (london(2026, 10, 14, 22, 59, 59), 48, end_of(2026, 10, 15)),
    (london(2026, 10, 14, 23, 0, 0), 48, end_of(2026, 10, 16)),
    # Just after midnight is a new day, not the end of the previous one.
    (london(2026, 10, 15, 0, 5), 24, end_of(2026, 10, 15)),
])
def test_a_hold_ends_at_the_end_of_a_london_day(made, hours, ends):
    assert hold_deadline(made, hours) == ends


def test_across_the_clocks_going_back():
    """BST → GMT on Sunday 25 October 2026. Booked the day before, a two-day
    hold ends at 23:59:59 GMT that Sunday — 23:59:59Z, not 22:59:59Z."""
    deadline = hold_deadline(london(2026, 10, 24, 12, 0), 48)
    assert deadline == datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc)
    # Booked on the change day itself, after the clocks went back.
    assert hold_deadline(london(2026, 10, 25, 10, 0), 24) == datetime(
        2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc
    )


def test_across_the_clocks_going_forward():
    """GMT → BST on Sunday 29 March 2026: that day ends at 23:59:59 BST,
    which is 22:59:59Z."""
    assert hold_deadline(london(2026, 3, 29, 9, 0), 24) == datetime(
        2026, 3, 29, 22, 59, 59, tzinfo=timezone.utc
    )
    # Booked the night before at 23:30 GMT: the end of the next day, in BST.
    assert hold_deadline(london(2026, 3, 28, 23, 30), 24) == datetime(
        2026, 3, 29, 22, 59, 59, tzinfo=timezone.utc
    )


def test_never_later_than_the_union_stops_selling():
    made = london(2026, 10, 14, 12, 0)
    close = london(2026, 10, 14, 16, 0)
    assert hold_deadline(made, 24, close) == close.astimezone(timezone.utc)
    # A close after the deadline changes nothing.
    assert hold_deadline(made, 24, close + timedelta(days=3)) == end_of(2026, 10, 14)


def test_a_utc_booking_time_is_read_as_london():
    """The database stores UTC: 22:30Z in October is 23:30 BST, after the cut-off."""
    made = datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)
    assert hold_deadline(made, 24) == end_of(2026, 10, 15)


@pytest.mark.parametrize("hours, days", [(1, 1), (24, 1), (25, 2), (48, 2), (72, 3), (0, 1)])
def test_hold_hours_round_up_to_whole_days(hours, days):
    assert hold_days(hours) == days
