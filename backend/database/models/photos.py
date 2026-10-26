"""Persisted EESoc photo sets; no authentication happens in this internal adapter."""
from typing import Literal
from pydantic import BaseModel, Field


class PhotoSetCreate(BaseModel):
    token: str = Field(min_length=24, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    event_id: int = Field(gt=0)
    orientation: Literal["portrait", "landscape"] = "portrait"
    created_by: int
    image_ids: list[int] = Field(min_length=1, max_length=4)
