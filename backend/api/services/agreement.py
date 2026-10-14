"""The sponsorship agreement, as data the site can enforce.

The 2026-27 agreement is one package at two fees (£2,000 or £4,000 ex VAT), so
every sponsor starts from the same clauses. The committee then adjusts per
sponsor in the admin page — a clause struck out, an allowance raised — and
those adjustments are rows in `sponsor_terms`. A sponsor with no row for a
clause has the standard package, which is why nothing needs seeding when a
sponsor is created.

An allowance is resolved in three steps: the sponsor's own figure if the
committee set one, else the committee's standard-package figure
(`sponsor_package_defaults`), else `default_allowance` below — which is only a
starting point until the committee sets the standard.

The wording below is the agreement's own, as submitted to the Union. It is for
the committee: the portal shows a sponsor each clause's label and how much of it
is used, never the text.

Two kinds of clause:

  * `eesoc` — what the society owes the sponsor. Some are countable (one
    workshop, one named event); usage is counted from the events the committee
    links to the sponsor and the marketing they share.
  * `sponsor` — what the sponsor owes the society. Tracked, not enforced: the
    committee ticks each one off when it arrives.
"""

from __future__ import annotations

from clients import database
from config import settings
from services import emailing
from utils import email as mailer
from utils.year import academic_year

CLAUSES: list[dict] = [
    # ------------------------------------------------ what EESoc provides
    {
        "key": "workshop",
        "side": "eesoc",
        "label": "Sponsored workshop",
        "text": (
            "The Student Group shall provide the Sponsor with a sponsored workshop, "
            "publicising the workshop, organising it and dealing with all associated "
            "logistics."
        ),
        "countable": True,
        "default_allowance": 1,
        "unit": "workshop",
        "links_events": True,
    },
    {
        "key": "named_event",
        "side": "eesoc",
        "label": "Naming rights on an event",
        "text": (
            "The Student Group shall provide the Sponsor with naming rights on one "
            "flagship event, publicising the event, organising it and dealing with "
            "all associated logistics."
        ),
        "countable": True,
        "default_allowance": 1,
        "unit": "named event",
        "links_events": True,
    },
    {
        "key": "seminar",
        "side": "eesoc",
        "label": "Industry seminar speaking slot",
        "text": (
            "The Student Group shall provide the Sponsor with a speaking slot in its "
            "industry seminar series, and shall handle the room and promotion."
        ),
        "countable": True,
        "default_allowance": 1,
        "unit": "talk",
        "links_events": True,
    },
    {
        "key": "careers_fair",
        "side": "eesoc",
        "label": "Careers fair stall",
        "text": (
            "The Student Group shall provide the Sponsor with a stall at its annual "
            "careers fair, prime placement, and a pre-event slot in the members' "
            "newsletter."
        ),
        "countable": True,
        "default_allowance": 1,
        "unit": "stall",
        "links_events": True,
    },
    {
        "key": "marketing",
        "side": "eesoc",
        "label": "Marketing to members",
        "text": (
            "The Student Group shall forward the Sponsor's marketing material to its "
            "members through its social media and its members' newsletter."
        ),
        "countable": True,
        # The agreement sets no number. Three a year is a starting point until
        # the committee sets the standard package's figure, not a term anyone
        # signed.
        "default_allowance": 3,
        "unit": "share",
        "links_events": False,
    },
    {
        "key": "contact",
        "side": "eesoc",
        "label": "Contact shared with members",
        "text": (
            "The Student Group shall share a contact provided by the Sponsor with its "
            "members, so that members may contact the Sponsor directly."
        ),
        "countable": False,
        "links_events": False,
    },
    {
        "key": "merch",
        "side": "eesoc",
        "label": "Merchandise and logo",
        "text": (
            "The Student Group shall distribute the Sponsor's merchandise to its "
            "members, and shall display the Sponsor's logo on Student Group "
            "merchandise by prior agreement."
        ),
        "countable": False,
        "links_events": False,
    },
    {
        "key": "wiee",
        "side": "eesoc",
        "label": "Women in Electrical Engineering and outreach",
        "text": (
            "The Student Group shall provide the Sponsor with access to its Women in "
            "Electrical Engineering sub-society and its diversity and outreach "
            "activities."
        ),
        "countable": False,
        "links_events": True,
    },
    # ---------------------------------------------- what the sponsor provides
    {
        "key": "s_marketing",
        "side": "sponsor",
        "label": "Marketing material",
        "text": (
            "The Sponsor shall provide the Student Group with marketing material for "
            "the Student Group to forward to its members."
        ),
    },
    {
        "key": "s_workshop",
        "side": "sponsor",
        "label": "Workshop content",
        "text": (
            "The Sponsor shall create the content and supply the materials for the "
            "sponsored workshop."
        ),
    },
    {
        "key": "s_events",
        "side": "sponsor",
        "label": "Content for events taken up",
        "text": (
            "The Sponsor shall create the content and supply the materials for any "
            "event the Sponsor takes up."
        ),
    },
    {
        "key": "s_brand",
        "side": "sponsor",
        "label": "Brand assets and approvals",
        "text": (
            "The Sponsor shall supply its brand assets and logos, and approve any use "
            "of its name or logo for named events, publicity and merchandise."
        ),
    },
    {
        "key": "s_fair",
        "side": "sponsor",
        "label": "Careers fair display",
        "text": (
            "The Sponsor shall post or bring what it needs to display at the careers "
            "fair, including its merchandise."
        ),
    },
]

BY_KEY = {c["key"]: c for c in CLAUSES}

# Clauses a linked event can deliver, for the committee's "link an event" form.
EVENT_CLAUSES = [c["key"] for c in CLAUSES if c.get("links_events")]


# Who a sponsor may ask to reach. A curated subset of the admin audience list:
# never the test list, never "everyone with an account", never the people
# booked on one event. The committee sees the request and can still change it.
MARKETING_AUDIENCES: list[dict] = [
    {"key": "members", "label": "All EESoc members"},
    {"key": "topic:roles", "label": "Members who want jobs and internships"},
    {"key": "dept_ugs_all", "label": "All EEE/EIE undergraduates"},
    {"key": "dept_ee1", "label": "EEE/EIE Year 1"},
    {"key": "dept_ee2", "label": "EEE Year 2"},
    {"key": "dept_eie2", "label": "EIE Year 2"},
    {"key": "dept_ee3", "label": "EEE Year 3"},
    {"key": "dept_eie3", "label": "EIE Year 3"},
    {"key": "dept_ee4", "label": "EEE Year 4"},
    {"key": "dept_eie4", "label": "EIE Year 4"},
    {"key": "dept_msc", "label": "MSc students"},
    {"key": "dept_phd", "label": "PhD students"},
]
MARKETING_AUDIENCE_KEYS = {a["key"] for a in MARKETING_AUDIENCES}

MARKETING_CHANNELS = {"email": "Email", "social": "Social media"}


COUNTABLE = [c for c in CLAUSES if c.get("countable")]


async def standard_allowances() -> dict[str, int]:
    """clause → the standard package's allowance: the committee's figure where
    they have set one, the code's starting point otherwise."""
    return {p["key"]: p["allowance"] for p in await standard_package()}


async def standard_package() -> list[dict]:
    """The countable clauses with the standard package's allowance for each,
    for the committee's "Standard package" editor."""
    rows = await database.get("/sponsors/terms/defaults") or []
    set_by_committee = {r["clause"]: r for r in rows}
    out = []
    for c in COUNTABLE:
        row = set_by_committee.get(c["key"])
        out.append(
            {
                "key": c["key"],
                "label": c["label"],
                "unit": c["unit"],
                "allowance": row["allowance"] if row else c["default_allowance"],
                "setByCommittee": row is not None,
            }
        )
    return out


def _effective(clause: dict, row: dict | None, standard: dict[str, int]) -> dict:
    agreed = True if row is None else bool(row.get("agreed", True))
    allowance = override = default = None
    if clause.get("countable"):
        default = standard[clause["key"]]
        override = row.get("allowance") if row else None
        allowance = default if override is None else override
    return {
        "key": clause["key"],
        "side": clause["side"],
        "label": clause["label"],
        "text": clause["text"],
        "countable": bool(clause.get("countable")),
        "unit": clause.get("unit"),
        "linksEvents": bool(clause.get("links_events")),
        "agreed": agreed,
        "allowance": allowance,
        # The two parts of `allowance`, for the admin page: the sponsor's own
        # figure (None when they are on the standard) and the standard's.
        "allowanceOverride": override,
        "standardAllowance": default,
        "note": (row or {}).get("note") or "",
        "doneAt": (row or {}).get("done_at"),
        # Whether the committee has ever touched this clause for this sponsor,
        # so the admin page can say "standard package" rather than imply a
        # decision somebody made.
        "customised": row is not None,
    }


async def terms(sponsor_id: int) -> list[dict]:
    """Every clause as it applies to this sponsor, with this year's usage."""
    rows = await database.get(f"/sponsors/{sponsor_id}/terms") or []
    by_clause = {r["clause"]: r for r in rows}
    used = await database.get(
        f"/sponsors/{sponsor_id}/usage", {"academic_year": academic_year()}
    ) or {}
    standard = await standard_allowances()
    out = []
    for clause in CLAUSES:
        term = _effective(clause, by_clause.get(clause["key"]), standard)
        if term["countable"]:
            term["used"] = int(used.get(clause["key"], 0))
            term["remaining"] = max(0, (term["allowance"] or 0) - term["used"])
        out.append(term)
    return out


async def term(sponsor_id: int, key: str) -> dict:
    for t in await terms(sponsor_id):
        if t["key"] == key:
            return t
    raise KeyError(key)


async def agreed_map() -> dict[int, set[str]]:
    """sponsor id → the clauses struck out for it. One query for every sponsor,
    for the public list, which must not fan out per card."""
    rows = await database.get("/sponsors/terms/all") or []
    struck: dict[int, set[str]] = {}
    for r in rows:
        if not r.get("agreed", True):
            struck.setdefault(r["sponsor_id"], set()).add(r["clause"])
    return struck


def marketing_email(item: dict, sponsor_name: str) -> tuple[str, str]:
    """(html, text) for a sponsor's marketing request, as members receive it.

    The sponsor's words through the same renderer as a newsletter, a line
    saying whose message it is — members should never have to guess whether
    EESoc or a company is talking to them — and the uploaded files underneath:
    images inline, PDFs as links. Used for the committee's preview and the send
    alike, so what was approved is what arrives.
    """
    body_html, body_text = emailing.render_body(item.get("body") or "")
    intro = f"Shared on behalf of {sponsor_name}, one of EESoc's sponsors."
    html = [
        f'<p style="margin:0 0 16px;font-family:{mailer.SANS};font-size:13px;'
        f'line-height:1.5;color:{mailer.MUTED};">{mailer.escape(intro)}</p>',
        body_html,
    ]
    text = [intro, body_text]
    for url in item.get("media") or []:
        absolute = url if url.startswith("http") else f"{settings.site_origin}{url}"
        href = mailer.escape(absolute)
        if url.lower().endswith(".pdf"):
            html.append(
                f'<p style="margin:0 0 14px;font-family:{mailer.SANS};font-size:15px;">'
                f'<a href="{href}" style="color:{mailer.BLUE};">Open the attached PDF</a></p>'
            )
            text.append(f"PDF: {absolute}")
        else:
            html.append(
                f'<p style="margin:0 0 14px;"><img src="{href}" alt="" width="520" '
                f'style="display:block;max-width:100%;height:auto;border:0;"></p>'
            )
            text.append(f"Image: {absolute}")
    return "".join(html), "\n\n".join(t for t in text if t)
