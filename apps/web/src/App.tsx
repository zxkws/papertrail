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
      setDownload("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
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

  function replaceRaster() {
    if (!selected || selected.kind !== "raster") return;
    const box = rasterBox(selected.id);
    if (!box) return;
    add(
      rasterReplaceOp(selected.id, selected.page_index, box, {
        text,
        original,
        font,
        size: fontSize,
        color: textColor,
        align: "left",
        erase,
      }),
    );
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
          {doc ? "更换 PDF" : "选择 PDF"}
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </label>
      </header>
      {!doc ? (
        <section className="empty">
          <p className="eyebrow">DETERMINISTIC · PRIVATE · LOCAL</p>
          <h1>
            把改动留在
            <br />
            <em>操作轨迹</em>里。
          </h1>
          <p>
            点击原生文字，修改、拖动或删除；也可以在任意页拖框覆盖并叠加新文字。无需云端
            AI。
          </p>
          <label className="drop">
            上传普通 PDF 开始
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
            />
            <span>≤100 MB · ≤300 页</span>
          </label>
        </section>
      ) : (
        <div className="workspace">
          <aside className="left">
            <p className="label">文档</p>
            <h2>{doc.page_count} 页</h2>
            <code>{doc.upload_sha256.slice(0, 16)}…</code>
            <div className="rail">
              {layouts.map((l) => (
                <div className="rail-item" key={l.page_index}>
                  <a href={`#page-${l.page_index}`}>
                    {String(l.page_index + 1).padStart(2, "0")}
                    <span>
                      {l.kind === "raster"
                        ? "扫描页 · 无文字对象"
                        : `${l.elements.length} 个文本框`}
                    </span>
                  </a>
                  {l.kind === "raster" && (
                    <button
                      className="ocr"
                      disabled={!!busy}
                      onClick={() => runOcr(l.page_index)}
                    >
                      {rasterBoxes[l.page_index]
                        ? `重新识别（当前 ${rasterBoxes[l.page_index].length} 框）`
                        : "识别文字"}
                    </button>
                  )}
                </div>
              ))}
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
                <a className="download" href={download}>
                  下载版本 ↓
                </a>
              )}
            </div>
            <PdfViewer
              url={api.file(doc.document_id)}
              layouts={layouts}
              elements={visual}
              covers={coverOverlays}
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
            {tool === "cover" ? (
              <>
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
              </>
            ) : (
              <>
                <label>
                  {tool === "add" ? "新增文字内容" : "文字内容"}
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
                {tool === "add" && (
                  <p className="muted">
                    现在到目标页点击或拖框放置文字。新增后可直接选中、拖动、替换或删除。
                  </p>
                )}
              </>
            )}
            {selected?.kind === "raster" && selectedBox && (
              <>
                <hr />
                <span className="tag">扫描页文字 · 像素重绘</span>
                <label>
                  原文（用于标定字号与字体，OCR 认错时改这里）
                  <input
                    value={original}
                    onChange={(e) => setOriginal(e.target.value)}
                  />
                </label>
                <button onClick={rematch} disabled={!!busy}>
                  按原文重新匹配
                </button>
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
                {selectedBox.suggest.iou !== undefined &&
                  selectedBox.suggest.iou < 0.7 && (
                    <p className="muted">
                      匹配分数偏低（{selectedBox.suggest.iou}），多半是原文与图上不一致，
                      改完原文再点重新匹配。
                    </p>
                  )}
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
                      {m.iou} {m.name} {m.font_size_pt}pt
                    </li>
                  ))}
                </ul>
                <p className="label">分析结果</p>
                <pre className="raw">
                  {JSON.stringify(
                    {
                      score: selectedBox.score,
                      angle: selectedBox.angle,
                      bbox: selectedBox.bbox,
                      ink_bbox: selectedBox.ink_bbox,
                      text_color: selectedBox.text_color,
                      bg_color: selectedBox.bg_color,
                      bg_std: selectedBox.bg_std,
                      bg_residual: selectedBox.bg_residual,
                      stroke_width: selectedBox.stroke_width,
                      suggest: selectedBox.suggest,
                    },
                    null,
                    1,
                  )}
                </pre>
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
              </>
            )}
            {selected && selected.kind !== "raster" && (
              <>
                <hr />
                <span className="tag">
                  {selected.kind === "added"
                    ? "新增元素"
                    : selected.editability === "native"
                      ? "原生文字"
                      : "仅覆盖"}
                </span>
                <h3>{selected.text}</h3>
                {selected.deleted ? (
                  <p className="deleted-note">此元素已删除。请撤销后再操作。</p>
                ) : selected.editability === "cover_only" ? (
                  <p className="muted">
                    复杂方向文字仅允许使用覆盖区域和新增文字，不能原生替换、移动或删除。
                  </p>
                ) : (
                  <>
                    <button
                      className="primary"
                      disabled={disabled}
                      onClick={replace}
                    >
                      应用替换
                    </button>
                    <p className="muted">可在画布上直接拖动所选文字。</p>
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
                  </>
                )}
              </>
            )}{" "}
            {!selected && tool === "select" && (
              <p className="muted">
                点击页面中的文字框开始编辑；原生或新增文字均可直接拖动。
              </p>
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
