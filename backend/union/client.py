"""eActivities API client.

Two things drive the design, both from the published docs:

  * "Excessive usage of the API will result is the removal of usage rights", and
    excessive requests earn a 5-minute IP ban; repeated auth failures earn an
    hour. So every call is throttled to a minimum interval and every response is
    cached — nothing in a user request path may reach upstream.
  * Online sales only refresh at 06:00 daily, so a short cache TTL buys nothing.

The client therefore serialises calls behind a lock and serves from cache
whenever it can.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("union.client")

BASE_URL = os.environ.get("ICU_API_BASE", "https://eactivities.union.ic.ac.uk/API")
API_KEY = os.environ.get("ICU_API_KEY", "")
CENTRE = os.environ.get("ICU_CENTRE_CODE", "")

# Sales only move once a day; an hour of cache is generous and still cheap.
CACHE_TTL_SECONDS = int(os.environ.get("ICU_CACHE_TTL", "3600"))
# Floor on the gap between two upstream calls.
MIN_INTERVAL_SECONDS = float(os.environ.get("ICU_MIN_INTERVAL", "2"))

_cache: dict[str, tuple[float, Any]] = {}
_lock = asyncio.Lock()
_last_call = 0.0


class UnionAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"eActivities {status}: {message}")
        self.status = status
        self.message = message


def configured() -> bool:
    return bool(API_KEY and CENTRE)


async def _throttled_get(path: str, params: dict | None = None, *, api_key: str | None = None, not_found_empty: bool = True) -> Any:
    global _last_call
    async with _lock:
        gap = time.monotonic() - _last_call
        if gap < MIN_INTERVAL_SECONDS:
            await asyncio.sleep(MIN_INTERVAL_SECONDS - gap)

        url = f"{BASE_URL}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(
                url,
                params=params or {},
                headers={"X-API-Key": api_key or API_KEY, "Accept": "application/json"},
            )
        _last_call = time.monotonic()

    if res.status_code == 404 and not_found_empty:
        # "No products found" is a legitimate empty state, not a failure — EESoc
        # had no 26-27 products at all when this was written.
        return []
    if res.status_code >= 400:
        try:
            message = res.json().get("message") or res.json().get("Message") or res.text
        except Exception:
            message = res.text
        raise UnionAPIError(res.status_code, str(message)[:300])
    return res.json()


async def get(path: str, params: dict | None = None, force: bool = False) -> Any:
    """Cached GET. `force=True` is what the admin 'sync now' button uses."""
    key = f"{path}?{sorted((params or {}).items())}"
    hit = _cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    data = await _throttled_get(path, params)
    _cache[key] = (time.monotonic(), data)
    return data


def centre_path(suffix: str) -> str:
    return f"/CSP/{CENTRE}{suffix}"


def cache_state() -> dict:
    return {
        "entries": len(_cache),
        "ttl_seconds": CACHE_TTL_SECONDS,
        "min_interval_seconds": MIN_INTERVAL_SECONDS,
        "configured": configured(),
        "centre": CENTRE or None,
    }


def clear_cache() -> None:
    _cache.clear()


async def external_members(centre: str, key: str, year: str) -> list[dict]:
    """No external credentials or responses enter the shared EESoc cache."""
    return await _throttled_get(f"/CSP/{centre}/reports/members", {"year":year}, api_key=key, not_found_empty=False)
