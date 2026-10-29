"""Client for the WhatsApp link (backend/whatsapp).

The service holds the society's number as a linked device; this only asks it
things. Every failure — not configured, not running, not linked, refused —
comes back as `WhatsAppUnavailable` with the service's own words, so a screen
can say what is wrong instead of a 500.
"""

from __future__ import annotations

from typing import Any

import httpx

from config import settings

_client: httpx.AsyncClient | None = None


class WhatsAppUnavailable(RuntimeError):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # Long enough for a post that waits its turn behind a few others: the
        # service spaces posts several seconds apart on purpose.
        _client = httpx.AsyncClient(base_url=settings.whatsapp_api_url, timeout=90)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def configured() -> bool:
    return bool(settings.whatsapp_api_url)


async def _call(method: str, path: str, body: dict | None = None) -> Any:
    if not configured():
        raise WhatsAppUnavailable("WhatsApp posting isn't set up here (WHATSAPP_API_URL).")
    try:
        res = await client().request(method, path, json=body)
    except httpx.HTTPError as exc:
        raise WhatsAppUnavailable(f"The WhatsApp service isn't answering ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", res.text)
        except Exception:  # noqa: BLE001
            detail = res.text
        # The service's refusals (400, 409, 429) keep their status. Its own
        # failures become 503, never 502: Cloudflare swaps a 502 from the site
        # for its HTML error page, and the screen would show that instead of
        # what went wrong.
        status = 503 if res.status_code >= 500 else res.status_code
        raise WhatsAppUnavailable(str(detail)[:300], status)
    return res.json()


def _prefix(account: str) -> str:
    import re
    if account == 'eesoc': return ''
    if not re.fullmatch(r'society-[1-9][0-9]*',account):
        raise WhatsAppUnavailable('Invalid society WhatsApp account',400)
    return f'/accounts/{account}'


async def status(account: str = "eesoc") -> dict:
    return await _call("GET", f"{_prefix(account)}/status")


async def link(phone: str | None = None, account: str = "eesoc") -> dict:
    return await _call("POST", f"{_prefix(account)}/link", {"phone": phone} if phone else {})


async def unlink(account: str = "eesoc") -> dict:
    return await _call("POST", f"{_prefix(account)}/unlink")


async def groups(account: str = "eesoc") -> list[dict]:
    return (await _call("GET", f"{_prefix(account)}/groups"))["groups"]


async def send(jid: str, text: str, key: str, account: str = "eesoc") -> dict:
    return await _call("POST", f"{_prefix(account)}/send", {"jid": jid, "text": text, "key": key})


async def join_invite(code: str) -> dict:
    return await _call('POST','/join-invite',{'code':code})
