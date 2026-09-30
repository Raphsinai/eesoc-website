"""Academic year, in eActivities' 'xx-xx' form.

Duplicated from the union service rather than shared, because the two are
separate deployables. ICU's year runs 1 August to 31 July — visible in the
committee feed, whose terms run 2026-08-01 to 2027-07-31.
"""

from datetime import datetime, timezone


def academic_year(when: datetime | None = None) -> str:
    now = when or datetime.now(timezone.utc)
    year = now.year % 100
    if now.month >= 8:
        return f"{year:02d}-{(year + 1) % 100:02d}"
    return f"{(year - 1) % 100:02d}-{year:02d}"
