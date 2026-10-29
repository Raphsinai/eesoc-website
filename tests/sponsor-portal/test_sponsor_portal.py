"""The sponsor portal against the live test stack.

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var.

The portal is the one part of the site used by outside companies, several of
whom compete with each other, so most of this is about what one sponsor can
reach:

1. a sponsor cannot read or change another sponsor's portal by naming its id;
2. nothing committee-only reaches a sponsor — not the fee or the internal note
   on their own row, and not the agreement's wording, which is the committee's;
3. the dashboard has no members count;
4. the standard package's allowances are the committee's to set, a sponsor's
   own figure wins over them, and the API enforces whichever applies;
5. a clause the committee struck is refused, not merely hidden;
6. a role past its closing day leaves the public careers list and shows as
   closed in the portal;
7. an event offered for sponsorship may still be an unpublished idea: sponsors
   see it, marked provisional, and students see nothing of it anywhere;
8. the committee can record a use that happened outside the portal; it counts
   against the allowance and the sponsor sees it beside everything else that
   used the clause, so "used" is always a list they can read.
"""

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

LONDON = ZoneInfo("Europe/London")

# Words that open every clause of the agreement. If any portal response carries
# them, the wording has leaked back into the sponsor's side.
CLAUSE_WORDING = ("The Student Group shall", "The Sponsor shall")

PANELS = ("jobs", "calendar", "request", "marketing", "merch", "contacts", "messages")


def _signed_in(api: httpx.Client, email: str, make_admin: bool = False) -> httpx.Client:
    """A client signed in through dev-login as `email` (the same cookie-header
    shape as conftest's fixtures)."""
    res = api.post("/auth/dev-login", json={"email": email, "make_admin": make_admin})
    res.raise_for_status()
    return httpx.Client(
        base_url=str(api.base_url),
        timeout=10.0,
        headers={
            "Cookie": f"session={res.cookies.get('session')}; csrf={res.cookies.get('csrf')}",
            "X-CSRF-Token": res.json()["csrf_token"],
        },
    )


@pytest.fixture()
def make_sponsor(api, admin_client):
    """Create a sponsor with one portal account; returns (sponsor, client).

    Deleted afterwards, which takes its listings, terms, marketing and notes
    with it (every child table cascades).
    """
    made = []

    def _make(*, active=False, **fields):
        tag = uuid.uuid4().hex[:10]
        created = admin_client.post(
            "/admin/sponsors",
            json={"slug": f"portal-{tag}", "name": f"Portal Test {tag}", "active": active, **fields},
        )
        assert created.status_code in (200, 201), created.text
        sponsor = created.json()
        made.append(sponsor["id"])
        email = f"portal-{tag}@example.com"
        granted = admin_client.post(f"/admin/sponsors/{sponsor['id']}/users", json={"email": email})
        assert granted.status_code == 200, granted.text
        return sponsor, _signed_in(api, email)

    yield _make
    for sponsor_id in made:
        admin_client.delete(f"/admin/sponsors/{sponsor_id}")


@pytest.fixture()
def standard_package(admin_client):
    """The standard package as it is now, put back after the test — it is
    shared by every sponsor, so a test that changes it must not leave it
    changed for the next."""
    before = admin_client.get("/admin/sponsors").json()["package"]
    yield {c["key"]: c for c in before}
    for c in before:
        admin_client.put(f"/admin/sponsor-package/{c['key']}", json={"allowance": c["allowance"]})


def _share(client: httpx.Client, subject: str = "Hiring now") -> httpx.Response:
    return client.post(
        "/portal/marketing",
        json={"subject": subject, "body": "Apply by Friday.", "audiences": ["members"], "channels": ["email"]},
    )


def _london_today():
    return datetime.now(LONDON).date()


# ---------------------------------------------------------------- 1. isolation


def test_portal_is_sponsors_only(member_client):
    assert member_client.get("/portal/dashboard").status_code == 403


def test_committee_portal_picker_returns_only_names_and_ids(make_sponsor, admin_client, member_client):
    sponsor, sponsor_client = make_sponsor()
    response = admin_client.get('/portal/sponsors')
    assert response.status_code == 200
    matches = [row for row in response.json()['sponsors'] if row['id'] == sponsor['id']]
    assert matches == [{'id': sponsor['id'], 'name': sponsor['name']}]
    assert all(set(row) == {'id', 'name'} for row in response.json()['sponsors'])
    # Members and sponsor users cannot enumerate the society's draft sponsors.
    assert member_client.get('/portal/sponsors').status_code == 403
    assert sponsor_client.get('/portal/sponsors').status_code == 403


def test_a_sponsor_reaches_only_its_own_portal(make_sponsor):
    mine, me = make_sponsor()
    theirs, them = make_sponsor()

    # Unscoped means "my own".
    dash = me.get("/portal/dashboard")
    assert dash.status_code == 200, dash.text
    assert dash.json()["sponsor"]["id"] == mine["id"]

    # Naming a competitor's id is refused, for reads and writes alike.
    assert me.get("/portal/dashboard", params={"sponsor_id": theirs["id"]}).status_code == 403
    assert me.get("/portal/panels/jobs", params={"sponsor_id": theirs["id"]}).status_code == 403
    assert me.get("/portal/profile", params={"sponsor_id": theirs["id"]}).status_code == 403
    assert me.put(
        "/portal/profile", params={"sponsor_id": theirs["id"]}, json={"name": "Hijacked"}
    ).status_code == 403

    # A listing id alone must not be enough to edit somebody else's role.
    posted = them.post("/portal/jobs", json={"title": "Their role", "status": "live"})
    assert posted.status_code == 200, posted.text
    listing = posted.json()["listing"]["id"]
    edit = me.put(f"/portal/jobs/{listing}", json={"title": "Taken over", "status": "closed"})
    assert edit.status_code == 404
    rows = them.get("/portal/panels/jobs").json()["rows"]
    assert [r["title"] for r in rows] == ["Their role"]

    # Nor a marketing request's id to withdraw it.
    share = _share(them)
    assert share.status_code == 200, share.text
    item = share.json()["item"]["id"]
    assert me.post(f"/portal/marketing/{item}/withdraw").status_code == 404

    # Nor a request's id to read its thread back out.
    req = them.post("/portal/requests", json={"format": "visit", "body": "Come to our lab."})
    assert req.status_code == 200, req.text
    rid = req.json()["request"]["id"]
    assert me.get("/portal/notes", params={"topic": f"request:{rid}"}).status_code == 404


def test_committee_must_say_which_sponsor_it_is_acting_for(make_sponsor, admin_client):
    sponsor, _ = make_sponsor()
    assert admin_client.get("/portal/dashboard").status_code == 400
    acting = admin_client.get("/portal/dashboard", params={"sponsor_id": sponsor["id"]})
    assert acting.status_code == 200, acting.text
    assert acting.json()["sponsor"]["name"] == sponsor["name"]


# --------------------------------------------------- 2. nothing committee-only


def test_saving_the_card_returns_no_committee_fields(make_sponsor):
    secret = f"renewal-talk-{uuid.uuid4().hex[:8]}"
    _, me = make_sponsor(internal_note=secret, amount_pence=400000)

    saved = me.put("/portal/profile", json={"one_line": "Chips, mostly."})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["one_line"] == "Chips, mostly."
    for response in (saved, me.get("/portal/profile")):
        assert secret not in response.text
        assert "internal_note" not in response.text
        assert "amount_pence" not in response.text


def test_the_portal_never_shows_the_agreement_wording(make_sponsor):
    _, me = make_sponsor()

    dash = me.get("/portal/dashboard")
    assert dash.status_code == 200, dash.text
    package = dash.json()["package"]
    # The clauses are still listed by name, with what is left of each.
    assert {"Sponsored workshop", "Marketing to members"} <= {t["label"] for t in package}
    assert all("text" not in t for t in package)

    for response in [dash] + [me.get(f"/portal/panels/{p}") for p in PANELS]:
        assert response.status_code == 200, response.text
        for words in CLAUSE_WORDING:
            assert words not in response.text, response.request.url


def test_the_committee_still_reads_the_wording(make_sponsor, admin_client):
    sponsor, _ = make_sponsor()
    detail = admin_client.get(f"/admin/sponsors/{sponsor['id']}/detail")
    assert detail.status_code == 200, detail.text
    terms = {t["key"]: t for t in detail.json()["terms"]}
    assert terms["workshop"]["text"].startswith("The Student Group shall")
    # As the agreement says it, not "one event".
    assert "naming rights on one flagship event" in terms["named_event"]["text"]


# ---------------------------------------------------------- 3. members count


def test_the_dashboard_has_no_members_count(make_sponsor):
    _, me = make_sponsor()
    labels = [s["label"] for s in me.get("/portal/dashboard").json()["stats"]]
    assert labels == ["Live listings", "Sponsorable events", "Marketing shares left"]


# --------------------------------------------------------- 4. allowances


def test_the_committee_sets_the_standard_package(make_sponsor, admin_client, standard_package):
    _, me = make_sponsor()
    current = standard_package["marketing"]["allowance"]
    new = current + 2

    set_ = admin_client.put("/admin/sponsor-package/marketing", json={"allowance": new})
    assert set_.status_code == 200, set_.text
    package = {c["key"]: c for c in set_.json()["package"]}
    assert package["marketing"]["allowance"] == new
    assert package["marketing"]["setByCommittee"] is True

    # A sponsor on the standard moves with it.
    panel = me.get("/portal/panels/marketing").json()
    assert (panel["allowance"], panel["remaining"]) == (new, new)


def test_the_standard_package_refuses_nonsense(admin_client):
    # Only countable clauses have an allowance, and none is negative.
    assert admin_client.put("/admin/sponsor-package/contact", json={"allowance": 2}).status_code == 404
    assert admin_client.put("/admin/sponsor-package/nonsense", json={"allowance": 2}).status_code == 404
    assert admin_client.put("/admin/sponsor-package/marketing", json={"allowance": -1}).status_code == 400


def test_the_standard_package_is_committee_only(make_sponsor, member_client):
    _, me = make_sponsor()
    for client in (me, member_client):
        assert client.put("/admin/sponsor-package/marketing", json={"allowance": 50}).status_code == 403


def test_a_sponsors_own_allowance_wins_and_is_enforced(make_sponsor, admin_client, standard_package):
    sponsor, me = make_sponsor()
    admin_client.put("/admin/sponsor-package/marketing", json={"allowance": 5})

    own = admin_client.put(
        f"/admin/sponsors/{sponsor['id']}/terms/marketing",
        json={"agreed": True, "allowance": 1, "note": ""},
    )
    assert own.status_code == 200, own.text
    term = next(t for t in own.json()["terms"] if t["key"] == "marketing")
    assert (term["allowance"], term["allowanceOverride"], term["standardAllowance"]) == (1, 1, 5)

    first = _share(me, "First")
    assert first.status_code == 200, first.text
    second = _share(me, "Second")
    assert second.status_code == 409
    assert "used all 1" in second.json()["detail"]

    # Withdrawing gives the share back.
    item = first.json()["item"]["id"]
    assert me.post(f"/portal/marketing/{item}/withdraw").status_code == 200
    assert _share(me, "Third").status_code == 200

    # Clearing their own figure puts them back on the standard.
    back = admin_client.put(
        f"/admin/sponsors/{sponsor['id']}/terms/marketing",
        json={"agreed": True, "allowance": None, "note": ""},
    )
    term = next(t for t in back.json()["terms"] if t["key"] == "marketing")
    assert (term["allowance"], term["allowanceOverride"]) == (5, None)


# ------------------------------------------------------- 5. struck clauses


def test_a_struck_clause_is_refused_not_just_hidden(make_sponsor, admin_client, api):
    sponsor, me = make_sponsor(active=True)
    saved = me.put(
        "/portal/contacts",
        json={"contacts": [{"kind": "email", "value": "grads@example.com", "label": "Send a CV"}]},
    )
    assert saved.status_code == 200, saved.text

    def public_card():
        cards = api.get("/sponsors").json()["sponsors"]
        return next(c for c in cards if c["id"] == sponsor["slug"])

    assert [c["value"] for c in public_card()["contacts"]] == ["grads@example.com"]

    struck = admin_client.put(
        f"/admin/sponsors/{sponsor['id']}/terms/contact",
        json={"agreed": False, "note": ""},
    )
    assert struck.status_code == 200, struck.text

    assert me.put("/portal/contacts", json={"contacts": []}).status_code == 403
    assert me.get("/portal/panels/contacts").json()["agreed"] is False
    # And the contact they had already saved stops being published.
    assert public_card()["contacts"] == []


# ------------------------------------------------------- 6. closing dates


def test_a_role_past_its_closing_day_leaves_the_site(make_sponsor, api):
    _, me = make_sponsor(active=True)
    today = _london_today()
    roles = {
        "Closed yesterday": today - timedelta(days=1),
        "Closes today": today,
        "Closes next week": today + timedelta(days=7),
    }
    for title, closes in roles.items():
        posted = me.post(
            "/portal/jobs",
            json={"title": title, "status": "live", "closes_on": closes.isoformat()},
        )
        assert posted.status_code == 200, posted.text
    me.post("/portal/jobs", json={"title": "No closing date", "status": "live"})

    public = {j["title"] for j in api.get("/jobs").json()["jobs"]}
    assert {"Closes today", "Closes next week", "No closing date"} <= public
    assert "Closed yesterday" not in public

    panel = me.get("/portal/panels/jobs").json()
    assert panel["heading"] == "3 roles live."
    rows = {r["title"]: r for r in panel["rows"]}
    assert rows["Closed yesterday"]["status"] == "Closed"
    assert rows["Closed yesterday"]["meta"].startswith("closed ")
    assert rows["Closes today"]["status"] == "Live"
    # The edit form still holds what the sponsor saved.
    assert rows["Closed yesterday"]["listing"]["status"] == "live"

    stats = {s["label"]: s for s in me.get("/portal/dashboard").json()["stats"]}
    assert stats["Live listings"]["value"] == "3"
    # Today and next week are inside the week; yesterday is not "closing".
    assert stats["Live listings"]["sub"] == "2 closing this week"


# ------------------------------------------------------ 7. sponsorable ideas

IDEA = "A hardware hackathon we want to run in the spring, with a sponsor setting the challenge."


@pytest.fixture()
def make_event(admin_client):
    """Create an event two months out; deleted afterwards."""
    made = []

    def _make(*, published: bool, sponsorable: bool = True):
        tag = uuid.uuid4().hex[:10]
        starts = (datetime.now(timezone.utc) + timedelta(days=60)).replace(microsecond=0)
        created = admin_client.post(
            "/admin/events",
            json={
                "slug": f"idea-{tag}",
                "title": f"Hackathon idea {tag}",
                "category": "Socials",
                "venue": "EEE 403",
                "description": IDEA,
                "starts_at": starts.isoformat(),
                "ends_at": (starts + timedelta(hours=3)).isoformat(),
                "published": published,
                "sponsorship_claimable": sponsorable,
            },
        )
        assert created.status_code in (200, 201), created.text
        made.append(created.json()["id"])
        return created.json()

    yield _make
    for event_id in made:
        admin_client.delete(f"/admin/events/{event_id}")


def _offered(client: httpx.Client) -> dict:
    return {r["eventId"]: r for r in client.get("/portal/dashboard").json()["sponsorable"]}


def test_an_unpublished_idea_is_offered_to_sponsors(make_sponsor, make_event):
    _, me = make_sponsor()
    idea = make_event(published=False)

    row = _offered(me)[idea["id"]]
    assert row["provisional"] is True
    # The month and no more: the date and room on a draft are placeholders.
    assert row["meta"].startswith("Provisionally ")
    assert row["meta"].endswith("details to be confirmed")
    assert "EEE 403" not in row["meta"]
    assert row["about"] == IDEA
    assert idea["id"] in {r["eventId"] for r in me.get("/portal/panels/calendar").json()["sponsorable"]}

    # They can ask about it and say they want it.
    assert me.get("/portal/notes", params={"topic": f"event:{idea['id']}"}).status_code == 200
    interest = me.post(f"/portal/events/{idea['id']}/interest")
    assert interest.status_code == 200, interest.text
    mine = {e["eventId"]: e for e in me.get("/portal/dashboard").json()["yourEvents"]}
    assert mine[idea["id"]]["provisional"] is True
    assert mine[idea["id"]]["statusLabel"] == "Interest registered"


def test_an_unpublished_idea_is_invisible_to_students(make_event, api, member_client):
    idea = make_event(published=False)
    # A fresh client: `api` keeps whatever cookies earlier dev-logins left it.
    anonymous = httpx.Client(base_url=str(api.base_url), timeout=10.0)

    for client in (anonymous, member_client):
        for path, params in [
            ("/events", None),
            ("/events", {"upcoming": "false", "limit": 500}),
            ("/events.ics", None),
            ("/sitemap.xml", None),
        ]:
            response = client.get(path, params=params)
            assert response.status_code == 200, (path, response.text)
            assert idea["slug"] not in response.text, path
            assert idea["title"] not in response.text, path
        # Search echoes the query back, so look at the results alone.
        found = client.get("/search", params={"q": idea["title"]})
        assert found.status_code == 200, found.text
        assert found.json()["results"] == []
        assert client.get(f"/events/{idea['slug']}").status_code == 404
        assert client.get(f"/events/{idea['slug']}/calendar.ics").status_code == 404


def test_a_draft_not_opened_to_sponsorship_stays_hidden_from_sponsors(make_sponsor, make_event):
    _, me = make_sponsor()
    draft = make_event(published=False, sponsorable=False)

    assert draft["id"] not in _offered(me)
    # Not even its title, through a thread or the interest button.
    assert me.get("/portal/notes", params={"topic": f"event:{draft['id']}"}).status_code == 404
    refused = me.post(f"/portal/events/{draft['id']}/interest")
    assert refused.status_code == 409
    assert draft["title"] not in refused.text


def test_a_published_sponsorable_event_shows_its_details(make_sponsor, make_event):
    _, me = make_sponsor()
    event = make_event(published=True)
    row = _offered(me)[event["id"]]
    assert row["provisional"] is False
    assert "EEE 403" in row["meta"]


# ------------------------------------------- 8. uses recorded by the committee


def _term(terms: list[dict], key: str) -> dict:
    return next(t for t in terms if t["key"] == key)


def _package(client: httpx.Client) -> dict:
    return {t["key"]: t for t in client.get("/portal/dashboard").json()["package"]}


def test_the_committee_records_a_use_done_outside_the_portal(make_sponsor, admin_client):
    sponsor, me = make_sponsor()
    url = f"/admin/sponsors/{sponsor['id']}/usage"

    def workshop_coverage():
        formats = {f["id"]: f for f in me.get("/portal/panels/request").json()["formats"]}
        return formats["workshop_talk"]["coverage"]

    # Workshop and seminar are one format: two left between them.
    assert workshop_coverage() == "In your package · 2 left this year"

    recorded = admin_client.post(
        url,
        json={
            "clause": "workshop",
            "title": "FPGA workshop at their office",
            "happened_on": _london_today().isoformat(),
        },
    )
    assert recorded.status_code == 200, recorded.text
    body = recorded.json()
    assert body["message"] == "Recorded against Sponsored workshop."
    assert _term(body["terms"], "workshop")["used"] == 1
    [use] = body["counted"]["workshop"]
    assert (use["kind"], use["title"]) == ("record", "FPGA workshop at their office")

    # The sponsor sees the use and where it came from, and the allowance spent.
    workshop = _package(me)["workshop"]
    assert workshop["status"] == "1 of 1 used"
    assert [(u["title"], u["recorded"]) for u in workshop["counted"]] == [
        ("FPGA workshop at their office", True)
    ]
    assert workshop_coverage() == "In your package · 1 left this year"

    # Removing it gives the use back, once.
    removed = admin_client.delete(f"{url}/{use['id']}")
    assert removed.status_code == 200, removed.text
    assert _term(removed.json()["terms"], "workshop")["used"] == 0
    assert _package(me)["workshop"]["counted"] == []
    assert admin_client.delete(f"{url}/{use['id']}").status_code == 404


def test_recorded_marketing_counts_against_the_allowance(make_sponsor, admin_client):
    sponsor, me = make_sponsor()
    admin_client.put(
        f"/admin/sponsors/{sponsor['id']}/terms/marketing",
        json={"agreed": True, "allowance": 2, "note": ""},
    )
    assert _share(me, "Through the portal").status_code == 200
    recorded = admin_client.post(
        f"/admin/sponsors/{sponsor['id']}/usage",
        json={"clause": "marketing", "title": "Instagram post about their internships"},
    )
    assert recorded.status_code == 200, recorded.text

    panel = me.get("/portal/panels/marketing").json()
    assert (panel["used"], panel["remaining"]) == (2, 0)
    assert [r["title"] for r in panel["recorded"]] == ["Instagram post about their internships"]
    # The recorded share spent the last one.
    assert _share(me, "One too many").status_code == 409

    # The count is the list: one through the portal, one by hand.
    counted = admin_client.get(f"/admin/sponsors/{sponsor['id']}/detail").json()["counted"]
    assert sorted(u["kind"] for u in counted["marketing"]) == ["marketing", "record"]
    assert _package(me)["marketing"]["status"] == "2 of 2 used"
    assert len(_package(me)["marketing"]["counted"]) == 2


def test_a_linked_event_is_listed_with_what_it_used(make_sponsor, make_event, admin_client):
    sponsor, me = make_sponsor()
    event = make_event(published=True)
    linked = admin_client.put(
        f"/admin/sponsors/{sponsor['id']}/events/{event['id']}", json={"clause": "seminar"}
    )
    assert linked.status_code == 200, linked.text

    seminar = _package(me)["seminar"]
    assert seminar["status"] == "1 of 1 used"
    assert [(u["title"], u["recorded"]) for u in seminar["counted"]] == [(event["title"], False)]


def test_a_use_from_an_earlier_year_does_not_spend_this_years(make_sponsor, admin_client):
    sponsor, me = make_sponsor()
    long_ago = _london_today() - timedelta(days=400)
    recorded = admin_client.post(
        f"/admin/sponsors/{sponsor['id']}/usage",
        json={"clause": "workshop", "title": "Last year's workshop", "happened_on": long_ago.isoformat()},
    )
    assert recorded.status_code == 200, recorded.text
    assert "does not count towards" in recorded.json()["message"]
    assert _term(recorded.json()["terms"], "workshop")["used"] == 0
    assert _package(me)["workshop"]["counted"] == []


def test_recording_uses_is_the_committees_and_is_checked(make_sponsor, admin_client, member_client):
    sponsor, me = make_sponsor()
    other, _ = make_sponsor()
    url = f"/admin/sponsors/{sponsor['id']}/usage"

    for client in (me, member_client):
        assert client.post(url, json={"clause": "workshop", "title": "Mine"}).status_code == 403
    # Only countable clauses are counted; a use needs saying what it was.
    assert admin_client.post(url, json={"clause": "contact", "title": "x"}).status_code == 404
    assert admin_client.post(url, json={"clause": "workshop", "title": "   "}).status_code == 400
    assert admin_client.post(
        url, json={"clause": "workshop", "title": "x", "happened_on": "soon"}
    ).status_code == 400

    made = admin_client.post(url, json={"clause": "workshop", "title": "Theirs"}).json()
    rid = made["counted"]["workshop"][0]["id"]
    # A record is removed only through the sponsor it belongs to, and only by
    # the committee.
    assert admin_client.delete(f"/admin/sponsors/{other['id']}/usage/{rid}").status_code == 404
    assert me.delete(f"{url}/{rid}").status_code == 403
    assert _term(admin_client.get(f"/admin/sponsors/{sponsor['id']}/detail").json()["terms"], "workshop")["used"] == 1
