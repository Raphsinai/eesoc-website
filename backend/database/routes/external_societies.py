"""Internal-only SQL adapter for connected Union societies and scoped ACLs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from postgres import get_pool

router = APIRouter(prefix="/external-societies")

class SocietyWrite(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    centre_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    credential_ciphertext: str

class CommitteeWrite(BaseModel):
    login: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,63}$")
    title: str = Field(default="Committee", max_length=120)
    managing: bool = False
    permissions: list[str] = []

class ExternalMember(BaseModel):
    cid: str
    login: str | None = None
    first_name: str | None = None
    last_name: str | None = None

@router.get("")
async def societies():
    async with (await get_pool()).acquire() as conn:
        rows = await conn.fetch("SELECT id,name,centre_code,synced_year,synced_at,sync_error FROM public.external_societies ORDER BY name")
        return [dict(r) for r in rows]

@router.post("")
async def add_society(item: SocietyWrite):
    async with (await get_pool()).acquire() as conn:
        if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.external_societies WHERE centre_code=$1)",item.centre_code):
            raise HTTPException(409, "That Union centre is already connected")
        row=await conn.fetchrow("INSERT INTO public.external_societies(name,centre_code,credential_ciphertext) VALUES($1,$2,$3) RETURNING id",item.name,item.centre_code,item.credential_ciphertext)
        return {"id":row['id']}

@router.get("/{society_id}/credential")
async def credential(society_id: int):
    async with (await get_pool()).acquire() as conn:
        r=await conn.fetchrow("SELECT centre_code,credential_ciphertext FROM public.external_societies WHERE id=$1",society_id)
        if not r: raise HTTPException(404,"Unknown society")
        return dict(r)

@router.delete("/{society_id}")
async def remove_society(society_id: int):
    async with (await get_pool()).acquire() as conn, conn.transaction():
        # Disable groups before deleting the society. ON DELETE SET NULL alone
        # would leave EESoc enabled in former society groups by accident.
        await conn.execute('''UPDATE public.whatsapp_group_settings
            SET enabled=false, eesoc_enabled=false WHERE society_id=$1''',society_id)
        await conn.execute('''DELETE FROM public.whatsapp_group_eesoc_access
            WHERE (account_key,jid) IN (SELECT account_key,jid
                  FROM public.whatsapp_group_settings WHERE society_id=$1)''',society_id)
        await conn.execute("DELETE FROM public.external_societies WHERE id=$1",society_id)
        return {"ok":True}

@router.post("/{society_id}/sync")
async def save_members(society_id: int, year: str, rows: list[ExternalMember]):
    async with (await get_pool()).acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.external_societies WHERE id=$1)",society_id):
            raise HTTPException(404,"Unknown society")
        # Replace the current academic year's snapshot atomically, never mix cohorts.
        await conn.execute("DELETE FROM public.external_society_members WHERE society_id=$1 AND academic_year=$2",society_id,year)
        seen=set()
        entries=[]
        for m in rows:
            if m.cid and m.cid not in seen:
                seen.add(m.cid)
                login=(m.login or '').strip().lower()
                entries.append((society_id,year,m.cid,login or None,m.first_name,m.last_name))
        if entries:
            await conn.executemany("INSERT INTO public.external_society_members(society_id,academic_year,cid,login,first_name,last_name) VALUES($1,$2,$3,$4,$5,$6)",entries)
        await conn.execute("UPDATE public.external_societies SET synced_year=$2,synced_at=NOW(),sync_error=NULL WHERE id=$1",society_id,year)
        return {"count":len(entries)}

@router.post("/{society_id}/sync-error")
async def sync_error(society_id:int, message:str):
    async with (await get_pool()).acquire() as conn:
        await conn.execute("UPDATE public.external_societies SET sync_error=$2 WHERE id=$1",society_id,message[:200])
    return {"ok":True}

@router.get("/{society_id}/audience")
async def mailing_audience(society_id:int, year:str):
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch("""
            SELECT DISTINCT ON (lower(m.login)) lower(m.login)||'@ic.ac.uk' AS email,
                   m.first_name,m.last_name,u.id AS user_id
              FROM public.external_society_members m
              LEFT JOIN public.users u ON lower(u.email)=lower(m.login)||'@ic.ac.uk'
             WHERE m.society_id=$1 AND m.academic_year=$2
               AND m.login ~ '^[a-z][a-z0-9._-]{1,63}$'
               AND NOT EXISTS (SELECT 1 FROM public.email_optouts o
                    WHERE lower(o.email)=lower(m.login)||'@ic.ac.uk'
                      AND o.topic IN ('*','newsletter','events_new','tickets_on_sale'))
               AND NOT EXISTS (SELECT 1 FROM public.email_subscriptions pref
                    WHERE pref.user_id=u.id AND pref.topic IN ('newsletter','events_new','tickets_on_sale')
                      AND pref.subscribed=false)
             ORDER BY lower(m.login),m.cid
        """,society_id,year)
        return [dict(r) for r in rows]

@router.get("/{society_id}/committee")
async def committee(society_id:int):
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch("SELECT login,title,managing,permissions FROM public.external_society_committee WHERE society_id=$1 ORDER BY login",society_id)
        return [dict(r) for r in rows]

@router.put("/{society_id}/committee/{login}")
async def set_committee(society_id:int,login:str,item:CommitteeWrite):
    if login!=item.login: raise HTTPException(400,"Login mismatch")
    async with (await get_pool()).acquire() as conn:
        await conn.execute("""INSERT INTO public.external_society_committee(society_id,login,title,managing,permissions)
            VALUES($1,$2,$3,$4,$5) ON CONFLICT(society_id,login) DO UPDATE
            SET title=EXCLUDED.title,managing=EXCLUDED.managing,permissions=EXCLUDED.permissions""",
            society_id,login,item.title,item.managing,item.permissions)
        return {"ok":True}

@router.delete("/{society_id}/committee/{login}")
async def remove_committee(society_id:int,login:str):
    async with (await get_pool()).acquire() as conn:
        await conn.execute("DELETE FROM public.external_society_committee WHERE society_id=$1 AND login=$2",society_id,login)
        return {"ok":True}

@router.get("/for-login/{login}")
async def committee_societies(login:str):
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch("""SELECT s.id,s.name,c.title,c.managing,c.permissions
            FROM public.external_society_committee c JOIN public.external_societies s ON s.id=c.society_id
            WHERE c.login=$1 ORDER BY s.name""",login)
        return [dict(r) for r in rows]

@router.get("/{society_id}/events")
async def society_events(society_id:int):
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch("SELECT event_id FROM public.external_society_events WHERE society_id=$1 ORDER BY event_id",society_id)
        return [r['event_id'] for r in rows]

@router.put("/{society_id}/events/{event_id}")
async def assign_event(society_id:int,event_id:int):
    async with (await get_pool()).acquire() as conn:
        await conn.execute("INSERT INTO public.external_society_events(society_id,event_id) VALUES($1,$2) ON CONFLICT DO NOTHING",society_id,event_id)
        return {"ok":True}

@router.delete("/{society_id}/events/{event_id}")
async def unassign_event(society_id:int,event_id:int):
    async with (await get_pool()).acquire() as conn:
        await conn.execute("DELETE FROM public.external_society_events WHERE society_id=$1 AND event_id=$2",society_id,event_id)
        return {"ok":True}

@router.get("/{society_id}/events/{event_id}/assigned")
async def assigned(society_id:int,event_id:int):
    async with (await get_pool()).acquire() as conn:
        return {"assigned":bool(await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.external_society_events WHERE society_id=$1 AND event_id=$2)",society_id,event_id))}

@router.get("/{society_id}/ticket-eligible")
async def ticket_eligible(society_id:int,login:str,year:str):
    async with (await get_pool()).acquire() as conn:
        return {"eligible":bool(await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.external_society_members WHERE society_id=$1 AND login=$2 AND academic_year=$3)",society_id,login,year))}

class GroupConfig(BaseModel):
    account_key: str
    jid: str
    society_id: int | None = None
    enabled: bool = False
    eesoc_enabled: bool = False
    eesoc_user_ids: list[int] = []

@router.get('/whatsapp/groups/settings')
async def all_group_settings():
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch('''SELECT g.account_key,g.jid,g.society_id,g.enabled,g.eesoc_enabled,
            COALESCE(array_agg(a.user_id) FILTER(WHERE a.user_id IS NOT NULL),'{}'::bigint[]) AS eesoc_user_ids
            FROM public.whatsapp_group_settings g
            LEFT JOIN public.whatsapp_group_eesoc_access a USING(account_key,jid)
            GROUP BY g.account_key,g.jid ORDER BY g.account_key,g.jid''')
        return [dict(r) for r in rows]

@router.put('/whatsapp/groups/settings')
async def set_group_settings(data:GroupConfig):
    async with (await get_pool()).acquire() as conn,conn.transaction():
        await conn.execute('''INSERT INTO public.whatsapp_group_settings(account_key,jid,society_id,enabled,eesoc_enabled)
            VALUES($1,$2,$3,$4,$5) ON CONFLICT(account_key,jid) DO UPDATE SET
            society_id=EXCLUDED.society_id, enabled=EXCLUDED.enabled, eesoc_enabled=EXCLUDED.eesoc_enabled''',
            data.account_key,data.jid,data.society_id,data.enabled,data.eesoc_enabled)
        await conn.execute('DELETE FROM public.whatsapp_group_eesoc_access WHERE account_key=$1 AND jid=$2',data.account_key,data.jid)
        if data.eesoc_user_ids:
            await conn.executemany('''INSERT INTO public.whatsapp_group_eesoc_access(account_key,jid,user_id)
                VALUES($1,$2,$3) ON CONFLICT DO NOTHING''',[(data.account_key,data.jid,u) for u in set(data.eesoc_user_ids)])
        return {'ok':True}

@router.get('/whatsapp/groups/{account_key}/{jid}/allowed')
async def group_allowed(account_key:str,jid:str,society_id:int|None=None,eesoc_user_id:int|None=None):
    async with (await get_pool()).acquire() as conn:
        row=await conn.fetchrow('''SELECT enabled,society_id,eesoc_enabled FROM public.whatsapp_group_settings
            WHERE account_key=$1 AND jid=$2''',account_key,jid)
        if not row or not row['enabled']: return {'allowed':False}
        if society_id is not None:
            return {'allowed':row['society_id']==society_id}
        if eesoc_user_id is not None and row['eesoc_enabled']:
            grant=await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM public.whatsapp_group_eesoc_access
                WHERE account_key=$1 AND jid=$2 AND user_id=$3)''',account_key,jid,eesoc_user_id)
            return {'allowed':bool(grant)}
        return {'allowed':False}

class InviteWrite(BaseModel):
    society_id:int
    invite_ciphertext:str
    requested_by:int

@router.post('/whatsapp/invites')
async def add_invite(data:InviteWrite):
    async with (await get_pool()).acquire() as conn:
        r=await conn.fetchrow('''INSERT INTO public.external_society_group_invites(society_id,invite_ciphertext,requested_by)
            VALUES($1,$2,$3) RETURNING id''',data.society_id,data.invite_ciphertext,data.requested_by)
        return {'id':r['id']}

@router.get('/whatsapp/invites')
async def list_invites(society_id:int|None=None):
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch('''SELECT i.id,i.society_id,s.name AS society_name,i.requested_by,
           i.status,i.joined_jid,i.created_at,i.decided_at
           FROM public.external_society_group_invites i
           JOIN public.external_societies s ON s.id=i.society_id
           WHERE ($1::bigint IS NULL OR i.society_id=$1) ORDER BY i.created_at DESC''',society_id)
        return [dict(r) for r in rows]

@router.get('/whatsapp/invites/{invite_id}/credential')
async def invite_credential(invite_id:int):
    async with (await get_pool()).acquire() as conn:
        r=await conn.fetchrow("SELECT id,society_id,invite_ciphertext,status FROM public.external_society_group_invites WHERE id=$1",invite_id)
        if not r: raise HTTPException(404,'Unknown invitation')
        return dict(r)

class InviteDecision(BaseModel):
    status:str
    decided_by:int
    joined_jid:str|None=None

@router.post('/whatsapp/invites/{invite_id}/decision')
async def decide_invite(invite_id:int,data:InviteDecision):
    if data.status not in ('joined','declined','failed'):
        raise HTTPException(400,'Invalid invitation decision')
    async with (await get_pool()).acquire() as conn:
        row=await conn.fetchrow('''UPDATE public.external_society_group_invites
            SET status=$2,decided_by=$3,joined_jid=$4,decided_at=NOW()
            WHERE id=$1 AND status='pending' RETURNING id''',invite_id,data.status,data.decided_by,data.joined_jid)
        if not row: raise HTTPException(409,'Invitation already decided')
        return {'ok':True}

class CredentialChange(BaseModel):
    credential_ciphertext: str

@router.put('/{society_id}/credential')
async def replace_credential(society_id:int, data:CredentialChange):
    async with (await get_pool()).acquire() as conn:
        updated=await conn.fetchval('''UPDATE public.external_societies
            SET credential_ciphertext=$2,sync_error=NULL WHERE id=$1 RETURNING id''',society_id,data.credential_ciphertext)
        if not updated: raise HTTPException(404,'Unknown society')
        return {'ok':True}

class SocietyGroupToggle(BaseModel):
    society_id: int
    account_key: str
    jid: str
    enabled: bool

@router.patch('/whatsapp/groups/society-enabled')
async def toggle_society_group_enabled(data:SocietyGroupToggle):
    # This operation intentionally cannot overwrite EESoc's group grants.
    # EESoc-managed groups can only be configured in EESoc's own admin panel.
    if data.account_key != f'society-{data.society_id}':
        raise HTTPException(403,'You cannot manage another WhatsApp account')
    async with (await get_pool()).acquire() as conn:
        await conn.execute('''INSERT INTO public.whatsapp_group_settings
            (account_key,jid,society_id,enabled)
            VALUES($1,$2,$3,$4)
            ON CONFLICT(account_key,jid) DO UPDATE SET enabled=EXCLUDED.enabled
            WHERE public.whatsapp_group_settings.society_id=EXCLUDED.society_id''',
            data.account_key,data.jid,data.society_id,data.enabled)
        return {'ok':True}
