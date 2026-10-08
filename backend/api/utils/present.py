"""Display formatting.

API.md specifies pre-formatted date/time strings ("Thu 22 Oct", "12:00 – 17:00")
because that is what the mockup renders. ISO timestamps are sent alongside them
so the client can regroup or re-localise without re-parsing the display text.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Every event is in London. Times are stored in UTC, so anything shown to a
# person goes through here first — formatting the UTC value directly showed
# every time an hour early during BST.
LONDON = ZoneInfo("Europe/London")


def london(when: datetime) -> datetime:
    """The same instant on a London clock. Naive values are taken as UTC."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(LONDON)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_date(when: datetime | None) -> str:
    if when is None:
        return ""
    when = london(when)
    return f"{DAYS[when.weekday()]} {when.day:02d} {MONTHS[when.month - 1]}"


def format_time_range(start: datetime | None, end: datetime | None) -> str:
    if start is None:
        return ""
    start = london(start)
    end = london(end) if end is not None else None
    if end is None:
        return start.strftime("%H:%M")
    # En dash with spaces, matching the mockup's "12:00 – 17:00".
    return f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}"


def format_doors_close(starts, doors) -> str | None:
    """The doors-close time as one short string, or None when there is none.

    "18:15" when it falls on the same London day as the start — the day is
    already printed beside it — and "Thu 9 Oct, 18:15" when it does not, so a
    doors-close on a different day can never be mistaken for one on this one.
    Accepts ISO strings as well as datetimes: every event dict here arrives
    from the database service as JSON.
    """
    doors_dt = _parse(doors)
    if doors_dt is None:
        return None
    when = london(doors_dt)
    if starts is None or london(_parse(starts)).date() == when.date():
        return when.strftime("%H:%M")
    return f"{format_date(doors_dt)}, {when.strftime('%H:%M')}"


def format_price(pence) -> str:
    """Pence to a display string.

    Coerces first: Postgres returns SUM() over BIGINT as NUMERIC, which asyncpg
    maps to Decimal and JSON serialises as a *string*. Taking the argument on
    trust means `pence % 100` silently becomes string formatting and raises.
    """
    try:
        pence = int(float(pence or 0))
    except (TypeError, ValueError):
        return "—"
    if pence == 0:
        return "Free"
    if pence % 100 == 0:
        return f"£{pence // 100}"
    return f"£{pence / 100:.2f}"


def sales_state(event: dict) -> tuple[str, str | None]:
    """When this event's tickets open: ("open" | "scheduled" | "soon", iso).

    Three states, not two. NULL is not "on sale" — it means the committee has
    not decided yet, and the honest thing to show is "On sale soon" rather than
    a booking form or a date we do not have.
    """
    opens = _parse(event.get("sales_open_at"))
    if opens is None:
        return "soon", None
    if opens.tzinfo is None:
        opens = opens.replace(tzinfo=timezone.utc)
    if opens <= datetime.now(timezone.utc):
        return "open", opens.isoformat()
    return "scheduled", opens.isoformat()


def sales_open(event: dict) -> bool:
    return sales_state(event)[0] == "open"


def event_status(event: dict) -> tuple[str, str]:
    """The badge on an event card: (label, tone).

    Tones are the design system's: success | accent | neutral | brand | danger.
    """
    types = event.get("ticket_types") or []
    if not event.get("published"):
        return "Draft", "neutral"
    if event.get("sold_out"):
        return "Sold out", "danger"

    # No ticket types at all = nothing to book. A drop-in event is not
    # "on sale soon", it is simply free to turn up to, and saying otherwise
    # makes people wait for tickets that are never coming.
    # Sign-ups happen on somebody else's form, so our ticket state says
    # nothing about whether there is room.
    if event.get("signup_url"):
        return "Sign up", "accent"
    if not types:
        return "Free · just turn up", "success"

    state, opens = sales_state(event)
    if state == "soon":
        return "On sale soon", "neutral"
    if state == "scheduled":
        return f"On sale {format_date(_parse(opens))}", "accent"

    remaining = event.get("remaining")
    if remaining is None:
        remaining = sum(
            (t["remaining"] for t in types if t.get("remaining") is not None),
            0,
        ) or None

    if types and all(t.get("members_only") for t in types):
        return "Members only", "brand"
    if types and all(t.get("price_pence", 0) == 0 for t in types):
        return "Free entry", "success"
    if remaining is not None and remaining <= 10:
        return f"{remaining} places", "accent"
    if remaining is not None:
        return f"{remaining} places", "accent"
    return "Booking open", "success"


def event_card(event: dict) -> dict:
    """Flatten an event into exactly the props EventCard.vue takes."""
    starts = _parse(event.get("starts_at"))
    ends = _parse(event.get("ends_at"))
    status, tone = event_status(event)
    return {
        "id": event.get("id"),
        "slug": event.get("slug"),
        "category": event.get("category") or "Event",
        "title": event.get("title"),
        "date": format_date(starts),
        "time": format_time_range(starts, ends),
        "venue": event.get("venue") or "",
        "status": status,
        "statusTone": tone,
        "imageUrl": event.get("image_url"),
        "signupUrl": event.get("signup_url"),
        "startsAt": event.get("starts_at"),
        "endsAt": event.get("ends_at"),
        # The doors-close cutoff, when the event has one, both raw (so a
        # client can re-localise) and ready-formatted.
        "doorsCloseAt": event.get("doors_close_at"),
        "doorsClose": format_doors_close(starts, event.get("doors_close_at")),
        "soldOut": bool(event.get("sold_out")),
        "published": bool(event.get("published")),
        "salesState": sales_state(event)[0],
        "salesOpenAt": sales_state(event)[1],
    }


def _parse(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
