import type {
  DocumentInfo,
  FontsResponse,
  Layout,
  Operation,
  RasterBox,
  RasterOcrResult,
} from "../types";
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
  ) {
    super(body);
  }
}
async function checked<T>(pending: Promise<Response>): Promise<T> {
  const response = await pending;
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return response.json();
}
export const api = {
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return checked<DocumentInfo>(
      fetch("/api/v1/documents", { method: "POST", body }),
    );
  },
  layout: (id: string, page: number) =>
    checked<Layout>(fetch(`/api/v1/documents/${id}/pages/${page}/layout`)),
  createDraft: (id: string) =>
    checked<{ id: string; revision: number }>(
      fetch(`/api/v1/documents/${id}/drafts`, { method: "POST" }),
    ),
  getDraft: (draft: string) =>
    checked<{ id: string; revision: number; operations: Operation[] }>(
      fetch(`/api/v1/drafts/${draft}`),
    ),
  save: (draft: string, revision: number, operations: Operation[]) =>
    checked<{ revision: number }>(
      fetch(`/api/v1/drafts/${draft}/operations`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: revision, operations }),
      }),
    ),
  export: (draft: string) =>
    checked<{ version_id: string }>(
      fetch(`/api/v1/drafts/${draft}/exports`, { method: "POST" }),
    ),
  fonts: () => checked<FontsResponse>(fetch("/api/v1/fonts")),
  rasterOcr: (id: string, page: number, dpi = 200) =>
    checked<RasterOcrResult>(
      fetch(`/api/v1/documents/${id}/pages/${page}/raster/ocr`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dpi, match_fonts: true }),
      }),
    ),
  rasterInspect: (
    id: string,
    page: number,
    quad: [number, number][],
    text: string,
  ) =>
    checked<RasterBox>(
      fetch(`/api/v1/documents/${id}/pages/${page}/raster/inspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quad, text, match_fonts: !!text }),
      }),
    ),
  file: (id: string) => `/api/v1/documents/${id}/file`,
  download: (id: string) => `/api/v1/versions/${id}/download`,
  /** 上传的是图片时用户要的是图片，不是 PDF */
  downloadPng: (id: string, page = 0, dpi = 200) =>
    `/api/v1/versions/${id}/download.png?page_index=${page}&dpi=${dpi}`,
};
