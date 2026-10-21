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

Two mailboxes, not one. Transactional mail (sign-in codes, your own booking)
goes from the webmaster address; anything sent to a list goes from the publicity
officer's. That separation is for deliverability as much as tone: a newsletter
that earns spam complaints must not be able to take sign-in codes down with it,
and the two mailboxes are rate limited independently.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import smtplib
from dataclasses import dataclass, replace
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

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
# The site's warning pair (amber-500/amber-100 in colors.css). The doors-close
# row is the one line in an email that is a hard limit at the door.
WARNING = "#D89A46"
WARNING_BG = "#FAEEDD"

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


def _detail_rows(pairs: list[tuple[str, str] | str]) -> str:
    """Label/value lines — ticket, time, deadline.

    A table rather than a flex row: Outlook renders this through Word, which
    has no flexbox, and the labels would stack on top of their values.

    An item that is already a string is a rendered `<tr>` (`_doors_close_row`),
    appended as-is: the doors-close line is a warning, not a detail, and is
    styled as one.
    """
    rows = ""
    for item in pairs:
        if isinstance(item, str):
            rows += item
            continue
        label, value = item
        rows += (
            f'<tr>'
            f'<td style="padding:6px 16px 6px 0;font-family:{MONO};font-size:12px;'
            f'letter-spacing:.1em;text-transform:uppercase;color:{MUTED};'
            f'white-space:nowrap;vertical-align:top;">{label}</td>'
            f'<td style="padding:6px 0;font-family:{SANS};font-size:15px;'
            f'color:{BODY_TEXT};">{value}</td>'
            f"</tr>"
        )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:4px 0 18px;width:100%;border-top:1px solid {BORDER};'
        f'border-bottom:1px solid {BORDER};">{rows}</table>'
    )


def _doors_close_row(doors_close: str | None) -> str:
    """The doors-close row of a details block, or "" when there is none.

    One helper because seven emails show it and they must all show it the same
    way: bold on a warning tint, so the one time limit that is not negotiable
    at the door does not read as just another detail.
    """
    if not doors_close:
        return ""
    return (
        f'<tr>'
        f'<td style="padding:6px 16px 6px 0;font-family:{MONO};font-size:12px;'
        f'letter-spacing:.1em;text-transform:uppercase;color:{INK};'
        f'white-space:nowrap;vertical-align:top;background:{WARNING_BG};">'
        f'Doors close</td>'
        f'<td style="padding:6px 0;font-family:{SANS};font-size:15px;'
        f'font-weight:700;color:{INK};background:{WARNING_BG};">'
        f'{escape(doors_close)}</td>'
        f"</tr>"
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


# ------------------------------------------------------------------- profiles
#
# Two mailboxes with different jobs, described once so no caller has to know
# which settings belong to which.


@dataclass(frozen=True)
class Profile:
    """One SMTP identity: where to connect, who to log in as, who it is from."""

    key: str
    host: str
    port: int
    user: str
    password: str
    sender: str
    reply_to: str
    starttls: bool
    # Bulk mail carries List-Unsubscribe and Precedence headers and is paced;
    # transactional mail carries Auto-Submitted and goes out immediately.
    bulk: bool = False

    @property
    def sender_address(self) -> str:
        return parseaddr(self.sender)[1].lower()

    def usable(self) -> bool:
        """A host, and a password if a username is set.

        Keying on the host alone would report "configured" on a fresh install —
        the default is smtp.office365.com — and every send would fail on
        authentication. Keying on the password alone would rule out an
        unauthenticated local relay like Mailpit, which is what development
        uses.
        """
        if not self.host:
            return False
        if self.user and not self.password:
            return False
        return True


def transactional() -> Profile:
    """The webmaster mailbox. Sign-in codes, your own booking, your own locker."""
    return Profile(
        key="transactional",
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_pass,
        sender=settings.smtp_from,
        reply_to=settings.smtp_reply_to,
        starttls=settings.smtp_starttls,
    )


def publicity() -> Profile:
    """The publicity officer's mailbox. Newsletters and announcements.

    Every field falls back to the transactional one, so a blank PUB_SMTP_* block
    means "send bulk from the webmaster mailbox" rather than "bulk is broken".

    The credentials are the interesting case. A shared Exchange mailbox has a
    *disabled* sign-in account and therefore no password of its own, so the
    normal arrangement is to authenticate as a real account that holds Send As
    on it and leave From as the shared address. With no PUB_SMTP_PASS set that
    is exactly what happens: webmaster credentials, publicity From.
    """
    base = transactional()
    has_own_credentials = bool(settings.pub_smtp_user and settings.pub_smtp_pass)
    return Profile(
        key="publicity",
        host=settings.pub_smtp_host or base.host,
        port=settings.pub_smtp_port or base.port,
        user=settings.pub_smtp_user if has_own_credentials else base.user,
        password=settings.pub_smtp_pass if has_own_credentials else base.password,
        sender=settings.pub_smtp_from or base.sender,
        reply_to=settings.pub_smtp_reply_to or base.reply_to,
        starttls=(
            settings.pub_smtp_starttls.strip().lower() not in ("false", "0", "no")
            if settings.pub_smtp_starttls.strip()
            else base.starttls
        ),
        bulk=True,
    )


def profile_for(name: str | None) -> Profile:
    return publicity() if name == "publicity" else transactional()


def _note(text: str) -> str:
    """A quieter aside — the line about what happens if they do nothing."""
    return (
        f'<p style="margin:0 0 14px;font-family:{SANS};font-size:13px;'
        f'line-height:1.6;color:{MUTED};">{text}</p>'
    )


def _footer_link(label: str, url: str) -> str:
    """The unsubscribe line. Deliberately a plain link in the body as well as a
    List-Unsubscribe header: the header is honoured by Gmail and Outlook but
    invisible in most clients, and somebody who cannot find the way out reports
    the message as spam instead."""
    return (
        f'<p style="margin:18px 0 0;font-family:{SANS};font-size:12px;'
        f'line-height:1.5;color:{FAINT};">'
        f'<a href="{url}" style="color:{FAINT};">{label}</a></p>'
    )


def escape(text: str) -> str:
    """Escape anything that came from a person before it goes in the HTML part.

    Event titles and ticket names are typed by the committee; a stray `&` in
    "Drinks & Dinner" would otherwise produce invalid markup, and a `<` would
    swallow the rest of the paragraph.
    """
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def configured(name: str | None = None) -> bool:
    """Whether we can actually send on this profile."""
    return profile_for(name).usable()


def describe() -> dict:
    """What the admin screen shows about mail, without revealing a password."""
    out = {}
    for prof in (transactional(), publicity()):
        out[prof.key] = {
            "host": prof.host,
            "port": prof.port,
            "login": prof.user or None,
            "from": prof.sender,
            "replyTo": prof.reply_to,
            "configured": prof.usable(),
            # The case worth seeing at a glance: sending as an address we are
            # not logged in as only works with Send As granted on the mailbox.
            "sendsAs": prof.sender_address != (prof.user or "").lower(),
        }
    return out


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


# Header folding off, deliberately.
#
# `List-Unsubscribe` carries URLs and is longer than the default 78-column
# limit, which makes Python fold it — and because there is no foldable
# whitespace inside a `<...>` target, it folds by RFC 2047 encoded-word. That
# is invalid for a structured header, and Gmail and Outlook simply ignore a
# List-Unsubscribe they cannot parse, so one-click unsubscribe silently stops
# working while the header looks present. Turning folding off emits it intact.
#
# Non-ASCII subjects are unaffected: encoding a subject is required by the
# charset, not by the line length, and still happens.
_POLICY = policy.SMTP.clone(max_line_length=0)


def _build(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    prof: Profile | None = None,
    headers: dict[str, str] | None = None,
) -> EmailMessage:
    prof = prof or transactional()
    message = EmailMessage(policy=_POLICY)
    message["From"] = prof.sender
    message["To"] = to
    message["Subject"] = subject
    if prof.reply_to:
        # The webmaster mailbox is checked irregularly; a reply should reach a
        # person who can answer.
        message["Reply-To"] = prof.reply_to
    # Set explicitly: some relays reject or down-rank messages without them.
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="eesoc.com")
    if prof.bulk:
        # Bulk mail must be unsubscribable and must not trigger out-of-office
        # storms. `Precedence: bulk` is what stops vacation auto-responders.
        message["Precedence"] = "bulk"
    else:
        # Transactional, not marketing — keep it out of bulk filters and stop
        # mailing-list software auto-replying.
        message["Auto-Submitted"] = "auto-generated"
    for name, value in (headers or {}).items():
        if value:
            message[name] = value
    # Plain text first, HTML as the alternative. Order matters: a client
    # that cannot render HTML takes the first part, and the text version is a
    # real message rather than "view this in a browser".
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message


def _send_sync(message: EmailMessage, prof: Profile, recipients: list[str]) -> dict:
    """Deliver one message to `recipients`, which are the envelope, not the
    headers — so a BCC'd address reaches its inbox without appearing in
    anybody's copy. Returns the addresses the server refused individually."""
    with smtplib.SMTP(prof.host, prof.port, timeout=settings.smtp_timeout_seconds) as smtp:
        smtp.ehlo()
        if prof.starttls:
            # Port 587 is STARTTLS, not implicit TLS. Without this the LOGIN
            # credentials go out in the clear.
            smtp.starttls()
            smtp.ehlo()
        if prof.user:
            _login(smtp, prof.user, prof.password)
        return smtp.send_message(message, to_addrs=recipients)


# Logged once per profile rather than per message: a newsletter to 400 people
# would otherwise write 400 identical lines about the same misconfiguration.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.error(message, *args)


def deliverable(to: str) -> bool:
    """Public form of `_deliverable`, for callers that record why nothing was
    sent rather than treating an allow-listed-out address as a failure."""
    return _deliverable(to)


def _deliverable(to: str) -> bool:
    """Whether MAIL_ALLOWLIST lets this address receive real mail."""
    allowed = [a.strip().lower() for a in settings.mail_allowlist.split(",") if a.strip()]
    if not allowed:
        return settings.environment != "staging"
    address = to.strip().lower()
    return any(
        address == a or (a.startswith("@") and address.endswith(a)) for a in allowed
    )


async def send(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    attempts: int = 2,
    profile: str | None = None,
    headers: dict[str, str] | None = None,
) -> bool:
    """Send a message to one person. Returns whether it actually went out.

    Never raises. A login must not 500 because Exchange is having a moment, and
    the caller already returns an indistinguishable 202 either way.
    """
    return bool(await send_many([to], subject, body, html, attempts, profile, headers))


async def send_many(
    recipients: list[str],
    subject: str,
    body: str,
    html: str | None = None,
    attempts: int = 2,
    profile: str | None = None,
    headers: dict[str, str] | None = None,
) -> list[str]:
    """Send one message to everybody in `recipients`. Returns who it reached.

    With more than one recipient they are all BCC'd and the visible To is the
    sending mailbox itself, so nobody sees anybody else's address. With exactly
    one it is an ordinary message addressed to them.

    Never raises, for the same reason as `send`.
    """
    prof = profile_for(profile)
    wanted = [r for r in recipients if _deliverable(r)]
    if len(wanted) < len(recipients):
        held = [r for r in recipients if r not in wanted]
        log.warning(
            "Not sending to %d address(es): not on MAIL_ALLOWLIST (%s): %s. "
            "Subject: %s\n%s",
            len(held), settings.environment, ", ".join(held[:10]), subject,
            body if settings.log_codes_when_unconfigured else "",
        )
    if not wanted:
        return []
    label = wanted[0] if len(wanted) == 1 else f"{len(wanted)} BCC recipients"
    if not prof.usable():
        if settings.log_codes_when_unconfigured:
            reason = "no host" if not prof.host else "no password"
            log.warning(
                "SMTP profile %s not configured (%s) — would have sent to %s:\n%s",
                prof.key, reason, label, body,
            )
        return []

    to_header = wanted[0] if len(wanted) == 1 else prof.sender

    def delivered(refused: dict) -> list[str]:
        if refused:
            log.warning(
                "Server refused %d of %d recipient(s) of %r: %s",
                len(refused), len(wanted), subject, ", ".join(list(refused)[:10]),
            )
        return [r for r in wanted if r not in refused]

    message = _build(to_header, subject, body, html, prof, headers)
    for attempt in range(1, attempts + 1):
        try:
            return delivered(await asyncio.to_thread(_send_sync, message, prof, wanted))
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError):
            # Transient: Exchange drops idle connections and rate-limits bursts.
            if attempt < attempts:
                await asyncio.sleep(2 * attempt)
                continue
            log.exception("Giving up sending to %s after %d attempts", label, attempts)
        except smtplib.SMTPRecipientsRefused as exc:
            log.error("Every recipient of %r was refused: %s", subject, exc.recipients)
            return []
        except smtplib.SMTPSenderRefused:
            # Exchange refuses a From the authenticated mailbox has no Send As
            # right for. That is a configuration problem nobody can fix from
            # here, and the message itself is usually worth delivering — so it
            # goes out from the mailbox we *are* logged into, once, loudly.
            fallback = transactional()
            if prof.sender_address == fallback.sender_address:
                log.exception("Sender %s refused sending to %s", prof.sender, label)
                return []
            _warn_once(
                f"sendas:{prof.key}",
                "%s is not permitted to send as %s, so bulk mail is going out "
                "from %s instead. Imperial ICT must grant Send As on the "
                "mailbox, or set PUB_SMTP_USER/PUB_SMTP_PASS to an account that "
                "already has it.",
                prof.user, prof.sender, fallback.sender,
            )
            retry = _build(
                wanted[0] if len(wanted) == 1 else fallback.sender,
                subject, body, html,
                # Keep the bulk headers and the publicity Reply-To; swap only
                # the From, which is the part Exchange objected to.
                replace(prof, sender=fallback.sender),
                headers,
            )
            try:
                return delivered(
                    await asyncio.to_thread(_send_sync, retry, fallback, wanted)
                )
            except Exception:
                log.exception("Fallback send to %s failed as well", label)
                return []
        except smtplib.SMTPAuthenticationError:
            # Almost always the annual password reset, or Microsoft disabling
            # basic auth on the tenant. Logged loudly because no amount of
            # retrying fixes it.
            _warn_once(
                f"auth:{prof.key}",
                "SMTP authentication failed for %s (profile %s). The mailbox "
                "password is reset yearly — check the credentials, and whether "
                "the tenant still permits SMTP AUTH.",
                prof.user, prof.key,
            )
            return []
        except Exception:
            log.exception("Failed to send mail to %s", label)
            return []
    return []


# --------------------------------------------------------------------- templates
#
# Every one of these sends both parts: a plain-text body that reads as a real
# message, and the branded HTML alternative. The text part is not a fallback
# afterthought — it is what lands in a terminal mail client, a screen reader,
# and the preview pane of anything that blocks HTML.


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


async def send_newsletter_confirm(to: str, url: str) -> bool:
    """Double opt-in for somebody who typed their address into the sign-up
    box. Nothing is subscribed until this link is followed, so a stranger
    cannot put someone else on the list."""
    body = (
        "Somebody — hopefully you — asked for the EESoc newsletter at this "
        "address.\n\n"
        f"Confirm: {url}\n\n"
        "If it wasn't you, ignore this email and nothing happens."
    )
    html = _shell(
        "Confirm the EESoc newsletter",
        "One click and you're on the list.",
        _p(
            "Somebody — hopefully you — asked for the EESoc newsletter at this "
            "address. What's on, when tickets go on sale, and what the society "
            "is up to."
        )
        + _button("Yes, sign me up", url)
        + _note("If it wasn't you, ignore this email and nothing happens."),
    )
    return await send(to, "Confirm the EESoc newsletter", body, html)


async def send_unsubscribe_link(to: str, url: str) -> bool:
    """Somebody's own unsubscribe link, on request.

    Bulk mail is BCC'd, so its footer can only carry the shared /unsubscribe
    page; this is what that page sends back. Personal, so it goes from the
    webmaster mailbox like a sign-in code.
    """
    body = (
        "Somebody — hopefully you — asked to stop EESoc newsletters and "
        "announcements at this address.\n\n"
        f"Unsubscribe: {url}\n\n"
        "If it wasn't you, ignore this email and nothing changes."
    )
    html = _shell(
        "Unsubscribe from EESoc email",
        "Your unsubscribe link.",
        _p(
            "Somebody — hopefully you — asked to stop EESoc newsletters and "
            "announcements at this address. Email about something you've "
            "booked still comes through."
        )
        + _button("Unsubscribe", url)
        + _note("If it wasn't you, ignore this email and nothing changes."),
    )
    return await send(to, "Unsubscribe from EESoc email", body, html)


async def send_waitlist_offer(
    to: str,
    event_title: str,
    ticket_type: str,
    expires: str,
    url: str,
    purchase_url: str | None = None,
    doors_close: str | None = None,
) -> bool:
    """A place has come free and is being held for them.

    The deadline is the point of this email: the hold expires, and saying so
    plainly is the difference between them buying and losing the seat.
    """
    title, ticket = escape(event_title), escape(ticket_type)
    body = (
        f"Good news — a place has opened up for {event_title}.\n\n"
        f"Ticket:      {ticket_type}\n"
        f"Held until:  {expires}\n"
        + (f"Doors close: {doors_close}\n" if doors_close else "")
        + "\n"
        "Buy it on the Union shop and we'll confirm your booking automatically "
        "once the purchase reaches us.\n\n"
        + (f"Buy your ticket: {purchase_url}\n" if purchase_url else "")
        + f"Your booking:    {url}\n\n"
        "If you no longer want it, ignore this email and the place goes to the "
        "next person on the list.\n"
    )
    rows = [("Ticket", ticket), ("Held until", escape(expires))]
    if doors_close:
        rows.append(_doors_close_row(doors_close))
    html = _shell(
        f"A place has come free for {title}",
        f"Held until {escape(expires)} — buy on the Union shop to confirm.",
        _p(f"A place has opened up for <strong>{title}</strong>.")
        + _detail_rows(rows)
        + _p(
            "Buy it on the Union shop and we'll confirm your booking "
            "automatically once the purchase reaches us."
        )
        + _button("Buy your ticket", purchase_url or url)
        + _note(
            f'<a href="{url}" style="color:{MUTED};">View your booking</a>. '
            "If you no longer want it, ignore this email and the place goes to "
            "the next person on the list."
        ),
    )
    return await send(to, f"A place has come free for {event_title}", body, html)


async def send_sponsor_note(
    to: list[str],
    sponsor: str,
    topic: str,
    note: str,
    author: str,
    url: str,
) -> list[str]:
    """A sponsor asked a question or left a note in the portal.

    To the committee, so it goes from the transactional mailbox like any other
    single message — not bulk, no unsubscribe. The note is quoted in full so it
    can be read, and often answered, from the inbox; the reply itself goes in
    the portal so the sponsor sees it where they asked.
    """
    who, what = escape(sponsor), escape(topic)
    quoted = "<br>".join(escape(line) for line in note.splitlines())
    body = (
        f"{sponsor} left a note in the sponsor portal.\n\n"
        f"About:  {topic}\n"
        f"From:   {author}\n\n"
        f"{note}\n\n"
        f"Reply in the admin page: {url}\n"
    )
    html = _shell(
        f"{who}: a note about {what}",
        escape(" ".join(note.split())[:140]),
        _detail_rows([("Sponsor", who), ("About", what), ("From", escape(author))])
        + f'<blockquote style="margin:0 0 18px;padding:12px 16px;border-left:3px solid {GOLD};'
        f'background:{SUNKEN};font-family:{SANS};font-size:15px;line-height:1.6;color:{BODY_TEXT};">'
        f"{quoted}</blockquote>"
        + _button("Reply in the admin page", url),
    )
    return await send_many(to, f"Sponsor note from {sponsor}: {topic}", body, html)


async def send_sponsor_reply(
    to: list[str], topic: str, url: str
) -> list[str]:
    """The committee answered a sponsor's note.

    Says that there is a reply, not what it is: the conversation lives in the
    portal, and copying it into email means two places to keep track of.
    """
    what = escape(topic)
    body = (
        f"The EESoc committee has replied to your note about {topic}.\n\n"
        f"Read it in the sponsor portal: {url}\n"
    )
    html = _shell(
        "EESoc has replied",
        f"About {what}",
        _p(f"The committee has replied to your note about <strong>{what}</strong>.")
        + _button("Read it in the portal", url),
    )
    return await send_many(to, f"EESoc replied: {topic}", body, html)


async def send_purchase_reminder(
    to: str,
    event_title: str,
    quantity: int,
    url: str,
    purchase_url: str | None = None,
    doors_close: str | None = None,
) -> bool:
    """The committee's manual nudge, from the unpaid-bookings screen."""
    title = escape(event_title)
    body = (
        f"We're holding {quantity} ticket(s) for {event_title}, but we can't see "
        "a purchase against your name on the Union shop yet.\n\n"
        "Buy it there and we'll confirm your place automatically. If you already "
        "have, reply with your order number and we'll match it up.\n\n"
        + (f"Doors close: {doors_close}\n\n" if doors_close else "")
        + (f"Buy your ticket: {purchase_url}\n" if purchase_url else "")
        + f"Your booking:    {url}\n"
    )
    # No details block in this one otherwise: the hold is the subject, and the
    # doors-close time is the only thing worth a row of its own.
    html = _shell(
        f"Your {title} ticket is still unpaid",
        "We can't see a purchase against your name yet.",
        _p(
            f"We're holding <strong>{quantity} ticket(s)</strong> for "
            f"<strong>{title}</strong>, but we can't see a purchase "
            "against your name on the Union shop yet."
        )
        + (
            _detail_rows([_doors_close_row(doors_close)])
            if doors_close
            else ""
        )
        + _button("Buy your ticket", purchase_url or url)
        + _note(
            f'<a href="{url}" style="color:{MUTED};">View your booking</a>. '
            "If you have already bought it, reply with your order number and "
            "we'll match it up."
        ),
    )
    return await send(to, f"Your {event_title} ticket is still unpaid", body, html)


async def send_booking_confirmed(
    to: str,
    event_title: str,
    ticket_type: str,
    starts: str,
    venue: str | None = None,
    quantity: int = 1,
    url: str | None = None,
    ticket_url: str | None = None,
    qr_url: str | None = None,
    doors_close: str | None = None,
) -> bool:
    """Their booking has gone from held to confirmed — or the committee added it.

    The one email that says "you have a place", so it carries everything they
    would otherwise have to look up: what, when, where, how many — and the QR
    the door scans. The QR is an image on our own domain, which a client may
    hold back until asked; the ticket link under it always works.
    """
    ticket = f"{ticket_type} × {quantity}" if quantity > 1 else ticket_type
    rows = [("Ticket", escape(ticket)), ("When", escape(starts))]
    lines = [f"Ticket:  {ticket}", f"When:    {starts}"]
    if doors_close:
        rows.append(_doors_close_row(doors_close))
        lines.append(f"Doors close: {doors_close}")
    if venue:
        rows.append(("Where", escape(venue)))
        lines.append(f"Where:   {venue}")
    body = (
        f"Your place at {event_title} is confirmed.\n\n"
        + "\n".join(lines)
        + "\n\n"
        + (f"Your ticket (show this at the door): {ticket_url}\n" if ticket_url else "")
        + (f"Event details: {url}\n" if url else "")
        + "\nSee you there.\n"
    )
    qr = (
        f'<p style="margin:4px 0 6px;text-align:center;">'
        f'<img src="{escape(qr_url)}" alt="Your ticket QR code" width="200" height="200" '
        f'style="display:inline-block;width:200px;height:200px;border:0;"></p>'
        f'<p style="margin:0 0 14px;text-align:center;font-family:{SANS};font-size:13px;'
        f'color:{MUTED};">Show this at the door.</p>'
        if qr_url else ""
    )
    html = _shell(
        f"You're confirmed for {escape(event_title)}",
        f"{escape(ticket)} · {escape(starts)}",
        _p(f"Your place at <strong>{escape(event_title)}</strong> is confirmed.")
        + _detail_rows(rows)
        + qr
        + (_button("Open your ticket", ticket_url) if ticket_url else "")
        + (_note(f'<a href="{url}" style="color:{MUTED};">Event details</a>') if url else "")
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
        f"Empty locker {escape(locker)} before {escape(deadline)}",
        "Locker allocations reset for the new academic year.",
        _p(f"You're holding EESoc locker <strong>{escape(locker)}</strong>.")
        + _p(
            f"Please empty it before <strong>{escape(deadline)}</strong>. Locker "
            "allocations reset for the new academic year, and anything left in "
            "it after that is removed."
        )
        + _p(
            "If you want a locker next year, you'll be able to book one again "
            "once the new allocation opens."
        ),
    )
    return await send(to, f"Empty locker {locker} before {deadline}", body, html)


# ------------------------------------------------------- booking lifecycle


async def send_booking_held(
    to: str,
    event_title: str,
    ticket_type: str,
    quantity: int,
    price: str,
    starts: str,
    expires: str,
    booking_url: str,
    purchase_url: str | None = None,
    needs_order_number: bool = False,
    doors_close: str | None = None,
) -> bool:
    """Sent the moment a hold is taken. The one email this flow was missing.

    A reservation here is not a ticket: the money changes hands on the Union
    shop and the sales feed only refreshes at 06:00. So the job of this message
    is to say, in order, what they have, what they must still do, and by when —
    with the link to pay as the only button on the page.

    `purchase_url` is optional because it genuinely can be missing: the Union's
    product feed often carries no URL for our centre, and the committee may not
    have pasted one in. Saying "find it on the Union shop" is poor but honest;
    inventing a URL would send people to a 404 and lose them the ticket.
    """
    title, ticket = escape(event_title), escape(ticket_type)

    if needs_order_number:
        what_next = (
            "Buy the ticket on the Union shop, then come back and enter your "
            "order number so we can match it to this booking."
        )
    else:
        what_next = (
            "Buy the ticket on the Union shop with the same Imperial account. "
            "We check purchases each morning; allow at least 24 hours after "
            "purchase for confirmation. If it has still not confirmed, use "
            "your booking page to submit the Union order number."
        )

    lines = [
        f"We're holding {quantity} × {ticket_type} for {event_title}.",
        "",
        f"Ticket:      {ticket_type} ({price})",
        f"How many:    {quantity}",
        f"Event:       {starts}",
    ]
    if doors_close:
        lines.append(f"Doors close: {doors_close}")
    lines += [
        f"Pay by:      {expires}",
        "",
        what_next,
        "",
    ]
    if purchase_url:
        lines += [f"Buy your ticket: {purchase_url}", ""]
    else:
        lines += [
            "You'll find it on the Union shop under Electrical Engineering "
            "Society.",
            "",
        ]
    lines += [
        f"Your booking:    {booking_url}",
        "",
        "If you don't buy it by the deadline the hold lapses and the place goes "
        "back on sale. You can cancel any time from the booking page.",
    ]

    rows = [
        ("Ticket", f"{ticket} ({escape(price)})"),
        ("How many", str(quantity)),
        ("Event", escape(starts)),
    ]
    if doors_close:
        rows.append(_doors_close_row(doors_close))
    rows.append(("Pay by", escape(expires)))
    html = _shell(
        f"We're holding your {title} ticket",
        f"Pay by {escape(expires)} to keep it.",
        _p(f"We're holding <strong>{quantity} × {ticket}</strong> for <strong>{title}</strong>.")
        + _detail_rows(rows)
        + _p(what_next)
        + (
            _button("Buy your ticket", purchase_url)
            if purchase_url
            else _p(
                "You'll find it on the Union shop under Electrical Engineering "
                "Society."
            )
        )
        + _note(
            f'Not bought it yet? Your place is held until {escape(expires)}. '
            f'After that it goes back on sale. '
            f'<a href="{booking_url}" style="color:{MUTED};">View or cancel your booking</a>.'
        ),
    )
    return await send(to, f"We're holding your {event_title} ticket", "\n".join(lines), html)


async def send_last_chance(
    to: str,
    event_title: str,
    ticket_type: str,
    quantity: int,
    expires: str,
    booking_url: str,
    purchase_url: str | None = None,
    doors_close: str | None = None,
) -> bool:
    """The one automated chase, sent shortly before a hold lapses.

    Separate from `send_purchase_reminder` (the committee's manual nudge)
    because the deadline is the whole message: this one is read by somebody who
    still has time to act, and it must say exactly how much.
    """
    title, ticket = escape(event_title), escape(ticket_type)
    body = (
        f"Your {event_title} ticket is still unpaid, and we can only hold it "
        f"until {expires}.\n\n"
        f"Ticket:   {quantity} × {ticket_type}\n"
        f"Hold ends: {expires}\n"
        + (f"Doors close: {doors_close}\n" if doors_close else "")
        + "\n"
        "Buy it on the Union shop and we'll confirm your place automatically.\n\n"
        + (f"Buy your ticket: {purchase_url}\n\n" if purchase_url else "")
        + f"Your booking:    {booking_url}\n\n"
        "If you've already bought it, you can ignore this — a purchase can take "
        "until the next morning to reach us. If you no longer want the ticket, "
        "cancel on the booking page so somebody else can have it.\n"
    )
    rows = [("Ticket", f"{quantity} × {ticket}"), ("Hold ends", escape(expires))]
    if doors_close:
        rows.append(_doors_close_row(doors_close))
    html = _shell(
        f"Last chance for your {title} ticket",
        f"The hold ends {escape(expires)}.",
        _p(
            f"Your <strong>{title}</strong> ticket is still unpaid, and we can "
            f"only hold it until <strong>{escape(expires)}</strong>."
        )
        + _detail_rows(rows)
        + (
            _button("Buy your ticket", purchase_url)
            if purchase_url
            else _p(
                "You'll find it on the Union shop under Electrical Engineering "
                "Society."
            )
        )
        + _note(
            "Already bought it? You can ignore this — a purchase can take until "
            "the next morning to reach us. Changed your mind? "
            f'<a href="{booking_url}" style="color:{MUTED};">Cancel the booking</a> '
            "so somebody else can have the place."
        ),
    )
    return await send(to, f"Last chance: your {event_title} ticket", body, html)


# ------------------------------------------------------------ announcements


def tickets_on_sale_message(
    event_title: str,
    when: str,
    venue: str,
    event_url: str,
    unsubscribe_url: str,
    tickets: list[tuple[str, str]] | None = None,
    purchase_url: str | None = None,
    doors_close: str | None = None,
    subject: str | None = None,
    heading: str | None = None,
    body_text: str = "",
    body_html: str = "",
) -> tuple[str, str, str]:
    """(subject, text, html) for the on-sale announcement, without sending it.

    Separate from the send so the approval queue can show the committee the
    whole message — opening, details, button, footer — from the same code that
    will deliver it. `subject`, `heading` and the opening (`body_*`, already
    rendered from the committee's markdown) are what a draft lets them edit;
    left blank, each falls back to the standard wording.
    """
    title = escape(event_title)
    subject = (subject or "").strip() or f"Tickets for {event_title} are on sale"
    heading = (heading or "").strip() or subject
    opening_text = body_text.strip() or f"Tickets for {event_title} are on sale now."
    opening_html = body_html or _p(f"Tickets for <strong>{title}</strong> are on sale now.")

    ticket_lines = "\n".join(f"  {name} — {price}" for name, price in (tickets or []))
    body = (
        f"{opening_text}\n\n"
        f"When:   {when}\n"
        + (f"Where:  {venue}\n" if venue else "")
        + (f"Doors close: {doors_close}\n" if doors_close else "")
        + (f"\nTickets:\n{ticket_lines}\n" if ticket_lines else "")
        + f"\nReserve your place: {event_url}\n"
        + (f"Buy on the Union shop: {purchase_url}\n" if purchase_url else "")
        + "\nYou asked us to tell you when this went on sale. "
        f"To stop these: {unsubscribe_url}\n"
    )

    rows = [("When", escape(when))]
    if venue:
        rows.append(("Where", escape(venue)))
    if doors_close:
        rows.append(_doors_close_row(doors_close))
    for name, price in tickets or []:
        rows.append((escape(name), escape(price)))

    html = _shell(
        escape(heading),
        f"{escape(when)}{' · ' + escape(venue) if venue else ''}",
        opening_html
        + _detail_rows(rows)
        + _button("Reserve your place", event_url)
        + _note("You asked us to tell you when this went on sale.")
        # BCC'd to everyone, so the link is the shared page that asks who you
        # are — see `_bulk_headers` — and the label says what it does.
        + _footer_link("Unsubscribe from EESoc email", unsubscribe_url),
    )
    return subject, body, html


async def send_tickets_on_sale(
    to: str | list[str],
    event_title: str,
    when: str,
    venue: str,
    event_url: str,
    unsubscribe_url: str,
    tickets: list[tuple[str, str]] | None = None,
    purchase_url: str | None = None,
    doors_close: str | None = None,
    subject: str | None = None,
    heading: str | None = None,
    body_text: str = "",
    body_html: str = "",
) -> list[str]:
    """Tickets have just opened for an event somebody asked to hear about.

    Goes from the publicity mailbox and carries an unsubscribe, because it is
    the one email in the booking flow the recipient did not trigger themselves.
    """
    subject, body, html = tickets_on_sale_message(
        event_title, when, venue, event_url, unsubscribe_url,
        tickets=tickets, purchase_url=purchase_url, doors_close=doors_close,
        subject=subject, heading=heading, body_text=body_text, body_html=body_html,
    )
    return await send_bulk_message(_as_list(to), subject, body, html, unsubscribe_url)


def _as_list(to: str | list[str]) -> list[str]:
    return [to] if isinstance(to, str) else list(to)


async def send_bulk_message(
    to: list[str], subject: str, text: str, html: str, unsubscribe_url: str
) -> list[str]:
    """One already-rendered bulk message to everybody in `to`, BCC'd.

    The one way anything addressed to a crowd leaves: the publicity mailbox,
    the unsubscribe headers, and `send_many`'s BCC — nobody sees anybody
    else's address, and it is one message per batch rather than one per person.
    """
    return await send_many(
        to, subject, text, html,
        profile="publicity",
        headers=_bulk_headers(unsubscribe_url),
    )


def _bulk_headers(unsubscribe_url: str) -> dict[str, str]:
    """One-click unsubscribe, as Gmail and Outlook want it.

    Both headers are needed and neither works alone: `List-Unsubscribe` alone
    makes the client show a confirmation page, and `List-Unsubscribe-Post`
    without the URL list is ignored. The mailto is the fallback for clients that
    only understand that form.

    One-click is only claimed for a signed link — one that names the address
    (or the alert) it stops. A message BCC'd to many people carries the shared
    /unsubscribe page instead, which asks who you are first; POSTing to it
    unsubscribes nobody, so advertising it as one-click would be a lie the
    client acts on.
    """
    reply_to = publicity().reply_to or settings.smtp_reply_to
    targets = [f"<{unsubscribe_url}>"]
    if reply_to:
        targets.append(f"<mailto:{reply_to}?subject=unsubscribe>")
    headers = {"List-Unsubscribe": ", ".join(targets)}
    if "?" in unsubscribe_url:
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return headers


def campaign_message(
    subject: str,
    heading: str,
    body_text: str,
    body_html: str,
    unsubscribe_url: str,
) -> tuple[str, str, str]:
    """(subject, text, html) for a newsletter, without sending it."""
    text = f"{body_text.rstrip()}\n\n—\nEESoc · eesoc.com\nUnsubscribe: {unsubscribe_url}\n"
    html = _shell(
        escape(heading or subject),
        # First line of the body, which is what an inbox preview would show
        # anyway — better to choose it than to let the client pick the wordmark.
        escape(" ".join(body_text.split())[:140]),
        body_html + _footer_link("Unsubscribe from EESoc email", unsubscribe_url),
    )
    return subject, text, html


async def send_campaign(
    to: str | list[str],
    subject: str,
    heading: str,
    body_text: str,
    body_html: str,
    unsubscribe_url: str,
) -> list[str]:
    """A committee-written newsletter.

    The body arrives already rendered — markdown is turned into the site's own
    typography by `services/emailing.py`, not here, so the admin preview and the
    delivered message are produced by the same code and cannot drift.
    """
    subject, text, html = campaign_message(
        subject, heading, body_text, body_html, unsubscribe_url
    )
    return await send_bulk_message(_as_list(to), subject, text, html, unsubscribe_url)


def event_announcement_message(
    subject: str,
    event_title: str,
    when: str,
    venue: str,
    event_url: str,
    unsubscribe_url: str,
    body_text: str = "",
    body_html: str = "",
    tickets: list[tuple[str, str]] | None = None,
    heading: str | None = None,
    doors_close: str | None = None,
) -> tuple[str, str, str]:
    """(subject, text, html) for a message about one event, without sending it."""
    title = escape(event_title)
    rows = [("When", escape(when))]
    if venue:
        rows.append(("Where", escape(venue)))
    if doors_close:
        rows.append(_doors_close_row(doors_close))
    for name, price in tickets or []:
        rows.append((escape(name), escape(price)))

    text_parts = [body_text.rstrip()] if body_text.strip() else []
    text_parts += [
        "",
        f"{event_title}",
        f"When:   {when}",
    ]
    if venue:
        text_parts.append(f"Where:  {venue}")
    if doors_close:
        text_parts.append(f"Doors close: {doors_close}")
    if tickets:
        text_parts += ["", "Tickets:"] + [f"  {n} — {p}" for n, p in tickets]
    text_parts += [
        "",
        f"Full details and booking: {event_url}",
        "",
        "—",
        "EESoc · eesoc.com",
        f"Unsubscribe: {unsubscribe_url}",
    ]

    html = _shell(
        escape(heading or subject),
        f"{title} · {escape(when)}",
        (body_html or "")
        + _detail_rows(rows)
        + _button("Full details and booking", event_url)
        + _footer_link("Unsubscribe from EESoc email", unsubscribe_url),
    )
    return subject, "\n".join(text_parts), html


async def send_event_announcement(
    to: str | list[str],
    subject: str,
    event_title: str,
    when: str,
    venue: str,
    event_url: str,
    unsubscribe_url: str,
    body_text: str = "",
    body_html: str = "",
    tickets: list[tuple[str, str]] | None = None,
    heading: str | None = None,
    doors_close: str | None = None,
) -> list[str]:
    """A committee-written message *about one event* — an announcement, a
    reminder, a change of room.

    Deliberately the same machinery as a newsletter (publicity mailbox, bulk
    headers, unsubscribe) with the event's own details rendered underneath the
    committee's words. Writing it as free text alone would mean every reminder
    re-typed the date and venue, and sooner or later one of them would be wrong.
    """
    subject, text, html = event_announcement_message(
        subject, event_title, when, venue, event_url, unsubscribe_url,
        body_text=body_text, body_html=body_html, tickets=tickets,
        heading=heading, doors_close=doors_close,
    )
    return await send_bulk_message(_as_list(to), subject, text, html, unsubscribe_url)


async def send_approval_needed(
    to: list[str], subject: str, what: str, recipients: int, url: str
) -> list[str]:
    """An automated bulk email is waiting for the committee to approve it.

    To the committee, from the transactional mailbox like a sponsor note: it is
    addressed to the people who have to act, not to the audience. Says what is
    waiting and for how many, because "an email needs approving" with no
    subject is the kind of notice that waits until tomorrow.
    """
    body = (
        "An email is waiting for a committee member to approve it. Nothing goes "
        "out until somebody does.\n\n"
        f"What:      {what}\n"
        f"Subject:   {subject}\n"
        f"Addressed: {recipients} recipient(s)\n\n"
        f"Review, edit, send or dismiss it: {url}\n"
    )
    html = _shell(
        "An email is waiting for approval",
        escape(subject),
        _p("An email is waiting for a committee member to approve it. Nothing "
           "goes out until somebody does.")
        + _detail_rows([
            ("What", escape(what)),
            ("Subject", escape(subject)),
            ("Addressed", f"{recipients} recipient(s)"),
        ])
        + _button("Review it", url),
    )
    return await send_many(to, f"Waiting for approval: {subject}", body, html)
