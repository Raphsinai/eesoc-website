"""Postgres persistence for photo print sets.

This adapter is internal-only; the externally reachable API enforces
photo-specific action permissions before calling these handlers.
"""
from __future__ import annotations

import json

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, HTTPException

from models.photos import PhotoSetCreate
from postgres import get_pool

router = APIRouter(prefix="/photo-sets")

COLUMNS = """
    s.id, s.token, s.title, s.orientation, s.status,
    s.created_by, s.created_at, s.published_at,
    COALESCE(jsonb_agg(
        jsonb_build_object(
            'id', u.id,
            'stored_name', u.stored_name,
            'original_name', u.original_name,
            'content_type', u.content_type,
            'position', p.position
        ) ORDER BY p.position
    ) FILTER (WHERE u.id IS NOT NULL), '[]'::jsonb) AS images
"""
FROM = """
    public.photo_sets s
    LEFT JOIN public.photo_set_images p ON p.set_id = s.id
    LEFT JOIN public.uploads u ON u.id = p.upload_id
"""


def _serialise(row) -> dict:
    result = dict(row)
    # asyncpg returns jsonb aggregates as JSON strings unless an explicit codec
    # is registered. The API expects a list, not that string's characters.
    if isinstance(result.get("images"), str):
        result["images"] = json.loads(result["images"])
    return result


@router.get("")
async def list_sets(limit: int = 60) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM {FROM} GROUP BY s.id "
            "ORDER BY s.created_at DESC, s.id DESC LIMIT $1;",
            max(1, min(limit, 200)),
        )
    return [_serialise(row) for row in rows]


@router.post("", status_code=201)
async def create_set(payload: PhotoSetCreate) -> dict:
    ids = payload.image_ids
    if len(set(ids)) != len(ids) or any(i <= 0 for i in ids):
        raise HTTPException(400, "Choose 1-4 different photos")
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Own, unused photo uploads only; lock to prevent concurrent reuse.
        rows = await conn.fetch(
            """
            SELECT u.id FROM public.uploads u
             WHERE u.id = ANY($1::bigint[])
               AND u.uploaded_by = $2
               AND u.purpose = 'photo_print'
               AND NOT EXISTS (
                   SELECT 1 FROM public.photo_set_images p WHERE p.upload_id = u.id
               )
             FOR UPDATE OF u;
            """,
            ids, payload.created_by,
        )
        if len(rows) != len(ids):
            raise HTTPException(400, "Some photos are unavailable or already used")
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.photo_sets
                    (token, title, orientation, created_by)
                VALUES ($1, $2, $3, $4)
                RETURNING id;
                """,
                payload.token, payload.title.strip(), payload.orientation, payload.created_by,
            )
            for position, image_id in enumerate(ids):
                await conn.execute(
                    "INSERT INTO public.photo_set_images (set_id, upload_id, position) "
                    "VALUES ($1, $2, $3);",
                    row["id"], image_id, position,
                )
        except UniqueViolationError:
            raise HTTPException(409, "Photo set already exists or an image is in use")
        found = await conn.fetchrow(
            f"SELECT {COLUMNS} FROM {FROM} WHERE s.id=$1 GROUP BY s.id;",
            row["id"],
        )
    return _serialise(found)


@router.delete("/orphan-uploads/{upload_id}")
async def delete_orphan_upload(upload_id: int, uploaded_by: int) -> dict:
    """Remove only a user's temporary photo upload not linked to any set."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM public.uploads u
            WHERE u.id=$1 AND u.uploaded_by=$2 AND u.purpose='photo_print'
              AND NOT EXISTS (
                  SELECT 1 FROM public.photo_set_images p
                  WHERE p.upload_id=u.id
              )
            RETURNING stored_name;
            """,
            upload_id, uploaded_by,
        )
    if not row:
        raise HTTPException(404, "Temporary upload not found")
    return dict(row)


@router.get("/by-token/{token}")
async def get_by_token(token: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {COLUMNS} FROM {FROM} WHERE s.token=$1 GROUP BY s.id;",
            token,
        )
    if row is None:
        raise HTTPException(404, "Photo set not found")
    return _serialise(row)


@router.get("/{set_id}")
async def get_set(set_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {COLUMNS} FROM {FROM} WHERE s.id=$1 GROUP BY s.id;",
            set_id,
        )
    if row is None:
        raise HTTPException(404, "Photo set not found")
    return _serialise(row)


@router.post("/{set_id}/publish")
async def publish(set_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.photo_sets SET
                status='published', published_at=COALESCE(published_at, NOW())
             WHERE id=$1 RETURNING id;
            """,
            set_id,
        )
    if row is None:
        raise HTTPException(404, "Photo set not found")
    return await get_set(set_id)


@router.post("/{set_id}/unpublish")
async def unpublish(set_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE public.photo_sets SET status='draft' WHERE id=$1 RETURNING id;",
            set_id,
        )
    if row is None:
        raise HTTPException(404, "Photo set not found")
    return await get_set(set_id)


@router.delete("/{set_id}")
async def delete_set(set_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        exists = await conn.fetchval(
            "SELECT id FROM public.photo_sets WHERE id=$1 FOR UPDATE;", set_id,
        )
        if exists is None:
            raise HTTPException(404, "Photo set not found")
        row = await conn.fetchrow(
            f"SELECT {COLUMNS} FROM {FROM} WHERE s.id=$1 GROUP BY s.id;",
            set_id,
        )
        await conn.execute("DELETE FROM public.photo_sets WHERE id=$1;", set_id)
    return _serialise(row)
