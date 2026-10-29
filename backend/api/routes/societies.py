"""Connected society administration and strictly scoped external committee portal."""
from __future__ import annotations

import asyncio
import csv
import io
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from clients import database, union
from config import settings
from models.auth import AuthSession
from services import societies, emailing, whatsapp
from services import sync as union_sync
from utils.deps import require_action, require_admin, require_session, verify_csrf, require_feature
from utils.year import academic_year

admin = APIRouter(prefix='/admin/societies', dependencies=[Depends(require_admin), require_feature('admin')])
portal = APIRouter(prefix='/society-portal')

class SocietyNew(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    centre_code: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    api_key: str = Field(min_length=8, max_length=1024)

class CommitteeWrite(BaseModel):
    title: str = Field(default='Committee',max_length=120)
    managing: bool = False
    permissions: list[str] = []

class GroupWrite(BaseModel):
    account_key: str
    jid: str
    society_id: int | None = None
    enabled: bool = False
    eesoc_enabled: bool = False
    eesoc_user_ids: list[int] = []

@admin.get('/eligible-lookup',dependencies=[Depends(require_action('events.ticket_types.eligibility'))])
async def ticket_society_lookup():
    return [{'id':s['id'],'name':s['name']} for s in await database.get('/external-societies') or []]

@admin.get('', dependencies=[Depends(require_action('societies.view'))])
async def list_societies():
    return await database.get('/external-societies') or []

@admin.post('', dependencies=[Depends(require_action('societies.create')),Depends(verify_csrf)])
async def create_society(body: SocietyNew):
    result=await database.post('/external-societies', {
        'name':body.name.strip(),'centre_code':body.centre_code.strip(),
        'credential_ciphertext':societies.encrypt(body.api_key.strip()),
    })
    return {'id':result['id']}

@admin.put('/{society_id}/credential',dependencies=[Depends(require_action('societies.credential.rotate')),Depends(verify_csrf)])
async def rotate_credential(society_id:int,api_key:str=Body(embed=True)):
    if len(api_key.strip())<8 or len(api_key.strip())>1024:
        raise HTTPException(400,'Invalid society API key')
    return await database.put(f'/external-societies/{society_id}/credential',{
        'credential_ciphertext':societies.encrypt(api_key.strip())})

@admin.delete('/{society_id}', dependencies=[Depends(require_action('societies.delete')),Depends(verify_csrf)])
async def delete_society(society_id:int):
    return await database.delete(f'/external-societies/{society_id}') or {'ok':True}

async def sync_one(society_id:int) -> dict:
    row=await database.get(f'/external-societies/{society_id}/credential')
    if not row: raise HTTPException(404,'Unknown society')
    async with union_sync._lease(f'external-society-{society_id}') as acquired:
        if not acquired: return {'ok':True,'skipped':True}
        try:
            records=await union.external_members(row['centre_code'],societies.decrypt(row['credential_ciphertext']),academic_year())
            result=await database.post(f'/external-societies/{society_id}/sync',records,params={'year':academic_year()})
            return {'ok':True,'count':result['count']}
        except union.UnionUnavailable:
            await database.post(f'/external-societies/{society_id}/sync-error',params={'message':'Union membership sync failed'})
            raise HTTPException(502,'Unable to sync membership from Union; previous snapshot retained')

@admin.post('/{society_id}/sync', dependencies=[Depends(require_action('societies.sync')),Depends(verify_csrf)])
async def sync_members(society_id:int):
    return await sync_one(society_id)

@admin.get('/{society_id}/committee',dependencies=[Depends(require_action('societies.committee.view'))])
async def list_committee(society_id:int):
    return await database.get(f'/external-societies/{society_id}/committee') or []

@admin.put('/{society_id}/committee/{login}',dependencies=[Depends(require_action('societies.committee.edit')),Depends(verify_csrf)])
async def set_committee(society_id:int,login:str,body:CommitteeWrite):
    login=login.strip().lower()
    if not societies.LOGIN_RE.fullmatch(login): raise HTTPException(400,'Invalid Imperial login')
    if len(set(body.permissions))!=len(body.permissions) or not set(body.permissions).issubset(societies.SOC_ACTIONS):
        raise HTTPException(400,'Unknown or duplicate committee permissions')
    return await database.put(f'/external-societies/{society_id}/committee/{login}', {'login':login,**body.model_dump()})

@admin.delete('/{society_id}/committee/{login}',dependencies=[Depends(require_action('societies.committee.delete')),Depends(verify_csrf)])
async def delete_committee(society_id:int,login:str):
    return await database.delete(f'/external-societies/{society_id}/committee/{login}') or {'ok':True}

@admin.get('/{society_id}/events',dependencies=[Depends(require_action('societies.view'))])
async def assigned_events(society_id:int):
    return await database.get(f'/external-societies/{society_id}/events') or []

@admin.put('/{society_id}/events/{event_id}',dependencies=[Depends(require_action('societies.events.assign')),Depends(verify_csrf)])
async def assign_event(society_id:int,event_id:int):
    event=await database.get(f'/events/{event_id}')
    if not event: raise HTTPException(404,'No such event')
    return await database.put(f'/external-societies/{society_id}/events/{event_id}',{})

@admin.delete('/{society_id}/events/{event_id}',dependencies=[Depends(require_action('societies.events.unassign')),Depends(verify_csrf)])
async def revoke_event(society_id:int,event_id:int):
    return await database.delete(f'/external-societies/{society_id}/events/{event_id}') or {'ok':True}

@admin.get('/{society_id}/mailing-list.csv',dependencies=[Depends(require_action('societies.members.export'))])
async def export_mailing_list(society_id:int):
    societies_all=await database.get('/external-societies') or []
    if not any(s['id']==society_id for s in societies_all): raise HTTPException(404,'Unknown society')
    addresses=await societies.mailing_list(society_id)
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(['Email'])
    for person in addresses: writer.writerow([person['email']])
    return Response(output.getvalue(), media_type='text/csv',headers={
        'Content-Disposition':f'attachment; filename="society-{society_id}-mailing.csv"',
        'Cache-Control':'no-store',
    })

@admin.get('/whatsapp/groups',dependencies=[Depends(require_action('societies.whatsapp.groups.view'))])
async def group_settings():
    return await database.get('/external-societies/whatsapp/groups/settings') or []

@admin.put('/whatsapp/groups',dependencies=[Depends(require_action('societies.whatsapp.groups.edit')),Depends(verify_csrf)])
async def set_group_settings(body:GroupWrite):
    if not re.fullmatch(r'eesoc|society-[1-9][0-9]*',body.account_key):
        raise HTTPException(400,'Unknown WhatsApp account')
    if not re.fullmatch(r'[0-9-]+@g\.us',body.jid):
        raise HTTPException(400,'Invalid group identifier')
    if body.account_key!='eesoc' and body.account_key!=f'society-{body.society_id}':
        raise HTTPException(400,'Society account cannot be assigned to a different society')
    if body.society_id and not any(s['id']==body.society_id for s in (await database.get('/external-societies') or [])):
        raise HTTPException(404,'Unknown society')
    # Can only grant an existing live group, preventing fake targets being planted.
    from clients import whatsapp as client
    try:
        live=await client.groups(body.account_key)
    except client.WhatsAppUnavailable as exc:
        raise HTTPException(503,'WhatsApp connection unavailable') from exc
    if body.jid not in {g['jid'] for g in live}:
        raise HTTPException(400,'That group is not on the linked WhatsApp account')
    if body.eesoc_user_ids:
        committee=await database.get('/auth/admins') or []
        known={r['user_id'] for r in committee if r.get('user_id')}
        if not set(body.eesoc_user_ids).issubset(known):
            raise HTTPException(400,'Only current EESoc committee users may be granted group access')
    return await database.put('/external-societies/whatsapp/groups/settings',body.model_dump())

@portal.get('')
async def my_societies(session:AuthSession=Depends(require_session)):
    """A separate committee portal; a society role never grants EESoc admin."""
    return await societies.affiliations(session)

@portal.get('/{society_id}/events')
async def my_events(society_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.view')
    ids=await database.get(f'/external-societies/{society_id}/events') or []
    return [e for event_id in ids if (e:=await database.get(f'/events/{event_id}'))]

EVENT_FIELDS={'title','description','category','venue','starts_at','ends_at',
              'doors_close_at','map_url','meeting_url','meeting_url_private',
              'sales_open_at','reservations_close_at','union_sales_close_at',
              'capacity','hold_hours','purchase_url','signup_url','image_url'}

@portal.put('/{society_id}/events/{event_id}',dependencies=[Depends(verify_csrf)])
async def edit_managed_event(society_id:int,event_id:int,body:dict=Body(...),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.edit',managing=True)
    event=await societies.require_event(society_id,event_id)
    if not body or set(body)-EVENT_FIELDS:
        raise HTTPException(400,'This action can only change event details, not its ownership, permissions or ticket types')
    updated=await database.patch(f'/events/{event_id}/society-fields',body)
    if {'sales_open_at','starts_at','published'} & body.keys():
        from routes.admin import _sync_on_sale_email
        await _sync_on_sale_email(event_id)
    return updated

@portal.patch('/{society_id}/events/{event_id}/published',dependencies=[Depends(verify_csrf)])
async def publish_managed_event(society_id:int,event_id:int,published:bool=Body(embed=True),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.publish',managing=True)
    event=await societies.require_event(society_id,event_id)
    updated=await database.patch(f'/events/{event_id}/society-fields',{'published':published})
    from routes.admin import _sync_on_sale_email
    await _sync_on_sale_email(event_id)
    return updated

TICKET_FIELDS={'name','description','price_pence','capacity','requires_purchase',
               'max_per_user','sort_order','purchase_url','sales_open_at','sales_close_at'}

@portal.put('/{society_id}/events/{event_id}/tickets/{ticket_id}',dependencies=[Depends(verify_csrf)])
async def edit_managed_ticket(society_id:int,event_id:int,ticket_id:int,body:dict=Body(...),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.tickets.edit',managing=True)
    await societies.require_event(society_id,event_id)
    ticket=await database.get(f'/ticket-types/{ticket_id}')
    if not ticket or ticket['event_id']!=event_id: raise HTTPException(404,'Ticket not on assigned event')
    if not body or set(body)-TICKET_FIELDS: raise HTTPException(400,'Unsupported ticket edit')
    return await database.put(f'/ticket-types/{ticket_id}',{**ticket,**body})

@portal.post('/{society_id}/events/{event_id}/tickets',dependencies=[Depends(verify_csrf)])
async def add_managed_ticket(society_id:int,event_id:int,body:dict=Body(...),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.tickets.create',managing=True)
    await societies.require_event(society_id,event_id)
    if not body or set(body)-TICKET_FIELDS: raise HTTPException(400,'Unsupported ticket fields')
    return await database.post(f'/events/{event_id}/ticket-types',body)

@portal.delete('/{society_id}/events/{event_id}/tickets/{ticket_id}',dependencies=[Depends(verify_csrf)])
async def remove_managed_ticket(society_id:int,event_id:int,ticket_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.tickets.delete',managing=True)
    await societies.require_event(society_id,event_id)
    ticket=await database.get(f'/ticket-types/{ticket_id}')
    if not ticket or ticket['event_id']!=event_id: raise HTTPException(404,'Ticket not on assigned event')
    return await database.delete(f'/ticket-types/{ticket_id}') or {'ok':True}

@portal.get('/{society_id}/events/{event_id}/bookings')
async def managed_bookings(society_id:int,event_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'events.bookings.view',managing=True)
    await societies.require_event(society_id,event_id)
    return await database.get('/reservations',{'event_id':event_id,'limit':5000}) or []

async def _check_audiences(society_id:int,items:list[str],session:AuthSession) -> tuple[list[str],list[dict]]:
    """Check every selected audience, not only the first one. Fail closed."""
    mail=emailing.email_keys(items)
    groups=emailing.whatsapp_groups_in(items)
    if not items or any(k not in {'test',f'society:{society_id}'} for k in mail):
        raise HTTPException(403,'Only the test audience and your own society members are permitted')
    if mail:
        await societies.require_society(session,society_id,'announcements.send',managing=True)
    if groups:
        await societies.require_society(session,society_id,'whatsapp.post',managing=True)
    selected=await whatsapp.pick(groups,society_id=society_id)
    if len(selected)!=len(set(groups)):
        raise HTTPException(403,'A WhatsApp group is disabled or not assigned to this society')
    return mail,selected

@portal.get('/{society_id}/announcements/audiences')
async def announcement_audiences(society_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'announcements.preview')
    test=await emailing.test_recipients()
    members=await societies.mailing_list(society_id)
    groups=await whatsapp.available(society_id=society_id)
    return {'audiences':[
        {'key':'test','label':'Test list','count':len(test)},
        {'key':f'society:{society_id}','label':'Your society members','count':len(members)},
        *[{'key':f"whatsapp:{g['target']}",'label':g['name'], 'count':g['size'], 'whatsapp':True} for g in groups],
    ],'environment':settings.environment}

class Announcement(BaseModel):
    subject: str = Field(default='',max_length=200)
    heading: str = Field(default='',max_length=200)
    body: str = Field(default='',max_length=40000)
    audiences: list[str] = []
    event_id: int | None = None

@portal.post('/{society_id}/announcements/preview',dependencies=[Depends(verify_csrf)])
async def announcement_preview(society_id:int,data:Announcement,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'announcements.preview')
    # Preview must never resolve a different society's audience either.
    mail=emailing.email_keys(data.audiences)
    if any(k not in {'test',f'society:{society_id}'} for k in mail):
        raise HTTPException(403,'Audience belongs to another society')
    if data.event_id: await societies.require_event(society_id,data.event_id)
    groups=emailing.whatsapp_groups_in(data.audiences)
    selected=await whatsapp.pick(groups,society_id=society_id)
    if len(selected)!=len(set(groups)):
        raise HTTPException(403,'Unassigned or disabled group')
    recipients=await emailing.resolve(mail,allowed_society_ids={society_id})
    html,text=emailing.render_body(data.body)
    return {'html':html,'text':text,'recipients':len(recipients),
        'sample':[r['email'] for r in recipients[:10]],
        'groups':[{'name':g['name'],'size':g['size']} for g in selected]}

@portal.post('/{society_id}/announcements/send',dependencies=[Depends(verify_csrf)])
async def announcement_send(society_id:int,data:Announcement,session:AuthSession=Depends(require_session)):
    mail,groups=await _check_audiences(society_id,data.audiences,session)
    event=None
    if data.event_id:
        event=await societies.require_event(society_id,data.event_id)
    if not mail and not groups: raise HTTPException(400,'Select an audience')
    recipients=await emailing.resolve(mail,allowed_society_ids={society_id})
    if mail:
        if not data.subject.strip(): raise HTTPException(400,'Subject is required for email')
        if not recipients: raise HTTPException(400,'Selected email list is empty')
        from utils import email as mailer
        if not mailer.configured('publicity'): raise HTTPException(503,'Publicity mailbox is not configured')
        if len(recipients)>settings.bulk_max_recipients: raise HTTPException(400,'Too many recipients')
    if not data.body.strip() and not event: raise HTTPException(400,'Message must not be blank')
    from utils import email as mailer
    from utils.present import format_date,format_time_range,format_doors_close,format_price
    from services.campaigns import parse_time
    campaign=None
    if mail:
        html,text=emailing.render_body(data.body)
        campaign=await database.post('/email/campaigns',{
            'subject':data.subject.strip(),'heading':(data.heading or data.subject).strip(),
            'body':data.body,'audiences':data.audiences+[f'society-sent:{society_id}'],
            'created_by':session.user.id,
        })
        if event:
            start,end=parse_time(event.get('starts_at')),parse_time(event.get('ends_at'))
            when=f'{format_date(start)}, {format_time_range(start,end)}' if start else 'TBC'
            tickets=[(t['name'],format_price(t.get('price_pence',0))) for t in event.get('ticket_types',[]) if not t.get('sold_out')]
            async def build(batch,unsubscribe):
                return await mailer.send_event_announcement(
                    [r['email'] for r in batch],data.subject.strip(),event['title'],when,
                    event.get('venue') or '',f"{settings.site_origin}/events/{event['slug']}",unsubscribe,
                    body_text=text,body_html=html,tickets=tickets,
                    heading=(data.heading or data.subject).strip(),
                    doors_close=format_doors_close(event.get('starts_at'),event.get('doors_close_at')))
        else:
            async def build(batch,unsubscribe):
                return await mailer.send_campaign([r['email'] for r in batch],data.subject.strip(),
                    (data.heading or data.subject).strip(),text,html,unsubscribe)
        # Seed recipient rows before returning. A rolling update between queueing
        # and the sender starting must not strand a campaign or re-mail users.
        await database.post('/campaign-recipients', {
            'campaign_id':campaign['id'], 'emails':[r['email'] for r in recipients],
        })
        from routes.admin import _dispatch
        _dispatch(emailing.send_bulk(campaign['id'],recipients,build,
            kind='event_announcement' if event else 'newsletter',event_id=data.event_id))
    if groups:
        post=await whatsapp.message((data.heading or data.subject).strip(),data.body,event)
        from routes.admin import _dispatch
        _dispatch(whatsapp.post(post,groups,campaign_id=campaign and campaign['id'],by=session.user.id))
    return {'ok':True,'recipients':len(recipients),'groups':len(groups),'campaignId':campaign and campaign['id']}


@portal.get('/{society_id}/whatsapp')
async def society_whatsapp(society_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'whatsapp.view')
    from clients import whatsapp as client
    account=f'society-{society_id}'
    try:
        raw=await client.status(account)
        live=await whatsapp.inventory(account)
    except client.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status,'WhatsApp service unavailable') from exc
    from routes.whatsapp import _status
    settings_rows=await database.get('/external-societies/whatsapp/groups/settings') or []
    configs={(r['account_key'],r['jid']):r for r in settings_rows}
    return {'status':_status(raw),'groups':[{**g,'enabled':configs.get((account,g['jid']),{}).get('enabled',False)} for g in live],
            'assigned':await whatsapp.available(society_id=society_id)}

@portal.post('/{society_id}/whatsapp/link',dependencies=[Depends(verify_csrf)])
async def link_society_whatsapp(society_id:int,phone:str|None=Body(default=None,embed=True),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'whatsapp.link',managing=True)
    from clients import whatsapp as client
    from routes.whatsapp import _status
    try:
        return _status(await client.link(phone,account=f'society-{society_id}'))
    except client.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status,'Could not link WhatsApp account') from exc

@portal.post('/{society_id}/whatsapp/unlink',dependencies=[Depends(verify_csrf)])
async def unlink_society_whatsapp(society_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'whatsapp.link',managing=True)
    from clients import whatsapp as client
    from routes.whatsapp import _status
    try:
        return _status(await client.unlink(account=f'society-{society_id}'))
    except client.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status,'Could not unlink WhatsApp account') from exc

@portal.put('/{society_id}/whatsapp/groups/{jid}',dependencies=[Depends(verify_csrf)])
async def toggle_society_group(society_id:int,jid:str,enabled:bool=Body(embed=True),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'whatsapp.groups.manage',managing=True)
    from clients import whatsapp as client
    account=f'society-{society_id}'
    try:
        live=await client.groups(account)
    except client.WhatsAppUnavailable as exc:
        raise HTTPException(503,'WhatsApp connection unavailable') from exc
    if jid not in {g['jid'] for g in live if g.get('canPost')}:
        raise HTTPException(403,'The society account cannot post to that group')
    # This adapter endpoint preserves all EESoc group-level grants.
    return await database.patch('/external-societies/whatsapp/groups/society-enabled',{
        'account_key':account,'jid':jid,'society_id':society_id,'enabled':enabled})

@portal.post('/{society_id}/whatsapp/invite',dependencies=[Depends(verify_csrf)])
async def request_eesoc_join(society_id:int,invite:str=Body(embed=True),session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'whatsapp.group.request',managing=True)
    match=re.fullmatch(r'(?:https?://chat\.whatsapp\.com/)?([A-Za-z0-9]{12,40})/?',invite.strip())
    if not match: raise HTTPException(400,'Enter a valid WhatsApp group invite link')
    row=await database.post('/external-societies/whatsapp/invites',{
        'society_id':society_id,'invite_ciphertext':societies.encrypt(match.group(1)),
        'requested_by':session.user.id})
    return {'id':row['id'],'status':'pending'}

@portal.get('/{society_id}/whatsapp/invites')
async def my_join_requests(society_id:int,session:AuthSession=Depends(require_session)):
    await societies.require_society(session,society_id,'whatsapp.view')
    return await database.get('/external-societies/whatsapp/invites',{'society_id':society_id}) or []

@admin.get('/whatsapp/invites',dependencies=[Depends(require_action('societies.whatsapp.invites.view'))])
async def review_whatsapp_invites():
    return await database.get('/external-societies/whatsapp/invites') or []

@admin.get('/whatsapp/societies',dependencies=[Depends(require_action('societies.whatsapp.groups.view'))])
async def whatsapp_society_lookup():
    return [{'id':s['id'],'name':s['name']} for s in (await database.get('/external-societies') or [])]

@admin.get('/whatsapp/inventory',dependencies=[Depends(require_action('societies.whatsapp.groups.view'))])
async def whatsapp_group_inventory():
    connected=await database.get('/external-societies') or []
    accounts=['eesoc']+[f"society-{s['id']}" for s in connected]
    rows=[]
    for account in accounts:
        rows.extend(await whatsapp.inventory(account))
    return rows

@admin.post('/whatsapp/invites/{invite_id}/approve',dependencies=[Depends(require_action('societies.whatsapp.invites.approve')),Depends(verify_csrf)])
async def approve_whatsapp_invite(invite_id:int,session:AuthSession=Depends(require_admin)):
    row=await database.get(f'/external-societies/whatsapp/invites/{invite_id}/credential')
    if not row: raise HTTPException(404,'Unknown invitation')
    if row['status']!='pending': raise HTTPException(409,'Invitation already reviewed')
    from clients import whatsapp as client
    code=societies.decrypt(row['invite_ciphertext'])
    try:
        joined=await client.join_invite(code)
    except client.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status,'Could not join the group; request remains pending') from exc
    jid=joined.get('jid')
    if not jid or not re.fullmatch(r'[0-9-]+@g\.us',jid):
        raise HTTPException(503,'WhatsApp did not return a usable group identifier')
    await database.put('/external-societies/whatsapp/groups/settings',{
        'account_key':'eesoc','jid':jid,'society_id':row['society_id'],
        'enabled':True,'eesoc_enabled':False,'eesoc_user_ids':[],
    })
    await database.post(f'/external-societies/whatsapp/invites/{invite_id}/decision',{
        'status':'joined','joined_jid':jid,'decided_by':session.user.id})
    return {'ok':True,'jid':jid,'message':'EESoc joined; EESoc user posting requires a separate group grant'}

@admin.post('/whatsapp/invites/{invite_id}/decline',dependencies=[Depends(require_action('societies.whatsapp.invites.decline')),Depends(verify_csrf)])
async def decline_whatsapp_invite(invite_id:int,session:AuthSession=Depends(require_admin)):
    return await database.post(f'/external-societies/whatsapp/invites/{invite_id}/decision',{
        'status':'declined','decided_by':session.user.id})


@admin.get('/whatsapp/eesoc-people',dependencies=[Depends(require_action('societies.whatsapp.groups.view'))])
async def eesoc_whatsapp_people():
    rows=await database.get('/auth/admins') or []
    return [{'user_id':r['user_id'],'name':r.get('email') or r.get('login') or str(r['user_id'])}
            for r in rows if r.get('user_id') is not None]
