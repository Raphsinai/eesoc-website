"""Resources: a tree of pages, each holding a tree of entries."""

from datetime import datetime

from pydantic import BaseModel


class ResourcePage(BaseModel):
    id: int | None = None
    slug: str
    title: str
    description: str | None = None
    intro: str = ""
    icon: str | None = None
    sort_order: int = 100
    published: bool = True
    members_only: bool = False
    updated_at: datetime | None = None


class ResourceEntry(BaseModel):
    id: int | None = None
    page_id: int | None = None
    parent_id: int | None = None
    kind: str = "item"
    title: str
    body: str = ""
    url: str | None = None
    badge: str | None = None
    sort_order: int = 100


class ResourceEntryNode(ResourceEntry):
    """Same row, with its descendants attached. Built server-side so the client
    never has to reassemble a tree from a flat list."""

    children: list["ResourceEntryNode"] = []


class ResourcePageDetail(ResourcePage):
    entries: list[ResourceEntryNode] = []
    entry_count: int = 0


class ResourceImport(BaseModel):
    """Bulk import payload — one page and its flattened entry list, where each
    entry names its parent by index rather than id (ids do not exist yet)."""

    slug: str
    title: str
    description: str | None = None
    intro: str = ""
    sort_order: int = 100
    entries: list[dict] = []


class Upload(BaseModel):
    id: int | None = None
    stored_name: str
    original_name: str
    content_type: str
    byte_size: int
    purpose: str = "general"
    uploaded_by: int | None = None
    uploaded_at: datetime | None = None
