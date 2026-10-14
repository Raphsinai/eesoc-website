"""The locker map against the live test stack.

NEEDS THE COMPOSE TEST STACK — runs in CI (see tests/README.md). Like the
baseline suites it does not guard on an env var.

The map is the lockers as they stand on levels 3–5 (migration 075): blocks of
nine, three by three, side by side from left to right along each wall. Door
numbers repeat — level 4's side wall has two blocks both reading 482–490 — so a
locker is booked by where it is, and named to the member that way.
"""

import httpx
import pytest

# Each wall's blocks, left to right, by the number on the top-left door.
LAYOUT = {
    (3, "front"): [301, 310, 319, 328, 337, 346],
    (4, "front"): [401, 410, 419, 428, 437, 446],
    (4, "side"): [464, 473, 482, 482, 373, 364],
    (5, "front"): [501, 510, 519, 528, 537, 546],
    (5, "side"): [555, 564, 582, 582],
}

# The one block whose doors do not run on from the first: it reads 374 twice.
ODD_BLOCK = (4, "side", 5)
ODD_NUMBERS = [373, 374, 374, 376, 377, 378, 379, 380, 381]


def _floors(client) -> list[dict]:
    response = client.get("/lockers")
    response.raise_for_status()
    return response.json()["floors"]


def _blocks(floors: list[dict]):
    for f in floors:
        for w in f["walls"]:
            for b in w["blocks"]:
                yield (f["floor"], w["wall"], b["block"]), b["lockers"]


def _sql(compose_exec, statement: str) -> str:
    return compose_exec(
        "postgres", "psql", "-U", "eesoc", "-d", "eesoc", "-v", "ON_ERROR_STOP=1",
        "-tA", "-c", statement,
    ).strip()


def test_the_map_is_the_lockers_on_the_walls(api):
    floors = _floors(api)
    assert [f["floor"] for f in floors] == [3, 4, 5]
    # Front wall before side wall; level 3 has no side wall.
    assert [[w["wall"] for w in f["walls"]] for f in floors] == [
        ["front"], ["front", "side"], ["front", "side"],
    ]

    walls = {}
    for (floor, wall, block), doors in _blocks(floors):
        walls.setdefault((floor, wall), []).append((block, doors[0]["number"]))
    assert {k: [n for _, n in v] for k, v in walls.items()} == LAYOUT
    assert all(
        [b for b, _ in v] == list(range(1, len(v) + 1)) for v in walls.values()
    ), "blocks are numbered from 1, left to right"

    assert sum(len(doors) for _, doors in _blocks(floors)) == 252


def test_every_block_is_three_by_three_and_runs_on_from_its_first_door(api):
    for key, doors in _blocks(_floors(api)):
        assert [(d["row"], d["col"]) for d in doors] == [
            (r, c) for r in (1, 2, 3) for c in (1, 2, 3)
        ], key
        first = doors[0]["number"]
        expected = ODD_NUMBERS if key == ODD_BLOCK else list(range(first, first + 9))
        assert [d["number"] for d in doors] == expected, key


def test_a_level_says_how_many_are_available(api):
    for f in _floors(api):
        total = sum(len(b["lockers"]) for w in f["walls"] for b in w["blocks"])
        free = sum(
            1 for w in f["walls"] for b in w["blocks"] for d in b["lockers"]
            if d["state"] == "free"
        )
        assert f["note"] == f"{free} of {total} available"


@pytest.fixture()
def locker_member(member_client, admin_client, compose_exec):
    """member1, holding nothing, with one free locker granted for the year.

    A grant rather than a Union purchase: it is the committee's own route to a
    locker and needs nothing faked in the sales feed. Undone afterwards, along
    with anything the test booked.
    """
    user_id = member_client.get("/auth/session").json()["user"]["id"]
    shortcode = _sql(compose_exec, f"SELECT shortcode FROM public.users WHERE id = {user_id};")
    assert shortcode, "member1 should carry its ICU login as a shortcode"

    def release_all():
        for b in member_client.get("/lockers/me").json()["bookings"]:
            member_client.post(f"/lockers/bookings/{b['id']}/release").raise_for_status()

    release_all()
    admin_client.put(
        f"/admin/locker-grants/{shortcode}", json={"free_count": 1}
    ).raise_for_status()
    try:
        yield member_client
    finally:
        release_all()
        admin_client.delete(f"/admin/locker-grants/{shortcode}").raise_for_status()


def test_a_repeated_number_is_booked_by_where_it_is(locker_member):
    blocks = dict(_blocks(_floors(locker_member)))
    third = blocks[(4, "side", 3)][0]
    fourth = blocks[(4, "side", 4)][0]
    assert third["number"] == fourth["number"] == 482
    assert third["id"] != fourth["id"]
    assert "3rd block from the left" in third["place"]
    assert "4th block from the left" in fourth["place"]

    booked = locker_member.post(f"/lockers/{fourth['id']}/book")
    assert booked.status_code == 200, booked.text

    blocks = dict(_blocks(_floors(locker_member)))
    assert blocks[(4, "side", 4)][0]["state"] == "mine"
    assert blocks[(4, "side", 3)][0]["state"] == "free", "only the one booked is taken"

    [booking] = locker_member.get("/lockers/me").json()["bookings"]
    assert booking["lockerId"] == fourth["id"]
    assert booking["locker"] == "482 (level 4, side wall, 4th block from the left, top row)"


def test_someone_elses_locker_shows_as_taken(locker_member, api):
    door = dict(_blocks(_floors(locker_member)))[(3, "front", 1)][0]
    assert locker_member.post(f"/lockers/{door['id']}/book").status_code == 200

    # A fresh client: the shared `api` one keeps the cookies of every dev-login
    # the fixtures made through it, and could be signed in as member1.
    with httpx.Client(base_url=str(api.base_url), timeout=10.0) as stranger:
        seen = dict(_blocks(_floors(stranger)))[(3, "front", 1)][0]
    assert seen["state"] == "taken"
    assert seen["mine"] is False
