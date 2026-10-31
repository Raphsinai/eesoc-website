"""The email queue and scheduled sending, without the stack.

Pure unit tests: the database adapter is replaced by an in-memory fake and the
SMTP layer by a recorder, so they run anywhere the api's requirements are
installed. They pin the rules the queue exists for:

  1. saving an event with a sale time drafts its on-sale email at once, sends
     nothing, and keeps the draft in step with the event — without ever
     sending on an approval of words that have since changed;
  2. an approved email goes at its time, once, from the scheduler; one whose
     event has gone is held, not sent;
  3. what is sent is one BCC'd message per batch — the people who asked about
     the event included — and their alerts are marked as they are told;
  4. who it goes to is the ticked audiences plus hand-added addresses minus
     hand-removed ones, and a hand-added address that has unsubscribed is
     skipped rather than mailed;
  5. recurring jobs run on the hour.
"""

import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"

os.environ.setdefault("DATABASE_ADAPTER_URL", "http://127.0.0.1:9")
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

from fastapi import HTTPException  # noqa: E402

database = importlib.import_module("clients.database")
mailer = importlib.import_module("utils.email")
clock = importlib.import_module("utils.clock")
emailing = importlib.import_module("services.emailing")
campaigns = importlib.import_module("services.campaigns")

SALE = "2099-02-01T12:00:00+00:00"

EVENT = {
    "id": 7,
    "slug": "robot-wars",
    "title": "Robot Wars",
    "category": "Socials",
    "venue": "EEE 403",
    "published": True,
    "starts_at": "2099-03-01T18:00:00+00:00",
    "ends_at": "2099-03-01T20:00:00+00:00",
    "sales_open_at": SALE,
    "sales_announced_at": None,
    "doors_close_at": None,
    "purchase_url": "https://shop.example.com/robot-wars",
    "ticket_types": [{"id": 1, "name": "Standard", "price_pence": 500}],
}


class FakeDatabase:
    """The adapter routes this flow touches, in memory, with its 409s."""

    def __init__(self):
        self.events = {7: dict(EVENT)}
        self.campaigns: dict[int, dict] = {}
        self.alerts = [
            {"id": 11, "email": "asker@example.com", "event_id": 7},
            {"id": 12, "email": "both@example.com", "event_id": 7},
        ]
        self.notified: list[int] = []
        self.optouts: list[dict] = []
        self.seeded: list[dict] = []
        # (kind, topic-or-event) -> rows the audience query returns.
        self.audience = {
            ("alerts", 7): [{"email": "asker@example.com"}, {"email": "both@example.com"}],
            ("topic", "tickets_on_sale"): [
                {"email": "both@example.com"}, {"email": "fan@example.com"},
            ],
            ("topic", "events_category:socials"): [{"email": "social@example.com"}],
        }

    def _campaign(self, path):
        return self.campaigns.get(int(path.split("/")[3]))

    async def get(self, path, params=None):
        params = params or {}
        if path == "/events":
            return [dict(e) for e in self.events.values()]
        if path.startswith("/events/"):
            event = self.events.get(int(path.split("/")[2]))
            return dict(event) if event else None
        if path == "/email/campaigns":
            rows = sorted(self.campaigns.values(), key=lambda r: -r["id"])
            if params.get("status"):
                wanted = params["status"].split(",")
                rows = [r for r in rows if r["status"] in wanted]
            if params.get("kind"):
                rows = [r for r in rows if r.get("kind") == params["kind"]]
            if params.get("event_id") is not None:
                rows = [r for r in rows if r.get("event_id") == params["event_id"]]
            return [dict(r) for r in rows[: params.get("limit", 50)]]
        if path.startswith("/email/campaigns/"):
            row = self._campaign(path)
            return dict(row) if row else None
        if path == "/email/sale-alerts":
            return [a for a in self.alerts if a["id"] not in self.notified]
        if path == "/email/optouts":
            return self.optouts
        if path == "/email/audience":
            key = (params["kind"], params.get("topic") or params.get("event_id"))
            return [dict(r) for r in self.audience.get(key, [])]
        if path == "/email/test-recipients":
            return []
        if path == "/product-links":
            return []
        return None

    async def post(self, path, json=None, params=None):
        if path == "/email/campaigns":
            row = {
                "id": len(self.campaigns) + 1, "status": "draft", "kind": "newsletter",
                "recipients": 0, "sent": 0, "failed": 0, "error": None,
                "extra_recipients": [], "excluded_recipients": [], "send_at": None,
                "edited_at": None, "reviewed_by": None, "created_at": "2099-01-01T00:00:00+00:00",
                **{k: v for k, v in json.items() if v is not None},
            }
            self.campaigns[row["id"]] = row
            return dict(row)
        if path.endswith("/decide"):
            row = self._campaign(path)
            if row["status"] != json.get("expect", "awaiting_approval"):
                raise HTTPException(409, f"no longer waiting ({row['status']})")
            row.update(status=json["status"], reviewed_by=json.get("reviewed_by"),
                       error=json.get("note"))
            if json.get("recipients") is not None:
                row["recipients"] = json["recipients"]
            return dict(row)
        if path.startswith("/events/") and path.endswith("/announced"):
            self.events[int(path.split("/")[2])]["sales_announced_at"] = "now"
            return {"claimed": True}
        if path == "/campaign-recipients":
            self.seeded.append(json)
            return {"inserted": len(json["emails"])}
        if path.startswith("/email/sale-alerts/") and path.endswith("/notified"):
            self.notified.append(int(path.split("/")[3]))
            return None
        raise AssertionError(f"unexpected POST {path}")

    async def put(self, path, json=None, params=None):
        assert path.endswith("/draft"), path
        row = self._campaign(path)
        by_hand = json.get("by_hand", True)
        if not (row["status"] == "awaiting_approval"
                or (not by_hand and row["status"] == "scheduled")):
            raise HTTPException(409, f"no longer waiting ({row['status']})")
        for key, value in json.items():
            if key != "by_hand" and value is not None:
                row[key] = value
        if by_hand:
            row["edited_at"] = "now"
        return dict(row)

    async def patch(self, path, json=None, params=None):
        row = self._campaign(path)
        row.update({k: v for k, v in (params or {}).items() if v is not None})
        return dict(row)


@pytest.fixture()
def fake(monkeypatch):
    db = FakeDatabase()
    for name in ("get", "post", "put", "patch"):
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


@pytest.fixture()
def sends(monkeypatch):
    """Bulk sends started (campaign id, recipient addresses), not run."""
    started = []
    monkeypatch.setattr(mailer, "configured", lambda *a, **k: True)
    monkeypatch.setattr(
        emailing, "send_bulk",
        lambda cid, recipients, build, **kw: ("send_bulk", cid, [r["email"] for r in recipients]),
    )
    monkeypatch.setattr(campaigns, "dispatch", started.append)
    return started


def run(coro):
    return asyncio.run(coro)


def iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def only_draft(fake) -> dict:
    [row] = fake.campaigns.values()
    return row


# ------------------------------------------------ 1. drafts follow the event


def test_saving_an_event_with_a_sale_time_drafts_its_email_and_sends_nothing(fake, outbox):
    run(campaigns.sync_on_sale(7))

    assert outbox == [], "drafting must not send anything"
    draft = only_draft(fake)
    assert draft["status"] == "awaiting_approval"
    assert draft["kind"] == "on_sale" and draft["event_id"] == 7
    assert draft["send_at"] == SALE, "timed for when sales open"
    assert draft["subject"] == "Tickets for Robot Wars are on sale"
    assert draft["audiences"] == [
        "alerts:7", "topic:tickets_on_sale", "topic:events_category:socials",
    ]
    assert draft["recipients"] == 4  # asker, both, fan, social — de-duplicated
    assert fake.events[7]["sales_announced_at"], "marked so nothing else announces it"
    assert fake.notified == [], "drafting must not mark anybody's alert as told"


def test_saving_again_does_not_write_a_second_draft(fake):
    run(campaigns.sync_on_sale(7))
    run(campaigns.sync_on_sale(7))
    run(campaigns.reconcile_on_sale())
    assert len(fake.campaigns) == 1


@pytest.mark.parametrize("change", [
    {"published": False},
    {"sales_open_at": None},
    {"starts_at": "2000-01-01T10:00:00+00:00"},
    {"sales_announced_at": "2026-01-01T00:00:00+00:00"},
])
def test_no_draft_for_an_event_that_does_not_want_one(fake, change):
    fake.events[7].update(change)
    run(campaigns.sync_on_sale(7))
    assert fake.campaigns == {}


def test_a_dismissed_email_is_not_written_again(fake):
    run(campaigns.sync_on_sale(7))
    only_draft(fake)["status"] = "dismissed"
    fake.events[7]["sales_announced_at"] = None  # even without the marker
    run(campaigns.sync_on_sale(7))
    assert len(fake.campaigns) == 1


def test_an_untouched_draft_follows_the_event(fake):
    run(campaigns.sync_on_sale(7))
    fake.events[7].update(title="Robot Wars II", sales_open_at="2099-02-02T09:30:00+00:00")
    run(campaigns.sync_on_sale(7))
    draft = only_draft(fake)
    assert draft["subject"] == "Tickets for Robot Wars II are on sale"
    assert "Robot Wars II" in draft["body"]
    assert campaigns.parse_time(draft["send_at"]) == campaigns.parse_time("2099-02-02T09:30:00+00:00")


def test_a_hand_edited_draft_keeps_its_words_but_follows_the_sale(fake):
    run(campaigns.sync_on_sale(7))
    run(database.put("/email/campaigns/1/draft", {"subject": "Our words"}))
    fake.events[7].update(title="Renamed", sales_open_at="2099-02-03T09:00:00+00:00")
    run(campaigns.sync_on_sale(7))
    draft = only_draft(fake)
    assert draft["subject"] == "Our words"
    assert campaigns.parse_time(draft["send_at"]) == campaigns.parse_time("2099-02-03T09:00:00+00:00")


def test_an_approved_email_moves_with_its_sale_and_stays_approved(fake):
    run(campaigns.sync_on_sale(7))
    only_draft(fake)["status"] = "scheduled"
    fake.events[7]["sales_open_at"] = "2099-02-05T12:00:00+00:00"
    run(campaigns.sync_on_sale(7))
    draft = only_draft(fake)
    assert draft["status"] == "scheduled"
    assert campaigns.parse_time(draft["send_at"]) == campaigns.parse_time("2099-02-05T12:00:00+00:00")


def test_approved_words_never_change_without_a_new_approval(fake):
    run(campaigns.sync_on_sale(7))
    only_draft(fake)["status"] = "scheduled"
    fake.events[7]["title"] = "Robot Wars: The Sequel"
    run(campaigns.sync_on_sale(7))
    draft = only_draft(fake)
    assert draft["status"] == "awaiting_approval", "back to a person"
    assert "approving again" in draft["error"]
    assert draft["subject"] == "Tickets for Robot Wars: The Sequel are on sale"


@pytest.mark.parametrize("change", [{"published": False}, {"sales_open_at": None}])
def test_unpublishing_takes_an_approved_email_off_the_schedule(fake, change):
    run(campaigns.sync_on_sale(7))
    only_draft(fake)["status"] = "scheduled"
    fake.events[7].update(change)
    run(campaigns.sync_on_sale(7))
    draft = only_draft(fake)
    assert draft["status"] == "awaiting_approval"
    assert draft["error"].startswith("Taken off the schedule")


def test_deleting_the_event_takes_an_approved_email_off_the_schedule(fake):
    run(campaigns.sync_on_sale(7))
    only_draft(fake)["status"] = "scheduled"
    del fake.events[7]
    run(campaigns.sync_on_sale(7))
    assert only_draft(fake)["status"] == "awaiting_approval"


# --------------------------------------------- 2. sent at its time, once


def test_scheduler_sends_what_is_due_once_and_sleeps_until_the_next(fake, sends):
    run(campaigns.sync_on_sale(7))
    due = only_draft(fake)
    due.update(status="scheduled", send_at=iso(-timedelta(seconds=1)), reviewed_by=3)
    later = iso(timedelta(hours=2))
    fake.campaigns[2] = {**due, "id": 2, "send_at": later, "event_id": 7, "kind": "newsletter",
                         "audiences": ["topic:tickets_on_sale"], "body": "Hi."}

    upcoming = run(campaigns.fire_due())

    assert campaigns.parse_time(later) == upcoming, "next wake-up is the next email's time"
    assert [s[1] for s in sends] == [1], "only the due one started"
    assert sorted(sends[0][2]) == [
        "asker@example.com", "both@example.com", "fan@example.com", "social@example.com",
    ]
    assert due["status"] == "sending" and due["reviewed_by"] == 3, "approver kept"
    assert fake.seeded and fake.seeded[0]["campaign_id"] == 1
    assert fake.campaigns[2]["status"] == "scheduled"

    run(campaigns.fire_due())
    assert len(sends) == 1, "never started twice"


def test_a_due_email_whose_event_is_gone_is_held_not_sent(fake, sends):
    run(campaigns.sync_on_sale(7))
    only_draft(fake).update(status="scheduled", send_at=iso(-timedelta(seconds=1)))
    fake.events[7]["published"] = False

    run(campaigns.fire_due())

    assert sends == []
    draft = only_draft(fake)
    assert draft["status"] == "awaiting_approval"
    assert draft["error"].startswith("Held at its send time")


def test_a_due_email_is_left_scheduled_while_mail_is_down(fake, sends, monkeypatch):
    run(campaigns.sync_on_sale(7))
    only_draft(fake).update(status="scheduled", send_at=iso(-timedelta(seconds=1)))
    monkeypatch.setattr(mailer, "configured", lambda *a, **k: False)

    run(campaigns.fire_due())

    assert sends == []
    assert only_draft(fake)["status"] == "scheduled", "goes as soon as mail is back"


# ------------------------------------------------- 5. recurring = on the hour


@pytest.mark.parametrize("now, expected", [
    ("2026-10-12T13:20:00+00:00", 40 * 60),
    ("2026-10-12T13:00:00+00:00", 60 * 60),
    # A wake-up a hair early has done this hour's run: the next is a full hour on.
    ("2026-10-12T13:59:58+00:00", 60 * 60 + 2),
    ("2026-10-12T14:00:00.500000+00:00", 60 * 60 - 0.5),
])
def test_recurring_jobs_wake_on_the_hour(now, expected):
    assert clock.seconds_until_next_hour(datetime.fromisoformat(now)) == expected


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


@pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason=(
        "builds a real message: on Python 3.12 the stdlib cannot serialise ANY "
        "message under utils/email.py's unfolded policy (max_line_length=0 sends "
        "it to a quoted-printable encoder that rejects 0). The api image runs "
        "3.14; the CI runner's own python3 is 3.12. The stack tests cover the "
        "same BCC property through the real api."
    ),
)
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


def test_approved_connected_society_email_runs_on_schedule_only_after_review(fake, sends, monkeypatch):
    """EESoc's society-list permission survives the approved scheduled send."""
    society = importlib.import_module('services.societies')
    async def roster(society_id):
        assert society_id == 3
        return [{'email': 'student@ic.ac.uk'}, {'email': 'second@ic.ac.uk'}]
    monkeypatch.setattr(society, 'mailing_list', roster)
    run(campaigns.queue(subject='Robotics briefing', body='Hello robotics.',
                        audiences=['society:3'], kind='newsletter',
                        send_at=iso(-timedelta(seconds=1))))
    row = only_draft(fake)
    assert row['recipients'] == 2, 'preview counts members of the selected roster'
    with pytest.raises(HTTPException) as exc:
        run(campaigns.recipients_for(row))
    assert exc.value.status_code == 403, 'resolution cannot bypass the grant'

    # An unapproved row does not become an authorised send simply because it
    # was set to scheduled: a named committee reviewer must approve it.
    row.update(status='scheduled', send_at=iso(-timedelta(seconds=1)), reviewed_by=None)
    run(campaigns.fire_due())
    assert sends == []
    assert row['status'] == 'awaiting_approval'

    row.update(status='scheduled', reviewed_by=17)
    run(campaigns.fire_due())
    assert sends == [('send_bulk', row['id'], ['student@ic.ac.uk', 'second@ic.ac.uk'])]
    assert row['status'] == 'sending'
