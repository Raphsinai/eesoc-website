"""Public sponsor list and the sponsor portal.

A sponsor account is an ordinary user (same email-code sign-in) linked to a
sponsor organisation through `sponsor_users`. Every portal endpoint is scoped to
the sponsors that account is linked to — otherwise a signed-in sponsor could
read and edit a competitor's listings by changing the id in the URL.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from clients import database
from services import storage
from models.auth import AuthSession
from utils.deps import require_feature, require_sponsor, sponsor_scope, verify_csrf
from utils.present import event_card, format_date

router = APIRouter()


@router.get("/sponsors")
async def list_sponsors() -> dict:
    """Public. Feeds both the home carousel and the Sponsors page.

    Explicitly field-by-field: `amount_pence` and `internal_note` are committee
    information and must never reach this response. Returning the row wholesale
    is how that leaks the first time somebody adds a column.
    """
    sponsors = await database.get("/sponsors", {"active_only": True}) or []
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
                # Published so members can write to the sponsor directly. EESoc
                # never handles a CV.
                "cvContact": s.get("cv_contact_email"),
                "cvContactName": s.get("cv_contact_name"),
                "cvContactNote": s.get("cv_contact_note"),
            }
            for s in sponsors
        ]
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


async def _scoped_sponsor(session: AuthSession, sponsor_id: int | None) -> int:
    """Resolve which sponsor this request acts for.

    A sponsor user normally has exactly one, so the id can be omitted. Admins
    must name one, since they belong to none.
    """
    if sponsor_id is not None:
        sponsor_scope(session, sponsor_id)
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


# --------------------------------------------------------------------- dashboard


@router.get("/portal/dashboard")
async def dashboard(
    sponsor_id: int | None = None, session: AuthSession = Depends(require_sponsor)
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id)
    stats = await database.get(f"/sponsors/{sid}/stats") or {}
    listings = await database.get(f"/sponsors/{sid}/listings") or []
    claimable = await database.get("/sponsors/claims/claimable") or []
    claims = await database.get(f"/sponsors/{sid}/claims") or []

    claimed_ids = {c["event_id"] for c in claims if c["status"] != "declined"}
    mine = []
    for claim in claims:
        event = await database.get(f"/events/{claim['event_id']}")
        if event:
            mine.append(
                {
                    "id": event["slug"],
                    "title": event["title"],
                    "meta": f"{format_date(_dt(event['starts_at']))} · {event.get('venue') or 'TBC'} · {claim['status']}",
                }
            )

    return {
        "stats": [
            {
                "value": str(stats.get("live_listings", 0)),
                "label": "Live listings",
                "sub": f"{stats.get('closing_soon', 0)} closing this week",
            },
            {
                "value": f"{stats.get('applications', 0):,}",
                "label": "Applications",
                "sub": "Across all your roles",
            },
            {
                "value": str(stats.get("events_open", 0)),
                "label": "Events open",
                "sub": "Unclaimed slots",
            },
            {
                "value": f"{stats.get('members_reached', 0):,}",
                "label": "Members reached",
                "sub": "Members we reach by newsletter",
            },
        ],
        "claimable": [
            {
                "id": e["slug"],
                "eventId": e["id"],
                "title": e["title"],
                "meta": f"{format_date(_dt(e['starts_at']))} · {e.get('venue') or 'TBC'}"
                + (f" · {e['capacity']} places" if e.get("capacity") else ""),
            }
            for e in claimable
            if e["id"] not in claimed_ids
        ],
        "yourEvents": mine,
        "listings": [_listing_row(r) for r in listings],
    }


def _listing_row(r: dict) -> dict:
    tone = {"live": "success", "closed": "neutral", "draft": "accent"}.get(
        r["status"], "neutral"
    )
    closes = format_date(_dt(r.get("closes_on")))
    meta = " · ".join(
        p
        for p in [
            r.get("location"),
            f"closes {closes}" if closes else None,
            f"{r.get('applications', 0)} applications",
        ]
        if p
    )
    return {
        "id": r["id"],
        "title": r["title"],
        "meta": meta,
        "status": r["status"].title(),
        "tone": tone,
        "action": "Repost" if r["status"] == "closed" else "Edit",
    }


# ------------------------------------------------------------------------ panels


@router.get("/portal/panels/{panel}")
async def panel(
    panel: str,
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id)

    if panel == "jobs":
        listings = await database.get(f"/sponsors/{sid}/listings") or []
        live = sum(1 for r in listings if r["status"] == "live")
        return {
            "eyebrow": "Job listings",
            "heading": f"{live} role{'s' if live != 1 else ''} live.",
            "label": "Your listings",
            "rows": [_listing_row(r) for r in listings],
            "asideLabel": "Post a role",
            "aside": "Listings go into the members' newsletter the Monday after you post, and sit on the site until the closing date.",
            "cta": "Post a job",
        }

    if panel == "calendar":
        claimable = await database.get("/sponsors/claims/claimable") or []
        return {
            "eyebrow": "Event calendar",
            "heading": "The term as we are planning it.",
            "label": "Open slots",
            "rows": [
                {
                    "id": e["id"],
                    "title": e["title"],
                    "meta": f"{format_date(_dt(e['starts_at']))} · {e.get('venue') or 'TBC'}",
                    "action": "Claim",
                }
                for e in claimable
            ],
            "asideLabel": "How claiming works",
            "aside": "Claim a slot and we hold it for five working days while we agree the details by email. Nothing is public until you say so.",
            "cta": "Book a call",
        }

    if panel == "request":
        requests = await database.get(f"/sponsors/{sid}/requests") or []
        return {
            "eyebrow": "Request an event",
            "heading": "Tell us the outcome you want.",
            "label": "Your requests" if requests else "Formats other sponsors have asked for",
            "rows": [
                {
                    "id": r["id"],
                    "title": r.get("format") or "Request",
                    "meta": (r.get("body") or "")[:120],
                    "action": r["status"].replace("_", " ").title(),
                }
                for r in requests
            ]
            or [
                {"id": "workshop", "title": "Sponsored workshop", "meta": "Your brief, our room and students · half or full day", "action": "Request"},
                {"id": "hackathon", "title": "Hackathon challenge", "meta": "You set it, you judge it, you hire from the shortlist", "action": "Request"},
                {"id": "visit", "title": "Site visit", "meta": "Your office or lab · 20–40 students", "action": "Request"},
                {"id": "prize", "title": "Named prize", "meta": "Attached to a project or a module", "action": "Request"},
            ],
            "asideLabel": "Anything else",
            "aside": "If it is not on the list, send two lines to eesoc.vp@imperial.ac.uk and we will tell you whether we can fill the room.",
            "cta": "Start a request",
        }

    if panel == "merch":
        assets = await database.get(f"/sponsors/{sid}/assets") or []
        return {
            "eyebrow": "Merch & assets",
            "heading": "Logos in, merch out.",
            "label": "Your files",
            "rows": [
                {
                    "id": a["id"],
                    "title": a["label"],
                    "meta": a["file_url"],
                    "action": "Delete",
                }
                for a in assets
            ],
            "asideLabel": "What we need",
            "aside": "Vector logo (SVG or EPS), light and dark variants, and any brand rules we should follow.",
            "cta": "Upload artwork",
        }

    if panel == "cv":
        sponsor = await database.get(f"/sponsors/{sid}") or {}
        configured = bool(sponsor.get("cv_contact_email"))
        return {
            "eyebrow": "CVs",
            "heading": (
                "Members send CVs to you directly."
                if configured
                else "Tell us where CVs should go."
            ),
            "label": "How it works",
            "rows": [
                {
                    "id": "direct",
                    "title": "Straight to you",
                    "meta": "EESoc does not collect or forward CVs — we're not permitted to.",
                    "action": "",
                },
                {
                    "id": "contact",
                    "title": sponsor.get("cv_contact_email") or "No address set",
                    "meta": " · ".join(
                        p for p in [
                            sponsor.get("cv_contact_name"),
                            sponsor.get("cv_contact_note"),
                        ] if p
                    ) or "Add an address and it appears on your public sponsor card.",
                    "action": "Edit",
                },
            ],
            "asideLabel": "Why not a CV book",
            "aside": (
                "We used to plan a CV book. EESoc isn't allowed to gather members' "
                "CVs and pass them on, so instead you publish a contact point and "
                "interested members write to you themselves."
            ),
            "cta": "Set CV contact",
            "cvContact": {
                "email": sponsor.get("cv_contact_email"),
                "name": sponsor.get("cv_contact_name"),
                "note": sponsor.get("cv_contact_note"),
            },
        }

    raise HTTPException(404, "No such panel")


# ------------------------------------------------------------------ portal actions


@router.post("/portal/jobs", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def post_job(
    body: dict = Body(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "A role needs a title.")

    row = await database.post(
        f"/sponsors/{sid}/listings",
        {
            "title": title,
            "location": (body.get("location") or "").strip() or None,
            "body": body.get("body", ""),
            "apply_url": (body.get("apply_url") or "").strip() or None,
            "closes_on": body.get("closes_on") or None,
            "status": body.get("status") or "live",
        },
    )
    return {"ok": True, "listing": row, "message": f"'{title}' is live."}


@router.put("/portal/jobs/{listing_id}", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def edit_job(
    listing_id: int,
    body: dict = Body(...),
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    await _own_listing(listing_id, session)
    return await database.put(f"/sponsors/listings/{listing_id}", body)


async def _own_listing(listing_id: int, session: AuthSession) -> None:
    """A listing id alone says nothing about who owns it, so check before write."""
    if session.is_admin:
        return
    for sid in session.sponsor_ids:
        listings = await database.get(f"/sponsors/{sid}/listings") or []
        if any(r["id"] == listing_id for r in listings):
            return
    raise HTTPException(404, "No such listing")


CLAIM_REFUSALS = {
    "not_published": "That event isn't published yet.",
    "not_offered": "That event isn't open for sponsorship.",
    "already_arranged": "That event is already being arranged with a sponsor.",
    "taken": "Somebody has already claimed that one.",
}


@router.post("/portal/events/{event_id}/claim", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def claim_event(
    event_id: int,
    sponsor_id: int | None = None,
    note: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Claim a sponsorship slot.

    Claimability is re-checked here rather than trusted from the list the
    sponsor was shown: that list may be minutes stale, and a button the UI
    chose not to render is not a control.
    """
    sid = await _scoped_sponsor(session, sponsor_id)

    state = await database.get(f"/sponsors/claims/is-claimable/{event_id}")
    if state is None:
        raise HTTPException(404, "No such event")
    if not state["claimable"]:
        raise HTTPException(
            409, CLAIM_REFUSALS.get(state["reason"], "That event can't be claimed.")
        )

    await database.post(f"/sponsors/{sid}/claims/{event_id}", params={"note": note})
    return {
        "ok": True,
        "message": "Claimed. We'll hold the slot for five working days while we agree the details.",
    }


@router.post("/portal/requests", dependencies=[Depends(verify_csrf), require_feature("sponsor_portal")])
async def create_request(
    body: dict = Body(...),
    sponsor_id: int | None = None,
    session: AuthSession = Depends(require_sponsor),
) -> dict:
    """Ask for an event format.

    This creates a *request*, not an event. If the committee turns it into one,
    that event is marked as arranged with this sponsor and never offered to the
    claim queue — see `requested_by_sponsor_id`.
    """
    sid = await _scoped_sponsor(session, sponsor_id)
    text = (body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "Tell us what you have in mind.")

    row = await database.post(
        f"/sponsors/{sid}/requests",
        {"sponsor_id": sid, "format": body.get("format"), "body": text},
    )
    return {
        "ok": True,
        "request": row,
        "message": (
            "Sent. The VP will come back to you by email — "
            "eesoc.vp@imperial.ac.uk if you want to chase it."
        ),
    }


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
    sid = await _scoped_sponsor(session, sponsor_id)

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
    sid = await _scoped_sponsor(session, sponsor_id)
    assets = await database.get(f"/sponsors/{sid}/assets") or []
    if not any(a["id"] == asset_id for a in assets):
        raise HTTPException(404, "No such asset")
    await database.delete(f"/sponsors/assets/{asset_id}")
    return {"ok": True}


# Fields a sponsor may change on their own card. `sort_order` and `active` are
# absent on purpose: where a sponsor appears, and whether they appear at all, is
# EESoc's call, not theirs.
SPONSOR_EDITABLE = {"name", "sector", "strand", "one_line", "blurb", "with_us",
                    "site", "logo_url", "roles",
                    "cv_contact_email", "cv_contact_name", "cv_contact_note"}


@router.get("/portal/profile")
async def get_profile(
    sponsor_id: int | None = None, session: AuthSession = Depends(require_sponsor)
) -> dict:
    sid = await _scoped_sponsor(session, sponsor_id)
    sponsor = await database.get(f"/sponsors/{sid}")
    if sponsor is None:
        raise HTTPException(404, "No such sponsor")
    return {
        "sponsor": {k: sponsor.get(k) for k in SPONSOR_EDITABLE | {"id", "slug"}},
        "editable": sorted(SPONSOR_EDITABLE),
        "note": "Ordering and visibility are set by the EESoc committee.",
    }


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
    sid = await _scoped_sponsor(session, sponsor_id)
    current = await database.get(f"/sponsors/{sid}")
    if current is None:
        raise HTTPException(404, "No such sponsor")

    merged = {**current, **{k: v for k, v in body.items() if k in SPONSOR_EDITABLE}}
    if not (merged.get("name") or "").strip():
        raise HTTPException(400, "A sponsor needs a name")
    return await database.put(f"/sponsors/{sid}", merged)
