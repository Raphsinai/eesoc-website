"""Public sponsor list and the sponsor portal.

A sponsor account is an ordinary user (same email-code sign-in) linked to a
sponsor organisation through `sponsor_users`. Every portal endpoint is scoped to
the sponsors that account is linked to — otherwise a signed-in sponsor could
read and edit a competitor's listings by changing the id in the URL.

What a sponsor may do is bounded by their agreement: `services/agreement.py`
holds the clauses, and the committee adjusts them per sponsor. A clause the
committee struck out is refused here, not merely hidden in the portal.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from clients import database
from services import agreement, sponsor_notes, storage
from models.auth import AuthSession
from utils.deps import (
    current_session,
    require_feature,
    require_sponsor,
    sponsor_scope,
    verify_csrf,
)
from utils.present import LONDON, format_date
from utils.year import academic_year

router = APIRouter()


@router.get("/sponsors")
async def list_sponsors(
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Public. Feeds both the home carousel and the Sponsors page.

    Committee with sponsors access additionally see unpublished drafts, the
    same preview events and resource pages give, so a card can be checked in
    place before it goes live. Narrower than `is_admin` on purpose: the 26-27
    pipeline is a list of companies that have not signed, and an events officer
    has no more business seeing it than a visitor does.

    Explicitly field-by-field: `amount_pence` and `internal_note` are committee
    information and must never reach this response. Returning the row wholesale
    is how that leaks the first time somebody adds a column.
    """
    may_preview = bool(session and session.can("sponsors"))
    sponsors = await database.get(
        "/sponsors", {"active_only": not may_preview}
    ) or []

    # Three queries for the whole page rather than three per card.
    struck = await agreement.agreed_map()
    contacts: dict[int, list[dict]] = {}
    for c in await database.get("/sponsors/contacts/all") or []:
        contacts.setdefault(c["sponsor_id"], []).append(c)
    year = academic_year()
    events: dict[int, list[dict]] = {}
    for link in await database.get("/sponsors/links/confirmed") or []:
        if not link.get("event_published") or link.get("event_academic_year") != year:
            continue
        events.setdefault(link["sponsor_id"], []).append(
            {
                "slug": link["event_slug"],
                "title": link["event_title"],
                "date": format_date(_dt(link.get("event_starts_at"))),
            }
        )

    return {
        "sponsors": [
            {
                "id": s["slug"],
                "name": s["name"],
                "sector": s.get("sector"),
                "strand": s.get("strand"),
                "oneLine": s.get("one_line"),
                "blurb": s.get("blurb", ""),
                "withUs": s.get("with_us", []),
                "site": s.get("site"),
                "roles": s.get("roles", 0),
                "logoUrl": s.get("logo_url"),
                # Only ever false for an admin: everyone else is served the
                # filtered list, so this cannot say anything about a draft to
                # somebody who was not already allowed to see it.
                "active": s.get("active", True),
                # Published so members can write to the sponsor directly. EESoc
                # never handles a CV. Withheld when the committee struck the
                # contact clause: sharing a contact is something the agreement
                # grants, not a default.
                "contacts": (
                    []
                    if "contact" in struck.get(s["id"], set())
                    else [_public_contact(c) for c in contacts.get(s["id"], [])]
                ),
                "events": events.get(s["id"], []),
            }
            for s in sponsors
        ]
    }


def _public_contact(c: dict) -> dict:
    return {
        "kind": c["kind"],
        "value": c["value"],
        "label": c.get("label") or "",
        "note": c.get("note") or "",
    }


@router.get("/jobs")
async def list_jobs() -> dict:
    """Public careers list — live roles across every active sponsor."""
    rows = await database.get("/sponsors/listings/all", {"status": "live"}) or []
    return {
        "jobs": [
            {
                "id": r["id"],
                "title": r["title"],
                "location": r.get("location"),
                "closes": format_date(_dt(r.get("closes_on"))),
                "applyUrl": r.get("apply_url"),
                "sponsor": r.get("sponsor_name"),
                "sponsorSlug": r.get("sponsor_slug"),
                "logoUrl": r.get("logo_url"),
            }
            for r in rows
        ]
    }


def _dt(value):
    from datetime import date, datetime

    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def _scoped_sponsor(session: AuthSession, sponsor_id: int | None, permission: str = 'sponsors.portal.view') -> int:
    """Resolve which sponsor this request acts for.

    A sponsor user normally has exactly one, so the id can be omitted. Admins
    must name one, since they belong to none.
    """
    if sponsor_id is not None:
        # Sponsor users retain their own self-service rights. Committee can
        # act for another company ONLY with the named operation's permission.
        if sponsor_id not in session.sponsor_ids and not session.can(permission):
            raise HTTPException(403, f"Permission required: {permission}")
        return sponsor_id
    if session.sponsor_ids:
        return session.sponsor_ids[0]
    # Admins belong to no sponsor, so there is nothing to default to. This is a
    # common way to hit a confusing 400 while testing, hence the explicit wording.
    raise HTTPException(
        400,
        "You are signed in as committee, not as a sponsor. Add ?sponsor_id=<id> "
        "to act on a specific sponsor's behalf.",
    )


async def _require_clause(sid: int, key: str) -> dict:
    """Refuse an action the agreement does not grant this sponsor.

    403, with the reason in words: the sponsor did nothing wrong, and the fix is
    a conversation with the committee, not a retry.
    """
    term = await agreement.term(sid, key)
    if not term["agreed"]:
        raise HTTPException(
            403,
            f"{term['label']} is not part of your agreement this year. "
            "Talk to eesoc.vp@imperial.ac.uk if you think it should be.",
        )
    return term


# --------------------------------------------------------------------- dashboard


@router.get("/portal/dashboard")
async def dashboard(
    sponsor_id: int | None = None, session: AuthSession = Depends(require_sponsor)
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.view")
    # Who the page is acting for. The view used to guess this from the public
    # list and picked whichever sponsor happened to come back first, so the
    # heading named the wrong company.
    acting = await database.get(f"/sponsors/{sid}")
    # Committee may name any sponsor, so an id that does not exist reaches here.
    # Without this it renders as a dashboard of zeros for a company that is not
    # there, which reads as data rather than as a mistake.
    if acting is None:
        raise HTTPException(404, "No such sponsor")
    stats = await database.get(f"/sponsors/{sid}/stats") or {}
    listings = await database.get(f"/sponsors/{sid}/listings") or []
    sponsorable = await database.get("/sponsors/claims/claimable", {"sponsor_id": sid}) or []
    claims = await database.get(f"/sponsors/{sid}/claims") or []
    terms = await agreement.terms(sid)
    counted = await agreement.counted(sid)
    marketing = next(t for t in terms if t["key"] == "marketing")
    notes = await database.get(f"/sponsors/{sid}/notes") or []

    return {
        # Committee replies the sponsor has not opened yet, for the badge on
        # the Messages link.
        "unreadReplies": sum(
            1 for n in notes if n["from_committee"] and n.get("read_at") is None
        ),
        "stats": [
            {
                "value": str(stats.get("live_listings", 0)),
                "label": "Live listings",
                "sub": f"{stats.get('closing_soon', 0)} closing this week",
            },
            {
                "value": str(stats.get("events_open", 0)),
                "label": "Sponsorable events",
                "sub": "Open for you to register interest",
            },
            {
                "value": (
                    f"{marketing['remaining']} of {marketing['allowance']}"
                    if marketing["agreed"]
                    else "—"
                ),
                "label": "Marketing shares left",
                "sub": f"For {academic_year()}" if marketing["agreed"] else "Not in your agreement",
            },
        ],
        "sponsorable": [_sponsorable_row(e) for e in sponsorable],
        "yourEvents": [_claim_row(c) for c in claims if c["status"] != "declined"],
        "listings": [_listing_row(r) for r in listings],
        "package": [
            _package_row(t, counted.get(t["key"], [])) for t in terms if t["side"] == "eesoc"
        ],
        "sponsor": {
            "id": sid,
            "name": acting.get("name"),
            "sector": acting.get("sector"),
            # Committee-only money stays out of this: the year is just which
            # agreement the portal is showing.
            "agreementYear": acting.get("agreement_year"),
        },
    }


def _package_row(t: dict, used_by: list[dict]) -> dict:
    """One clause as the sponsor sees it: its name, what is left, and what used
    the rest — including anything the committee recorded because it happened
    outside the portal.

    Not its wording. The agreement text is the committee's (the admin page
    shows it); a sponsor reads their signed copy, not ours.
    """
    if not t["agreed"]:
        status = "Not in your agreement"
    elif t["countable"]:
        status = f"{t['used']} of {t['allowance']} used"
    elif t["doneAt"]:
        status = "Done"
    else:
        status = "Included"
    return {
        "key": t["key"],
        "label": t["label"],
        "agreed": t["agreed"],
        "status": status,
        "note": t["note"],
        "counted": [_use_view(u) for u in used_by] if t["countable"] else [],
    }


def _use_view(u: dict) -> dict:
    return {
        "title": u["title"],
        "date": u["date"],
        "recorded": u["kind"] == "record",
        "pending": u["pending"],
    }


ABOUT_LENGTH = 400


def _provisional_meta(starts_at) -> str:
    """When an unpublished event is, said no more precisely than it is known.

    A draft offered to sponsors is usually an idea: its date is a placeholder
    the event form insists on, so showing "Thu 12 Nov · EEE 403" would promise
    what nobody has booked. The month is as far as it goes.
    """
    when = _dt(starts_at)
    if when is not None and when.tzinfo is not None:
        when = when.astimezone(LONDON)
    month = when.strftime("%B %Y") if when else "Date to be confirmed"
    return f"Provisionally {month} · details to be confirmed"


def _about(text: str | None) -> str:
    text = " ".join((text or "").split())
    if len(text) <= ABOUT_LENGTH:
        return text
    return text[:ABOUT_LENGTH].rsplit(" ", 1)[0] + "…"


def _sponsorable_row(e: dict) -> dict:
    provisional = not e.get("published")
    if provisional:
        meta = _provisional_meta(e["starts_at"])
    else:
        meta = f"{format_date(_dt(e['starts_at']))} · {e.get('venue') or 'TBC'}" + (
            f" · {e['capacity']} places" if e.get("capacity") else ""
        )
    return {
        "id": e["slug"],
        "eventId": e["id"],
        "title": e["title"],
        "meta": meta,
        # Not published yet, so an idea: students cannot see it and the details
        # above are the committee's plan, not a booking.
        "provisional": provisional,
        "about": _about(e.get("description")),
        "interested": bool(e.get("interested")),
    }


CLAIM_STATUS = {
    "interested": "Interest registered",
    "confirmed": "Yours",
    "declined": "Not this time",
}


def _claim_row(c: dict) -> dict:
    clause = agreement.BY_KEY.get(c.get("clause") or "")
    provisional = not c.get("published")
    when = (
        [_provisional_meta(c.get("starts_at"))]
        if provisional
        else [format_date(_dt(c.get("starts_at"))), c.get("venue") or "TBC"]
    )
    return {
        "id": c["slug"],
        "eventId": c["event_id"],
        "title": c["title"],
        "provisional": provisional,
        "meta": " · ".join(
            p
            for p in [
                *when,
                clause["label"] if clause else None,
            ]
            if p
        ),
        "status": c["status"],
        "statusLabel": CLAIM_STATUS.get(c["status"], c["status"]),
        "published": bool(c.get("published")),
    }


def _expired(r: dict) -> bool:
    """Live, but past its closing day — so off the public careers list, which
    is the same rule (`/sponsors/listings/all`)."""
    closes = _dt(r.get("closes_on"))
    return (
        r["status"] == "live"
        and closes is not None
        and closes.date() < datetime.now(LONDON).date()
    )


def _listing_row(r: dict) -> dict:
    expired = _expired(r)
    shown = "closed" if expired else r["status"]
    tone = {"live": "success", "closed": "neutral", "draft": "accent"}.get(
        shown, "neutral"
    )
    closes = format_date(_dt(r.get("closes_on")))
    meta = " · ".join(
        p
        for p in [
            r.get("location"),
            (f"closed {closes}" if expired else f"closes {closes}") if closes else None,
        ]
        if p
    )
    return {
        "id": r["id"],
        "title": r["title"],
        "meta": meta,
        "status": shown.title(),
        "tone": tone,
        # The full row, so the edit form opens filled in rather than with only
        # the title copied across.
        "listing": {
            "title": r["title"],
            "location": r.get("location") or "",
            "apply_url": r.get("apply_url") or "",
            "closes_on": str(r["closes_on"])[:10] if r.get("closes_on") else "",
            "body": r.get("body") or "",
            "status": r["status"],
        },
    }


# ------------------------------------------------------------------------ panels

REQUEST_FORMATS: list[dict] = [
    {
        "id": "workshop_talk",
        "title": "Workshop or talk",
        "meta": "A hands-on session or a talk — your content, our room and students.",
        "clauses": ["workshop", "seminar"],
    },
    {
        "id": "named_event",
        "title": "Named event",
        "meta": "An EESoc event carrying your name. You supply the content; we run it.",
        "clauses": ["named_event"],
    },
    {
        "id": "visit",
        "title": "Site visit",
        "meta": "Students at your office or lab · usually 20–40.",
        "clauses": [],
    },
    {
        "id": "hackathon",
        "title": "Hackathon challenge",
        "meta": "You set it, you judge it, you hire from the shortlist.",
        "clauses": [],
    },
    {
        "id": "other",
        "title": "Something else",
        "meta": "Tell us the outcome you want and we'll say whether we can do it.",
        "clauses": [],
    },
]
FORMAT_BY_ID = {f["id"]: f for f in REQUEST_FORMATS}

REQUEST_STATUS = {"open": "Sent", "in_progress": "In discussion", "closed": "Closed"}


def _format_view(fmt: dict, terms: dict[str, dict]) -> dict:
    """Say plainly whether a format is something the sponsor has already paid
    for, so nobody asks for a site visit believing it is in the package."""
    included = [terms[k] for k in fmt["clauses"] if terms[k]["agreed"]]
    if not included:
        coverage = "Outside your package — we'll tell you whether we can do it."
    else:
        left = sum(t["remaining"] for t in included)
        coverage = (
            f"In your package · {left} left this year"
            if left
            else "In your package, but this year's is used — we can talk about another."
        )
    return {
        "id": fmt["id"],
        "title": fmt["title"],
        "meta": fmt["meta"],
        "coverage": coverage,
        "included": bool(included),
    }


@router.get("/portal/panels/{panel}")
async def panel(
    panel: str,
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.view")

    if panel == "jobs":
        listings = await database.get(f"/sponsors/{sid}/listings") or []
        live = sum(1 for r in listings if r["status"] == "live" and not _expired(r))
        return {
            "eyebrow": "Job listings",
            "heading": f"{live} role{'s' if live != 1 else ''} live.",
            "rows": [_listing_row(r) for r in listings],
            "aside": (
                "Members are notified of posted listings, and each one stays on "
                "the site until the end of its closing date."
            ),
        }

    if panel == "calendar":
        sponsorable = await database.get(
            "/sponsors/claims/claimable", {"sponsor_id": sid}
        ) or []
        claims = await database.get(f"/sponsors/{sid}/claims") or []
        return {
            "eyebrow": "Event calendar",
            "heading": "The term as we are planning it.",
            "sponsorable": [_sponsorable_row(e) for e in sponsorable],
            "yourEvents": [_claim_row(c) for c in claims],
            "aside": (
                "Register your interest in any event below. Many are still ideas: "
                "the timing is provisional and the details are worked out with the "
                "sponsor who takes it. Several sponsors can ask for the same one; "
                "the committee confirms who takes it and agrees the details with "
                "you by email. Students see an event only once it is published."
            ),
        }

    if panel == "request":
        terms = {t["key"]: t for t in await agreement.terms(sid)}
        requests = await database.get(f"/sponsors/{sid}/requests") or []
        return {
            "eyebrow": "Request an event",
            "heading": "Tell us the outcome you want.",
            "formats": [_format_view(f, terms) for f in REQUEST_FORMATS],
            "requests": [
                {
                    "id": r["id"],
                    "format": r.get("format") or "Request",
                    "body": r.get("body") or "",
                    "timing": r.get("timing") or "",
                    "status": r["status"],
                    "statusLabel": REQUEST_STATUS.get(r["status"], r["status"]),
                    "response": r.get("response") or "",
                    "sent": format_date(_dt(r.get("created_at"))),
                }
                for r in requests
            ],
            "aside": (
                "Your request goes to the committee, who reply here and by email. "
                "eesoc.vp@imperial.ac.uk if you want to chase it."
            ),
        }

    if panel == "marketing":
        term = await agreement.term(sid, "marketing")
        items = await database.get(f"/sponsors/{sid}/marketing") or []
        counted = (await agreement.counted(sid)).get("marketing", [])
        return {
            "eyebrow": "Marketing",
            "heading": "Share something with our members.",
            "agreed": term["agreed"],
            "allowance": term["allowance"],
            "used": term["used"],
            "remaining": term["remaining"],
            "year": academic_year(),
            "audiences": agreement.MARKETING_AUDIENCES,
            "channels": [
                {"key": k, "label": v} for k, v in agreement.MARKETING_CHANNELS.items()
            ],
            "items": [_marketing_view(m) for m in items],
            # Shares the committee made outside the portal and recorded, so the
            # count above adds up to what is listed here.
            "recorded": [_use_view(u) for u in counted if u["kind"] == "record"],
            "aside": (
                "Write the subject and message, add images or a PDF, and choose who "
                "it is for. The committee checks it, then shares it by email and on "
                "our social media. Each request uses one of your shares for the "
                "year; a declined or withdrawn one gives it back."
            ),
        }

    if panel == "merch":
        term = await agreement.term(sid, "merch")
        assets = await database.get(f"/sponsors/{sid}/assets") or []
        return {
            "eyebrow": "Brand & merch",
            "heading": "Logos in, merch out.",
            "merchAgreed": term["agreed"],
            "rows": [
                {"id": a["id"], "title": a["label"], "url": a["file_url"]}
                for a in assets
            ],
            "aside": (
                "Vector logo (PDF or high-resolution PNG), light and dark variants, "
                "and any brand rules we should follow. We ask before using your name "
                "or logo on a named event, publicity or merchandise."
            ),
        }

    if panel == "contacts":
        term = await agreement.term(sid, "contact")
        contacts = await database.get(f"/sponsors/{sid}/contacts") or []
        return {
            "eyebrow": "Contacts",
            "heading": (
                "How members reach you."
                if contacts
                else "Tell members how to reach you."
            ),
            "agreed": term["agreed"],
            "contacts": [
                {k: c.get(k) or "" for k in ("kind", "value", "label", "note")}
                for c in contacts
            ],
            "aside": (
                "Every contact here appears on your public sponsor card — an email "
                "address for CVs, a careers page, an application form, a booking "
                "link. EESoc does not collect or forward CVs; members contact you "
                "directly."
            ),
        }

    if panel == "messages":
        notes = await database.get(f"/sponsors/{sid}/notes") or []
        view = sponsor_notes.threads(notes, "sponsor")
        # Opening the panel is reading the replies in it.
        if any(t["unread"] for t in view):
            await database.post(
                f"/sponsors/{sid}/notes/read", params={"from_committee": True}
            )
        return {
            "eyebrow": "Messages",
            "heading": "Ask us anything.",
            "threads": view,
            "topics": await _note_topics(sid),
            "aside": (
                "Questions and notes go straight to the committee, who are emailed "
                "each one and reply here. You can also ask about a specific event, "
                "request, share or role from its own page."
            ),
        }

    raise HTTPException(404, "No such panel")


async def _note_topics(sid: int) -> list[dict]:
    """Everything a sponsor can start a thread about, for the picker."""
    out = [{"topic": "general", "label": "General question", "group": "General"}]
    for t in await agreement.terms(sid):
        if t["agreed"]:
            out.append({"topic": f"clause:{t['key']}", "label": t["label"], "group": "Your agreement"})
    seen = set()
    for e in await database.get(f"/sponsors/{sid}/claims") or []:
        seen.add(e["event_id"])
        out.append({"topic": f"event:{e['event_id']}", "label": e["title"], "group": "Events"})
    for e in await database.get("/sponsors/claims/claimable", {"sponsor_id": sid}) or []:
        if e["id"] not in seen:
            out.append({"topic": f"event:{e['id']}", "label": e["title"], "group": "Events"})
    for r in await database.get(f"/sponsors/{sid}/requests") or []:
        out.append({"topic": f"request:{r['id']}", "label": r.get("format") or "Request", "group": "Requests"})
    for m in await database.get(f"/sponsors/{sid}/marketing") or []:
        out.append({"topic": f"marketing:{m['id']}", "label": m["subject"], "group": "Marketing"})
    for l in await database.get(f"/sponsors/{sid}/listings") or []:
        out.append({"topic": f"listing:{l['id']}", "label": l["title"], "group": "Job listings"})
    return out


MARKETING_STATUS = {
    "pending": "Waiting for the committee",
    "shared": "Shared",
    "declined": "Declined",
    "withdrawn": "Withdrawn",
}


def _marketing_view(m: dict) -> dict:
    labels = {a["key"]: a["label"] for a in agreement.MARKETING_AUDIENCES}
    return {
        "id": m["id"],
        "subject": m["subject"],
        "body": m.get("body") or "",
        "media": [_media_view(u) for u in m.get("media") or []],
        "audiences": [labels.get(a, a) for a in m.get("audiences") or []],
        "channels": [
            agreement.MARKETING_CHANNELS.get(c, c) for c in m.get("channels") or []
        ],
        "preferredDate": format_date(_dt(m.get("preferred_date"))),
        "status": m["status"],
        "statusLabel": MARKETING_STATUS.get(m["status"], m["status"]),
        "response": m.get("response") or "",
        "emailed": format_date(_dt(m.get("emailed_at"))),
        "posted": format_date(_dt(m.get("posted_at"))),
        "sent": format_date(_dt(m.get("created_at"))),
    }


def _media_view(url: str) -> dict:
    return {"url": url, "kind": "pdf" if url.lower().endswith(".pdf") else "image"}


# ------------------------------------------------------------------ portal actions

LISTING_STATUSES = {"draft", "live", "closed"}


def _listing_payload(body: dict) -> dict:
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "A role needs a title.")
    status = body.get("status") or "live"
    if status not in LISTING_STATUSES:
        raise HTTPException(400, "A listing is a draft, live or closed.")
    apply_url = (body.get("apply_url") or "").strip() or None
    if apply_url and not re.match(r"^https?://", apply_url, re.I):
        apply_url = f"https://{apply_url}"
    return {
        "title": title,
        "location": (body.get("location") or "").strip() or None,
        "body": body.get("body") or "",
        "apply_url": apply_url,
        "closes_on": body.get("closes_on") or None,
        "status": status,
    }


@router.post("/portal/jobs", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def post_job(
    body: dict = Body(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.jobs.create")
    payload = _listing_payload(body)
    row = await database.post(f"/sponsors/{sid}/listings", payload)
    state = "is live" if payload["status"] == "live" else f"is saved as {payload['status']}"
    return {"ok": True, "listing": row, "message": f"'{payload['title']}' {state}."}


@router.put("/portal/jobs/{listing_id}", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def edit_job(
    listing_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    await _own_listing(listing_id, session)
    row = await database.put(f"/sponsors/listings/{listing_id}", _listing_payload(body))
    return {"ok": True, "listing": row, "message": "Listing saved."}


async def _own_listing(listing_id: int, session: AuthSession) -> None:
    """A listing id alone says nothing about who owns it, so check before write."""
    if session.can("sponsors.portal.jobs.edit"):
        return
    for sid in session.sponsor_ids:
        listings = await database.get(f"/sponsors/{sid}/listings") or []
        if any(r["id"] == listing_id for r in listings):
            return
    raise HTTPException(404, "No such listing")


INTEREST_REFUSALS = {
    "not_offered": "That event isn't open for sponsorship.",
    "past": "That event has already happened.",
    "already_arranged": "That event is already being arranged with a sponsor.",
    "taken": "That event has been given to a sponsor already.",
    "already_interested": "You've already registered interest in that event.",
}


# `/claim` is the old name, kept so a portal tab opened before this deploy
# still works when its button is pressed.
@router.post("/portal/events/{event_id}/interest", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
@router.post("/portal/events/{event_id}/claim", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def register_interest(
    event_id: int,
    sponsor_id: int | None = None,
    note: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Register interest in sponsoring an event.

    Re-checked here rather than trusted from the list the sponsor was shown:
    that list may be minutes stale, and a button the UI chose not to render is
    not a control.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.events.claim")

    state = await database.get(
        f"/sponsors/claims/is-claimable/{event_id}", {"sponsor_id": sid}
    )
    if state is None:
        raise HTTPException(404, "No such event")
    if not state["claimable"]:
        raise HTTPException(
            409, INTEREST_REFUSALS.get(state["reason"], "That event isn't available.")
        )

    await database.post(
        f"/sponsors/{sid}/claims/{event_id}",
        params={"note": (note or "").strip()[:500] or None},
    )
    return {
        "ok": True,
        "message": (
            "Interest registered. The committee will be in touch by email to "
            "agree the details."
        ),
    }


@router.delete("/portal/events/{event_id}/interest", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def withdraw_interest(
    event_id: int,
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Take back an expression of interest. Never undoes a confirmed link —
    that is an arrangement with the committee, not a button."""
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.events.withdraw")
    await database.delete(
        f"/sponsors/{sid}/claims/{event_id}", {"only_interest": True}
    )
    return {"ok": True, "message": "Interest withdrawn."}


@router.post("/portal/requests", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def create_request(
    body: dict = Body(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Ask for an event.

    This creates a *request*, not an event. If the committee turns it into one,
    they link that event to this sponsor, which takes it off every other
    sponsor's list.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.requests.create")
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "Tell us what you have in mind.")
    fmt = FORMAT_BY_ID.get(body.get("format") or "other")
    if fmt is None:
        raise HTTPException(400, "Choose what kind of event you're asking for.")

    row = await database.post(
        f"/sponsors/{sid}/requests",
        {
            "sponsor_id": sid,
            "format": fmt["title"],
            "body": text[:5000],
            "timing": (body.get("timing") or "").strip()[:200] or None,
        },
    )
    return {
        "ok": True,
        "request": row,
        "message": (
            "Sent. The committee will reply here and by email — "
            "eesoc.vp@imperial.ac.uk if you want to chase it."
        ),
    }


# ---------------------------------------------------------------- marketing


@router.post("/portal/marketing/media", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def upload_marketing_media(
    file: UploadFile = File(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """An image or PDF to go with a marketing request.

    Uploaded on its own so the form can show it before the request is sent.
    Same allow-list as every other upload — no SVG.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.marketing.upload")
    await _require_clause(sid, "marketing")
    meta = await storage.save(file, f"sponsor-{sid}")
    row = await database.post("/uploads", {**meta, "uploaded_by": session.user.id})
    url = storage.public_url(row["stored_name"])
    return {"ok": True, **_media_view(url), "name": file.filename or "file"}


def _our_media(url: str) -> bool:
    """Whether a URL is a file we stored, so a members' email never embeds
    something a third party hosts and can change after the committee looked."""
    prefix = storage.public_url("")
    return isinstance(url, str) and url.startswith(prefix) and ".." not in url


MAX_MEDIA = 6


@router.post("/portal/marketing", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def create_marketing(
    body: dict = Body(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Ask EESoc to share something with members.

    Counted against the sponsor's allowance for the year at the moment of
    asking. The check and the insert are two calls, so two requests sent in the
    same instant could both pass with one share left; the committee approves
    every one by hand, which is where that would be caught.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.marketing.create")
    term = await _require_clause(sid, "marketing")
    if term["remaining"] <= 0:
        raise HTTPException(
            409,
            f"You've used all {term['allowance']} of your marketing shares for "
            f"{academic_year()}. A declined or withdrawn request gives one back.",
        )

    subject = (body.get("subject") or "").strip()
    text = (body.get("body") or "").strip()
    if not subject:
        raise HTTPException(400, "Give it a subject.")
    if not text:
        raise HTTPException(400, "Write the message you want members to read.")

    audiences = [a for a in body.get("audiences") or [] if isinstance(a, str)]
    if not audiences:
        raise HTTPException(400, "Choose who it is for.")
    unknown = set(audiences) - agreement.MARKETING_AUDIENCE_KEYS
    if unknown:
        raise HTTPException(400, "That audience isn't one we can share with.")

    channels = [c for c in body.get("channels") or ["email"] if isinstance(c, str)]
    if not channels or set(channels) - set(agreement.MARKETING_CHANNELS):
        raise HTTPException(400, "Choose email, social media, or both.")

    media = [u for u in body.get("media") or [] if u]
    if len(media) > MAX_MEDIA:
        raise HTTPException(400, f"Up to {MAX_MEDIA} files per request.")
    if not all(_our_media(u) for u in media):
        raise HTTPException(400, "Upload files here rather than linking to them.")

    preferred = body.get("preferred_date") or None
    if preferred:
        try:
            preferred = date.fromisoformat(str(preferred)[:10]).isoformat()
        except ValueError:
            raise HTTPException(400, "That date doesn't look right.")

    row = await database.post(
        f"/sponsors/{sid}/marketing",
        {
            "academic_year": academic_year(),
            "subject": subject[:200],
            "body": text[:10000],
            "media": media,
            "audiences": list(dict.fromkeys(audiences)),
            "channels": list(dict.fromkeys(channels)),
            "preferred_date": preferred,
            "created_by": session.user.id,
        },
    )
    return {
        "ok": True,
        "item": _marketing_view(row),
        "message": "Sent to the committee. You'll see here when it has been shared.",
    }


@router.post("/portal/marketing/{marketing_id}/withdraw", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def withdraw_marketing(
    marketing_id: int,
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.marketing.withdraw")
    item = await database.get(f"/sponsors/marketing/{marketing_id}")
    # Somebody else's request is reported as missing, not forbidden: whether
    # an id exists is itself information about a competitor.
    if item is None or item["sponsor_id"] != sid:
        raise HTTPException(404, "No such request")
    if item["status"] != "pending":
        raise HTTPException(409, "Only a request still waiting for the committee can be withdrawn.")
    await database.patch(f"/sponsors/marketing/{marketing_id}", {"status": "withdrawn"})
    return {"ok": True, "message": "Withdrawn. That share is yours to use again."}


# -------------------------------------------------------------------- notes


@router.get("/portal/notes")
async def get_thread(
    topic: str = "general",
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """One thread, for the "ask a question" dialog on an item."""
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.notes.view")
    label = await sponsor_notes.resolve_topic(sid, topic)
    notes = await database.get(f"/sponsors/{sid}/notes", {"topic": topic}) or []
    view = sponsor_notes.threads(notes, "sponsor")
    if view and view[0]["unread"]:
        await database.post(
            f"/sponsors/{sid}/notes/read",
            params={"from_committee": True, "topic": topic},
        )
    return {"topic": topic, "label": label, "notes": view[0]["notes"] if view else []}


@router.post("/portal/notes", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def add_note(
    topic: str = Body(default="general", embed=True),
    body: str = Body(embed=True),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Leave a question or note for the committee, who are emailed about it.

    A committee member acting for a sponsor in the portal writes as the
    committee, so a note typed while checking a sponsor's view is never filed
    as though the sponsor said it.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.notes.send")
    sponsor = await database.get(f"/sponsors/{sid}")
    if sponsor is None:
        raise HTTPException(404, "No such sponsor")
    label = await sponsor_notes.resolve_topic(sid, topic)
    text = sponsor_notes.clean_body(body)
    as_committee = sid not in session.sponsor_ids
    note = await database.post(
        f"/sponsors/{sid}/notes",
        {
            "topic": topic,
            "topic_label": label,
            "body": text,
            "from_committee": as_committee,
            "author_id": session.user.id,
        },
    )
    if as_committee:
        sponsor_notes.alert_sponsor(sid, topic, label)
    else:
        sponsor_notes.alert_committee(sponsor, topic, label, text, session.user.email)
    return {
        "ok": True,
        "note": note,
        "message": (
            "Sent. The committee has been notified and will reply here."
            if not as_committee
            else "Sent to the sponsor as the committee."
        ),
    }


# ----------------------------------------------------------------- contacts

MAX_CONTACTS = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_contacts(raw: list) -> list[dict]:
    """Validate a contact list from the portal. Shared with the admin page."""
    if not isinstance(raw, list):
        raise HTTPException(400, "Contacts must be a list.")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        value = (item.get("value") or "").strip()
        if not value:
            continue
        if kind == "email":
            value = value.removeprefix("mailto:")
            if not EMAIL.match(value):
                raise HTTPException(400, f"'{value}' doesn't look like an email address.")
        elif kind == "link":
            # Any other scheme is refused outright rather than prefixed:
            # "javascript:…" with https:// stuck on the front is harmless but
            # is nobody's careers page. A port ("site.com:8080") is not a scheme.
            scheme = re.match(r"^([a-z][a-z0-9+.-]*):(?!\d)", value, re.I)
            if scheme and scheme.group(1).lower() not in ("http", "https"):
                raise HTTPException(400, f"'{value}' doesn't look like a web address.")
            if not re.match(r"^https?://", value, re.I):
                value = f"https://{value}"
            # A link is shown as a link and nothing else: no javascript:, and no
            # whitespace that would let it break out of the href.
            if not re.match(r"^https?://[^\s<>\"']+$", value, re.I):
                raise HTTPException(400, f"'{value}' doesn't look like a web address.")
        else:
            raise HTTPException(400, "A contact is an email address or a link.")
        out.append(
            {
                "kind": kind,
                "value": value[:300],
                "label": (item.get("label") or "").strip()[:80],
                "note": (item.get("note") or "").strip()[:200],
            }
        )
    if len(out) > MAX_CONTACTS:
        raise HTTPException(400, f"Up to {MAX_CONTACTS} contacts.")
    return out


@router.put("/portal/contacts", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def save_contacts(
    contacts: list = Body(embed=True),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.contacts.edit")
    await _require_clause(sid, "contact")
    saved = await database.put(f"/sponsors/{sid}/contacts", clean_contacts(contacts))
    return {
        "ok": True,
        "contacts": saved,
        "message": "Saved. Your sponsor card shows these now.",
    }


# --------------------------------------------------------------- brand & merch


@router.post("/portal/merch/upload", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def upload_asset(
    file: UploadFile = File(...),
    label: str = Form("Artwork"),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Accept a logo or artwork file from a sponsor.

    Writes to the same media volume as committee uploads, through the same
    allow-list — JPEG, PNG, WebP, GIF, PDF, and deliberately not SVG, which can
    carry script and would be served from our own origin.

    Filed under purpose='sponsor' with the uploader recorded, so an unwanted file
    can be traced to whoever put it there.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.merch.upload")

    meta = await storage.save(file, f"sponsor-{sid}")
    row = await database.post("/uploads", {**meta, "uploaded_by": session.user.id})
    url = storage.public_url(row["stored_name"])

    asset = await database.post(
        f"/sponsors/{sid}/assets",
        {"sponsor_id": sid, "label": label.strip() or "Artwork", "file_url": url},
    )
    return {"ok": True, "asset": asset, "url": url, "message": f"'{asset['label']}' uploaded."}


@router.delete("/portal/merch/{asset_id}", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def delete_asset(
    asset_id: int,
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Remove one of your own assets.

    Scoped: the asset must belong to a sponsor this account represents, or an
    id in the URL would let one sponsor delete another's artwork.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.merch.delete")
    assets = await database.get(f"/sponsors/{sid}/assets") or []
    if not any(a["id"] == asset_id for a in assets):
        raise HTTPException(404, "No such asset")
    await database.delete(f"/sponsors/assets/{asset_id}")
    return {"ok": True}


# Fields a sponsor may change on their own card. `sort_order` and `active` are
# absent on purpose: where a sponsor appears, and whether they appear at all, is
# EESoc's call, not theirs. Contacts are their own list (`/portal/contacts`).
SPONSOR_EDITABLE = {"name", "sector", "strand", "one_line", "blurb", "with_us",
                    "site", "logo_url", "roles"}


def _card(sponsor: dict) -> dict:
    """The sponsor's card as the sponsor may see it.

    The adapter's row also carries `amount_pence` and `internal_note`, which
    are the committee's; every portal response that holds a sponsor row goes
    through here so neither reaches the sponsor's browser.
    """
    return {k: sponsor.get(k) for k in SPONSOR_EDITABLE | {"id", "slug"}}


@router.get("/portal/profile")
async def get_profile(
    sponsor_id: int | None = None, session: AuthSession = Depends(require_sponsor)
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.view")
    sponsor = await database.get(f"/sponsors/{sid}")
    if sponsor is None:
        raise HTTPException(404, "No such sponsor")
    return {
        "sponsor": _card(sponsor),
        "editable": sorted(SPONSOR_EDITABLE),
    }


@router.post("/portal/profile/logo", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def upload_logo(
    file: UploadFile = File(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Replace the logo on this sponsor's card.

    Images only: the shared allow-list also takes PDFs, which an <img> cannot
    show. SVG is refused by that allow-list already (it can carry script). Saved
    straight onto the card, so the upload is the whole action.
    """
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "The logo must be a JPEG, PNG, WebP or GIF image.")
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.profile.logo")
    current = await database.get(f"/sponsors/{sid}")
    if current is None:
        raise HTTPException(404, "No such sponsor")

    meta = await storage.save(file, f"sponsor-{sid}")
    row = await database.post("/uploads", {**meta, "uploaded_by": session.user.id})
    url = storage.public_url(row["stored_name"])
    await database.put(f"/sponsors/{sid}", {**current, "logo_url": url})
    return {"ok": True, "url": url, "message": "Logo updated. It is live on your card."}


@router.put("/portal/profile", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def update_profile(
    body: dict = Body(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Let a sponsor maintain their own card.

    The existing row is fetched and only whitelisted keys are overwritten, so a
    crafted payload cannot change `sort_order`, `active` or `slug` — the fields
    that decide prominence and identity.
    """
    sid = await _scoped_sponsor(session, sponsor_id, permission="sponsors.portal.profile.edit")
    current = await database.get(f"/sponsors/{sid}")
    if current is None:
        raise HTTPException(404, "No such sponsor")

    merged = {**current, **{k: v for k, v in body.items() if k in SPONSOR_EDITABLE}}
    if not (merged.get("name") or "").strip():
        raise HTTPException(400, "A sponsor needs a name")
    return _card(await database.put(f"/sponsors/{sid}", merged))
