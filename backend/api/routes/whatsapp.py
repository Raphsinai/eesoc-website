"""The WhatsApp link, from the Email tab.

Linking the society's number (a QR code to scan, or a code to type on the
phone) and enabling the groups the site may post into. An enabled group is
then an audience like any other — ticked under "Who gets it" and sent by the
same Send (routes/admin.py, services/campaigns.py).

The same people as email — the `site` capability — because every post here is
an announcement to a crowd. services/whatsapp.py says what the link is, and
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
from utils.deps import require_admin, require_feature, require_site, verify_csrf

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


@router.get("")
async def overview(_: AuthSession = Depends(require_site)) -> dict:
    """Everything the WhatsApp card shows: linked or not, the groups, which
    are chosen, and the latest posts."""
    status, problem = await _status_or_problem()
    chosen = await whatsapp.chosen()
    chosen_jids = {g["jid"] for g in chosen}

    groups = None
    if status and status["state"] == "connected":
        try:
            groups = [
                {**g, "chosen": g["jid"] in chosen_jids, "allowed": whatsapp.allowed(g["jid"])}
                for g in await link.groups()
            ]
        except link.WhatsAppUnavailable as exc:
            problem = str(exc)

    allowlist = whatsapp.allowlist()
    posts = await database.get("/whatsapp/posts", {"limit": 15}) or []
    return {
        "configured": link.configured(),
        # Staging's card asks before a group is enabled: its number may be the
        # society's real one, and its groups the real community.
        "environment": settings.environment,
        "status": status,
        "problem": problem,
        "groups": groups,
        "chosen": [
            {"jid": g["jid"], "name": g.get("name") or "", "allowed": whatsapp.allowed(g["jid"])}
            for g in chosen
        ],
        # WHATSAPP_GROUP_ALLOWLIST, when set: shown, so a group it leaves out
        # is not mistaken for a fault.
        "allowlist": sorted(allowlist) if allowlist is not None else None,
        "posts": [_post_summary(p) for p in posts],
    }


@router.post("/link", dependencies=[Depends(verify_csrf)])
async def start_link(
    phone: str | None = Body(default=None, embed=True),
    session: AuthSession = Depends(require_site),
) -> dict:
    """Start linking the society's number. With `phone`, WhatsApp gives a code
    to type on the phone; without, a QR code to scan."""
    try:
        status = _status(await link.link(phone))
    except link.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log.info("WhatsApp linking started by user %s", session.user.id)
    return status


@router.post("/unlink", dependencies=[Depends(verify_csrf)])
async def unlink(session: AuthSession = Depends(require_site)) -> dict:
    try:
        status = _status(await link.unlink())
    except link.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log.info("WhatsApp unlinked by user %s", session.user.id)
    return status


@router.put("/groups", dependencies=[Depends(verify_csrf)])
async def choose_group(
    jid: str = Body(embed=True),
    chosen: bool = Body(embed=True),
    session: AuthSession = Depends(require_site),
) -> dict:
    """Choose a group for announcements, or stop posting there.

    Only a group the number is in and can post to may be chosen, so the list
    never holds a group a post would fail in. Taking one off needs nothing.
    """
    if not chosen:
        await database.delete("/whatsapp/groups", {"jid": jid})
        return {"ok": True}
    try:
        groups = {g["jid"]: g for g in await link.groups()}
    except link.WhatsAppUnavailable as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    group = groups.get(jid)
    if group is None:
        raise HTTPException(404, "The linked number isn't in that group.")
    if not group["canPost"]:
        raise HTTPException(
            400,
            "The linked number can't post there — only admins can, or it's a "
            "community rather than its announcements group.",
        )
    # Stored under the name it is ticked by in the audience list — so a
    # community's announcements channel says so, rather than taking the
    # community's own name.
    name = group["name"]
    if group.get("communityAnnouncements"):
        name = f"Announcements — {(group.get('parent') or {}).get('name') or name}"
    await database.put(
        "/whatsapp/groups",
        {"jid": jid, "name": name, "added_by": session.user.id},
    )
    return {"ok": True}
