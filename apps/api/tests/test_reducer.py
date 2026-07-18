import pytest
from app.models import NativeElement, Operation, TextStyle
from app.reducer import ReducerError, canonical_reduce

native = NativeElement(
    id="n:hash:p0:e0000",
    page_index=0,
    text="Old",
    bbox=(10, 10, 80, 30),
    font_name="Helvetica",
    font_size=12,
    color="#000000",
)


def op(seq, type, **kw):
    return Operation(id=f"op{seq}", seq=seq, type=type, page_index=0, **kw)


def test_native_replace_move():
    out = canonical_reduce(
        [native],
        [
            op(
                1,
                "replace_text",
                target_element_id=native.id,
                payload={"text": "New"},
                style=TextStyle(),
            ),
            op(2, "move_text", target_element_id=native.id, bbox=(20, 20, 90, 40)),
        ],
    )
    assert (
        len(out["redactions"]) == 1
        and out["inserts"][0]["text"] == "New"
        and out["inserts"][0]["bbox"] == [20, 20, 90, 40]
    )


def test_native_replace_delete():
    out = canonical_reduce(
        [native],
        [
            op(1, "replace_text", target_element_id=native.id, payload={"text": "New"}),
            op(2, "delete_text", target_element_id=native.id),
        ],
    )
    assert len(out["redactions"]) == 1 and out["inserts"] == []


def test_native_move_move():
    out = canonical_reduce(
        [native],
        [
            op(1, "move_text", target_element_id=native.id, bbox=(15, 15, 85, 35)),
            op(2, "move_text", target_element_id=native.id, bbox=(25, 25, 95, 45)),
        ],
    )
    assert len(out["inserts"]) == 1 and out["inserts"][0]["bbox"] == [25, 25, 95, 45]


def test_added_move_and_delete():
    add = op(
        1,
        "add_text",
        created_element_id="a:abc",
        bbox=(10, 10, 100, 40),
        payload={"text": "Added"},
    )
    moved = canonical_reduce(
        [], [add, op(2, "move_text", target_element_id="a:abc", bbox=(30, 30, 120, 60))]
    )
    assert moved["redactions"] == [] and moved["inserts"][0]["bbox"] == [
        30,
        30,
        120,
        60,
    ]
    deleted = canonical_reduce(
        [], [add, op(2, "delete_text", target_element_id="a:abc")]
    )
    assert deleted["redactions"] == [] and deleted["inserts"] == []


def test_operation_after_delete_rejected():
    with pytest.raises(ReducerError, match="TARGET_DELETED"):
        canonical_reduce(
            [native],
            [
                op(1, "delete_text", target_element_id=native.id),
                op(2, "move_text", target_element_id=native.id, bbox=(1, 1, 2, 2)),
            ],
        )


@pytest.mark.parametrize("kind", ["replace_text", "delete_text", "move_text"])
def test_cover_only_native_target_rejected(kind):
    protected = native.model_copy(update={"editability": "cover_only"})
    kwargs = {"target_element_id": protected.id}
    if kind == "move_text":
        kwargs["bbox"] = (20, 20, 90, 40)
    if kind == "replace_text":
        kwargs["payload"] = {"text": "nope"}
    with pytest.raises(ReducerError, match="TARGET_COVER_ONLY"):
        canonical_reduce([protected], [op(1, kind, **kwargs)])
