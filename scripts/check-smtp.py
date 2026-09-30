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

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
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
                smtp.login(user, password)
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
        print(f"\nFAIL  authentication rejected: {exc.smtp_code} {exc.smtp_error!r}")
        print("      Most likely one of:")
        print("      - the password is wrong, or was reset (Imperial forces this yearly)")
        print("      - SMTP AUTH is disabled for the mailbox (ask IT to enable it)")
        print("      - the account requires MFA, which basic SMTP cannot satisfy")
        return 1
    except smtplib.SMTPNotSupportedError as exc:
        print(f"\nFAIL  {exc}")
        if "STARTTLS" in str(exc).upper():
            print("      The port is probably wrong: 587 is STARTTLS, 465 is")
            print("      implicit TLS, 25 is usually blocked outright.")
        else:
            # Reaching here means the server took the connection but offers no
            # AUTH — see the WARN above, which has already named the cause.
            print("      See the warning above: the mailbox almost certainly has")
            print("      SMTP AUTH disabled. A new password will not fix it.")
        return 1
    except (TimeoutError, OSError) as exc:
        print(f"\nFAIL  could not reach {host}:{port} — {exc}")
        print("      Outbound SMTP is often blocked on home and campus networks.")
        return 1
    except smtplib.SMTPException as exc:
        print(f"\nFAIL  {type(exc).__name__}: {exc}")
        return 1

    print("\nAll good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
