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
    error: str | None = None
    created_by: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CampaignCreate(BaseModel):
    subject: str
    heading: str | None = None
    body: str = ""
    audiences: list[str] = []
    created_by: int | None = None


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
