#!/usr/bin/env python3
"""位图改字的命令行入口：不开界面也能查看文字框、批量替换文字。

直接处理图片（PNG/JPG），不经过 PDF 那一层，适合批量套词、界面图多语言这类场景：

    python -m app.raster.cli in.png --list
    python -m app.raster.cli in.png -o out.png -r "SP-100238=>SP-777901"
    python -m app.raster.cli in.png -o out.png --edits edits.json

PDF 走 HTTP 接口（/raster/ocr、operation、导出），不用这里。
"""
from __future__ import annotations

import argparse
import json

import cv2

from . import ocr as O
from . import pipeline as P


def main(argv=None):
    ap = argparse.ArgumentParser(description="PNG 文字编辑")
    ap.add_argument("image")
    ap.add_argument("-o", "--out", help="输出路径，不给则只做识别")
    ap.add_argument("-r", "--replace", action="append", default=[],
                    metavar="OLD=>NEW", help="按 OCR 文本替换，可重复")
    ap.add_argument("--edits", help="从 JSON 文件读取完整编辑列表")
    ap.add_argument("--list", action="store_true", help="列出识别到的文字框")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出识别结果")
    ap.add_argument("--engine", default="auto", choices=["auto", "rapidocr", "tesseract"])
    ap.add_argument("--lang", default="eng", help="tesseract 语言包")
    ap.add_argument("--font", help="强制使用某个字体，默认自动匹配")
    ap.add_argument("--erase", default="auto", choices=["auto", "solid", "smooth", "telea", "ns"])
    ap.add_argument("--no-match", action="store_true", help="跳过字体匹配（更快）")
    args = ap.parse_args(argv)

    img = cv2.imread(args.image)
    if img is None:
        ap.error(f"无法读取图片：{args.image}")

    if args.edits:
        edits = json.load(open(args.edits))
        out, used = P.apply_edits(img, edits)
        cv2.imwrite(args.out, out)
        print(json.dumps(used, ensure_ascii=False, indent=2))
        return 0

    boxes, engine = O.detect(img, engine=args.engine, lang=args.lang)
    match = not args.no_match
    infos = [P.inspect_box(img, b["quad"], b["text"], match_fonts=match) | {"score": b["score"]}
             for b in boxes]

    if args.json:
        print(json.dumps(infos, ensure_ascii=False, indent=2))
    elif args.list or not args.out:
        print(f"引擎 {engine}，{len(infos)} 个文字框")
        for i, (b, info) in enumerate(zip(boxes, infos)):
            s = info.get("suggest", {})
            fix = f" 纠正为 {s['text']!r}" if s.get("text") else ""
            iou = f" iou={s['iou']}" if s.get("iou") is not None else ""
            print(f"[{i}] {b['score']:.3f} {b['text']!r}{fix} bbox={info['bbox']} "
                  f"字体={s.get('font')}{iou} 字号={s.get('size')} 色={info['text_color']} 擦除={s.get('erase')}")

    if not args.out:
        return 0

    rules = []
    for r in args.replace:
        if "=>" not in r:
            ap.error(f"替换规则要写成 OLD=>NEW：{r}")
        old, new = r.split("=>", 1)
        rules.append((old, new))

    edits, hits = [], 0
    for b, info in zip(boxes, infos):
        source = info["suggest"].get("text", b["text"])  # 字体匹配纠正过的原文
        text = source
        for old, new in rules:
            if old in text:
                text = text.replace(old, new)
        if text == source:
            continue
        hits += 1
        edits.append({
            "quad": b["quad"], "original_text": source, "text": text,
            "font": args.font or info["suggest"].get("font"),
            "size": None if args.font else info["suggest"].get("size"),
            "color": info["text_color"], "erase": args.erase,
        })

    if not edits:
        print("没有匹配到任何替换规则，输出未改动的原图")
    out, used = P.apply_edits(img, edits)
    cv2.imwrite(args.out, out)
    print(f"改写 {hits} 处 -> {args.out}")
    for u in used:
        print("   ", {k: u[k] for k in ("font", "size", "erase", "color") if k in u})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
