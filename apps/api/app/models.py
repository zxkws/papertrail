from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, Field, model_validator

BBox = tuple[float, float, float, float]
Rotation = Literal[0, 90, 180, 270]


class TextStyle(BaseModel):
    font_family: str = "helv"
    font_size_pt: Annotated[float, Field(gt=0, le=144)] = 11
    color: str = Field(default="#111111", pattern=r"^#[0-9a-fA-F]{6}$")
    align: Literal["left", "center", "right"] = "left"
    rotation: Rotation = 0


class Operation(BaseModel):
    id: str
    seq: Annotated[int, Field(gt=0)]
    type: Literal[
        "replace_text", "delete_text", "move_text", "add_text", "cover_region"
    ]
    page_index: Annotated[int, Field(ge=0)]
    target_element_id: str | None = None
    created_element_id: str | None = None
    original_bbox: BBox | None = None
    bbox: BBox | None = None
    payload: dict = Field(default_factory=dict)
    style: TextStyle | None = None
    z_order: int | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.bbox and (self.bbox[2] <= self.bbox[0] or self.bbox[3] <= self.bbox[1]):
            raise ValueError("bbox must have positive area")
        if self.type == "add_text":
            if not self.created_element_id or not self.created_element_id.startswith(
                "a:"
            ):
                raise ValueError("add_text requires stable a: created_element_id")
            if not self.bbox:
                raise ValueError("add_text requires bbox")
        elif self.type == "cover_region":
            if not self.bbox:
                raise ValueError("cover_region requires bbox")
        elif not self.target_element_id:
            raise ValueError(f"{self.type} requires target_element_id")
        return self


class SaveOperations(BaseModel):
    expected_revision: Annotated[int, Field(ge=0)]
    operations: list[Operation]


class NativeElement(BaseModel):
    id: str
    page_index: int
    text: str
    bbox: BBox
    font_name: str
    font_size: float
    color: str
    flags: int = 0
    source: Literal["native"] = "native"
    editability: Literal["native", "cover_only"] = "native"
