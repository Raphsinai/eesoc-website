"""When a hold's pay-by deadline falls — the reference for the SQL.

The deadline itself is computed in the database (`HOLD_EXPIRES`, in
database/routes/events.py), in the same statement that creates the hold. This
is the same rule in Python, so the tests can check the two agree — across both
clock changes and the 23:00 boundary — and so anything that has to state a
deadline before the booking exists says the same thing the database will.

The rule (section 17): a hold of N days — `hold_hours` rounded up to whole
days, at least one — ends at 23:59:59 London time on the day it was made, plus
N-1 days. Made at 23:00 or later, it runs a day longer, so nobody gets a
deadline minutes away. Never later than the Union stops selling.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

# Booked at or after this London hour, the hold runs to the end of tomorrow.
LATE_HOUR = 23


def hold_days(hold_hours: int) -> int:
    """`hold_hours` as the whole days a hold lasts: rounded up, at least one."""
    return max(1, ceil(hold_hours / 24))


def hold_deadline(
    made_at: datetime, hold_hours: int, union_sales_close_at: datetime | None = None
) -> datetime:
    """The pay-by deadline, in UTC, of a hold on a ticket to be bought."""
    if made_at.tzinfo is None:
        made_at = made_at.replace(tzinfo=timezone.utc)
    local = made_at.astimezone(LONDON)
    day = local.date()
    if local.hour >= LATE_HOUR:
        day += timedelta(days=1)
    last_day = day + timedelta(days=hold_days(hold_hours) - 1)
    deadline = datetime.combine(last_day, time(23, 59, 59), tzinfo=LONDON).astimezone(timezone.utc)
    if union_sales_close_at is not None:
        close = union_sales_close_at
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        deadline = min(deadline, close.astimezone(timezone.utc))
    return deadline
