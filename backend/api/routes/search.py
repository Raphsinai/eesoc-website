"""Header search.

Replaces the client-side index built from the mockup's `site.js`, which searched
placeholder events that do not exist and could never find a real one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from clients import database
from models.auth import AuthSession
from utils.deps import current_session

router = APIRouter()

# Static destinations, so "lockers" finds the page even with nothing in the
# database yet.
PAGES = [
    ("Home", "/", "Page"),
    ("What's On", "/events", "Page"),
    ("Resources", "/resources", "Page"),
    ("Sponsors", "/sponsors", "Page"),
    ("Lockers", "/lockers", "Page"),
]


@router.get("/search")
async def search(
    q: str = "", limit: int = 8, session: AuthSession | None = Depends(current_session)
) -> dict:
    term = q.strip().lower()
    if len(term) < 2:
        return {"results": []}

    results: list[dict] = []

    def add(label: str, kind: str, to: str, hint: str | None = None) -> None:
        results.append({"label": label, "kind": kind, "to": to, "hint": hint})

    for label, to, kind in PAGES:
        if term in label.lower():
            add(label, kind, to)

    # Only published events, and only what the caller may see.
    can_see_all = bool(session and (session.can("events") or session.can("bookings")))
    can_see_industry = bool(session and (session.can("industry") or session.can("industry_bookings")))
    can_see_resources = bool(session and session.can("resources"))
    events = await database.get(
        "/events", {"published_only": not (can_see_all or can_see_industry), "limit": 200}
    ) or []
    for event in events:
        if not event["published"] and not (can_see_all or (can_see_industry and event.get("industry"))):
            continue
        haystack = " ".join(
            filter(None, [event.get("title"), event.get("category"), event.get("venue")])
        ).lower()
        if term in haystack:
            add(event["title"], "Event", f"/events/{event['slug']}", event.get("venue"))

    pages = await database.get("/resource-pages", {"published_only": not can_see_resources}) or []
    for page in pages:
        haystack = f"{page['title']} {page.get('description') or ''}".lower()
        if term in haystack:
            add(page["title"], "Resources", f"/resources/{page['slug']}")

    sponsors = await database.get("/sponsors", {"active_only": True}) or []
    for sponsor in sponsors:
        if term in f"{sponsor['name']} {sponsor.get('sector') or ''}".lower():
            add(sponsor["name"], "Sponsor", "/sponsors", sponsor.get("sector"))

    # Exact prefix matches first — typing "loc" should surface Lockers above an
    # event that merely mentions it.
    results.sort(key=lambda r: (not r["label"].lower().startswith(term), r["label"]))
    return {"results": results[:limit], "query": q}
