"""对单个文字框做像素级分析：文字掩码、颜色、字号线索、笔画粗细。

所有几何量都用整图坐标，方便前端直接画框。
"""
from __future__ import annotations

import cv2
import numpy as np


def quad_to_bbox(quad) -> list[int]:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]


def quad_angle(quad) -> float:
    (x0, y0), (x1, y1) = quad[0], quad[1]
    return float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))


def clamp_region(bbox, shape, pad=None):
    """OCR 框常常贴着字面切，升降部会被切掉，所以按框高比例外扩再分析。"""
    h, w = shape[:2]
    x0, y0, x1, y1 = bbox
    if pad is None:
        pad = max(3, int(round((y1 - y0) * 0.2)))
    return (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad))


def text_mask(img_bgr, region, keep_bbox=None):
    """Otsu 二值化 + 用边框像素判断极性，返回文字掩码(uint8 0/1)。"""
    x0, y0, x1, y1 = region
    crop = img_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((0, 0), np.uint8)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    border = np.concatenate([th[0, :], th[-1, :], th[:, 0], th[:, -1]])
    bg_is_bright = border.mean() > 127
    mask = ((th == 0) if bg_is_bright else (th == 255)).astype(np.uint8)
    if keep_bbox is not None:
        mask = _keep_components_touching(mask, region, keep_bbox)
    return mask


def _keep_components_touching(mask, region, bbox):
    """外扩后可能带进相邻行的笔画，只保留与原始 OCR 框相交的连通块。"""
    n, labels = cv2.connectedComponents(mask, connectivity=8)
    if n <= 1:
        return mask
    rx0, ry0 = region[0], region[1]
    bx0, by0, bx1, by1 = bbox
    sel = labels[max(0, by0 - ry0):max(0, by1 - ry0), max(0, bx0 - rx0):max(0, bx1 - rx0)]
    keep = set(np.unique(sel).tolist()) - {0}
    if not keep:
        return mask
    return np.isin(labels, list(keep)).astype(np.uint8)


def _median_color(crop_bgr, sel):
    px = crop_bgr[sel.astype(bool)]
    if px.size == 0:
        return None, 0.0
    med = np.median(px, axis=0)
    std = float(np.mean(np.std(px.astype(np.float32), axis=0)))
    return [int(med[2]), int(med[1]), int(med[0])], std  # BGR -> RGB


def analyze(img_bgr, quad) -> dict:
    bbox = quad_to_bbox(quad)
    region = clamp_region(bbox, img_bgr.shape)
    x0, y0, x1, y1 = region
    crop = img_bgr[y0:y1, x0:x1]
    mask = text_mask(img_bgr, region, keep_bbox=bbox)

    out = {
        "bbox": bbox,
        "region": [x0, y0, x1, y1],
        "angle": round(quad_angle(quad), 3),
    }
    if mask.size == 0 or mask.sum() == 0:
        out.update(ink_bbox=bbox, ink_size=[bbox[2] - bbox[0], bbox[3] - bbox[1]],
                   text_color=[0, 0, 0], bg_color=[255, 255, 255], bg_std=0.0, bg_residual=0.0,
                   stroke_width=1.0, coverage=0.0)
        return out

    ys, xs = np.nonzero(mask)
    ink = [int(x0 + xs.min()), int(y0 + ys.min()), int(x0 + xs.max()) + 1, int(y0 + ys.max()) + 1]

    # 文字色取笔画内核，避开抗锯齿边缘；核心被腐蚀空了就退回整块掩码
    core = cv2.erode(mask, np.ones((3, 3), np.uint8))
    if core.sum() < 8:
        core = mask
    text_color, _ = _median_color(crop, core)

    # 背景色取掩码膨胀之外的像素，同样避开抗锯齿光晕
    halo = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    bg_color, bg_std = _median_color(crop, 1 - halo)
    if bg_color is None:
        bg_color, bg_std = [255, 255, 255], 0.0

    # 对背景像素拟合一次线性平面，残差小说明是渐变而非纹理
    bg_sel = (1 - halo).astype(bool)
    residual = 0.0
    ys_b, xs_b = np.nonzero(bg_sel)
    if len(xs_b) > 50:
        Amat = np.stack([xs_b, ys_b, np.ones_like(xs_b)], 1).astype(np.float32)
        res = []
        for c in range(3):
            v = crop[:, :, c][bg_sel].astype(np.float32)
            coef, *_ = np.linalg.lstsq(Amat, v, rcond=None)
            res.append(float(np.std(v - Amat @ coef)))
        residual = float(np.mean(res))

    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    stroke = float(2 * dist[mask > 0].mean())

    out.update(
        ink_bbox=ink,
        ink_size=[ink[2] - ink[0], ink[3] - ink[1]],
        text_color=text_color,
        bg_color=bg_color,
        bg_std=round(bg_std, 2),
        bg_residual=round(residual, 2),
        stroke_width=round(stroke, 2),
        coverage=round(float(mask.mean()), 4),
    )
    return out


def full_mask(img_shape, region, mask):
    """把局部掩码放回整图尺寸。"""
    full = np.zeros(img_shape[:2], np.uint8)
    x0, y0, x1, y1 = region
    full[y0:y1, x0:x1] = mask
    return full


def soft_mask(img_bgr, region, text_color_rgb, bg_color_rgb, keep=None):
    """抗锯齿覆盖度掩码(0~1)：按文字色/背景色把灰度线性映射到墨水浓度。

    比二值掩码更适合做字体比对——不会把边缘过渡算成实心笔画，
    因此不会系统性偏向粗体。
    """
    x0, y0, x1, y1 = region
    crop = img_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros((0, 0), np.float32)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tg = float(np.dot(text_color_rgb[::-1], [0.114, 0.587, 0.299]))
    bg = float(np.dot(bg_color_rgb[::-1], [0.114, 0.587, 0.299]))
    if abs(tg - bg) < 1e-3:
        return np.zeros_like(gray)
    soft = np.clip((gray - bg) / (tg - bg), 0.0, 1.0)
    if keep is not None:  # 与二值掩码同口径，剔除相邻行的残留
        soft = soft * cv2.dilate(keep, np.ones((3, 3), np.uint8)).astype(np.float32)
    return soft
