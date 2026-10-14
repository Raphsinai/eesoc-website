"""Subscriptions, per-event sale alerts and bulk-send records.

Field names match the SQL columns exactly, as everywhere else in the adapter,
so a Record becomes a model with `Model(**dict(row))`.
"""

from datetime import datetime

from pydantic import BaseModel


class OptOut(BaseModel):
    email: str
    topic: str = "*"
    created_at: datetime | None = None
    reason: str | None = None


class Subscription(BaseModel):
    user_id: int
    topic: str
    subscribed: bool = True
    updated_at: datetime | None = None


class SubscriptionWrite(BaseModel):
    """One topic's setting. Sent as a list by the preferences screen.

    `subscribed` is explicit rather than a toggle: the adapter must never have
    to know what the previous value was, so a replayed request is harmless.
    """

    topic: str
    subscribed: bool


class SaleAlert(BaseModel):
    id: int | None = None
    event_id: int
    email: str
    # Null when the address has no account yet — which is the ordinary case for
    # somebody who has only ever pressed "tell me when this is on sale".
    user_id: int | None = None
    token: str | None = None
    created_at: datetime | None = None
    notified_at: datetime | None = None
    cancelled_at: datetime | None = None


class SaleAlertDetail(SaleAlert):
    event_slug: str | None = None
    event_title: str | None = None
    event_starts_at: datetime | None = None
    sales_open_at: datetime | None = None


class Campaign(BaseModel):
    id: int | None = None
    subject: str
    heading: str | None = None
    body: str = ""
    audiences: list[str] = []
    status: str = "draft"
    recipients: int = 0
    sent: int = 0
    failed: int = 0
    # What the message is (newsletter, event announcement, on-sale) and which
    # event it was about. A send interrupted by a pod replacement can only be
    # finished by a *different* process, which never saw the closure that
    # built it — these two are how the stored row says which one to rebuild.
    kind: str = "newsletter"
    event_id: int | None = None
    error: str | None = None
    created_by: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # A draft's hand edits to who it goes to, on top of `audiences` (073).
    extra_recipients: list[str] = []
    excluded_recipients: list[str] = []
    # Who approved or dismissed it, and when; `status` says which.
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    # When it should go (074) — for an on-sale email, the event's sale time —
    # and when a person last edited it.
    send_at: datetime | None = None
    edited_at: datetime | None = None
    # Also post it into the chosen WhatsApp groups when it is sent (077).
    whatsapp: bool = False


class CampaignCreate(BaseModel):
    subject: str
    heading: str | None = None
    body: str = ""
    audiences: list[str] = []
    created_by: int | None = None
    # A draft for the approval queue is created `awaiting_approval` with its
    # kind and event already set, because nothing will send it to set them
    # later. Everything else is created `draft` and its sender sets both.
    status: str = "draft"
    kind: str = "newsletter"
    event_id: int | None = None
    send_at: datetime | None = None


class CampaignDraftEdit(BaseModel):
    """A committee member's edit to a draft waiting for approval.

    Every field optional: a field left out keeps what the draft has.
    """

    subject: str | None = None
    heading: str | None = None
    body: str | None = None
    audiences: list[str] | None = None
    extra_recipients: list[str] | None = None
    excluded_recipients: list[str] | None = None
    send_at: datetime | None = None
    whatsapp: bool | None = None
    # False for the system's own updates (the event changed), which neither
    # mark the draft as hand-edited nor are refused for a scheduled one.
    by_hand: bool = True


class CampaignDecision(BaseModel):
    """Move a draft between states — only from the status expected.

    Conditional, so two committee members pressing Send at the same moment
    produce one send and one "already dealt with", not two sends.
    """

    status: str
    expect: str = "awaiting_approval"
    reviewed_by: int | None = None
    recipients: int | None = None
    # Stored as the row's `error`: why the system held a scheduled email back.
    note: str | None = None


class Recipient(BaseModel):
    """One deliverable address, with whatever identity we hold for it.

    No unsubscribe token: it is derived from the address by the sender (an
    HMAC, see api/services/emailing.py), so it works for the majority of
    recipients who have no account and costs no storage.
    """

    email: str
    user_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    # Which audience key produced this address. Kept for the admin preview
    # ("42 members, 9 with accounts only"), not used when sending.
    source: str = ""
