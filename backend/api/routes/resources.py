"""Resources: public reads, admin writes, and file uploads.

Content is a tree (page -> section -> module -> per-year feedback), mirroring
what eesoc.com actually publishes, and every node is editable without a deploy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from clients import database
from models.auth import AuthSession
from services import storage
from utils.deps import require_action, current_session, require_resources, require_files, verify_csrf

log = logging.getLogger("api.resources")

router = APIRouter()

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "resources_seed.json"


@router.get("/resources")
async def list_pages(session: AuthSession | None = Depends(current_session)) -> dict:
    """Sidebar. Admins additionally see unpublished pages."""
    is_admin = bool(session and session.can("resources.drafts"))
    pages = await database.get(
        "/resource-pages", {"published_only": not is_admin}
    ) or []
    return {
        "pages": [
            {
                "id": p["id"],
                "slug": p["slug"],
                "label": p["title"],
                "description": p.get("description"),
                "icon": p.get("icon"),
                "membersOnly": p.get("members_only", False),
                "published": p.get("published", True),
            }
            for p in pages
        ]
    }


@router.get("/resources/{slug}")
async def get_page(
    slug: str, session: AuthSession | None = Depends(current_session)
) -> dict:
    page = await database.get(f"/resource-pages/by-slug/{slug}")
    if page is None:
        raise HTTPException(404, "No such resource page")

    is_admin = bool(session and session.can("resources.drafts"))
    if not page["published"] and not is_admin:
        raise HTTPException(404, "No such resource page")

    # Members-only pages are gated on the server. Hiding the link in the sidebar
    # is presentation; this is the actual control.
    if page["members_only"] and not (session and (session.is_member_now or is_admin)):
        return {
            "slug": page["slug"],
            "title": page["title"],
            "description": page.get("description"),
            "intro": page.get("intro", ""),
            "locked": True,
            "entries": [],
            "message": "Log in with your Imperial account to see this page.",
        }

    return {
        "id": page["id"],
        "slug": page["slug"],
        "title": page["title"],
        "description": page.get("description"),
        "intro": page.get("intro", ""),
        "entries": page.get("entries", []),
        "entryCount": page.get("entry_count", 0),
        "updatedAt": page.get("updated_at"),
        "locked": False,
    }


# ------------------------------------------------------------- admin: pages


@router.post("/admin/resources", dependencies=[Depends(require_action("resources.pages.create")), Depends(verify_csrf)])
async def create_page(
    body: dict = Body(...), session: AuthSession = Depends(require_resources)
) -> dict:
    if not (body.get("slug") or "").strip():
        raise HTTPException(400, "A page needs a slug")
    return await database.post(
        "/resource-pages", body, params={"updated_by": session.user.id}
    )


@router.put("/admin/resources/{page_id}", dependencies=[Depends(require_action("resources.pages.edit")), Depends(verify_csrf)])
async def update_page(
    page_id: int, body: dict = Body(...), session: AuthSession = Depends(require_resources)
) -> dict:
    return await database.put(
        f"/resource-pages/{page_id}", body, params={"updated_by": session.user.id}
    )


@router.delete("/admin/resources/{page_id}", dependencies=[Depends(require_action("resources.pages.delete")), Depends(verify_csrf)])
async def delete_page(page_id: int, _: AuthSession = Depends(require_resources)) -> dict:
    await database.delete(f"/resource-pages/{page_id}")
    return {"ok": True}


# ----------------------------------------------------------- admin: entries


@router.post("/admin/resource-entries", dependencies=[Depends(require_action("resources.entries.create")), Depends(verify_csrf)])
async def create_entry(
    body: dict = Body(...), _: AuthSession = Depends(require_resources)
) -> dict:
    if not body.get("page_id"):
        raise HTTPException(400, "An entry must belong to a page")
    return await database.post("/resource-entries", body)


@router.put("/admin/resource-entries/{entry_id}", dependencies=[Depends(require_action("resources.entries.edit")), Depends(verify_csrf)])
async def update_entry(
    entry_id: int, body: dict = Body(...), _: AuthSession = Depends(require_resources)
) -> dict:
    return await database.put(f"/resource-entries/{entry_id}", body)


@router.delete("/admin/resource-entries/{entry_id}", dependencies=[Depends(require_action("resources.entries.delete")), Depends(verify_csrf)])
async def delete_entry(entry_id: int, _: AuthSession = Depends(require_resources)) -> dict:
    await database.delete(f"/resource-entries/{entry_id}")
    return {"ok": True}


@router.post("/admin/resource-entries/{entry_id}/move", dependencies=[Depends(require_action("resources.entries.move")), Depends(verify_csrf)])
async def move_entry(
    entry_id: int,
    direction: int = Body(embed=True, default=-1),
    _: AuthSession = Depends(require_resources),
) -> dict:
    await database.post(
        f"/resource-entries/{entry_id}/move", params={"direction": direction}
    )
    return {"ok": True}


# ------------------------------------------------------------------- import


async def import_seed(force: bool = False) -> dict:
    """Load the content scraped from eesoc.com.

    Runs automatically on first boot when there are no pages, so a fresh
    deployment is not an empty site. `force` re-imports and overwrites, which is
    destructive to local edits — hence admin-only and never automatic.
    """
    if not SEED_PATH.exists():
        return {"ok": False, "error": f"No seed file at {SEED_PATH}"}

    existing = await database.get("/resource-pages/count")
    if existing and not force:
        return {"ok": True, "skipped": True, "pages": existing}

    payload = json.loads(SEED_PATH.read_text())
    results = []
    for page in payload:
        results.append(await database.post("/resource-pages/import", page))
    log.info("Imported %d resource pages from seed", len(results))
    return {"ok": True, "pages": results}


@router.post("/admin/resources/import", dependencies=[Depends(require_action("resources.import")), Depends(verify_csrf)])
async def trigger_import(
    force: bool = False, _: AuthSession = Depends(require_resources)
) -> dict:
    """Re-import from the bundled snapshot of eesoc.com.

    `force=true` overwrites every page in the seed, discarding edits made here.
    """
    return await import_seed(force=force)


# ------------------------------------------------------------------ uploads


@router.post("/admin/uploads", dependencies=[Depends(require_action("files.upload")), Depends(verify_csrf)])
async def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form("general"),
    session: AuthSession = Depends(require_files),
) -> dict:
    """Store a file on the media volume and record it.

    Returns a `url` usable directly as an event image, committee photo or
    sponsor logo — nginx serves it off the same volume without touching Python.
    """
    meta = await storage.save(file, purpose)
    row = await database.post(
        "/uploads", {**meta, "uploaded_by": session.user.id}
    )
    return {**row, "url": storage.public_url(row["stored_name"])}


@router.get("/admin/uploads", dependencies=[Depends(require_action("files.view"))])
async def list_uploads(
    purpose: str | None = None, _: AuthSession = Depends(require_files)
) -> dict:
    rows = await database.get("/uploads", {"purpose": purpose}) or []
    # Totals come from the recorded byte_size, not from the store: listing an
    # entire bucket on every dashboard load would be absurd.
    return {
        "uploads": [
            {**r, "url": storage.public_url(r["stored_name"])} for r in rows
        ],
        "usage": {
            "files": len(rows),
            "bytes": sum(r.get("byte_size") or 0 for r in rows),
            **storage.describe(),
        },
    }


@router.delete("/admin/uploads/{upload_id}", dependencies=[Depends(require_action("files.delete")), Depends(verify_csrf)])
async def delete_upload(upload_id: int, _: AuthSession = Depends(require_files)) -> dict:
    """Remove the row first, then the bytes.

    In that order a crash between the two leaves an orphaned file (harmless,
    reclaimable) rather than a database row pointing at nothing (a broken image
    on the site).
    """
    row = await database.delete(f"/uploads/{upload_id}")
    if row is None:
        raise HTTPException(404, "No such upload")
    await storage.delete(row["stored_name"])
    return {"ok": True}
