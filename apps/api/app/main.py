from __future__ import annotations

import os
from typing import NoReturn

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .models import (
    RASTER_TYPES,
    NativeElement,
    Operation,
    RasterInspectRequest,
    RasterOcrRequest,
    RasterPreviewRequest,
    SaveOperations,
)
from .pdf_engine import export_pdf, image_to_pdf, parse_layout, sniff_image
from .reducer import ReducerError, canonical_reduce
from . import raster_ext, storage

app = FastAPI(title="Papertrail PDF API", version="0.1.0")


def configured_cors_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.getenv(
            "PDF_EDITOR_CORS_ORIGINS", "http://localhost:5173"
        ).split(",")
        if origin.strip()
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


def missing(exc: Exception) -> NoReturn:
    raise HTTPException(404, "resource not found") from exc


def raster_module():
    """位图能力是可选依赖，没装就明确告诉调用方，而不是 500。"""
    if not raster_ext.AVAILABLE:
        raise HTTPException(
            503,
            detail={
                "code": "RASTER_UNAVAILABLE",
                "hint": raster_ext.HINT,
                "error": raster_ext.IMPORT_ERROR,
            },
        )
    return raster_ext.raster


def page_meta(document_id: str, page_index: int) -> dict:
    try:
        meta = storage.document(document_id)
    except KeyError as exc:
        missing(exc)
    if page_index < 0 or page_index >= meta["page_count"]:
        raise HTTPException(404, "page not found")
    return meta


def require_raster_page(meta: dict, page_index: int) -> None:
    """矢量页栅格化会毁掉整页文字层，位图操作只允许落在扫描页上。"""
    layout = parse_layout(meta["source_path"], meta["source_sha256"], page_index)
    if layout.get("kind") != "raster":
        raise HTTPException(
            422,
            detail={
                "code": "RASTER_EDIT_ON_VECTOR_PAGE",
                "page_index": page_index,
                "hint": "矢量页请用 replace_text / add_text 等原生操作",
            },
        )


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "engine": fitz.VersionBind}


@app.post("/api/v1/documents", status_code=201)
async def upload(file: UploadFile = File(...)):
    max_size = 100 * 1024 * 1024
    chunks = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > max_size:
            raise HTTPException(413, "PDF exceeds 100 MB")
        chunks.append(chunk)
    data = b"".join(chunks)

    origin = None
    media_type = sniff_image(data)
    if media_type:
        # 截图/扫描照片直接收下，服务端包成单页 PDF 走同一套页面模型。
        # 要求用户先自己转 PDF 是没有意义的——转完依然只有像素。
        try:
            data, width_px, height_px = image_to_pdf(data)
        except Exception as exc:
            raise HTTPException(422, "damaged or unsupported image") from exc
        origin = {
            "data": b"".join(chunks),
            "media_type": media_type,
            "width_px": width_px,
            "height_px": height_px,
        }
    elif not data.startswith(b"%PDF"):
        raise HTTPException(415, "only PDF or PNG/JPEG/GIF/BMP/TIFF/WebP images are accepted")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages = doc.page_count
        doc.close()
    except Exception as exc:
        raise HTTPException(422, "damaged or unsupported PDF") from exc
    if pages > 300:
        raise HTTPException(413, "PDF exceeds 300 pages")
    meta = storage.save_document(
        file.filename or ("upload.png" if origin else "upload.pdf"), data, pages, origin
    )
    return {
        "document_id": meta["id"],
        "status": "READY",
        "upload_sha256": meta["source_sha256"],
        "page_count": pages,
        "origin": meta.get("origin"),
    }


@app.get("/api/v1/documents/{document_id}")
def get_document(document_id: str):
    try:
        return storage.document(document_id)
    except KeyError as exc:
        missing(exc)


@app.get("/api/v1/documents/{document_id}/file")
def source_file(document_id: str):
    try:
        meta = storage.document(document_id)
    except KeyError as exc:
        missing(exc)
    return FileResponse(
        meta["source_path"], media_type="application/pdf", filename=meta["filename"]
    )


@app.get("/api/v1/documents/{document_id}/pages/{page_index}/layout")
def layout(document_id: str, page_index: int):
    try:
        meta = storage.document(document_id)
    except KeyError as exc:
        missing(exc)
    if page_index < 0 or page_index >= meta["page_count"]:
        raise HTTPException(404, "page not found")
    return parse_layout(meta["source_path"], meta["source_sha256"], page_index)


@app.get("/api/v1/fonts")
def list_fonts():
    module = raster_module()
    from PIL import features

    return {
        "fonts": [
            {k: entry[k] for k in ("name", "family", "style", "path", "index")}
            for entry in module.fonts.registry()
        ],
        "default": module.fonts.default_font(),
        "ocr_engines": module.ocr.available_engines(),
        # 没有 raqm 就没有字距调整，重绘保真度和字体匹配分数都会明显下降，
        # 而且不会报错——放在这里好让部署环境一眼看出来
        "text_layout": {
            "kerning": bool(features.check("raqm")),
            "raqm": features.version("raqm"),
            "freetype": features.version("freetype2"),
        },
    }


@app.get("/api/v1/documents/{document_id}/pages/{page_index}/render.png")
def render_page_png(document_id: str, page_index: int, dpi: int = 144):
    """把页面渲染成 PNG。只用到 fitz，不需要位图 extra。"""
    if dpi < 36 or dpi > 600:
        raise HTTPException(422, "dpi must be between 36 and 600")
    meta = page_meta(document_id, page_index)
    doc = fitz.open(meta["source_path"])
    try:
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        data = pix.tobytes("png")
    finally:
        doc.close()
    return Response(data, media_type="image/png")


@app.post("/api/v1/documents/{document_id}/pages/{page_index}/raster/ocr")
def raster_ocr(document_id: str, page_index: int, body: RasterOcrRequest):
    module = raster_module()
    meta = page_meta(document_id, page_index)
    require_raster_page(meta, page_index)
    doc = fitz.open(meta["source_path"])
    try:
        return module.pages.ocr_page(
            doc[page_index], dpi=body.dpi, engine=body.engine, lang=body.lang,
            match_fonts=body.match_fonts, min_score=body.min_score,
        )
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        doc.close()


@app.post("/api/v1/documents/{document_id}/pages/{page_index}/raster/inspect")
def raster_inspect(document_id: str, page_index: int, body: RasterInspectRequest):
    module = raster_module()
    meta = page_meta(document_id, page_index)
    require_raster_page(meta, page_index)
    doc = fitz.open(meta["source_path"])
    try:
        return module.pages.inspect_quad(
            doc[page_index], [list(p) for p in body.quad], body.text,
            match_fonts=body.match_fonts, dpi=body.dpi,
        )
    finally:
        doc.close()


@app.post("/api/v1/documents/{document_id}/pages/{page_index}/raster/preview")
def raster_preview(document_id: str, page_index: int, body: RasterPreviewRequest):
    """按真实流水线渲染一条编辑并回传受影响区域，让画布预览与导出结果一致。"""
    module = raster_module()
    meta = page_meta(document_id, page_index)
    require_raster_page(meta, page_index)
    item = {
        "bbox": list(body.bbox),
        "quad": [list(p) for p in body.quad] if body.quad else None,
        "text": body.text,
        "style": body.style.model_dump() if body.style else {},
        "payload": {
            "original_text": body.original_text,
            "font": body.font,
            "erase": body.erase,
            "grow": body.grow,
        },
    }
    doc = fitz.open(meta["source_path"])
    try:
        return module.pages.preview_edit(doc[page_index], item, dpi=body.dpi)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        doc.close()


@app.post("/api/v1/documents/{document_id}/drafts", status_code=201)
def create_draft(document_id: str):
    try:
        return storage.create_draft(document_id)
    except KeyError as exc:
        missing(exc)


@app.get("/api/v1/drafts/{draft_id}")
def get_draft(draft_id: str):
    try:
        return storage.draft(draft_id)
    except KeyError as exc:
        missing(exc)


def draft_native(draft_value: dict) -> list[NativeElement]:
    meta = storage.document(draft_value["document_id"])
    result = []
    for page in range(meta["page_count"]):
        result.extend(
            NativeElement.model_validate(e)
            for e in parse_layout(meta["source_path"], meta["source_sha256"], page)[
                "elements"
            ]
        )
    return result


@app.put("/api/v1/drafts/{draft_id}/operations")
def save_operations(draft_id: str, body: SaveOperations):
    with storage.lock:
        try:
            value = storage.draft(draft_id)
        except KeyError as exc:
            missing(exc)
        try:
            meta = storage.document(value["document_id"])
        except KeyError as exc:
            missing(exc)
        for op in body.operations:
            if op.type in RASTER_TYPES:
                if op.page_index >= meta["page_count"]:
                    raise HTTPException(422, f"page {op.page_index} out of range")
                require_raster_page(meta, op.page_index)
        if body.expected_revision != value["revision"]:
            raise HTTPException(
                409,
                detail={
                    "code": "DRAFT_REVISION_CONFLICT",
                    "latest_revision": value["revision"],
                },
            )
        try:
            canonical = canonical_reduce(draft_native(value), body.operations)
        except ReducerError as exc:
            raise HTTPException(422, str(exc)) from exc
        value.update(
            revision=value["revision"] + 1,
            operations=[op.model_dump() for op in body.operations],
            canonical_hash=canonical["canonical_hash"],
        )
        storage.save_draft(value)
    return {
        "revision": value["revision"],
        "operation_set_hash": canonical["canonical_hash"],
    }


@app.post("/api/v1/drafts/{draft_id}/exports", status_code=201)
def create_export(draft_id: str):
    with storage.lock:
        try:
            value = storage.draft(draft_id)
            meta = storage.document(value["document_id"])
        except KeyError as exc:
            missing(exc)
    operations = [Operation.model_validate(op) for op in value["operations"]]
    try:
        canonical = canonical_reduce(draft_native(value), operations)
        temp = export_pdf(meta["source_path"], canonical)
    except (ReducerError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    version = storage.publish_export(
        meta["id"], temp, canonical["canonical_hash"], value["revision"]
    )
    return {
        "version_id": version["id"],
        "canonical_hash": canonical["canonical_hash"],
        "sha256": version["sha256"],
        "status": "READY",
    }


@app.get("/api/v1/versions/{version_id}/download.png")
def download_png(version_id: str, page_index: int = 0, dpi: int = 200):
    """把导出结果的某一页渲染成 PNG。上传的是图片时，用户要的是图片，不是 PDF。"""
    if dpi < 36 or dpi > 600:
        raise HTTPException(422, "dpi must be between 36 and 600")
    try:
        value = storage.version(version_id)
    except KeyError as exc:
        missing(exc)
    doc = fitz.open(value["path"])
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise HTTPException(404, "page not found")
        pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        data = pix.tobytes("png")
    finally:
        doc.close()
    return Response(
        data,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="edited-{version_id}.png"'},
    )


@app.get("/api/v1/versions/{version_id}/download")
def download(version_id: str):
    try:
        value = storage.version(version_id)
    except KeyError as exc:
        missing(exc)
    filename = value.get("filename")
    if not filename:
        try:
            filename = storage.document(value["document_id"])["filename"]
        except KeyError:
            filename = f"edited-{version_id}.pdf"
    return FileResponse(
        value["path"], media_type="application/pdf", filename=filename
    )
