import { useMemo, useState } from "react";
import { api } from "./lib/api";
import { saveDraftWithRetry, saveThenExport } from "./lib/exportWorkflow";
import { fitBbox } from "./lib/fitBbox";
import { covers, deriveElements } from "./lib/operationState";
import {
  boxStyle,
  boxText,
  deriveRasterElements,
  rasterDeleteOp,
  rasterId,
  rasterReplaceOp,
} from "./lib/rasterState";
import {
  commit,
  initialHistory,
  redo,
  undo,
  type History,
} from "./lib/history";
import { PdfViewer } from "./components/PdfViewer";
import type {
  BBox,
  DocumentInfo,
  FontsResponse,
  Layout,
  Operation,
  RasterBox,
  RasterPreview,
  VisualElement,
} from "./types";
import "./styles.css";

const uid = () => crypto.randomUUID();
type Tool = "select" | "add" | "cover";
export default function App() {
  const [doc, setDoc] = useState<DocumentInfo>();
  const [layouts, setLayouts] = useState<Layout[]>([]);
  const [draft, setDraft] = useState<{ id: string; revision: number }>();
  const [history, setHistory] = useState<History>(initialHistory);
  const [selectedId, setSelectedId] = useState<string>();
  const [text, setText] = useState("");
  const [fontSize, setFontSize] = useState(12);
  const [textColor, setTextColor] = useState("#17201b");
  const [coverColor, setCoverColor] = useState("#FFFFFF");
  const [tool, setTool] = useState<Tool>("select");
  const [rasterBoxes, setRasterBoxes] = useState<Record<number, RasterBox[]>>({});
  const [fonts, setFonts] = useState<FontsResponse>();
  const [original, setOriginal] = useState("");
  const [font, setFont] = useState("");
  const [erase, setErase] = useState("auto");
  const [busy, setBusy] = useState("");
  const [download, setDownload] = useState("");
  const [versionId, setVersionId] = useState("");
  const [dragging, setDragging] = useState(false);
  // 元素 id -> 服务端渲染的真实预览。画布上的 HTML 文本只能示意位置，
  // 反映不了匹配到的字体，也反映不了擦除与背景修补的效果
  const [previews, setPreviews] = useState<Record<string, RasterPreview>>({});
  const [error, setError] = useState("");
  const ops = history.present;
  const visual = useMemo(
    () => [
      ...deriveElements(layouts, ops),
      ...deriveRasterElements(rasterBoxes, ops),
    ],
    [layouts, ops, rasterBoxes],
  );
  const selected = visual.find((e) => e.id === selectedId);
  const selectedBox =
    selected?.kind === "raster" ? rasterBox(selected.id) : undefined;
  const coverOverlays = useMemo(() => covers(ops), [ops]);
  async function upload(file: File) {
    setBusy("正在读取结构");
    setError("");
    try {
      const d = await api.upload(file);
      setDoc(d);
      const all = await Promise.all(
        Array.from({ length: d.page_count }, (_, i) =>
          api.layout(d.document_id, i),
        ),
      );
      setLayouts(all);
      setDraft(await api.createDraft(d.document_id));
      setHistory(initialHistory);
      setSelectedId(undefined);
      setRasterBoxes({});
      setPreviews({});
      setDownload("");
      setVersionId("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  /** 落地页整块都可以拖入文件。`.drop input` 是 display:none 的，
   *  不自己处理 drop，浏览器会直接打开文件、把应用页面顶掉。 */
  const dropHandlers = {
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: (e: React.DragEvent) => {
      if (e.currentTarget === e.target) setDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const dropped = e.dataTransfer.files?.[0];
      if (dropped) upload(dropped);
    },
  };

  function add(op: Omit<Operation, "id" | "seq">) {
    setHistory((h) =>
      commit(h, [
        ...h.present,
        { ...op, id: uid(), seq: h.present.length + 1 },
      ]),
    );
  }
  function select(e: VisualElement) {
    setSelectedId(e.id);
    setText(e.text);
    setFontSize(e.style.font_size_pt);
    setTextColor(e.style.color);
    setTool("select");
    if (e.kind === "raster") {
      const box = rasterBox(e.id);
      // 原文可改：OCR 认错时改这里再重新匹配，字号和字体是按它标定出来的
      setOriginal(box ? boxText(box) : e.text);
      setFont(e.style.font_family);
      setErase(box?.suggest.erase ?? "auto");
    }
  }

  /** 由元素 id 反查这次识别的原始框（携带 quad 和全部分析值）。 */
  function rasterBox(id: string): RasterBox | undefined {
    for (const [page, list] of Object.entries(rasterBoxes)) {
      const hit = list.findIndex((_, i) => rasterId(Number(page), i) === id);
      if (hit >= 0) return list[hit];
    }
    return undefined;
  }

  async function runOcr(page: number) {
    if (!doc) return;
    setBusy(`识别第 ${page + 1} 页`);
    setError("");
    try {
      if (!fonts) setFonts(await api.fonts());
      const result = await api.rasterOcr(doc.document_id, page);
      setRasterBoxes((prev) => ({ ...prev, [page]: result.boxes }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  /** 改过原文后重新标定字号与字体。 */
  async function rematch() {
    if (!doc || !selected || selected.kind !== "raster") return;
    const box = rasterBox(selected.id);
    if (!box) return;
    setBusy("重新匹配字体");
    try {
      const fresh = await api.rasterInspect(
        doc.document_id,
        selected.page_index,
        box.quad,
        original,
      );
      setRasterBoxes((prev) => {
        const list = [...(prev[selected.page_index] ?? [])];
        const at = list.findIndex(
          (_, i) => rasterId(selected.page_index, i) === selected.id,
        );
        if (at >= 0) list[at] = fresh;
        return { ...prev, [selected.page_index]: list };
      });
      const style = boxStyle(fresh);
      setFont(style.font_family);
      setFontSize(style.font_size_pt);
      setTextColor(style.color);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  function currentForm() {
    return {
      text,
      original,
      font,
      size: fontSize,
      color: textColor,
      align: "left" as const,
      erase,
    };
  }

  /** 按当前表单向服务端要一张真实重绘的预览。 */
  async function preview(id: string, page: number, box: RasterBox) {
    if (!doc) return;
    setBusy("渲染预览");
    setError("");
    try {
      const form = currentForm();
      const result = await api.rasterPreview(doc.document_id, page, {
        bbox: box.bbox,
        quad: box.quad,
        text: form.text,
        original_text: form.original,
        font: form.font,
        erase: form.erase,
        style: {
          font_family: form.font,
          font_size_pt: form.size,
          color: form.color,
          align: form.align,
          rotation: 0,
        },
      });
      setPreviews((prev) => ({ ...prev, [id]: result }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  function replaceRaster() {
    if (!selected || selected.kind !== "raster") return;
    const box = rasterBox(selected.id);
    if (!box) return;
    add(rasterReplaceOp(selected.id, selected.page_index, box, currentForm()));
    // 应用后顺手取一张真实预览，画布上显示的就不再是「示意」了
    preview(selected.id, selected.page_index, box);
  }
  async function save() {
    if (!draft) return;
    setBusy("保存草稿");
    setError("");
    try {
      const revision = await saveDraftWithRetry(api, draft, ops);
      setDraft({ ...draft, revision });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function exportPdf() {
    if (!draft) return;
    setBusy("保存当前编辑并生成新版本");
    setError("");
    try {
      const r = await saveThenExport(api, draft, ops);
      setDraft({ ...draft, revision: r.revision });
      setDownload(api.download(r.version_id));
      setVersionId(r.version_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  function replace() {
    const layout = layouts.find(
      (item) => item.page_index === selected?.page_index,
    );
    if (
      selected &&
      layout &&
      selected.kind !== "raster" &&
      !selected.deleted &&
      selected.editability === "native"
    )
      add({
        type: "replace_text",
        page_index: selected.page_index,
        target_element_id: selected.id,
        bbox: fitBbox(
          selected.bbox,
          text,
          fontSize,
          layout.width_pt,
          layout.height_pt,
        ),
        payload: { text },
        style: { ...selected.style, font_size_pt: fontSize, color: textColor },
      });
  }
  function moveElement(id: string, page: number, bbox: BBox) {
    const item = visual.find((e) => e.id === id);
    // 位图框没有对应的 PDF 文字对象，move_text 指向它会在 reducer 里报
    // TARGET_NOT_FOUND；位置调整要通过重新框选来做
    if (item && item.kind !== "raster" && !item.deleted && item.editability === "native")
      add({ type: "move_text", page_index: page, target_element_id: id, bbox });
  }
  function region(page: number, bbox: BBox) {
    if (tool === "add") {
      const id = `a:${uid()}`;
      const value = text || "新增文字";
      const layout = layouts.find((item) => item.page_index === page);
      add({
        type: "add_text",
        page_index: page,
        created_element_id: id,
        bbox: layout
          ? fitBbox(
              bbox,
              value,
              fontSize,
              layout.width_pt,
              layout.height_pt,
            )
          : bbox,
        payload: { text: value },
        style: {
          font_family: "helv",
          font_size_pt: fontSize,
          color: textColor,
          align: "left",
          rotation: 0,
        },
      });
      setSelectedId(id);
    } else if (tool === "cover")
      add({
        type: "cover_region",
        page_index: page,
        bbox,
        payload: { cover_color: coverColor },
      });
    setTool("select");
  }
  const disabled =
    !selected || selected.deleted || selected.editability !== "native";
  return (
    <main>
      <header>
        <div className="brand">
          <span className="mark">P/</span>
          <div>
            <b>Papertrail</b>
            <small>LOCAL PDF WORKBENCH</small>
          </div>
        </div>
        <div className="trust">
          <i /> 源文件永不修改
        </div>
        <label className="upload">
          {doc ? "更换文件" : "选择文件"}
          <input
            type="file"
            accept="application/pdf,image/png,image/jpeg,image/gif,image/bmp,image/tiff,image/webp"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </label>
      </header>
      {!doc ? (
        <section
          className={`empty ${dragging ? "dragging" : ""}`}
          {...dropHandlers}
        >
          <p className="eyebrow">DETERMINISTIC · PRIVATE · LOCAL</p>
          <h1>
            按文档类型
            <br />
            <em>自动分流</em>处理。
          </h1>
          <div className="paths">
            <div className="path">
              <h3>矢量 PDF（数字生成）</h3>
              <p>点选文字直接改，导出无损，文字仍可选中。</p>
            </div>
            <div className="path">
              <h3>扫描件 / 截图 / 照片</h3>
              <p>先 OCR 识别，再擦掉原字、用匹配到的字体重绘像素。</p>
            </div>
          </div>
          <label className="drop">
            上传 PDF 或直接拖拽图片至此
            <input
              type="file"
              accept="application/pdf,image/png,image/jpeg,image/gif,image/bmp,image/tiff,image/webp"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
            />
            <span>支持 PDF、PNG、JPG、WEBP 等 · ≤100 MB</span>
          </label>
        </section>
      ) : (
        <div className="workspace">
          <aside className="left">
            <p className="label">文档</p>
            <h2>{doc.page_count} 页</h2>
            <code>{doc.upload_sha256.slice(0, 16)}…</code>
            {doc.origin && (
              <div className="doc-origin">
                <code>{doc.origin.width_px} × {doc.origin.height_px}</code>
                <code>{doc.origin.media_type}</code>
              </div>
            )}
            <div className="rail">
              {layouts.map((l) => {
                const isRaster = l.kind === "raster";
                const boxes = rasterBoxes[l.page_index];
                return (
                  <div className={`rail-item ${isRaster ? "is-raster" : "is-vector"}`} key={l.page_index}>
                    <a href={`#page-${l.page_index}`}>
                      <span className="page-num">{String(l.page_index + 1).padStart(2, "0")}</span>
                      {isRaster ? (
                        <span className="page-badge raster">位图页</span>
                      ) : (
                        <span className="page-badge vector">矢量页</span>
                      )}
                    </a>
                    <div className="page-desc">
                      {isRaster ? "扫描件/截图，需识别文字" : `包含 ${l.elements.length} 个文本框`}
                    </div>
                    {isRaster && (
                      <button
                        className={`ocr-btn ${boxes ? 'done' : 'pending'}`}
                        disabled={!!busy}
                        onClick={() => runOcr(l.page_index)}
                      >
                        {boxes
                          ? `重新识别（当前 ${boxes.length} 框）`
                          : "识别文字"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="warnings">
              <b>编辑提示</b>
              <p>任何内容编辑都会使现有数字签名失效。</p>
              <p>扫描覆盖只是视觉覆盖，不是安全脱敏。</p>
            </div>
          </aside>
          <section className="viewer">
            <div className="toolbar">
              <button
                onClick={() => setHistory(undo(history))}
                disabled={!history.past.length}
              >
                ↶ 撤销
              </button>
              <button
                onClick={() => setHistory(redo(history))}
                disabled={!history.future.length}
              >
                ↷ 重做
              </button>
              <span>{ops.length} 个操作</span>
              <button onClick={save}>保存草稿</button>
              <button className="export" onClick={exportPdf}>
                保存并导出 PDF
              </button>
              {download && (
                <>
                  <a className="download" href={download}>
                    下载 PDF ↓
                  </a>
                  {doc?.origin && versionId && (
                    <a className="download" href={api.downloadPng(versionId)}>
                      下载 PNG ↓
                    </a>
                  )}
                </>
              )}
            </div>
            <PdfViewer
              url={api.file(doc.document_id)}
              layouts={layouts}
              elements={visual}
              covers={coverOverlays}
              previews={previews}
              selected={selectedId}
              tool={tool}
              onSelect={select}
              onRegion={region}
              onMove={moveElement}
            />
          </section>
          <aside className="inspector">
            <p className="label">检查器</p>
            <div className="tool-tabs">
              <button
                className={tool === "select" ? "active" : ""}
                onClick={() => setTool("select")}
              >
                选择/拖动
              </button>
              <button
                className={tool === "add" ? "active" : ""}
                onClick={() => setTool("add")}
              >
                新增文字
              </button>
              <button
                className={tool === "cover" ? "active" : ""}
                onClick={() => setTool("cover")}
              >
                覆盖区域
              </button>
            </div>

            {tool === "cover" && (
              <div className="tool-panel">
                <p className="muted">
                  在任意页面拖框确认覆盖范围；单击会创建 170×32 pt
                  区域。预览会即时显示。
                </p>
                <label>
                  覆盖颜色
                  <input
                    type="color"
                    value={coverColor}
                    onChange={(e) =>
                      setCoverColor(e.target.value.toUpperCase())
                    }
                  />
                </label>
              </div>
            )}

            {tool === "add" && (
              <div className="tool-panel">
                <label>
                  新增文字内容
                  <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                  />
                </label>
                <div className="style-row">
                  <label>
                    字号
                    <input
                      type="number"
                      min="4"
                      max="144"
                      value={fontSize}
                      onChange={(e) => setFontSize(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    颜色
                    <input
                      type="color"
                      value={textColor}
                      onChange={(e) => setTextColor(e.target.value)}
                    />
                  </label>
                </div>
                <p className="muted">
                  现在到目标页点击或拖框放置文字。新增后可直接选中、拖动、替换或删除。
                </p>
              </div>
            )}

            {tool === "select" && !selected && (
              <p className="muted">
                点击页面中的文字框开始编辑；原生或新增文字均可直接拖动。
              </p>
            )}

            {tool === "select" && selected?.kind === "raster" && selectedBox && (
              <div className="raster-panel">
                <div className="panel-header raster-header">
                  <span className="tag raster-tag">位图页文字</span>
                  <p>像素重绘模式</p>
                </div>

                <div className="workflow-step">
                  <h4>1. 确认原文与匹配</h4>
                  {selectedBox.suggest.iou !== undefined && selectedBox.suggest.iou < 0.7 && (
                    <div className="iou-warning">
                      <strong>⚠️ 匹配分数低 ({selectedBox.suggest.iou})</strong>
                      <p>原文与图上可能不一致。请修正原文后重新匹配。</p>
                    </div>
                  )}
                  <label>
                    原文（用于标定字号与字体，OCR 认错时改这里）
                    <input
                      value={original}
                      onChange={(e) => setOriginal(e.target.value)}
                    />
                  </label>
                  <button onClick={rematch} disabled={!!busy} className="rematch-btn">
                    按原文重新匹配
                  </button>
                </div>

                <div className="workflow-step">
                  <h4>2. 修改内容</h4>
                  <label>
                    新文字
                    <textarea
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                    />
                  </label>
                  <label>
                    字体
                    <select value={font} onChange={(e) => setFont(e.target.value)}>
                      {fonts?.fonts.map((f) => (
                        <option key={`${f.path}:${f.index}`} value={f.name}>
                          {f.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="style-row">
                    <label>
                      字号 pt
                      <input
                        type="number"
                        step="0.01"
                        min="1"
                        value={fontSize}
                        onChange={(e) => setFontSize(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      颜色
                      <input
                        type="color"
                        value={textColor}
                        onChange={(e) => setTextColor(e.target.value)}
                      />
                    </label>
                    <label>
                      擦除
                      <select
                        value={erase}
                        onChange={(e) => setErase(e.target.value)}
                      >
                        {["auto", "solid", "smooth", "telea", "ns"].map((m) => (
                          <option key={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {fonts && !fonts.text_layout.kerning && (
                    <p className="muted">
                      服务端缺少 raqm，重绘不做字距调整，匹配分数与保真度都会下降。
                    </p>
                  )}
                </div>

                <div className="workflow-step">
                  <h4>3. 诊断信息</h4>
                  <div className="diagnostics">
                    <p className="label">字体匹配 soft-IoU</p>
                    <ul className="matches">
                      {selectedBox.font_matches.slice(0, 6).map((m) => (
                        <li
                          key={m.name}
                          onClick={() => {
                            setFont(m.name);
                            setFontSize(m.font_size_pt);
                          }}
                        >
                          <span className="iou-val">{m.iou}</span> {m.name} {m.font_size_pt}pt
                        </li>
                      ))}
                    </ul>
                    <p className="label">底层分析值</p>
                    <table className="raw-table">
                      <tbody>
                        <tr><td>score</td><td>{selectedBox.score}</td></tr>
                        <tr><td>angle</td><td>{selectedBox.angle}</td></tr>
                        <tr><td>bbox</td><td>{JSON.stringify(selectedBox.bbox)}</td></tr>
                        <tr><td>ink_bbox</td><td>{JSON.stringify(selectedBox.ink_bbox)}</td></tr>
                        <tr><td>text_color</td><td>{JSON.stringify(selectedBox.text_color)}</td></tr>
                        <tr><td>bg_color</td><td>{JSON.stringify(selectedBox.bg_color)}</td></tr>
                        <tr><td>bg_std</td><td>{JSON.stringify(selectedBox.bg_std)}</td></tr>
                        <tr><td>bg_residual</td><td>{selectedBox.bg_residual}</td></tr>
                        <tr><td>stroke_width</td><td>{selectedBox.stroke_width}</td></tr>
                        <tr><td>suggest.erase</td><td>{selectedBox.suggest.erase}</td></tr>
                        <tr><td>suggest.iou</td><td>{selectedBox.suggest.iou}</td></tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="workflow-actions">
                  <button
                    onClick={() =>
                      preview(selected.id, selected.page_index, selectedBox)
                    }
                    disabled={!!busy}
                  >
                    预览效果（服务端真实重绘）
                  </button>
                  <button className="primary" onClick={replaceRaster}>
                    应用替换
                  </button>
                  <button
                    className="danger"
                    onClick={() =>
                      add(
                        rasterDeleteOp(
                          selected.id,
                          selected.page_index,
                          selectedBox,
                          erase,
                        ),
                      )
                    }
                  >
                    只擦除不重写
                  </button>
                </div>
              </div>
            )}

            {tool === "select" && selected && selected.kind !== "raster" && (
              <div className="vector-panel">
                <div className="panel-header vector-header">
                  <span className="tag vector-tag">
                    {selected.kind === "added"
                      ? "新增元素"
                      : selected.editability === "native"
                        ? "原生文字"
                        : "仅覆盖"}
                  </span>
                  <p>矢量对象</p>
                </div>
                
                <div className="workflow-step">
                  <label>
                    文字内容
                    <textarea
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                    />
                  </label>
                  <div className="style-row">
                    <label>
                      字号
                      <input
                        type="number"
                        min="4"
                        max="144"
                        value={fontSize}
                        onChange={(e) => setFontSize(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      颜色
                      <input
                        type="color"
                        value={textColor}
                        onChange={(e) => setTextColor(e.target.value)}
                      />
                    </label>
                  </div>

                  {selected.deleted ? (
                    <p className="deleted-note">此元素已删除。请撤销后再操作。</p>
                  ) : selected.editability === "cover_only" ? (
                    <p className="muted">
                      复杂方向文字仅允许使用覆盖区域和新增文字，不能原生替换、移动或删除。
                    </p>
                  ) : (
                    <>
                      <p className="muted">可在画布上直接拖动所选文字。</p>
                      <div className="workflow-actions">
                        <button
                          className="primary"
                          disabled={disabled}
                          onClick={replace}
                        >
                          应用替换
                        </button>
                        <button
                          className="danger"
                          disabled={disabled}
                          onClick={() =>
                            add({
                              type: "delete_text",
                              page_index: selected.page_index,
                              target_element_id: selected.id,
                            })
                          }
                        >
                          删除文字
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}
          </aside>
        </div>
      )}
      {busy && <div className="toast">{busy}…</div>}
      {error && (
        <div className="error" onClick={() => setError("")}>
          {error}
        </div>
      )}
    </main>
  );
}
