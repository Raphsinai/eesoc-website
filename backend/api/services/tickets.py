"""Ticket tokens and their QR codes.

A ticket token is `<reservation id>.<hmac>`: the id is what the door looks up
and the HMAC is what makes the pair unforgeable. It needs no session — whoever
holds it has the ticket in their hand, which at a door is exactly the
credential that matters. Signed with the same secret as the unsubscribe links,
over a prefixed value, so a token minted for one purpose can never be replayed
as another.

The format matches the one the in-progress check-in section (plan 06) uses, so
the two converge rather than issue two kinds of ticket.

The QR encodes the ticket page's URL rather than the bare token: scanned with
any phone camera it opens the holder's ticket, and for a signed-in committee
member that page has a Check in button. The door screen pulls the token back
out of the URL.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re

from fastapi import HTTPException

from config import settings
from services import emailing

TOKEN = re.compile(r"(\d+)\.([0-9a-f]{32})")


def token_for(reservation_id: int) -> str:
    digest = hmac.new(
        emailing._secret().encode(),
        f"checkin:{reservation_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{reservation_id}.{digest}"


def parse(raw: str) -> int:
    """The reservation id in a token, or a ticket page URL containing one.

    400 rather than 404 for a bad signature: the id parses and the HMAC does
    not add up, which says somebody made this token rather than mistyped it.
    """
    match = TOKEN.search(raw or "")
    if not match:
        raise HTTPException(400, "That isn't an EESoc ticket.")
    reservation_id = int(match.group(1))
    expected = token_for(reservation_id).split(".", 1)[1]
    if not hmac.compare_digest(expected, match.group(2)):
        raise HTTPException(400, "That ticket doesn't verify — it may have been altered.")
    return reservation_id


def ticket_url(reservation_id: int) -> str:
    return f"{settings.site_origin}/ticket/{token_for(reservation_id)}"


def qr_url(reservation_id: int) -> str:
    return f"{settings.site_origin}/api/qr/{token_for(reservation_id)}.png"


def render_qr(token: str, box_size: int = 8) -> bytes:
    """The ticket page URL for this token, as a PNG about 300px square.

    `qrcode` is imported here so a host without it keeps every other route.
    """
    import qrcode

    image = qrcode.make(
        f"{settings.site_origin}/ticket/{token}", box_size=box_size, border=2
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
