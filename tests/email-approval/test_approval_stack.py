"""The email queue and scheduled sending against the live test stack.

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var: without the stack it fails
with a connection error.

The whole path, through the real api, adapter, Postgres and mailpit:

  * publishing an event with a sale time drafts its on-sale email at once —
    no timer involved — and sends nothing;
  * the draft's words and recipients can be edited, approved for its time,
    taken back, approved again; moving the sale moves the send, and at that
    moment it goes by itself as one BCC'd message to the edited list;
  * unpublishing the event takes an approved email back off the schedule;
  * a draft can still be sent there and then, and a test goes to the test
    list only; a dismissed draft sends nothing and is not written again.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

SEND_TIMEOUT = 60.0


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat()


def _payload(token: str, sale: timedelta | None, **changes) -> dict:
    """A whole event: the admin update replaces every field, so the same
    payload is sent again with changes rather than a partial one."""
    return {
        "slug": f"sched-{token}",
        "title": f"Scheduled Night {token}",
        "category": "Socials",
        "venue": "EEE 403",
        "starts_at": _iso(timedelta(days=30)),
        "ends_at": _iso(timedelta(days=30, hours=2)),
        "sales_open_at": _iso(sale) if sale is not None else None,
        "published": True,
        **changes,
    }


def _create(admin_client, payload: dict) -> int:
    created = admin_client.post("/admin/events", json=payload)
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


def _update(admin_client, event_id: int, payload: dict) -> None:
    updated = admin_client.put(f"/admin/events/{event_id}", json=payload)
    assert updated.status_code == 200, updated.text


def _drafts(admin_client, event_id: int) -> list[dict]:
    items = admin_client.get("/admin/email/queue").json()["items"]
    return [q for q in items if (q.get("event") or {}).get("id") == event_id]


def _the_draft(admin_client, event_id: int) -> dict:
    drafts = _drafts(admin_client, event_id)
    assert len(drafts) == 1, drafts
    return drafts[0]


def _cleanup(admin_client, event_id: int) -> None:
    for draft in _drafts(admin_client, event_id):
        admin_client.post(f"/admin/email/queue/{draft['id']}/dismiss")
    admin_client.delete(f"/admin/events/{event_id}")


def _messages(mailpit, needle: str) -> list[dict]:
    """Every message in the sink whose subject contains `needle`, in full."""
    out = []
    listing = mailpit.get("/api/v1/messages", params={"limit": 500}).json()
    for summary in listing.get("messages", []):
        if needle in (summary.get("Subject") or ""):
            out.append(mailpit.get(f"/api/v1/message/{summary['ID']}").json())
    return out


def _on_sale_mail(mailpit, title: str) -> list[dict]:
    """The standard on-sale email, as the audience would get it. Exact
    subject, so the "waiting for approval" notice to the committee (sent when
    EMAIL_APPROVAL_NOTIFY is set) does not count."""
    subject = f"Tickets for {title} are on sale"
    return [m for m in _messages(mailpit, title) if m.get("Subject") == subject]


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


def test_publishing_with_a_sale_time_drafts_the_email_at_once(admin_client, mailpit):
    token = uuid.uuid4().hex[:8]
    payload = _payload(token, sale=timedelta(hours=2))
    event_id = _create(admin_client, payload)
    try:
        # No announce pass, no waiting: the save itself wrote it.
        draft = _the_draft(admin_client, event_id)
        assert draft["kind"] == "on_sale"
        assert draft["status"] == "awaiting_approval"
        assert datetime.fromisoformat(draft["sendAt"]) == datetime.fromisoformat(
            payload["sales_open_at"]
        )

        # Saving again, or renaming the event, keeps the one draft — renamed.
        renamed = {**payload, "title": f"Renamed Night {token}"}
        _update(admin_client, event_id, renamed)
        draft = _the_draft(admin_client, event_id)
        assert draft["subject"] == f"Tickets for Renamed Night {token} are on sale"

        assert _on_sale_mail(mailpit, payload["title"]) == []
        assert _on_sale_mail(mailpit, renamed["title"]) == []
    finally:
        _cleanup(admin_client, event_id)


def test_approved_email_goes_at_its_time_as_edited(admin_client, mailpit):
    token = uuid.uuid4().hex[:8]
    tester = f"sched-tester-{token}@example.com"
    added = f"sched-added-{token}@example.com"
    payload = _payload(token, sale=timedelta(hours=1))
    event_id = _create(admin_client, payload)
    assert admin_client.post(
        "/admin/email/test-recipients", json={"email": tester}
    ).status_code == 200

    try:
        draft_id = _the_draft(admin_client, event_id)["id"]
        subject = f"Scheduled {token}: tickets are out"
        edited = admin_client.put(
            f"/admin/email/queue/{draft_id}",
            json={"subject": subject, "body": f"Hand-edited opening {token}.",
                  "audiences": ["test"], "extra": [added]},
        )
        assert edited.status_code == 200, edited.text
        assert {tester, added} <= {r["email"] for r in edited.json()["recipientList"]}

        # Approve for its time; while approved it cannot be edited; taken back
        # and approved again.
        assert admin_client.post(f"/admin/email/queue/{draft_id}/schedule").status_code == 200
        assert _the_draft(admin_client, event_id)["status"] == "scheduled"
        refused = admin_client.put(f"/admin/email/queue/{draft_id}", json={"subject": "x"})
        assert refused.status_code == 409, refused.text
        assert admin_client.post(f"/admin/email/queue/{draft_id}/unschedule").status_code == 200
        assert _the_draft(admin_client, event_id)["status"] == "awaiting_approval"
        assert admin_client.post(f"/admin/email/queue/{draft_id}/schedule").status_code == 200

        # Bring the sale forward to a few seconds from now. The approval holds
        # (the words were hand-edited, so they do not change); the send moves.
        _update(admin_client, event_id, {**payload, "sales_open_at": _iso(timedelta(seconds=4))})
        moved = _the_draft(admin_client, event_id)
        assert moved["status"] == "scheduled"
        assert datetime.fromisoformat(moved["sendAt"]) < datetime.now(timezone.utc) + timedelta(minutes=1)

        # Nobody presses anything: it goes at its time.
        _wait_for_status(admin_client, draft_id, "sent")
        real = [m for m in _messages(mailpit, subject) if not m["Subject"].startswith("[Test]")]
        assert len(real) == 1, "one BCC'd message, not one per recipient"
        assert {tester, added} <= _addresses(real[0], "To", "Bcc")
        assert not ({tester, added} & _addresses(real[0], "To")), "BCC'd"
        assert f"Hand-edited opening {token}." in (real[0].get("Text") or "")
        assert _drafts(admin_client, event_id) == []
    finally:
        admin_client.delete("/admin/email/test-recipients", params={"email": tester})
        _cleanup(admin_client, event_id)


def test_unpublishing_takes_an_approved_email_off_the_schedule(admin_client):
    token = uuid.uuid4().hex[:8]
    payload = _payload(token, sale=timedelta(hours=1))
    event_id = _create(admin_client, payload)
    try:
        draft_id = _the_draft(admin_client, event_id)["id"]
        admin_client.put(
            f"/admin/email/queue/{draft_id}",
            json={"audiences": [], "extra": [f"unpub-{token}@example.com"]},
        )
        assert admin_client.post(f"/admin/email/queue/{draft_id}/schedule").status_code == 200

        _update(admin_client, event_id, {**payload, "published": False})

        draft = _the_draft(admin_client, event_id)
        assert draft["status"] == "awaiting_approval"
        assert "Taken off the schedule" in (draft["note"] or "")
    finally:
        _cleanup(admin_client, event_id)


def test_a_draft_whose_sale_has_opened_is_sent_now_and_tested_first(admin_client, mailpit):
    token = uuid.uuid4().hex[:8]
    tester = f"now-tester-{token}@example.com"
    payload = _payload(token, sale=-timedelta(minutes=5))
    event_id = _create(admin_client, payload)
    admin_client.post("/admin/email/test-recipients", json={"email": tester})
    try:
        draft_id = _the_draft(admin_client, event_id)["id"]
        subject = f"Sent now {token}"
        admin_client.put(
            f"/admin/email/queue/{draft_id}", json={"subject": subject, "audiences": ["test"]}
        )
        late = admin_client.post(f"/admin/email/queue/{draft_id}/schedule")
        assert late.status_code == 400, "its time has passed — send it now instead"

        tested = admin_client.post(f"/admin/email/queue/{draft_id}/test")
        assert tested.status_code == 200, tested.text
        assert _messages(mailpit, f"[Test] {subject}"), "the test never reached the sink"
        assert _the_draft(admin_client, event_id)["status"] == "awaiting_approval"

        sent = admin_client.post(f"/admin/email/queue/{draft_id}/send")
        assert sent.status_code == 200, sent.text
        _wait_for_status(admin_client, draft_id, "sent")
        again = admin_client.post(f"/admin/email/queue/{draft_id}/send")
        assert again.status_code == 409, again.text
    finally:
        admin_client.delete("/admin/email/test-recipients", params={"email": tester})
        _cleanup(admin_client, event_id)


def test_dismissed_draft_sends_nothing_and_is_not_written_again(admin_client, mailpit):
    token = uuid.uuid4().hex[:8]
    payload = _payload(token, sale=timedelta(hours=1))
    event_id = _create(admin_client, payload)
    try:
        draft_id = _the_draft(admin_client, event_id)["id"]
        assert admin_client.post(f"/admin/email/queue/{draft_id}/dismiss").status_code == 200
        assert admin_client.post(f"/admin/email/queue/{draft_id}/send").status_code == 409

        _update(admin_client, event_id, {**payload, "title": f"Still dismissed {token}"})
        assert _drafts(admin_client, event_id) == []
        assert _on_sale_mail(mailpit, payload["title"]) == []
    finally:
        _cleanup(admin_client, event_id)


def test_dashboard_counts_what_needs_approval(admin_client):
    token = uuid.uuid4().hex[:8]
    event_id = _create(admin_client, _payload(token, sale=timedelta(hours=1)))
    try:
        dash = admin_client.get("/admin/dashboard").json()
        assert dash["emailsAwaitingApproval"] >= 1
        assert any(w.get("action") == "email" for w in dash["warnings"])
    finally:
        _cleanup(admin_client, event_id)
