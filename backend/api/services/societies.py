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

SOC_ACTIONS = {
    'events.view','events.edit','events.publish','events.tickets.create','events.tickets.edit','events.tickets.delete','events.bookings.view',
    'announcements.preview','announcements.send','whatsapp.view',
    'whatsapp.link','whatsapp.post','whatsapp.groups.manage','whatsapp.group.request',
}
LOGIN_RE = re.compile(r'^[a-z][a-z0-9._-]{1,63}$')


def identity_login(session: AuthSession) -> str:
    """Only a verified Imperial mailbox is proof that somebody owns its login."""
    email = str(session.user.email).strip().lower()
    local, _, domain = email.partition('@')
    if not session.user.email_verified or domain not in ('ic.ac.uk','imperial.ac.uk') or not LOGIN_RE.fullmatch(local):
        raise HTTPException(403,'Sign in with your verified Imperial account to manage a society')
    return local


async def affiliations(session: AuthSession) -> list[dict]:
    return await database.get(f'/external-societies/for-login/{identity_login(session)}') or []


async def require_society(session: AuthSession, society_id: int, action: str | None = None, *, managing: bool = False) -> dict:
    if action and action not in SOC_ACTIONS:
        raise ValueError(f'Unknown society action: {action}')
    society = next((s for s in await affiliations(session) if s['id']==society_id), None)
    if not society:
        raise HTTPException(403,'Not a named committee member of this society')
    if managing and not society['managing']:
        raise HTTPException(403,'This action requires the managing committee role')
    if action and action not in society['permissions']:
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
