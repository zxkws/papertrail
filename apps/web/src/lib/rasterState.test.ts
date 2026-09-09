import { describe, expect, it } from "vitest";
import {
  deriveRasterElements,
  rasterDeleteOp,
  rasterId,
  rasterReplaceOp,
} from "./rasterState";
import type { Operation, RasterBox } from "../types";

const box: RasterBox = {
  index: 0,
  text: "Order No:SP-100238",
  score: 0.98,
  quad: [
    [30, 40],
    [200, 40],
    [200, 60],
    [30, 60],
  ],
  bbox: [30, 40, 200, 60],
  ink_bbox: [31, 42, 198, 58],
  angle: 0,
  text_color: "#1E1E1E",
  bg_color: "#FCFCFA",
  bg_std: 0.4,
  bg_residual: 0.1,
  stroke_width: 2.1,
  // 字体匹配阶段纠正过的原文：OCR 吞掉了冒号后的空格
  suggest: {
    text: "Order No: SP-100238",
    font: "Helvetica Regular",
    font_size_pt: 11.52,
    color: "#1E1E1E",
    erase: "solid",
    iou: 0.99,
  },
  font_matches: [
    {
      name: "Helvetica Regular",
      family: "Helvetica",
      style: "Regular",
      iou: 0.99,
      size: 32,
      font_size_pt: 11.52,
    },
  ],
};

const id = rasterId(0, 0);
const op = (
  seq: number,
  partial: Omit<Operation, "id" | "seq">,
): Operation => ({ id: `op-${seq}`, seq, ...partial });

describe("deriveRasterElements", () => {
  it("用纠正后的原文和建议样式建立元素", () => {
    const [element] = deriveRasterElements({ 0: [box] }, []);
    expect(element.id).toBe(id);
    expect(element.kind).toBe("raster");
    expect(element.text).toBe("Order No: SP-100238");
    expect(element.style.font_size_pt).toBe(11.52);
    expect(element.style.font_family).toBe("Helvetica Regular");
    expect(element.changed).toBe(false);
  });

  it("替换操作改写文字并标记为已改动", () => {
    const ops = [op(1, rasterReplaceOp(id, 0, box, form("Order No: SP-777901")))];
    const [element] = deriveRasterElements({ 0: [box] }, ops);
    expect(element.text).toBe("Order No: SP-777901");
    expect(element.changed).toBe(true);
    expect(element.deleted).toBe(false);
  });

  it("同一个框后来的操作覆盖先前的，删除按 seq 生效", () => {
    const ops = [
      op(1, rasterReplaceOp(id, 0, box, form("第一次"))),
      op(2, rasterReplaceOp(id, 0, box, form("第二次"))),
      op(3, rasterDeleteOp(id, 0, box, "auto")),
    ];
    const [element] = deriveRasterElements({ 0: [box] }, ops);
    expect(element.text).toBe("第二次");
    expect(element.deleted).toBe(true);
  });

  it("草稿重开、还没重新识别时，操作本身也要能画出来", () => {
    const ops = [op(1, rasterReplaceOp(id, 0, box, form("改过的")))];
    const [element] = deriveRasterElements({}, ops);
    expect(element.id).toBe(id);
    expect(element.text).toBe("改过的");
    expect(element.bbox).toEqual(box.bbox);
  });

  it("忽略与位图无关的操作", () => {
    const ops = [
      op(1, {
        type: "replace_text",
        page_index: 0,
        target_element_id: "n:1",
        payload: { text: "x" },
      }),
    ];
    expect(deriveRasterElements({ 0: [box] }, ops)[0].changed).toBe(false);
  });
});

describe("操作构造", () => {
  it("替换操作带上 quad 与原文，坐标保持 PDF 点", () => {
    const built = rasterReplaceOp(id, 2, box, form("新文字"));
    expect(built.type).toBe("raster_replace_text");
    expect(built.page_index).toBe(2);
    expect(built.created_element_id).toBe(id);
    expect(built.bbox).toEqual(box.bbox);
    expect(built.payload?.quad).toEqual(box.quad);
    expect(built.payload?.original_text).toBe("Order No: SP-100238");
    expect(built.style?.font_size_pt).toBe(11.52);
  });

  it("删除操作只擦除，不带新文字", () => {
    const built = rasterDeleteOp(id, 0, box, "telea");
    expect(built.type).toBe("raster_delete_text");
    expect(built.payload?.erase).toBe("telea");
    expect(built.payload?.text).toBeUndefined();
  });
});

function form(text: string) {
  return {
    text,
    original: "Order No: SP-100238",
    font: "Helvetica Regular",
    size: 11.52,
    color: "#1E1E1E",
    align: "left" as const,
    erase: "auto",
  };
}
