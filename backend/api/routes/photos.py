"""EESoc event photo studio: committee drafts and QR-linked public downloads.

Each public photo set has an unpredictable token. No album indexes or lists are
public, and a draft has no public representation (even with its token).
"""
from __future__ import annotations

import asyncio
import io
import logging
import secrets
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from clients import database
from config import settings
from models.auth import AuthSession
from services import photo_print, storage
from utils.deps import require_action, require_session, verify_csrf

router = APIRouter()
log = logging.getLogger("api.photos")


class CreatePhotos(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    orientation: Literal["portrait", "landscape"] = "portrait"
    image_ids: list[int] = Field(min_length=1, max_length=4)


def _page_url(row: dict) -> str:
    return f"{settings.site_origin.rstrip('/')}/photos/{row['token']}"


def _present(row: dict) -> dict:
    images = [
        {
            "id": i["id"],
            "url": storage.public_url(i["stored_name"]),
            "filename": f"eesoc-photo-{n}.jpg",
        }
        for n, i in enumerate(row.get("images") or [], 1)
    ]
    result = {
        "id": row["id"],
        "title": row["title"],
        "orientation": row["orientation"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "publishedAt": row.get("published_at"),
        "images": images,
        "count": len(images),
    }
    if row["status"] == "published":
        result["url"] = _page_url(row)
        result["printUrl"] = f"/api/photos/{row['token']}/print.png"
        result["downloadUrl"] = f"/api/photos/{row['token']}/download.zip"
    return result


async def _published(token: str) -> dict:
    row = await database.get(f"/photo-sets/by-token/{token}")
    if not row or row["status"] != "published":
        raise HTTPException(404, "Photo page not found")
    return row


async def _print(row: dict) -> Response:
    images = row.get("images") or []
    photos = await asyncio.gather(*(storage.load(i["stored_name"]) for i in images))
    image = await asyncio.to_thread(
        photo_print.render, photos, row["title"], _page_url(row), row["orientation"],
    )
    return Response(
        image, media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="eesoc-photo-print.png"',
        },
    )


# Public, unlisted, token-only. Neither a sign-in nor a committee grant is needed.
@router.get("/photos/{token}")
async def public_photo_set(token: str) -> dict:
    return _present(await _published(token))


@router.get("/photos/{token}/print.png")
async def public_print(token: str) -> Response:
    return await _print(await _published(token))


@router.get("/photos/{token}/download.zip")
async def download_photos(token: str) -> Response:
    row = await _published(token)
    data = await asyncio.gather(
        *(storage.load(i["stored_name"]) for i in row["images"])
    )

    def _zip() -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
            for n, photo in enumerate(data, 1):
                archive.writestr(f"eesoc-photo-{n}.jpg", photo)
        return out.getvalue()

    return Response(
        await asyncio.to_thread(_zip), media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="eesoc-photos.zip"',
        },
    )


@router.get(
    "/admin/photos/sets", dependencies=[Depends(require_action("photos.view"))],
)
async def admin_list_sets() -> dict:
    rows = await database.get("/photo-sets") or []
    return {"sets": [_present(row) for row in rows]}


@router.get(
    "/admin/photos/sets/{set_id}",
    dependencies=[Depends(require_action("photos.view"))],
)
async def admin_get_set(set_id: int) -> dict:
    row = await database.get(f"/photo-sets/{set_id}")
    if not row:
        raise HTTPException(404, "Photo set not found")
    return _present(row)


@router.get(
    "/admin/photos/sets/{set_id}/preview.png",
    dependencies=[Depends(require_action("photos.view"))],
)
async def admin_preview(set_id: int) -> Response:
    row = await database.get(f"/photo-sets/{set_id}")
    if not row:
        raise HTTPException(404, "Photo set not found")
    return await _print(row)


@router.post(
    "/admin/photos/uploads",
    dependencies=[Depends(require_action("photos.upload")), Depends(verify_csrf)],
)
async def upload_photo(
    file: UploadFile = File(...),
    session: AuthSession = Depends(require_session),
) -> dict:
    # One upload per request, so 4 full-resolution JPEGs do not exceed the
    # nginx 12 MB request cap when somebody makes a four-photo collage.
    meta = await storage.save(file, "photo_print", photo_mode=True)
    try:
        row = await database.post("/uploads", {**meta, "uploaded_by": session.user.id})
    except Exception:
        await storage.delete(meta["stored_name"])
        raise
    return {"id": row["id"], "url": storage.public_url(row["stored_name"])}


@router.delete(
    "/admin/photos/uploads/{upload_id}",
    dependencies=[Depends(require_action("photos.upload")), Depends(verify_csrf)],
)
async def cancel_photo_upload(
    upload_id: int,
    session: AuthSession = Depends(require_session),
) -> dict:
    """Clean up an incomplete set, without touching another person's images."""
    row = await database.delete(
        f"/photo-sets/orphan-uploads/{upload_id}",
        params={"uploaded_by": session.user.id},
    )
    if not row:
        raise HTTPException(404, "Temporary upload not found")
    if not await storage.delete(row["stored_name"]):
        log.warning("Orphan photo object cleanup failed for upload %s", upload_id)
    return {"ok": True}


@router.post(
    "/admin/photos/sets",
    dependencies=[Depends(require_action("photos.create")), Depends(verify_csrf)],
)
async def create_set(
    payload: CreatePhotos,
    session: AuthSession = Depends(require_session),
) -> dict:
    if len(set(payload.image_ids)) != len(payload.image_ids):
        raise HTTPException(400, "Select different photos")
    if not payload.title.strip():
        raise HTTPException(400, "Enter a title")
    row = await database.post(
        "/photo-sets",
        {
            **payload.model_dump(),
            "title": payload.title.strip(),
            "token": secrets.token_urlsafe(24),
            "created_by": session.user.id,
        },
    )
    return _present(row)


@router.post(
    "/admin/photos/sets/{set_id}/publish",
    dependencies=[Depends(require_action("photos.publish")), Depends(verify_csrf)],
)
async def publish_set(set_id: int) -> dict:
    row = await database.post(f"/photo-sets/{set_id}/publish")
    if not row:
        raise HTTPException(404, "Photo set not found")
    return _present(row)


@router.post(
    "/admin/photos/sets/{set_id}/unpublish",
    dependencies=[Depends(require_action("photos.unpublish")), Depends(verify_csrf)],
)
async def unpublish_set(set_id: int) -> dict:
    row = await database.post(f"/photo-sets/{set_id}/unpublish")
    if not row:
        raise HTTPException(404, "Photo set not found")
    return _present(row)


@router.delete(
    "/admin/photos/sets/{set_id}",
    dependencies=[Depends(require_action("photos.delete")), Depends(verify_csrf)],
)
async def delete_set(set_id: int) -> dict:
    row = await database.delete(f"/photo-sets/{set_id}")
    if not row:
        raise HTTPException(404, "Photo set not found")
    # Delete the metadata after unlinking the set, then remove each object.
    # Failed object deletion is logged rather than breaking the now-deleted set.
    for image in row.get("images") or []:
        try:
            await database.delete(f"/uploads/{image['id']}")
            if not await storage.delete(image["stored_name"]):
                log.warning("Could not delete photo object from set %s", set_id)
        except Exception:
            log.exception("Photo upload cleanup failed for set %s", set_id)
    return {"ok": True}
