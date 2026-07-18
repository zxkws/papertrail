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
  elements: TextElement[];
}
export type OpType =
  "replace_text" | "delete_text" | "move_text" | "add_text" | "cover_region";
export interface Operation {
  id: string;
  seq: number;
  type: OpType;
  page_index: number;
  target_element_id?: string;
  created_element_id?: string;
  bbox?: BBox;
  payload?: Record<string, string>;
  style?: TextStyle;
  z_order?: number;
}
export interface VisualElement {
  id: string;
  page_index: number;
  text: string;
  bbox: BBox;
  style: TextStyle;
  kind: "native" | "added";
  deleted: boolean;
  changed: boolean;
  editability: "native" | "cover_only";
}
export interface DocumentInfo {
  document_id: string;
  upload_sha256: string;
  page_count: number;
}
