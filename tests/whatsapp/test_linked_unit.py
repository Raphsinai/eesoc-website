"""Posting into WhatsApp groups through the linked number, without the stack.

The adapter and the WhatsApp service are both faked in memory. These pin what
services/whatsapp.py promises, because each failure here is one the society
pays for in its own groups:

  1. WHATSAPP_GROUP_ALLOWLIST, when set, fences any environment; unset,
     staging posts where its Email tab enabled (after asking);
  2. a post that goes with an email is claimed per group before it is made,
     so a second scheduler — or a retry — never posts it twice;
  3. a second press of "Post" with the same words is not a second post;
  4. a failure is recorded and does not stop the next group;
  5. an email posts only into the groups picked for it — still enabled —
     with its event's details; nothing is picked by default;
  6. only a group the number can post in may be chosen.
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

from fastapi import HTTPException  # noqa: E402

database = importlib.import_module("clients.database")
link = importlib.import_module("clients.whatsapp")
settings = importlib.import_module("config").settings
whatsapp = importlib.import_module("services.whatsapp")
campaigns = importlib.import_module("services.campaigns")

ANNOUNCE = {"jid": "111-1@g.us", "name": "EESoc Announcements"}
YEAR2 = {"jid": "222-2@g.us", "name": "EEE Year 2"}

EVENT = {
    "id": 7, "slug": "robot-wars", "title": "Robot Wars", "venue": "EEE 403",
    "published": True, "starts_at": "2099-03-01T18:00:00+00:00",
    "ends_at": "2099-03-01T20:00:00+00:00", "sales_open_at": "2099-02-01T12:00:00+00:00",
    "doors_close_at": None, "purchase_url": None,
    "ticket_types": [{"id": 1, "name": "Standard", "price_pence": 500}],
}


class FakeDatabase:
    def __init__(self):
        self.groups = [dict(ANNOUNCE), dict(YEAR2)]
        self.posts: list[dict] = []

    async def get(self, path, params=None):
        params = params or {}
        if path == "/whatsapp/groups":
            return [dict(g) for g in self.groups]
        if path == "/whatsapp/posts":
            rows = [p for p in reversed(self.posts)
                    if params.get("jid") in (None, p["jid"])
                    and params.get("campaign_id") in (None, p["campaign_id"])]
            return [dict(p) for p in rows]
        if path == "/events/7":
            return dict(EVENT)
        if path in ("/product-links", "/union/products"):
            return []
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path, json=None, params=None):
        assert path == "/whatsapp/posts", path
        if json["campaign_id"] is not None and any(
            p["campaign_id"] == json["campaign_id"] and p["jid"] == json["jid"] for p in self.posts
        ):
            raise HTTPException(409, "Already posted")
        row = {**json, "id": len(self.posts) + 1, "status": "sending", "message_id": None, "error": None}
        self.posts.append(row)
        return dict(row)

    async def patch(self, path, json=None, params=None):
        row = self.posts[int(path.rsplit("/", 1)[1]) - 1]
        row.update({k: v for k, v in json.items() if v is not None or k == "error"})
        return dict(row)

    async def put(self, path, json=None, params=None):
        assert path == "/whatsapp/groups"
        self.groups = [g for g in self.groups if g["jid"] != json["jid"]] + [json]
        return json

    async def delete(self, path, params=None):
        self.groups = [g for g in self.groups if g["jid"] != params["jid"]]


class FakeLink:
    """The WhatsApp service: records posts, and can be told to refuse."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.refuse: dict[str, str] = {}
        self.groups_list = [
            {**ANNOUNCE, "canPost": True},
            {**YEAR2, "canPost": True},
            {"jid": "333-3@g.us", "name": "Committee chat", "canPost": False},
        ]

    async def send(self, jid, text, key):
        if jid in self.refuse:
            raise link.WhatsAppUnavailable(self.refuse[jid], 429)
        self.sent.append((jid, text, key))
        return {"id": f"m{len(self.sent)}"}

    async def groups(self):
        return [dict(g) for g in self.groups_list]


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDatabase()
    for name in ("get", "post", "patch", "put", "delete"):
        monkeypatch.setattr(database, name, getattr(fake, name))
    return fake


@pytest.fixture()
def wa(monkeypatch):
    fake = FakeLink()
    monkeypatch.setattr(link, "send", fake.send)
    monkeypatch.setattr(link, "groups", fake.groups)
    return fake


@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "whatsapp_group_allowlist", "")


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ 1. staging


def test_the_allowlist_fences_any_environment_when_set(monkeypatch, db, wa):
    # Unset: wherever the Email tab enabled — staging included, which asks
    # in the browser before a group is enabled there.
    monkeypatch.setattr(settings, "environment", "staging")
    assert whatsapp.allowed(ANNOUNCE["jid"])
    assert run(whatsapp.post("hello", [ANNOUNCE]))[0]["status"] == "sent"

    # Set: nothing outside it, whatever is enabled.
    monkeypatch.setattr(settings, "whatsapp_group_allowlist", "222-2@g.us")
    results = run(whatsapp.post("hello again", [ANNOUNCE, YEAR2]))
    assert [r["status"] for r in results] == ["skipped", "sent"]
    assert [s[0] for s in wa.sent] == [ANNOUNCE["jid"], "222-2@g.us"]


# ----------------------------------------------------- 2. one post per email


def test_an_email_is_posted_once_per_group_however_often_it_is_fired(db, wa):
    first = run(whatsapp.post("on sale", [ANNOUNCE, YEAR2], campaign_id=41, by=3))
    assert [r["status"] for r in first] == ["sent", "sent"]
    again = run(whatsapp.post("on sale", [ANNOUNCE, YEAR2], campaign_id=41, by=3))
    assert [r["status"] for r in again] == ["skipped", "skipped"]
    assert len(wa.sent) == 2
    # Recorded before posting, finished after; the service key is the claim's id.
    assert [p["status"] for p in db.posts] == ["sent", "sent"]
    assert wa.sent[0][2] == "post:1"


# ------------------------------------------------------ 3. a double press


def test_pressing_post_twice_posts_once(db, wa):
    run(whatsapp.post("AGM tonight", [ANNOUNCE]))
    second = run(whatsapp.post("AGM tonight", [ANNOUNCE]))
    assert second[0]["status"] == "skipped"
    assert len(wa.sent) == 1
    # Different words are a different post.
    run(whatsapp.post("AGM moved to 403", [ANNOUNCE]))
    assert len(wa.sent) == 2


# ------------------------------------------------------- 4. failures


def test_a_refused_post_is_recorded_and_the_next_group_still_goes(db, wa):
    wa.refuse[ANNOUNCE["jid"]] = "20 posts in the last hour is the limit."
    results = run(whatsapp.post("hello", [ANNOUNCE, YEAR2], campaign_id=5))
    assert [r["status"] for r in results] == ["failed", "sent"]
    assert db.posts[0]["status"] == "failed"
    assert "limit" in db.posts[0]["error"]
    # A failed post is not a reason to refuse the same words next time.
    wa.refuse.clear()
    assert run(whatsapp.post("hello", [ANNOUNCE]))[0]["status"] == "sent"


# --------------------------------------------------- 5. with an email


ROW = {
    "id": 9, "kind": "on_sale", "event_id": 7, "subject": "Tickets for Robot Wars are on sale",
    "heading": "", "body": "", "whatsapp_groups": [YEAR2["jid"]], "reviewed_by": 3,
}


def test_an_email_goes_only_into_the_groups_picked_for_it(db, wa):
    # Nothing picked: nothing posted, however many groups are enabled.
    assert run(whatsapp.post_campaign({**ROW, "whatsapp_groups": []})) == []
    assert wa.sent == []

    results = run(whatsapp.post_campaign(ROW))
    assert [(r["name"], r["status"]) for r in results] == [("EEE Year 2", "sent")]
    text = wa.sent[0][1]
    assert text.startswith("*Tickets for Robot Wars are on sale*\n\nTickets for *Robot Wars* are on sale now.")
    assert "*Where:* EEE 403" in text
    assert all(p["campaign_id"] == 9 and p["created_by"] == 3 for p in db.posts)


def test_a_group_picked_for_an_email_but_disabled_since_is_left_out(db, wa):
    db.groups = [dict(ANNOUNCE)]  # Year 2 disabled after the email picked it
    row = {**ROW, "id": 10, "whatsapp_groups": [YEAR2["jid"], ANNOUNCE["jid"]]}
    results = run(whatsapp.post_campaign(row))
    assert [r["name"] for r in results] == ["EESoc Announcements"]
    assert [s[0] for s in wa.sent] == [ANNOUNCE["jid"]]


def test_an_email_with_no_groups_chosen_posts_nothing_and_does_not_raise(db, wa):
    db.groups = []
    assert run(whatsapp.post_campaign(ROW)) == []


def test_starting_a_send_dispatches_the_whatsapp_post_only_when_ticked(monkeypatch):
    started = []

    async def fake_recipients(row):
        return [{"email": "a@example.com"}], []

    async def fake_builder(row, **kwargs):
        return None

    async def fake_post(path, json=None, params=None):
        return {**ROW, "status": "sending"}

    monkeypatch.setattr(campaigns, "recipients_for", fake_recipients)
    monkeypatch.setattr(campaigns, "builder_for", fake_builder)
    monkeypatch.setattr(campaigns, "check_sendable", lambda *a: None)
    monkeypatch.setattr(database, "post", fake_post)
    monkeypatch.setattr(campaigns.emailing, "send_bulk", lambda *a, **k: ("email", a[0]))
    monkeypatch.setattr(whatsapp, "post_campaign", lambda row: ("whatsapp", row["id"], row["reviewed_by"]))
    monkeypatch.setattr(campaigns, "dispatch", started.append)

    run(campaigns.start_send({**ROW, "whatsapp_groups": []}, expect="scheduled", reviewed_by=3))
    assert started == [("email", 9)]
    started.clear()
    run(campaigns.start_send(ROW, expect="scheduled", reviewed_by=4))
    assert started == [("email", 9), ("whatsapp", 9, 4)]


# --------------------------------------------------------- 6. the routes


def test_only_a_group_the_number_can_post_in_may_be_chosen(db, wa):
    routes = importlib.import_module("routes.whatsapp")
    session = type("S", (), {"user": type("U", (), {"id": 3})()})()
    with pytest.raises(HTTPException) as cannot:
        run(routes.choose_group(jid="333-3@g.us", chosen=True, session=session))
    assert cannot.value.status_code == 400
    with pytest.raises(HTTPException) as unknown:
        run(routes.choose_group(jid="999-9@g.us", chosen=True, session=session))
    assert unknown.value.status_code == 404

    db.groups = []
    run(routes.choose_group(jid=YEAR2["jid"], chosen=True, session=session))
    assert db.groups == [{"jid": YEAR2["jid"], "name": "EEE Year 2", "added_by": 3}]
    run(routes.choose_group(jid=YEAR2["jid"], chosen=False, session=session))
    assert db.groups == []


def test_posting_now_needs_words_and_groups_picked_from_the_enabled(db, wa):
    routes = importlib.import_module("routes.whatsapp")
    session = type("S", (), {"user": type("U", (), {"id": 3})()})()

    def post_now(**kw):
        args = {"heading": "AGM", "body": "Tonight, 6pm.", "event_id": None, "kind": None,
                "groups": [YEAR2["jid"]], "session": session}
        return run(routes.post_now(**{**args, **kw}))

    for bad in ({"heading": "", "body": ""}, {"groups": []}, {"groups": ["999-9@g.us"]}):
        with pytest.raises(HTTPException) as refused:
            post_now(**bad)
        assert refused.value.status_code == 400, bad
    assert wa.sent == []

    res = post_now()
    assert res["ok"] and res["message"] == "Posted to EEE Year 2."
    # Only the group picked — not every enabled one.
    assert wa.sent == [(YEAR2["jid"], "*AGM*\n\nTonight, 6pm.", "post:1")]


# ------------------------------------------------- 7. what the browser gets


def test_the_services_own_failures_never_reach_the_browser_as_502(monkeypatch):
    """Cloudflare swaps a 502 from the site for its HTML error page, which the
    Email tab once showed in full instead of the reason — so the service's
    failures become 503, and its refusals keep their own status."""
    import httpx

    def reply(status, detail):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": detail}))
        return httpx.AsyncClient(base_url="http://whatsapp:8080", transport=transport)

    monkeypatch.setattr(settings, "whatsapp_api_url", "http://whatsapp:8080")
    for service_status, expected in ((500, 503), (502, 503), (409, 409), (429, 429)):
        monkeypatch.setattr(link, "client", lambda s=service_status: reply(s, "EBUSY: resource busy"))
        with pytest.raises(link.WhatsAppUnavailable) as raised:
            run(link.link("447700900123"))
        assert raised.value.status == expected, service_status
        assert "EBUSY" in str(raised.value)
