"""Internal-only SQL adapter for connected Union societies and scoped ACLs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
import json
from pydantic import BaseModel, Field
from postgres import get_pool

# Used by event admission and sale reconciliation. Only an authenticated,
# email-verified account may use roster identity, whether through its official
# CID, its verified Union contact address, or its Imperial Login. A shortcode
# merely typed on a guest booking is never proof of society membership.
EXTERNAL_ACCOUNT_MATCH = """(
    u.email_verified AND (
        (u.member_id IS NOT NULL AND em.cid = (
            SELECT own.cid FROM public.members own WHERE own.id=u.member_id))
        OR (em.email IS NOT NULL AND lower(em.email)=lower(u.email))
        OR (em.login IS NOT NULL
            AND lower(split_part(u.email,'@',2)) IN ('ic.ac.uk','imperial.ac.uk')
            AND lower(em.login)=lower(split_part(u.email,'@',1)))
    )
)"""

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
    email: str | None = None
    order_no: str | None = None
    member_type: str | None = None
    union_profile: dict = Field(default_factory=dict)

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
        # Refuse to orphan an event that has no EESoc organiser and no other
        # external organiser. Reassign the event before disconnecting society.
        orphan=await conn.fetchval("""SELECT e.id FROM public.events e
            JOIN public.external_society_events link ON link.event_id=e.id
            WHERE link.society_id=$1 AND NOT e.eesoc_organised
              AND NOT EXISTS (SELECT 1 FROM public.external_society_events other
                  WHERE other.event_id=e.id AND other.society_id<>$1)
            LIMIT 1 FOR UPDATE OF e""",society_id)
        if orphan is not None:
            raise HTTPException(409,f'Reassign event {orphan} before removing its only organiser')
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
                entries.append((society_id,year,m.cid,login or None,m.first_name,m.last_name,
                    (m.email or '').strip().lower() or None,m.order_no,m.member_type,
                    json.dumps(m.union_profile)))
        if entries:
            await conn.executemany("""INSERT INTO public.external_society_members
                (society_id,academic_year,cid,login,first_name,last_name,email,order_no,member_type,union_profile)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)""",entries)
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

@router.get('/lookup-member')
async def lookup_member(email:str, year:str):
    """Union identity for a verified mailbox (API only; never public)."""
    address=email.strip().lower()
    login,_,domain=address.partition('@')
    college=domain in ('ic.ac.uk','imperial.ac.uk')
    async with (await get_pool()).acquire() as conn:
        row=await conn.fetchrow("""SELECT em.society_id,em.cid,em.login,em.first_name,
                   em.last_name,em.email,em.order_no,em.member_type,em.academic_year
            FROM public.external_society_members em
            WHERE em.academic_year=$2 AND (
                (em.email IS NOT NULL AND lower(em.email)=$1)
                OR ($3::boolean AND lower(em.login)=$4))
            ORDER BY (lower(em.email)=$1) DESC, em.society_id LIMIT 1""",
            address,year,college,login)
        if not row: raise HTTPException(404,'No matching member')
        return dict(row)

@router.get('/for-user/{user_id}/memberships')
async def memberships_for_user(user_id:int,year:str):
    """The verified account's own external rosters; includes official CID."""
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch(f"""SELECT s.id AS society_id,s.name AS society_name,
                   em.cid,em.login,em.first_name,em.last_name,em.email,
                   em.order_no,em.member_type,em.academic_year
            FROM public.users u JOIN public.external_society_members em
              ON {EXTERNAL_ACCOUNT_MATCH}
            JOIN public.external_societies s ON s.id=em.society_id
            WHERE u.id=$1 AND em.academic_year=$2 ORDER BY s.name,em.cid""",
            user_id,year)
        return [dict(r) for r in rows]

@router.get('/{society_id}/members')
async def society_member_records(society_id:int,year:str,search:str='',limit:int=100,offset:int=0):
    """Full roster only to callers authorised by the EESoc public API."""
    limit=max(1,min(limit,200))
    term=search.strip().lower()[:100]
    async with (await get_pool()).acquire() as conn:
        rows=await conn.fetch("""SELECT cid,login,first_name,last_name,email,
                order_no,member_type,academic_year,union_profile
            FROM public.external_society_members
            WHERE society_id=$1 AND academic_year=$2 AND (
               $3='' OR lower(cid) LIKE '%'||$3||'%'
                OR lower(coalesce(login,'')) LIKE '%'||$3||'%'
                OR lower(coalesce(first_name,'')||' '||coalesce(last_name,'')) LIKE '%'||$3||'%')
            ORDER BY login NULLS LAST,cid LIMIT $4 OFFSET $5""",
            society_id,year,term,limit,offset)
        total=await conn.fetchval("""SELECT count(*) FROM public.external_society_members
            WHERE society_id=$1 AND academic_year=$2 AND (
                $3='' OR lower(cid) LIKE '%'||$3||'%'
                OR lower(coalesce(login,'')) LIKE '%'||$3||'%'
                OR lower(coalesce(first_name,'')||' '||coalesce(last_name,'')) LIKE '%'||$3||'%')""",
            society_id,year,term)
        members=[]
        for row in rows:
            member=dict(row)
            if isinstance(member.get('union_profile'),str):
                member['union_profile']=json.loads(member['union_profile'])
            members.append(member)
        return {'total':total,'members':members}

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
    async with (await get_pool()).acquire() as conn, conn.transaction():
        current=await conn.fetchrow('SELECT eesoc_organised FROM public.events WHERE id=$1 FOR UPDATE',event_id)
        if current and not current['eesoc_organised']:
            remaining=await conn.fetchval("""SELECT count(*)
                FROM public.external_society_events WHERE event_id=$1 AND society_id<>$2""",event_id,society_id)
            is_assigned=await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM public.external_society_events
                WHERE event_id=$1 AND society_id=$2)""",event_id,society_id)
            if is_assigned and not remaining:
                raise HTTPException(409,'Reassign the event before removing its only organiser')
        await conn.execute("DELETE FROM public.external_society_events WHERE society_id=$1 AND event_id=$2",society_id,event_id)
        return {"ok":True}

@router.get("/{society_id}/events/{event_id}/assigned")
async def assigned(society_id:int,event_id:int):
    async with (await get_pool()).acquire() as conn:
        return {"assigned":bool(await conn.fetchval("SELECT EXISTS(SELECT 1 FROM public.external_society_events WHERE society_id=$1 AND event_id=$2)",society_id,event_id))}

@router.get("/{society_id}/ticket-eligible")
async def ticket_eligible(society_id:int,user_id:int,year:str):
    async with (await get_pool()).acquire() as conn:
        eligible=await conn.fetchval(f"""SELECT EXISTS(
            SELECT 1 FROM public.users u JOIN public.external_society_members em
              ON {EXTERNAL_ACCOUNT_MATCH}
            WHERE u.id=$1 AND em.society_id=$2 AND em.academic_year=$3)""",
            user_id,society_id,year)
        return {'eligible':bool(eligible)}

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
