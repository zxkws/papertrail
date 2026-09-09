"""位图页（扫描件）编辑路径的端到端测试。

覆盖三件事：页面类型判定是否可靠、改字后能不能被 OCR 读回、
矢量页有没有被这条路径误伤。
"""
import hashlib
import io

import cv2
import fitz
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import pdf_engine, raster_ext
from app.main import app
from app.models import Operation
from app.reducer import ReducerError, canonical_reduce

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not raster_ext.AVAILABLE, reason=f"未安装位图 extra：{raster_ext.IMPORT_ERROR}"
)

WIDTH, HEIGHT = 420, 260
LINES = [("Invoice No: INV-20240513", 40), ("Amount Due: 128.00", 90)]


def scanned_pdf(dpi=150, invisible_layer=False):
    """造一张「扫描件」：文字先画进位图，整页只有这张图。"""
    from PIL import Image, ImageDraw, ImageFont

    from app.raster import fonts as raster_fonts

    scale = dpi / 72
    entry = raster_fonts.find("Helvetica Regular") or raster_fonts.default_font()
    image = Image.new("RGB", (int(WIDTH * scale), int(HEIGHT * scale)), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    for text, top in LINES:
        font = ImageFont.truetype(entry["path"], int(16 * scale), index=entry["index"])
        draw.text((int(30 * scale), int(top * scale)), text, font=font, fill=(24, 24, 26))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    document = fitz.open()
    page = document.new_page(width=WIDTH, height=HEIGHT)
    page.insert_image(page.rect, stream=buffer.getvalue())
    if invisible_layer:  # 模拟 ocrmypdf 盖的隐形文字层
        for text, top in LINES:
            page.insert_text((30, top + 12), text, fontsize=16, render_mode=3)
    data = document.tobytes()
    document.close()
    return data


def vector_pdf():
    document = fitz.open()
    page = document.new_page(width=WIDTH, height=HEIGHT)
    page.insert_text((30, 60), "Vector invoice line", fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def page_text(pdf_bytes, page_index=0, dpi=200):
    """把导出结果渲染出来重新 OCR，验证改字是否真的落在像素上。"""
    from app.raster import ocr as raster_ocr
    from app.raster.pages import render_page

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        image, _ = render_page(document[page_index], dpi)
    finally:
        document.close()
    boxes, _ = raster_ocr.detect(image)
    return [b["text"] for b in boxes]


def upload(data):
    response = client.post(
        "/api/v1/documents", files={"file": ("scan.pdf", data, "application/pdf")}
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


# ---------- 页面类型判定 ----------

def test_page_kind_distinguishes_scan_from_vector():
    for data, expected in ((scanned_pdf(), "raster"), (vector_pdf(), "vector")):
        document = fitz.open(stream=data, filetype="pdf")
        try:
            assert pdf_engine.page_kind(document[0]) == expected
        finally:
            document.close()


def test_invisible_ocr_layer_still_counts_as_scan():
    """带隐形文字层的「可搜索 PDF」仍然是扫描页——那层改不动一个像素。"""
    document = fitz.open(stream=scanned_pdf(invisible_layer=True), filetype="pdf")
    try:
        page = document[0]
        assert page.get_text("text").strip()          # 有文字，但是不可见的
        assert pdf_engine.visible_text_chars(page) == 0
        assert pdf_engine.page_kind(page) == "raster"
    finally:
        document.close()


def test_layout_reports_page_kind():
    document_id = upload(scanned_pdf())
    layout = client.get(f"/api/v1/documents/{document_id}/pages/0/layout").json()
    assert layout["kind"] == "raster"
    assert layout["elements"] == []


# ---------- 识别与分析 ----------

def test_raster_ocr_returns_point_coordinates():
    document_id = upload(scanned_pdf())
    response = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/ocr",
        json={"dpi": 200, "match_fonts": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] >= 2
    texts = " ".join(box["text"] for box in body["boxes"])
    assert "INV-20240513" in texts

    box = body["boxes"][0]
    # 坐标必须落在页面尺寸内（点），而不是渲染像素
    assert 0 <= box["bbox"][0] < WIDTH and 0 <= box["bbox"][3] <= HEIGHT
    assert box["text_color"].startswith("#")
    assert box["suggest"]["font_size_pt"] == pytest.approx(16, abs=1.5)


def test_raster_ocr_rejected_on_vector_page():
    document_id = upload(vector_pdf())
    response = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/ocr", json={}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RASTER_EDIT_ON_VECTOR_PAGE"


# ---------- 端到端改字 ----------

def test_export_replaces_text_on_scanned_page():
    document_id = upload(scanned_pdf())
    ocr = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/ocr", json={"dpi": 200}
    ).json()
    target = next(b for b in ocr["boxes"] if "INV-" in b["text"])
    suggest = target["suggest"]

    draft = client.post(f"/api/v1/documents/{document_id}/drafts").json()
    operation = {
        "id": "op-1", "seq": 1, "type": "raster_replace_text", "page_index": 0,
        "created_element_id": "r:p0:inv", "bbox": target["bbox"],
        "payload": {
            "text": "Invoice No: INV-99887766",
            "original_text": suggest.get("text", target["text"]),
            "font": suggest.get("font"), "quad": target["quad"], "erase": "auto",
        },
        "style": {
            "font_family": suggest.get("font", "helv"),
            "font_size_pt": suggest["font_size_pt"],
            "color": target["text_color"], "align": "left", "rotation": 0,
        },
    }
    saved = client.put(
        f"/api/v1/drafts/{draft['id']}/operations",
        json={"expected_revision": draft["revision"], "operations": [operation]},
    )
    assert saved.status_code == 200, saved.text

    export = client.post(f"/api/v1/drafts/{draft['id']}/exports")
    assert export.status_code == 201, export.text
    download = client.get(
        f"/api/v1/versions/{export.json()['version_id']}/download"
    )
    assert download.status_code == 200

    texts = page_text(download.content)
    assert any("99887766" in t for t in texts), texts
    assert not any("20240513" in t for t in texts), texts
    assert any("128.00" in t for t in texts), "未编辑的那行不该受影响"


def test_raster_operation_rejected_on_vector_page():
    document_id = upload(vector_pdf())
    draft = client.post(f"/api/v1/documents/{document_id}/drafts").json()
    response = client.put(
        f"/api/v1/drafts/{draft['id']}/operations",
        json={
            "expected_revision": draft["revision"],
            "operations": [{
                "id": "op-1", "seq": 1, "type": "raster_replace_text", "page_index": 0,
                "created_element_id": "r:p0:a", "bbox": [10, 10, 200, 40],
                "payload": {"text": "nope"},
            }],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RASTER_EDIT_ON_VECTOR_PAGE"


def test_export_refuses_raster_edit_on_vector_page():
    """即使绕过保存时的校验，导出阶段也必须再挡一次。"""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(vector_pdf())
        path = handle.name
    canonical = {
        "redactions": [], "covers": [], "inserts": [],
        "raster_edits": [{
            "kind": "RASTER_TEXT", "page_index": 0, "bbox": [10, 10, 200, 40],
            "quad": None, "text": "x", "style": {}, "payload": {},
            "target_id": "r:p0:a", "first_seq": 1, "seq": 1,
        }],
    }
    with pytest.raises(ValueError, match="RASTER_EDIT_ON_VECTOR_PAGE"):
        pdf_engine.export_pdf(path, canonical)


# ---------- reducer ----------

def make_op(**kwargs):
    base = {
        "id": "op", "seq": 1, "type": "raster_replace_text", "page_index": 0,
        "created_element_id": "r:p0:a", "bbox": (10, 10, 200, 40), "payload": {},
    }
    base.update(kwargs)
    return Operation(**base)


def test_reducer_last_write_wins_per_box():
    canonical = canonical_reduce([], [
        make_op(id="a", seq=1, payload={"text": "first"}),
        make_op(id="b", seq=2, payload={"text": "second"}),
    ])
    assert len(canonical["raster_edits"]) == 1
    edit = canonical["raster_edits"][0]
    assert edit["text"] == "second"
    assert edit["first_seq"] == 1  # 排序仍按首次出现，避免改一次就跳序


def test_reducer_delete_becomes_empty_text():
    canonical = canonical_reduce([], [make_op(type="raster_delete_text")])
    assert canonical["raster_edits"][0]["text"] == ""


def test_reducer_rejects_page_switch_and_bad_quad():
    with pytest.raises(ReducerError, match="RASTER_PAGE_MISMATCH"):
        canonical_reduce([], [
            make_op(id="a", seq=1),
            make_op(id="b", seq=2, page_index=1),
        ])
    with pytest.raises(ReducerError, match="RASTER_QUAD_SHAPE"):
        canonical_reduce([], [make_op(payload={"quad": [[0, 0], [1, 1]]})])


def test_fonts_endpoint_reports_text_layout():
    """暴露排版能力：缺 raqm 时 Pillow 不做字距调整，重绘保真度和字体匹配都会
    悄悄下降且不报错，所以要能从接口上直接看出来。"""
    body = client.get("/api/v1/fonts").json()
    layout = body["text_layout"]
    assert set(layout) == {"kerning", "raqm", "freetype"}
    assert isinstance(layout["kerning"], bool)
    assert layout["freetype"]  # 没有 freetype 的话根本画不了字


def test_render_png_endpoint_needs_no_raster_extra():
    document_id = upload(vector_pdf())
    response = client.get(f"/api/v1/documents/{document_id}/pages/0/render.png?dpi=96")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


# ---------- 未安装可选依赖时的降级 ----------

def test_endpoints_degrade_when_extra_missing(monkeypatch):
    monkeypatch.setattr(raster_ext, "AVAILABLE", False)
    monkeypatch.setattr(raster_ext, "IMPORT_ERROR", "ModuleNotFoundError: No module named 'cv2'")
    document_id = upload(scanned_pdf())

    response = client.get("/api/v1/fonts")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RASTER_UNAVAILABLE"

    response = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/ocr", json={}
    )
    assert response.status_code == 503

    # 页面渲染只用 fitz，不该跟着一起挂
    assert client.get(
        f"/api/v1/documents/{document_id}/pages/0/render.png"
    ).status_code == 200


def test_export_reports_missing_extra(monkeypatch, tmp_path):
    monkeypatch.setattr(raster_ext, "AVAILABLE", False)
    path = tmp_path / "scan.pdf"
    path.write_bytes(scanned_pdf())
    canonical = {
        "redactions": [], "covers": [], "inserts": [],
        "raster_edits": [{
            "kind": "RASTER_TEXT", "page_index": 0, "bbox": [10, 10, 200, 40],
            "quad": None, "text": "x", "style": {}, "payload": {},
            "target_id": "r:p0:a", "first_seq": 1, "seq": 1,
        }],
    }
    with pytest.raises(ValueError, match="RASTER_UNAVAILABLE"):
        pdf_engine.export_pdf(str(path), canonical)


# ---------- 图片直接上传 ----------

def png_bytes(width=900, height=300):
    from PIL import Image, ImageDraw, ImageFont

    from app.raster import fonts as raster_fonts

    entry = raster_fonts.find("Helvetica Regular") or raster_fonts.default_font()
    image = Image.new("RGB", (width, height), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(entry["path"], 44, index=entry["index"])
    draw.text((60, 60), "Screenshot No: SC-20240513", font=font, fill=(24, 24, 26))
    draw.text((60, 160), "Status: PENDING", font=font, fill=(24, 24, 26))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_png_upload_becomes_a_raster_page():
    """截图直接传，服务端包成单页 PDF；要求用户先自己转 PDF 是没有意义的。"""
    raw = png_bytes()
    response = client.post(
        "/api/v1/documents", files={"file": ("shot.png", raw, "image/png")}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["page_count"] == 1
    assert body["origin"]["kind"] == "image"
    assert body["origin"]["media_type"] == "image/png"
    assert (body["origin"]["width_px"], body["origin"]["height_px"]) == (900, 300)
    # 原图另存并单独记哈希，交上来的字节不被改动
    assert body["origin"]["sha256"] == hashlib.sha256(raw).hexdigest()

    layout = client.get(
        f"/api/v1/documents/{body['document_id']}/pages/0/layout"
    ).json()
    assert layout["kind"] == "raster"


def test_png_page_renders_back_at_original_pixel_size():
    """页面尺寸按 200dpi 折算，渲染回来必须与原图逐像素同尺寸，改字不损失清晰度。"""
    document_id = upload(png_bytes(1536, 1024))
    meta = client.get(f"/api/v1/documents/{document_id}").json()
    document = fitz.open(meta["source_path"])
    try:
        from app.raster.pages import render_page

        image, _ = render_page(document[0], 200)
    finally:
        document.close()
    assert (image.shape[1], image.shape[0]) == (1536, 1024)


def test_png_upload_full_edit_and_png_download():
    document_id = upload(png_bytes())
    ocr = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/ocr", json={"dpi": 200}
    ).json()
    target = next(b for b in ocr["boxes"] if "SC-" in b["text"])

    draft = client.post(f"/api/v1/documents/{document_id}/drafts").json()
    client.put(
        f"/api/v1/drafts/{draft['id']}/operations",
        json={
            "expected_revision": draft["revision"],
            "operations": [{
                "id": "op-1", "seq": 1, "type": "raster_replace_text", "page_index": 0,
                "created_element_id": "r:p0:a", "bbox": target["bbox"],
                "payload": {
                    "text": "Screenshot No: SC-99887766",
                    "original_text": target["suggest"].get("text", target["text"]),
                    "font": target["suggest"].get("font"), "quad": target["quad"],
                },
                "style": {
                    "font_family": target["suggest"].get("font", "helv"),
                    "font_size_pt": target["suggest"]["font_size_pt"],
                    "color": target["text_color"], "align": "left", "rotation": 0,
                },
            }],
        },
    ).raise_for_status()
    version = client.post(f"/api/v1/drafts/{draft['id']}/exports").json()

    png = client.get(f"/api/v1/versions/{version['version_id']}/download.png")
    assert png.status_code == 200
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    # 直接量像素：fitz 打开 PNG 时会按 96dpi 折算成点，量 rect 会得到 675x225
    pixmap = fitz.Pixmap(png.content)
    assert (pixmap.width, pixmap.height) == (900, 300)

    texts = page_text(client.get(
        f"/api/v1/versions/{version['version_id']}/download"
    ).content)
    assert any("99887766" in t for t in texts), texts
    assert any("PENDING" in t for t in texts), "未编辑的行不该受影响"


def test_non_image_non_pdf_is_rejected():
    response = client.post(
        "/api/v1/documents", files={"file": ("a.txt", b"hello world", "text/plain")}
    )
    assert response.status_code == 415


# ---------- 服务端真实预览 ----------

def test_preview_matches_the_exported_pixels():
    """画布预览必须与导出结果一致——预览走的就是同一条重绘流水线。"""
    document_id = upload(scanned_pdf())
    ocr = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/ocr", json={"dpi": 200}
    ).json()
    target = next(b for b in ocr["boxes"] if "INV-" in b["text"])
    suggest = target["suggest"]
    new_text = "Invoice No: INV-99887766"
    style = {
        "font_family": suggest.get("font", "helv"),
        "font_size_pt": suggest["font_size_pt"],
        "color": target["text_color"],
        "align": "left",
        "rotation": 0,
    }

    preview = client.post(
        f"/api/v1/documents/{document_id}/pages/0/raster/preview",
        json={
            "bbox": target["bbox"], "quad": target["quad"], "text": new_text,
            "original_text": suggest.get("text", target["text"]),
            "font": suggest.get("font"), "style": style, "dpi": 200,
        },
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["image"].startswith("data:image/png;base64,")
    assert body["used"]["font"] == suggest.get("font")

    import base64 as b64

    crop = cv2.imdecode(
        np.frombuffer(b64.b64decode(body["image"].split(",", 1)[1]), np.uint8),
        cv2.IMREAD_COLOR,
    )
    # 预览里应当已经是新文字，而不是原文
    texts = raster_ocr_texts(crop)
    assert any("99887766" in t for t in texts), texts

    # 同一条编辑走导出，落到同一块区域的像素应当与预览一致
    draft = client.post(f"/api/v1/documents/{document_id}/drafts").json()
    client.put(
        f"/api/v1/drafts/{draft['id']}/operations",
        json={"expected_revision": draft["revision"], "operations": [{
            "id": "op-1", "seq": 1, "type": "raster_replace_text", "page_index": 0,
            "created_element_id": "r:p0:inv", "bbox": target["bbox"],
            "payload": {
                "text": new_text, "original_text": suggest.get("text", target["text"]),
                "font": suggest.get("font"), "quad": target["quad"],
            },
            "style": style,
        }]},
    ).raise_for_status()
    version = client.post(f"/api/v1/drafts/{draft['id']}/exports").json()
    exported = client.get(
        f"/api/v1/versions/{version['version_id']}/download"
    ).content

    from app.raster.pages import render_page

    document = fitz.open(stream=exported, filetype="pdf")
    try:
        full, scale = render_page(document[0], 200)
    finally:
        document.close()
    x0, y0, x1, y1 = (int(round(v * scale)) for v in body["region_pt"])
    same_region = full[y0:y1, x0:x1]
    assert same_region.shape == crop.shape, (same_region.shape, crop.shape)
    assert np.array_equal(same_region, crop), "预览与导出结果不是同一批像素"


def raster_ocr_texts(image):
    from app.raster import ocr as raster_ocr

    return [b["text"] for b in raster_ocr.detect(image)[0]]
