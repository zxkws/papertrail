export type BBox = [number, number, number, number];
export type Rotation = 0 | 90 | 180 | 270;
export interface TextStyle {
  font_family: string;
  font_size_pt: number;
  color: string;
  align: "left" | "center" | "right";
  rotation: Rotation;
}
export interface TextElement {
  id: string;
  page_index: number;
  text: string;
  bbox: BBox;
  font_name: string;
  font_size: number;
  color: string;
  editability: "native" | "cover_only";
}
export interface Layout {
  page_index: number;
  width_pt: number;
  height_pt: number;
  rotation: Rotation;
  scan_likelihood: number;
  kind: PageKind;
  elements: TextElement[];
}
export type OpType =
  | "replace_text"
  | "delete_text"
  | "move_text"
  | "add_text"
  | "cover_region"
  // 位图页（扫描件/截图）：没有文字对象，按框重绘像素
  | "raster_replace_text"
  | "raster_delete_text";
export type PageKind = "vector" | "raster";
/** 位图框的分析结果，坐标一律是 PDF 点。 */
export interface RasterBox {
  index: number;
  text: string;
  score: number;
  quad: [number, number][];
  bbox: BBox;
  ink_bbox: BBox;
  angle: number;
  text_color: string;
  bg_color: string;
  bg_std: number;
  bg_residual: number;
  stroke_width: number;
  suggest: {
    text?: string;
    font?: string;
    size?: number;
    font_size_pt?: number;
    color?: string;
    erase?: string;
    iou?: number;
  };
  font_matches: {
    name: string; family: string; style: string;
    iou: number; size: number; font_size_pt: number;
  }[];
}
/** operation 的载荷。已知键给出确切类型，其余留给后端扩展。 */
export interface OperationPayload {
  text?: string;
  cover_color?: string;
  // 位图操作专用
  original_text?: string;
  font?: string;
  erase?: string;
  grow?: number;
  angle?: number;
  opacity?: number;
  /** 文字框四点，PDF 点坐标；缺省时用 bbox */
  quad?: [number, number][];
  [key: string]: unknown;
}
export interface Operation {
  id: string;
  seq: number;
  type: OpType;
  page_index: number;
  target_element_id?: string;
  created_element_id?: string;
  bbox?: BBox;
  payload?: OperationPayload;
  style?: TextStyle;
  z_order?: number;
}
export interface VisualElement {
  id: string;
  page_index: number;
  text: string;
  bbox: BBox;
  style: TextStyle;
  /** raster：扫描页上 OCR 出来的文字框，没有对应的 PDF 文字对象 */
  kind: "native" | "added" | "raster";
  deleted: boolean;
  changed: boolean;
  editability: "native" | "cover_only";
}
export interface DocumentInfo {
  document_id: string;
  upload_sha256: string;
  page_count: number;
}

export interface FontEntry {
  name: string;
  family: string;
  style: string;
  path: string;
  index: number;
}
export interface FontsResponse {
  fonts: FontEntry[];
  default: FontEntry | null;
  ocr_engines: string[];
  /** 缺少 raqm 时不做字距调整，重绘保真度会下降 */
  text_layout: { kerning: boolean; raqm: string | null; freetype: string | null };
}
export interface RasterOcrResult {
  engine: string;
  dpi: number;
  scale: number;
  count: number;
  boxes: RasterBox[];
}
