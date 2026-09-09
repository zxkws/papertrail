"""编辑流水线：分析 -> 擦除 -> 重绘。前后端共用的核心逻辑都在这里。"""
from __future__ import annotations

import re

import cv2
import numpy as np
from PIL import Image

from . import analyze as A
from . import fonts as F
from . import inpaint as I
from . import render as R


def bgr_to_pil(img_bgr) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")


def pil_to_bgr(img_pil) -> np.ndarray:
    return cv2.cvtColor(np.array(img_pil.convert("RGB")), cv2.COLOR_RGB2BGR)


LOW_IOU = 0.7  # 低于此值多半是 OCR 文本与图上文字对不上


def text_variants(text: str) -> list[str]:
    """OCR 常见的空格错漏，生成几个候选写法用于回退比对。"""
    out = []
    fixed = re.sub(r"([:：,，;；.、])(\S)", r"\1 \2", text)
    if fixed != text:
        out.append(fixed)
    squeezed = re.sub(r"\s+", "", text)
    if squeezed != text:
        out.append(squeezed)
    return out


def inspect_box(img_bgr, quad, text="", match_fonts=False, top=8) -> dict:
    """返回一个框的全部可编辑属性；match_fonts=True 时附带字体匹配结果。"""
    info = A.analyze(img_bgr, quad)
    info["text"] = text
    info["suggest"] = {}

    if text:
        region = info["region"]
        soft = A.soft_mask(img_bgr, region, info["text_color"], info["bg_color"],
                           keep=A.text_mask(img_bgr, region, keep_bbox=info["bbox"]))
        if match_fonts and soft.size:
            matches = R.match_font(F.candidates(), text, info["ink_bbox"], region, soft, top=top)
            # 分数偏低时，多半是 OCR 文本本身有出入，换几种写法再比一次
            if matches and matches[0]["iou"] < LOW_IOU:
                for variant in text_variants(text):
                    alt = R.match_font(matches, variant, info["ink_bbox"], region, soft, top=top)
                    if alt and alt[0]["iou"] > matches[0]["iou"] + 0.1:
                        matches, info["suggest"]["text"] = alt, variant
            info["font_matches"] = matches
            if matches:
                info["suggest"]["font"] = matches[0]["name"]
                info["suggest"]["size"] = matches[0]["size"]
                info["suggest"]["iou"] = matches[0]["iou"]
        if "font" not in info["suggest"]:
            d = F.default_font()
            if d:
                calib = R.calibrate(d, text, info["ink_bbox"])
                info["suggest"]["font"] = d["name"]
                info["suggest"]["size"] = calib["size"] if calib else None
    info["suggest"]["erase"] = I.choose_method(info["bg_std"], info.get("bg_residual"))
    info["suggest"]["color"] = info["text_color"]
    return info


def apply_edit(img_bgr, edit: dict) -> tuple[np.ndarray, dict]:
    """对单个框执行一次编辑。edit 字段见 README。返回(新图, 本次用到的参数)。"""
    quad = edit["quad"]
    original = edit.get("original_text", "") or ""
    new_text = edit.get("text", "")

    info = A.analyze(img_bgr, quad)
    region = info["region"]
    mask = A.text_mask(img_bgr, region, keep_bbox=info["bbox"])
    full = A.full_mask(img_bgr.shape, region, mask)

    # 1) 擦除原文字
    bg = edit.get("bg_color") or info["bg_color"]
    erased, method = I.erase(
        img_bgr, full, bg, info["bg_std"],
        method=edit.get("erase") or "auto", grow=int(3 if edit.get("grow") is None else edit["grow"]),
        bg_residual=info.get("bg_residual"),
    )
    used = {"erase": method, "bg_color": bg, "ink_bbox": info["ink_bbox"], "angle": info["angle"]}

    if not new_text:
        return erased, used

    # 2) 定字体、字号、基线
    entry = F.find(edit.get("font") or "") or F.default_font()
    if entry is None:
        raise RuntimeError("系统里没有找到可用字体")
    calib_text = original or new_text
    calib = R.calibrate(entry, calib_text, info["ink_bbox"], mode=edit.get("fit") or "height")
    if calib is None:
        raise RuntimeError("字号标定失败：墨迹框为空或原文为空")

    size = int(edit.get("size") or calib["size"])
    if size != calib["size"]:
        calib = R.calibrate(entry, calib_text, info["ink_bbox"], mode=edit.get("fit") or "height")
        calib["font"] = R.load(entry, size)
        calib["size"] = size
        bb = R.ink(calib["font"], calib_text)
        calib["anchor"] = [float(info["ink_bbox"][0] - bb[0]), float(info["ink_bbox"][1] - bb[1])]

    # 仅在显式要求时把过长的新文本压回框宽
    if edit.get("shrink_to_fit", False):
        box_w = info["bbox"][2] - info["bbox"][0]
        bb = R.ink(calib["font"], new_text)
        w = bb[2] - bb[0]
        limit = box_w * float(edit.get("max_width_ratio", 1.0))
        if w > limit > 0:
            size = max(4, int(size * limit / w))
            calib["font"] = R.load(entry, size)
            calib["size"] = size

    align = edit.get("align", "left")
    xy = R.place(calib["font"], new_text, calib, info["ink_bbox"], align=align)
    color = edit.get("color") or info["text_color"]
    angle = float(info["angle"] if edit.get("angle") is None else edit["angle"])

    out_pil = R.draw_text(bgr_to_pil(erased), new_text, calib["font"], xy, color,
                          angle=angle, opacity=int(edit.get("opacity") or 255))
    used.update(font=entry["name"], size=calib["size"], color=color, align=align,
                baseline=[round(xy[0], 2), round(xy[1], 2)], calibrated_on=calib_text)
    return pil_to_bgr(out_pil), used


def apply_edits(img_bgr, edits: list[dict]) -> tuple[np.ndarray, list[dict]]:
    out = img_bgr.copy()
    used = []
    for e in edits:
        out, u = apply_edit(out, e)
        used.append(u)
    return out, used
