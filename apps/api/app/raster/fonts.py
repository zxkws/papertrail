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
    # Linux 容器里的常备替身：Liberation 与 Arial/Times/Courier 度量兼容，
    # Noto CJK 负责中日韩——少了它们，服务端就只能拿 DejaVu 硬顶
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "Noto Serif", "Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans Mono",
]

# 「缺字」基准码位：U+FFFF 是 Unicode 非字符，任何字体都不会给它配字形；
# 私用区 U+E000 再兜一层——但不能只用它，私用区字体恰恰给那里配了真字形。
_NO_GLYPH = ("\uffff", "\ue000")


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


def covers(entry, text: str, sample: int = 8) -> bool:
    """这个字体能不能把 text 画出来。

    匹配是按形状打分的，如果候选里没有能渲染中文的字体，
    照样会挑出一个拉丁字体，然后中文重绘出一排豆腐块。
    判断方式是拿私用区码位的渲染结果当「缺字」基准去比对——
    不引额外依赖，也不用解析 cmap。
    """
    from PIL import Image, ImageDraw, ImageFont

    chars = [c for c in dict.fromkeys(text) if not c.isspace()][:sample]
    if not chars:
        return True
    try:
        font = ImageFont.truetype(entry["path"], 24, index=entry.get("index", 0))
    except Exception:
        return False

    def shape(ch):
        canvas = Image.new("L", (48, 48), 0)
        ImageDraw.Draw(canvas).text((4, 4), ch, font=font, fill=255)
        return canvas.tobytes()

    try:
        # 系统字体里难免有 FreeType 渲染不了的（实测有字体会直接抛 stack overflow），
        # 一个坏字体不该让整次匹配或回退搜索崩掉——当作「画不出」跳过即可
        # 缺字时有的字体画空白，有的画方框（.notdef），所以两类基准都要比
        baselines = {Image.new("L", (48, 48), 0).tobytes()}
        baselines.update(shape(c) for c in _NO_GLYPH)
        return all(shape(ch) not in baselines for ch in chars)
    except Exception:
        return False


def first_covering(text: str) -> dict | None:
    """按候选字体、再按全表的顺序，找第一个画得出 text 的字体。"""
    seen = set()
    for entry in candidates() + registry():
        key = (entry["path"], entry["index"])
        if key in seen:
            continue
        seen.add(key)
        if covers(entry, text):
            return entry
    return None


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
