from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, Field, model_validator

BBox = tuple[float, float, float, float]
Rotation = Literal[0, 90, 180, 270]
RASTER_TYPES = ("raster_replace_text", "raster_delete_text")


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
        "replace_text",
        "delete_text",
        "move_text",
        "add_text",
        "cover_region",
        # 位图页（扫描件/截图）专用：没有可编辑的文字对象，按框重绘像素
        "raster_replace_text",
        "raster_delete_text",
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
        elif self.type in RASTER_TYPES:
            # 位图页没有 native 元素可指向，框本身就是标识，由前端给稳定的 r: id
            if not self.created_element_id or not self.created_element_id.startswith(
                "r:"
            ):
                raise ValueError(f"{self.type} requires stable r: created_element_id")
            if not self.bbox:
                raise ValueError(f"{self.type} requires bbox")
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


class RasterOcrRequest(BaseModel):
    """位图页整页识别。dpi 只影响识别精度，返回的坐标一律是 PDF 点。"""

    dpi: Annotated[int, Field(ge=72, le=600)] = 200
    engine: Literal["auto", "rapidocr", "tesseract"] = "auto"
    lang: str = "eng"
    match_fonts: bool = True
    min_score: Annotated[float, Field(ge=0, le=1)] = 0.0


class RasterInspectRequest(BaseModel):
    """分析单个手动框，quad 为四个点（PDF 点坐标）。"""

    quad: list[tuple[float, float]] = Field(min_length=4, max_length=4)
    text: str = ""
    match_fonts: bool = True
    dpi: Annotated[int, Field(ge=72, le=600)] = 200


class RasterPreviewRequest(BaseModel):
    """预览单条位图编辑。字段与 raster 操作的 payload/style 一致。"""

    bbox: BBox
    quad: list[tuple[float, float]] | None = None
    text: str = ""
    original_text: str = ""
    font: str | None = None
    erase: str = "auto"
    grow: int = 3
    style: TextStyle | None = None
    dpi: Annotated[int, Field(ge=72, le=600)] = 200
