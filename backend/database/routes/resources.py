"""Resource pages, their entry trees, and upload metadata."""

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from models.resources import (
    ResourceEntry,
    ResourceEntryNode,
    ResourceImport,
    ResourcePage,
    ResourcePageDetail,
    Upload,
)
from postgres import get_pool

router = APIRouter()

PAGE_COLUMNS = """
    id, slug, title, description, intro, icon, sort_order, published,
    members_only, updated_at
"""
ENTRY_COLUMNS = "id, page_id, parent_id, kind, title, body, url, badge, sort_order"


def _build_tree(rows) -> list[ResourceEntryNode]:
    """Flat rows -> nested nodes, in one pass.

    Children are attached by identity, so the ordering the query produced is
    preserved at every level without a second sort.
    """
    nodes: dict[int, ResourceEntryNode] = {}
    roots: list[ResourceEntryNode] = []
    for r in rows:
        nodes[r["id"]] = ResourceEntryNode(**dict(r), children=[])
    for r in rows:
        node = nodes[r["id"]]
        parent = nodes.get(r["parent_id"]) if r["parent_id"] else None
        (parent.children if parent else roots).append(node)
    return roots


# --------------------------------------------------------------------- pages


@router.get("/resource-pages")
async def list_pages(published_only: bool = True) -> list[ResourcePage]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {PAGE_COLUMNS} FROM public.resource_pages
             WHERE ($1::bool IS NOT TRUE OR published)
             ORDER BY sort_order, title;
            """,
            published_only,
        )
    return [ResourcePage(**dict(r)) for r in rows]


@router.get("/resource-pages/by-slug/{slug}")
async def get_page(slug: str) -> ResourcePageDetail:
    pool = await get_pool()
    async with pool.acquire() as conn:
        page = await conn.fetchrow(
            f"SELECT {PAGE_COLUMNS} FROM public.resource_pages WHERE slug = $1;", slug
        )
        if page is None:
            raise HTTPException(404, "No such resource page")
        rows = await conn.fetch(
            f"""
            SELECT {ENTRY_COLUMNS} FROM public.resource_entries
             WHERE page_id = $1
             ORDER BY sort_order, id;
            """,
            page["id"],
        )
    return ResourcePageDetail(**dict(page), entries=_build_tree(rows), entry_count=len(rows))


@router.post("/resource-pages", status_code=201)
async def create_page(page: ResourcePage, updated_by: int | None = None) -> ResourcePage:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO public.resource_pages
                    (slug, title, description, intro, icon, sort_order, published,
                     members_only, updated_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                RETURNING {PAGE_COLUMNS};
                """,
                page.slug, page.title, page.description, page.intro, page.icon,
                page.sort_order, page.published, page.members_only, updated_by,
            )
        except UniqueViolationError:
            raise HTTPException(409, f"A page with slug '{page.slug}' already exists")
    return ResourcePage(**dict(row))


@router.put("/resource-pages/{page_id}")
async def update_page(page_id: int, page: ResourcePage, updated_by: int | None = None) -> ResourcePage:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.resource_pages SET
                slug=$2, title=$3, description=$4, intro=$5, icon=$6, sort_order=$7,
                published=$8, members_only=$9, updated_by=$10, updated_at=NOW()
             WHERE id=$1
            RETURNING {PAGE_COLUMNS};
            """,
            page_id, page.slug, page.title, page.description, page.intro, page.icon,
            page.sort_order, page.published, page.members_only, updated_by,
        )
    if row is None:
        raise HTTPException(404, "No such resource page")
    return ResourcePage(**dict(row))


@router.delete("/resource-pages/{page_id}", status_code=204)
async def delete_page(page_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.resource_pages WHERE id = $1;", page_id)


# ------------------------------------------------------------------- entries


@router.post("/resource-entries", status_code=201)
async def create_entry(entry: ResourceEntry) -> ResourceEntry:
    """New nodes land at the end of their level unless told otherwise, so adding
    something never reshuffles what is already there."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if entry.sort_order == 100:
            entry.sort_order = (
                await conn.fetchval(
                    """
                    SELECT COALESCE(MAX(sort_order), 0) + 10
                      FROM public.resource_entries
                     WHERE page_id = $1 AND parent_id IS NOT DISTINCT FROM $2;
                    """,
                    entry.page_id,
                    entry.parent_id,
                )
                or 10
            )
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.resource_entries
                (page_id, parent_id, kind, title, body, url, badge, sort_order)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING {ENTRY_COLUMNS};
            """,
            entry.page_id, entry.parent_id, entry.kind, entry.title, entry.body,
            entry.url, entry.badge, entry.sort_order,
        )
    return ResourceEntry(**dict(row))


@router.put("/resource-entries/{entry_id}")
async def update_entry(entry_id: int, entry: ResourceEntry) -> ResourceEntry:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.resource_entries SET
                parent_id=$2, kind=$3, title=$4, body=$5, url=$6, badge=$7,
                sort_order=$8, updated_at=NOW()
             WHERE id=$1
            RETURNING {ENTRY_COLUMNS};
            """,
            entry_id, entry.parent_id, entry.kind, entry.title, entry.body,
            entry.url, entry.badge, entry.sort_order,
        )
    if row is None:
        raise HTTPException(404, "No such entry")
    return ResourceEntry(**dict(row))


@router.delete("/resource-entries/{entry_id}", status_code=204)
async def delete_entry(entry_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.resource_entries WHERE id = $1;", entry_id)


@router.post("/resource-entries/{entry_id}/move")
async def move_entry(entry_id: int, direction: int) -> list[ResourceEntry]:
    """Swap an entry with its neighbour at the same level.

    Swapping two sort_orders is enough and avoids renumbering a whole level,
    which would fight with concurrent edits.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        me = await conn.fetchrow(
            f"SELECT {ENTRY_COLUMNS} FROM public.resource_entries WHERE id = $1 FOR UPDATE;",
            entry_id,
        )
        if me is None:
            raise HTTPException(404, "No such entry")

        comparison = "<" if direction < 0 else ">"
        order = "DESC" if direction < 0 else "ASC"
        neighbour = await conn.fetchrow(
            f"""
            SELECT {ENTRY_COLUMNS} FROM public.resource_entries
             WHERE page_id = $1
               AND parent_id IS NOT DISTINCT FROM $2
               AND (sort_order, id) {comparison} ($3, $4)
             ORDER BY sort_order {order}, id {order}
             LIMIT 1
            FOR UPDATE;
            """,
            me["page_id"], me["parent_id"], me["sort_order"], me["id"],
        )
        if neighbour is None:
            return [ResourceEntry(**dict(me))]

        await conn.execute(
            "UPDATE public.resource_entries SET sort_order=$2 WHERE id=$1;",
            me["id"], neighbour["sort_order"],
        )
        await conn.execute(
            "UPDATE public.resource_entries SET sort_order=$2 WHERE id=$1;",
            neighbour["id"], me["sort_order"],
        )
    return [
        ResourceEntry(**{**dict(me), "sort_order": neighbour["sort_order"]}),
        ResourceEntry(**{**dict(neighbour), "sort_order": me["sort_order"]}),
    ]


# -------------------------------------------------------------------- import


@router.post("/resource-pages/import")
async def import_page(payload: ResourceImport) -> dict:
    """Replace a page and its whole tree in one transaction.

    Used to seed from the current eesoc.com content. Idempotent: re-importing
    the same slug wipes and rebuilds rather than duplicating, so it can be run
    again after the source changes.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        page_id = await conn.fetchval(
            """
            INSERT INTO public.resource_pages (slug, title, description, intro, sort_order)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (slug) DO UPDATE SET
                title=EXCLUDED.title, description=EXCLUDED.description,
                intro=EXCLUDED.intro, sort_order=EXCLUDED.sort_order, updated_at=NOW()
            RETURNING id;
            """,
            payload.slug, payload.title, payload.description, payload.intro,
            payload.sort_order,
        )
        await conn.execute(
            "DELETE FROM public.resource_entries WHERE page_id = $1;", page_id
        )

        # Entries arrive flat, each naming its parent by list index; ids only
        # exist once inserted, so map index -> id as we go. The source order is
        # depth-first, so a parent is always inserted before its children.
        ids: dict[int, int] = {}
        for index, entry in enumerate(payload.entries):
            parent_index = entry.get("parent")
            new_id = await conn.fetchval(
                """
                INSERT INTO public.resource_entries
                    (page_id, parent_id, kind, title, body, url, badge, sort_order)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                RETURNING id;
                """,
                page_id,
                ids.get(parent_index) if parent_index is not None else None,
                entry.get("kind", "item"),
                entry.get("title", "Untitled"),
                entry.get("body", ""),
                entry.get("url"),
                entry.get("badge"),
                (index + 1) * 10,
            )
            ids[index] = new_id

    return {"slug": payload.slug, "page_id": page_id, "entries": len(payload.entries)}


@router.get("/resource-pages/count")
async def page_count() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM public.resource_pages;")


# ------------------------------------------------------------------- uploads


@router.post("/uploads", status_code=201)
async def record_upload(upload: Upload) -> Upload:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.uploads
                (stored_name, original_name, content_type, byte_size, purpose, uploaded_by)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id, stored_name, original_name, content_type, byte_size,
                      purpose, uploaded_by, uploaded_at;
            """,
            upload.stored_name, upload.original_name, upload.content_type,
            upload.byte_size, upload.purpose, upload.uploaded_by,
        )
    return Upload(**dict(row))


@router.get("/uploads")
async def list_uploads(purpose: str | None = None, limit: int = 200) -> list[Upload]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, stored_name, original_name, content_type, byte_size,
                   purpose, uploaded_by, uploaded_at
              FROM public.uploads
             WHERE ($1::varchar IS NULL OR purpose = $1)
             ORDER BY uploaded_at DESC LIMIT $2;
            """,
            purpose, limit,
        )
    return [Upload(**dict(r)) for r in rows]


@router.delete("/uploads/{upload_id}")
async def delete_upload(upload_id: int) -> Upload:
    """Returns the row so the caller can unlink the file from the volume — the
    database has no idea where the bytes live."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM public.uploads WHERE id = $1
            RETURNING id, stored_name, original_name, content_type, byte_size,
                      purpose, uploaded_by, uploaded_at;
            """,
            upload_id,
        )
    if row is None:
        raise HTTPException(404, "No such upload")
    return Upload(**dict(row))
