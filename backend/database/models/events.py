"""Events, ticket types, product links and reservations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator


class TicketType(BaseModel):
    id: int | None = None
    event_id: int | None = None
    name: str
    description: str | None = None
    price_pence: int = 0
    capacity: int | None = None
    requires_purchase: bool = True
    members_only: bool = False
    max_per_user: int = 1
    sort_order: int = 100
    # Overrides the event-level link for this type, for the common case where
    # each ticket type is a separate Union product.
    purchase_url: str | None = None
    # This type's own sale window (080), inside the event's: early bird until
    # Friday, general from Friday. None = opens with the event / sells until
    # the event's booking closes.
    sales_open_at: datetime | None = None
    sales_close_at: datetime | None = None


class TicketTypeAvailability(TicketType):
    """A ticket type with live demand folded in, so the UI never has to compute
    remaining stock itself (and cannot disagree with the booking endpoint)."""

    taken: int = 0
    remaining: int | None = None       # None = uncapped
    sold_out: bool = False


def _normalise_target_years(value):
    """The database stores the year groups as the canonical comma string
    ("1,2"), while the admin form may send a list and the API always speaks a
    list of ints. Accept a comma-separated string, a list of ints, or None and
    normalise to a sorted list so the same event never round-trips into two
    different orders. An empty string means "aimed at everyone", as it does in
    the column itself."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    return sorted({int(item) for item in value})


class Event(BaseModel):
    id: int | None = None
    slug: str
    title: str
    category: str | None = None
    description: str = ""
    image_url: str | None = None
    venue: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    # When the doors shut, if the venue has a hard entry cutoff. None = no
    # cutoff. May sit before starts_at (doors close at 18:00 for an 18:30
    # talk), but never after ends_at — see the routes' validation.
    doors_close_at: datetime | None = None
    capacity: int | None = None
    # Standalone admin action: an event edit must never reset this setting.
    availability_badge: Literal["auto", "not_many_left", "selling_fast"] = "auto"
    hold_hours: int = 48
    published: bool = False
    # When tickets go on sale. None = not announced yet, which the site
    # shows as "On sale soon" and refuses to book.
    sales_open_at: datetime | None = None
    # The sale window's end (066). After `reservations_close_at` nobody can
    # reserve; no hold's pay-by deadline may pass `union_sales_close_at`.
    # Either None = no limit, as before.
    reservations_close_at: datetime | None = None
    union_sales_close_at: datetime | None = None
    # Set when the "tickets are on sale" emails went out. Read-only from the
    # admin form: it is the announcer's own bookkeeping, and letting an edit
    # clear it would re-announce the event.
    sales_announced_at: datetime | None = None
    # Where to buy, when the Union's product feed carries no URL of its own.
    purchase_url: str | None = None
    # An external sign-up form (often a sponsor's own). When set, the event
    # page links there instead of offering a booking.
    signup_url: str | None = None
    # Off by default: an event is only offered to sponsors when the committee
    # says so.
    sponsorship_claimable: bool = False
    requested_by_sponsor_id: int | None = None
    # An industry event — careers fair, sponsor talk, site visit. It is what
    # the industry role is allowed to edit, so it is a stored fact rather than
    # a reading of the free-text category.
    industry: bool = False
    # A map of the venue and the online-meeting link. The meeting link can be
    # private: a sponsor's Zoom link is often only for people who booked, so
    # the flag tells the public event payload to withhold it.
    map_url: str | None = None
    meeting_url: str | None = None
    meeting_url_private: bool = False
    # Which year groups this is aimed at; empty = everyone. The API speaks a
    # list of ints and the database route serialises to the canonical comma
    # string, so both forms are accepted here (see the validator below).
    target_years: list[int] = []
    academic_year: str
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Runs before field validation so a row's stored comma string ("1,2") and
    # an admin form's list both arrive as the same sorted list of ints.
    @field_validator("target_years", mode="before")
    @classmethod
    def _norm_target_years(cls, value):
        return _normalise_target_years(value)


class EventDetail(Event):
    ticket_types: list[TicketTypeAvailability] = []
    taken: int = 0
    remaining: int | None = None
    sold_out: bool = False


class EventWrite(BaseModel):
    """Admin create/update payload. Ticket types are sent with the event so a new
    event and its types are created in one transaction."""

    slug: str
    title: str
    category: str | None = None
    description: str = ""
    image_url: str | None = None
    venue: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    # Must be on the write model too, not just Event: the admin form sends it
    # with every create and update.
    doors_close_at: datetime | None = None
    capacity: int | None = None
    hold_hours: int = 48
    published: bool = False
    # When tickets go on sale. None = not announced yet, which the site shows
    # as "On sale soon" and refuses to book. Must be on THIS model, not just
    # on Event: create and update both read it off the write payload.
    sales_open_at: datetime | None = None
    # The sale window's end (066). After `reservations_close_at` nobody can
    # reserve; no hold's pay-by deadline may pass `union_sales_close_at`.
    # Either None = no limit, as before.
    reservations_close_at: datetime | None = None
    union_sales_close_at: datetime | None = None
    purchase_url: str | None = None
    signup_url: str | None = None
    sponsorship_claimable: bool = False
    requested_by_sponsor_id: int | None = None
    # Must be on the write model too, not just on Event: the api decides it
    # from the caller's role and sends it with every create and update.
    industry: bool = False
    # The calendar/link and year-group fields must be writable too, or the
    # admin form could never set them: the api layer forwards the whole body
    # and the database serialises these onto the row.
    map_url: str | None = None
    meeting_url: str | None = None
    meeting_url_private: bool = False
    # Sent as a list of ints by the form; a comma string is also accepted so
    # a client speaking the storage format keeps working.
    target_years: list[int] = []
    academic_year: str | None = None
    ticket_types: list[TicketType] = []

    @field_validator("target_years", mode="before")
    @classmethod
    def _norm_target_years(cls, value):
        return _normalise_target_years(value)



class ProductLink(BaseModel):
    id: int | None = None
    purpose: str                        # event_ticket | locker | membership
    event_id: int | None = None
    ticket_type_id: int | None = None
    union_product_id: int
    union_product_line_id: int | None = None
    academic_year: str
    created_by: int | None = None
    created_at: datetime | None = None


class Reservation(BaseModel):
    id: int | None = None
    event_id: int
    ticket_type_id: int
    user_id: int | None = None  # a guest draft hold has no user until claimed
    quantity: int = 1
    status: str = "held"
    created_at: datetime | None = None
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    order_number: str | None = None
    matched_sale_id: int | None = None
    note: str | None = None
    booked_as_guest: bool = False
    reminder_sent_at: datetime | None = None
    # 'booking' for the normal flow, 'committee' for a ticket added by hand.
    source: str = "booking"
    added_by: int | None = None
    # The "you're confirmed" email: see migration 059 for the states.
    confirmation_email: str | None = None
    confirmation_sent_at: datetime | None = None
    confirmation_attempts: int = 0


class CommitteeTicket(BaseModel):
    """A ticket a committee member adds for somebody, already confirmed."""
    event_id: int
    ticket_type_id: int
    user_id: int
    quantity: int = 1
    note: str | None = None
    added_by: int | None = None
    send_confirmation: bool = True
    booked_as_guest: bool = False


class ConfirmationResult(BaseModel):
    id: int
    outcome: str                    # sent | failed | suppressed


class ReservationDetail(Reservation):
    event_title: str | None = None
    event_slug: str | None = None
    event_starts_at: datetime | None = None
    # The doors-close time and venue travel with the event columns here, so
    # "My tickets" (section 15) can show them without a second query.
    event_doors_close_at: datetime | None = None
    event_venue: str | None = None
    ticket_type_name: str | None = None
    price_pence: int = 0
    requires_purchase: bool = True
    members_only: bool = False
    has_member_record: bool = False
    user_email: str | None = None
    user_first_name: str | None = None
    user_last_name: str | None = None
    user_shortcode: str | None = None


class ReservationRequest(BaseModel):
    event_id: int
    ticket_type_id: int
    # None for a draft: the seat is held before we know who is taking it.
    user_id: int | None = None
    draft_token: str | None = None
    quantity: int = 1
    order_number: str | None = None
    booked_as_guest: bool = False
    # SHA-256 of the browser's anonymous visitor cookie, when there is one.
    visitor_hash: str | None = None
    # Guests hold for less time than a signed-in user. None = use the event's
    # own hold_hours.
    hold_hours_override: int | None = None
    # The current academic year, so a free members-only ticket can tell
    # whether the person is a member *this* year. None reads as "not shown" —
    # the booking then waits, held, rather than being confirmed on a guess.
    academic_year: str | None = None


class ReservationResult(BaseModel):
    """`ok=False` carries a machine-readable reason so the API layer can map it
    to the right status code without string-matching an error message."""

    ok: bool
    reason: str | None = None           # sold_out | type_sold_out | over_user_limit | ...
    reservation: Reservation | None = None


class WaitlistEntry(BaseModel):
    id: int | None = None
    event_id: int
    user_id: int
    # Recorded so an offer can be steered at what they wanted, but never used to
    # order the queue — the waitlist is event-wide.
    preferred_ticket_type_id: int | None = None
    quantity: int = 1
    created_at: datetime | None = None
    offered_at: datetime | None = None
    offer_expires_at: datetime | None = None
    reservation_id: int | None = None
    claimed_at: datetime | None = None
    released_at: datetime | None = None
    notified_at: datetime | None = None


class WaitlistDetail(WaitlistEntry):
    email: str | None = None
    event_title: str | None = None
    event_slug: str | None = None
    position: int = 0
