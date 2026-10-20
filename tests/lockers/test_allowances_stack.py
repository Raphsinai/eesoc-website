"""Free locker allowances against the live test stack.

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var.

An allowance gives somebody free lockers for the academic year, on top of any
they buy. It is giving away what is otherwise paid for, so only a superuser may
make one (the `locker_allowances` capability in api/utils/roles.py) — an admin,
one rung lower, is refused.
"""

import uuid

import pytest

# member1 in the union stub's roster (tests/union_stub/fixtures.py): an ICU
# member whose login is ab1234. "member1" is not shaped like a shortcode, so
# the address can only be resolved through the members feed.
MEMBER_EMAIL = "member1@ic.ac.uk"
MEMBER_SHORTCODE = "ab1234"
DENIED = "You cannot grant free locker allowances."


def _mine(client, shortcode: str) -> dict | None:
    response = client.get("/admin/locker-grants")
    response.raise_for_status()
    return next(
        (a for a in response.json()["allowances"] if a["shortcode"] == shortcode), None
    )


@pytest.fixture()
def cleanup(superuser_client):
    """Shortcodes a test gave allowances to, taken away afterwards."""
    given: list[str] = []
    yield given
    for shortcode in given:
        superuser_client.delete(f"/admin/locker-grants/{shortcode}")


def test_only_a_superuser_holds_the_capability(superuser_client, admin_client):
    assert "locker_allowances" in superuser_client.get("/auth/session").json()["capabilities"]
    assert "locker_allowances" not in admin_client.get("/auth/session").json()["capabilities"]


def test_an_admin_is_refused(admin_client):
    for response in (
        admin_client.get("/admin/locker-grants"),
        admin_client.put(f"/admin/locker-grants/{MEMBER_SHORTCODE}", json={"free_count": 1}),
        admin_client.delete(f"/admin/locker-grants/{MEMBER_SHORTCODE}"),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == DENIED


def test_an_address_is_resolved_to_the_shortcode(superuser_client, member_client, cleanup):
    """Given by email, the allowance lands on the member's shortcode and counts
    towards what they may book."""
    cleanup.append(MEMBER_SHORTCODE)
    made = superuser_client.put(
        f"/admin/locker-grants/{MEMBER_EMAIL}",
        json={"free_count": 2, "note": "  Committee — Treasurer  "},
    )
    assert made.status_code == 200, made.text
    assert made.json()["shortcode"] == MEMBER_SHORTCODE

    listed = _mine(superuser_client, MEMBER_SHORTCODE)
    assert listed is not None
    assert listed["free"] == 2
    assert listed["isMember"] is True
    assert listed["note"] == "Committee — Treasurer"
    assert listed["givenBy"] == "ras124@ic.ac.uk"

    entitlement = member_client.get("/lockers").json()["entitlement"]
    assert entitlement["granted"] == 2


def test_giving_again_replaces_the_number(superuser_client, cleanup):
    cleanup.append(MEMBER_SHORTCODE)
    for count in (3, 1):
        superuser_client.put(
            f"/admin/locker-grants/{MEMBER_SHORTCODE}", json={"free_count": count}
        ).raise_for_status()
    assert _mine(superuser_client, MEMBER_SHORTCODE)["free"] == 1


@pytest.mark.parametrize("count", [0, -1, 11])
def test_a_count_out_of_range_is_refused(superuser_client, count):
    response = superuser_client.put(
        f"/admin/locker-grants/{MEMBER_SHORTCODE}", json={"free_count": count}
    )
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("typed", ["not a shortcode", "nobody.atall99@imperial.ac.uk"])
def test_something_that_is_not_a_shortcode_is_refused(superuser_client, typed):
    """Stored, it would match nobody's account and never apply."""
    response = superuser_client.put(f"/admin/locker-grants/{typed}", json={"free_count": 1})
    assert response.status_code == 400, response.text
    assert "shortcode" in response.json()["detail"]


def test_a_shortcode_nobody_holds_is_kept_but_flagged(superuser_client, cleanup):
    """Allowances are made ahead of time, so an unknown shortcode is allowed —
    it may be somebody who has not joined yet — but the list says so."""
    shortcode = f"zz{uuid.uuid4().int % 100000}"
    cleanup.append(shortcode)
    superuser_client.put(
        f"/admin/locker-grants/{shortcode}", json={"free_count": 1}
    ).raise_for_status()
    listed = _mine(superuser_client, shortcode)
    assert listed["isMember"] is False
    assert listed["held"] == 0


def test_taking_it_away(superuser_client, member_client):
    superuser_client.put(
        f"/admin/locker-grants/{MEMBER_SHORTCODE}", json={"free_count": 1}
    ).raise_for_status()
    superuser_client.delete(f"/admin/locker-grants/{MEMBER_SHORTCODE}").raise_for_status()
    assert _mine(superuser_client, MEMBER_SHORTCODE) is None
    assert member_client.get("/lockers").json()["entitlement"]["granted"] == 0
