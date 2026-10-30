"""Cross-society identity and capability checks; never trust a browser society ID."""
from __future__ import annotations

import base64
import hashlib
import re

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from clients import database
from config import settings
from models.auth import AuthSession
from utils.year import academic_year

from utils import roles as role_table

# Event actions are the same keys as EESoc committee permissions. A society
# cannot acquire a global scope or operate the EESoc-wide Union sync.
SOC_ACTIONS = frozenset(p for p in role_table.PERMISSIONS if (
    p.startswith('events.') and p not in {
        'events.scope.all','events.scope.industry',
        'events.sync.union','events.sync.reconcile',
    }) or p in {
        'email.audiences','email.preview','email.send','email.announce_event',
        'access.view','access.grant','access.revoke',
        'access.roles.create','access.roles.edit',
        'societies.members.view','societies.members.export',
    }
) | {
    'announcements.preview','announcements.send',
    'whatsapp.view','whatsapp.link','whatsapp.post',
    'whatsapp.groups.manage','whatsapp.group.request',
}
LEGACY = {
    'events.tickets.create':'events.ticket_types.create',
    'events.tickets.edit':'events.ticket_types.edit',
    'events.tickets.delete':'events.ticket_types.delete',
    'announcements.preview':'email.preview',
    'announcements.send':'email.send',
}


def grants(items: list[str] | None) -> frozenset[str]:
    actions = set(items or [])
    actions.update(LEGACY[p] for p in list(actions) if p in LEGACY)
    if 'announcements.send' in actions:
        actions.add('email.announce_event')
    if 'announcements.preview' in actions:
        actions.add('email.audiences')
    return frozenset(actions & SOC_ACTIONS)


def permission_catalogue() -> list[dict]:
    sections = []
    for group, subgroups in role_table.GROUPS.items():
        entries = []
        for subgroup, permissions in subgroups.items():
            actions = [{'id':k,'label':v} for k,v in permissions.items()
                       if k in SOC_ACTIONS]
            if actions:
                entries.append({'name':subgroup,'actions':actions})
        if entries:
            sections.append({'group':group,'sections':entries})
    sections.append({'group':'Society WhatsApp','sections':[{'name':'Own groups','actions':[
        {'id':key,'label':label} for key,label in (
            ('whatsapp.view','View society groups'),
            ('whatsapp.post','Post to assigned groups'),
            ('whatsapp.link','Link the society WhatsApp number'),
            ('whatsapp.groups.manage','Enable or disable society groups'),
            ('whatsapp.group.request','Request EESoc to join a group'),
        )
    ]}]})
    return sections


class ScopedCommitteeSession:
    """Event-scoped adapter for existing EESoc booking/check-in services.

    Construct only after verifying a society committee membership AND the
    targeted event is assigned to the same society. Real audit user preserved.
    """
    def __init__(self, session: AuthSession, society: dict):
        self.user = session.user
        self.is_admin = True
        self.society_id = society['id']
        self.permissions = grants(society['permissions'])

    def can(self, key: str) -> bool:
        if key in self.permissions:
            return True
        if key == 'events.scope.all':
            return True  # Only checked, explicitly assigned event requests.
        if key == 'events':
            return any(p in self.permissions for p in (
                'events.create','events.edit','events.publish','events.delete',
                'events.ticket_types.create','events.ticket_types.edit',
                'events.ticket_types.delete',
            ))
        if key == 'bookings':
            return any(p.startswith(('events.bookings.','events.checkin.'))
                       for p in self.permissions)
        if key == 'societies.events.assign':
            return True  # Only when the caller's organiser is forced in body.
        return False


LOGIN_RE = re.compile(r'^[a-z][a-z0-9._-]{1,63}$')


def identity_login(session: AuthSession) -> str:
    """Only a verified Imperial mailbox is proof that somebody owns its login."""
    email = str(session.user.email).strip().lower()
    local, _, domain = email.partition('@')
    if not session.user.email_verified or domain not in ('ic.ac.uk','imperial.ac.uk') or not LOGIN_RE.fullmatch(local):
        raise HTTPException(403,'Sign in with your verified Imperial account to manage a society')
    return local


async def verified_identity_login(session: AuthSession) -> str | None:
    """Resolve a society committee Login from an authenticated Union identity.

    The email's local part may be firstname.lastname rather than the official
    shortcode; a verified personal contact address can also appear on a synced
    Union society roster. Never use a user-typed shortcode to grant access.
    """
    if not session.user.email_verified:
        raise HTTPException(403, 'Verify your email to manage a society')
    email = str(session.user.email).strip().lower()
    local, _, domain = email.partition('@')
    # Shortcode@Imperial is already proof of ownership of that Login.
    from services.identity import is_shortcode
    if domain in ('ic.ac.uk', 'imperial.ac.uk') and is_shortcode(local):
        return local
    # The EESoc Union record is linked to this verified account by member_id,
    # not by the browser's editable shortcode field.
    member = session.member
    if (member and session.user.member_id is not None
            and member.id == session.user.member_id and member.login
            and LOGIN_RE.fullmatch(member.login.lower())):
        return member.login.lower()
    # The external adapter matches the verified account by its official CID,
    # exact verified email, or a genuine Imperial address; it never trusts the
    # account's typed shortcode when matching a personal email.
    rows = (await database.get(
        f'/external-societies/for-user/{session.user.id}/memberships',
        {'year': academic_year()},
    ) or []) if session.user.id else []
    official = {str(row['login']).lower() for row in rows
                if row.get('login') and LOGIN_RE.fullmatch(str(row['login']).lower())}
    if len(official) > 1:
        raise HTTPException(409, 'Conflicting Union logins on linked society records')
    if official:
        return next(iter(official))
    # Staff or alumni with a verified Imperial email may not be on the current
    # membership roster, but still have an explicitly named committee Login.
    if domain in ('ic.ac.uk','imperial.ac.uk') and LOGIN_RE.fullmatch(local):
        return local
    return None


async def affiliations(session: AuthSession) -> list[dict]:
    login = await verified_identity_login(session)
    if not login:
        return []
    return await database.get(f'/external-societies/for-login/{login}') or []


async def require_society(session: AuthSession, society_id: int, action: str | None = None, *, managing: bool = False) -> dict:
    if action and action not in SOC_ACTIONS:
        raise ValueError(f'Unknown society action: {action}')
    society = next((s for s in await affiliations(session) if s['id']==society_id), None)
    if not society:
        raise HTTPException(403,'Not a named committee member of this society')
    if managing and not society['managing']:
        raise HTTPException(403,'This action requires the managing committee role')
    if action and action not in grants(society['permissions']):
        raise HTTPException(403,f'Society permission required: {action}')
    return society


async def require_event(society_id:int,event_id:int) -> dict:
    allowed=await database.get(f'/external-societies/{society_id}/events/{event_id}/assigned')
    if not allowed or not allowed['assigned']:
        raise HTTPException(403,'This event is not assigned to your society')
    event=await database.get(f'/events/{event_id}')
    if not event:
        raise HTTPException(404,'Unknown event')
    return event


def cipher() -> Fernet:
    secret=settings.external_society_secret
    if not secret or len(secret)<32:
        raise HTTPException(503,'External society credentials are not configured on the server')
    key=base64.urlsafe_b64encode(hashlib.sha256(secret.encode('utf-8')).digest())
    return Fernet(key)


def encrypt(raw: str) -> str:
    return cipher().encrypt(raw.encode('utf-8')).decode('ascii')


def decrypt(raw: str) -> str:
    try:
        return cipher().decrypt(raw.encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise HTTPException(503,'Cannot decrypt saved society credential') from exc


async def mailing_list(society_id:int) -> list[dict]:
    return await database.get(f'/external-societies/{society_id}/audience', {'year': academic_year()}) or []


async def eligible_societies(user_id:int|None, ids:list[int]) -> set[int]:
    """Read the verified account's Union identities, not a browser shortcode.

    Users linked to EESoc via CID, members on a society Union contact email,
    and verified Imperial Login aliases are all covered by the same SQL gate.
    """
    if not user_id or not ids:
        return set()
    matched=set()
    for society_id in set(ids):
        row=await database.get(f'/external-societies/{society_id}/ticket-eligible', {
            'user_id':user_id,'year':academic_year()})
        if row and row.get('eligible'):
            matched.add(society_id)
    return matched
