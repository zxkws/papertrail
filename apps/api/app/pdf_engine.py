from __future__ import annotations

import hashlib
import tempfile
from collections import OrderedDict
from pathlib import Path
from threading import RLock

import fitz

from .models import NativeElement

LAYOUT_CACHE_MAX_SIZE = 2048
_layout_cache: OrderedDict[tuple[str, int], dict] = OrderedDict()
_layout_cache_lock = RLock()


IMAGE_COVERAGE_MIN = 0.6  # 图像覆盖率超过此值且无可见文字，判为位图页

# 上传图片时按这个分辨率折算页面尺寸。取值与位图流水线的渲染 DPI 一致，
# 这样「渲染这一页」拿回来的就是原始像素，不放大也不缩小，改字不损失清晰度。
IMAGE_PAGE_DPI = 200
IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
}


def sniff_image(data: bytes) -> str | None:
    """按魔数判断是不是支持的图片。不信任扩展名和 Content-Type。"""
    for magic, media_type in IMAGE_MAGIC.items():
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_to_pdf(data: bytes, dpi: int = IMAGE_PAGE_DPI) -> tuple[bytes, int, int]:
    """把一张图包成单页 PDF，返回 (pdf 字节, 宽像素, 高像素)。

    截图和扫描件本来就只有像素，转成 PDF 并不会让它变得可编辑——
    这里只是让它进入统一的页面模型，随后照样走位图路径（OCR + 重绘）。
    """
    pix = fitz.Pixmap(data)
    width_px, height_px = pix.width, pix.height
    if not width_px or not height_px:
        raise ValueError("EMPTY_IMAGE")
    scale = 72.0 / dpi
    doc = fitz.open()
    page = doc.new_page(width=width_px * scale, height=height_px * scale)
    page.insert_image(page.rect, stream=data)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out, width_px, height_px


def visible_text_chars(page) -> int:
    """可见文字的字符数。

    render mode 3 是不可见文字——ocrmypdf 那类「可搜索 PDF」在扫描图上盖的就是它，
    只服务搜索和复制，改它一个可见像素都不会变。这里必须排除，
    否则扫描页会被当成矢量页，走到根本改不动像素的原生路径上。
    """
    try:
        spans = page.get_texttrace()
    except Exception:
        return len(page.get_text("text").strip())
    total = 0
    for span in spans:
        if span.get("type") == 3 or float(span.get("opacity", 1)) <= 0.01:
            continue
        total += sum(1 for ch in span.get("chars", []) if chr(ch[0]).strip())
    return total


def image_coverage(page) -> float:
    area = abs(page.rect.get_area()) or 1.0
    covered, seen = 0.0, set()
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen:
            continue
        seen.add(xref)
        for rect in page.get_image_rects(xref):
            covered += abs(fitz.Rect(rect).get_area())
    return min(1.0, covered / area)


def page_kind(page) -> str:
    """vector：有可见文字对象，走原生 redaction+insert；raster：整页就是一张图。"""
    if visible_text_chars(page) > 0:
        return "vector"
    if image_coverage(page) >= IMAGE_COVERAGE_MIN:
        return "raster"
    return "vector"


def color_hex(value: int) -> str:
    return f"#{value & 0xFFFFFF:06X}"


def parse_layout(source_path: str, document_hash: str, page_index: int) -> dict:
    cache_key = (document_hash, page_index)
    with _layout_cache_lock:
        cached = _layout_cache.get(cache_key)
        if cached is not None:
            _layout_cache.move_to_end(cache_key)
            return cached

    doc = fitz.open(source_path)
    try:
        page = doc[page_index]
        raw = page.get_text("dict")
        elements: list[dict] = []
        ordinal = 0
        chars = 0
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    direction = line.get("dir", (1, 0))
                    editable = "native" if abs(direction[1]) < 0.001 else "cover_only"
                    element = NativeElement(
                        id=f"n:{document_hash[:16]}:p{page_index}:e{ordinal:04d}",
                        page_index=page_index,
                        text=text,
                        bbox=tuple(round(float(x), 4) for x in span["bbox"]),
                        font_name=span.get("font", "unknown"),
                        font_size=span.get("size", 11),
                        color=color_hex(span.get("color", 0)),
                        flags=span.get("flags", 0),
                        editability=editable,
                    )
                    elements.append(element.model_dump())
                    ordinal += 1
                    chars += len(text)
        rect = page.rect
        result = {
            "schema_version": "v1",
            "page_index": page_index,
            "width_pt": rect.width,
            "height_pt": rect.height,
            "crop_box": list(page.cropbox),
            "media_box": list(page.mediabox),
            "rotation": page.rotation if page.rotation in (0, 90, 180, 270) else 0,
            "scan_likelihood": 1.0 if chars == 0 else 0.0,
            "kind": page_kind(page),
            "elements": elements,
        }
    finally:
        doc.close()

    with _layout_cache_lock:
        existing = _layout_cache.get(cache_key)
        if existing is not None:
            _layout_cache.move_to_end(cache_key)
            return existing
        _layout_cache[cache_key] = result
        if len(_layout_cache) > LAYOUT_CACHE_MAX_SIZE:
            _layout_cache.popitem(last=False)
    return result


def clear_layout_cache() -> None:
    with _layout_cache_lock:
        _layout_cache.clear()


def _rgb(hex_color: str) -> tuple[float, float, float]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _font_name(text: str) -> str:
    helvetica = fitz.Font(fontname="helv")
    if all(
        ord(character) < 32 or helvetica.has_glyph(ord(character))
        for character in text
    ):
        return "helv"
    return "china-ss"


def export_pdf(source_path: str, canonical: dict) -> Path:
    # 延迟导入：位图能力是可选依赖，且 raster 包会反向引用本模块的 page_kind
    from . import raster_ext

    doc = fitz.open(source_path)
    try:
        # 阶段 0：位图页整页重绘。必须排在 A~C 之前——这一步会整页替换，
        # 放在后面会把已经画上去的覆盖块和插入文字一起冲掉。
        raster_items = canonical.get("raster_edits") or []
        if raster_items:
            if not raster_ext.AVAILABLE:
                raise ValueError(f"RASTER_UNAVAILABLE: {raster_ext.HINT}")
            grouped: dict[int, list] = {}
            for item in raster_items:
                grouped.setdefault(item["page_index"], []).append(item)
            for page_index, items in sorted(grouped.items()):
                if page_index >= doc.page_count:
                    raise ValueError(f"RASTER_PAGE_OUT_OF_RANGE:{page_index}")
                if page_kind(doc[page_index]) != "raster":
                    # 矢量页栅格化会毁掉整页的文字层和矢量图形，
                    # 与「非破坏式」的前提冲突，宁可报错也不做
                    raise ValueError(f"RASTER_EDIT_ON_VECTOR_PAGE:{page_index}")
                raster_ext.raster.edit_page(doc, page_index, items)

        by_page: dict[int, dict[str, list]] = {}
        for phase in ("redactions", "covers", "inserts"):
            for item in canonical[phase]:
                by_page.setdefault(
                    item["page_index"], {"redactions": [], "covers": [], "inserts": []}
                )[phase].append(item)
        for page_index, phases in by_page.items():
            page = doc[page_index]
            # A. Native text redaction. Images and graphics are explicitly preserved.
            for item in phases["redactions"]:
                page.add_redact_annot(fitz.Rect(item["bbox"]), fill=False)
            if phases["redactions"]:
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_NONE,
                    graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                    text=fitz.PDF_REDACT_TEXT_REMOVE,
                )
            # B. Visual-only scan cover.
            for item in phases["covers"]:
                page.draw_rect(
                    fitz.Rect(item["bbox"]),
                    color=_rgb(item["color"]),
                    fill=_rgb(item["color"]),
                    overlay=True,
                )
            # C. Final text insertion.
            for item in phases["inserts"]:
                style = item["style"]
                rotation = style.get("rotation", 0)
                if rotation not in (0, 90, 180, 270):
                    raise ValueError("UNSUPPORTED_TEXT_ROTATION")
                rect = fitz.Rect(item["bbox"])
                font_size = float(style.get("font_size_pt", 11))
                font_name = _font_name(item["text"])
                # Deterministic MVP fit: retry down to 4pt instead of publishing clipped text.
                result = -1.0
                while font_size >= 4 and result < 0:
                    result = page.insert_textbox(
                        rect,
                        item["text"],
                        fontsize=font_size,
                        fontname=font_name,
                        color=_rgb(style.get("color", "#111111")),
                        rotate=rotation,
                        align={"left": 0, "center": 1, "right": 2}.get(
                            style.get("align"), 0
                        ),
                        overlay=True,
                    )
                    if result < 0:
                        font_size -= 0.5
                if result < 0:
                    raise ValueError(f"TEXT_OVERFLOW:{item['target_id']}")
        tmp = tempfile.NamedTemporaryFile(
            prefix="pdf-export-", suffix=".pdf", delete=False
        )
        tmp.close()
        path = Path(tmp.name)
        doc.save(path, garbage=3, deflate=True)
        check = fitz.open(path)
        try:
            if check.page_count != doc.page_count:
                raise ValueError("export verification failed")
        finally:
            check.close()
        return path
    finally:
        doc.close()
