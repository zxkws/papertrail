# Papertrail — 文档文字编辑器

本地、确定性、非破坏式的文档改字工具。前端采用 React + TypeScript + Vite + PDF.js，
后端采用 FastAPI + PyMuPDF 1.26.3；不调用大模型或云 API。

**按页面类型分流**，两条路径互不混用：

| 页面 | 判据 | 路径 | 保真度 |
| --- | --- | --- | --- |
| 矢量页（数字生成） | 有可见文字对象 | 原生 redaction + 重新插字 | 无损，文字仍可选中 |
| 位图页（扫描件/截图） | 无可见文字，整页是图 | OCR 定位 → 擦除 → 按原字体重绘像素 | 像素级重绘 |

这条分界线是硬性的：矢量页栅格化会毁掉整页的文字层和矢量图形，与「非破坏式」的前提冲突，
因此位图操作落在矢量页上会被保存和导出两道校验挡回（`RASTER_EDIT_ON_VECTOR_PAGE`）。

需要说明的是，把 PNG 转成 PDF 并不能让它变得可编辑——那只是把位图塞进 PDF 容器，
页面里依然没有文字对象。ocrmypdf 那类「可搜索 PDF」加的是 render mode 3 的**不可见**文字层，
只服务搜索和复制，改它一个可见像素都不会变。本项目对这种页面的判定依据是可见文字数，
而不是 `get_text()` 有没有内容。

## 能力

- 上传普通 PDF，使用只读源对象保存并计算 SHA-256，导出绝不覆盖源文件。
- PDF.js 多页预览；layout API 返回文本、bbox、字体、字号、颜色、页面几何和稳定 `n:` ID。
- 点击文字后创建 replace/delete/move 操作；原生和新增文字都有即时 overlay，可在画布直接拖动。任意页面可点击/拖框创建 `add_text` 和视觉 `cover_region`，并设置文字内容、字号、颜色及覆盖颜色。
- 浏览器 undo/redo；JSON 草稿 operation log、revision 乐观锁。导出会先保存当前浏览器操作，遇到 409 获取最新 revision 后重试，避免未手动保存的编辑丢失。
- Python canonical reducer 支持 native/added 稳定 ID，覆盖 replace→move、replace→delete、move→move、add→move、add→delete。
- 导出严格分四阶段：位图页整页重绘→native 文字 redaction（显式保留图片/矢量）→视觉覆盖→最终插字；rotation 仅 0/90/180/270。
- 位图页：整页 OCR 出文字框，逐框给出文字色、背景色、墨迹框、笔画粗细，并匹配出最接近的系统字体与字号；改字后按原基线重绘。所有坐标对外都是 PDF 点，渲染 DPI 只在服务端内部出现。

> **重要限制**：源文件永不修改；任何编辑都会使已有数字签名失效；扫描覆盖只是视觉覆盖，底层像素仍存在，**不能用于安全脱敏**。

## 本地启动

需要 Python 3.11+、uv、Node 22：

```bash
make install
make dev
```

位图页编辑是**可选依赖**（onnxruntime + opencv 体积可观，只处理矢量 PDF 可以不装）：

```bash
uv pip install --python apps/api/.venv/bin/python 'apps/api[raster]'
```

未安装时服务照常启动，位图相关接口返回 503 并说明原因。
镜像同理：`docker build --build-arg INSTALL_RASTER=1 -f Dockerfile.api .`（会一并装上 libGL）。

访问 <http://localhost:5173>。也可分开运行 `make api` 与 `make web`。Docker 可运行 `docker compose up --build`。

## 部署

前端托管在 Vercel，API 自托管在自己的服务器上，两者靠 `vercel.json` 里的 rewrite 串起来。

### 1. 镜像

推送 `main` 分支中与 API 有关的改动后，GitHub Actions 会构建 `Dockerfile.api`，
推送 Linux AMD64/ARM64 多架构镜像到 GitHub Container Registry：

```text
ghcr.io/<owner>/papertrail-api:latest
ghcr.io/<owner>/papertrail-api:sha-<commit>
```

发布的镜像**带位图能力**（`INSTALL_RASTER=1`），扫描页编辑开箱可用，代价是体积明显大于纯矢量版——
除了 onnxruntime + opencv，还要装 libGL 和一组系统字体。字体是硬需求：重绘时要从系统已装字体里
挑最接近原文的一款，而 slim 基础镜像几乎不带字体，缺了就直接报「找不到可用字体」。
镜像内装的是 Liberation（与 Arial/Times/Courier 度量兼容）、DejaVu 和 Noto（含 CJK）。

只需要矢量能力时可以自行 `docker build -f Dockerfile.api .` 构建瘦身版（默认 `INSTALL_RASTER=0`）。

私有 GHCR 包要用带 `read:packages` 的令牌拉取，也可以在 GitHub Packages 里把包改成公开。

### 2. 服务器上跑起来

```bash
export GHCR_OWNER=<你的 GitHub 用户名>
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
curl http://127.0.0.1:8000/api/v1/health
```

默认只监听回环地址，TLS 交给宿主机上的反向代理（Caddy / nginx）来做——
Vercel 的 rewrite 是服务端代理，理论上回源用 HTTP 也能通，但那段流量是明文的，
建议还是配好 HTTPS 再对外。

数据目录挂在具名卷 `pdf_data`（容器内 `/app/data`）。当前是 JSON/文件存储，
**只支持单实例**，不要多副本共用同一份数据目录。健康检查路径 `/api/v1/health`。

### 3. 让前端找到 API

把 `vercel.json` 里的 rewrite 目标改成 API 的公网地址（占位符是 `https://api.example.com`）：

```json
{"source": "/api/:path*", "destination": "https://你的域名/api/:path*"}
```

前端调的是相对路径 `/api/...`，Vercel 在服务端把请求代理过去，浏览器看到的是同源，
因此**不需要配 CORS**。只有在前端直连 API 域名时才需要设 `PDF_EDITOR_CORS_ORIGINS`。

一个需要实测的点：上传走的是 Vercel 的代理，而本项目允许 100 MB 的 PDF，
平台侧对请求体大小可能有更低的限制。如果大文件上传失败，就改成前端直连 API 域名
（把 `api.ts` 里的相对路径换成可配置的 base，并把 `PDF_EDITOR_CORS_ORIGINS` 设成前端域名）。

## 验证

```bash
make test
make smoke
```

Smoke 在临时目录生成无敏感信息的社区工作坊示例 PDF，执行上传→layout→五类操作→保存→导出→重开验证，并在流程前后同时断言输入 fixture 与只读存储 source 的 SHA-256 不变。pytest 还覆盖图片/矢量保留、cover 仅视觉覆盖和 0/90/180/270 导出。结果写入 `test-results/smoke.json`。

## API

- `POST /api/v1/documents` multipart 上传
- `GET /api/v1/documents/{id}` 元数据
- `GET /api/v1/documents/{id}/file` 不可变源文件
- `GET /api/v1/documents/{id}/pages/{page}/layout` 页面结构
- `POST /api/v1/documents/{id}/drafts` 创建草稿
- `GET /api/v1/drafts/{id}` 读取 operation log
- `PUT /api/v1/drafts/{id}/operations` `{expected_revision, operations}`
- `POST /api/v1/drafts/{id}/exports` 归并并同步导出 MVP
- `GET /api/v1/versions/{id}/download` 下载新版本

位图页专用（需要 raster extra）：

- `GET /api/v1/fonts` 系统字体表与可用 OCR 引擎
- `GET /api/v1/documents/{id}/pages/{page}/render.png?dpi=` 页面渲染图（仅用 fitz，不需要 extra）
- `POST /api/v1/documents/{id}/pages/{page}/raster/ocr` 整页识别 + 逐框分析
- `POST /api/v1/documents/{id}/pages/{page}/raster/inspect` 分析单个手动框

两个位图 operation 类型：`raster_replace_text` / `raster_delete_text`，
用 `r:` 前缀的稳定 id 标识框，同一个框后来的操作直接覆盖前面的。
`bbox` 与 `payload.quad` 都是 PDF 点坐标，`style.font_size_pt` 是点，因此预览与导出用不同 DPI 也不会错位。

OpenAPI 在 API 启动后的 `/docs`。独立 schema 位于 `schemas/operation.schema.json`。

## 已知限制

- 仅处理未加密、可正常解析、最多 100 MB / 300 页的 PDF；不支持密码、任意角度、竖排/RTL 原生编辑、段落回流与原字体精确复刻。
- 位图路径一框一行，不支持竖排、多行段落和艺术字（描边/渐变填充/投影）；重绘的是整行，OCR 文本必须准确，识别错了要先改「原文」再重新匹配（字体匹配 IoU 偏低就是这个信号）。
- 位图字体只能在服务端已装的系统字体里挑最接近的；纹理背景擦除靠 inpainting 推测，纹理越规则越容易看出补丁。
- 位图页导出会整页替换成改后的图：该页的注释、链接会丢失，页面旋转归一化为 0（视觉不变），文件体积按图像重新计算。
- 新文字使用 Helvetica fallback，CJK 等缺字内容可能无法写入；固定 bbox 会在 4pt 下限内确定性缩字，仍溢出则阻止导出。
- cover_region 是纯色矩形，不修补纹理背景，也不是不可恢复删除。
- MVP 使用本地 JSON/文件存储、同步导出、单用户模型；生产环境需增加身份鉴权、隔离 worker、对象存储、数据库、队列和恶意 PDF 沙箱。
- 当前 redaction 按提取 span bbox 工作，复杂相邻字形尚未实现 5% 邻接冲突阻断。
