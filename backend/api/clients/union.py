"""Client for the ICU bridge.

Only the sync job calls this. Nothing in a user request path may, because the
upstream is rate limited and a burst earns an IP ban for the whole society.
"""

from __future__ import annotations

from typing import Any

import httpx

from config import settings

_client: httpx.AsyncClient | None = None


class UnionUnavailable(RuntimeError):
    pass


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # Generous timeout: the members report for a large society is ~130 KB
        # and the upstream is not fast.
        _client = httpx.AsyncClient(base_url=settings.union_api_url, timeout=60)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def configured() -> bool:
    return bool(settings.union_api_url)


async def _get(path: str, params: dict | None = None) -> Any:
    if not configured():
        raise UnionUnavailable("UNION_API_URL is not set")
    try:
        res = await client().get(path, params=params or {})
    except httpx.HTTPError as exc:
        raise UnionUnavailable(str(exc)) from exc
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", res.text)
        except Exception:
            detail = res.text
        raise UnionUnavailable(f"{res.status_code}: {str(detail)[:300]}")
    return res.json()


async def academic_year() -> str:
    data = await _get("/academic-year")
    return data["academic_year"]


async def members(year: str, force: bool = False) -> list[dict]:
    return await _get("/members", {"year": year, "force": force})


async def committee(year: str, force: bool = False) -> list[dict]:
    return await _get("/committee", {"year": year, "force": force})


async def products(year: str, force: bool = False) -> list[dict]:
    return await _get("/products", {"year": year, "force": force})


async def sales(year: str, force: bool = False) -> list[dict]:
    return await _get("/sales", {"year": year, "force": force})


async def health() -> dict:
    return await _get("/health")


async def external_members(centre: str, credential: str, year: str) -> list[dict]:
    if not configured():
        raise UnionUnavailable('Union bridge is not configured')
    try:
        res=await client().post('/external-members',json={
            'centre_code':centre,'credential':credential,'year':year})
    except httpx.HTTPError as exc:
        raise UnionUnavailable('Union bridge is unavailable') from exc
    if res.status_code >= 400:
        raise UnionUnavailable('Union refused external society membership sync')
    return res.json()
