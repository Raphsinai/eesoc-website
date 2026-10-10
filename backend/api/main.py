"""EESoc public API.

Sits behind the frontend's nginx `/api` proxy, so it shares an origin with the
SPA — no CORS, and session cookies work without SameSite=None.

The database adapter and the ICU bridge are internal-only; this service is the
only one the browser can reach, and the only one that enforces authorisation.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from clients import database, union
from config import settings
from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.checkin import router as checkin_router
from routes.committee import router as committee_router
from routes.email import router as email_router
from routes.events import router as events_router
from routes.lockers import router as lockers_router
from routes.passkeys import admin_router as passkey_links_router
from routes.passkeys import router as passkeys_router
from routes.resources import import_seed
from routes.resources import router as resources_router
from routes.search import router as search_router
from routes.seo import router as seo_router
from routes.sponsors import router as sponsors_router
from services import announce as announce_service
from services import emailing as emailing_service
from services import maintenance
from services import storage
from services import sync as sync_service
from utils import email as mailer
from utils.year import academic_year

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")


def _guard_dev_login() -> None:
    """Refuse to start with the authentication bypass enabled outside dev.

    A misconfigured environment variable is exactly how this kind of flag
    reaches production, so it fails loudly at boot rather than quietly opening
    the door.
    """
    if settings.dev_login_enabled and settings.environment != "development":
        raise RuntimeError(
            "DEV_LOGIN_ENABLED is on but ENVIRONMENT is "
            f"'{settings.environment}'. Refusing to start: this flag bypasses "
            "authentication entirely."
        )
    if settings.dev_login_enabled:
        log.warning(
            "DEV LOGIN IS ENABLED — /auth/dev-login will mint a session for any "
            "email with no code. Never run this outside development."
        )


async def _seed_with_retry(attempts: int = 12, delay: float = 5.0) -> None:
    """Import the bundled resource content once the adapter is reachable."""
    for attempt in range(1, attempts + 1):
        try:
            result = await import_seed()
            if result.get("skipped"):
                log.info("Resources already present (%s pages)", result.get("pages"))
            elif result.get("ok"):
                log.info("Seeded %d resource pages", len(result.get("pages", [])))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Resource seeding attempt %d/%d failed (%s); retrying in %.0fs",
                attempt, attempts, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
    log.error("Gave up seeding resources — import manually from /admin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _guard_dev_login()
    maintenance.log_startup_state()
    storage.ensure_root()
    log.info("Media backend: %s", storage.describe())

    # Seed the resource tree on first boot so a fresh deployment is not an empty
    # site. Runs in the background with retries: nothing guarantees the adapter
    # is listening when we start (compose only waits for the container, and k8s
    # does not order pods at all), and the API can serve everything else
    # meanwhile.
    seed_task = asyncio.create_task(_seed_with_retry())

    task: asyncio.Task | None = None
    if union.configured():
        task = asyncio.create_task(sync_service.scheduler())
    else:
        log.warning("UNION_API_URL not set — ICU sync disabled")

    # Separate from the daily ICU sync, and on a much shorter cycle. These two
    # jobs only read our own database, so they cost nothing upstream — and both
    # are time-critical in a way the nightly pass cannot serve: "tickets are on
    # sale" is worthless the morning after, and a last-chance warning that
    # arrives once the hold has already lapsed is worse than none.
    announce_task: asyncio.Task | None = None
    if mailer.configured():
        announce_task = asyncio.create_task(announce_service.scheduler())
        log.info(
            "On-sale announcements and purchase reminders every %d minutes",
            settings.announce_interval_minutes,
        )
    else:
        log.warning("No SMTP credentials — on-sale and reminder emails disabled")

    # A bulk send that the previous pod was mid-flight on is deliberately left
    # `status='sending'` in the database, so this pod — its replacement, under a
    # rolling update — finishes it rather than a half-sent campaign being
    # discovered weeks later. The campaign rows say which sends those are and
    # how to rebuild their messages.
    resume_task = asyncio.create_task(emailing_service.resume_pending())
    log.info("Resuming bulk sends the previous pod left mid-flight, if any")

    yield

    seed_task.cancel()
    for background in (task, announce_task, resume_task):
        if background is None:
            continue
        background.cancel()
        try:
            await background
        except asyncio.CancelledError:
            pass
    await database.close()
    await union.close()


app = FastAPI(
    title="EESoc API",
    description="Public API for the EESoc site.",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(events_router)
app.include_router(lockers_router)
app.include_router(committee_router)
app.include_router(email_router)
app.include_router(resources_router)
app.include_router(sponsors_router)
app.include_router(search_router)
app.include_router(seo_router)
app.include_router(admin_router)
app.include_router(checkin_router)
app.include_router(passkeys_router)
app.include_router(passkey_links_router)


@app.get("/health")
async def health() -> dict:
    """Liveness. Deliberately does not touch the database — a liveness probe that
    fails on a transient database blip gets the pod killed for no reason."""
    return {
        "ok": True,
        "environment": settings.environment,
        "academic_year": academic_year(),
    }


@app.get("/status")
async def site_status() -> dict:
    """What the SPA renders its "under maintenance" banners from.

    Public and unauthenticated: the whole point is that somebody who cannot sign
    in is told why. It leaks no more than trying the feature would.
    """
    return maintenance.status()


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness. This one *does* check dependencies, because a pod that cannot
    reach the adapter should be taken out of the load balancer."""
    checks = {}
    try:
        await database.get("/health")
        checks["database"] = True
    except Exception:
        checks["database"] = False

    checks["media"] = await storage.healthy()
    ok = all(checks.values())
    return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)
