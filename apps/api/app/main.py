from __future__ import annotations

import os
from typing import NoReturn

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .models import NativeElement, Operation, SaveOperations
from .pdf_engine import export_pdf, parse_layout
from .reducer import ReducerError, canonical_reduce
from . import storage

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
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "only valid PDF files are accepted")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        pages = doc.page_count
        doc.close()
    except Exception as exc:
        raise HTTPException(422, "damaged or unsupported PDF") from exc
    if pages > 300:
        raise HTTPException(413, "PDF exceeds 300 pages")
    meta = storage.save_document(file.filename or "upload.pdf", data, pages)
    return {
        "document_id": meta["id"],
        "status": "READY",
        "upload_sha256": meta["source_sha256"],
        "page_count": pages,
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


@app.get("/api/v1/versions/{version_id}/download")
def download(version_id: str):
    try:
        value = storage.version(version_id)
    except KeyError as exc:
        missing(exc)
    return FileResponse(
        value["path"], media_type="application/pdf", filename=f"edited-{version_id}.pdf"
    )
