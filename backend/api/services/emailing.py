"""Who hears from us, about what, and how a bulk send actually runs.

Three things live here:

  * the topic registry — the fixed vocabulary of things somebody can subscribe
    to, with the default for an account that has never expressed a preference;
  * audience resolution — turning the keys an admin ticked into a de-duplicated
    list of addresses;
  * the sender — markdown to the site's own typography, then one message per
    batch with the recipients BCC'd, recorded as it goes.

Everything bulk is BCC'd in batches rather than sent per recipient: one message
to four hundred people goes out in seconds, where one each took a quarter of an
hour under Exchange's rate limit. The cost is the per-person unsubscribe link —
a shared message can only carry a shared one, so it points at /unsubscribe,
which emails anybody their own signed link on request. Mail that is about one
person (a sign-in code, a booking, a per-event alert they set) is still sent to
them alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from urllib.parse import quote, urlencode

from clients import database
from config import settings
from utils import email as mailer
from utils.year import academic_year

log = logging.getLogger("api.emailing")


# ------------------------------------------------------------------- topics
#
# `default` is what an account with no row for this topic counts as. Opt-in is
# the honest default for anything promotional; the two exceptions are the
# general newsletter and event announcements, which are the reason somebody
# joined a society in the first place — and both carry a one-click unsubscribe
# in every message.

TOPICS: list[dict] = [
    {
        "key": "newsletter",
        "label": "EESoc newsletter",
        "description": "What the society is up to, every few weeks.",
        "default": True,
    },
    {
        "key": "events_new",
        "label": "New events",
        "description": "When we announce something new in the calendar.",
        "default": True,
    },
    {
        "key": "tickets_on_sale",
        "label": "Tickets going on sale",
        "description": (
            "When tickets open for an event. You can also ask about one event "
            "at a time from its own page."
        ),
        "default": True,
    },
    {
        "key": "roles",
        "label": "Jobs and internships",
        "description": "Openings posted by the companies that sponsor us.",
        "default": False,
    },
    {
        "key": "resources",
        "label": "Resources",
        "description": "New past papers, notes and module feedback.",
        "default": False,
    },
    {
        "key": "lockers",
        "label": "Lockers",
        "description": "When locker booking opens for the year.",
        "default": False,
    },
    {
        "key": "committee",
        "label": "Committee and elections",
        "description": "Committee openings, by-elections and how to stand.",
        "default": False,
    },
]

TOPIC_KEYS = {t["key"] for t in TOPICS}
DEFAULTS = {t["key"]: t["default"] for t in TOPICS}

# Per-category event topics are derived from the events table rather than
# listed here: a category exists because an event uses it, and hard-coding the
# list would mean a new category silently has no subscribers.
CATEGORY_PREFIX = "events_category:"


def category_topic(category: str) -> str:
    return f"{CATEGORY_PREFIX}{category.strip().lower()}"


async def topics() -> list[dict]:
    """The full subscribable set: the fixed topics, then one per event category."""
    out = [dict(t) for t in TOPICS]
    categories = await database.get("/email/event-categories") or []
    for category in categories:
        out.append(
            {
                "key": category_topic(category),
                "label": f"{category} events",
                "description": f"Announcements for {category.lower()} events only.",
                "default": False,
                "category": category,
            }
        )
    return out


def default_for(topic: str) -> bool:
    return DEFAULTS.get(topic, False)


# ------------------------------------------------------- the departmental lists
#
# The year-group aliases the department maintains. These are single addresses
# with a whole cohort behind them, so they are NOT a subscriber list and nothing
# here can unsubscribe them — the department owns them. They are offered
# separately in the admin screen for exactly that reason.
#
# Sending to one of these usually requires the sending address to be permitted
# to post. If a send to a list bounces, that is the first thing to check.

DEPARTMENT_LISTS: list[dict] = [
    # Every EEE and EIE undergraduate, all years. Ticking this alongside a
    # year-group list sends that year the message twice — the department
    # expands both aliases, and we only see the two addresses.
    {"key": "dept_ugs_all", "label": "All undergraduates", "email": "ee_ugs_all@ic.ac.uk"},
    {"key": "dept_ee1", "label": "EEE/EIE Year 1", "email": "ee1ugs@ic.ac.uk"},
    {"key": "dept_ee2",  "label": "EEE Year 2",     "email": "ee2ugs@ic.ac.uk"},
    {"key": "dept_eie2", "label": "EIE Year 2",     "email": "eie2ugs@ic.ac.uk"},
    {"key": "dept_ee3",  "label": "EEE Year 3",     "email": "ee3ugs@ic.ac.uk"},
    {"key": "dept_eie3", "label": "EIE Year 3",     "email": "eie3ugs@ic.ac.uk"},
    {"key": "dept_ee4",  "label": "EEE Year 4",     "email": "ee4ugs@ic.ac.uk"},
    {"key": "dept_eie4", "label": "EIE Year 4",     "email": "eie4ugs@ic.ac.uk"},
    {"key": "dept_msc",  "label": "MSc",            "email": "ee_msc@ic.ac.uk"},
    {"key": "dept_phd",  "label": "PhD",            "email": "ee_phd@ic.ac.uk"},
]

DEPARTMENT_BY_KEY = {d["key"]: d for d in DEPARTMENT_LISTS}


# -------------------------------------------------------------- audiences


async def audiences() -> list[dict]:
    """Everything an admin can tick, with a live count against each.

    Counted by resolving them for real rather than with a cheaper COUNT(*): the
    numbers shown must be the numbers sent to, including the opt-out filtering,
    or the preview is a lie at exactly the moment somebody is deciding whether
    to press send.
    """
    groups: list[dict] = []

    test = await test_recipients()
    groups.append({
        "key": "test",
        "label": "Test list",
        "group": "Testing",
        "description": ", ".join(test) if test else "Empty — add addresses below.",
        "count": len(test),
    })

    people = [
        {"key": "members", "label": "EESoc members", "group": "People",
         "description": f"Everyone ICU lists as a member for {academic_year()}."},
        {"key": "accounts", "label": "Everyone with an account", "group": "People",
         "description": "Anybody who has signed in to eesoc.com."},
    ]
    for topic in await topics():
        people.append({
            "key": f"topic:{topic['key']}",
            "label": f"Subscribed: {topic['label']}",
            "group": "Subscriptions",
            "description": topic["description"],
        })

    for entry in DEPARTMENT_LISTS:
        groups.append({
            "key": entry["key"],
            "label": entry["label"],
            "group": "Department lists",
            "description": entry["email"],
            # A list is one address, however many people read it, and we cannot
            # know the second number.
            "count": 1,
            "list": True,
        })

    for item in people:
        try:
            item["count"] = len(await resolve([item["key"]]))
        except Exception:  # noqa: BLE001
            # A count is not worth failing the screen over.
            log.exception("Could not count audience %s", item["key"])
            item["count"] = None
        groups.append(item)

    return groups


async def resolve(keys: list[str]) -> list[dict]:
    """Audience keys to a de-duplicated recipient list.

    De-duplication is on the lowercased address and happens across all keys
    together, so somebody who is both a member and subscribed to the newsletter
    gets one email rather than two. First key wins, which is why `members` is
    listed first — it carries the better name.
    """
    seen: dict[str, dict] = {}

    for key in keys:
        for recipient in await _resolve_one(key):
            address = (recipient.get("email") or "").strip().lower()
            if not address or "@" not in address:
                continue
            seen.setdefault(address, {**recipient, "email": address})

    return list(seen.values())


async def _resolve_one(key: str) -> list[dict]:
    if key in DEPARTMENT_BY_KEY:
        entry = DEPARTMENT_BY_KEY[key]
        # Not run through the opt-out filter: these are the department's lists,
        # not ours, and an individual unsubscribing from EESoc mail cannot
        # remove a cohort alias.
        return [{"email": entry["email"], "source": key, "list": True,
                 "first_name": None, "last_name": None, "user_id": None}]

    if key == "test":
        # Not opt-out filtered: committee members who asked to see the drafts.
        return [
            {"email": e, "source": "test", "first_name": None, "last_name": None,
             "user_id": None}
            for e in await test_recipients()
        ]

    if key == "members":
        return await database.get(
            "/email/audience",
            {"kind": "members", "academic_year": academic_year()},
        ) or []

    if key == "accounts":
        return await database.get("/email/audience", {"kind": "accounts"}) or []

    if key.startswith("topic:"):
        topic = key.split(":", 1)[1]
        return await database.get(
            "/email/audience",
            {"kind": "topic", "topic": topic, "default_on": default_for(topic)},
        ) or []

    if key.split(":", 1)[0] in EVENT_AUDIENCES:
        kind, _, raw_id = key.partition(":")
        return await _event_people(kind, int(raw_id))

    if key.startswith("alerts:"):
        return await database.get(
            "/email/audience",
            {"kind": "alerts", "event_id": int(key.split(":", 1)[1])},
        ) or []

    log.warning("Unknown audience key %r — ignored", key)
    return []


# --------------------------------------------------------- one event's people
#
# The people attached to one event, by where their booking stands. Not opt-out
# filtered: this is mail about something the person booked — the same footing
# as the pay-up reminder — not a newsletter they can leave.

EVENT_AUDIENCES = {
    "unpaid": ("Hasn't bought yet", "Holding a ticket that still needs buying on the Union shop."),
    "confirmed": ("Has bought", "Purchase matched — these people have a place."),
    "booked": ("Everyone booked", "Held or bought: everyone with a live booking."),
    "waitlist": ("Waitlist", "Waiting for a place to free up."),
}


async def _event_people(kind: str, event_id: int) -> list[dict]:
    if kind == "waitlist":
        rows = await database.get("/waitlist", {"event_id": event_id}) or []
        return [
            {"email": r.get("email"), "source": f"waitlist:{event_id}",
             "first_name": None, "last_name": None, "user_id": r.get("user_id")}
            for r in rows
            if not r.get("claimed_at")
        ]
    rows = await database.get(
        "/reservations", {"event_id": event_id, "limit": 5000}
    ) or []
    def wanted(r: dict) -> bool:
        if kind == "unpaid":
            return r["status"] == "held" and r.get("requires_purchase", True)
        if kind == "confirmed":
            return r["status"] == "confirmed"
        return r["status"] in ("held", "confirmed")
    return [
        {"email": r.get("user_email"), "source": f"{kind}:{event_id}",
         "first_name": r.get("user_first_name"), "last_name": r.get("user_last_name"),
         "user_id": r.get("user_id")}
        for r in rows
        if wanted(r)
    ]


async def event_audiences(event_id: int) -> list[dict]:
    """The "This event" group for the per-event email dialog, with counts."""
    out = []
    for kind, (label, description) in EVENT_AUDIENCES.items():
        key = f"{kind}:{event_id}"
        out.append({
            "key": key, "label": label, "group": "This event",
            "description": description, "count": len(await resolve([key])),
        })
    return out


async def test_recipients() -> list[str]:
    """The test list's addresses.

    Staging's copy of the database has every real address scrubbed out, so its
    list arrives empty on every refresh. There it is refilled from the full
    addresses in MAIL_ALLOWLIST — the people staging may mail anyway.
    """
    rows = await database.get("/email/test-recipients") or []
    if not rows and settings.environment == "staging":
        for address in settings.mail_allowlist.split(","):
            address = address.strip().lower()
            if "@" in address and not address.startswith("@"):
                await database.post("/email/test-recipients", {"email": address})
        rows = await database.get("/email/test-recipients") or []
    return [r["email"] for r in rows]


# ------------------------------------------------------- unsubscribe links
#
# Signed rather than stored, so a link works for the majority of recipients who
# have no account — and for a departmental alias, where there is no person to
# key a row against.


def _secret() -> str:
    if settings.email_token_secret:
        return settings.email_token_secret
    # The SMTP password is a stable secret this process must already hold: with
    # no mail credential there is no mail, so there are no links to sign. It
    # does mean links stop working after the annual password reset, which is
    # why EMAIL_TOKEN_SECRET exists.
    return settings.smtp_pass or "eesoc-unsigned-development-only"


def sign(address: str) -> str:
    """A short tag proving this address was given a link by us."""
    digest = hmac.new(
        _secret().encode(), address.strip().lower().encode(), hashlib.sha256
    )
    return digest.hexdigest()[:32]


def verify(address: str, token: str) -> bool:
    return hmac.compare_digest(sign(address), (token or "").strip())


def unsubscribe_url(address: str, topic: str | None = None) -> str:
    """The link that goes in the footer and the List-Unsubscribe header.

    Points at the SPA, which shows what is about to happen and then calls the
    API — except for the one-click POST, which mail clients send straight to the
    API path with the same query.
    """
    params = {"e": address.strip().lower(), "t": sign(address)}
    if topic:
        params["topic"] = topic
    return f"{settings.site_origin}/unsubscribe?{urlencode(params)}"


def general_unsubscribe_url() -> str:
    """The unsubscribe link for a message BCC'd to many people.

    Cannot name an address, so it lands on the page that asks for one and emails
    back that address's own signed link. No one-click: see `_bulk_headers`.
    """
    return f"{settings.site_origin}/unsubscribe"


def subscribe_token(address: str) -> str:
    """Proves a newsletter sign-up link was sent to this address.

    Signed over a prefixed value, so an unsubscribe token — which is in every
    bulk email and gets forwarded — can never be replayed to subscribe someone.
    """
    return sign("subscribe:" + address.strip().lower())


def verify_subscribe(address: str, token: str) -> bool:
    return hmac.compare_digest(subscribe_token(address), (token or "").strip())


def subscribe_confirm_url(address: str) -> str:
    params = {"e": address.strip().lower(), "t": subscribe_token(address)}
    return f"{settings.site_origin}/subscribe?{urlencode(params)}"


def alert_cancel_url(token: str) -> str:
    """Stop the per-event "tell me when this is on sale" alert."""
    return f"{settings.site_origin}/unsubscribe?alert={quote(token)}"


# ------------------------------------------------------------- body rendering
#
# The same renderer feeds the admin preview and the delivered message, so what
# the committee approves is what arrives.

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")


def _inline(text: str) -> str:
    """Escape first, then re-introduce only the constructs we recognise.

    That ordering is the whole security model: committee-authored copy goes into
    an HTML email, and an unescaped `<` would let a stray angle bracket swallow
    the rest of the message — or worse, be deliberate.
    """
    out = mailer.escape(text)
    out = _BOLD.sub(r"<strong>\1</strong>", out)

    def link(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        # http(s) and mailto only. A `javascript:` href does nothing in a mail
        # client, but it does in the admin preview, which renders in a browser.
        if not re.match(r"^(https?://|mailto:|/)", href, re.I):
            return label
        if href.startswith("/"):
            href = f"{settings.site_origin}{href}"
        return f'<a href="{href}" style="color:{mailer.BLUE};">{label}</a>'

    return _LINK.sub(link, out)


def _plain(text: str) -> str:
    """The same constructs, for the text/plain part.

    Not the raw markdown: `**bold**` reads as noise, and `[the listing](/events)`
    is worse than useless — a relative URL in a plain-text email cannot be
    clicked or copied anywhere. Links become "label (absolute url)", which is
    the convention every plain-text newsletter settled on for a reason.
    """
    def link(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        if href.startswith("/"):
            href = f"{settings.site_origin}{href}"
        # A link whose text already is the URL does not need it twice.
        return label if label.strip() == href.strip() else f"{label} ({href})"

    out = _LINK.sub(link, text)
    return _BOLD.sub(r"\1", out)


def render_body(markdown: str) -> tuple[str, str]:
    """(html, text) for a committee-written body.

    Supports headings, paragraphs, bullet lists and `[label](url)` links —
    the same subset the resource pages use, for the same reason: a full
    markdown library is 50 KB for four constructs, and an email can only render
    a fraction of what one would emit.
    """
    html_parts: list[str] = []
    text_parts: list[str] = []

    for block in re.split(r"\n{2,}", (markdown or "").strip()):
        block = block.strip()
        if not block:
            continue

        heading = _HEADING.match(block)
        if heading and "\n" not in block:
            size = {1: 19, 2: 17, 3: 15}[len(heading.group(1))]
            html_parts.append(
                f'<h2 style="margin:22px 0 10px;font-family:{mailer.SANS};'
                f'font-size:{size}px;line-height:1.3;color:{mailer.INK};">'
                f"{_inline(heading.group(2))}</h2>"
            )
            text_parts.append(_plain(heading.group(2)))
            continue

        lines = block.split("\n")
        if all(line.strip().startswith(("- ", "* ")) for line in lines):
            items = "".join(
                f'<li style="margin:0 0 6px;">{_inline(line.strip()[2:])}</li>'
                for line in lines
            )
            html_parts.append(
                f'<ul style="margin:0 0 14px;padding-left:20px;'
                f'font-family:{mailer.SANS};font-size:15px;line-height:1.6;'
                f'color:{mailer.BODY_TEXT};">{items}</ul>'
            )
            text_parts.extend(f"  • {_plain(line.strip()[2:])}" for line in lines)
            continue

        # A single newline inside a paragraph is a line break, not a new
        # paragraph — which is what somebody typing an address expects.
        html_parts.append(
            f'<p style="margin:0 0 14px;font-family:{mailer.SANS};font-size:15px;'
            f'line-height:1.6;color:{mailer.BODY_TEXT};">'
            + "<br>".join(_inline(line) for line in lines)
            + "</p>"
        )
        text_parts.append(_plain(block))

    return "".join(html_parts), "\n\n".join(text_parts)


# ---------------------------------------------------------------- sending


async def send_bulk(campaign_id: int, recipients: list[dict], build) -> dict:
    """Send to everybody in batches, recording progress as it goes.

    `build(batch, unsubscribe)` sends one batch of recipient rows and returns
    the addresses it reached. Normally that is one message with the whole batch
    BCC'd; a caller with something personal for a few of them (the on-sale
    email's per-event alerts) can send those separately inside the same call.
    This function knows nothing about what is being sent — a newsletter and an
    event announcement share all of this.

    Counts stay per recipient, not per message, so the history reads "412 sent"
    rather than "1 sent".
    """
    total = len(recipients)
    await database.patch(
        f"/email/campaigns/{campaign_id}",
        params={"status": "sending", "recipients": total},
    )

    size = max(1, settings.bulk_batch_size)
    batches = [recipients[i:i + size] for i in range(0, total, size)]
    unsubscribe = general_unsubscribe_url()

    sent = failed = 0
    try:
        for index, batch in enumerate(batches, start=1):
            try:
                reached = len(await build(batch, unsubscribe))
            except Exception:  # noqa: BLE001
                log.exception(
                    "Campaign %s: batch %d of %d failed", campaign_id, index, len(batches)
                )
                reached = 0
            sent += reached
            failed += len(batch) - reached

            await database.patch(
                f"/email/campaigns/{campaign_id}",
                params={"sent": sent, "failed": failed},
            )

            # Exchange throttles a mailbox at roughly 30 messages a minute, and
            # being throttled mid-send looks identical to the mail being
            # rejected. Pacing is cheaper than finding out which it was.
            if index < len(batches) and settings.bulk_send_interval_seconds > 0:
                await asyncio.sleep(settings.bulk_send_interval_seconds)
    except asyncio.CancelledError:
        await database.patch(
            f"/email/campaigns/{campaign_id}",
            params={"status": "failed", "sent": sent, "failed": failed,
                    "error": "Cancelled — the service stopped mid-send."},
        )
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Campaign %s failed", campaign_id)
        await database.patch(
            f"/email/campaigns/{campaign_id}",
            params={"status": "failed", "sent": sent, "failed": failed,
                    "error": str(exc)[:400]},
        )
        return {"sent": sent, "failed": failed, "ok": False}

    # A run where nothing got out is a failure, whatever the loop did. Recording
    # it as "sent" is how a committee discovers weeks later that the newsletter
    # they thought went out never did — the usual cause being a mailbox password
    # that lapsed.
    outcome = "sent" if sent else "failed"
    await database.patch(
        f"/email/campaigns/{campaign_id}",
        params={
            "status": outcome,
            "sent": sent,
            "failed": failed,
            "error": (
                "Nothing was sent — check the mailbox credentials." if not sent else None
            ),
        },
    )
    log.info("Campaign %s finished: %d sent, %d failed", campaign_id, sent, failed)
    return {"sent": sent, "failed": failed, "ok": failed == 0}
