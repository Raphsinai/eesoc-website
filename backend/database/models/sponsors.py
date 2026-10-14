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
    announced_at: datetime | None = None
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
    # interested: the sponsor asked for it. confirmed: the committee linked it,
    # so the sponsor has it. declined: the committee said no.
    status: str = "interested"
    note: str | None = None
    # The agreement clause a confirmed link delivers; None is outside the package.
    clause: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SponsorLink(BaseModel):
    """The committee attaching an event to a sponsor."""
    clause: str | None = None
    note: str | None = None


class SponsorRequest(BaseModel):
    id: int | None = None
    sponsor_id: int
    format: str | None = None
    body: str = ""
    timing: str | None = None
    status: str = "open"
    response: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SponsorRequestUpdate(BaseModel):
    status: str | None = None
    response: str | None = None


class SponsorContact(BaseModel):
    id: int | None = None
    sponsor_id: int | None = None
    kind: str                           # email | link
    value: str
    label: str = ""
    note: str = ""
    sort_order: int = 0


class SponsorTerm(BaseModel):
    clause: str
    agreed: bool = True
    allowance: int | None = None
    note: str = ""
    done_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: int | None = None


class SponsorPackageDefault(BaseModel):
    """The committee's allowance for a clause of the standard package."""

    clause: str | None = None
    allowance: int
    updated_at: datetime | None = None
    updated_by: int | None = None


class SponsorMarketing(BaseModel):
    id: int | None = None
    sponsor_id: int | None = None
    academic_year: str
    subject: str
    body: str = ""
    media: list[str] = []
    audiences: list[str] = []
    channels: list[str] = ["email"]
    preferred_date: date | None = None
    status: str = "pending"             # pending | shared | declined | withdrawn
    response: str = ""
    campaign_id: int | None = None
    emailed_at: datetime | None = None
    posted_at: datetime | None = None
    created_by: int | None = None
    decided_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SponsorMarketingUpdate(BaseModel):
    """Every field optional: only what is sent is changed."""
    subject: str | None = None
    body: str | None = None
    status: str | None = None
    response: str | None = None
    campaign_id: int | None = None
    decided_by: int | None = None
    mark_emailed: bool = False
    mark_posted: bool = False


class SponsorNote(BaseModel):
    id: int | None = None
    sponsor_id: int | None = None
    topic: str = "general"
    topic_label: str = ""
    body: str
    from_committee: bool = False
    author_id: int | None = None
    read_at: datetime | None = None
    created_at: datetime | None = None


class SponsorAsset(BaseModel):
    id: int | None = None
    sponsor_id: int
    label: str
    file_url: str
    uploaded_at: datetime | None = None
