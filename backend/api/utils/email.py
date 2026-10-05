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
import base64
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from config import settings

log = logging.getLogger("api.email")


# ------------------------------------------------------------------- styling
#
# Colours are the site's own tokens (frontend/src/styles/.../colors.css), not
# approximations, so a sign-in email looks like the page it leads to.
#
# Everything is inline styles on tables. Not a stylistic choice: Gmail strips
# <style> blocks in some clients, Outlook's Word renderer ignores most modern
# CSS, and flexbox/grid are unreliable across the field. Web fonts are left out
# for the same reason — the stacks below fall back to whatever the device has.

INK = "#0B0D12"
BODY_TEXT = "#1E2230"
MUTED = "#5A6274"
FAINT = "#8A93A6"
PAGE = "#F7F8FA"
CARD = "#FFFFFF"
BORDER = "#DDE1E8"
SUNKEN = "#EEF0F4"
BLUE = "#1049B1"
GOLD = "#E7B570"

MONO = "SFMono-Regular, ui-monospace, Menlo, Consolas, 'Liberation Mono', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def _shell(heading: str, preheader: str, body: str) -> str:
    """Wrap body HTML in the branded frame.

    `preheader` is the grey line inboxes show after the subject. It is hidden
    in the message itself; left empty, clients pull the first visible text,
    which is usually the wordmark.
    """
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{heading}</title></head>
<body style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="max-width:520px;background:{CARD};border:1px solid {BORDER};border-radius:14px;overflow:hidden;">
      <tr><td style="background:{INK};padding:20px 28px;">
        <span style="font-family:{MONO};font-size:15px;font-weight:700;letter-spacing:.34em;color:#FFFFFF;">EESOC</span>
      </td></tr>
      <!-- The gold rule under the header, as on the site. -->
      <tr><td style="height:3px;background:{GOLD};line-height:3px;font-size:0;">&nbsp;</td></tr>
      <tr><td style="padding:32px 28px 8px;">
        <h1 style="margin:0 0 16px;font-family:{SANS};font-size:21px;line-height:1.3;color:{INK};">{heading}</h1>
        {body}
      </td></tr>
      <tr><td style="padding:24px 28px 28px;">
        <div style="border-top:1px solid {BORDER};padding-top:16px;
                    font-family:{SANS};font-size:12px;line-height:1.5;color:{FAINT};">
          Imperial College Electrical Engineering Society ·
          <a href="https://eesoc.com" style="color:{FAINT};">eesoc.com</a>
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def _p(text: str) -> str:
    return (
        f'<p style="margin:0 0 14px;font-family:{SANS};font-size:15px;'
        f'line-height:1.6;color:{BODY_TEXT};">{text}</p>'
    )


def _button(label: str, url: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 18px;">'
        f'<tr><td style="background:{BLUE};border-radius:8px;">'
        f'<a href="{url}" style="display:inline-block;padding:12px 22px;font-family:{SANS};'
        f'font-size:15px;font-weight:600;color:#FFFFFF;text-decoration:none;">{label}</a>'
        f"</td></tr></table>"
    )


def _detail_rows(pairs: list[tuple[str, str]]) -> str:
    """Label/value lines — ticket, time, deadline.

    A table rather than a flex row: Outlook renders this through Word, which
    has no flexbox, and the labels would stack on top of their values.
    """
    rows = "".join(
        f'<tr>'
        f'<td style="padding:6px 16px 6px 0;font-family:{MONO};font-size:12px;'
        f'letter-spacing:.1em;text-transform:uppercase;color:{MUTED};'
        f'white-space:nowrap;vertical-align:top;">{label}</td>'
        f'<td style="padding:6px 0;font-family:{SANS};font-size:15px;'
        f'color:{BODY_TEXT};">{value}</td>'
        f"</tr>"
        for label, value in pairs
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:4px 0 18px;width:100%;border-top:1px solid {BORDER};'
        f'border-bottom:1px solid {BORDER};">{rows}</table>'
    )


def _code_block(code: str) -> str:
    """The sign-in code.

    Deliberately one contiguous text node. Apple's one-time-code autofill (and
    every "copy the code" reflex) breaks the moment the digits are split into
    per-character boxes or rendered as an image — a popular treatment that
    makes the code unselectable and undetectable. `letter-spacing` is safe: it
    spaces the glyphs without changing the text.
    """
    return (
        f'<div style="margin:4px 0 18px;padding:18px 20px;background:{SUNKEN};'
        f'border:1px solid {BORDER};border-radius:10px;text-align:center;">'
        f'<div style="font-family:{MONO};font-size:31px;font-weight:700;'
        f'letter-spacing:.22em;color:{INK};">{code}</div>'
        f"</div>"
    )


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


def _login(smtp: smtplib.SMTP, user: str, password: str) -> None:
    """Authenticate, tolerating a password that is not pure ASCII.

    smtplib base64-encodes AUTH responses via `.encode("ascii")`, so a password
    containing so much as a £ raises UnicodeEncodeError *before anything is
    sent* — which surfaces as a generic send failure with nothing to suggest the
    password is the problem. Exchange accepts UTF-8, so the exchange is issued
    by hand in that case.

    The check is done up front rather than by catching the error, because
    catching it leaves the connection half way through an AUTH it cannot
    finish.
    """
    try:
        password.encode("ascii")
    except UnicodeEncodeError:
        encoded_user = base64.b64encode(user.encode("utf-8")).decode("ascii")
        code, response = smtp.docmd("AUTH", f"LOGIN {encoded_user}")
        if code != 334:
            raise smtplib.SMTPAuthenticationError(code, response)
        encoded_pass = base64.b64encode(password.encode("utf-8")).decode("ascii")
        code, response = smtp.docmd(encoded_pass)
        if code != 235:
            raise smtplib.SMTPAuthenticationError(code, response)
        return

    smtp.login(user, password)


def _build(to: str, subject: str, body: str, html: str | None = None) -> EmailMessage:
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
    # Plain text first, HTML as the alternative. Order matters: a client
    # that cannot render HTML takes the first part, and the text version is a
    # real message rather than "view this in a browser".
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
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
            _login(smtp, settings.smtp_user, settings.smtp_pass)
        smtp.send_message(message)


async def send(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    attempts: int = 2,
) -> bool:
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

    message = _build(to, subject, body, html)
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
    """The sign-in code.

    Three things make this autofill on Apple devices, and all three are easy
    to break by accident:

      * the code leads the subject line, which is where iOS and macOS Mail
        look first;
      * in the body it is one contiguous run of characters sitting next to the
        word "code", with nothing numeric near it to confuse the match;
      * the plain-text part ends with the origin-bound format Apple
        documents, `@domain #code`, which costs nothing because that part is
        not what gets displayed.

    Anything that splits the digits — per-character boxes, an image, a table
    cell each — stops both the autofill and the reader's own copy-paste.
    """
    body = (
        f"Your EESoc sign-in code is {code}\n\n"
        f"It expires in {minutes} minutes and can only be used once.\n\n"
        "If you didn't ask to sign in, you can ignore this email — nobody can "
        "get into your account without this code.\n\n"
        f"@eesoc.com #{code}\n"
    )
    html = _shell(
        "Your sign-in code",
        f"{code} — expires in {minutes} minutes",
        _code_block(code)
        + _p(f"Enter this code to finish signing in. It expires in {minutes} minutes and can only be used once.")
        + _p(
            "If you didn't ask to sign in, you can ignore this email — nobody "
            "can get into your account without this code."
        ),
    )
    return await send(to, f"{code} is your EESoc sign-in code", body, html)


async def send_waitlist_offer(
    to: str, event_title: str, ticket_type: str, expires: str, url: str
) -> bool:
    """A place has come free and is being held for them.

    The deadline is the point of this email: the hold expires, and saying so
    plainly is the difference between them buying and losing the seat.
    """
    body = (
        f"Good news — a place has opened up for {event_title}.\n\n"
        f"Ticket:      {ticket_type}\n"
        f"Held until:  {expires}\n\n"
        "Buy it on the Union shop and we'll confirm your booking automatically "
        "once the purchase reaches us.\n\n"
        f"Your booking: {url}\n\n"
        "If you no longer want it, ignore this email and the place goes to the "
        "next person on the list.\n"
    )
    html = _shell(
        f"A place has come free for {event_title}",
        f"Held until {expires} — buy on the Union shop to confirm.",
        _p(f"A place has opened up for <strong>{event_title}</strong>.")
        + _detail_rows([("Ticket", ticket_type), ("Held until", expires)])
        + _p(
            "Buy it on the Union shop and we'll confirm your booking "
            "automatically once the purchase reaches us."
        )
        + _button("View your booking", url)
        + _p(
            "If you no longer want it, ignore this email and the place goes to "
            "the next person on the list."
        ),
    )
    return await send(to, f"A place has come free for {event_title}", body, html)


async def send_purchase_reminder(
    to: str, event_title: str, quantity: int, url: str
) -> bool:
    body = (
        f"We're holding {quantity} ticket(s) for {event_title}, but we can't see "
        "a purchase against your name on the Union shop yet.\n\n"
        "Buy it there and we'll confirm your place automatically. If you already "
        "have, reply with your order number and we'll match it up.\n\n"
        f"Your booking: {url}\n"
    )
    html = _shell(
        f"Your {event_title} ticket is still unpaid",
        "We can't see a purchase against your name yet.",
        _p(
            f"We're holding <strong>{quantity} ticket(s)</strong> for "
            f"<strong>{event_title}</strong>, but we can't see a purchase "
            "against your name on the Union shop yet."
        )
        + _button("View your booking", url)
        + _p(
            "If you have already bought it, reply with your order number and "
            "we'll match it up."
        ),
    )
    return await send(to, f"Your {event_title} ticket is still unpaid", body, html)


async def send_booking_confirmed(
    to: str, event_title: str, ticket_type: str, starts: str
) -> bool:
    body = (
        f"Your place at {event_title} is confirmed.\n\n"
        f"Ticket:  {ticket_type}\n"
        f"When:    {starts}\n\n"
        "See you there.\n"
    )
    html = _shell(
        f"You're confirmed for {event_title}",
        f"{ticket_type} · {starts}",
        _p(f"Your place at <strong>{event_title}</strong> is confirmed.")
        + _detail_rows([("Ticket", ticket_type), ("When", starts)])
        + _p("See you there."),
    )
    return await send(to, f"You're confirmed for {event_title}", body, html)


async def send_locker_clearout(to: str, locker: str, deadline: str) -> bool:
    """Yearly "empty your locker" notice.

    The handover's first locker instruction: make sure holders are told before
    the reservations are wiped, because anything left behind gets cut off.
    """
    body = (
        f"You're holding EESoc locker {locker}.\n\n"
        f"Please empty it before {deadline}. Locker allocations reset for the new "
        "academic year, and anything left in it after that is removed.\n\n"
        "If you want a locker next year, you'll be able to book one again once "
        "the new allocation opens.\n"
    )
    html = _shell(
        f"Empty locker {locker} before {deadline}",
        f"Locker allocations reset for the new academic year.",
        _p(f"You're holding EESoc locker <strong>{locker}</strong>.")
        + _p(
            f"Please empty it before <strong>{deadline}</strong>. Locker "
            "allocations reset for the new academic year, and anything left in "
            "it after that is removed."
        )
        + _p(
            "If you want a locker next year, you'll be able to book one again "
            "once the new allocation opens."
        ),
    )
    return await send(to, f"Empty locker {locker} before {deadline}", body, html)
