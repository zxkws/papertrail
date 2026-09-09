"""系统字体扫描与登记。"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import ImageFont

FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "C:/Windows/Fonts",
]

EXTS = (".ttf", ".otf", ".ttc")

# 字体匹配时优先试这些常见正文字体，避免遍历全部系统字体
PREFERRED = [
    "Helvetica", "Helvetica Neue", "Arial", "Arial Narrow", "Verdana", "Tahoma",
    "Times New Roman", "Georgia", "Courier New", "Menlo", "Monaco",
    "SF Pro Text", "SF Pro Display", ".SF NS", "Geneva", "Optima", "Futura",
    "PingFang SC", "Heiti SC", "Songti SC", "STHeiti", "Hiragino Sans GB",
    "Microsoft YaHei", "SimSun", "SimHei", "Noto Sans", "DejaVu Sans",
]


def _iter_font_files():
    seen = set()
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith(EXTS) and not fn.startswith("."):
                    p = os.path.join(root, fn)
                    if p not in seen:
                        seen.add(p)
                        yield p


@lru_cache(maxsize=1)
def registry() -> list[dict]:
    """[{path, index, family, style, name}]，.ttc 会展开其中每个 face。"""
    out = []
    for path in _iter_font_files():
        n = 12 if path.lower().endswith(".ttc") else 1
        for idx in range(n):
            try:
                f = ImageFont.truetype(path, 16, index=idx)
                family, style = f.getname()
            except Exception:
                break
            if not family:
                break
            out.append({
                "path": path,
                "index": idx,
                "family": family,
                "style": style or "Regular",
                "name": f"{family} {style or 'Regular'}".strip(),
            })
    out.sort(key=lambda e: (e["family"], e["style"]))
    return out


def find(name: str) -> dict | None:
    name = (name or "").strip().lower()
    if not name:
        return None
    reg = registry()
    for e in reg:
        if e["name"].lower() == name or e["path"].lower() == name:
            return e
    family = [e for e in reg if e["family"].lower() == name]
    if family:  # 只给家族名时优先 Regular，而不是按字母序撞上 Bold
        for e in family:
            if e["style"].lower() in ("regular", "book", "roman", "medium"):
                return e
        return family[0]
    return None


def candidates(limit_family_styles=True) -> list[dict]:
    """字体匹配候选：优先字体的全部字重 + 其它字体各取一个 Regular。"""
    reg = registry()
    pref_lower = [p.lower() for p in PREFERRED]
    picked, seen_family = [], set()
    for e in reg:
        if e["family"].lower() in pref_lower:
            picked.append(e)
            seen_family.add(e["family"])
    if not picked:  # 系统里一个常见字体都没有就退回全表
        picked = reg[:200]
    return picked


def default_font() -> dict | None:
    for name in ("Helvetica", "Arial", "DejaVu Sans", "PingFang SC"):
        e = find(name)
        if e:
            return e
    reg = registry()
    return reg[0] if reg else None
