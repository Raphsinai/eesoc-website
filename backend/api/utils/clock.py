"""When the recurring background jobs run.

Recurring jobs run once an hour, on the hour — 12:00, 13:00, 14:00 — not every
few minutes. Something that has to happen at a particular moment (an email
approved for when sales open) is not a recurring job: it sleeps until exactly
that moment instead (services/campaigns.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# A wake-up that lands a hair before the hour (timers are not exact) has done
# the hour's run; without this margin the next sleep would be milliseconds
# long and the job would run twice for the same hour.
_MARGIN = timedelta(seconds=5)


def seconds_until_next_hour(now: datetime | None = None) -> float:
    """Seconds from `now` to the next o'clock that is more than a few seconds
    away. Whole hours line up in UTC and London alike, so UTC is used."""
    now = now or datetime.now(timezone.utc)
    start = (now + _MARGIN).replace(minute=0, second=0, microsecond=0)
    return (start + timedelta(hours=1) - now).total_seconds()
