"""Internal SQL-backed bulk import and committee review of Union sales exports.

Every state change and audit entry share a transaction; importing the same file
again is idempotent. No fuzzy matches are ever approved automatically.
"""
from __future__ import annotations

from hashlib import sha256
import json

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from postgres import get_pool
from sale_matching import choose_reservation

router = APIRouter(prefix='/product-sales')


class SaleRow(BaseModel):
    source_row: int = Field(gt=0)
    cid: str | None = None
    buyer_name: str | None = None
    email: str | None = None
    order_number: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)
    product_line_id: int | None = None


class ImportRequest(BaseModel):
    filename: str
    file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    product_id: int = Field(gt=0)
    ticket_type_id: int = Field(gt=0)
    uploaded_by: int
    rows: list[SaleRow] = Field(min_length=1, max_length=5000)


@router.get('/targets')
async def targets() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT DISTINCT pl.union_product_id AS product_id,
                      e.id AS event_id, e.title AS event_title,
                      tt.id AS ticket_type_id, tt.name AS ticket_type_name,
                      pl.union_product_line_id AS product_line_id,
                      pl.ticket_type_id IS NOT NULL AS direct_link,
                      e.industry AS industry
                 FROM public.product_links pl
                 JOIN public.events e ON e.id=pl.event_id
                 JOIN public.ticket_types tt ON tt.event_id=e.id
                    AND (pl.ticket_type_id IS NULL OR tt.id=pl.ticket_type_id)
                WHERE pl.purpose='event_ticket'
                ORDER BY product_id, event_id, ticket_type_id""")
    return [dict(row) for row in rows]


async def _target(conn, product_id: int, ticket_type_id: int) -> dict:
    row = await conn.fetchrow("""SELECT tt.id AS ticket_type_id, tt.event_id, e.title AS event_title,
                  tt.name AS ticket_type_name, e.industry
             FROM public.ticket_types tt JOIN public.events e ON e.id=tt.event_id
            WHERE tt.id=$2 AND EXISTS
                (SELECT 1 FROM public.product_links pl
                  WHERE pl.purpose='event_ticket' AND pl.union_product_id=$1
                    AND pl.event_id=e.id
                    AND (pl.ticket_type_id IS NULL OR pl.ticket_type_id=tt.id))""",
        product_id, ticket_type_id)
    if row is None:
        raise HTTPException(400, 'Product ID is not mapped to that ticket type')
    return dict(row)


async def _existing(conn, ticket_type_id: int) -> list[dict]:
    rows = await conn.fetch("""SELECT r.id, r.status, r.quantity,
                      m.cid AS cid,
                      trim(concat_ws(' ', coalesce(nullif(m.first_name, ''), u.first_name),
                                          coalesce(nullif(m.last_name, ''), u.last_name))) AS name
                 FROM public.reservations r
                 JOIN public.users u ON u.id=r.user_id
                 LEFT JOIN public.members m ON m.id=u.member_id
                WHERE r.ticket_type_id=$1 AND r.status IN ('held','confirmed')
                ORDER BY r.id FOR UPDATE OF r""", ticket_type_id)
    return [dict(row) for row in rows]


@router.post('/imports')
async def create_import(request: ImportRequest) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        target = await _target(conn, request.product_id, request.ticket_type_id)
        old = await conn.fetchrow('SELECT id FROM public.product_sales_imports WHERE file_sha256=$1', request.file_sha256)
        if old:
            return {'id': old['id'], 'existing': True, 'event_id': target['event_id']}
        if len({r.source_row for r in request.rows}) != len(request.rows):
            raise HTTPException(400, 'Duplicate row numbers in upload')
        batch_id = await conn.fetchval("""INSERT INTO public.product_sales_imports
            (file_sha256, filename, product_id, event_id, ticket_type_id, uploaded_by)
            VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (file_sha256) DO NOTHING RETURNING id""",
            request.file_sha256, request.filename[:255], request.product_id,
            target['event_id'], request.ticket_type_id, request.uploaded_by)
        if batch_id is None:
            same = await conn.fetchval('SELECT id FROM public.product_sales_imports WHERE file_sha256=$1', request.file_sha256)
            return {'id': same, 'existing': True, 'event_id': target['event_id']}
        reservations = await _existing(conn, request.ticket_type_id)
        already_used: set[int] = set()
        counts = dict(confirmed=0, review=0, unreserved=0, duplicate=0)
        for sale in request.rows:
            if sale.product_line_id is not None:
                allowed = await conn.fetchval("""SELECT EXISTS(
                    SELECT 1 FROM public.product_links pl
                     WHERE pl.purpose='event_ticket' AND pl.union_product_id=$1
                       AND pl.event_id=$2 AND (pl.ticket_type_id=$3 OR pl.ticket_type_id IS NULL)
                       AND (pl.union_product_line_id IS NULL OR pl.union_product_line_id=$4))""",
                    request.product_id, target['event_id'], request.ticket_type_id, sale.product_line_id)
                if not allowed:
                    raise HTTPException(400, f'Row {sale.source_row}: product line not linked to selected ticket')
            key_source = (f"{request.product_id}:{sale.product_line_id or 0}:{sale.order_number.casefold().strip()}"
                          if sale.order_number else f"{request.file_sha256}:{sale.source_row}")
            key = sha256(key_source.encode()).hexdigest()
            duplicate = await conn.fetchval("""SELECT EXISTS(
                SELECT 1 FROM public.product_sales_import_rows
                 WHERE source_key=$1 AND status IN ('confirmed','review','unreserved','ticket_added'))""", key)
            # Prevent reuse of an order already tied to a confirmed reservation.
            if sale.order_number and not duplicate:
                duplicate = await conn.fetchval("""SELECT EXISTS(
                    SELECT 1 FROM public.reservations confirmed
                    WHERE confirmed.event_id=$2 AND confirmed.status='confirmed'
                      AND upper(confirmed.order_number)=upper($1))""",
                    sale.order_number, target['event_id'])
            if duplicate:
                status, suggestion, suggestions, reason = 'duplicate', None, [], 'previously_imported_sale'
            else:
                status, suggestion, suggestions, reason = choose_reservation(sale, reservations, already_used)
            linked_id = None
            if status == 'confirmed' and suggestion is not None:
                linked_id = await conn.fetchval("""UPDATE public.reservations SET status='confirmed',
                    confirmed_at=NOW(), order_number=COALESCE($2,order_number)
                    WHERE id=$1 AND status='held' RETURNING id""", suggestion['id'], sale.order_number)
                if linked_id is None:
                    status, reason = 'review', 'concurrent_confirmation'
                else:
                    already_used.add(linked_id)
            await conn.execute("""INSERT INTO public.product_sales_import_rows
                (import_id, source_row, source_key, cid, buyer_name, email,
                 order_number, quantity, product_line_id, ticket_type_id,
                 status, matched_on, suggested_reservation_id, suggestions, reservation_id, detail)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15,$16)""",
                batch_id, sale.source_row, key, sale.cid, sale.buyer_name,
                sale.email, sale.order_number, sale.quantity, sale.product_line_id,
                request.ticket_type_id, status, reason,
                suggestion['id'] if suggestion else None, json.dumps(suggestions), linked_id, reason)
            counts[status] += 1
            if linked_id:
                await conn.execute("""INSERT INTO public.action_audit
                    (actor_user_id,action,entity_type,entity_id,after_data)
                    VALUES ($1,'reservations.sale_import.confirm','reservation',$2,
                        jsonb_build_object('batch_id',$3::bigint,'source_row',$4::integer,'match',$5::text))""",
                    request.uploaded_by, linked_id, batch_id, sale.source_row, reason)
        await conn.execute("""INSERT INTO public.action_audit
            (actor_user_id,action,entity_type,entity_id,after_data)
            VALUES ($1,'reservations.sale_import.upload','product_sales_import',$2,
                jsonb_build_object('product_id',$3::bigint,'ticket_type_id',$4::bigint,
                                   'rows',$5::integer))""",
            request.uploaded_by, batch_id, request.product_id,
            request.ticket_type_id, len(request.rows))
    return {'id': batch_id, 'existing': False, 'event_id': target['event_id'], 'counts': counts}


@router.get('/imports')
async def list_imports(event_id: int | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch("""SELECT i.id,i.filename,i.product_id,i.event_id,i.ticket_type_id,
                i.uploaded_by,i.uploaded_at,u.email AS uploaded_by_email,
                count(r.id)::integer AS total,
                count(*) FILTER(WHERE r.status='confirmed')::integer AS confirmed,
                count(*) FILTER(WHERE r.status='review')::integer AS review,
                count(*) FILTER(WHERE r.status='unreserved')::integer AS unreserved,
                count(*) FILTER(WHERE r.status='ticket_added')::integer AS ticket_added
            FROM public.product_sales_imports i
            JOIN public.product_sales_import_rows r ON r.import_id=i.id
            LEFT JOIN public.users u ON u.id=i.uploaded_by
            WHERE ($1::bigint IS NULL OR i.event_id=$1)
            GROUP BY i.id,u.email ORDER BY i.id DESC LIMIT 100""", event_id)
    return [dict(item) for item in records]


@router.get('/rows')
async def list_rows(event_id: int, status: str | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT r.*,i.event_id,i.product_id,i.filename,
                tt.name AS ticket_type_name
              FROM public.product_sales_import_rows r
              JOIN public.product_sales_imports i ON i.id=r.import_id
              JOIN public.ticket_types tt ON tt.id=r.ticket_type_id
             WHERE i.event_id=$1 AND ($2::varchar IS NULL OR r.status=$2)
             ORDER BY r.id DESC LIMIT 2000""", event_id, status)
    return [decode_row(dict(row)) for row in rows]


def decode_row(row: dict) -> dict:
    if isinstance(row.get('suggestions'), str):
        row['suggestions'] = json.loads(row['suggestions'])
    return row


@router.get('/rows/{row_id}')
async def one_row(row_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""SELECT r.*,i.event_id,i.product_id
             FROM public.product_sales_import_rows r
             JOIN public.product_sales_imports i ON i.id=r.import_id WHERE r.id=$1""", row_id)
    if row is None:
        raise HTTPException(404, 'Import sale not found')
    return decode_row(dict(row))


@router.post('/rows/{row_id}/approve')
async def approve_row(row_id: int, reservation_id: int = Body(embed=True), actor_id: int = Body(embed=True)) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("""SELECT r.*,i.event_id FROM public.product_sales_import_rows r
            JOIN public.product_sales_imports i ON i.id=r.import_id
            WHERE r.id=$1 FOR UPDATE OF r""", row_id)
        if row is None or row['status'] != 'review':
            raise HTTPException(409, 'Sale is no longer awaiting approval')
        suggestions = json.loads(row['suggestions']) if isinstance(row['suggestions'], str) else row['suggestions']
        if reservation_id not in [v['id'] for v in suggestions]:
            raise HTTPException(400, 'Choose a reservation from the suggested matches')
        reservation = await conn.fetchrow("SELECT id, ticket_type_id, event_id, status, quantity FROM public.reservations WHERE id=$1 FOR UPDATE", reservation_id)
        if (reservation is None or reservation['event_id'] != row['event_id'] or
            reservation['ticket_type_id'] != row['ticket_type_id'] or
            reservation['status'] != 'held'):
            raise HTTPException(409, 'Reservation is not eligible for this sale')
        if reservation['quantity'] != row['quantity']:
            raise HTTPException(409, 'Sale and reservation quantities differ. Resolve quantities before confirming.')
        await conn.execute("""UPDATE public.reservations SET status='confirmed', confirmed_at=NOW(),
            order_number=COALESCE($2,order_number) WHERE id=$1""", reservation_id, row['order_number'])
        await conn.execute("""UPDATE public.product_sales_import_rows SET status='confirmed',
            reservation_id=$2,reviewed_by=$3,reviewed_at=NOW(),matched_on='committee_approved'
            WHERE id=$1""", row_id, reservation_id, actor_id)
        await conn.execute("""INSERT INTO public.action_audit(actor_user_id,action,entity_type,entity_id,after_data)
            VALUES ($1,'reservations.sale_import.approve','reservation',$2,
                    jsonb_build_object('import_row_id',$3::bigint))""", actor_id, reservation_id, row_id)
    return {'ok': True, 'reservation_id': reservation_id}
