"""The WhatsApp link, from the Email tab.

Linking the society's number (a QR code to scan, or a code to type on the
phone), and the groups it is in. Every group it can post in is then an
audience like any other — ticked under "Who gets it" and sent by the same Send
(routes/admin.py, services/campaigns.py).

The separate `whatsapp` capability controls linking, groups and posting. services/whatsapp.py says what the link is, and
why it is handled carefully.
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from clients import database
from clients import whatsapp as link
from config import settings
from models.auth import AuthSession
from services import whatsapp
from utils.deps import require_action, require_admin, require_feature, require_whatsapp, verify_csrf

log = logging.getLogger("api.whatsapp")

router = APIRouter(
    prefix="/admin/whatsapp",
    dependencies=[Depends(require_admin), require_feature("admin")],
)


def _qr_svg(data: str) -> str:
    """The linking QR as an SVG data URL. SVG, so no image library is needed —
    `qrcode` draws it itself — and it stays sharp at any size."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    svg = image.to_string(encoding="unicode")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _status(raw: dict) -> dict:
    return {
        "state": raw.get("state"),
        "error": raw.get("error"),
        "since": raw.get("since"),
        "me": raw.get("me"),
        "qr": _qr_svg(raw["qr"]) if raw.get("qr") else None,
        "pairingCode": raw.get("pairingCode"),
        "postsThisHour": raw.get("postsThisHour"),
        "maxPerHour": raw.get("maxPerHour"),
    }


async def _status_or_problem() -> tuple[dict | None, str | None]:
    try:
        return _status(await link.status()), None
    except link.WhatsAppUnavailable as exc:
        return None, str(exc)


def _post_summary(p: dict) -> dict:
    return {
        "id": p["id"],
        "group": p.get("group_name") or p["jid"],
        "status": p["status"],
        "error": p.get("error"),
        "campaignId": p.get("campaign_id"),
        "createdAt": p.get("created_at"),
        "sentAt": p.get("sent_at"),
        "preview": " ".join((p.get("body") or "").split())[:140],
    }


@router.get("", dependencies=[Depends(require_action("whatsapp.view"))])
async def overview(_: AuthSession = Depends(require_whatsapp)) -> dict:
    """Everything the WhatsApp card shows: linked or not, the groups the
    number is in (and which it can post in), and the latest posts."""
    status, problem = await _status_or_problem()

    groups = None
    if status and status["state"] == "connected":
        try:
            groups = [{**g, "allowed": whatsapp.allowed(g["jid"])} for g in await link.groups()]
        except link.WhatsAppUnavailable as exc:
            problem = str(exc)

    allowlist = whatsapp.allowlist()
    posts = await database.get("/whatsapp/posts", {"limit": 15}) or []
    return {
        "configured": link.configured(),
        "environment": settings.environment,
        "status": status,
        "problem": problem,
        "groups": groups,
        # WHATSAPP_GROUP_ALLOWLIST, when set: shown, so a group it leaves out
        # is not mistaken for a fault.
        "allowlist": sorted(allowlist) if allowlist is not None else None,
        "posts": [_post_summary(p) for p in posts],
    }


@router.get("/targets", dependencies=[Depends(require_action("whatsapp.groups"))])
async def targets(_: AuthSession = Depends(require_whatsapp)) -> dict:
    """The groups a communication can be posted into — for the WhatsApp
    pickers beside the sends that have no audience list of their own (the
    roles digest, a sponsor's marketing). `environment`, so staging can say
    that its WhatsApp is the real one before anything is posted."""
    return {
        "configured": link.configured(),
        "environment": settings.environment,
        "groups": await whatsapp.available(),
    }


@router.post("/link", dependencies=[Depends(require_action("whatsapp.link")), Depends(verify_csrf)])
async def start_link(
    phone: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_whatsapp),
) -> dict:
    """Start linking the society's number. With `phone`, WhatsApp gives a code
    to type on the phone; without, a QR code to scan."""
    try:
        status = _status(await link.link(phone))
    except link.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log.info("WhatsApp linking started by user %s", session.user.id)
    return status


@router.post("/unlink", dependencies=[Depends(require_action("whatsapp.unlink")), Depends(verify_csrf)])
async def unlink(session: AuthSession = Depends(require_whatsapp)) -> dict:
    try:
        status = _status(await link.unlink())
    except link.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log.info("WhatsApp unlinked by user %s", session.user.id)
    return status
