"""Sign-in codes travel through the mail sink (mailpit), not the api log.

This is the acceptance criterion for the SMTP wiring: the api service points at
mailpit, a requested code lands there as a real message, and the code from that
message opens a session. If the api fell back to logging codes instead of
emailing them, this fails.
"""

import re
import uuid

CODE_RE = re.compile(r"^\d{6}$")


def _fresh_email() -> str:
    """A unique address so successive runs never hit a resend cooldown.

    The domain does not matter to mailpit, and an external (non-Imperial)
    address is deliberate: it exercises the "brand-new account" branch of
    verification rather than reusing a stub union member.
    """
    return f"baseline-code-{uuid.uuid4().hex}@example.com"


def _mailpit_addresses(mailpit) -> set[str]:
    """Every recipient address mailpit has ever seen, from the message list."""
    response = mailpit.get("/api/v1/messages")
    response.raise_for_status()
    addresses = set()
    for message in response.json().get("messages", []):
        for recipient in message.get("To", []):
            address = recipient.get("Address")
            if address:
                addresses.add(address)
    return addresses


def test_code_is_emailed_through_mailpit_and_verifies(api, mailpit,
                                                       login_code_from_mailpit):
    email = _fresh_email()

    # request-code always answers 202, even for a brand-new address, so the
    # assertion here is only the status code -- the mail is the real outcome.
    response = api.post("/auth/request-code", json={"email": email})
    assert response.status_code == 202

    # The helper polls mailpit until a message to this address appears and
    # returns the six-digit code. This is the mail-sink assertion: without
    # mailpit the poll would time out.
    code = login_code_from_mailpit(email)
    assert CODE_RE.match(code), f"expected a 6-digit code, got {code!r}"

    # Belt and braces: the message list itself names the address, proving the
    # code came from mailpit rather than anywhere else.
    assert email in _mailpit_addresses(mailpit)

    # The code from the mailbox opens a real session.
    verify = api.post("/auth/verify-code", json={"email": email, "code": code})
    assert verify.status_code == 200
    body = verify.json()
    assert body["authenticated"] is True
    # A fresh external address has no union record, so it is not a member.
    assert body["is_member"] is False

    # The session cookies were set on the client, so a subsequent probe agrees.
    session = api.get("/auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True