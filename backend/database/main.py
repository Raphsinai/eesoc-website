import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("database")

import migrate
from postgres import connect, disconnect, get_pool
from routes.auth import router as auth_router
from routes.audit import router as audit_router
from routes.checkin import router as checkin_router
from routes.email import router as email_router
from routes.jobs import router as jobs_router
from routes.events import router as events_router
from routes.lockers import router as lockers_router
from routes.opportunities import router as opportunities_router
from routes.passkeys import router as passkeys_router
from routes.photos import router as photos_router
from routes.resources import router as resources_router
from routes.sponsors import router as sponsors_router
from routes.union import router as union_router
from routes.whatsapp import router as whatsapp_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()

    # Schema is brought up to date before a single request is served, so the
    # api service can never see a half-migrated database.
    pool = await get_pool()
    async with pool.acquire() as conn:
        await migrate.backfill_existing(conn)
        result = await migrate.run(conn)
    if result["applied"]:
        log.info("Applied migrations: %s", ", ".join(result["applied"]))
    else:
        log.info("Schema up to date (%d migrations)", result["already"])

    yield
    await disconnect()


app = FastAPI(
    title="EESoc database adapter",
    description=(
        "The only service that speaks SQL. Unauthenticated by design and reachable "
        "only on the compose network — the api service is its sole client and is "
        "where authorisation is enforced."
    ),
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(audit_router)
app.include_router(union_router)
app.include_router(events_router)
app.include_router(lockers_router)
app.include_router(opportunities_router)
app.include_router(resources_router)
app.include_router(sponsors_router)
app.include_router(email_router)
app.include_router(jobs_router)
app.include_router(checkin_router)
app.include_router(passkeys_router)
app.include_router(photos_router)
app.include_router(whatsapp_router)


@app.middleware('http')
async def audit_adapter_write(request, call_next):
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE') or request.url.path == '/audit/actions':
        return await call_next(request)
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        try:
            route = request.scope.get('route')
            path = getattr(route, 'path', '(unmatched)')
            actor_raw = request.headers.get('x-audit-actor-id')
            actor_id = int(actor_raw) if actor_raw else None
            # No body, query parameters, cookies, emails, codes or headers.
            metadata = {'route': path, 'method': request.method,
                        'status': status, 'request_id': request.headers.get('x-audit-request-id'),
                        'origin': request.headers.get('x-audit-origin', 'internal-job'),
                        'acting_as_id': request.headers.get('x-audit-acting-as-id')}
            import json
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""INSERT INTO public.action_audit
                    (actor_user_id, action, entity_type, entity_id, after_data)
                    VALUES ($1,$2,'adapter_write',0,$3::jsonb)""",
                    actor_id, request.method + ' ' + path, json.dumps(metadata))
        except Exception:
            log.exception('Failed to journal adapter mutation %s', request.method)


@app.get("/health")
async def health() -> dict:
    from postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1;")
    return {"ok": True}
