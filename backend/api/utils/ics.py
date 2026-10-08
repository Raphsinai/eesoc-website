"""RFC 5545 (iCalendar) output — stdlib only, on purpose.

No fastapi, database or settings imports here: the module is pure text
assembly, and keeping it standalone means the tests can exercise escaping,
folding and UTC stamping without standing up the stack. The callers pass
`site_origin` in, which is where the deployed origin lives.

Event times are stored as absolute UTC instants (see migration 031's comment:
times stayed TIMESTAMPTZ in UTC, with London applied only for display), so
DTSTART and DTEND are simply those instants in Z form. A "Z" time is already
an absolute time — a VTIMEZONE component exists to define the rules for
interpreting a *local* time, so none is needed or wanted here.

Neither output ever includes the event's meeting link or anything about
attendees: calendar files are fetched by clients that cannot authenticate, so
what goes into them is exactly what is already public on the event page.
"""

from datetime import datetime, timezone
from urllib.parse import urlparse

PRODID = "-//EESoc//Events//EN"

# RFC 5545 lines are octet-counted, and a continuation line's leading space
# counts, so the first line carries 75 octets and every following one 74.
LINE_LIMIT = 75


def escape(value) -> str:
    """Escape a value for an RFC 5545 TEXT property.

    Backslash first — escaping anything else would double the escapes already
    present. Newlines become a literal "\\n" (the sequence, not a real break),
    which is how a DESCRIPTION survives a round trip through a parser.
    """
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> list[str]:
    """Split one logical line at 75 octets into physical lines.

    Continuation lines begin with a single space, per RFC 5545 3.1. Octets, not
    characters, because the limit is a wire-format one — and the split steps
    back over UTF-8 continuation bytes (0b10xxxxxx) so a multi-byte character
    is never cut in half.
    """
    raw = line.encode("utf-8")
    if len(raw) <= LINE_LIMIT:
        return [line]
    parts: list[str] = []
    start, first = 0, True
    while start < len(raw):
        limit = LINE_LIMIT if first else LINE_LIMIT - 1
        end = min(start + limit, len(raw))
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        parts.append(("" if first else " ") + raw[start:end].decode("utf-8"))
        start = end
        first = False
    return parts


def utc_stamp(value) -> str:
    """An RFC 5545 UTC timestamp: YYYYMMDDTHHMMSSZ.

    Accepts a datetime or an ISO string (what the database service returns).
    A naive value is taken as UTC — that is how the app treats every naive
    time it produces, and guessing a local timezone would put the event at the
    wrong instant for half the year.
    """
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _host(site_origin: str) -> str:
    """The host part of the site origin, for building stable UIDs.

    A UID has to be globally unique and stable; `<slug>@<our host>` is both,
    and it self-documents which system minted it.
    """
    return urlparse(site_origin).hostname or site_origin


def _vevent_lines(event: dict, site_origin: str) -> list[str]:
    """The VEVENT for one event, as unfolded logical lines.

    DTEND is omitted when the event has no end rather than invented: RFC 5545
    allows a start-only event, and a fabricated end would put a wrong time on
    somebody's calendar. The meeting link is deliberately absent (see the
    module docstring) even when the event has one.
    """
    lines = ["BEGIN:VEVENT"]
    lines.append(f"UID:{event['slug']}@{_host(site_origin)}")
    lines.append(f"DTSTAMP:{utc_stamp(datetime.now(timezone.utc))}")
    lines.append(f"DTSTART:{utc_stamp(event['starts_at'])}")
    if event.get("ends_at"):
        lines.append(f"DTEND:{utc_stamp(event['ends_at'])}")
    lines.append(f"SUMMARY:{escape(event.get('title'))}")
    if event.get("venue"):
        lines.append(f"LOCATION:{escape(event['venue'])}")
    lines.append(f"URL:{site_origin}/events/{event['slug']}")
    if event.get("description"):
        lines.append(f"DESCRIPTION:{escape(event['description'])}")
    lines.append("END:VEVENT")
    return lines


def _calendar(body: list[str], name: str | None) -> str:
    """Wrap VEVENT lines in a VCALENDAR, fold every line, join with CRLF.

    RFC 5545 lines end with CRLF — not the platform newline — and the file
    ends with one too, which is what the stricter parsers insist on.
    """
    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
    ]
    if name:
        head.append(f"X-WR-CALNAME:{escape(name)}")
    physical: list[str] = []
    for line in head + body + ["END:VCALENDAR"]:
        physical.extend(fold(line))
    return "\r\n".join(physical) + "\r\n"


def build_event_ics(event: dict, site_origin: str) -> str:
    """A complete one-event calendar document: VCALENDAR + that VEVENT.

    A calendar file the parsers accept needs the VCALENDAR wrapper even for a
    single event, so this (not a bare VEVENT) is what the download endpoint
    returns.
    """
    return _calendar(_vevent_lines(event, site_origin), None)


def build_calendar_ics(events: list[dict], site_origin: str, name: str) -> str:
    """A multi-event calendar document, ordered as given (the caller sorts)."""
    body: list[str] = []
    for event in events:
        body.extend(_vevent_lines(event, site_origin))
    return _calendar(body, name)
