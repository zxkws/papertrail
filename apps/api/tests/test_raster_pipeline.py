"""位图流水线本身的自检：字体/字号能否原样认回、三种背景的擦除是否干净。

测试图在用例里现画，同时留一张「无文字的真值背景」用于衡量擦除质量——
对擦完的图再跑一次二值化是没有意义的，空白区域照样会被二值出噪声。
"""
import numpy as np
import pytest

from app import raster_ext

pytestmark = pytest.mark.skipif(
    not raster_ext.AVAILABLE, reason=f"未安装位图 extra：{raster_ext.IMPORT_ERROR}"
)

if raster_ext.AVAILABLE:
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    from app.raster import analyze, fonts, pipeline


def draw(text, font_name, size, bg=(250, 250, 252), fg=(28, 28, 30), pad=30):
    entry = fonts.find(font_name)
    if entry is None:
        pytest.skip(f"系统无字体 {font_name}")
    font = ImageFont.truetype(entry["path"], size, index=entry["index"])
    box = font.getbbox(text)
    image = Image.new("RGB", (box[2] + pad * 2, box[3] + pad * 2), bg)
    ImageDraw.Draw(image).text((pad, pad), text, font=font, fill=fg)
    bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    quad = [[pad - 4, pad - 4], [box[2] + pad + 4, pad - 4],
            [box[2] + pad + 4, box[3] + pad + 4], [pad - 4, box[3] + pad + 4]]
    return bgr, quad, entry


@pytest.mark.parametrize("font_name,size", [
    ("Helvetica Regular", 28),
    ("Helvetica Bold", 36),
    ("Times New Roman", 30),
    ("Courier New", 24),
])
def test_font_and_size_are_recovered(font_name, size):
    text = "Sample Text 12345"
    image, quad, entry = draw(text, font_name, size)
    info = pipeline.inspect_box(image, quad, text, match_fonts=True)
    top = info["font_matches"][0]
    assert top["family"] == entry["family"], f"认成了 {top['name']}"
    assert abs(top["size"] - size) <= 1
    assert top["iou"] > 0.8


def test_low_score_triggers_ocr_text_repair():
    """OCR 吞掉标点后的空格时，应当自己把原文补回来，而不是照着错的重绘。"""
    text = "Order No: SP-100238"
    image, quad, _ = draw(text, "Helvetica Regular", 32)
    info = pipeline.inspect_box(image, quad, text.replace(": ", ":"), match_fonts=True)
    assert info["suggest"].get("text") == text
    assert info["suggest"]["iou"] > 0.9


def backgrounds():
    """平坦、渐变、纹理三种背景，各返回 (带字图, 真值背景图, quad, 期望策略)。"""
    width, height = 520, 90
    text = "Background handling line"
    entry = fonts.find("Helvetica Regular") or fonts.default_font()
    font = ImageFont.truetype(entry["path"], 30, index=entry["index"])

    flat = np.full((height, width, 3), 244, np.uint8)

    ramp = np.linspace(205, 250, width).astype(np.uint8)
    gradient = np.dstack([np.tile(ramp, (height, 1))] * 3)

    rng = np.random.default_rng(11)
    texture = rng.integers(150, 200, (height // 4 + 1, width // 4 + 1, 3), dtype=np.uint8)
    texture = np.repeat(np.repeat(texture, 4, 0), 4, 1)[:height, :width]

    cases = []
    for base, expected in ((flat, "solid"), (gradient, "smooth"), (texture, "telea")):
        truth = base.copy()
        image = Image.fromarray(cv2.cvtColor(base.copy(), cv2.COLOR_BGR2RGB))
        ImageDraw.Draw(image).text((25, 25), text, font=font, fill=(20, 20, 22))
        with_text = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        box = font.getbbox(text)
        quad = [[21, 21], [box[2] + 29, 21], [box[2] + 29, box[3] + 29], [21, box[3] + 29]]
        cases.append((with_text, truth, quad, text, expected))
    return cases


@pytest.mark.parametrize("index", [0, 1, 2])
def test_erase_strategy_and_quality(index):
    with_text, truth, quad, text, expected = backgrounds()[index]
    info = pipeline.inspect_box(with_text, quad, text)
    assert info["suggest"]["erase"] == expected, (
        f"std={info['bg_std']} residual={info['bg_residual']}"
    )

    erased, _ = pipeline.apply_edits(
        with_text, [{"quad": quad, "original_text": text, "text": ""}]
    )
    region = info["region"]
    x0, y0, x1, y1 = region
    core = cv2.erode(
        analyze.text_mask(with_text, region, keep_bbox=info["bbox"]),
        np.ones((3, 3), np.uint8),
    ).astype(bool)
    assert core.sum() > 50

    got = erased[y0:y1, x0:x1].astype(np.float32)[core].mean()
    want = truth[y0:y1, x0:x1].astype(np.float32)[core].mean()
    assert abs(got - want) < 8, f"笔画位置仍有残留：擦除后 {got:.1f} / 真值 {want:.1f}"


def test_edit_leaves_the_rest_of_the_image_untouched():
    """等长替换不应碰到框外任何像素。

    （新文本比原文长时会正常溢出原框，那是预期行为，不在这条断言的范围内。）
    """
    text = "Quantity: 128 pcs"
    image, quad, _ = draw(text, "Helvetica Regular", 30)
    info = pipeline.inspect_box(image, quad, text)
    out, _ = pipeline.apply_edits(
        image, [{"quad": quad, "original_text": text, "text": "Quantity: 999 pcs"}]
    )
    x0, y0, x1, y1 = info["region"]
    before, after = image.copy(), out.copy()
    before[y0:y1, x0:x1] = 0
    after[y0:y1, x0:x1] = 0
    assert np.array_equal(before, after)


def test_latin_font_cannot_render_cjk_and_falls_back():
    """拉丁字体画中文只会出豆腐块，必须自动换成画得出的字体。"""
    latin = fonts.find("Courier New") or fonts.find("Liberation Mono")
    if latin is None:
        pytest.skip("系统里没有纯拉丁字体可用于此用例")
    assert fonts.covers(latin, "Invoice 2024") is True
    assert fonts.covers(latin, "发票号码") is False

    picked = fonts.first_covering("发票号码")
    assert picked is not None and fonts.covers(picked, "发票号码")

    image, quad, _ = draw("Invoice No 123", "Courier New", 28)
    out, used = pipeline.apply_edits(image, [{
        "quad": quad, "original_text": "Invoice No 123", "text": "发票号码 123",
        "font": latin["name"],
    }])
    assert used[0].get("font_fallback_from") == latin["name"]
    assert used[0]["font"] != latin["name"]
    # 落笔的字体必须真的画得出中文
    assert fonts.covers(fonts.find(used[0]["font"]), "发票号码")
