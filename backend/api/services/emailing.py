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
person's own doing (a sign-in code, a booking) is still sent to them alone.

Nothing here decides *whether* to send. Bulk mail written by a timer waits for
a committee member's approval first — see services/campaigns.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import uuid
from time import monotonic
from urllib.parse import quote, urlencode

from fastapi import HTTPException
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


# A WhatsApp group the linked number can post in is an audience like any
# other: ticked in the same list, sent by the same Send. Its key is
# "whatsapp:<group id>". It is not an email address, so `resolve` skips it;
# the send paths post into it (services/whatsapp.py).
WHATSAPP_PREFIX = "whatsapp:"


def whatsapp_groups_in(keys: list[str] | None) -> list[str]:
    """The WhatsApp group ids among audience keys, in order, once each."""
    out: list[str] = []
    for key in keys or []:
        if key.startswith(WHATSAPP_PREFIX):
            jid = key[len(WHATSAPP_PREFIX):]
            if jid and jid not in out:
                out.append(jid)
    return out


def email_keys(keys: list[str] | None) -> list[str]:
    """The audience keys that are email, not WhatsApp."""
    return [k for k in keys or [] if not k.startswith(WHATSAPP_PREFIX)]


async def audiences(*, external_societies: bool = False, whatsapp_user_id: int | None = None, whatsapp_admin_all: bool = False) -> list[dict]:
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

    if external_societies:
        for society in await database.get('/external-societies') or []:
            key=f"society:{society['id']}"
            groups.append({'key':key,'label':f"{society['name']} members",
                'group':'Connected societies','description':f"Union membership list for {academic_year()}",
                'count':len(await resolve([key],allowed_society_ids='all'))})

    # Last, as their own group: a different kind of place, like the
    # department lists — every group the linked number can post in.
    # Here rather than at the top: services/whatsapp builds on this module.
    from services import whatsapp

    for group in await whatsapp.available(user_id=whatsapp_user_id,admin_all=whatsapp_admin_all):
        where = f" · in the {group['community']} community" if group["community"] and not group["announcements"] else ""
        groups.append({
            "key": f"{WHATSAPP_PREFIX}{group['target']}",
            "label": group["name"],
            "group": "WhatsApp",
            "description": f"{group['size'] or 0} people{where} · posted from the society's number.",
            "count": group["size"],
            "whatsapp": True,
        })

    return groups


async def resolve(keys: list[str], *, allowed_society_ids=None) -> list[dict]:
    """Audience keys to a de-duplicated recipient list.

    De-duplication is on the lowercased address and happens across all keys
    together, so somebody who is both a member and subscribed to the newsletter
    gets one email rather than two. First key wins, which is why `members` is
    listed first — it carries the better name.
    """
    seen: dict[str, dict] = {}

    for key in keys:
        if key.startswith('society:'):
            try: society_id=int(key.split(':',1)[1])
            except ValueError: raise HTTPException(400,'Invalid society audience')
            if allowed_society_ids != 'all' and society_id not in (allowed_society_ids or set()):
                raise HTTPException(403,'Not permitted to use this society mailing list')
        for recipient in await _resolve_one(key):
            address = (recipient.get("email") or "").strip().lower()
            if not address or "@" not in address:
                continue
            seen.setdefault(address, {**recipient, "email": address})

    return list(seen.values())


async def _resolve_one(key: str) -> list[dict]:
    if key.startswith('society:'):
        from services import societies
        try: society_id=int(key.split(':',1)[1])
        except ValueError: return []
        return [{**r, 'source':key} for r in await societies.mailing_list(society_id)]

    if key.startswith(WHATSAPP_PREFIX):
        return []  # a WhatsApp group, posted into rather than mailed

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
#
# The send is resumable across a pod replacement. Two claims make that work:
#
#   * the sender lease (job_leases, "campaign:<id>") — at most one sender at a
#     time, old pod or new. It is a database lease, not an in-process lock,
#     because a rolling update has both pods alive together and only the
#     database is visible to both.
#
#   * one row per recipient (campaign_recipients), claimed before the message
#     goes out and confirmed after. A pod killed mid-batch leaves its claimed
#     row unconfirmed, and that row goes stale and is re-claimed — so the
#     replacement pod knows exactly who was already messaged and never
#     double-sends, while a row confirmed `sent` is never mailed again.
#
# Both live behind services/database-routes/jobs.py; this file only ever talks
# to the adapter.

# Both windows are read from the environment rather than hard-coded because CI
# shortens them (the section-02 rollout overlay sets each to 5s) so a killed
# test pod's campaign finishes within seconds, not minutes. Production runs
# with the defaults here.

# The sender lease outlives one batch, not one send: it is renewed after every
# batch, so a paced send keeps it for as long as it keeps making progress. The
# TTL has to comfortably exceed the LONGEST paced batch — at the production
# defaults (batch 400 x interval 2s) that is about 800s — because a live
# sender must never lose its lease mid-batch: the moment the lease expires
# underneath one, another pod can take the campaign over and re-claim rows the
# first sender is still sending, and those recipients get mailed twice. 900s
# clears the longest batch with slack.
#
# What that wait costs is smaller than it sounds. The graceful path — SIGTERM,
# CancelledError, the `finally` releasing the lease — hands the campaign over
# at once, so a rolling update's replacement pod resumes within seconds and
# the TTL never delays anything. The wait only bites on a SIGKILL or a crash,
# where nothing ran to release the lease — and there, a resume within ~15
# minutes beats the shorter TTL that would be needed to beat it, because a TTL
# under the longest batch is exactly what lets a mid-batch sender be taken
# over and double-mail its rows.
SENDER_LEASE_TTL_SECONDS = int(os.environ.get("SENDER_LEASE_TTL_SECONDS", "900"))

# A claimed-but-unconfirmed row has to belong to a dead sender for this long
# before it is re-claimable. Long on purpose: at this age the alternative is
# re-sending on a message that did in fact go out (the sender died between
# SMTP accepting it and the confirm landing), which is the worse failure.
BATCH_STALE_SECONDS = int(os.environ.get("BATCH_STALE_SECONDS", "600"))

# TTL >= stale is an invariant, not a coincidence, and shortening either window
# has to keep it. Nobody can take a campaign over until the dead sender's lease
# has expired, and the lease is renewed right after each batch of rows is
# claimed — so by the time a takeover is possible at all, every row the dead
# sender claimed and never confirmed is already past its stale window and
# re-claimable. A resumed campaign therefore always has every remaining
# recipient within reach, and can never mark itself complete with a row still
# stranded inside a stale window it could not re-claim.

# The startup resume re-tries a campaign whose lease somebody else holds,
# this often, for this long in total, before giving up and leaving it to the
# next restart or another retry. Comfortably past the lease TTL: a SIGKILLed
# sender's lease frees at most one TTL after its last renewal, and a resume
# whose budget ended exactly there would give up in the very poll before the
# takeover it was waiting for became possible.
RESUME_POLL_SECONDS = 10
RESUME_RETRY_SECONDS = 1200


async def send_bulk(
    campaign_id: int,
    recipients: list[dict],
    build,
    kind: str = "newsletter",
    event_id: int | None = None,
) -> dict:
    """Send to everybody in batches, recording progress as it goes.

    `build(batch, unsubscribe)` sends one batch of recipient rows and returns
    the addresses it reached: one message with the whole batch BCC'd. This
    function knows nothing about what is being sent — a newsletter and an
    event announcement share all of this.

    `kind` and `event_id` are written onto the campaign row, not kept here: a
    send interrupted by a restart can only be finished by a *different*
    process, and the row is the only place that process can read them from.
    `recipients` may be empty on a resume — the per-recipient rows seeded by
    the previous pod *are* the audience then, and re-resolving the stored
    audience keys would re-mail people who have since opted out.

    Counts stay per recipient, not per message, so the history reads "412 sent"
    rather than "1 sent".
    """
    await database.patch(
        f"/email/campaigns/{campaign_id}",
        params={
            "status": "sending",
            "kind": kind,
            # Sent only when there is a fresh audience: on a resume this would
            # otherwise reset the recorded count to zero.
            **({"recipients": len(recipients)} if recipients else {}),
            **({"event_id": event_id} if event_id is not None else {}),
        },
    )

    if recipients:
        # Idempotent, so a resume that reaches this with the same list is a
        # no-op: DO NOTHING leaves every previously confirmed row alone.
        await database.post(
            "/campaign-recipients",
            {"campaign_id": campaign_id, "emails": [r["email"] for r in recipients]},
        )

    # The lease. Not claimed -> another pod (old or new) is mid-send on this
    # campaign right now, and there is nothing this one can do but stay out of
    # the way: claiming anyway would put two senders on one campaign, and each
    # sender paces itself at Exchange's per-mailbox rate limit.
    lease_name = f"campaign:{campaign_id}"
    holder = uuid.uuid4().hex
    claim = await database.post(
        "/job-leases/claim",
        {"name": lease_name, "holder": holder, "ttl_seconds": SENDER_LEASE_TTL_SECONDS},
    )
    if not (claim or {}).get("claimed"):
        log.info(
            "Campaign %s: sender lease held by %r — deferring to that pod",
            campaign_id, (claim or {}).get("holder"),
        )
        # ok, not an error: the campaign is in good hands, and this is the
        # state the resume path retries later.
        return {"sent": 0, "failed": 0, "deferred": True, "ok": True}

    # Where the last sender left off, so a replacement pod's counters continue
    # its predecessor's instead of restarting the visible history at zero —
    # the committee reads "sent 412", not "sent 200 (twice)".
    campaign = await database.get(f"/email/campaigns/{campaign_id}") or {}
    sent = int(campaign.get("sent") or 0)
    failed = int(campaign.get("failed") or 0)
    unsubscribe = general_unsubscribe_url()

    try:
        while True:
            batch = await database.post(
                "/campaign-recipients/claim",
                {
                    "campaign_id": campaign_id,
                    "batch_size": max(1, settings.bulk_batch_size),
                    "stale_seconds": BATCH_STALE_SECONDS,
                },
            ) or {}
            emails = batch.get("emails") or []
            if not emails:
                break

            # Renewal, not a second lease: same name, same holder. Losing it
            # means somebody took over after this pod's TTL ran out mid-batch
            # — that pod now owns the send, so stop rather than double-pace it.
            renewal = await database.post(
                "/job-leases/claim",
                {
                    "name": lease_name,
                    "holder": holder,
                    "ttl_seconds": SENDER_LEASE_TTL_SECONDS,
                },
            )
            if not (renewal or {}).get("claimed"):
                log.warning(
                    "Campaign %s: lost the sender lease mid-batch — leaving the "
                    "rest to the lease holder", campaign_id,
                )
                # The campaign stays 'sending'; the rows this pod claimed but
                # did not confirm will go stale and the new holder re-claims them.
                break

            rows = [{"email": e} for e in emails]
            try:
                reached = await build(rows, unsubscribe)
            except Exception:  # noqa: BLE001
                log.exception(
                    "Campaign %s: batch of %d failed", campaign_id, len(rows)
                )
                reached = []
            reached_set = {str(e).strip().lower() for e in reached}

            delivered = [e for e in emails if e.strip().lower() in reached_set]
            missed = [e for e in emails if e.strip().lower() not in reached_set]
            for batch_emails, ok in ((delivered, True), (missed, False)):
                if not batch_emails:
                    continue
                # Both confirmed either way: a row left 'claimed' would be
                # re-sent once it goes stale, and a missed address must not be
                # re-mailed on every future resume.
                await database.post(
                    "/campaign-recipients/confirm",
                    {"campaign_id": campaign_id, "emails": batch_emails, "ok": ok},
                )
                sent += len(batch_emails) if ok else 0
                failed += 0 if ok else len(batch_emails)

            await database.patch(
                f"/email/campaigns/{campaign_id}",
                params={"sent": sent, "failed": failed},
            )

            # Exchange throttles a mailbox at roughly 30 messages a minute, and
            # being throttled mid-send looks identical to the mail being
            # rejected. Pacing is cheaper than finding out which it was.
            if batch.get("remaining", 0) and settings.bulk_send_interval_seconds > 0:
                await asyncio.sleep(settings.bulk_send_interval_seconds)
    except asyncio.CancelledError:
        # SIGTERM under a rolling update. Deliberately NOT marking the campaign
        # failed: this send is not lost, it is paused — the rows stay claimable
        # (a claimed-unconfirmed one goes stale and is re-claimed) and the
        # replacement pod's startup resume finishes it. Marking it failed here
        # is how a graceful shutdown used to destroy the campaign it was saving.
        # The lease is released in `finally`, so the next sender starts at once.
        log.info("Campaign %s interrupted mid-send — left resumable", campaign_id)
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Campaign %s failed", campaign_id)
        await database.patch(
            f"/email/campaigns/{campaign_id}",
            params={
                "status": "failed",
                "sent": sent,
                "failed": failed,
                "error": str(exc)[:400],
            },
        )
        return {"sent": sent, "failed": failed, "ok": False}
    finally:
        # Always given back — also on the cancelled path, because that is the
        # case where it matters most: the successor pod cannot resume while a
        # lease from a pod that asked to stop still covers the campaign.
        await database.post(
            "/job-leases/release", {"name": lease_name, "holder": holder}
        )

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


# ------------------------------------------------------------- resuming sends


async def resume_pending() -> dict:
    """Pick up bulk sends the previous api pod left unfinished.

    Runs once at startup, beside the announce scheduler: a rolling update
    stops the old pod while a slow, paced send is mid-flight, and "the new pod
    finishes it" is the whole design. The campaign rows say which sends those
    are (`status = 'sending'`), and `kind`/`event_id` say how to rebuild the
    message.

    Does not re-resolve the audience: the rows seeded when the send started are
    the people who were messaged, and a subscriber who opted out since should
    not be re-subscribed by an automatic retry.
    """
    if not mailer.configured():
        # With no mail credentials no send could have run, so none can be
        # resumed — and a resumed send here would only mark campaigns failed.
        log.info("No mail credentials — skipping the send-resume pass")
        return {"resumable": 0, "skipped": True}

    rows = await database.get("/campaigns/resumable") or []
    results: list[dict] = []
    deferred: list[dict] = []

    for row in rows:
        await _resume_one_into(row, results, deferred)

    # A deferred campaign is leased by a live pod (or by one that died with
    # seconds left on its lease). Either way the lease frees up within its TTL,
    # and polling picks the campaign up then — without this wait, a send that
    # was claimed a second before the old pod stopped would sit unresumed
    # until the next restart.
    deadline = monotonic() + RESUME_RETRY_SECONDS
    while deferred and monotonic() < deadline:
        await asyncio.sleep(RESUME_POLL_SECONDS)
        still: list[dict] = []
        for row in deferred:
            await _resume_one_into(row, results, still)
        deferred = still

    if deferred:
        log.warning(
            "%d campaign(s) still leased elsewhere after %ss — leaving them for "
            "the next restart", len(deferred), RESUME_RETRY_SECONDS,
        )
    log.info("Resume pass: %d resumable, %d picked up", len(rows), len(rows) - len(deferred))
    return {"resumable": len(rows), "results": results}


async def _resume_one_into(row: dict, done: list[dict], deferred: list[dict]) -> None:
    """Resume one campaign, filing the outcome or the deferral into a list.

    One bad campaign must never kill the pass — several can be resumable after
    one deploy, and the others still deserve their retry.
    """
    campaign_id = row.get("id")
    try:
        outcome = await _resume_one(row)
    except Exception:  # noqa: BLE001
        log.exception("Resuming campaign %s failed", campaign_id)
        done.append({"campaign": campaign_id, "ok": False, "error": True})
        return
    if outcome.get("deferred"):
        deferred.append({"campaign": campaign_id, "deferred": True})
    else:
        done.append({"campaign": campaign_id, **outcome})


async def _resume_one(row: dict) -> dict:
    """Rebuild one campaign's `build` closure from the stored row, and send.

    This is why the row carries `kind` and `event_id`: the closure that built
    the original messages died with the old pod, and the stored columns are the
    only description of it that survives. The builder is the same one the
    approval queue previews and sends with, so a resumed message is the one
    that was approved.
    """
    # Imported here, not at the top: campaigns imports this module, so a
    # module-level import would be circular at load time. Only ever called from
    # the startup task, never at import time.
    from services import campaigns

    campaign_id = row["id"]
    kind = (row.get("kind") or "newsletter").strip()

    try:
        build = await campaigns.builder_for(row)
    except campaigns.Unbuildable as exc:
        # The event was deleted between send and resume, or the kind is from a
        # future deploy. A row this old is an audit record, not a promise, and
        # retrying cannot help — close it with the reason rather than leaving
        # it resumable forever.
        log.error("Campaign %s cannot be resumed: %s — marking failed", campaign_id, exc)
        await database.patch(
            f"/email/campaigns/{campaign_id}",
            params={"status": "failed", "error": f"Cannot resume: {exc}."},
        )
        return {"campaign": campaign_id, "ok": False, "error": str(exc)}

    return await send_bulk(
        campaign_id, [], build, kind=kind, event_id=row.get("event_id")
    )
