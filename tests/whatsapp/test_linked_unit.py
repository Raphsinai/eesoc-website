"""Posting into WhatsApp groups through the linked number, without the stack.

The adapter and the WhatsApp service are both faked in memory. These pin what
services/whatsapp.py promises, because each failure here is one the society
pays for in its own groups:

  1. WHATSAPP_GROUP_ALLOWLIST, when set, fences any environment;
  2. a post that goes with an email is claimed per group before it is made,
     so a second scheduler — or a retry — never posts it twice;
  3. a second press of "Post" with the same words is not a second post;
  4. a failure is recorded and does not stop the next group;
  5. every group the linked number can post in is an audience like any other,
     read live from the number: ticked in the same list, skipped by email,
     posted into by the same Send — a newsletter, an event announcement, a
     queued email, the roles digest, a sponsor's marketing — WhatsApp alone
     included;
  6. a send that ticks a group it can't post in, or while WhatsApp is down,
     says so instead of reaching fewer places than the screen showed.
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

ANNOUNCE = {"jid": "111-1@g.us", "name": "Announcements — EESoc"}
YEAR2 = {"jid": "222-2@g.us", "name": "EEE Year 2"}

EVENT = {
    "id": 7, "slug": "robot-wars", "title": "Robot Wars", "venue": "EEE 403",
    "published": True, "starts_at": "2099-03-01T18:00:00+00:00",
    "ends_at": "2099-03-01T20:00:00+00:00", "sales_open_at": "2099-02-01T12:00:00+00:00",
    "doors_close_at": None, "purchase_url": None,
    "ticket_types": [{"id": 1, "name": "Standard", "price_pence": 500}],
}


class FakeDatabase:
    """The adapter's WhatsApp posts, with the per-email claim's 409."""

    def __init__(self):
        self.posts: list[dict] = []

    async def get(self, path, params=None):
        params = params or {}
        if path == '/auth/admins':
            return [{'user_id':3,'email':'committee@ic.ac.uk','role':'events',
                     'permissions':['whatsapp.post']}]
        if path == '/committee-roles':
            return []
        if path == '/external-societies/whatsapp/groups/settings':
            return [
                {'account_key':'eesoc','jid':jid,'society_id':None,'enabled':True,
                 'eesoc_enabled':True,'eesoc_user_ids':[3]}
                for jid in ('111-1@g.us','222-2@g.us')
            ]
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


class FakeLink:
    """The WhatsApp service: the number's groups, live; records posts, and
    can be told to refuse a post or to be down altogether."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.refuse: dict[str, str] = {}
        self.down = False
        self.groups_list = [
            {"jid": "111-1@g.us", "name": "EESoc", "size": 412, "canPost": True,
             "communityAnnouncements": True, "parent": {"jid": "999-0@g.us", "name": "EESoc"}},
            {"jid": "222-2@g.us", "name": "EEE Year 2", "size": 180, "canPost": True, "parent": None},
            {"jid": "333-3@g.us", "name": "Committee chat", "size": 12, "canPost": False, "parent": None},
        ]

    async def send(self, jid, text, key, account="eesoc"):
        if jid in self.refuse:
            raise link.WhatsAppUnavailable(self.refuse[jid], 429)
        self.sent.append((jid, text, key))
        return {"id": f"m{len(self.sent)}"}

    async def groups(self, account="eesoc"):
        if self.down:
            raise link.WhatsAppUnavailable("WhatsApp is not connected right now (reconnecting).", 409)
        return [dict(g) for g in self.groups_list]


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDatabase()
    for name in ("get", "post", "patch"):
        monkeypatch.setattr(database, name, getattr(fake, name))
    return fake


@pytest.fixture()
def wa(monkeypatch):
    fake = FakeLink()
    monkeypatch.setattr(link, "send", fake.send)
    monkeypatch.setattr(link, "groups", fake.groups)
    monkeypatch.setattr(link, "configured", lambda: True)
    return fake


@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "whatsapp_group_allowlist", "")


def run(coro):
    return asyncio.run(coro)


def session_of(user_id=3, permissions=None):
    """An admin session with the communication grants required by each channel.

    Direct-call tests skip FastAPI dependency injection, so the fake must
    implement the same permission check as a real AuthSession.
    """
    grants = frozenset({"email.send", "whatsapp.post"} if permissions is None else permissions)
    return type("S", (), {
        "user": type("U", (), {"id": user_id})(),
        "can": lambda self, name: name in grants,
    })()


def permitted(*items):
    return [{**g,'scope':'eesoc','account':'eesoc'} for g in items]


# ------------------------------------------------------------ 1. staging


def test_the_allowlist_fences_any_environment_when_set(monkeypatch, db, wa):
    # Unset: any group the number can post in — staging included, whose Send
    # says first that its WhatsApp is the real one.
    monkeypatch.setattr(settings, "environment", "staging")
    assert whatsapp.allowed(ANNOUNCE["jid"])
    assert run(whatsapp.post("hello", permitted(ANNOUNCE), by=3))[0]["status"] == "sent"

    # Set: nothing outside it — not posted, and not offered either.
    monkeypatch.setattr(settings, "whatsapp_group_allowlist", "222-2@g.us")
    results = run(whatsapp.post("hello again", permitted(ANNOUNCE, YEAR2), by=3))
    assert [r["status"] for r in results] == ["skipped", "sent"]
    assert [s[0] for s in wa.sent] == [ANNOUNCE["jid"], "222-2@g.us"]
    assert [g["jid"] for g in run(whatsapp.available(user_id=3))] == ["222-2@g.us"]


# ----------------------------------------------------- 2. one post per email


def test_an_email_is_posted_once_per_group_however_often_it_is_fired(db, wa):
    first = run(whatsapp.post("on sale", permitted(ANNOUNCE, YEAR2), campaign_id=41, by=3))
    assert [r["status"] for r in first] == ["sent", "sent"]
    again = run(whatsapp.post("on sale", permitted(ANNOUNCE, YEAR2), campaign_id=41, by=3))
    assert [r["status"] for r in again] == ["skipped", "skipped"]
    assert len(wa.sent) == 2
    # Recorded before posting, finished after; the service key is the claim's id.
    assert [p["status"] for p in db.posts] == ["sent", "sent"]
    assert wa.sent[0][2] == "post:1"


# ------------------------------------------------------ 3. a double press


def test_pressing_post_twice_posts_once(db, wa):
    run(whatsapp.post("AGM tonight", permitted(ANNOUNCE), by=3))
    second = run(whatsapp.post("AGM tonight", permitted(ANNOUNCE), by=3))
    assert second[0]["status"] == "skipped"
    assert len(wa.sent) == 1
    # Different words are a different post.
    run(whatsapp.post("AGM moved to 403", permitted(ANNOUNCE), by=3))
    assert len(wa.sent) == 2


# ------------------------------------------------------- 4. failures


def test_a_refused_post_is_recorded_and_the_next_group_still_goes(db, wa):
    wa.refuse[ANNOUNCE["jid"]] = "20 posts in the last hour is the limit."
    results = run(whatsapp.post("hello", permitted(ANNOUNCE, YEAR2), campaign_id=5, by=3))
    assert [r["status"] for r in results] == ["failed", "sent"]
    assert db.posts[0]["status"] == "failed"
    assert "limit" in db.posts[0]["error"]
    # A failed post is not a reason to refuse the same words next time.
    wa.refuse.clear()
    assert run(whatsapp.post("hello", permitted(ANNOUNCE), by=3))[0]["status"] == "sent"


# ------------------------------------------- 5. a recipient like any other


ROW = {
    "id": 9, "kind": "on_sale", "event_id": 7, "subject": "Tickets for Robot Wars are on sale",
    "heading": "", "body": "", "reviewed_by": 3, "created_by": 3,
    # A WhatsApp group is ticked in the same list as the email audiences.
    "audiences": ["topic:tickets_on_sale", f"whatsapp:{YEAR2['jid']}"],
}


def test_whatsapp_groups_are_audience_keys_that_email_skips(monkeypatch):
    emailing = importlib.import_module("services.emailing")
    keys = ["members", "whatsapp:111-1@g.us", "test", "whatsapp:111-1@g.us", "whatsapp:222-2@g.us"]
    assert emailing.whatsapp_groups_in(keys) == ["111-1@g.us", "222-2@g.us"]
    assert emailing.email_keys(keys) == ["members", "test"]

    async def must_not_be_asked(*a, **k):
        raise AssertionError("a WhatsApp group was resolved as email")

    monkeypatch.setattr(database, "get", must_not_be_asked)
    assert run(emailing.resolve(["whatsapp:111-1@g.us"])) == []


def test_every_group_the_number_can_post_in_is_offered_live(monkeypatch, db, wa):
    emailing = importlib.import_module("services.emailing")

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(emailing, "resolve", nothing)
    monkeypatch.setattr(emailing, "topics", nothing)
    monkeypatch.setattr(emailing, "test_recipients", nothing)
    offered = [a for a in run(emailing.audiences(whatsapp_user_id=3)) if a["group"] == "WhatsApp"]
    # The community's channel first, named as what it is; enabled by group ACL;
    # nothing the number can't post in.
    assert [(a["key"], a["label"]) for a in offered] == [
        ("whatsapp:111-1@g.us", "Announcements — EESoc"),
        ("whatsapp:222-2@g.us", "EEE Year 2"),
    ]
    assert all(a["whatsapp"] for a in offered)

    # Not linked or not answering: no WhatsApp groups offered, and no error.
    wa.down = True
    assert [a for a in run(emailing.audiences(whatsapp_user_id=3)) if a["group"] == "WhatsApp"] == []


def test_an_email_posts_only_into_the_groups_ticked_among_its_audiences(db, wa):
    # No WhatsApp group ticked: nothing posted.
    assert run(whatsapp.post_campaign({**ROW, "audiences": ["topic:tickets_on_sale"]})) == []
    assert wa.sent == []

    results = run(whatsapp.post_campaign(ROW))
    assert [(r["name"], r["status"]) for r in results] == [("EEE Year 2", "sent")]
    text = wa.sent[0][1]
    assert text.startswith("*Tickets for Robot Wars are on sale*\n\nTickets for *Robot Wars* are on sale now.")
    assert "*Where:* EEE 403" in text
    assert all(p["campaign_id"] == 9 and p["created_by"] == 3 for p in db.posts)


def test_a_group_the_number_has_left_since_is_left_out(db, wa):
    wa.groups_list = [g for g in wa.groups_list if g["jid"] != YEAR2["jid"]]
    row = {**ROW, "id": 10, "audiences": [f"whatsapp:{YEAR2['jid']}", f"whatsapp:{ANNOUNCE['jid']}"]}
    results = run(whatsapp.post_campaign(row))
    assert [r["name"] for r in results] == ["Announcements — EESoc"]
    assert [s[0] for s in wa.sent] == [ANNOUNCE["jid"]]


def test_a_queued_email_while_whatsapp_is_down_posts_nothing_and_does_not_raise(db, wa):
    wa.down = True
    assert run(whatsapp.post_campaign(ROW)) == []


def _queue_fakes(monkeypatch, recipients):
    started, patched = [], []

    async def fake_recipients(row):
        return list(recipients), []

    async def fake_builder(row, **kwargs):
        return None

    async def fake_post(path, json=None, params=None):
        return {**ROW, "status": "sending"}

    async def fake_patch(path, json=None, params=None):
        patched.append((path, params))
        return {}

    monkeypatch.setattr(campaigns, "recipients_for", fake_recipients)
    monkeypatch.setattr(campaigns, "builder_for", fake_builder)
    monkeypatch.setattr(campaigns.emailing, "send_bulk", lambda *a, **k: ("email", a[0]))
    monkeypatch.setattr(campaigns.mailer, "configured", lambda *a, **k: True)
    monkeypatch.setattr(database, "post", fake_post)
    monkeypatch.setattr(database, "patch", fake_patch)
    monkeypatch.setattr(whatsapp, "post_campaign", lambda row: ("whatsapp", row["id"], row["reviewed_by"]))
    monkeypatch.setattr(campaigns, "dispatch", started.append)
    return started, patched


def test_a_queued_email_posts_into_its_groups_as_it_sends(monkeypatch):
    started, _ = _queue_fakes(monkeypatch, [{"email": "a@example.com"}])
    run(campaigns.start_send({**ROW, "audiences": ["topic:tickets_on_sale"]}, expect="scheduled", reviewed_by=3))
    assert started == [("email", 9)]
    started.clear()
    run(campaigns.start_send(ROW, expect="scheduled", reviewed_by=4))
    assert started == [("email", 9), ("whatsapp", 9, 4)]


def test_a_queued_email_for_whatsapp_only_posts_and_is_finished(monkeypatch):
    started, patched = _queue_fakes(monkeypatch, [])
    run(campaigns.start_send({**ROW, "audiences": [f"whatsapp:{YEAR2['jid']}"]}, expect="scheduled", reviewed_by=4))
    # Nobody to mail: no bulk send, the record finished, the group posted.
    assert started == [("whatsapp", 9, 4)]
    assert patched == [("/email/campaigns/9", {"status": "sent"})]

    # Neither email nor WhatsApp: refused, as ever.
    with pytest.raises(campaigns.NotSendable):
        run(campaigns.start_send({**ROW, "audiences": []}, expect="scheduled", reviewed_by=4))


# --------------------------------------------------------- 6. the routes


def test_the_pickers_get_the_live_groups_and_the_environment(monkeypatch, db, wa):
    routes = importlib.import_module("routes.whatsapp")
    monkeypatch.setattr(settings, "environment", "staging")
    got = run(routes.targets(_=session_of()))
    assert got["configured"] and got["environment"] == "staging"
    assert [g["name"] for g in got["groups"]] == ["Announcements — EESoc", "EEE Year 2"]


def test_a_newsletter_send_mails_its_audiences_and_posts_into_its_groups(monkeypatch, db, wa):
    admin = importlib.import_module("routes.admin")
    dispatched = []
    monkeypatch.setattr(admin, "_dispatch", dispatched.append)

    # WhatsApp only: nothing to mail, so no mailbox needed and no campaign.
    res = run(admin.send_newsletter(
        subject="AGM tonight", body="6pm.", heading=None,
        audiences=[f"whatsapp:{YEAR2['jid']}"], session=session_of(),
    ))
    assert res["recipients"] == 0 and res["campaignId"] is None
    assert "Posting into EEE Year 2" in res["message"]
    run(dispatched.pop())
    assert wa.sent == [(YEAR2["jid"], "*AGM tonight*\n\n6pm.", "post:1")]

    # A group the number can't post in: refused, not quietly skipped.
    with pytest.raises(HTTPException) as refused:
        run(admin.send_newsletter(
            subject="AGM", body="x", heading=None,
            audiences=["whatsapp:333-3@g.us"], session=session_of(),
        ))
    assert refused.value.status_code == 400

    # WhatsApp down: said so, rather than posting nowhere.
    wa.down = True
    with pytest.raises(HTTPException) as down:
        run(admin.send_newsletter(
            subject="AGM", body="x", heading=None,
            audiences=[f"whatsapp:{YEAR2['jid']}"], session=session_of(),
        ))
    assert down.value.status_code == 503

    # Nothing ticked at all.
    with pytest.raises(HTTPException):
        run(admin.send_newsletter(subject="AGM", body="x", heading=None, audiences=[], session=session_of()))


def test_combined_send_requires_both_communication_permissions(monkeypatch, db, wa):
    admin = importlib.import_module("routes.admin")
    for session, audiences, description in (
        (session_of(permissions={"email"}), [f"whatsapp:{YEAR2['jid']}"], "WhatsApp"),
        (session_of(permissions={"whatsapp"}), ["members"], "email"),
    ):
        with pytest.raises(HTTPException) as exc:
            run(admin.send_newsletter(
                subject="Hello", body="Hello", heading=None,
                audiences=audiences, session=session,
            ))
        assert exc.value.status_code == 403
        assert description.lower() in exc.value.detail.lower()
    assert not wa.sent
    assert not db.posts


def test_a_subject_is_the_emails_not_whatsapps(monkeypatch, db, wa):
    """A WhatsApp post has no subject line, so WhatsApp alone needs only
    words; the email still needs its subject."""
    admin = importlib.import_module("routes.admin")
    dispatched = []
    monkeypatch.setattr(admin, "_dispatch", dispatched.append)

    res = run(admin.send_newsletter(
        subject="", body="Pizza in **403** now.", heading=None,
        audiences=[f"whatsapp:{YEAR2['jid']}"], session=session_of(),
    ))
    assert res["ok"]
    run(dispatched.pop())
    assert wa.sent == [(YEAR2["jid"], "Pizza in *403* now.", "post:1")]

    with pytest.raises(HTTPException) as nothing:
        run(admin.send_newsletter(
            subject="", body="  ", heading=None,
            audiences=[f"whatsapp:{YEAR2['jid']}"], session=session_of(),
        ))
    assert nothing.value.status_code == 400

    monkeypatch.setattr(admin.mailer, "configured", lambda *a, **k: True)

    async def someone(keys, **kwargs):
        return [{"email": "a@example.com"}]

    monkeypatch.setattr(admin.emailing, "resolve", someone)
    with pytest.raises(HTTPException) as no_subject:
        run(admin.send_newsletter(
            subject="", body="Pizza.", heading=None,
            audiences=["members", f"whatsapp:{YEAR2['jid']}"], session=session_of(),
        ))
    assert no_subject.value.status_code == 400
    assert "subject" in no_subject.value.detail


def _email_side(monkeypatch, admin, db):
    """Everything an email send touches besides WhatsApp, recorded not sent."""
    calls = []
    real_get, real_post, real_patch = db.get, db.post, db.patch

    async def get(path, params=None):
        if path == '/external-societies/whatsapp/groups/settings':
            return [{'account_key':'eesoc','jid':jid,'society_id':None,'enabled':True,
                     'eesoc_enabled':True,'eesoc_user_ids':[3]}
                    for jid in ('111-1@g.us','222-2@g.us')]
        if path == "/sponsors/listings/unannounced":
            return [{"id": 1, "title": "Intern", "sponsor_name": "Arm", "location": "Cambridge",
                     "url": "https://arm.example/jobs/1", "deadline": None, "kind": "internship"}]
        return await real_get(path, params)

    async def post(path, json=None, params=None):
        if path == "/whatsapp/posts":
            return await real_post(path, json, params)
        calls.append(path)
        return {"id": 77} if path == "/email/campaigns" else {}

    async def patch(path, json=None, params=None):
        if path.startswith("/whatsapp/posts/"):
            return await real_patch(path, json, params)
        calls.append(path)
        return {}

    async def someone(keys, **kwargs):
        return [{"email": "a@example.com"}]

    monkeypatch.setattr(database, "get", get)
    monkeypatch.setattr(database, "post", post)
    monkeypatch.setattr(database, "patch", patch)
    monkeypatch.setattr(admin.emailing, "resolve", someone)
    monkeypatch.setattr(admin.mailer, "configured", lambda *a, **k: True)
    monkeypatch.setattr(admin.emailing, "send_bulk", lambda *a, **k: ("email", a[0]))
    return calls


def test_the_roles_digest_can_go_on_whatsapp_too(monkeypatch, db, wa):
    admin = importlib.import_module("routes.admin")
    dispatched = []
    monkeypatch.setattr(admin, "_dispatch", dispatched.append)
    _email_side(monkeypatch, admin, db)

    res = run(admin.send_roles_digest(whatsapp_groups=[YEAR2["jid"]], session=session_of()))
    assert "Posting into EEE Year 2" in res["message"]
    assert dispatched[0] == ("email", 77)
    run(dispatched[1])
    assert wa.sent[0][0] == YEAR2["jid"] and "Intern" in wa.sent[0][1]
    assert db.posts[0]["campaign_id"] == 77


def test_a_sponsors_marketing_can_go_on_whatsapp_even_when_asked_for_social_only(monkeypatch, db, wa):
    admin = importlib.import_module("routes.admin")
    dispatched = []
    monkeypatch.setattr(admin, "_dispatch", dispatched.append)
    calls = _email_side(monkeypatch, admin, db)
    item = {"id": 5, "status": "pending", "channels": ["social"], "subject": "Arm is hiring",
            "body": "Apply by **Friday**.", "media": ["/media/arm.pdf"], "audiences": ["members"],
            "sponsor_id": 2, "emailed_at": None}

    async def the_item(_id):
        return dict(item)

    async def the_sponsor(_id):
        return {"name": "Arm", "slug": "arm"}

    async def unchanged(it, _session):
        return it

    monkeypatch.setattr(admin, "_marketing_or_404", the_item)
    monkeypatch.setattr(admin, "_sponsor_or_404", the_sponsor)
    monkeypatch.setattr(admin, "_finish_marketing", unchanged)

    # Social only, nothing ticked: nothing to do, and it says what would.
    with pytest.raises(HTTPException) as nothing:
        run(admin.send_sponsor_marketing(5, whatsapp_groups=[], session=session_of()))
    assert "WhatsApp" in nothing.value.detail

    res = run(admin.send_sponsor_marketing(5, whatsapp_groups=[YEAR2["jid"]], session=session_of()))
    assert "Posting into EEE Year 2" in res["message"]
    assert calls == []  # no email: it wasn't asked for
    run(dispatched.pop())
    text = wa.sent[0][1]
    assert text.startswith("*Arm is hiring*\n\n_Shared on behalf of Arm, one of EESoc's sponsors._")
    assert "Apply by *Friday*." in text
    assert "PDF: " in text and text.rstrip().endswith("/media/arm.pdf")


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
