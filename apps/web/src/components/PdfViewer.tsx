import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { BBox, Layout, VisualElement } from "../types";
import type { covers as coverFn } from "../lib/operationState";
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Cover = ReturnType<typeof coverFn>[number];
type Tool = "select" | "add" | "cover";
interface Props {
  url: string;
  layouts: Layout[];
  elements: VisualElement[];
  covers: Cover[];
  selected?: string;
  tool: Tool;
  onSelect: (e: VisualElement) => void;
  onRegion: (page: number, bbox: BBox) => void;
  onMove: (id: string, page: number, bbox: BBox) => void;
}
export function PdfViewer({
  url,
  layouts,
  elements,
  covers,
  selected,
  tool,
  onSelect,
  onRegion,
  onMove,
}: Props) {
  const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy>();
  useEffect(() => {
    let live = true;
    pdfjs.getDocument(url).promise.then((v) => live && setPdf(v));
    return () => {
      live = false;
    };
  }, [url]);
  if (!pdf) return <div className="loading">正在校准纸张坐标…</div>;
  return (
    <div className="pages">
      {layouts.map((l) => (
        <Page
          key={l.page_index}
          pdf={pdf}
          layout={l}
          elements={elements.filter((e) => e.page_index === l.page_index)}
          covers={covers.filter((c) => c.page_index === l.page_index)}
          selected={selected}
          tool={tool}
          onSelect={onSelect}
          onRegion={onRegion}
          onMove={onMove}
        />
      ))}
    </div>
  );
}
function Page({
  pdf,
  layout,
  elements,
  covers,
  selected,
  tool,
  onSelect,
  onRegion,
  onMove,
}: {
  pdf: pdfjs.PDFDocumentProxy;
  layout: Layout;
  elements: VisualElement[];
  covers: Cover[];
  selected?: string;
  tool: Tool;
  onSelect: (e: VisualElement) => void;
  onRegion: (page: number, bbox: BBox) => void;
  onMove: (id: string, page: number, bbox: BBox) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scale = 1.3;
  const [gesture, setGesture] = useState<{
    kind: "region" | "move";
    start: [number, number];
    now: [number, number];
    element?: VisualElement;
  }>();
  useEffect(() => {
    let task: pdfjs.RenderTask | undefined;
    pdf.getPage(layout.page_index + 1).then((page) => {
      const vp = page.getViewport({ scale });
      const c = canvas.current;
      if (!c) return;
      c.width = vp.width;
      c.height = vp.height;
      task = page.render({
        canvas: c,
        canvasContext: c.getContext("2d")!,
        viewport: vp,
      });
    });
    return () => task?.cancel();
  }, [pdf, layout.page_index]);
  function point(e: React.PointerEvent): [number, number] {
    const r = e.currentTarget.getBoundingClientRect();
    return [(e.clientX - r.left) / scale, (e.clientY - r.top) / scale];
  }
  function down(e: React.PointerEvent) {
    if (tool === "select") return;
    const p = point(e);
    e.currentTarget.setPointerCapture(e.pointerId);
    setGesture({ kind: "region", start: p, now: p });
  }
  function movePointer(e: React.PointerEvent) {
    if (gesture) setGesture({ ...gesture, now: point(e) });
  }
  function up() {
    if (!gesture) return;
    const [x1, y1] = gesture.start,
      [x2, y2] = gesture.now;
    if (gesture.kind === "region") {
      const w = Math.abs(x2 - x1),
        h = Math.abs(y2 - y1);
      const bbox: BBox =
        w < 4 || h < 4
          ? [x1, y1, x1 + 170, y1 + 32]
          : [
              Math.min(x1, x2),
              Math.min(y1, y2),
              Math.max(x1, x2),
              Math.max(y1, y2),
            ];
      onRegion(layout.page_index, bbox);
    } else if (gesture.element) {
      const dx = x2 - x1,
        dy = y2 - y1;
      const b = gesture.element.bbox;
      onMove(gesture.element.id, layout.page_index, [
        b[0] + dx,
        b[1] + dy,
        b[2] + dx,
        b[3] + dy,
      ]);
    }
    setGesture(undefined);
  }
  function elementDown(e: React.PointerEvent, item: VisualElement) {
    e.stopPropagation();
    onSelect(item);
    // 位图框只能选中编辑，不能拖动——它没有对应的 PDF 文字对象
    if (
      tool === "select" &&
      item.kind !== "raster" &&
      !item.deleted &&
      item.editability === "native"
    ) {
      const r = e.currentTarget.parentElement!.getBoundingClientRect();
      const p: [number, number] = [
        (e.clientX - r.left) / scale,
        (e.clientY - r.top) / scale,
      ];
      e.currentTarget.setPointerCapture(e.pointerId);
      setGesture({ kind: "move", start: p, now: p, element: item });
    }
  }
  const preview =
    gesture?.kind === "region"
      ? ([
          Math.min(gesture.start[0], gesture.now[0]),
          Math.min(gesture.start[1], gesture.now[1]),
          Math.max(gesture.start[0], gesture.now[0]),
          Math.max(gesture.start[1], gesture.now[1]),
        ] as BBox)
      : undefined;
  return (
    <article
      id={`page-${layout.page_index}`}
      className={`page tool-${tool}`}
      style={{
        width: layout.width_pt * scale,
        height: layout.height_pt * scale,
      }}
    >
      <div className={`page-type-tag ${layout.kind}`}>
        {layout.kind === "raster" ? "位图页 (扫描件)" : "矢量页 (原生文字)"}
      </div>
      <canvas ref={canvas} />
      <div
        className="hit-layer"
        onPointerDown={down}
        onPointerMove={movePointer}
        onPointerUp={up}
      >
        {covers.map((c) => (
          <div
            key={c.id}
            className="cover-overlay"
            style={box(c.bbox, scale, c.color)}
          />
        ))}
        {elements.map((item) =>
          item.deleted ? (
            <div
              key={item.id}
              className="deleted-overlay"
              style={box(item.bbox, scale, "white")}
            />
          ) : (
            <button
              title={item.text}
              aria-label={`编辑文字 ${item.text}`}
              className={`hit ${selected === item.id ? "selected" : ""} ${item.editability} ${item.kind} ${item.changed ? "changed" : ""}`}
              key={item.id}
              onPointerDown={(e) => elementDown(e, item)}
              style={{
                ...box(
                  item.bbox,
                  scale,
                  item.changed || item.kind === "added"
                    ? "white"
                    : "transparent",
                ),
                color: item.style.color,
                fontSize: item.style.font_size_pt * scale,
                textAlign: item.style.align,
              }}
            >
              {item.changed || item.kind === "added" ? item.text : ""}
            </button>
          ),
        )}
        {preview && (
          <div
            className={`region-preview ${tool}`}
            style={box(
              preview,
              scale,
              tool === "cover"
                ? "rgba(255,255,255,.75)"
                : "rgba(215,255,63,.28)",
            )}
          />
        )}
      </div>
      <span className="page-no">{layout.page_index + 1}</span>
    </article>
  );
}
function box(b: BBox, scale: number, background: string): React.CSSProperties {
  return {
    left: b[0] * scale,
    top: b[1] * scale,
    width: (b[2] - b[0]) * scale,
    height: (b[3] - b[1]) * scale,
    background,
  };
}
