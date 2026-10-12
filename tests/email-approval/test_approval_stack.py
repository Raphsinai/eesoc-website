"""The email approval queue against the live test stack.

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var: without the stack it fails
with a connection error.

The whole path, through the real api, adapter, Postgres and mailpit:

  * an event whose sale has opened produces exactly one on-sale draft, and
    nothing in the mail sink — however many passes run;
  * the draft's words and recipients can be edited, a test goes to the test
    list only, and Send delivers the edited message as one BCC'd message to
    the edited list;
  * a dismissed draft sends nothing and cannot then be sent.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

SEND_TIMEOUT = 60.0


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat()


def _create_on_sale_event(admin_client, token: str) -> dict:
    slug = f"approval-{token}"
    created = admin_client.post(
        "/admin/events",
        json={
            "slug": slug,
            "title": f"Approval Night {token}",
            "category": "Socials",
            "venue": "EEE 403",
            "starts_at": _iso(timedelta(days=30)),
            "ends_at": _iso(timedelta(days=30, hours=2)),
            # Already open, so the very next on-sale pass picks it up.
            "sales_open_at": _iso(-timedelta(hours=1)),
            "published": True,
            "ticket_types": [
                {"name": "Standard", "price_pence": 500, "capacity": 50,
                 "requires_purchase": False},
            ],
        },
    )
    assert created.status_code in (200, 201), created.text
    return created.json()


def _drafts_for(admin_client, event_id: int) -> list[dict]:
    items = admin_client.get("/admin/email/queue").json()["items"]
    return [q for q in items if (q.get("event") or {}).get("id") == event_id]


def _run_pass_until_drafted(admin_client, event_id: int) -> list[dict]:
    """Run the on-sale pass until this event's draft exists.

    Polled, because a pass already running (the scheduler's own tick) makes
    announce-now skip — and that pass writes the draft a moment later.
    """
    deadline = time.monotonic() + 20.0
    while True:
        ran = admin_client.post("/admin/email/announce-now")
        assert ran.status_code == 200, ran.text
        drafts = _drafts_for(admin_client, event_id)
        if drafts or time.monotonic() > deadline:
            return drafts
        time.sleep(0.5)


def _messages(mailpit, needle: str) -> list[dict]:
    """Every message in the sink whose subject contains `needle`, in full."""
    out = []
    listing = mailpit.get("/api/v1/messages", params={"limit": 500}).json()
    for summary in listing.get("messages", []):
        if needle in (summary.get("Subject") or ""):
            out.append(mailpit.get(f"/api/v1/message/{summary['ID']}").json())
    return out


def _audience_mail(mailpit, event: dict) -> list[dict]:
    """The on-sale email itself, as the audience would get it.

    Matched on the exact subject, so the "waiting for approval" notice to the
    committee (sent when EMAIL_APPROVAL_NOTIFY is set) does not count as the
    announcement having gone out.
    """
    subject = f"Tickets for {event['title']} are on sale"
    return [m for m in _messages(mailpit, event["title"]) if m.get("Subject") == subject]


def _addresses(message: dict, *fields: str) -> set[str]:
    return {
        (entry.get("Address") or "").lower()
        for field in fields
        for entry in (message.get(field) or [])
        if isinstance(entry, dict)
    }


def _wait_for_status(admin_client, campaign_id: int, status: str) -> dict:
    deadline = time.monotonic() + SEND_TIMEOUT
    row = None
    while time.monotonic() < deadline:
        rows = admin_client.get("/admin/email/campaigns", params={"limit": 200}).json()
        row = next((r for r in rows if r["id"] == campaign_id), None)
        if row and row["status"] == status:
            return row
        time.sleep(0.5)
    raise AssertionError(f"campaign {campaign_id} never reached {status!r}: {row}")


def test_on_sale_email_waits_for_approval_then_sends_as_edited(admin_client, mailpit):
    token = uuid.uuid4().hex[:8]
    tester = f"approval-tester-{token}@example.com"
    added = f"approval-added-{token}@example.com"
    removed = f"approval-removed-{token}@example.com"
    event = _create_on_sale_event(admin_client, token)

    assert admin_client.post(
        "/admin/email/test-recipients", json={"email": tester}
    ).status_code == 200

    try:
        # The pass writes the draft. A second pass (or the scheduler's own
        # tick racing this one) must not write another.
        _run_pass_until_drafted(admin_client, event["id"])
        assert admin_client.post("/admin/email/announce-now").status_code == 200
        drafts = _drafts_for(admin_client, event["id"])
        assert len(drafts) == 1, drafts
        draft_id = drafts[0]["id"]
        assert drafts[0]["kind"] == "on_sale"

        # ...and nothing about this event reached its audience.
        assert _audience_mail(mailpit, event) == []

        # Edit: new words, only the test list ticked, one address added, and
        # one added-then-taken-off to prove removal sticks.
        subject = f"Approved {token}: tickets are out"
        edited = admin_client.put(
            f"/admin/email/queue/{draft_id}",
            json={
                "subject": subject,
                "body": f"Hand-edited opening {token}.",
                "audiences": ["test"],
                "extra": [added, removed],
                "excluded": [removed],
            },
        )
        assert edited.status_code == 200, edited.text
        detail = edited.json()
        listed = {r["email"] for r in detail["recipientList"]}
        assert {tester, added} <= listed
        assert removed not in listed
        assert detail["preview"]["subject"] == subject
        assert f"Hand-edited opening {token}." in detail["preview"]["html"]

        # A test goes to the test list only, marked as a test, and the draft
        # stays in the queue.
        tested = admin_client.post(f"/admin/email/queue/{draft_id}/test")
        assert tested.status_code == 200, tested.text
        test_messages = _messages(mailpit, f"[Test] {subject}")
        assert test_messages, "the test send never reached the sink"
        assert added not in set().union(*(_addresses(m, "To", "Bcc") for m in test_messages))
        assert _drafts_for(admin_client, event["id"]), "a test send must not take it off the queue"

        # Send.
        sent = admin_client.post(f"/admin/email/queue/{draft_id}/send")
        assert sent.status_code == 200, sent.text
        _wait_for_status(admin_client, draft_id, "sent")

        real = [m for m in _messages(mailpit, subject) if not m["Subject"].startswith("[Test]")]
        assert len(real) == 1, "the batch must be one message, not one per recipient"
        envelope = _addresses(real[0], "To", "Bcc")
        assert {tester, added} <= envelope
        assert removed not in envelope
        # BCC'd: the visible To is not any recipient.
        assert not ({tester, added} & _addresses(real[0], "To"))
        assert f"Hand-edited opening {token}." in (real[0].get("Text") or "")

        # Gone from the queue, and a second Send cannot repeat it.
        assert _drafts_for(admin_client, event["id"]) == []
        again = admin_client.post(f"/admin/email/queue/{draft_id}/send")
        assert again.status_code == 409, again.text
    finally:
        admin_client.delete("/admin/email/test-recipients", params={"email": tester})
        admin_client.delete(f"/admin/events/{event['id']}")


def test_dismissed_draft_sends_nothing(admin_client, mailpit):
    token = uuid.uuid4().hex[:8]
    event = _create_on_sale_event(admin_client, token)
    try:
        [draft] = _run_pass_until_drafted(admin_client, event["id"])

        dismissed = admin_client.post(f"/admin/email/queue/{draft['id']}/dismiss")
        assert dismissed.status_code == 200, dismissed.text
        assert _drafts_for(admin_client, event["id"]) == []

        refused = admin_client.post(f"/admin/email/queue/{draft['id']}/send")
        assert refused.status_code == 409, refused.text
        edit = admin_client.put(f"/admin/email/queue/{draft['id']}", json={"subject": "x"})
        assert edit.status_code == 409, edit.text

        # Another pass does not bring it back.
        admin_client.post("/admin/email/announce-now")
        assert _drafts_for(admin_client, event["id"]) == []
        assert _audience_mail(mailpit, event) == []
    finally:
        admin_client.delete(f"/admin/events/{event['id']}")


def test_dashboard_counts_what_is_waiting(admin_client):
    token = uuid.uuid4().hex[:8]
    event = _create_on_sale_event(admin_client, token)
    try:
        assert _run_pass_until_drafted(admin_client, event["id"])
        dash = admin_client.get("/admin/dashboard").json()
        assert dash["emailsAwaitingApproval"] >= 1
        assert any(w.get("action") == "email" for w in dash["warnings"])
    finally:
        for draft in _drafts_for(admin_client, event["id"]):
            admin_client.post(f"/admin/email/queue/{draft['id']}/dismiss")
        admin_client.delete(f"/admin/events/{event['id']}")
