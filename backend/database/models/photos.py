"""Validated persisted photo-set creation and framing input."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class PhotoPlacement(BaseModel):
    x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    zoom: float = Field(default=1, ge=1, le=8, allow_inf_nan=False)


class PhotoSetCreate(BaseModel):
    token: str = Field(min_length=24, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    qr_code: str | None = Field(default=None, min_length=12, max_length=16,
                                pattern=r'^[A-Za-z0-9_-]+$')
    event_id: int = Field(gt=0)
    orientation: Literal['portrait', 'landscape'] = 'portrait'
    created_by: int
    image_ids: list[int] = Field(min_length=1, max_length=4)
    placements: list[PhotoPlacement] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def validate_placements(self):
        if self.placements and len(self.placements) != len(self.image_ids):
            raise ValueError('Framing must match the number of selected photos')
        return self


class PhotoFramingPatch(BaseModel):
    placements: list[PhotoPlacement] = Field(min_length=1, max_length=4)
