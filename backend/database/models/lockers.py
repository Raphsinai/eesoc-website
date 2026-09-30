"""Lockers and locker bookings."""

from datetime import datetime

from pydantic import BaseModel


class Locker(BaseModel):
    id: int | None = None
    bank: str
    number: int
    floor: int | None = None
    is_active: bool = True
    note: str | None = None


class LockerState(Locker):
    """A locker plus who holds it, for rendering the bank map. `mine` lets the UI
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
    bank: str | None = None
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
