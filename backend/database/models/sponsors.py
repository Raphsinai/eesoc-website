"""Sponsors and the sponsor portal."""

from datetime import date, datetime

from pydantic import BaseModel


class Sponsor(BaseModel):
    id: int | None = None
    slug: str
    name: str
    sector: str | None = None
    strand: str | None = None
    one_line: str | None = None
    blurb: str = ""
    with_us: list[str] = []
    site: str | None = None
    logo_url: str | None = None
    roles: int = 0
    active: bool = True
    sort_order: int = 100
    # Where a member sends their CV. EESoc does not collect CVs, so this is a
    # direct line to the sponsor rather than anything we hold.
    cv_contact_email: str | None = None
    cv_contact_name: str | None = None
    cv_contact_note: str | None = None
    # Committee-only. Never returned by a public endpoint.
    amount_pence: int = 0
    internal_note: str = ""
    agreement_year: str | None = None


class JobListing(BaseModel):
    id: int | None = None
    sponsor_id: int
    title: str
    location: str | None = None
    body: str = ""
    apply_url: str | None = None
    closes_on: date | None = None
    applications: int = 0
    status: str = "live"                # draft | live | closed
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JobListingWrite(BaseModel):
    title: str
    location: str | None = None
    body: str = ""
    apply_url: str | None = None
    closes_on: date | None = None
    status: str = "live"


class EventSponsorship(BaseModel):
    id: int | None = None
    event_id: int
    sponsor_id: int
    status: str = "claimed"             # claimed | confirmed | declined
    note: str | None = None
    created_at: datetime | None = None


class SponsorRequest(BaseModel):
    id: int | None = None
    sponsor_id: int
    format: str | None = None
    body: str = ""
    status: str = "open"
    created_at: datetime | None = None


class SponsorAsset(BaseModel):
    id: int | None = None
    sponsor_id: int
    label: str
    file_url: str
    uploaded_at: datetime | None = None
