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
    doc = fitz.open(source_path)
    try:
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
