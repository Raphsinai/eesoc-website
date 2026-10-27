"""Persistent role defaults. Changes are audited in the same transaction."""
from __future__ import annotations

import json
from fastapi import APIRouter, Body, HTTPException
from asyncpg.exceptions import UniqueViolationError
from postgres import get_pool

router = APIRouter(prefix='/committee-roles')


@router.get('')
async def list_roles() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT id,label,blurb,capabilities,updated_by,updated_at
            FROM public.committee_roles ORDER BY CASE id
              WHEN 'superuser' THEN 0 WHEN 'admin' THEN 1
              WHEN 'events' THEN 2 WHEN 'industry' THEN 3 ELSE 4 END, label""")
    return [dict(row) for row in rows]


@router.post('')
async def create_role(id: str = Body(embed=True), label: str = Body(embed=True),
                      blurb: str = Body(default='', embed=True),
                      capabilities: list[str] = Body(embed=True),
                      actor_id: int = Body(embed=True)) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        try:
            row = await conn.fetchrow("""INSERT INTO public.committee_roles
                (id,label,blurb,capabilities,updated_by) VALUES
                ($1,$2,$3,$4::text[],$5)
                RETURNING id,label,blurb,capabilities,updated_at""",
                id,label,blurb,capabilities,actor_id)
        except UniqueViolationError as exc:
            raise HTTPException(409, 'A role with that ID already exists') from exc
        await conn.execute("""INSERT INTO public.action_audit(actor_user_id,action,entity_type,entity_id,after_data)
            VALUES($1,'access.roles.create','committee_role',0,
                jsonb_build_object('id',$2::text,'label',$3::text,'capabilities',$4::text[]))""",
            actor_id,id,label,capabilities)
    return dict(row)


@router.put('/{role_id}')
async def edit_role(role_id: str, label: str = Body(embed=True),
                    blurb: str = Body(default='',embed=True),
                    capabilities: list[str] = Body(embed=True),
                    actor_id: int = Body(embed=True)) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        old = await conn.fetchrow("SELECT label,blurb,capabilities FROM public.committee_roles WHERE id=$1 FOR UPDATE", role_id)
        if old is None:
            raise HTTPException(404, 'No such role')
        row = await conn.fetchrow("""UPDATE public.committee_roles
            SET label=$2,blurb=$3,capabilities=$4::text[],updated_by=$5,updated_at=NOW()
            WHERE id=$1 RETURNING id,label,blurb,capabilities,updated_at""",
            role_id,label,blurb,capabilities,actor_id)
        await conn.execute("""INSERT INTO public.action_audit(actor_user_id,action,entity_type,entity_id,before_data,after_data)
            VALUES($1,'access.roles.edit','committee_role',0,
                jsonb_build_object('id',$2::text,'label',$3::text,'capabilities',$4::text[]),
                jsonb_build_object('id',$2::text,'label',$5::text,'capabilities',$6::text[]))""",
            actor_id,role_id,old['label'],old['capabilities'],label,capabilities)
    return dict(row)
