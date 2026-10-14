"""The WhatsApp groups announcements go to, and the posts made into them.

Field names match the SQL columns exactly, as everywhere else in the adapter.
"""

from datetime import datetime

from pydantic import BaseModel


class WhatsAppGroup(BaseModel):
    jid: str
    name: str = ""
    added_by: int | None = None
    added_at: datetime | None = None


class WhatsAppPost(BaseModel):
    id: int | None = None
    campaign_id: int | None = None
    jid: str
    group_name: str = ""
    body: str
    status: str = "sending"
    message_id: str | None = None
    error: str | None = None
    created_by: int | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None


class WhatsAppPostClaim(BaseModel):
    """A post about to be made. Written before it goes: see migration 077."""

    campaign_id: int | None = None
    jid: str
    group_name: str = ""
    body: str
    created_by: int | None = None


class WhatsAppPostResult(BaseModel):
    status: str
    message_id: str | None = None
    error: str | None = None
