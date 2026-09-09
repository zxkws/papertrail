"""OCR 适配层：优先 RapidOCR(PP-OCRv4, 中英)，缺失时退回本机 tesseract。

统一输出 [{quad, text, score}]，quad 为四点多边形（整图坐标）。
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict

import cv2

_rapid = None


def available_engines() -> list[str]:
    engines = []
    try:
        import rapidocr_onnxruntime  # noqa: F401
        engines.append("rapidocr")
    except Exception:
        pass
    if shutil.which("tesseract"):
        engines.append("tesseract")
    return engines


def _bbox_quad(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _run_rapidocr(img_bgr):
    global _rapid
    if _rapid is None:
        from rapidocr_onnxruntime import RapidOCR
        _rapid = RapidOCR()
    result, _ = _rapid(img_bgr)
    out = []
    for item in result or []:
        quad, text, score = item[0], item[1], item[2]
        out.append({
            "quad": [[float(p[0]), float(p[1])] for p in quad],
            "text": text,
            "score": float(score),
        })
    return out


def _run_tesseract(img_bgr, lang="eng", psm=11):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "in.png")
        cv2.imwrite(p, img_bgr)
        cmd = ["tesseract", p, "stdout", "-l", lang, "--psm", str(psm), "tsv"]
        tsv = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout

    lines = defaultdict(lambda: {"words": [], "box": None, "conf": []})
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        conf = float(row.get("conf") or -1)
        if not text or conf < 0:
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        e = lines[key]
        e["words"].append(text)
        e["conf"].append(conf)
        e["box"] = (x, y, x + w, y + h) if e["box"] is None else (
            min(e["box"][0], x), min(e["box"][1], y),
            max(e["box"][2], x + w), max(e["box"][3], y + h))

    out = []
    for e in lines.values():
        x0, y0, x1, y1 = e["box"]
        out.append({
            "quad": _bbox_quad(x0, y0, x1 - x0, y1 - y0),
            "text": " ".join(e["words"]),
            "score": round(sum(e["conf"]) / len(e["conf"]) / 100, 4),
        })
    out.sort(key=lambda e: (e["quad"][0][1], e["quad"][0][0]))
    return out


def detect(img_bgr, engine="auto", lang="eng") -> tuple[list[dict], str]:
    engines = available_engines()
    if not engines:
        raise RuntimeError("没有可用的 OCR 引擎：请安装 rapidocr-onnxruntime 或 tesseract")
    if engine == "auto":
        engine = engines[0]
    if engine not in engines:
        raise RuntimeError(f"OCR 引擎 {engine} 不可用，当前可用：{engines}")
    if engine == "rapidocr":
        return _run_rapidocr(img_bgr), engine
    return _run_tesseract(img_bgr, lang=lang), engine
