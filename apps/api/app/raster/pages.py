"""PDF 页面与位图流水线之间的桥接：页面类型判定、点↔像素换算、整页改字。

坐标约定：对外一律用 PDF 点（与 native 元素的 bbox 同一套），
渲染 DPI 只在这一层内部出现，因此预览与导出用不同 DPI 也不会错位。
"""
from __future__ import annotations

import base64

import cv2
import fitz
import numpy as np

from ..pdf_engine import page_kind  # noqa: F401  (对外从 raster 包也能取到)
from . import ocr as ocr_engine
from . import pipeline

RASTER_DPI = 200  # 导出与 OCR 的默认渲染精度

# 页面类型判定放在 pdf_engine（纯 fitz，不依赖 opencv），
# 这样没装位图 extra 的部署也能告诉前端哪些页是扫描页。


def render_page(page, dpi: int = RASTER_DPI):
    """渲染成 BGR 图，同时返回点→像素的缩放系数。"""
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2BGR), scale


def _hex_to_rgb(value: str | None):
    if not value:
        return None
    v = value.lstrip("#")
    return [int(v[i:i + 2], 16) for i in (0, 2, 4)]


def _rgb_to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(c) for c in rgb))


def quad_pt_to_px(quad_pt, scale):
    return [[float(x) * scale, float(y) * scale] for x, y in quad_pt]


def quad_px_to_pt(quad_px, scale):
    return [[round(float(x) / scale, 3), round(float(y) / scale, 3)] for x, y in quad_px]


def bbox_to_quad(bbox):
    x0, y0, x1, y1 = bbox
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def edit_to_pixels(item: dict, scale: float) -> dict:
    """canonical 里的一条位图编辑（点坐标）转成流水线要的像素参数。"""
    quad_pt = item.get("quad") or bbox_to_quad(item["bbox"])
    style = item.get("style") or {}
    size_pt = style.get("font_size_pt")
    payload = item.get("payload") or {}
    return {
        "quad": quad_pt_to_px(quad_pt, scale),
        "text": item.get("text", ""),
        "original_text": payload.get("original_text", ""),
        "font": payload.get("font") or None,
        "size": int(round(float(size_pt) * scale)) if size_pt else None,
        "color": _hex_to_rgb(style.get("color")),
        "align": style.get("align", "left"),
        "erase": payload.get("erase", "auto"),
        "grow": int(payload.get("grow", 3)),
        "angle": payload.get("angle"),
        "opacity": int(payload.get("opacity", 255)),
    }


def ocr_page(page, dpi: int = RASTER_DPI, engine: str = "auto", lang: str = "eng",
             match_fonts: bool = True, min_score: float = 0.0) -> dict:
    """识别整页文字并逐框分析，结果换算回点坐标。"""
    img, scale = render_page(page, dpi)
    raw, used_engine = ocr_engine.detect(img, engine=engine, lang=lang)
    boxes = []
    for index, item in enumerate(raw):
        if item["score"] < min_score:
            continue
        info = pipeline.inspect_box(img, item["quad"], item["text"], match_fonts=match_fonts)
        boxes.append(box_to_points(info, item, index, scale))
    return {"engine": used_engine, "dpi": dpi, "scale": scale,
            "count": len(boxes), "boxes": boxes}


def box_to_points(info: dict, item: dict, index: int, scale: float) -> dict:
    """把一个分析结果里的像素量换算成点，颜色转成 #RRGGBB。"""
    suggest = dict(info.get("suggest") or {})
    if suggest.get("size"):
        suggest["font_size_pt"] = round(suggest["size"] / scale, 2)
    if suggest.get("color"):
        suggest["color"] = _rgb_to_hex(suggest["color"])
    return {
        "index": index,
        "text": item["text"],
        "score": item["score"],
        "quad": quad_px_to_pt(item["quad"], scale),
        "bbox": [round(v / scale, 3) for v in info["bbox"]],
        "ink_bbox": [round(v / scale, 3) for v in info["ink_bbox"]],
        "angle": info["angle"],
        "text_color": _rgb_to_hex(info["text_color"]),
        "bg_color": _rgb_to_hex(info["bg_color"]),
        "bg_std": info["bg_std"],
        "bg_residual": info.get("bg_residual"),
        "stroke_width": info["stroke_width"],
        "suggest": suggest,
        "font_matches": [
            {**m, "font_size_pt": round(m["size"] / scale, 2)}
            for m in info.get("font_matches", [])
        ],
    }


def inspect_quad(page, quad_pt, text: str = "", match_fonts: bool = True,
                 dpi: int = RASTER_DPI) -> dict:
    """分析单个框（手动框选走这里），入参出参都是点坐标。"""
    img, scale = render_page(page, dpi)
    quad_px = quad_pt_to_px(quad_pt, scale)
    info = pipeline.inspect_box(img, quad_px, text, match_fonts=match_fonts)
    return box_to_points(info, {"text": text, "score": 1.0, "quad": quad_px}, 0, scale)


def preview_edit(page, item: dict, dpi: int = RASTER_DPI, pad_pt: float = 6.0) -> dict:
    """按真实流水线渲染单条编辑，只回传受影响的那一小块。

    画布上用 HTML 文本做预览是不可能准的：匹配到的字体在服务端、.ttc 字体集
    浏览器加载不了、中文字体几十 MB，而擦除与背景修补的效果更是覆盖层表现不出来的。
    这里直接跑一遍真实重绘再裁剪，所见即所得。
    """
    img, scale = render_page(page, dpi)
    out, used = pipeline.apply_edits(img, [edit_to_pixels(item, scale)])

    quad_pt = item.get("quad") or bbox_to_quad(item["bbox"])
    xs = [p[0] for p in quad_pt]
    ys = [p[1] for p in quad_pt]
    # 新文字可能比原文长而溢出原框，预览要把溢出部分也带上
    region_pt = [
        max(0.0, min(xs) - pad_pt),
        max(0.0, min(ys) - pad_pt),
        min(page.rect.width, max(xs) + pad_pt + (max(xs) - min(xs))),
        min(page.rect.height, max(ys) + pad_pt),
    ]
    x0, y0, x1, y1 = (int(round(v * scale)) for v in region_pt)
    x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
    crop = out[y0:min(y1, out.shape[0]), x0:min(x1, out.shape[1])]
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        raise ValueError("PREVIEW_ENCODE_FAILED")
    return {
        "region_pt": [round(v, 3) for v in region_pt],
        "dpi": dpi,
        "used": used[0] if used else {},
        "image": "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode(),
    }


def edit_page(doc, page_index: int, items: list[dict], dpi: int = RASTER_DPI) -> list[dict]:
    """把该页渲染成图、改字、再整页替换回去。

    只允许作用在位图页：矢量页栅格化会毁掉整页的文字层和矢量图形，
    与本项目「非破坏式」的前提冲突，调用方必须先用 page_kind 挡住。
    """
    page = doc[page_index]
    rect = fitz.Rect(page.rect)
    img, scale = render_page(page, dpi)
    out, used = pipeline.apply_edits(img, [edit_to_pixels(i, scale) for i in items])
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        raise ValueError("RASTER_ENCODE_FAILED")

    # 整页替换：新建同尺寸页面并铺上改后的图。页面旋转在渲染时已经生效，
    # 因此新页 rotation 归零，视觉效果不变。
    doc.delete_page(page_index)
    new_page = doc.new_page(page_index, width=rect.width, height=rect.height)
    new_page.insert_image(new_page.rect, stream=buf.tobytes())
    for record, item in zip(used, items):
        record["target_id"] = item.get("target_id")
    return used
