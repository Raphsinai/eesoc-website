"""Announcements as WhatsApp posts.

The site writes the post; a person sends it. What is written is the
committee's words in WhatsApp's own formatting, with an event's date, venue,
prices and link added from the database exactly as the email adds them — so a
WhatsApp reminder cannot advertise last week's room either. "Open in WhatsApp"
is a wa.me link that opens the app with the text filled in; the committee
member picks the EESoc community or group and presses send. That press is the
approval, so the rule that nothing addressed to a crowd goes without a
person's say-so holds here without a queue.

The site never posts by itself, deliberately. The two ways it could both fail
the society:

  * WhatsApp's Business API cannot post into a group or a community at all —
    only to individual numbers, each of which has opted in, as templates Meta
    has approved in advance, paid per message. EESoc holds no member's number,
    and starting to would be a new store of personal data to scrub and guard.
  * Driving WhatsApp Web from the server (whatsapp-web.js, Baileys) breaks
    WhatsApp's terms, and the ban lands on the number that runs the society's
    community — the eActivities hazard again, with no appeal.

Database-only, like the email preview: nothing here spends anything against
the Union's rate limit.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from config import settings
from services import campaigns, purchase

# The email body's own patterns, so a construct is recognised in both or in
# neither: what the committee types once must mean the same in each place.
from services.emailing import _BOLD, _HEADING, _LINK
from utils.present import format_doors_close


def _bold(text: str) -> str:
    """WhatsApp's *bold*, which only takes when the stars touch the words."""
    text = " ".join(_BOLD.sub(r"\1", text).split())
    return f"*{text}*" if text else ""


def _link(match: re.Match) -> str:
    label, href = match.group(1), match.group(2)
    if href.startswith("/"):
        href = f"{settings.site_origin}{href}"
    elif not re.match(r"^(https?://|mailto:)", href, re.I):
        # The email drops these to their label too: neither a `javascript:`
        # nor a bare word is somewhere a member can be sent.
        return label
    # WhatsApp makes a bare address tappable; "mailto:" in front is noise.
    href = re.sub(r"^mailto:", "", href, flags=re.I)
    # WhatsApp has no link text — it shows the address and makes it tappable —
    # so the label stays and the address follows it, as in the text email.
    return label if label.strip() == href.strip() else f"{label} ({href})"


def _inline(text: str) -> str:
    return _BOLD.sub(r"*\1*", _LINK.sub(_link, text))


def from_markdown(markdown: str) -> str:
    """The committee's markdown as WhatsApp formats it.

    The same blocks as `emailing.render_body`: a heading becomes a bold line,
    a block of bullets stays a block of bullets — WhatsApp draws "- " as a
    list, and "* " would read as the start of something bold — and a single
    newline inside a paragraph stays a line break.
    """
    out: list[str] = []
    for block in re.split(r"\n{2,}", (markdown or "").strip()):
        block = block.strip()
        if not block:
            continue

        heading = _HEADING.match(block)
        if heading and "\n" not in block:
            out.append(_bold(_LINK.sub(_link, heading.group(2))))
            continue

        lines = [line.strip() for line in block.split("\n")]
        if all(line.startswith(("- ", "* ")) for line in lines):
            out.append("\n".join(f"- {_inline(line[2:])}" for line in lines))
            continue

        out.append("\n".join(_inline(line) for line in lines))
    return "\n\n".join(out)


async def message(
    heading: str, body: str, event: dict | None = None, kind: str | None = None
) -> str:
    """The whole post: a bold first line, the committee's words, and — for an
    event — its details and link, from the same helpers the email uses.

    `kind` is a campaign kind; only "on_sale" changes anything: the standard
    opening when none is written, and the Union shop link, as in that email.
    """
    heading = " ".join((heading or "").split())
    parts = [_bold(heading)] if heading else []
    opening = from_markdown(body)

    if event is None:
        return "\n\n".join(parts + ([opening] if opening else []))

    title = event["title"]
    if not opening and kind == "on_sale":
        opening = f"Tickets for *{title}* are on sale now."
    if opening:
        parts.append(opening)

    details: list[str] = []
    # Named once: a title already in the heading or the words is not repeated
    # above the details, which on a phone is a line of scrolling for nothing.
    if title.casefold() not in f"{heading} {opening}".casefold():
        details.append(_bold(title))
    details.append(f"*When:* {campaigns.event_when(event)}")
    if event.get("venue"):
        details.append(f"*Where:* {event['venue']}")
    doors = format_doors_close(event.get("starts_at"), event.get("doors_close_at"))
    if doors:
        details.append(f"*Doors close:* {doors}")
    tickets = campaigns.event_tickets(event)
    if len(tickets) == 1:
        details.append(f"*Tickets:* {tickets[0][0]} — {tickets[0][1]}")
    elif tickets:
        details.append("*Tickets:*\n" + "\n".join(f"- {n} — {p}" for n, p in tickets))
    parts.append("\n".join(details))

    url = f"{settings.site_origin}/events/{event['slug']}"
    if kind == "on_sale":
        links = [f"Reserve your place: {url}"]
        shop = await purchase.link_for(event)
        if shop:
            links.append(f"Buy on the Union shop: {shop}")
        parts.append("\n".join(links))
    else:
        parts.append(f"Full details and booking: {url}")
    return "\n\n".join(parts)


def warning(event: dict | None, kind: str | None = None) -> str | None:
    """Why this should not be posted yet, if anything.

    Shown, not enforced, as for a queued email — but it matters more here:
    a message in a group of hundreds cannot be quietly corrected afterwards.
    """
    if event is None:
        return None
    if not event.get("published"):
        return (
            "This event isn't published, so the link in the post won't open "
            "for anybody else yet."
        )
    now = campaigns.utcnow()
    starts = campaigns.parse_time(event.get("starts_at"))
    if starts is not None and starts <= now:
        return "This event has already started."
    opens = campaigns.parse_time(event.get("sales_open_at"))
    if kind == "on_sale" and opens is not None and opens > now:
        return (
            f"Sales don't open until {campaigns.when_text(opens)}. Post this then "
            "— before that, it says tickets are on sale when they aren't."
        )
    return None


def share_url(text: str) -> str:
    """wa.me with no number: WhatsApp opens with the text filled in and asks
    which chat it is for — the app on a phone, WhatsApp Desktop or Web on a
    laptop."""
    return f"https://wa.me/?text={quote(text, safe='')}"
