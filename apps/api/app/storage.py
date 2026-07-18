from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from threading import RLock

configured_root = os.getenv("PDF_EDITOR_DATA")
ROOT = (
    Path(configured_root)
    if configured_root
    else Path(__file__).resolve().parents[3] / "data"
).resolve()
SOURCES = ROOT / "sources"
EXPORTS = ROOT / "exports"
META = ROOT / "meta"
for directory in (SOURCES, EXPORTS, META):
    directory.mkdir(parents=True, exist_ok=True)

lock = RLock()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> dict:
    if not path.exists():
        raise KeyError(path.stem)
    return json.loads(path.read_text("utf-8"))


def save_document(filename: str, data: bytes, page_count: int) -> dict:
    document_id = str(uuid.uuid4())
    digest = sha256_bytes(data)
    source_path = SOURCES / f"{document_id}.pdf"
    # Exclusive create makes the source immutable at the storage boundary.
    with source_path.open("xb") as f:
        f.write(data)
    source_path.chmod(0o444)
    meta = {
        "id": document_id,
        "filename": Path(filename).name,
        "source_sha256": digest,
        "size_bytes": len(data),
        "page_count": page_count,
        "source_path": str(source_path),
    }
    atomic_json(META / f"document-{document_id}.json", meta)
    return meta


def document(document_id: str) -> dict:
    return read_json(META / f"document-{document_id}.json")


def create_draft(document_id: str) -> dict:
    document(document_id)
    draft_id = str(uuid.uuid4())
    draft = {
        "id": draft_id,
        "document_id": document_id,
        "revision": 0,
        "operations": [],
        "canonical_hash": None,
    }
    atomic_json(META / f"draft-{draft_id}.json", draft)
    return draft


def draft(draft_id: str) -> dict:
    return read_json(META / f"draft-{draft_id}.json")


def save_draft(value: dict) -> None:
    atomic_json(META / f"draft-{value['id']}.json", value)


def publish_export(
    document_id: str, temp_path: Path, canonical_hash: str, draft_revision: int
) -> dict:
    source_document = document(document_id)
    version_id = str(uuid.uuid4())
    final_path = EXPORTS / f"{version_id}.pdf"
    fd, staged_path = tempfile.mkstemp(
        dir=EXPORTS, prefix=".tmp-export-", suffix=".pdf"
    )
    os.close(fd)
    try:
        shutil.copyfile(temp_path, staged_path)
        os.replace(staged_path, final_path)
    finally:
        if os.path.exists(staged_path):
            os.unlink(staged_path)
        temp_path.unlink(missing_ok=True)
    digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
    version = {
        "id": version_id,
        "document_id": document_id,
        "filename": source_document["filename"],
        "path": str(final_path),
        "sha256": digest,
        "canonical_hash": canonical_hash,
        "draft_revision": draft_revision,
    }
    atomic_json(META / f"version-{version_id}.json", version)
    return version


def version(version_id: str) -> dict:
    return read_json(META / f"version-{version_id}.json")
