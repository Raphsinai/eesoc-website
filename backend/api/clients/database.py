"""Thin transport for the database adapter.

One shared AsyncClient for the process rather than one per call: building a
fresh connection pool per request throws away keep-alive for no benefit.

Callers serialise Pydantic models with `model_dump(mode="json")` — plain
`model_dump()` leaves `datetime` objects in place and httpx's JSON encoder
rejects them.
"""

from __future__ import annotations

from typing import Any
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
from fastapi import HTTPException

from config import settings

_client: httpx.AsyncClient | None = None
_actor_id: ContextVar[int | None] = ContextVar('audit_actor_id', default=None)
_acting_as: ContextVar[int | None] = ContextVar('audit_acting_as', default=None)
_request_id: ContextVar[str | None] = ContextVar('audit_request_id', default=None)


@contextmanager
def audit_context(actor_id: int | None, acting_as_id: int | None, request_id: str):
    tokens = (_actor_id.set(actor_id), _acting_as.set(acting_as_id),
              _request_id.set(request_id))
    try:
        yield
    finally:
        _request_id.reset(tokens[2])
        _acting_as.reset(tokens[1])
        _actor_id.reset(tokens[0])



def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.database_adapter_url, timeout=30)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _clean(params: dict | None) -> dict | None:
    """Drop None values — httpx would otherwise send `?x=None` and the adapter
    would parse the literal string 'None'."""
    if params is None:
        return None
    return {k: v for k, v in params.items() if v is not None}


async def _request(method: str, path: str, **kwargs) -> Any:
    # Internal adapter endpoints receive the resolved actor, not untrusted
    # browser-supplied identity headers. No request body or credentials logged.
    headers = dict(kwargs.pop('headers', {}) or {})
    if _request_id.get() is not None:
        headers['X-Audit-Request-Id'] = _request_id.get()
        headers['X-Audit-Origin'] = 'api-request'
    if _actor_id.get() is not None:
        headers['X-Audit-Actor-Id'] = str(_actor_id.get())
    if _acting_as.get() is not None:
        headers['X-Audit-Acting-As-Id'] = str(_acting_as.get())
    res = await client().request(method, path, headers=headers, **kwargs)
    if res.status_code == 404:
        # 404 is "no such row" throughout the adapter; callers decide whether
        # that is an error or an empty result.
        return None
    if res.status_code >= 400:
        # Pass the adapter's own message through — it carries the useful detail
        # (e.g. "An event with slug 'x' already exists").
        try:
            detail = res.json().get("detail", res.text)
        except Exception:
            detail = res.text
        raise HTTPException(res.status_code, detail)
    if res.status_code == 204 or not res.content:
        return None
    return res.json()


async def get(path: str, params: dict | None = None) -> Any:
    return await _request("GET", path, params=_clean(params))


async def post(path: str, json: Any = None, params: dict | None = None) -> Any:
    return await _request("POST", path, json=json, params=_clean(params))


async def put(path: str, json: Any = None, params: dict | None = None) -> Any:
    return await _request("PUT", path, json=json, params=_clean(params))


async def patch(path: str, json: Any = None, params: dict | None = None) -> Any:
    return await _request("PATCH", path, json=json, params=_clean(params))


async def delete(path: str, params: dict | None = None) -> Any:
    return await _request("DELETE", path, params=_clean(params))
