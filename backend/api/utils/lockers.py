"""How a locker is named to a person.

The number on the door is not enough on its own: level 4's side wall has two
blocks both numbered 482–490, and level 5's two numbered 582–590. So wherever a
locker is named — the booking panel, an email, the committee's list of holders —
it says where the locker stands too, in the words you would use in front of it.
"""

ROWS = ("top", "middle", "bottom")


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def row_col(slot: int) -> tuple[int, int]:
    """A door's row and column in its block, both from 1. Doors are numbered
    1–9 left to right, top row first."""
    return (slot - 1) // 3 + 1, (slot - 1) % 3 + 1


def place(locker: dict) -> str:
    """'Level 4, side wall, 3rd block from the left, top row'."""
    row, _ = row_col(locker["slot"])
    return (
        f"Level {locker['floor']}, {locker['wall']} wall, "
        f"{ordinal(locker['block'])} block from the left, {ROWS[row - 1]} row"
    )


def name(locker: dict) -> str:
    """'482 (level 4, side wall, 3rd block from the left, top row)'."""
    where = place(locker)
    return f"{locker['number']} ({where[0].lower()}{where[1:]})"
