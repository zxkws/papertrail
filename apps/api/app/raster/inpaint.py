"""擦除原文字：纯色背景直接填充，复杂背景走 OpenCV inpaint。"""
from __future__ import annotations

import cv2
import numpy as np

FLAT_STD = 3.0       # 背景标准差低于此值：纯色
RESIDUAL_STD = 4.0   # 去掉线性渐变后仍有较大残差：视为纹理


def choose_method(bg_std: float, bg_residual: float | None = None) -> str:
    """纯色 -> 直接填色；平滑渐变 -> 归一化卷积；纹理 -> Telea。"""
    if bg_std < FLAT_STD:
        return "solid"
    if bg_residual is not None and bg_residual < RESIDUAL_STD:
        return "smooth"
    return "telea"


def smooth_fill(img_bgr, hole):
    """归一化卷积补洞：用逐级放大的高斯核把周围背景平滑地插进空洞。

    对线性/径向渐变、柔和阴影这类平滑背景，比单一中位色自然得多。
    """
    src = img_bgr.astype(np.float32)
    known = (hole == 0).astype(np.float32)
    out = src.copy()
    todo = hole > 0
    for k in (15, 31, 63, 127, 255):
        if not todo.any():
            break
        k = k if k % 2 else k + 1
        num = cv2.GaussianBlur(src * known[..., None], (k, k), 0)
        den = cv2.GaussianBlur(known, (k, k), 0)
        ok = den > 1e-3
        sel = todo & ok
        if sel.any():
            out[sel] = (num[sel] / den[sel][..., None])
            todo = todo & ~sel
    return np.clip(out, 0, 255).astype(np.uint8)


def erase(img_bgr, mask, bg_color_rgb, bg_std, method="auto", grow=3, bg_residual=None):
    """在原图上擦掉 mask 覆盖的文字，返回新图。mask 为整图尺寸的 0/1。"""
    if method == "auto":
        method = choose_method(bg_std, bg_residual)
    if mask.sum() == 0:
        return img_bgr.copy(), method

    k = max(1, int(grow) * 2 + 1)
    grown = cv2.dilate(mask, np.ones((k, k), np.uint8))
    out = img_bgr.copy()

    if method == "solid":
        bgr = np.array([bg_color_rgb[2], bg_color_rgb[1], bg_color_rgb[0]], np.uint8)
        out[grown > 0] = bgr
        # 轻微羽化边界，避免纯色块和周围渐变出现硬边
        edge = cv2.dilate(grown, np.ones((3, 3), np.uint8)) - grown
        if edge.sum() > 0:
            blur = cv2.GaussianBlur(out, (5, 5), 0)
            out[edge > 0] = blur[edge > 0]
    elif method == "smooth":
        out = smooth_fill(out, grown)
    else:
        flags = cv2.INPAINT_TELEA if method == "telea" else cv2.INPAINT_NS
        out = cv2.inpaint(out, grown * 255, 3, flags)
    return out, method
