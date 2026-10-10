"""Pulling eActivities into the local mirror, and reconciling reservations.

Runs once a day shortly after 06:00, because that is when the Union's sales feed
has refreshed — polling more often would just burn requests against a rate limit
that bans the whole society's IP when exceeded. The committee roster is the
exception: it only changes at handover, so it is pulled on 1 August or on
demand.

Every step records a row in `union_sync_runs`, so a failure is visible on the
admin screen instead of silently leaving stale data behind.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from clients import database, union
from services import announce, confirmations, purchase
from utils import email as mailer
from config import settings
from utils.present import format_date, format_doors_close, format_time_range, london
from utils.year import academic_year

log = logging.getLogger("api.sync")

# The in-process half of the run guard: two requests hitting this pod must not
# interleave sync steps.
_lock = asyncio.Lock()


@asynccontextmanager
async def _lease(name: str, ttl_seconds: int = 900):
    """The cross-pod half of the run guard: a named lease in the database.

    A rolling update starts the replacement pod before stopping the old one,
    so for a minute there are two api processes that each believe they are
    alone — and the whole point of the daily job is that it runs once, not
    once per pod: the Union rate-limits per source IP and a breach bans the
    whole society. The in-process lock above cannot see the other pod; a lease
    row can, because both pods talk to the same database through the adapter.

    Released in `finally`, so a graceful shutdown hands the lease straight
    back; the TTL is the takeover path for a pod that died mid-sync without
    releasing.
    """
    holder = uuid.uuid4().hex
    claim = await database.post(
        "/job-leases/claim",
        {"name": name, "holder": holder, "ttl_seconds": ttl_seconds},
    )
    if not (claim or {}).get("claimed"):
        yield False
        return
    try:
        yield True
    finally:
        await database.post(
            "/job-leases/release", {"name": name, "holder": holder}
        )


async def _run_step(kind: str, year: str, fetch, store) -> dict:
    run_id = await database.post("/union/sync-runs", params={"kind": kind, "academic_year": year})
    try:
        rows = await fetch()
        count = await store(rows)
        await database.post(
            f"/union/sync-runs/{run_id}/finish",
            params={"ok": True, "row_count": count},
        )
        return {"kind": kind, "ok": True, "rows": count}
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        log.exception("Sync step %s failed", kind)
        await database.post(
            f"/union/sync-runs/{run_id}/finish",
            params={"ok": False, "message": str(exc)[:400]},
        )
        return {"kind": kind, "ok": False, "error": str(exc)[:400]}


async def sync_all(force: bool = False, year: str | None = None) -> dict:
    """Full pull, then reconcile. Steps are independent: a failure in one does
    not abandon the others, because a stale products list should not stop new
    sales from confirming bookings."""
    if not union.configured():
        return {"ok": False, "error": "UNION_API_URL is not set"}

    year = year or academic_year()

    # The committee changes once a year, on handover. Re-pulling it nightly
    # would only risk wiping the ordering and profiles the committee has set
    # against a feed that has not changed. `force` is the admin's "sync now".
    today = datetime.now(timezone.utc)
    sync_committee_today = today.month == 8 and today.day == 1

    # The lease, not the in-process lock, is what keeps a rolling update's two
    # pods from both running the daily sync. Both guards are needed: the lease
    # against the other pod, the lock against a second request on this one.
    async with _lease("daily-union-sync") as held:
        if not held:
            log.info("Another pod is running the union sync — skipping")
            # ok, not an error: the work is happening, just elsewhere, and a
            # skipped sync is infinitely better than a doubled one against a
            # rate limit that bans the society.
            return {"ok": True, "skipped": True}

        async with _lock:
            results = [
                await _run_step(
                    "members", year,
                    lambda: union.members(year, force),
                    lambda rows: database.post(
                        "/union/members/sync", rows, params={"academic_year": year}
                    ),
                ),
                *(
                    [
                        await _run_step(
                            "committee", year,
                            lambda: union.committee(year, force),
                            lambda rows: database.post(
                                "/union/committee/sync", rows, params={"academic_year": year}
                            ),
                        )
                    ]
                    if sync_committee_today or force
                    else []
                ),
                await _run_step(
                    "products", year,
                    lambda: union.products(year, force),
                    lambda rows: database.post(
                        "/union/products/sync", rows, params={"academic_year": year}
                    ),
                ),
                await _run_step(
                    "sales", year,
                    lambda: union.sales(year, force),
                    lambda rows: database.post(
                        "/union/sales/sync", rows, params={"academic_year": year}
                    ),
                ),
            ]

            # Order matters: expire first so a stale hold cannot claim a sale that
            # should go to somebody still waiting, then match against fresh sales.
            expired = await database.post("/reservations/expire")
            matched = await database.post(
                "/reservations/match", params={"academic_year": year}
            )
            # Expiring a hold frees a seat, so the queue is worked immediately
            # afterwards rather than a day later.
            promoted = await database.post("/waitlist/promote")
            notified = await notify_waitlist_offers((promoted or {}).get("offered", []))
            swept = await database.post("/auth/sessions/sweep")

    # Belt and braces. The announcer has its own ten-minute loop; running it
    # here as well means a night when that loop was down still gets worked
    # through, and the claim columns make the second run a no-op when it was
    # not.
    try:
        announced = await announce.run_once()
    except Exception:  # noqa: BLE001
        log.exception("Announce pass inside the daily sync failed")
        announced = {"error": True}

    return {
        "ok": all(r["ok"] for r in results),
        "academic_year": year,
        "steps": results,
        "expired_reservations": expired,
        "matched": matched,
        "waitlist_promoted": (promoted or {}).get("count", 0),
        "waitlist_notified": notified,
        "committee_synced": sync_committee_today or force,
        "sessions_swept": swept,
        "announced": announced,
    }


async def reconcile_only() -> dict:
    """Expire, match, then work the waitlist — no upstream calls, so this is
    cheap enough to run whenever an order number is typed in."""
    expired = await database.post("/reservations/expire")
    matched = await database.post(
        "/reservations/match", params={"academic_year": academic_year()}
    )
    promoted = await database.post("/waitlist/promote")
    notified = await notify_waitlist_offers((promoted or {}).get("offered", []))
    if (matched or {}).get("confirmed"):
        confirmations.trigger()
    return {
        "expired": expired,
        **(matched or {}),
        "promoted": (promoted or {}).get("count", 0),
        "notified": notified,
    }


def _seconds_until_next_run() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(
        hour=settings.sync_hour, minute=settings.sync_minute, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _dependencies_up(timeout_seconds: float = 120.0) -> bool:
    """Wait until the database adapter and the union bridge both answer.

    Nothing orders startup — compose waits only for containers to exist, k8s
    not at all — so the startup sync used to run before the adapter was
    listening, fail on its lease, and never be tried again: the test stack
    then had no members, and every member-shaped test failed.

    Only the two services' own /health is polled, and the bridge's answers
    from its local cache state: waiting here costs eActivities nothing. The
    sync itself still runs exactly once, because retrying *that* could repeat
    upstream calls against a rate limit that bans the society.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            await database.get("/health")
            await union.health()
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(2)


async def scheduler() -> None:
    """Background loop. Cancelled on shutdown by the lifespan handler."""
    if settings.sync_on_startup:
        if await _dependencies_up():
            try:
                await sync_all()
            except Exception:
                log.exception("Startup sync failed")
        else:
            log.error(
                "Startup sync skipped: the database adapter or the union bridge "
                "never came up"
            )

    while True:
        delay = _seconds_until_next_run()
        log.info("Next ICU sync in %.1f hours", delay / 3600)
        try:
            await asyncio.sleep(delay)
            await sync_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad night kill the loop.
            log.exception("Scheduled sync failed")
            await asyncio.sleep(300)


async def sync_committee_now(force: bool = True) -> dict:
    """Pull the committee roster on demand.

    Separate from the daily sync because the roster only changes at handover:
    the scheduled job skips it except on 1 August, so this is how a mid-year
    change (a resignation, a by-election) gets picked up. Intros, photos and
    ordering survive — they are keyed on login and stored apart from the roster.
    """
    if not union.configured():
        return {"ok": False, "error": "UNION_API_URL is not set"}

    year = academic_year()
    # Same two guards as the daily sync, for the same two concurrent callers:
    # a second request on this pod (the lock), a second pod (the lease).
    async with _lease("union-committee-pull") as held:
        if not held:
            log.info("Another pod is pulling the committee roster — skipping")
            return {"ok": True, "skipped": True}

        async with _lock:
            result = await _run_step(
                "committee", year,
                lambda: union.committee(year, force),
                lambda rows: database.post(
                    "/union/committee/sync", rows, params={"academic_year": year}
                ),
            )
    return {"ok": result["ok"], "academic_year": year, **result}


async def notify_waitlist_offers(offers: list[dict]) -> int:
    """Email everyone who has just been given a freed place.

    Promotion and notification are separate steps on purpose: the seat is held
    in the database first, so a mail failure cannot lose somebody their place.
    `notified_at` records who has been told, so a retry cannot email twice.
    """
    sent = 0
    for offer in offers or []:
        try:
            user = await database.get(f"/auth/users/{offer['user_id']}")
            event = await database.get(f"/events/{offer['event_id']}")
            reservation = await database.get(f"/reservations/{offer['reservation_id']}")
            if not (user and event and reservation):
                continue

            ticket_name = "your ticket"
            for ticket in event.get("ticket_types", []):
                if ticket["id"] == reservation["ticket_type_id"]:
                    ticket_name = ticket["name"]
                    break

            expires = _readable(reservation.get("expires_at"))
            url = f"{settings.site_origin}/events/{event['slug']}"
            link = await purchase.link_for(event, reservation["ticket_type_id"])

            if await mailer.send_waitlist_offer(
                str(user["email"]), event["title"], ticket_name, expires, url,
                purchase_url=link,
                doors_close=format_doors_close(
                    event.get("starts_at"), event.get("doors_close_at")
                ),
            ):
                sent += 1
            # Marked either way: an unsendable address must not cause a retry
            # loop that re-mails everybody else on the next pass.
            await database.post(f"/waitlist/{offer['waitlist_id']}/notified")
        except Exception:  # noqa: BLE001
            log.exception("Could not notify waitlist offer %s", offer.get("waitlist_id"))
    return sent


def _readable(iso: str | None) -> str:
    """A deadline a person can act on, not an ISO timestamp."""
    if not iso:
        return "soon"
    try:
        when = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{format_date(when)} at {london(when).strftime('%H:%M')}"
