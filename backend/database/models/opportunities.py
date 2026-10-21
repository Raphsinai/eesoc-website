"""Shapes crossing the API/database-adapter boundary for opportunities."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

Year = Literal["1", "2", "3", "4", "msc", "phd"]


class OpportunityCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=20, max_length=10000)
    links: list[HttpUrl] = Field(default_factory=list, max_length=10)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    target_years: list[Year] = Field(min_length=1, max_length=6)
    image_ids: list[int] = Field(default_factory=list, max_length=5)
    posted_until: date | None = None
    # Adapter-only, set from a verified session, never from a client request.
    submitted_by: int | None = None

    @field_validator("title", "description")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value or (value.count("\n") > 0 and len(value.split()) == 0):
            raise ValueError("Cannot be blank")
        return value

    @field_validator("title")
    @classmethod
    def title_one_line(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Use one line for the title")
        return value

    @field_validator("contact_phone")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        import re

        value = value.strip()
        if not re.fullmatch(r"[0-9+(). \-]{6,40}", value):
            raise ValueError("Use a phone number with digits and normal punctuation")
        return value

    @field_validator("image_ids")
    @classmethod
    def unique_images(cls, value: list[int]) -> list[int]:
        if any(i < 1 for i in value) or len(set(value)) != len(value):
            raise ValueError("Image IDs must be positive and unique")
        return value

    @field_validator("target_years")
    @classmethod
    def unique_years(cls, value: list[Year]) -> list[Year]:
        if len(set(value)) != len(value):
            raise ValueError("Choose each year only once")
        return value


class Opportunity(OpportunityCreate):
    id: int
    status: Literal["pending", "approved", "rejected", "withdrawn"] = "pending"
    review_notes: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    approved_at: datetime | None = None
    withdrawn_at: datetime | None = None
    email_campaign_id: int | None = None
    editable_after_rejection: bool = False
    revision: int = 1
    updated_at: datetime | None = None
    announcement_subject: str | None = None
    announcement_body: str | None = None
    created_at: datetime | None = None
    submitter_email: str | None = None
    images: list[dict] = Field(default_factory=list)
    pending_revision: dict | None = None
    pending_images: list[dict] = Field(default_factory=list)
    pending_revision_at: datetime | None = None


class OpportunityWithdraw(BaseModel):
    owner_id: int


class OpportunityReview(BaseModel):
    decision: Literal["approved", "rejected"]
    notes: str | None = Field(default=None, max_length=3000)
    allow_edit: bool = False
    reviewed_by: int | None = None
    campaign_subject: str | None = None
    campaign_body: str | None = None


class OpportunityEdit(OpportunityCreate):
    # The API supplies owner_id from the session, not a browser field.
    owner_id: int | None = None


class AnnouncementDraft(BaseModel):
    subject: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=20, max_length=10000)
    owner_id: int | None = None

    @field_validator("subject", "body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.split():
            raise ValueError("Cannot be blank")
        return value

    @field_validator("subject")
    @classmethod
    def one_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Write a single-line subject")
        return value


class AnnouncementSubmit(BaseModel):
    owner_id: int
    audiences: list[str] = Field(min_length=1)
