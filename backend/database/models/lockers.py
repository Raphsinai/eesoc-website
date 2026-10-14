"""Lockers and locker bookings."""

from datetime import datetime

from pydantic import BaseModel


class Locker(BaseModel):
    """One locker, placed by where it stands: level, wall, which block of nine
    counting from the left, and which door in that block (1–9, left to right,
    top row first). `number` is what is printed on the door, and is not unique —
    two blocks on the same wall can carry the same numbers."""

    id: int | None = None
    floor: int
    wall: str                           # front | side
    block: int
    slot: int
    number: int
    is_active: bool = True
    note: str | None = None


class LockerState(Locker):
    """A locker plus who holds it, for rendering the map. `mine` lets the UI
    distinguish the current user's locker from someone else's without leaking
    which user holds the others."""

    booking_id: int | None = None
    booked_by: int | None = None
    mine: bool = False
    state: str = "free"                 # free | taken | mine | inactive


class LockerBooking(BaseModel):
    id: int | None = None
    locker_id: int
    user_id: int
    academic_year: str
    order_number: str | None = None
    matched_sale_id: int | None = None
    created_at: datetime | None = None
    released_at: datetime | None = None


class LockerBookingDetail(LockerBooking):
    floor: int | None = None
    wall: str | None = None
    block: int | None = None
    slot: int | None = None
    number: int | None = None


class LockerBookingRequest(BaseModel):
    locker_id: int
    user_id: int
    academic_year: str
    order_number: str | None = None
    matched_sale_id: int | None = None


class LockerBookingResult(BaseModel):
    ok: bool
    reason: str | None = None           # taken | no_entitlement | not_member | ...
    booking: LockerBooking | None = None
