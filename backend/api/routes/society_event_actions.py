"""Event-management parity for connected societies, within one assigned event.

Every handler checks the society's current explicit grant AND the event's
assignment before calling the same EESoc committee action. Reservations,
imported rows, QR tickets and ticket-type IDs are looked up separately before
use; a foreign ID must never inherit a trusted event_id from the URL.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field

from clients import database
from models.auth import AuthSession
from routes import admin as eesoc, checkin, product_sales
from services import societies, tickets
from utils.deps import require_session, verify_csrf

router = APIRouter(prefix='/society-portal/{society_id}')


async def scoped(society_id:int, event_id:int, action:str,
                 session:AuthSession, *, managing:bool=True) -> societies.ScopedCommitteeSession:
    membership=await societies.require_society(session,society_id,action,managing=managing)
    await societies.require_event(society_id,event_id)
    return societies.ScopedCommitteeSession(session,membership)


async def reservation_scope(society_id:int,event_id:int,reservation_id:int,
                            action:str,session:AuthSession) -> societies.ScopedCommitteeSession:
    actor=await scoped(society_id,event_id,action,session)
    reservation=await database.get(f'/reservations/{reservation_id}')
    if not reservation or reservation['event_id']!=event_id:
        raise HTTPException(404,'No such booking on your assigned event')
    return actor


async def import_scope(society_id:int,event_id:int,row_id:int,
                       action:str,session:AuthSession,*,managing:bool=True) -> societies.ScopedCommitteeSession:
    actor=await scoped(society_id,event_id,action,session,managing=managing)
    row=await database.get(f'/product-sales/rows/{row_id}')
    if not row or row['event_id']!=event_id:
        raise HTTPException(404,'No such sale on your assigned event')
    return actor


class ManualBooking(BaseModel):
    ticket_type_id:int
    email:EmailStr
    first_name:str|None=None
    last_name:str|None=None
    quantity:int=Field(default=1,ge=1,le=20)
    note:str|None=None
    order_number:str|None=None
    override_confirmation:bool=False
    confirm_immediately:bool=False
    send_confirmation:bool=True
    override_capacity:bool=False


class Confirm(BaseModel):
    order_number:str|None=None
    override:bool=False


class IssueSaleTicket(BaseModel):
    email:EmailStr
    first_name:str|None=None
    last_name:str|None=None
    note:str|None=None
    send_confirmation:bool=True
    override_capacity:bool=False


@router.post('/events',dependencies=[Depends(verify_csrf)])
async def create_owned_event(society_id:int,body:dict=Body(...),session:AuthSession=Depends(require_session)):
    me=await societies.require_society(session,society_id,'events.create',managing=True)
    if 'organiser_society_ids' in body and body['organiser_society_ids'] != [society_id]:
        raise HTTPException(403,'You can only create events owned by your society')
    if 'eesoc_organised' in body and body['eesoc_organised'] is not False:
        raise HTTPException(403,'You cannot create events under the EESoc identity')
    # EESoc-wide event governance remains under the EESoc committee.
    body={**body,'organiser_society_ids':[society_id],'eesoc_organised':False}
    return await eesoc.create_event(body=body,session=societies.ScopedCommitteeSession(session,me))


@router.delete('/events/{event_id}',dependencies=[Depends(verify_csrf)])
async def delete_owned_event(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.delete',session)
    return await eesoc.delete_event(event_id=event_id,session=actor)


@router.patch('/events/{event_id}/photo-short-name',dependencies=[Depends(verify_csrf)])
async def change_photo_name(society_id:int,event_id:int,
                            photo_short_name:str|None=Body(default=None,embed=True),
                            session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.photo_short_name.edit',session)
    return await eesoc.edit_photo_short_name(event_id=event_id,photo_short_name=photo_short_name,session=actor)


@router.patch('/events/{event_id}/waitlist-enabled',dependencies=[Depends(verify_csrf)])
async def change_waitlist(society_id:int,event_id:int,
                          waitlist_enabled:bool=Body(embed=True),session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.waitlist.manage',session)
    return await eesoc.set_event_waitlist_enabled(event_id=event_id,
                                                   waitlist_enabled=waitlist_enabled,session=actor)


@router.patch('/events/{event_id}/availability-badge',dependencies=[Depends(verify_csrf)])
async def change_badge(society_id:int,event_id:int,
                       availability_badge:str=Body(embed=True),session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.availability_badge.edit',session)
    if availability_badge not in ('auto','not_many_left','selling_fast'):
        raise HTTPException(400,'Invalid availability badge')
    return await eesoc.set_event_availability_badge(event_id=event_id,
                                                    availability_badge=availability_badge,session=actor)


@router.get('/events/{event_id}/detail')
async def booking_detail(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.view',session,managing=False)
    return await eesoc.event_detail(event_id=event_id,session=actor)


@router.get('/events/{event_id}/reservations')
async def bookings(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.view',session,managing=False)
    return await eesoc.event_reservations(event_id=event_id,session=actor)


@router.get('/events/{event_id}/overdue')
async def overdue(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.view',session,managing=False)
    data=await eesoc.overdue_reservations(session=actor)
    # The EESoc helper returns a site-wide report; filter before returning.
    pending=[r for r in await database.get('/reservations/overdue') or []
             if r.get('event_id')==event_id]
    allowed_ids={r['id'] for r in pending}
    records=[r for r in data.get('overdue',[]) if r['id'] in allowed_ids]
    return {'overdue':records,'count':len(records)}


@router.get('/events/{event_id}/waitlist')
async def event_waitlist(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.waitlist',session,managing=False)
    return await eesoc.waitlist(event_id=event_id,session=actor)


@router.get('/events/{event_id}/sale-alerts')
async def event_sale_alerts(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.sale_alerts',session,managing=False)
    return await eesoc.event_sale_alerts(event_id=event_id,session=actor)


@router.post('/events/{event_id}/tickets/add',dependencies=[Depends(verify_csrf)])
async def add_booking(society_id:int,event_id:int,body:ManualBooking,
                      session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.add',session)
    return await eesoc.add_ticket(event_id=event_id,**body.model_dump(),session=actor)


@router.post('/events/{event_id}/reservations/{reservation_id}/confirm',dependencies=[Depends(verify_csrf)])
async def confirm_booking(society_id:int,event_id:int,reservation_id:int,body:Confirm,
                          session:AuthSession=Depends(require_session)):
    me=await societies.require_society(session,society_id,managing=True)
    action='events.bookings.confirm_override' if body.override else 'events.bookings.confirm'
    if action not in societies.grants(me['permissions']):
        raise HTTPException(403,f'Society permission required: {action}')
    actor=await reservation_scope(society_id,event_id,reservation_id,action,session)
    return await eesoc.confirm_manually(reservation_id=reservation_id,
        order_number=body.order_number,override=body.override,session=actor)


@router.patch('/events/{event_id}/reservations/{reservation_id}/order-number',dependencies=[Depends(verify_csrf)])
async def edit_order(society_id:int,event_id:int,reservation_id:int,
                     order_number:str|None=Body(default=None,embed=True),
                     session:AuthSession=Depends(require_session)):
    actor=await reservation_scope(society_id,event_id,reservation_id,
        'events.bookings.order_number.edit',session)
    return await eesoc.committee_order_number(reservation_id=reservation_id,
        order_number=order_number,session=actor)


@router.post('/events/{event_id}/reservations/{reservation_id}/ticket-type',dependencies=[Depends(verify_csrf)])
async def change_booking_ticket(society_id:int,event_id:int,reservation_id:int,
                                ticket_type_id:int=Body(embed=True),
                                session:AuthSession=Depends(require_session)):
    actor=await reservation_scope(society_id,event_id,reservation_id,
        'events.bookings.change_ticket',session)
    target=await database.get(f'/ticket-types/{ticket_type_id}')
    if not target or target['event_id'] != event_id:
        raise HTTPException(404,'Ticket type is not on your event')
    return await eesoc.change_ticket_type(reservation_id=reservation_id,
        ticket_type_id=ticket_type_id,session=actor)


@router.post('/events/{event_id}/reservations/{reservation_id}/remind',dependencies=[Depends(verify_csrf)])
async def remind_booking(society_id:int,event_id:int,reservation_id:int,
                         session:AuthSession=Depends(require_session)):
    actor=await reservation_scope(society_id,event_id,reservation_id,
        'events.bookings.remind',session)
    return await eesoc.remind(reservation_id=reservation_id,session=actor)


@router.post('/events/{event_id}/reservations/{reservation_id}/confirmation',dependencies=[Depends(verify_csrf)])
async def resend_booking(society_id:int,event_id:int,reservation_id:int,
                         session:AuthSession=Depends(require_session)):
    actor=await reservation_scope(society_id,event_id,reservation_id,
        'events.bookings.resend',session)
    return await eesoc.resend_confirmation(reservation_id=reservation_id,session=actor)


@router.post('/events/{event_id}/reservations/{reservation_id}/cancel',dependencies=[Depends(verify_csrf)])
async def cancel_booking(society_id:int,event_id:int,reservation_id:int,
                         session:AuthSession=Depends(require_session)):
    actor=await reservation_scope(society_id,event_id,reservation_id,
        'events.bookings.cancel',session)
    return await eesoc.cancel_reservation(reservation_id=reservation_id,session=actor)


@router.get('/events/{event_id}/product-sales/targets')
async def sale_targets(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    await scoped(society_id,event_id,'events.bookings.import.view',session,managing=False)
    rows=await database.get('/product-sales/targets') or []
    return {'targets':[r for r in rows if r['event_id']==event_id]}


@router.get('/events/{event_id}/product-sales/imports')
async def sale_imports(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.import.view',session,managing=False)
    return await product_sales.list_imports(event_id=event_id,session=actor)


@router.get('/events/{event_id}/product-sales/rows')
async def sale_rows(society_id:int,event_id:int,status:str|None=None,
                    session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.import.view',session,managing=False)
    return await product_sales.list_import_rows(event_id=event_id,status=status,session=actor)


@router.post('/events/{event_id}/product-sales/imports',dependencies=[Depends(verify_csrf)])
async def sale_upload(society_id:int,event_id:int,file:UploadFile=File(...),
                      ticket_type_id:int=Form(...),session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.bookings.import',session)
    target=await database.get(f'/ticket-types/{ticket_type_id}')
    if not target or target['event_id']!=event_id:
        raise HTTPException(404,'Ticket type is not on your event')
    return await product_sales.upload_import(file=file,ticket_type_id=ticket_type_id,session=actor)


@router.get('/events/{event_id}/product-sales/rows/{row_id}/candidates')
async def sale_candidates(society_id:int,event_id:int,row_id:int,q:str='',limit:int=30,
                          session:AuthSession=Depends(require_session)):
    actor=await import_scope(society_id,event_id,row_id,'events.bookings.import.view',session,managing=False)
    return await product_sales.search_import_reservations(row_id=row_id,q=q,limit=limit,session=actor)


@router.post('/events/{event_id}/product-sales/rows/{row_id}/approve',dependencies=[Depends(verify_csrf)])
async def approve_sale(society_id:int,event_id:int,row_id:int,reservation_id:int=Body(embed=True),
                       session:AuthSession=Depends(require_session)):
    actor=await import_scope(society_id,event_id,row_id,'events.bookings.confirm',session)
    reservation=await database.get(f'/reservations/{reservation_id}')
    if not reservation or reservation['event_id']!=event_id:
        raise HTTPException(404,'Not a booking on this event')
    return await product_sales.approve_import_match(row_id=row_id,reservation_id=reservation_id,session=actor)


@router.post('/events/{event_id}/product-sales/rows/{row_id}/create-ticket',dependencies=[Depends(verify_csrf)])
async def issue_sale_ticket(society_id:int,event_id:int,row_id:int,body:IssueSaleTicket,
                            session:AuthSession=Depends(require_session)):
    actor=await import_scope(society_id,event_id,row_id,'events.bookings.add',session)
    return await product_sales.create_ticket_for_sale(row_id=row_id,**body.model_dump(),session=actor)


@router.get('/events/{event_id}/checkin')
async def checkin_list(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.checkin.view',session,managing=False)
    return await checkin.checkin_event(event_id=event_id,_=actor)


@router.get('/events/{event_id}/checkin/state')
async def checkin_status(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.checkin.view',session,managing=False)
    return await checkin.checkin_state(event_id=event_id,_=actor)


@router.post('/checkin/scan',dependencies=[Depends(verify_csrf)])
async def combined_checkin_scan(society_id:int,token:str=Body(embed=True),
                                event_ids:list[int]=Body(embed=True),
                                session:AuthSession=Depends(require_session)):
    """Scan across the selected events, never another society's bookings."""
    membership=await societies.require_society(session,society_id,'events.checkin.scan',managing=True)
    checkin.validate_event_ids(event_ids)
    for event_id in event_ids:
        await societies.require_event(society_id,event_id)
    record=await checkin._booking(tickets.parse(token))
    # A foreign ticket cannot reveal its holder, even via a wrong-event result.
    await societies.require_event(society_id,record['event_id'])
    actor=societies.ScopedCommitteeSession(session,membership)
    return await checkin.scan(token=token,event_id=None,event_ids=event_ids,session=actor)


@router.post('/events/{event_id}/checkin/scan',dependencies=[Depends(verify_csrf)])
async def checkin_scan(society_id:int,event_id:int,token:str=Body(embed=True),
                       session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.checkin.scan',session)
    record=await checkin._booking(tickets.parse(token))
    if record['event_id']!=event_id:
        raise HTTPException(403,'Ticket belongs to another event')
    return await checkin.scan(token=token,event_id=event_id,session=actor)


@router.post('/events/{event_id}/checkin/reservations/{reservation_id}',dependencies=[Depends(verify_csrf)])
async def checkin_manual(society_id:int,event_id:int,reservation_id:int,
                         override:bool=Body(default=False,embed=True),
                         session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.checkin.manual',session)
    booking=await checkin._booking(reservation_id)
    if booking['event_id']!=event_id:
        raise HTTPException(403,'Ticket belongs to another event')
    return await checkin.check_in(reservation_id=reservation_id,event_id=event_id,
                                  override=override,session=actor)


@router.delete('/events/{event_id}/checkin/reservations/{reservation_id}',dependencies=[Depends(verify_csrf)])
async def checkin_undo(society_id:int,event_id:int,reservation_id:int,
                       session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.checkin.undo',session)
    booking=await checkin._booking(reservation_id)
    if booking['event_id']!=event_id:
        raise HTTPException(403,'Ticket belongs to another event')
    return await checkin.undo_check_in(reservation_id=reservation_id,session=actor)

@router.get('/events/{event_id}/product-links')
async def own_product_links(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    await scoped(society_id,event_id,'events.ticket_types.product',session,managing=False)
    return await database.get('/product-links',{'event_id':event_id}) or []


@router.put('/events/{event_id}/product',dependencies=[Depends(verify_csrf)])
async def link_event_product(society_id:int,event_id:int,
                             union_product_id:int|None=Body(default=None,embed=True),
                             shop_product_ref:str|None=Body(default=None,embed=True),
                             session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.ticket_types.product',session)
    return await eesoc.set_event_product(event_id=event_id,union_product_id=union_product_id,
        shop_product_ref=shop_product_ref,session=actor)


@router.put('/events/{event_id}/tickets/{ticket_type_id}/product',dependencies=[Depends(verify_csrf)])
async def link_ticket_product(society_id:int,event_id:int,ticket_type_id:int,
                              union_product_id:int|None=Body(default=None,embed=True),
                              union_product_line_id:int|None=Body(default=None,embed=True),
                              shop_product_ref:str|None=Body(default=None,embed=True),
                              session:AuthSession=Depends(require_session)):
    actor=await scoped(society_id,event_id,'events.ticket_types.product',session)
    ticket=await database.get(f'/ticket-types/{ticket_type_id}')
    if not ticket or ticket['event_id']!=event_id:
        raise HTTPException(404,'Ticket type is not on your assigned event')
    return await eesoc.set_ticket_product(ticket_type_id=ticket_type_id,
        union_product_id=union_product_id,union_product_line_id=union_product_line_id,
        shop_product_ref=shop_product_ref,session=actor)
