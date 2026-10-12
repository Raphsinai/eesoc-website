"""The email approval queue, without the stack.

Pure unit tests: the database adapter is replaced by an in-memory fake and the
SMTP layer by a recorder, so they run anywhere the api's requirements are
installed. They pin the three rules the queue exists for:

  1. the timer writes the on-sale email and sends nothing;
  2. what is sent is one BCC'd message per batch — the people who asked about
     the event included, no longer one message each — and their alerts are
     marked as they are told;
  3. who it goes to is the ticked audiences plus hand-added addresses minus
     hand-removed ones, and a hand-added address that has unsubscribed is
     skipped rather than mailed.
"""

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"

os.environ.setdefault("DATABASE_ADAPTER_URL", "http://127.0.0.1:9")
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

database = importlib.import_module("clients.database")
mailer = importlib.import_module("utils.email")
emailing = importlib.import_module("services.emailing")
campaigns = importlib.import_module("services.campaigns")
announce = importlib.import_module("services.announce")

EVENT = {
    "id": 7,
    "slug": "robot-wars",
    "title": "Robot Wars",
    "category": "Socials",
    "venue": "EEE 403",
    "published": True,
    "starts_at": "2099-03-01T18:00:00+00:00",
    "ends_at": "2099-03-01T20:00:00+00:00",
    "doors_close_at": None,
    "purchase_url": "https://shop.example.com/robot-wars",
    "ticket_types": [{"id": 1, "name": "Standard", "price_pence": 500}],
}


class FakeDatabase:
    """The adapter routes this flow touches, in memory."""

    def __init__(self):
        self.campaigns: dict[int, dict] = {}
        self.alerts = [
            {"id": 11, "email": "asker@example.com", "event_id": 7},
            {"id": 12, "email": "both@example.com", "event_id": 7},
        ]
        self.notified: list[int] = []
        self.optouts: list[dict] = []
        # (kind, topic-or-event) -> rows the audience query returns.
        self.audience = {
            ("alerts", 7): [{"email": "asker@example.com"}, {"email": "both@example.com"}],
            ("topic", "tickets_on_sale"): [
                {"email": "both@example.com"}, {"email": "fan@example.com"},
            ],
            ("topic", "events_category:socials"): [{"email": "social@example.com"}],
        }
        self.due = [EVENT]

    async def get(self, path, params=None):
        params = params or {}
        if path == "/events/due-announcement":
            return self.due
        if path.startswith("/events/"):
            return EVENT if int(path.split("/")[2]) == EVENT["id"] else None
        if path == "/email/sale-alerts":
            return [a for a in self.alerts if a["id"] not in self.notified]
        if path == "/email/optouts":
            return self.optouts
        if path == "/email/audience":
            key = (params["kind"], params.get("topic") or params.get("event_id"))
            return [dict(r) for r in self.audience.get(key, [])]
        if path == "/email/test-recipients":
            return []
        if path.startswith("/email/campaigns/"):
            return self.campaigns.get(int(path.rsplit("/", 1)[1]))
        if path == "/product-links":
            return []
        return None

    async def post(self, path, json=None, params=None):
        if path == "/email/campaigns":
            row = {
                "id": len(self.campaigns) + 1, "recipients": 0, "sent": 0, "failed": 0,
                "extra_recipients": [], "excluded_recipients": [], **json,
            }
            self.campaigns[row["id"]] = row
            return dict(row)
        if path.endswith("/announced"):
            return {"claimed": True}
        if path.startswith("/email/sale-alerts/") and path.endswith("/notified"):
            self.notified.append(int(path.split("/")[3]))
            return None
        raise AssertionError(f"unexpected POST {path}")

    async def patch(self, path, json=None, params=None):
        row = self.campaigns[int(path.rsplit("/", 1)[1])]
        row.update({k: v for k, v in (params or {}).items() if v is not None})
        return dict(row)


@pytest.fixture()
def fake(monkeypatch):
    db = FakeDatabase()
    for name in ("get", "post", "patch"):
        monkeypatch.setattr(database, name, getattr(db, name))
    return db


@pytest.fixture()
def outbox(monkeypatch):
    """Every message that would have gone to SMTP: (recipients, subject, text, html)."""
    sent = []

    async def fake_send_many(recipients, subject, body, html=None, *args, **kwargs):
        sent.append((list(recipients), subject, body, html))
        return list(recipients)

    monkeypatch.setattr(mailer, "send_many", fake_send_many)
    return sent


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------- 1. nothing auto-sends


def test_on_sale_pass_queues_a_draft_and_sends_nothing(fake, outbox):
    result = run(announce.announce_sales())

    assert outbox == [], "the on-sale pass must not send anything itself"
    assert result["events"] == 1
    [draft] = fake.campaigns.values()
    assert draft["status"] == "awaiting_approval"
    assert draft["kind"] == "on_sale"
    assert draft["event_id"] == EVENT["id"]
    assert draft["subject"] == "Tickets for Robot Wars are on sale"
    assert draft["audiences"] == [
        "alerts:7", "topic:tickets_on_sale", "topic:events_category:socials",
    ]
    # asker, both, fan, social — de-duplicated across audiences.
    assert draft["recipients"] == 4
    assert fake.notified == [], "queueing must not mark anybody's alert as told"


# ---------------------------------------------------- 2. one BCC'd message


def test_send_is_one_bcc_message_including_the_people_who_asked(fake, outbox):
    row = {
        "id": 1, "kind": "on_sale", "event_id": 7,
        "subject": "Robot Wars: tickets out now", "heading": "", "body": "Go **now**.",
    }
    build = run(campaigns.builder_for(row))
    batch = [{"email": e} for e in
             ("asker@example.com", "both@example.com", "fan@example.com")]

    reached = run(build(batch, emailing.general_unsubscribe_url()))

    assert len(outbox) == 1, "one message for the batch, not one per person"
    recipients, subject, text, html = outbox[0]
    assert sorted(recipients) == sorted(r["email"] for r in batch)
    assert subject == "Robot Wars: tickets out now"
    assert "Go now." in text and "<strong>now</strong>" in html
    # The event's own details still come from the event, not the draft.
    assert "EEE 403" in text and "Standard" in text
    assert sorted(reached) == sorted(recipients)
    # Both askers in the batch are marked told; nobody else had an alert.
    assert sorted(fake.notified) == [11, 12]


def test_bcc_send_hides_the_audience_from_each_other(monkeypatch):
    """Through the real send_many: more than one recipient means the visible
    To is our own mailbox and everybody is on the envelope only."""
    captured = {}

    def fake_send_sync(message, prof, recipients):
        captured["to"] = message["To"]
        captured["envelope"] = list(recipients)
        return {}

    monkeypatch.setattr(mailer, "_send_sync", fake_send_sync)
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings, "smtp_user", "")
    monkeypatch.setattr(mailer.settings, "mail_allowlist", "")
    monkeypatch.setattr(mailer.settings, "environment", "production")

    subject, text, html = mailer.tickets_on_sale_message(
        "Robot Wars", "Sat 1 Mar", "EEE 403", "https://x/e", "https://x/unsubscribe",
    )
    run(mailer.send_bulk_message(["a@example.com", "b@example.com"], subject, text, html,
                                 "https://x/unsubscribe"))

    assert captured["envelope"] == ["a@example.com", "b@example.com"]
    assert "a@example.com" not in captured["to"] and "b@example.com" not in captured["to"]


def test_test_send_marks_no_alerts(fake, outbox):
    row = {"id": 1, "kind": "on_sale", "event_id": 7, "subject": "s", "body": ""}
    build = run(campaigns.builder_for(row, mark_alerts=False))
    run(build([{"email": "asker@example.com"}], emailing.general_unsubscribe_url()))
    assert len(outbox) == 1
    assert fake.notified == []


def test_blank_opening_falls_back_to_the_standard_line():
    _, text, html = mailer.tickets_on_sale_message(
        "Robot Wars", "Sat 1 Mar", "EEE 403", "https://x/e", "https://x/u",
    )
    assert text.startswith("Tickets for Robot Wars are on sale now.")
    assert "Tickets for <strong>Robot Wars</strong> are on sale now." in html


def test_preview_is_the_message_that_will_be_sent(fake, outbox):
    row = {"id": 1, "kind": "on_sale", "event_id": 7,
           "subject": "Edited subject", "heading": "Edited heading", "body": "Edited opening."}
    shown = run(campaigns.preview(row))
    build = run(campaigns.builder_for(row, mark_alerts=False))
    run(build([{"email": "x@example.com"}], emailing.general_unsubscribe_url()))

    _, subject, _, html = outbox[0]
    assert shown["subject"] == subject == "Edited subject"
    assert shown["html"] == html
    assert "Edited heading" in html and "Edited opening." in html


def test_a_deleted_event_cannot_be_built(fake):
    with pytest.raises(campaigns.Unbuildable):
        run(campaigns.builder_for({"id": 1, "kind": "on_sale", "event_id": 999, "subject": "s"}))


# ------------------------------------------------- 3. editable recipients


def test_recipients_are_audiences_plus_added_minus_removed(fake):
    row = {
        "audiences": ["alerts:7", "topic:tickets_on_sale"],
        "extra_recipients": ["New@Example.com", "fan@example.com"],
        "excluded_recipients": ["both@example.com"],
    }
    recipients, skipped = run(campaigns.recipients_for(row))

    assert [r["email"] for r in recipients] == [
        "asker@example.com", "fan@example.com", "new@example.com",
    ]
    assert skipped == []


def test_hand_added_address_that_unsubscribed_is_skipped(fake):
    fake.optouts = [
        {"email": "gone@example.com", "topic": "*"},
        # A department list cannot be unsubscribed by us, whatever is stored.
        {"email": "ee1ugs@ic.ac.uk", "topic": "*"},
    ]
    row = {"audiences": [], "extra_recipients": ["gone@example.com", "ee1ugs@ic.ac.uk"],
           "excluded_recipients": []}
    recipients, skipped = run(campaigns.recipients_for(row))

    assert skipped == ["gone@example.com"]
    assert [(r["email"], r["list"]) for r in recipients] == [("ee1ugs@ic.ac.uk", True)]


def test_clean_addresses_normalises_and_drops_junk():
    assert campaigns.clean_addresses(
        [" A@B.com ", "a@b.com", "not-an-address", "@nobody", "", "c@d.org"]
    ) == ["a@b.com", "c@d.org"]


def test_warning_flags_an_event_that_has_started():
    started = {**EVENT, "starts_at": "2000-01-01T10:00:00+00:00"}
    assert campaigns.warning({"kind": "on_sale"}, started) == "The event has already started."
    assert campaigns.warning({"kind": "on_sale"}, None).startswith("The event has been deleted")
    assert campaigns.warning({"kind": "on_sale"}, EVENT) is None
    assert campaigns.warning({"kind": "newsletter"}, None) is None
