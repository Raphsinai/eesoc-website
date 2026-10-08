"""Job leases and per-recipient bulk-send state.

Field names match the SQL columns exactly, as everywhere else in the adapter,
so a Record becomes a model with `Model(**dict(row))` where a table is read
back. The lease and claim endpoints return hand-shaped dicts instead, because
a caller wants the *verdict* (claimed, released, remaining), not the row.
"""

from datetime import datetime

from pydantic import BaseModel


class JobLeaseClaim(BaseModel):
    """Ask to hold a named lease.

    `holder` is an opaque per-process id (a uuid4 hex in practice) so a pod
    can only ever release a lease it still holds — releasing by name alone
    would let a pod that lost the lease to another delete the winner's row.
    """

    name: str
    holder: str
    ttl_seconds: int = 900


class JobLeaseRelease(BaseModel):
    name: str
    holder: str


class RecipientSeed(BaseModel):
    """Idempotently add the rows for one campaign's audience."""

    campaign_id: int
    emails: list[str] = []


class RecipientClaim(BaseModel):
    """Take the next batch of unsent recipients.

    `stale_seconds` is how old a claimed-but-unconfirmed row must be before it
    is considered abandoned by a dead sender and re-claimable.
    """

    campaign_id: int
    batch_size: int = 100
    stale_seconds: int = 600


class RecipientConfirm(BaseModel):
    """Report a batch's outcome: every email claimed is confirmed either way.

    `ok` for the whole list, because a sender either reached an address or it
    did not — a partial re-send inside one batch is not a thing the protocol
    needs.
    """

    campaign_id: int
    emails: list[str]
    ok: bool


class ResumableCampaign(BaseModel):
    """One interrupted bulk send, as the resume path reads it.

    Returned as a plain dict by the endpoint (the columns come straight off the
    campaign row); this model documents the shape and is not currently
    deserialised anywhere — kept for symmetry with the other adapters' models.
    """

    id: int
    subject: str
    heading: str | None = None
    body: str = ""
    audiences: list[str] = []
    status: str = "sending"
    recipients: int = 0
    sent: int = 0
    failed: int = 0
    error: str | None = None
    kind: str = "newsletter"
    event_id: int | None = None
    created_by: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None