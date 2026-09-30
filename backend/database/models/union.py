"""Mirrors of eActivities payloads, as stored locally."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class CommitteeMember(BaseModel):
    id: int | None = None
    cid: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    login: str | None = None
    post_name: str
    phone_no: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    academic_year: str


class CommitteeEntry(CommitteeMember):
    """Committee member joined with the locally-edited overlay."""

    intro: str = ""
    photo_url: str | None = None
    display_role: str | None = None
    # Which row of the committee grid this person sits in, and where in it.
    row_number: int = 1
    sort_order: int = 100


class CommitteeProfile(BaseModel):
    """The editable half: ICU owns the name and post, we own the rest.

    Keyed on `login`, not CID — that is what identifies the same person across a
    re-sync, and what the rest of the system canonicalises on.
    """

    cid: str | None = None
    login: str | None = None
    intro: str = ""
    photo_url: str | None = None
    # Optional override for ICU's post name; blank falls back to theirs.
    display_role: str | None = None
    row_number: int = 1
    sort_order: int = 100


class UnionProduct(BaseModel):
    product_id: int
    product_line_id: int
    academic_year: str
    product_name: str | None = None
    line_name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    active: bool | None = None
    selling_date_start: datetime | None = None
    selling_date_end: datetime | None = None
    url: str | None = None


class UnionSale(BaseModel):
    id: int | None = None
    order_number: str
    academic_year: str
    sale_datetime: datetime | None = None
    product_id: int
    product_line_id: int
    price: Decimal | None = None
    quantity: int = 1
    quantity_collected: int = 0
    customer_cid: str | None = None
    customer_first_name: str | None = None
    customer_last_name: str | None = None
    customer_email: str | None = None
    customer_login: str | None = None


class SyncRun(BaseModel):
    id: int | None = None
    kind: str
    academic_year: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    ok: bool | None = None
    row_count: int | None = None
    message: str | None = None


class SiteContent(BaseModel):
    key: str
    value: str
