"""Permission-checked import and reconciliation of committee Union exports."""
from __future__ import annotations

import hashlib
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from clients import database
from models.auth import AuthSession
from services import confirmations
from services.identity import resolve_or_create_user
from services.product_sales_import import MAX_UPLOAD_BYTES, parse_export, product_ids_in_filename
from utils.auth import normalise_email
from utils.deps import require_action, require_admin, require_any_action, verify_csrf, may_manage_booking
from utils.year import academic_year

router = APIRouter(prefix='/admin/product-sales', dependencies=[Depends(require_admin)])


async def event_for_import(event_id: int, session: AuthSession) -> dict:
    event = await database.get(f'/events/{event_id}')
    if event is None:
        raise HTTPException(404, 'No such event')
    if not may_manage_booking(session, event):
        raise HTTPException(403, 'You cannot manage sales for this event')
    return event


@router.get('/targets', dependencies=[Depends(require_any_action('events.bookings.import.view', 'events.bookings.import'))])
async def import_targets(session: AuthSession = Depends(require_admin)) -> dict:
    rows = await database.get('/product-sales/targets') or []
    result = []
    for row in rows:
        if may_manage_booking(session, row):
            result.append(row)
    return {'targets': result}


@router.post('/imports', dependencies=[Depends(require_action('events.bookings.import')), Depends(verify_csrf)])
async def upload_import(
    file: UploadFile = File(...),
    ticket_type_id: int = Form(...),
    session: AuthSession = Depends(require_admin),
) -> dict:
    # Uploading directly confirms exact matches, so the importer must have both
    # permissions. Reviewers never acquire import permissions by implication.
    if not session.can('events.bookings.confirm'):
        raise HTTPException(403, 'Confirmation permission is required to import and auto-confirm sales')
    targets = await import_targets(session)
    matches = [r for r in targets['targets'] if r['ticket_type_id'] == ticket_type_id]
    if not matches:
        raise HTTPException(403, 'This ticket type is not available in your event scope')
    products_in_name = product_ids_in_filename(file.filename or '')
    product_ids = {r['product_id'] for r in matches if r['product_id'] in products_in_name}
    if len(product_ids) != 1:
        raise HTTPException(400, 'Filename must contain exactly one product ID linked to the selected ticket type')
    product_id = product_ids.pop()
    target = next(row for row in matches if row['product_id'] == product_id)
    await event_for_import(target['event_id'], session)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Maximum sales export size is 6 MB')
    rows = parse_export(file.filename or '', content)
    digest = hashlib.sha256(content).hexdigest()
    result = await database.post('/product-sales/imports', {
        'filename': file.filename, 'file_sha256': digest,
        'product_id': product_id, 'ticket_type_id': ticket_type_id,
        'uploaded_by': session.user.id, 'rows': rows,
    })
    if result and result.get('counts', {}).get('confirmed'):
        confirmations.trigger()
    return result


@router.get('/imports', dependencies=[Depends(require_action('events.bookings.import.view'))])
async def list_imports(event_id: int, session: AuthSession = Depends(require_admin)) -> dict:
    await event_for_import(event_id, session)
    return {'imports': await database.get('/product-sales/imports', {'event_id': event_id}) or []}


@router.get('/rows', dependencies=[Depends(require_action('events.bookings.import.view'))])
async def list_import_rows(event_id: int, status: str | None = None,
                           session: AuthSession = Depends(require_admin)) -> dict:
    await event_for_import(event_id, session)
    if status and status not in ('confirmed', 'review', 'unreserved', 'duplicate', 'ticket_added'):
        raise HTTPException(400, 'Invalid sales row status')
    return {'rows': await database.get('/product-sales/rows', {'event_id': event_id, 'status': status}) or []}


async def scoped_row(row_id: int, session: AuthSession) -> dict:
    row = await database.get(f'/product-sales/rows/{row_id}')
    if row is None:
        raise HTTPException(404, 'No such imported sale')
    await event_for_import(row['event_id'], session)
    return row


@router.post('/rows/{row_id}/approve', dependencies=[Depends(require_action('events.bookings.confirm')), Depends(verify_csrf)])
async def approve_import_match(row_id: int, reservation_id: int = Body(embed=True),
                               session: AuthSession = Depends(require_admin)) -> dict:
    row = await scoped_row(row_id, session)
    if row['status'] != 'review':
        raise HTTPException(409, 'Sale is not awaiting approval')
    # DB adapter rechecks scope, status, quantity and suggestion membership in
    # the same transaction as changing the reservation and recording attribution.
    result = await database.post(f'/product-sales/rows/{row_id}/approve', {
        'reservation_id': reservation_id, 'actor_id': session.user.id,
    })
    confirmations.trigger()
    return result


@router.post('/rows/{row_id}/create-ticket', dependencies=[Depends(require_action('events.bookings.add')), Depends(verify_csrf)])
async def create_ticket_for_sale(
    row_id: int,
    email: str = Body(embed=True),
    first_name: str | None = Body(default=None, embed=True),
    last_name: str | None = Body(default=None, embed=True),
    note: str | None = Body(default=None, embed=True),
    send_confirmation: bool = Body(default=True, embed=True),
    override_capacity: bool = Body(default=False, embed=True),
    session: AuthSession = Depends(require_admin),
) -> dict:
    row = await scoped_row(row_id, session)
    if row['status'] != 'unreserved':
        raise HTTPException(409, 'This imported sale has already been handled')
    if override_capacity and not session.can('events.bookings.capacity_override'):
        raise HTTPException(403, 'Capacity override permission required')
    address = normalise_email(email)
    if '@' not in address or len(address) > 254:
        raise HTTPException(400, 'A valid email is required to issue a ticket')
    person = await resolve_or_create_user(
        address, first_name=(first_name or '').strip() or None,
        last_name=(last_name or '').strip() or None, verified=False,
    )
    result = await database.post('/reservations/committee', {
        'event_id': row['event_id'], 'ticket_type_id': row['ticket_type_id'],
        'user_id': person['id'], 'quantity': row['quantity'],
        'note': f'Created from uploaded Union sale row {row_id}' + (f': {note.strip()}' if note and note.strip() else ''),
        'added_by': session.user.id, 'send_confirmation': send_confirmation,
        'override_capacity': override_capacity, 'import_row_id': row_id,
        'booked_as_guest': person.get('member_id') is None,
    })
    if result is None:
        raise HTTPException(409, 'Could not create ticket for imported sale')
    if send_confirmation:
        confirmations.trigger()
    return {'ok': True, 'reservation': result}
