"""Shared helpers for the ICU bridge."""

from datetime import datetime, timezone


def academic_year(when: datetime | None = None) -> str:
    """The eActivities academic-year string, e.g. '26-27'.

    ICU's year runs 1 August to 31 July — visible in the committee feed, where
    terms are dated 2026-08-01 to 2027-07-31. So August onwards belongs to the
    year that is just starting.
    """
    now = when or datetime.now(timezone.utc)
    year = now.year % 100
    if now.month >= 8:
        return f"{year:02d}-{(year + 1) % 100:02d}"
    return f"{(year - 1) % 100:02d}-{year:02d}"


def parse_dt(value: str | None) -> datetime | None:
    """eActivities sends 'YYYY-MM-DD HH:MM:SS' with no timezone. It is UK local
    time; we store UTC-aware timestamps, so attach a timezone rather than letting
    a naive value through and comparing it against NOW() later."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
