from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .models import NativeElement, Operation, TextStyle

ENGINE_CONTRACT = "pymupdf-1.26.3/redact-text-only/v1"


class ReducerError(ValueError):
    pass


def _style(element: NativeElement) -> dict:
    return TextStyle(font_family="helv", font_size_pt=element.font_size, color=element.color).model_dump()


def canonical_reduce(native_elements: list[NativeElement], operations: list[Operation]) -> dict:
    ids, seqs = set(), set()
    for op in operations:
        if op.id in ids or op.seq in seqs:
            raise ReducerError("duplicate operation id or seq")
        ids.add(op.id); seqs.add(op.seq)

    states = {
        e.id: {
            "kind": "native", "page_index": e.page_index, "original_bbox": list(e.bbox),
            "bbox": list(e.bbox), "text": e.text, "style": _style(e), "deleted": False,
            "changed": False, "first_seq": 0, "z_order": 0, "editability": e.editability,
        } for e in native_elements
    }
    covers: list[dict] = []

    for op in sorted(operations, key=lambda item: (item.seq, item.id)):
        if op.type == "cover_region":
            covers.append({
                "kind": "VISUAL_COVER", "page_index": op.page_index, "bbox": list(op.bbox),
                "color": op.payload.get("cover_color", "#FFFFFF"), "z_order": op.z_order or op.seq,
                "seq": op.seq, "id": op.id, "not_for_secure_redaction": True,
            })
            continue
        if op.type == "add_text":
            target = op.created_element_id
            if target in states:
                raise ReducerError("ADDED_ID_DUPLICATE")
            states[target] = {
                "kind": "added", "page_index": op.page_index, "original_bbox": None,
                "bbox": list(op.bbox), "text": str(op.payload.get("text", "")),
                "style": (op.style or TextStyle()).model_dump(), "deleted": False,
                "changed": True, "first_seq": op.seq, "z_order": op.z_order or op.seq,
                "editability": "native",
            }
            continue
        target = op.target_element_id
        if target not in states:
            raise ReducerError("TARGET_NOT_FOUND")
        state = states[target]
        if state["page_index"] != op.page_index:
            raise ReducerError("TARGET_PAGE_MISMATCH")
        if state["kind"] == "native" and state["editability"] != "native":
            raise ReducerError("TARGET_COVER_ONLY")
        if state["deleted"]:
            raise ReducerError("TARGET_DELETED")
        if not state["first_seq"]:
            state["first_seq"] = op.seq
        state["changed"] = True
        if op.type == "delete_text":
            state["deleted"] = True
        elif op.type == "move_text":
            if not op.bbox:
                raise ReducerError("move_text requires bbox")
            state["bbox"] = list(op.bbox)
        elif op.type == "replace_text":
            if "text" in op.payload:
                state["text"] = str(op.payload["text"])
            if op.bbox:
                state["bbox"] = list(op.bbox)
            if op.style:
                state["style"] = op.style.model_dump()

    redactions, inserts = [], []
    for target, state in states.items():
        if state["kind"] == "native" and state["changed"]:
            redactions.append({"kind": "TEXT_REDACT", "page_index": state["page_index"], "bbox": state["original_bbox"], "target_id": target})
        if state["changed"] and not state["deleted"]:
            inserts.append({
                "kind": "TEXT_INSERT", "page_index": state["page_index"], "bbox": state["bbox"],
                "target_id": target, "text": state["text"], "style": state["style"],
                "z_order": state["z_order"], "first_seq": state["first_seq"],
            })
    redactions.sort(key=lambda x: (x["page_index"], x["bbox"][1], x["bbox"][0], x["target_id"]))
    covers.sort(key=lambda x: (x["page_index"], x["z_order"], x["seq"], x["id"]))
    inserts.sort(key=lambda x: (x["page_index"], x["z_order"], x["first_seq"], x["target_id"]))
    result = {"schema_version": "v1", "engine_contract_version": ENGINE_CONTRACT, "redactions": redactions, "covers": covers, "inserts": inserts}
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    result["canonical_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    return result
