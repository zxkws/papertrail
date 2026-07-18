# Papertrail — Web PDF 编辑器 MVP

本地、确定性、非破坏式的轻量 PDF 编辑器。前端采用 React + TypeScript + Vite + PDF.js，后端采用 FastAPI + PyMuPDF 1.26.3；不调用大模型或云 API。

## 能力

- 上传普通 PDF，使用只读源对象保存并计算 SHA-256，导出绝不覆盖源文件。
- PDF.js 多页预览；layout API 返回文本、bbox、字体、字号、颜色、页面几何和稳定 `n:` ID。
- 点击文字后创建 replace/delete/move 操作；原生和新增文字都有即时 overlay，可在画布直接拖动。任意页面可点击/拖框创建 `add_text` 和视觉 `cover_region`，并设置文字内容、字号、颜色及覆盖颜色。
- 浏览器 undo/redo；JSON 草稿 operation log、revision 乐观锁。导出会先保存当前浏览器操作，遇到 409 获取最新 revision 后重试，避免未手动保存的编辑丢失。
- Python canonical reducer 支持 native/added 稳定 ID，覆盖 replace→move、replace→delete、move→move、add→move、add→delete。
- 导出严格分三阶段：native 文字 redaction（显式保留图片/矢量）→视觉覆盖→最终插字；rotation 仅 0/90/180/270。

> **重要限制**：源文件永不修改；任何编辑都会使已有数字签名失效；扫描覆盖只是视觉覆盖，底层像素仍存在，**不能用于安全脱敏**。

## 本地启动

需要 Python 3.11、uv、Node 22：

```bash
make install
make dev
```

访问 <http://localhost:5173>。也可分开运行 `make api` 与 `make web`。Docker 可运行 `docker compose up --build`。

## 后端镜像与第三方部署

推送 `main` 分支中与 API 镜像有关的改动后，GitHub Actions 会自动构建
`Dockerfile.api`，并推送以下 Linux AMD64/ARM64 多架构镜像到 GitHub Container Registry：

```text
ghcr.io/zxkws/web-pdf-editor-api:latest
ghcr.io/zxkws/web-pdf-editor-api:sha-<commit>
```

第三方平台部署时暴露端口 `8000`，将持久化卷挂载到 `/app/data`，并至少设置：

```env
PDF_EDITOR_DATA=/app/data
PDF_EDITOR_CORS_ORIGINS=https://api.example.com
```

健康检查路径是 `/api/v1/health`。当前 JSON/文件存储只支持单实例运行；不要在共用这份
数据目录的情况下横向扩容。私有 GHCR 包需要使用具有 `read:packages` 权限的 GitHub
令牌拉取，也可以在 GitHub Packages 中将镜像包改为公开。

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

OpenAPI 在 API 启动后的 `/docs`。独立 schema 位于 `schemas/operation.schema.json`。

## 已知限制

- 仅处理未加密、可正常解析、最多 100 MB / 300 页的 PDF；不支持密码、OCR、任意角度、竖排/RTL 原生编辑、段落回流与原字体精确复刻。
- 新文字使用 Helvetica fallback，CJK 等缺字内容可能无法写入；固定 bbox 会在 4pt 下限内确定性缩字，仍溢出则阻止导出。
- cover_region 是纯色矩形，不修补纹理背景，也不是不可恢复删除。
- MVP 使用本地 JSON/文件存储、同步导出、单用户模型；生产环境需增加身份鉴权、隔离 worker、对象存储、数据库、队列和恶意 PDF 沙箱。
- 当前 redaction 按提取 span bbox 工作，复杂相邻字形尚未实现 5% 邻接冲突阻断。
