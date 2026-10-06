"""Events, ticket types, product links and reservations."""

from datetime import datetime

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


class TicketTypeAvailability(TicketType):
    """A ticket type with live demand folded in, so the UI never has to compute
    remaining stock itself (and cannot disagree with the booking endpoint)."""

    taken: int = 0
    remaining: int | None = None       # None = uncapped
    sold_out: bool = False


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
    capacity: int | None = None
    hold_hours: int = 48
    published: bool = False
    # When tickets go on sale. None = not announced yet, which the site
    # shows as "On sale soon" and refuses to book.
    sales_open_at: datetime | None = None
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
    academic_year: str
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    capacity: int | None = None
    hold_hours: int = 48
    published: bool = False
    # When tickets go on sale. None = not announced yet, which the site shows
    # as "On sale soon" and refuses to book. Must be on THIS model, not just
    # on Event: create and update both read it off the write payload.
    sales_open_at: datetime | None = None
    purchase_url: str | None = None
    signup_url: str | None = None
    sponsorship_claimable: bool = False
    requested_by_sponsor_id: int | None = None
    # Must be on the write model too, not just on Event: the api decides it
    # from the caller's role and sends it with every create and update.
    industry: bool = False
    academic_year: str | None = None
    ticket_types: list[TicketType] = []

    @field_validator("starts_at", "ends_at", "sales_open_at")
    @classmethod
    def _wall_clock(cls, value: datetime | None) -> datetime | None:
        """Event times are London wall-clock times, stored as typed (migration
        031). An offset on the way in is dropped, not converted: converting is
        exactly what used to move events by an hour."""
        return value.replace(tzinfo=None) if value is not None else None


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


class ReservationDetail(Reservation):
    event_title: str | None = None
    event_slug: str | None = None
    event_starts_at: datetime | None = None
    ticket_type_name: str | None = None
    price_pence: int = 0
    requires_purchase: bool = True
    members_only: bool = False
    has_member_record: bool = False
    user_email: str | None = None


class ReservationRequest(BaseModel):
    event_id: int
    ticket_type_id: int
    # None for a draft: the seat is held before we know who is taking it.
    user_id: int | None = None
    draft_token: str | None = None
    quantity: int = 1
    order_number: str | None = None
    booked_as_guest: bool = False
    # Guests hold for less time than a signed-in user. None = use the event's
    # own hold_hours.
    hold_hours_override: int | None = None


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
