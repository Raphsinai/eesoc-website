#!/usr/bin/env python3
"""Check the SMTP settings actually work, and say precisely why if they don't.

    python3 scripts/check-smtp.py                 # connect + authenticate only
    python3 scripts/check-smtp.py you@ic.ac.uk    # also send a real test email

Reads backend.env. Never prints the password.

Worth running before trusting login emails in production: a wrong password, a
blocked port and a tenant with SMTP AUTH turned off all fail identically from
the application's point of view, but need completely different fixes.
"""

from __future__ import annotations

import base64
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / "backend.env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        sys.exit(f"No {path.name} found. Copy backend.env.example to backend.env first.")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


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


def _explain_auth_failure(code, detail, user) -> None:
    text = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
    print(f"\nFAIL  authentication rejected: {code} {text.strip()}")

    # Exchange's sub-code is the useful part; the English is the same either way.
    if "5.7.139" in text or "SmtpClientAuthentication is disabled" in text:
        print("      SMTP AUTH is disabled FOR THIS MAILBOX. The password is not")
        print("      the problem. Imperial ICT must enable Authenticated SMTP on")
        print(f"      {user}.")
        return

    print("      The server offered LOGIN, so SMTP AUTH itself is available —")
    print("      these credentials were refused. Most likely, in order:")
    print()
    print(f"      1. {user} is a SHARED mailbox. Shared mailboxes have a")
    print("         disabled sign-in account and NO password of their own, so")
    print("         no password can ever work. Authenticate as a real account")
    print("         that holds Send As on it, and leave SMTP_FROM as the shared")
    print("         address — SMTP_USER and SMTP_FROM are separate settings for")
    print("         exactly this.")
    print("      2. The password belongs to a different account than SMTP_USER.")
    print("      3. The account has MFA or Conditional Access, which basic SMTP")
    print("         cannot satisfy.")


def main() -> int:
    env = load_env(ENV)
    host = env.get("SMTP_HOST") or "smtp.office365.com"
    port = int(env.get("SMTP_PORT") or 587)
    user = env.get("SMTP_USER") or ""
    password = env.get("SMTP_PASS") or ""
    sender = env.get("SMTP_FROM") or user
    starttls = (env.get("SMTP_STARTTLS") or "true").lower() not in ("false", "0", "no")

    # Exchange Online refuses a From the authenticated mailbox is not allowed to
    # send as, and the rejection happens at send time, not login — so a checker
    # that only logs in would pass while every real email bounced.
    from_address = parseaddr(sender)[1].lower()

    print(f"host      {host}:{port}")
    print(f"user      {user or '(none)'}")
    print(f"password  {'set (' + str(len(password)) + ' chars)' if password else 'NOT SET'}")
    print(f"starttls  {starttls}")
    print(f"from      {sender}\n")

    if user and not password:
        print("FAIL  SMTP_USER is set but SMTP_PASS is empty.")
        print("      The app treats this as unconfigured and logs codes instead.")
        return 1

    # Not a `with` block. On exit smtplib sends QUIT, and after a failed AUTH
    # Exchange answers that badly enough that __exit__ raises a *new*
    # SMTPResponseException which replaces the original SMTPAuthenticationError
    # — so a rejected password came out reported as "could not reach the
    # server". Closing by hand keeps the real exception.
    smtp = None
    try:
        smtp = smtplib.SMTP(host, port, timeout=20)
        if True:
            smtp.set_debuglevel(0)
            smtp.ehlo()
            print("ok    connected")

            if starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                print("ok    STARTTLS negotiated")

            mechanisms = smtp.esmtp_features.get("auth", "")
            print(f"ok    server offers auth: {mechanisms.strip() or '(none advertised)'}")
            if user and "LOGIN" not in mechanisms.upper():
                # Exchange Online disables SMTP AUTH per-mailbox by default and
                # Microsoft has been retiring basic auth outright. This is the
                # most likely failure, and no password change fixes it.
                print("\nWARN  The server is not advertising LOGIN.")
                print("      On Exchange Online this usually means SMTP AUTH is")
                print("      disabled for this mailbox. Imperial IT can enable it")
                print("      ('Authenticated SMTP' on the mailbox).")

            if user:
                _login(smtp, user, password)
                print("ok    authenticated")

            if user and from_address and from_address != user.lower():
                print("\nWARN  SMTP_FROM is not the mailbox being logged into.")
                print(f"      login: {user}")
                print(f"      from:  {from_address}")
                print("      Exchange Online rejects a From the mailbox has no")
                print("      Send As right for. Either match them, or have IT")
                print("      grant Send As on the other address.")

            if len(sys.argv) > 1:
                to = sys.argv[1]
                message = EmailMessage()
                message["From"] = sender
                message["To"] = to
                message["Subject"] = "EESoc website SMTP test"
                message.set_content(
                    "If you are reading this, the EESoc website can send email.\n"
                )
                smtp.send_message(message)
                print(f"ok    test email sent to {to}")
            else:
                print("\n(no recipient given — pass one to send a real test email)")

    except smtplib.SMTPAuthenticationError as exc:
        _explain_auth_failure(exc.smtp_code, exc.smtp_error, user)
        return 1
    except smtplib.SMTPNotSupportedError as exc:
        print(f"\nFAIL  {exc}")
        if "STARTTLS" in str(exc).upper():
            print("      The port is probably wrong: 587 is STARTTLS, 465 is")
            print("      implicit TLS, 25 is usually blocked outright.")
        else:
            print("      See the warning above: the mailbox almost certainly has")
            print("      SMTP AUTH disabled. A new password will not fix it.")
        return 1
    except smtplib.SMTPResponseException as exc:
        # Includes anything raised while closing the connection.
        if exc.smtp_code in (454, 530, 534, 535):
            _explain_auth_failure(exc.smtp_code, exc.smtp_error, user)
        else:
            print(f"\nFAIL  {exc.smtp_code} {exc.smtp_error!r}")
        return 1
    except smtplib.SMTPException as exc:
        print(f"\nFAIL  {type(exc).__name__}: {exc}")
        return 1
    except (TimeoutError, OSError) as exc:
        # Last, because smtplib.SMTPException subclasses OSError.
        print(f"\nFAIL  could not reach {host}:{port} — {exc}")
        print("      Outbound SMTP is often blocked on home and campus networks.")
        return 1
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass

    print("\nAll good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
