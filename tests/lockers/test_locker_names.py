"""How a locker is named to a person (api/utils/lockers.py).

The number on a door is not unique — two blocks on level 4's side wall both
read 482–490 — so every name carries where the locker stands, in the words you
would use in front of it.
"""

import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "backend" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from utils.lockers import name, ordinal, place, row_col  # noqa: E402


@pytest.mark.parametrize("n, said", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (6, "6th"),
    (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"),
])
def test_ordinals(n, said):
    assert ordinal(n) == said


def test_doors_run_left_to_right_top_row_first():
    assert [row_col(slot) for slot in range(1, 10)] == [
        (1, 1), (1, 2), (1, 3),
        (2, 1), (2, 2), (2, 3),
        (3, 1), (3, 2), (3, 3),
    ]


def test_the_same_number_on_two_blocks_gets_two_names():
    third = {"floor": 4, "wall": "side", "block": 3, "slot": 1, "number": 482}
    fourth = {**third, "block": 4}
    assert place(third) == "Level 4, side wall, 3rd block from the left, top row"
    assert name(fourth) == "482 (level 4, side wall, 4th block from the left, top row)"
    assert name(third) != name(fourth)


def test_rows():
    door = {"floor": 3, "wall": "front", "block": 1, "number": 301}
    assert place({**door, "slot": 5}).endswith("middle row")
    assert place({**door, "slot": 9}).endswith("bottom row")
