"""Internal SQL-backed bulk import and committee review of Union sales exports.

Every state change and audit entry share a transaction; importing the same file
again is idempotent. No fuzzy matches are ever approved automatically.
"""
from __future__ import annotations

import json
from difflib import SequenceMatcher

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from postgres import get_pool
from sale_matching import choose_reservation, sale_source_key

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


async def _record_verified_import_order(conn, reservation_id: int, order_number: str | None,
                                        actor_id: int) -> None:
    """An accepted Union sale is valid evidence for a pending committee claim.

    The daily Union sync normally settles these claims. If the committee
    imports the actual sales export first, mark a matching claim as verified
    now instead of later rejecting an already-confirmed booking.
    """
    if not order_number:
        return
    matched = await conn.fetchval("""UPDATE public.reservation_order_verification
        SET status='matched', checked_at=NOW()
        WHERE reservation_id=$1 AND status='pending'
          AND upper(btrim(order_number))=upper(btrim($2::text))
        RETURNING reservation_id""", reservation_id, order_number)
    if matched:
        await conn.execute("""INSERT INTO public.action_audit
            (actor_user_id, action, entity_type, entity_id, after_data)
            VALUES ($1, 'reservations.order.matched', 'reservation', $2,
                jsonb_build_object('order_number',$3::text,'source','committee_sale_import'))""",
            actor_id, reservation_id, order_number)


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
            key = sale_source_key(request.product_id, request.ticket_type_id,
                                  sale, request.file_sha256)
            duplicate = await conn.fetchval("""SELECT EXISTS(
                SELECT 1 FROM public.product_sales_import_rows
                 WHERE source_key=$1 AND status IN ('confirmed','review','unreserved','ticket_added'))""", key)
            # Prevent reuse of an order already tied to a confirmed reservation.
            if sale.order_number and not duplicate:
                duplicate = await conn.fetchval("""SELECT EXISTS(
                    SELECT 1 FROM public.reservations confirmed
                    WHERE confirmed.event_id=$2 AND confirmed.ticket_type_id=$3
                      AND confirmed.status='confirmed'
                      AND upper(confirmed.order_number)=upper($1))""",
                    sale.order_number, target['event_id'], request.ticket_type_id)
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
                    await _record_verified_import_order(conn, linked_id, sale.order_number,
                                                        request.uploaded_by)
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


@router.get('/rows/{row_id}/candidates')
async def reservation_candidates(row_id: int, q: str = '', limit: int = 30) -> dict:
    """Find eligible reservations for an imported sale, never automatically link.

    The import can be 'review' or 'unreserved'; lack of an automatic suggestion
    must not prevent a committee officer from finding an existing reservation.
    Results are scoped to the imported row's event and exact ticket type.
    """
    if len(q) > 120 or limit < 1 or limit > 100:
        raise HTTPException(400, 'Invalid reservation search')
    pool = await get_pool()
    async with pool.acquire() as conn:
        sale = await conn.fetchrow("""SELECT r.status,r.quantity,r.buyer_name,r.cid,
                    r.email,i.event_id,r.ticket_type_id
                FROM public.product_sales_import_rows r
                JOIN public.product_sales_imports i ON i.id=r.import_id
                WHERE r.id=$1""", row_id)
        if sale is None:
            raise HTTPException(404, 'Imported sale not found')
        if sale['status'] not in ('review', 'unreserved'):
            raise HTTPException(409, 'This sale has already been handled')
        search = q.strip()
        # Use a literal substring search, not an attacker-supplied SQL wildcard.
        escape = chr(92)
        needle = ('%' + search.replace(escape, escape * 2)
                  .replace('%', escape + '%').replace('_', escape + '_') + '%') if search else None
        candidates = await conn.fetch("""SELECT r.id,r.quantity,r.status,r.created_at,
                       r.order_number,u.email, u.first_name,u.last_name,
                       m.cid,m.first_name AS member_first_name,m.last_name AS member_last_name
                    FROM public.reservations r
                    JOIN public.users u ON u.id=r.user_id
                    LEFT JOIN public.members m ON m.id=u.member_id
                    WHERE r.event_id=$1 AND r.ticket_type_id=$2
                      AND r.status='held'
                      AND ($3::text IS NULL OR
                           r.id::text=$4 OR
                           u.email ILIKE $3 ESCAPE '\' OR
                           m.cid ILIKE $3 ESCAPE '\' OR
                           trim(concat_ws(' ',u.first_name,u.last_name)) ILIKE $3 ESCAPE '\' OR
                           trim(concat_ws(' ',m.first_name,m.last_name)) ILIKE $3 ESCAPE '\' OR
                           coalesce(r.order_number,'') ILIKE $3 ESCAPE '\')
                    ORDER BY r.created_at DESC LIMIT 1000""",
                    sale['event_id'],sale['ticket_type_id'],needle,search)
    from sale_matching import normalized_name, normalized_cid
    buyer = normalized_name(sale['buyer_name'])
    parts = buyer.split()
    buyer_first, buyer_last = (parts[0], parts[-1]) if parts else ('', '')
    result = []
    for record in candidates:
        record = dict(record)
        name = ' '.join(filter(None,(record.get('member_first_name') or record.get('first_name'),
                                     record.get('member_last_name') or record.get('last_name'))))
        name_normalized = normalized_name(name)
        chunks = name_normalized.split()
        first = chunks[0] if chunks else ''
        last = chunks[-1] if chunks else ''
        score = round(SequenceMatcher(None,buyer,name_normalized).ratio(),3) if buyer and name_normalized else 0
        if buyer_first and buyer_last and first and last:
            score = max(score,round((SequenceMatcher(None,buyer_first,first).ratio()+
                                    SequenceMatcher(None,buyer_last,last).ratio())/2,3))
        cid_match = bool(sale['cid'] and record['cid'] and
                         normalized_cid(sale['cid']) == normalized_cid(record['cid']))
        email_match = bool(sale['email'] and record['email'] and
                           sale['email'].strip().casefold() == record['email'].strip().casefold())
        if cid_match:
            score = 1
        elif email_match:
            score = max(score,.99)
        result.append({'id':record['id'],'name':name or record['email'],
                       'email':record['email'],'cid':record['cid'],
                       'quantity':record['quantity'],'status':record['status'],
                       'created_at':record['created_at'],'order_number':record['order_number'],
                       'score':score,'cid_match':cid_match,'email_match':email_match,
                       'quantity_matches':record['quantity']==sale['quantity']})
    # Display eligible rows first, then the strongest matches. Never select one
    # for the officer; the separate approval endpoint rechecks every invariant.
    result.sort(key=lambda r:(not r['quantity_matches'],-r['score'], -r['id']))
    return {'candidates':result[:limit],'total':len(result),
            'truncated':len(candidates)==1000}


@router.post('/rows/{row_id}/approve')
async def approve_row(row_id: int, reservation_id: int = Body(embed=True), actor_id: int = Body(embed=True)) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("""SELECT r.*,i.event_id FROM public.product_sales_import_rows r
            JOIN public.product_sales_imports i ON i.id=r.import_id
            WHERE r.id=$1 FOR UPDATE OF r""", row_id)
        if row is None or row['status'] not in ('review', 'unreserved'):
            raise HTTPException(409, 'Sale has already been handled')
        # Serialise confirmations for one event so two separate imported rows
        # cannot both claim the same Union order in concurrent transactions.
        await conn.fetchval('SELECT id FROM public.events WHERE id=$1 FOR UPDATE', row['event_id'])
        reservation = await conn.fetchrow("SELECT id, ticket_type_id, event_id, status, quantity FROM public.reservations WHERE id=$1 FOR UPDATE", reservation_id)
        if (reservation is None or reservation['event_id'] != row['event_id'] or
            reservation['ticket_type_id'] != row['ticket_type_id'] or
            reservation['status'] != 'held'):
            raise HTTPException(409, 'Reservation is not eligible for this sale')
        if reservation['quantity'] != row['quantity']:
            raise HTTPException(409, 'Sale and reservation quantities differ. Resolve quantities before confirming.')
        # Never reuse one Union purchase to confirm several reservations.
        # The row lock above serialises this reservation; imported row locks
        # serialise the sale. Check for an already-confirmed matching order too.
        if row['order_number']:
            used = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM public.reservations
                WHERE id<>$1 AND event_id=$2 AND ticket_type_id=$3 AND status='confirmed'
                  AND upper(btrim(order_number))=upper(btrim($4::text)))""",
                reservation_id, row['event_id'], row['ticket_type_id'], row['order_number'])
            if used:
                raise HTTPException(409, 'This Union order already confirms another reservation')
        await conn.execute("""UPDATE public.reservations SET status='confirmed', confirmed_at=NOW(),
            order_number=COALESCE($2,order_number) WHERE id=$1""", reservation_id, row['order_number'])
        await conn.execute("""UPDATE public.product_sales_import_rows SET status='confirmed',
            reservation_id=$2,reviewed_by=$3,reviewed_at=NOW(),matched_on='committee_linked'
            WHERE id=$1""", row_id, reservation_id, actor_id)
        await _record_verified_import_order(conn, reservation_id, row['order_number'], actor_id)
        await conn.execute("""INSERT INTO public.action_audit(actor_user_id,action,entity_type,entity_id,after_data)
            VALUES ($1,'reservations.sale_import.approve','reservation',$2,
                    jsonb_build_object('import_row_id',$3::bigint,'source_status',$4::text,
                                       'order_number',$5::text,'quantity',$6::integer,
                                       'status','confirmed'))""", actor_id, reservation_id,
                row_id, row['status'], row['order_number'], row['quantity'])
    return {'ok': True, 'reservation_id': reservation_id}
