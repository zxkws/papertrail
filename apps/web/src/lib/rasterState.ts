import type {
  BBox,
  Operation,
  RasterBox,
  TextStyle,
  VisualElement,
} from "../types";

const fallback: TextStyle = {
  font_family: "helv",
  font_size_pt: 12,
  color: "#111111",
  align: "left",
  rotation: 0,
};

const RASTER_TYPES = ["raster_replace_text", "raster_delete_text"] as const;
type RasterOpType = (typeof RASTER_TYPES)[number];
const isRasterOp = (op: Operation): op is Operation & { type: RasterOpType } =>
  (RASTER_TYPES as readonly string[]).includes(op.type);

/** 位图框的稳定 id。重新识别会重排下标，但已建立的操作自带 bbox/quad，导出不受影响。 */
export const rasterId = (page: number, index: number) => `r:p${page}:${index}`;

export const boxStyle = (box: RasterBox): TextStyle => ({
  font_family: box.suggest.font ?? fallback.font_family,
  font_size_pt: box.suggest.font_size_pt ?? fallback.font_size_pt,
  color: box.text_color,
  align: "left",
  rotation: 0,
});

/** OCR 出来的原文。suggest.text 是字体匹配阶段纠正过的版本（OCR 常吞掉标点后的空格）。 */
export const boxText = (box: RasterBox) => box.suggest.text ?? box.text;

/** 识别结果 + 已有位图操作 -> 画布上的可视元素。 */
export function deriveRasterElements(
  boxes: Record<number, RasterBox[]>,
  ops: Operation[],
): VisualElement[] {
  const state = new Map<string, VisualElement>();
  for (const [page, list] of Object.entries(boxes)) {
    const pageIndex = Number(page);
    list.forEach((box, index) => {
      const id = rasterId(pageIndex, index);
      state.set(id, {
        id,
        page_index: pageIndex,
        text: boxText(box),
        bbox: [...box.bbox],
        style: boxStyle(box),
        kind: "raster",
        deleted: false,
        changed: false,
        editability: "native",
      });
    });
  }
  for (const op of [...ops].sort((a, b) => a.seq - b.seq)) {
    if (!isRasterOp(op) || !op.created_element_id) continue;
    const id = op.created_element_id;
    // 草稿重开、还没重新识别时，操作自身也要能画出来
    const element: VisualElement = state.get(id) ?? {
      id,
      page_index: op.page_index,
      text: "",
      bbox: [...(op.bbox ?? [0, 0, 0, 0])] as BBox,
      style: op.style ?? fallback,
      kind: "raster",
      deleted: false,
      changed: true,
      editability: "native",
    };
    element.changed = true;
    if (op.type === "raster_delete_text") {
      element.deleted = true;
    } else {
      element.deleted = false;
      element.text = String(op.payload?.text ?? "");
      if (op.bbox) element.bbox = [...op.bbox];
      if (op.style) element.style = { ...op.style };
    }
    state.set(id, element);
  }
  return [...state.values()];
}

export interface RasterForm {
  text: string;
  original: string;
  font: string;
  size: number;
  color: string;
  align: TextStyle["align"];
  erase: string;
}

/** 组装一条位图改字操作。quad 与 bbox 都用 PDF 点坐标，服务端据此换算像素。 */
export function rasterReplaceOp(
  id: string,
  page: number,
  box: RasterBox,
  form: RasterForm,
): Omit<Operation, "id" | "seq"> {
  return {
    type: "raster_replace_text",
    page_index: page,
    created_element_id: id,
    bbox: box.bbox,
    payload: {
      text: form.text,
      original_text: form.original,
      font: form.font,
      erase: form.erase,
      quad: box.quad,
    },
    style: {
      font_family: form.font,
      font_size_pt: form.size,
      color: form.color,
      align: form.align,
      rotation: 0,
    },
  };
}

export function rasterDeleteOp(
  id: string,
  page: number,
  box: RasterBox,
  erase: string,
): Omit<Operation, "id" | "seq"> {
  return {
    type: "raster_delete_text",
    page_index: page,
    created_element_id: id,
    bbox: box.bbox,
    payload: { erase, quad: box.quad },
  };
}
