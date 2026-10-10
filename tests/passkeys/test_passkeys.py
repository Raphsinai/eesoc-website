"""Passkeys against the live test stack: saving one, signing in with it, and the
committee's setup links.

NEEDS THE COMPOSE TEST STACK (see tests/README.md), like the baseline suite.
Every ceremony is real — a software authenticator (passkey_authenticator.py)
signs with a P-256 key, and the api verifies with py_webauthn — so a pass here
means the cryptography, the challenge bookkeeping and the access rules all
hold, not just that the endpoints answer.

Each test makes its own accounts with fresh addresses, so nothing depends on
order or on what an earlier run left in the database.
"""

import uuid

import pytest

from passkey_authenticator import Browser, SoftAuthenticator, token_from

# The standing superuser (SUPERUSER_EMAIL's default). On the test stack's api
# dev-login makes this address a superuser on every request.
SUPERUSER = "ras124@ic.ac.uk"


def _address(label: str) -> str:
    return f"passkey-{label}-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture()
def browser(api):
    """A fresh, signed-out browser on the api the SPA uses."""
    return Browser(str(api.base_url))


@pytest.fixture()
def new_browser(api):
    """Make as many separate browsers as a test needs."""
    return lambda: Browser(str(api.base_url))


def _signed_in(new_browser, email: str, make_admin: bool = False) -> Browser:
    b = new_browser()
    b.dev_login(email, make_admin=make_admin)
    return b


def _with_passkey(new_browser, label: str):
    """A signed-in account holding one passkey, and the device holding it."""
    email = _address(label)
    owner = _signed_in(new_browser, email)
    device = SoftAuthenticator()
    saved = owner.add_passkey(device)
    assert saved.status_code == 200, saved.text
    return email, owner, device


# ------------------------------------------------------------- your own passkey


def test_save_a_passkey_then_sign_in_with_it(new_browser):
    email = _address("roundtrip")
    owner = _signed_in(new_browser, email)
    assert owner.session()["passkeys"] == 0

    device = SoftAuthenticator()
    saved = owner.add_passkey(device, name="Chrome on macOS")
    assert saved.status_code == 200, saved.text
    assert saved.json()["passkey"]["name"] == "Chrome on macOS"
    assert owner.session()["passkeys"] == 1

    listing = owner.get("/auth/passkeys").json()["passkeys"]
    assert len(listing) == 1
    assert listing[0]["viaLink"] is False
    assert listing[0]["lastUsedAt"] is None
    # Nothing that could be used to sign in leaves the server.
    assert "public_key" not in listing[0] and "credential_id" not in listing[0]

    # A different browser, signed out, with no address typed: the passkey
    # alone says whose account this is.
    fresh = new_browser()
    signed_in = fresh.passkey_sign_in(device)
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["authenticated"] is True
    assert signed_in.json()["user"]["email"] == email
    assert fresh.session()["user"]["email"] == email

    used = owner.get("/auth/passkeys").json()["passkeys"][0]
    assert used["lastUsedAt"] is not None


def test_an_answer_works_once(new_browser):
    """The challenge is deleted as it is read, so replaying a captured answer
    fails even though its signature is perfectly good."""
    _, _, device = _with_passkey(new_browser, "replay")
    b = new_browser()
    options = b.post("/auth/passkey/options").json()
    answer = device.get(options)

    first = b.post("/auth/passkey/verify", {"credential": answer})
    assert first.status_code == 200, first.text

    replay = new_browser().post("/auth/passkey/verify", {"credential": answer})
    assert replay.status_code == 400


def test_a_challenge_we_never_issued_is_refused(new_browser):
    _, _, device = _with_passkey(new_browser, "forged")
    answer = device.get({"challenge": "bm90LWEtcmVhbC1jaGFsbGVuZ2U", "rpId": "localhost"})
    res = new_browser().post("/auth/passkey/verify", {"credential": answer})
    assert res.status_code == 400


def test_another_sites_origin_is_refused(new_browser):
    """A phishing page relaying our challenge signs it with its own origin in
    the client data — which is the whole of the protection passkeys give."""
    _, _, device = _with_passkey(new_browser, "origin")
    res = new_browser().passkey_sign_in(device, origin="https://eesoc-login.example")
    assert res.status_code == 400

    owner = _signed_in(new_browser, _address("origin-reg"))
    options = owner.post("/auth/passkeys/options").json()
    answer = SoftAuthenticator(origin="https://eesoc-login.example").create(options)
    assert owner.post("/auth/passkeys", {"credential": answer}).status_code == 400


def test_an_unknown_passkey_is_refused(new_browser):
    stranger = SoftAuthenticator()
    # Made against our options, but never saved anywhere.
    stranger.create({"rp": {"id": "localhost"}, "challenge": "eA", "user": {"id": "eA"}})
    res = new_browser().passkey_sign_in(stranger)
    assert res.status_code == 400
    assert "emailed code" in res.json()["detail"]


def test_a_counter_that_goes_backwards_is_refused(new_browser):
    """A hardware key counts up. Going backwards means a cloned key."""
    owner = _signed_in(new_browser, _address("clone"))
    device = SoftAuthenticator()
    options = owner.post("/auth/passkeys/options").json()
    answer = device.create(options, counter=1, synced=False)
    assert owner.post("/auth/passkeys", {"credential": answer}).status_code == 200

    assert new_browser().passkey_sign_in(device, counter=5).status_code == 200
    assert new_browser().passkey_sign_in(device, counter=3).status_code == 400


def test_a_removed_passkey_no_longer_signs_in(new_browser):
    _, owner, device = _with_passkey(new_browser, "removed")
    passkey_id = owner.get("/auth/passkeys").json()["passkeys"][0]["id"]

    assert owner.delete(f"/auth/passkeys/{passkey_id}").status_code == 200
    assert owner.session()["passkeys"] == 0
    assert new_browser().passkey_sign_in(device).status_code == 400


def test_nobody_can_remove_somebody_elses_passkey(new_browser):
    _, owner, _ = _with_passkey(new_browser, "victim")
    passkey_id = owner.get("/auth/passkeys").json()["passkeys"][0]["id"]

    other = _signed_in(new_browser, _address("other"))
    assert other.delete(f"/auth/passkeys/{passkey_id}").status_code == 404
    assert len(owner.get("/auth/passkeys").json()["passkeys"]) == 1


def test_a_registration_is_for_the_account_that_asked(new_browser):
    """Options issued to one account cannot be redeemed by another: the
    challenge carries the account it was made for."""
    alice = _signed_in(new_browser, _address("alice"))
    mallory = _signed_in(new_browser, _address("mallory"))

    options = alice.post("/auth/passkeys/options").json()
    answer = SoftAuthenticator().create(options)
    assert mallory.post("/auth/passkeys", {"credential": answer}).status_code == 400
    assert mallory.session()["passkeys"] == 0


def test_saving_a_passkey_needs_a_session(browser):
    assert browser.post("/auth/passkeys/options").status_code == 401


def test_not_while_impersonating(new_browser):
    """A passkey outlives the impersonation, so it would be a silent way back
    into the account. Refused."""
    target = _address("impersonated")
    _signed_in(new_browser, target)
    superuser = _signed_in(new_browser, SUPERUSER)
    assert superuser.post("/admin/impersonate", {"email": target}).status_code == 200

    assert superuser.post("/auth/passkeys/options").status_code == 403
    assert superuser.post("/admin/passkey-links", {"email": _address("x")}).status_code in (400, 403)


# ------------------------------------------------------------------- setup links


def _make_link(maker: Browser, email: str) -> dict:
    res = maker.post("/admin/passkey-links", {"email": email})
    assert res.status_code == 200, res.text
    return res.json()


def test_a_link_signs_somebody_in_with_no_code(new_browser):
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    sponsor = _address("sponsor")

    link = _make_link(admin, sponsor)
    assert link["email"] == sponsor
    assert link["neverSignedIn"] is True
    token = token_from(link["url"])

    visitor = new_browser()
    about = visitor.post("/auth/passkey-link", {"token": token})
    assert about.status_code == 200, about.text
    assert about.json()["email"] == sponsor

    device = SoftAuthenticator()
    done = visitor.redeem_link(token, device)
    assert done.status_code == 200, done.text
    assert done.json()["authenticated"] is True
    assert done.json()["user"]["email"] == sponsor
    assert done.json()["passkeys"] == 1

    mine = visitor.get("/auth/passkeys").json()["passkeys"]
    assert mine[0]["viaLink"] is True

    # From now on the passkey is how they get in.
    again = new_browser().passkey_sign_in(device)
    assert again.status_code == 200, again.text
    assert again.json()["user"]["email"] == sponsor

    # And the link is spent.
    assert new_browser().post("/auth/passkey-link", {"token": token}).status_code == 404
    listed = {l["id"]: l for l in admin.get("/admin/passkey-links").json()["links"]}
    assert listed[link["id"]]["status"] == "used"


def test_a_link_works_once_even_racing(new_browser):
    """Two tabs both fetch options from the same link; only the first answer
    saves a passkey."""
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    token = token_from(_make_link(admin, _address("twice"))["url"])

    a, b = new_browser(), new_browser()
    options_a = a.post("/auth/passkey-link/options", {"token": token}).json()
    options_b = b.post("/auth/passkey-link/options", {"token": token}).json()
    answer_a = SoftAuthenticator().create(options_a)
    answer_b = SoftAuthenticator().create(options_b)

    first = a.post("/auth/passkey-link/register", {"token": token, "credential": answer_a})
    second = b.post("/auth/passkey-link/register", {"token": token, "credential": answer_b})
    assert first.status_code == 200, first.text
    assert second.status_code in (404, 410)


def test_an_answer_from_one_link_cannot_spend_another(new_browser):
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    token_1 = token_from(_make_link(admin, _address("one"))["url"])
    token_2 = token_from(_make_link(admin, _address("two"))["url"])

    b = new_browser()
    options = b.post("/auth/passkey-link/options", {"token": token_1}).json()
    answer = SoftAuthenticator().create(options)
    res = b.post("/auth/passkey-link/register", {"token": token_2, "credential": answer})
    assert res.status_code == 400


def test_revoked_and_replaced_links_stop_working(new_browser):
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    email = _address("revoked")

    first = _make_link(admin, email)
    second = _make_link(admin, email)
    # A fresh link replaces the live one: never two working links in circulation.
    assert new_browser().post(
        "/auth/passkey-link", {"token": token_from(first["url"])}
    ).status_code == 404

    assert admin.delete(f"/admin/passkey-links/{second['id']}").status_code == 200
    assert new_browser().post(
        "/auth/passkey-link", {"token": token_from(second["url"])}
    ).status_code == 404


def test_only_admins_and_superusers_make_links(new_browser):
    superuser = _signed_in(new_browser, SUPERUSER)
    officer_email = _address("events-officer")
    _signed_in(new_browser, officer_email)
    granted = superuser.post("/admin/admins", {"email": officer_email, "role": "events"})
    assert granted.status_code == 200, granted.text

    officer = _signed_in(new_browser, officer_email)
    assert officer.post("/admin/passkey-links", {"email": _address("x")}).status_code == 403
    assert officer.get("/admin/passkey-links").status_code == 403

    member = _signed_in(new_browser, _address("plain"))
    assert member.post("/admin/passkey-links", {"email": _address("y")}).status_code == 403

    session = officer.session()
    assert "passkey_links" not in session["capabilities"]
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    assert "passkey_links" in admin.session()["capabilities"]


def test_committee_accounts_need_a_superuser(new_browser):
    """Otherwise an admin could link a superuser's account and walk into it."""
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    other_admin = _address("other-admin")
    _signed_in(new_browser, other_admin, make_admin=True)

    refused = admin.post("/admin/passkey-links", {"email": other_admin})
    assert refused.status_code == 403

    superuser = _signed_in(new_browser, SUPERUSER)
    assert superuser.post("/admin/passkey-links", {"email": other_admin}).status_code == 200


def test_the_standing_superuser_takes_no_links(new_browser):
    superuser = _signed_in(new_browser, SUPERUSER)
    assert superuser.post("/admin/passkey-links", {"email": SUPERUSER}).status_code == 400
    admin = _signed_in(new_browser, _address("admin"), make_admin=True)
    assert admin.post("/admin/passkey-links", {"email": SUPERUSER}).status_code == 400


def test_a_link_is_honoured_only_while_its_maker_could_make_it(new_browser):
    """Checked again when the link is used, which closes the slow way round:
    link an ordinary account, then have it given a committee role."""
    superuser = _signed_in(new_browser, SUPERUSER)
    admin_email = _address("admin")
    admin = _signed_in(new_browser, admin_email, make_admin=True)

    # 1. The account is promoted after the link is made.
    promoted = _address("promoted")
    link = _make_link(admin, promoted)
    assert superuser.post("/admin/admins", {"email": promoted, "role": "events"}).status_code == 200
    assert new_browser().post(
        "/auth/passkey-link", {"token": token_from(link["url"])}
    ).status_code == 403

    # 2. The maker loses their role after making it.
    plain = _address("plain")
    link = _make_link(admin, plain)
    admin_id = admin.session()["user"]["id"]
    assert superuser.delete(f"/admin/admins/{admin_id}").status_code == 200
    assert new_browser().post(
        "/auth/passkey-link", {"token": token_from(link["url"])}
    ).status_code == 403


def test_link_endpoints_reject_rubbish(new_browser):
    b = new_browser()
    assert b.post("/auth/passkey-link", {"token": "x" * 43}).status_code == 404
    assert b.post("/auth/passkey-link", {"token": "short"}).status_code == 422
    assert b.post("/auth/passkey/verify", {"credential": {"id": "x"}}).status_code == 400
    assert b.post(
        "/auth/passkey/verify",
        {"credential": {"id": "x", "response": {"clientDataJSON": "!!!"}}},
    ).status_code == 400


def test_status_lists_passkeys_as_a_feature(api):
    features = api.get("/status").json()["features"]
    assert features["passkeys"]["available"] is True
