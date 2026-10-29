"""Internal API and adapter action journal. No raw payloads or auth secrets."""
from __future__ import annotations

import json
from fastapi import APIRouter, Body, HTTPException
from postgres import get_pool

router = APIRouter()


@router.post('/audit/actions')
async def append_action(entry: dict = Body(...)) -> dict:
    action = str(entry.get('action', ''))[:240]
    if not action:
        raise HTTPException(400, 'Action required')
    actor_id = entry.get('actor_user_id')
    meta = entry.get('meta') or {}
    if not isinstance(meta, dict):
        raise HTTPException(400, 'Invalid audit metadata')
    pool = await get_pool()
    async with pool.acquire() as conn:
        key = await conn.fetchval("""INSERT INTO public.action_audit
            (actor_user_id, action, entity_type, entity_id, after_data)
            VALUES ($1,$2,'api_request',0,$3::jsonb) RETURNING id""",
            actor_id, action, json.dumps(meta))
    return {'id': key}


@router.get('/audit/actions')
async def get_actions(limit: int = 100, actor_user_id: int | None = None,
                      include_requests: bool = False) -> list[dict]:
    if limit < 1 or limit > 200:
        raise HTTPException(400, 'Limit must be between 1 and 200')
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT a.id, a.at, a.actor_user_id, u.email AS actor_email,
                   a.action, a.entity_type, a.entity_id, a.before_data, a.after_data
              FROM public.action_audit a
              LEFT JOIN public.users u ON u.id=a.actor_user_id
             WHERE ($1::bigint IS NULL OR a.actor_user_id=$1)
               AND ($3::boolean OR a.entity_type NOT IN ('api_request','adapter_write'))
             ORDER BY a.id DESC LIMIT $2""", actor_user_id, limit, include_requests)
    result = []
    for row in rows:
        record = dict(row)
        for column in ('before_data','after_data'):
            value = record[column]
            if isinstance(value, str):
                try:
                    record[column] = json.loads(value)
                except ValueError:
                    record[column] = {'summary': 'Unable to decode stored change metadata'}
        result.append(record)
    return result
