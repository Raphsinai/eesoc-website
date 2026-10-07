"""Email preferences and unsubscribing.

The unsubscribe endpoints are deliberately unauthenticated. They are reached
from a link in a message, often days later, on a phone, by somebody who has
never signed in — and an unsubscribe that asks you to log in first is an
unsubscribe people answer with the spam button instead.

What stands in for authentication is a signature over the address (see
`services/emailing.sign`): enough that one person cannot unsubscribe another,
and nothing more, because nothing more is at stake. The reverse is NOT open:
re-subscribing needs a session, or a forwarded email would let anyone put an
address back on a list.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from clients import database
from models.auth import AuthSession
from services import emailing
from services.identity import resolve_or_create_user
from utils import email as mailer
from utils.auth import normalise_email
from utils.deps import client_ip, current_session, require_session, verify_csrf

log = logging.getLogger("api.email")

router = APIRouter()


@router.get("/email/topics")
async def list_topics(
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """What can be subscribed to, and — when signed in — what this person has
    chosen. One request, because the preferences screen needs both and they are
    meaningless apart."""
    available = await emailing.topics()

    chosen: dict[str, bool] = {}
    if session:
        rows = await database.get(
            "/email/subscriptions", {"user_id": session.user.id}
        ) or []
        chosen = {r["topic"]: r["subscribed"] for r in rows}

    return {
        "topics": [
            {
                "key": t["key"],
                "label": t["label"],
                "description": t["description"],
                # The effective answer, so the client never has to apply the
                # default rule itself and cannot disagree with the sender.
                "subscribed": chosen.get(t["key"], t["default"]),
                "isDefault": t["key"] not in chosen,
                "category": t.get("category"),
            }
            for t in available
        ],
        "authenticated": session is not None,
        "email": session.user.email if session else None,
    }


@router.put("/me/email-preferences", dependencies=[Depends(verify_csrf)])
async def set_preferences(
    preferences: dict[str, bool] = Body(embed=True),
    session: AuthSession = Depends(require_session),
) -> dict:
    """Save a batch of topic choices.

    Unknown keys are dropped rather than 400ing the whole save: a client cached
    from before a topic was renamed should still be able to set the ones it does
    know about.
    """
    known = {t["key"] for t in await emailing.topics()}
    items = [
        {"topic": key, "subscribed": bool(value)}
        for key, value in preferences.items()
        if key in known
    ]
    if not items:
        raise HTTPException(400, "No known topics in that request.")

    await database.put(f"/email/subscriptions/{session.user.id}", items)

    # Turning any topic back on is an explicit statement that this address wants
    # mail, so the blanket opt-out — which a one-click unsubscribe sets — has to
    # go with it. Without this the preferences screen would happily show
    # everything ticked while the sender skipped the address entirely.
    if any(item["subscribed"] for item in items):
        await database.delete(
            "/email/optouts", {"email": str(session.user.email), "topic": "*"}
        )

    return {"ok": True, "saved": len(items)}


# ---------------------------------------------------------- newsletter sign-up
#
# The box on "What's on". Signed in, it is one click. Signed out, it is double
# opt-in: the address gets a link, and nothing is subscribed until it is
# followed — otherwise anyone could sign anyone up.
#
# Throttled in-process (the api runs one replica): one confirmation per address
# per ten minutes, twenty per source address per hour. Enough to stop the box
# being used to mail-bomb somebody from our mailbox.

_last_sent: dict[str, float] = {}
_by_ip: dict[str, list[float]] = {}


def _throttled(address: str, ip: str) -> bool:
    now = time.monotonic()
    if now - _last_sent.get(address, -1e9) < 600:
        return True
    recent = [t for t in _by_ip.get(ip, []) if now - t < 3600]
    if len(recent) >= 20:
        _by_ip[ip] = recent
        return True
    _by_ip[ip] = recent + [now]
    _last_sent[address] = now
    return False


async def _subscribe(user_id: int, address: str) -> None:
    await database.put(
        f"/email/subscriptions/{user_id}", [{"topic": "newsletter", "subscribed": True}]
    )
    # Asking for the newsletter is an explicit request for mail, so an earlier
    # one-click unsubscribe must not keep silently suppressing it.
    for topic in ("*", "newsletter"):
        await database.delete("/email/optouts", {"email": address, "topic": topic})


@router.post("/email/subscribe", dependencies=[Depends(verify_csrf)])
async def subscribe(
    request: Request,
    email: str = Body(default="", embed=True),
    session: AuthSession | None = Depends(current_session),
) -> dict:
    """Sign up for the newsletter."""
    if session is not None and (
        not email.strip() or normalise_email(email) == normalise_email(str(session.user.email))
    ):
        await _subscribe(session.user.id, str(session.user.email))
        return {
            "ok": True,
            "subscribed": True,
            "message": f"Done — the newsletter will go to {session.user.email}.",
        }

    address = normalise_email(email)
    if not address or "@" not in address:
        raise HTTPException(400, "That doesn't look like an email address.")

    # Same answer whether or not we actually sent, so the box cannot be used
    # to find out anything about an address.
    reply = {
        "ok": True,
        "subscribed": False,
        "message": f"Nearly there — we've emailed {address} a link to confirm.",
    }
    if _throttled(address, client_ip(request)):
        log.warning("Newsletter sign-up throttled for %s", address)
        return reply
    await mailer.send_newsletter_confirm(address, emailing.subscribe_confirm_url(address))
    return reply


@router.post("/email/subscribe/confirm", dependencies=[Depends(verify_csrf)])
async def confirm_subscribe(e: str | None = None, t: str | None = None) -> dict:
    """Follow the link from the confirmation email."""
    address = normalise_email(e or "")
    if not address or not emailing.verify_subscribe(address, t or ""):
        raise HTTPException(400, "That confirmation link is not valid.")
    # Following the link proves the address, so the account (created if this is
    # their first contact) is a verified one.
    user = await resolve_or_create_user(address, verified=True)
    await _subscribe(user["id"], address)
    log.info("Newsletter confirmed for %s", address)
    return {"ok": True, "message": "You're on the list. The next newsletter is on its way to you."}


# ------------------------------------------------------------- unsubscribing


@router.get("/email/unsubscribe")
async def describe_unsubscribe(
    e: str | None = None, t: str | None = None, topic: str | None = None,
    alert: str | None = None,
) -> dict:
    """What this link is about to do, for the page that confirms it.

    Says nothing about whether the address is on any list — only whether the
    signature is ours. Reporting "no such address" would turn this into a way
    to test whether somebody receives EESoc email, which is information about
    them, not about the caller.
    """
    if alert:
        row = await database.get(f"/email/sale-alerts/by-token/{alert}")
        if row is None:
            raise HTTPException(404, "That link has expired.")
        return {
            "kind": "alert",
            "email": row["email"],
            "eventTitle": row.get("event_title"),
            "eventSlug": row.get("event_slug"),
            "alreadyDone": row.get("cancelled_at") is not None,
        }

    address = normalise_email(e or "")
    if not address or not emailing.verify(address, t or ""):
        raise HTTPException(400, "That unsubscribe link is not valid.")

    existing = await database.get("/email/optouts", {"email": address}) or []
    topics = {row["topic"] for row in existing}
    label = None
    if topic:
        label = next(
            (x["label"] for x in await emailing.topics() if x["key"] == topic), topic
        )
    return {
        "kind": "topic" if topic else "all",
        "email": address,
        "topic": topic,
        "topicLabel": label,
        "alreadyDone": "*" in topics or (topic in topics if topic else False),
    }


@router.post("/email/unsubscribe/request", dependencies=[Depends(verify_csrf)])
async def request_unsubscribe(
    request: Request, email: str = Body(default="", embed=True)
) -> dict:
    """Email somebody their own unsubscribe link.

    Bulk mail is BCC'd, so its footer links to the shared /unsubscribe page
    rather than a signed per-person link. This is how that page turns an address
    into one. Mailing the link rather than unsubscribing on the spot means a
    stranger cannot take somebody else off the list.
    """
    address = normalise_email(email)
    if not address or "@" not in address:
        raise HTTPException(400, "That doesn't look like an email address.")
    # Same answer whether or not we sent, as for sign-up: the box must not
    # reveal whether an address gets EESoc email.
    reply = {
        "ok": True,
        "message": f"We've emailed {address} a link to unsubscribe.",
    }
    if _throttled(f"unsubscribe:{address}", client_ip(request)):
        log.warning("Unsubscribe link request throttled for %s", address)
        return reply
    await mailer.send_unsubscribe_link(address, emailing.unsubscribe_url(address))
    return reply


@router.post("/email/unsubscribe")
async def unsubscribe(
    e: str | None = None, t: str | None = None, topic: str | None = None,
    alert: str | None = None,
) -> dict:
    """Stop sending.

    Takes its arguments from the query string, not a JSON body, because this is
    also the target of the `List-Unsubscribe-Post` one-click header — Gmail and
    Outlook POST to the bare URL with no body at all.

    Not CSRF-checked, and it must not be: the request arrives from a mail
    client with no session to ride, and the token is what authorises it.
    """
    if alert:
        row = await database.get(f"/email/sale-alerts/by-token/{alert}")
        if row is None:
            raise HTTPException(404, "That link has expired.")
        await database.post(f"/email/sale-alerts/{row['id']}/cancel")
        return {
            "ok": True,
            "message": (
                f"You'll hear nothing more about {row.get('event_title')}. "
                "Other EESoc email is unaffected."
            ),
        }

    address = normalise_email(e or "")
    if not address or not emailing.verify(address, t or ""):
        raise HTTPException(400, "That unsubscribe link is not valid.")

    await database.post(
        "/email/optouts",
        {"email": address, "topic": topic or "*", "reason": "unsubscribe link"},
    )
    log.info("Unsubscribed %s from %s", address, topic or "everything")

    if topic:
        return {"ok": True, "message": "Done — you won't get those any more."}
    return {
        "ok": True,
        "message": (
            "Done. You won't get any more newsletters or announcements from "
            "us. Email about something you've actually booked — your ticket, "
            "your sign-in code — still comes through."
        ),
    }


@router.post("/me/email-resubscribe", dependencies=[Depends(verify_csrf)])
async def resubscribe(session: AuthSession = Depends(require_session)) -> dict:
    """Undo a blanket unsubscribe. Needs a session, deliberately — see the
    module docstring."""
    await database.delete(
        "/email/optouts", {"email": str(session.user.email), "topic": "*"}
    )
    return {"ok": True, "message": "You're back on the list."}


@router.get("/me/email-status")
async def email_status(session: AuthSession = Depends(require_session)) -> dict:
    """Whether this account is suppressed outright, which the preferences
    screen has to say — otherwise every topic reads as subscribed while nothing
    is actually sent."""
    rows = await database.get(
        "/email/optouts", {"email": str(session.user.email)}
    ) or []
    return {
        "email": session.user.email,
        "unsubscribedFromAll": any(r["topic"] == "*" for r in rows),
        "unsubscribedTopics": [r["topic"] for r in rows if r["topic"] != "*"],
    }


@router.get("/me/sale-alerts")
async def my_sale_alerts(session: AuthSession = Depends(require_session)) -> dict:
    """Per-event alerts this person has asked for, so they can be managed from
    one place rather than only from the link in an email."""
    rows = await database.get(
        "/email/sale-alerts", {"email": str(session.user.email)}
    ) or []
    return {
        "alerts": [
            {
                "id": r["id"],
                "eventTitle": r.get("event_title"),
                "eventSlug": r.get("event_slug"),
                "notified": r.get("notified_at") is not None,
            }
            for r in rows
        ]
    }
