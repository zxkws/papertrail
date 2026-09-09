"""字号标定、文字重绘、字体匹配。

核心思路：用 OCR 出来的原文做自标定——同一串文字用候选字体渲染一遍，
把渲染墨迹框缩放到与原图墨迹框一致，即可反推字号和基线位置。
这样不依赖任何 cap-height 经验系数，升降部也能自然对齐。
"""
from __future__ import annotations

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

from .fonts import covers as covers_text


def load(font, size):
    if isinstance(font, dict):
        return ImageFont.truetype(font["path"], size, index=font.get("index", 0))
    return ImageFont.truetype(font, size)


def ink(font_obj, text):
    """相对「左-基线」原点的墨迹框。"""
    return font_obj.getbbox(text, anchor="ls")


def calibrate(font, text, ink_bbox, mode="height", base=120, max_size=1200):
    """返回 {size, anchor(基线左端点, 整图坐标), ink, font}。

    先按 base 号线性外推得到初值，再在初值附近逐号搜索——字体 hinting 会让
    墨迹高度与字号并非严格线性，直接外推常差 1~2 号。
    """
    tw = ink_bbox[2] - ink_bbox[0]
    th = ink_bbox[3] - ink_bbox[1]
    if not text or tw <= 0 or th <= 0:
        return None
    try:
        f = load(font, base)
        bb = ink(f, text)
    except Exception:
        return None
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w <= 0 or h <= 0:
        return None

    sh, sw = th / h, tw / w
    scale = {"height": sh, "width": sw, "fit": min(sh, sw)}.get(mode, sh)
    guess = max(4, min(max_size, int(round(base * scale))))

    def err(size):
        """(主判据, 次判据)：字号量化后高度常有并列，用宽度打破平局。"""
        bb = ink(load(font, size), text)
        w2, h2 = bb[2] - bb[0], bb[3] - bb[1]
        if mode == "width":
            return (abs(w2 - tw), abs(h2 - th))
        if mode == "fit":
            return (abs(w2 - tw) / max(tw, 1) + abs(h2 - th) / max(th, 1), 0)
        return (abs(h2 - th), abs(w2 - tw))

    span = max(2, int(guess * 0.15))
    best, best_err = guess, None
    for size in range(max(4, guess - span), min(max_size, guess + span) + 1):
        try:
            e = err(size)
        except Exception:
            continue
        if best_err is None or e < best_err:
            best, best_err = size, e
    size = best

    f2 = load(font, size)
    bb2 = ink(f2, text)
    return {
        "size": size,
        "anchor": [float(ink_bbox[0] - bb2[0]), float(ink_bbox[1] - bb2[1])],
        "ink": list(bb2),
        "font": f2,
    }


def place(font_obj, new_text, calib, ink_bbox, align="left"):
    """新文字的基线锚点：y 沿用标定基线，x 按对齐方式定。"""
    bb = ink(font_obj, new_text) if new_text else (0, 0, 0, 0)
    y = calib["anchor"][1]
    if align == "right":
        x = ink_bbox[2] - bb[2]
    elif align == "center":
        x = (ink_bbox[0] + ink_bbox[2]) / 2 - (bb[0] + bb[2]) / 2
    else:
        x = ink_bbox[0] - bb[0]
    return (float(x), float(y))


def draw_text(img_rgba, text, font_obj, baseline_xy, color_rgb, angle=0.0, opacity=255):
    """在整图上画一行文字；有倾角时绕基线锚点旋转。"""
    if not text:
        return img_rgba
    layer = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(baseline_xy, text, font=font_obj, anchor="ls",
           fill=(int(color_rgb[0]), int(color_rgb[1]), int(color_rgb[2]), int(opacity)))
    if abs(angle) > 0.2:
        layer = layer.rotate(-angle, resample=Image.BICUBIC, center=baseline_xy)
    return Image.alpha_composite(img_rgba, layer)


def render_soft(font_obj, text, anchor_xy, region):
    """把文字渲染成 0~1 覆盖度图，尺寸与 region 一致。"""
    x0, y0, x1, y1 = region
    canvas = Image.new("L", (max(1, x1 - x0), max(1, y1 - y0)), 0)
    ImageDraw.Draw(canvas).text((anchor_xy[0] - x0, anchor_xy[1] - y0), text,
                                font=font_obj, fill=255, anchor="ls")
    return np.asarray(canvas, dtype=np.float32) / 255.0


def render_soft_xscaled(font_obj, text, anchor_xy, region, ink_bbox, tol=0.02):
    """把渲染结果横向拉伸到与实际墨迹等宽再比对。

    OCR 文本与图上文字有出入时（漏字、吞空格），整行宽度对不上会让后面的
    字全部错位、IoU 崩掉。按总宽归一化后比的是字形本身，排序更稳。
    """
    m = render_soft(font_obj, text, anchor_xy, region)
    cols = np.nonzero(m.max(axis=0) > 0.05)[0]
    target = int(ink_bbox[2] - ink_bbox[0])
    if len(cols) < 2 or target < 2:
        return m
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    if abs((c1 - c0) - target) <= max(2, target * tol):
        return m
    scaled = cv2.resize(m[:, c0:c1], (target, m.shape[0]), interpolation=cv2.INTER_AREA)
    out = np.zeros_like(m)
    x = max(0, int(ink_bbox[0] - region[0]))
    w = min(scaled.shape[1], out.shape[1] - x)
    if w > 0:
        out[:, x:x + w] = scaled[:, :w]
    return out


def soft_iou(a, b):
    m = np.minimum(a, b).sum()
    u = np.maximum(a, b).sum()
    return float(m / u) if u > 0 else 0.0


def match_font(candidates, text, ink_bbox, region, observed_soft, top=8, refine=10):
    """字体匹配：原文用候选字体重渲染，与实际墨迹的覆盖度图算 soft-IoU。

    两轮——先按标定位置粗筛，再对领先的若干候选做 ±2px 位移、±2 字号精修，
    消除取整误差带来的错判。
    """
    obs = observed_soft
    if obs.size == 0 or obs.sum() <= 0:
        return []

    coarse = []
    for entry in candidates:
        if not covers_text(entry, text):
            continue  # 画不出这行字的字体，形状分数再高也没意义
        calib = calibrate(entry, text, ink_bbox)
        if calib is None:
            continue
        try:
            m = render_soft(calib["font"], text, calib["anchor"], region)
        except Exception:
            continue
        coarse.append((soft_iou(m, obs), entry, calib))
    coarse.sort(key=lambda t: -t[0])

    scored = []
    for score, entry, calib in coarse[:refine]:
        best = (score, calib["size"], calib["anchor"][0], calib["anchor"][1])
        for ds in (-2, -1, 0, 1, 2):
            size = calib["size"] + ds
            if size < 4:
                continue
            try:
                f = load(entry, size)
            except Exception:
                continue
            bb = ink(f, text)
            ax, ay = ink_bbox[0] - bb[0], ink_bbox[1] - bb[1]
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    xy = (ax + dx, ay + dy)
                    s = max(soft_iou(render_soft(f, text, xy, region), obs),
                            soft_iou(render_soft_xscaled(f, text, xy, region, ink_bbox), obs))
                    if s > best[0]:
                        best = (s, size, ax + dx, ay + dy)
        scored.append({
            "name": entry["name"], "family": entry["family"], "style": entry["style"],
            "path": entry["path"], "index": entry["index"],
            "iou": round(best[0], 4), "size": int(best[1]),
            "anchor": [round(best[2], 2), round(best[3], 2)],
        })
    for score, entry, calib in coarse[refine:]:
        scored.append({
            "name": entry["name"], "family": entry["family"], "style": entry["style"],
            "path": entry["path"], "index": entry["index"],
            "iou": round(score, 4), "size": calib["size"], "anchor": calib["anchor"],
        })
    scored.sort(key=lambda e: -e["iou"])
    return scored[:top]
