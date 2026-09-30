"""Outbound email.

Sends through Imperial's shared webmaster mailbox on Exchange Online. The
settings come from the 2023/24 handover, where Auth0 was configured with
smtp.office365.com:587, LOGIN authentication and TLS — the same mailbox, now
driven from here instead.

Deliberately stdlib `smtplib` in a worker thread rather than an async SMTP
dependency: this sends a handful of messages a day, and the thread keeps the
event loop free.

Two operational facts inherited from the handover, both of which break sending
silently if forgotten:

  * The webmaster account's password must be reset every year or the account
    locks. When it changes, update SMTP_PASS.
  * Exchange Online only accepts `From` addresses the authenticated mailbox is
    allowed to send as, so SMTP_FROM is not freely choosable.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from config import settings

log = logging.getLogger("api.email")


def configured() -> bool:
    """Whether we can actually send.

    A host must be set, and if a username is set then a password is required.
    Keying on the host alone would report "configured" on a fresh install — the
    default is smtp.office365.com — and every send would fail on authentication.
    Keying on the password alone would rule out an unauthenticated local relay
    like Mailpit, which is exactly what development uses.
    """
    if not settings.smtp_host:
        return False
    if settings.smtp_user and not settings.smtp_pass:
        return False
    return True


def _build(to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    if settings.smtp_reply_to:
        # The webmaster mailbox is checked irregularly; a reply should reach a
        # person who can answer.
        message["Reply-To"] = settings.smtp_reply_to
    # Set explicitly: some relays reject or down-rank messages without them.
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="eesoc.com")
    # These are transactional, not marketing — keep them out of bulk filters and
    # stop mailing-list software auto-replying.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    return message


def _send_sync(message: EmailMessage) -> None:
    with smtplib.SMTP(
        settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
    ) as smtp:
        smtp.ehlo()
        if settings.smtp_starttls:
            # Port 587 is STARTTLS, not implicit TLS. Without this the LOGIN
            # credentials go out in the clear.
            smtp.starttls()
            smtp.ehlo()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.send_message(message)


async def send(to: str, subject: str, body: str, attempts: int = 2) -> bool:
    """Send a message. Returns whether it actually went out.

    Never raises. A login must not 500 because Exchange is having a moment, and
    the caller already returns an indistinguishable 202 either way.
    """
    if not configured():
        if settings.log_codes_when_unconfigured:
            reason = (
                "no SMTP_HOST" if not settings.smtp_host else "no SMTP_PASS"
            )
            log.warning(
                "SMTP not configured (%s) — would have sent to %s:\n%s",
                reason,
                to,
                body,
            )
        return False

    message = _build(to, subject, body)
    for attempt in range(1, attempts + 1):
        try:
            await asyncio.to_thread(_send_sync, message)
            return True
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError):
            # Transient: Exchange drops idle connections and rate-limits bursts.
            if attempt < attempts:
                await asyncio.sleep(2 * attempt)
                continue
            log.exception("Giving up sending to %s after %d attempts", to, attempts)
        except smtplib.SMTPAuthenticationError:
            # Almost always the annual password reset, or Microsoft disabling
            # basic auth on the tenant. Logged loudly because no amount of
            # retrying fixes it.
            log.error(
                "SMTP authentication failed for %s. The webmaster password is "
                "reset yearly — check SMTP_PASS, and whether the tenant still "
                "permits SMTP AUTH.",
                settings.smtp_user,
            )
            return False
        except Exception:
            log.exception("Failed to send mail to %s", to)
            return False
    return False


# --------------------------------------------------------------------- templates
#
# Plain text on purpose. These are short, transactional, and HTML would only add
# ways for them to render badly or land in spam.


async def send_login_code(to: str, code: str, minutes: int) -> bool:
    return await send(
        to,
        f"{code} is your EESoc sign-in code",
        f"Your EESoc sign-in code is {code}\n\n"
        f"It expires in {minutes} minutes and can only be used once.\n\n"
        "If you didn't ask to sign in, you can ignore this email — nobody can "
        "get into your account without this code.\n",
    )


async def send_waitlist_offer(
    to: str, event_title: str, ticket_type: str, expires: str, url: str
) -> bool:
    """A place has come free and is being held for them.

    The deadline is the point of this email: the hold expires, and saying so
    plainly is the difference between them buying and losing the seat.
    """
    return await send(
        to,
        f"A place has come free for {event_title}",
        f"Good news — a place has opened up for {event_title}.\n\n"
        f"Ticket:      {ticket_type}\n"
        f"Held until:  {expires}\n\n"
        "Buy it on the Union shop and we'll confirm your booking automatically "
        "once the purchase reaches us.\n\n"
        f"Your booking: {url}\n\n"
        "If you no longer want it, ignore this email and the place goes to the "
        "next person on the list.\n",
    )


async def send_purchase_reminder(
    to: str, event_title: str, quantity: int, url: str
) -> bool:
    return await send(
        to,
        f"Your {event_title} ticket is still unpaid",
        f"We're holding {quantity} ticket(s) for {event_title}, but we can't see "
        "a purchase against your name on the Union shop yet.\n\n"
        "Buy it there and we'll confirm your place automatically. If you already "
        "have, reply with your order number and we'll match it up.\n\n"
        f"Your booking: {url}\n",
    )


async def send_booking_confirmed(
    to: str, event_title: str, ticket_type: str, starts: str
) -> bool:
    return await send(
        to,
        f"You're confirmed for {event_title}",
        f"Your place at {event_title} is confirmed.\n\n"
        f"Ticket:  {ticket_type}\n"
        f"When:    {starts}\n\n"
        "See you there.\n",
    )


async def send_locker_clearout(to: str, locker: str, deadline: str) -> bool:
    """Yearly "empty your locker" notice.

    The handover's first locker instruction: make sure holders are told before
    the reservations are wiped, because anything left behind gets cut off.
    """
    return await send(
        to,
        f"Empty locker {locker} before {deadline}",
        f"You're holding EESoc locker {locker} on Level 4.\n\n"
        f"Please empty it before {deadline}. Locker allocations reset for the new "
        "academic year, and anything left in it after that is removed.\n\n"
        "If you want a locker next year, you'll be able to book one again once "
        "the new allocation opens.\n",
    )
