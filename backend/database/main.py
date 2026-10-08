import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("database")

import migrate
from postgres import connect, disconnect, get_pool
from routes.auth import router as auth_router
from routes.checkin import router as checkin_router
from routes.email import router as email_router
from routes.jobs import router as jobs_router
from routes.events import router as events_router
from routes.lockers import router as lockers_router
from routes.resources import router as resources_router
from routes.sponsors import router as sponsors_router
from routes.union import router as union_router


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
app.include_router(union_router)
app.include_router(events_router)
app.include_router(lockers_router)
app.include_router(resources_router)
app.include_router(sponsors_router)
app.include_router(email_router)
app.include_router(jobs_router)
app.include_router(checkin_router)


@app.get("/health")
async def health() -> dict:
    from postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1;")
    return {"ok": True}
