import { describe, expect, it } from "vitest";
import { deriveElements } from "./operationState";
import type { Layout, Operation } from "../types";
const layout: Layout = {
  page_index: 0,
  width_pt: 500,
  height_pt: 500,
  rotation: 0,
  scan_likelihood: 0,
  elements: [
    {
      id: "n:1",
      page_index: 0,
      text: "one",
      bbox: [1, 2, 30, 12],
      font_name: "helv",
      font_size: 10,
      color: "#000000",
      editability: "native",
    },
    {
      id: "n:2",
      page_index: 0,
      text: "two",
      bbox: [1, 20, 30, 30],
      font_name: "helv",
      font_size: 10,
      color: "#000000",
      editability: "native",
    },
  ],
};
const base = {
  font_family: "helv",
  font_size_pt: 12,
  color: "#123456",
  align: "left" as const,
  rotation: 0 as const,
};
describe("operation overlay state", () => {
  it("keeps a stable added id through replace and move", () => {
    const ops: Operation[] = [
      {
        id: "1",
        seq: 1,
        type: "add_text",
        page_index: 0,
        created_element_id: "a:stable",
        bbox: [10, 10, 80, 30],
        payload: { text: "new" },
        style: base,
      },
      {
        id: "2",
        seq: 2,
        type: "replace_text",
        page_index: 0,
        target_element_id: "a:stable",
        payload: { text: "changed" },
      },
      {
        id: "3",
        seq: 3,
        type: "move_text",
        page_index: 0,
        target_element_id: "a:stable",
        bbox: [30, 40, 100, 60],
      },
    ];
    expect(
      deriveElements([layout], ops).find((e) => e.id === "a:stable"),
    ).toMatchObject({
      text: "changed",
      bbox: [30, 40, 100, 60],
      deleted: false,
    });
  });
  it("does not leak another selection text and marks deleted targets", () => {
    const ops: Operation[] = [
      {
        id: "1",
        seq: 1,
        type: "replace_text",
        page_index: 0,
        target_element_id: "n:1",
        payload: { text: "edited" },
      },
      {
        id: "2",
        seq: 2,
        type: "delete_text",
        page_index: 0,
        target_element_id: "n:1",
      },
    ];
    const state = deriveElements([layout], ops);
    expect(state.find((e) => e.id === "n:1")).toMatchObject({
      text: "edited",
      deleted: true,
    });
    expect(state.find((e) => e.id === "n:2")?.text).toBe("two");
  });

  it("copies replacement styles instead of sharing the operation object", () => {
    const operation: Operation = {
      id: "replace-style",
      seq: 1,
      type: "replace_text",
      page_index: 0,
      target_element_id: "n:1",
      payload: { text: "styled" },
      style: { ...base },
    };

    const element = deriveElements([layout], [operation]).find(
      (item) => item.id === "n:1",
    )!;
    element.style.color = "#FFFFFF";

    expect(operation.style?.color).toBe("#123456");
  });
});
