import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import NoReturn, get_type_hints

import fitz
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import pdf_engine
from app.main import app, configured_cors_origins, missing, upload

client = TestClient(app)
STYLE = {
    "font_family": "helv",
    "font_size_pt": 10,
    "color": "#153A22",
    "align": "left",
    "rotation": 0,
}


def sample(rotations=(0,)):
    d = fitz.open()
    for index, rotation in enumerate(rotations):
        p = d.new_page(width=360, height=300)
        p.set_rotation(rotation)
        p.insert_text((45, 55), f"Native text page {index}", fontsize=12)
        p.insert_text((45, 85), f"Delete me {index}", fontsize=10)
        p.draw_rect(fitz.Rect(35, 110, 170, 175), color=(0, 0.4, 0), width=2)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.clear_with(0xDD7744)
        p.insert_image(fitz.Rect(190, 110, 230, 150), pixmap=pix)
    data = d.tobytes()
    d.close()
    return data


def qin_qin_sample():
    document = fitz.open()
    page = document.new_page(width=360, height=300)
    page.insert_text((45, 55), "QIN QIN", fontsize=12, fontname="helv")
    data = document.tobytes()
    document.close()
    return data


def upload_and_draft(source):
    up = client.post(
        "/api/v1/documents", files={"file": ("fixture.pdf", source, "application/pdf")}
    )
    assert up.status_code == 201, up.text
    info = up.json()
    layouts = [
        client.get(f"/api/v1/documents/{info['document_id']}/pages/{i}/layout").json()
        for i in range(info["page_count"])
    ]
    draft = client.post(f"/api/v1/documents/{info['document_id']}/drafts").json()
    return info, layouts, draft


def save_export(draft, operations):
    saved = client.put(
        f"/api/v1/drafts/{draft['id']}/operations",
        json={"expected_revision": draft["revision"], "operations": operations},
    )
    assert saved.status_code == 200, saved.text
    reopened = client.get(f"/api/v1/drafts/{draft['id']}")
    assert reopened.status_code == 200
    stored = reopened.json()["operations"]
    assert len(stored) == len(operations)
    assert [(o["id"], o["type"], o["seq"]) for o in stored] == [
        (o["id"], o["type"], o["seq"]) for o in operations
    ]
    exported = client.post(f"/api/v1/drafts/{draft['id']}/exports")
    assert exported.status_code == 201, exported.text
    output = client.get(
        f"/api/v1/versions/{exported.json()['version_id']}/download"
    ).content
    return fitz.open(stream=output, filetype="pdf")


def test_export_download_uses_original_filename():
    uploaded = client.post(
        "/api/v1/documents",
        files={"file": ("original-statement.pdf", sample(), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    draft = client.post(
        f"/api/v1/documents/{uploaded.json()['document_id']}/drafts"
    )
    assert draft.status_code == 201, draft.text
    exported = client.post(f"/api/v1/drafts/{draft.json()['id']}/exports")
    assert exported.status_code == 201, exported.text

    downloaded = client.get(
        f"/api/v1/versions/{exported.json()['version_id']}/download"
    )

    assert downloaded.status_code == 200
    assert (
        downloaded.headers["content-disposition"]
        == 'attachment; filename="original-statement.pdf"'
    )


def test_upload_layout_revision_conflict_and_immutable_source():
    source = sample()
    digest = hashlib.sha256(source).hexdigest()
    info, layouts, draft = upload_and_draft(source)
    target = layouts[0]["elements"][0]
    ops = [
        {
            "id": "op1",
            "seq": 1,
            "type": "replace_text",
            "page_index": 0,
            "target_element_id": target["id"],
            "bbox": target["bbox"],
            "payload": {"text": "Updated workshop memo"},
            "style": STYLE,
        }
    ]
    assert (
        client.put(
            f"/api/v1/drafts/{draft['id']}/operations",
            json={"expected_revision": 0, "operations": ops},
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/drafts/{draft['id']}/operations",
            json={"expected_revision": 0, "operations": ops},
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/documents/{info['document_id']}").json()["source_sha256"]
        == digest
    )
    assert client.get(f"/api/v1/documents/{info['document_id']}/file").content == source


def test_replace_text_uses_expanded_bbox_without_shrinking_or_clipping():
    _, layouts, draft = upload_and_draft(qin_qin_sample())
    target = next(
        element for element in layouts[0]["elements"] if element["text"] == "QIN QIN"
    )
    original_bbox = target["bbox"]
    expanded_bbox = [
        original_bbox[0],
        original_bbox[1],
        original_bbox[0] + 80,
        original_bbox[3] + 4,
    ]
    operation = {
        "id": "replace-qin-qin",
        "seq": 1,
        "type": "replace_text",
        "page_index": 0,
        "target_element_id": target["id"],
        "bbox": expanded_bbox,
        "payload": {"text": "LI HUIQI"},
        "style": {
            **STYLE,
            "font_size_pt": target["font_size"],
            "color": target["color"],
        },
    }

    pdf = save_export(draft, [operation])
    exported_text = pdf[0].get_text()
    assert "LI HUIQI" in exported_text
    assert "QIN QIN" not in exported_text
    pdf.close()


@pytest.mark.parametrize(
    "kind", ["replace_text", "delete_text", "move_text", "add_text", "cover_region"]
)
def test_each_operation_real_api_save_export_reopen(kind):
    source = sample()
    info, layouts, draft = upload_and_draft(source)
    target = layouts[0]["elements"][0]
    common = {"id": f"op-{kind}", "seq": 1, "type": kind, "page_index": 0}
    if kind == "replace_text":
        op = {
            **common,
            "target_element_id": target["id"],
            "bbox": target["bbox"],
            "payload": {"text": "REPLACED VALUE"},
            "style": STYLE,
        }
    elif kind == "delete_text":
        op = {**common, "target_element_id": target["id"]}
    elif kind == "move_text":
        op = {**common, "target_element_id": target["id"], "bbox": [45, 190, 190, 210]}
    elif kind == "add_text":
        op = {
            **common,
            "created_element_id": "a:stable-api-test",
            "bbox": [45, 215, 180, 235],
            "payload": {"text": "ADDED VALUE"},
            "style": STYLE,
        }
    else:
        op = {**common, "bbox": target["bbox"], "payload": {"cover_color": "#FFFFFF"}}
    pdf = save_export(draft, [op])
    text = "\n".join(p.get_text() for p in pdf)
    if kind == "replace_text":
        assert "REPLACED VALUE" in text and target["text"] not in text
    elif kind == "delete_text":
        assert target["text"] not in text
    elif kind == "move_text":
        assert target["text"] in text and pdf[0].search_for(target["text"])[0].y0 > 170
    elif kind == "add_text":
        assert "ADDED VALUE" in text
    else:
        assert (
            target["text"] in text
        )  # visual cover leaves underlying content recoverable
    pdf.close()


@pytest.mark.parametrize("kind", ["add_text", "replace_text"])
def test_cjk_text_is_extractable_after_export(kind):
    source = sample()
    info, layouts, draft = upload_and_draft(source)
    target = layouts[0]["elements"][0]
    common = {
        "id": f"cjk-{kind}",
        "seq": 1,
        "type": kind,
        "page_index": 0,
        "bbox": [45, 200, 220, 235],
        "payload": {"text": "新增文字"},
        "style": STYLE,
    }
    if kind == "add_text":
        operation = {**common, "created_element_id": "a:cjk-export"}
    else:
        operation = {**common, "target_element_id": target["id"]}

    pdf = save_export(draft, [operation])
    assert "新增文字" in pdf[0].get_text()
    pdf.close()


def test_concurrent_saves_with_same_revision_allow_only_one_success():
    _, _, draft = upload_and_draft(sample())
    workers = 8
    barrier = Barrier(workers)

    def save_once(index: int) -> int:
        barrier.wait()
        response = client.put(
            f"/api/v1/drafts/{draft['id']}/operations",
            json={
                "expected_revision": 0,
                "operations": [
                    {
                        "id": f"cover-{index}",
                        "seq": 1,
                        "type": "cover_region",
                        "page_index": 0,
                        "bbox": [10, 10, 20, 20],
                    }
                ],
            },
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=workers) as executor:
        statuses = list(executor.map(save_once, range(workers)))

    assert statuses.count(200) == 1
    assert statuses.count(409) == workers - 1
    assert client.get(f"/api/v1/drafts/{draft['id']}").json()["revision"] == 1


def test_layout_is_cached_by_source_hash_and_page(tmp_path, monkeypatch):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(sample())
    pdf_engine.clear_layout_cache()
    real_open = pdf_engine.fitz.open
    calls = 0

    def counted_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pdf_engine.fitz, "open", counted_open)
    first = pdf_engine.parse_layout(str(source_path), "stable-hash", 0)
    second = pdf_engine.parse_layout(str(tmp_path / "missing.pdf"), "stable-hash", 0)

    assert second is first
    assert calls == 1


def test_oversized_upload_stops_reading_as_soon_as_limit_is_exceeded():
    class OversizedUpload:
        filename = "oversized.pdf"
        calls = 0
        chunk = b"x" * (1024 * 1024)

        async def read(self, size: int) -> bytes:
            assert size == 1024 * 1024
            self.calls += 1
            if self.calls > 101:
                raise AssertionError("oversized upload was read past the size limit")
            return self.chunk

    file = OversizedUpload()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(upload(file))

    assert exc_info.value.status_code == 413
    assert file.calls == 101


def test_cors_origins_are_configurable_and_default_to_vite(monkeypatch):
    monkeypatch.delenv("PDF_EDITOR_CORS_ORIGINS", raising=False)
    assert configured_cors_origins() == ["http://localhost:5173"]
    monkeypatch.setenv(
        "PDF_EDITOR_CORS_ORIGINS", "https://编辑.example, https://admin.example "
    )
    assert configured_cors_origins() == [
        "https://编辑.example",
        "https://admin.example",
    ]


def test_missing_is_typed_as_never_returning():
    assert get_type_hints(missing)["return"] is NoReturn


def test_redaction_preserves_images_and_vector_and_cover_is_visual_only():
    source = sample()
    info, layouts, draft = upload_and_draft(source)
    target = layouts[0]["elements"][0]
    src = fitz.open(stream=source, filetype="pdf")
    before_images = len(src[0].get_images(full=True))
    before_drawings = len(src[0].get_drawings())
    src.close()
    ops = [
        {
            "id": "replace",
            "seq": 1,
            "type": "replace_text",
            "page_index": 0,
            "target_element_id": target["id"],
            "bbox": target["bbox"],
            "payload": {"text": "SAFE DEMO"},
            "style": STYLE,
        },
        {
            "id": "cover",
            "seq": 2,
            "type": "cover_region",
            "page_index": 0,
            "bbox": layouts[0]["elements"][1]["bbox"],
            "payload": {"cover_color": "#FFFFFF"},
        },
    ]
    pdf = save_export(draft, ops)
    assert len(pdf[0].get_images(full=True)) == before_images
    assert len(pdf[0].get_drawings()) >= before_drawings
    assert layouts[0]["elements"][1]["text"] in pdf[0].get_text()
    pdf.close()


def test_all_supported_rotations_export():
    source = sample((0, 90, 180, 270))
    info, layouts, draft = upload_and_draft(source)
    ops = []
    for seq, (layout, rotation) in enumerate(zip(layouts, (0, 90, 180, 270)), 1):
        ops.append(
            {
                "id": f"rot-{rotation}",
                "seq": seq,
                "type": "add_text",
                "page_index": layout["page_index"],
                "created_element_id": f"a:rot-{rotation}",
                "bbox": [245, 185, 340, 225],
                "payload": {"text": f"R{rotation}"},
                "style": {**STYLE, "rotation": rotation},
            }
        )
    pdf = save_export(draft, ops)
    assert pdf.page_count == 4
    for i, rotation in enumerate((0, 90, 180, 270)):
        assert f"R{rotation}" in pdf[i].get_text()
    pdf.close()
