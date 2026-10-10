"""Passkey shapes. Field names match the SQL columns, as everywhere else in this
service, so a Record maps straight onto a model."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ChallengeCreate(BaseModel):
    challenge: str
    purpose: Literal["register", "authenticate"]
    user_id: int | None = None
    link_id: int | None = None
    expires_at: datetime


class ChallengeConsume(BaseModel):
    challenge: str
    purpose: Literal["register", "authenticate"]


class PasskeyCreate(BaseModel):
    user_id: int
    credential_id: str
    public_key: str
    sign_count: int = 0
    transports: list[str] = []
    aaguid: str | None = None
    device_type: str | None = None
    backed_up: bool = False
    name: str = ""
    # Set when the passkey is being saved through a committee-made link: the
    # link is spent in the same transaction, or the passkey is not saved.
    link_id: int | None = None


class PasskeyUsed(BaseModel):
    sign_count: int


class LinkCreate(BaseModel):
    token_hash: str
    user_id: int
    created_by: int
    expires_at: datetime
