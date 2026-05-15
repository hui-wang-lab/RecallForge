# 控制台上传进度条与回显增强 — 设计规格

- 状态：Draft
- 日期：2026-05-14
- 关联模块：`recallforge/console`
- 关联文档：[AGENTS.md](../AGENTS.md)、[M2-design.md](M2-design.md)
- 涉及文件：
  - `recallforge/console/static/style.css`（上传进度条 + 阶段指示器 + 结果展示 + 发送按钮 loading + 思考动画）
  - `recallforge/console/static/index.html`（上传 DOM 重构 + 问答区占位元素）
  - `recallforge/console/static/app.js`（XHR 上传 + 状态机 + 轮询 + 发送反馈 + 空值校验）
- 后端改动：无

## 背景

当前控制台存在两个页面的体验问题：

1. **文档导入页**：点击"提交导入"后没有任何视觉反馈——按钮只是变灰（opacity 0.6），用户不知道上传是否在进行；导入结果以原始 JSON 展示，信息可读性差
2. **智能问答页**：点击发送按钮后没有任何反应——按钮未置灰、无 loading 动画、回答区无"思考中"提示；textarea 未禁用，用户可以重复发送

后端 `POST /api/knowledge/documents` 是**同步**接口（阻塞到 ingest + embedding backfill 完成才返回），且已有 `GET /api/knowledge/ingest-jobs/{job_id}` 轮询端点。无需修改后端。

## 改动范围

仅修改 3 个前端文件：

- `recallforge/console/static/style.css`
- `recallforge/console/static/index.html`
- `recallforge/console/static/app.js`

---

## 1. 上传进度条

### 实现

- 将 `fetch()` 替换为 `XMLHttpRequest`，利用 `xhr.upload.onprogress` 获取实际上传百分比
- 进度条**显示在 drop zone 内部**（上传时隐藏 drop zone 默认内容，显示进度条）
- 进度条组件：标签（"上传中… 45%"）+ 轨道 + 填充条，使用现有 accent 色系

### 关键细节

- `xhr.upload.onprogress` 中 `event.total` 可能为 0，需防除零
- XHR 发送 FormData 时不要手动设置 `Content-Type`
- 设置 `xhr.timeout = 300000`（5 分钟），覆盖大文件场景
- 上传完成后恢复 drop zone 默认状态

### CSS 新增

```css
/* 上传中的 drop zone 状态 */
.drop-zone.uploading .drop-zone-content { display: none; }
.drop-zone.uploading .upload-progress { display: flex; }

.upload-progress {
  display: none;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 80%;
}

.progress-label {
  font-size: 13px;
  color: var(--ink-2);
}

.progress-bar-track {
  width: 100%;
  height: 8px;
  border-radius: 4px;
  background: var(--line);
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--accent);
  width: 0%;
  transition: width 0.3s ease;
}

.progress-bar-fill.error {
  background: #ef4444;
}
```

### HTML 结构变更

在 drop zone 内部，将现有内容包裹到 `.drop-zone-content` 中，并新增 `.upload-progress` 容器：

```html
<div class="drop-zone" id="drop-zone">
  <div class="drop-zone-content" id="drop-zone-content">
    <!-- 现有内容：svg, p, span, input, file-name -->
  </div>
  <div class="upload-progress" id="upload-progress">
    <span class="progress-label" id="progress-label">上传中… 0%</span>
    <div class="progress-bar-track">
      <div class="progress-bar-fill" id="progress-fill"></div>
    </div>
  </div>
</div>
```

### JS 核心逻辑

新增 `uploadWithProgress(formData)` 函数，使用 XHR 替代 fetch：

```javascript
function uploadWithProgress(formData) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/knowledge/documents");
    xhr.timeout = 300000;

    xhr.upload.onprogress = (e) => {
      const percent = e.total > 0 ? (e.loaded / e.total) * 100 : 0;
      updateUploadProgress(percent);
    };

    xhr.onload = () => {
      const data = JSON.parse(xhr.responseText || "{}");
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ data, ok: true, status: xhr.status });
      } else {
        resolve({ data, ok: false, status: xhr.status });
      }
    };

    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.ontimeout = () => reject(new Error("上传超时"));
    xhr.send(formData);
  });
}

function updateUploadProgress(percent) {
  const fill = document.getElementById("progress-fill");
  const label = document.getElementById("progress-label");
  if (fill) fill.style.width = `${Math.round(percent)}%`;
  if (label) label.textContent = `上传中… ${Math.round(percent)}%`;
}
```

---

## 2. 阶段指示器（Phase Stepper）

在结果卡片顶部添加 4 步水平指示器：

```
  ● 上传  ──  ● 解析切片  ──  ● Embedding  ──  ● 完成
```

### 状态机

`idle → uploading → processing → embedding → complete / error`

- 每步有圆点 + 文字标签
- 已完成步骤：实心圆 + 勾号，连接线高亮
- 当前步骤：accent 边框 + 旋转 spinner
- 未到达步骤：灰色边框
- 错误步骤：红色圆点

### 阶段转换逻辑

由于后端同步处理，典型流程为 `uploading → complete`（跳过中间态）。中间态为未来异步化预留。

| 条件 | 转换目标 |
|------|----------|
| `DocumentIngestResponse.status === "success"` 且 `embedding_status === "succeeded"` | 直接跳到 `complete` |
| `embedding_status === "not_requested"` 或 `"not_configured"` | `complete`，embedding 步骤灰显跳过 |
| `embedding_status === "failed"` | `complete`，embedding 步骤红色错误 |
| Job `status === "running"` | `processing` 步骤显示 spinner |
| Job `status === "failed"` | `error` |
| Job `status === "skipped_duplicate"` | `complete` |

### CSS 新增

```css
.phase-stepper {
  display: flex;
  align-items: flex-start;
  margin-bottom: 18px;
}

.phase-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  position: relative;
}

.phase-dot {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid var(--line);
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 1;
}

.phase-dot.active {
  border-color: var(--accent);
  background: var(--accent-bg);
}

.phase-dot.done {
  border-color: var(--accent);
  background: var(--accent);
}

.phase-dot.error {
  border-color: #ef4444;
  background: #fef2f2;
}

.phase-dot svg {
  width: 12px;
  height: 12px;
}

.phase-label {
  font-size: 11px;
  color: var(--muted);
  margin-top: 6px;
}

.phase-label.active {
  color: var(--accent);
  font-weight: 550;
}

/* 连接线 */
.phase-step:not(:first-child)::before {
  content: "";
  position: absolute;
  top: 11px;
  right: 50%;
  width: 100%;
  height: 2px;
  background: var(--line);
  z-index: 0;
}

.phase-step.done:not(:first-child)::before,
.phase-step.passed:not(:first-child)::before {
  background: var(--accent);
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.phase-dot.active .spin-icon {
  animation: spin 1s linear infinite;
}
```

### HTML 结构

```html
<div class="phase-stepper" id="phase-stepper" style="display:none">
  <div class="phase-step" data-phase="uploading">
    <div class="phase-dot" id="dot-uploading"></div>
    <span class="phase-label">上传</span>
  </div>
  <div class="phase-step" data-phase="processing">
    <div class="phase-dot" id="dot-processing"></div>
    <span class="phase-label">解析切片</span>
  </div>
  <div class="phase-step" data-phase="embedding">
    <div class="phase-dot" id="dot-embedding"></div>
    <span class="phase-label">Embedding</span>
  </div>
  <div class="phase-step" data-phase="complete">
    <div class="phase-dot" id="dot-complete"></div>
    <span class="phase-label">完成</span>
  </div>
</div>
```

### JS 核心逻辑

```javascript
const PHASES = ["uploading", "processing", "embedding", "complete"];

function setUploadPhase(phase) {
  uploadPhase = phase;

  // 更新 drop zone 状态
  const dropZone = document.getElementById("drop-zone");
  if (phase === "uploading") {
    dropZone.classList.add("uploading");
  } else {
    dropZone.classList.remove("uploading");
  }

  // 更新 phase stepper
  const stepper = document.getElementById("phase-stepper");
  if (phase !== "idle") stepper.style.display = "flex";

  renderPhaseDots(phase);

  // 更新结果卡片左侧竖线颜色
  const card = document.querySelector("#page-upload .result-card");
  card.classList.remove("bar-success", "bar-error", "bar-warning");
  if (phase === "complete") card.classList.add("bar-success");
  if (phase === "error") card.classList.add("bar-error");
}

function renderPhaseDots(currentPhase) {
  const currentIdx = PHASES.indexOf(currentPhase);
  PHASES.forEach((p, i) => {
    const dot = document.getElementById(`dot-${p}`);
    const label = dot?.parentElement.querySelector(".phase-label");
    dot.classList.remove("done", "active", "error");
    label?.classList.remove("active");

    if (i < currentIdx) {
      dot.classList.add("done");
      dot.innerHTML = checkmarkSVG();
      dot.parentElement.classList.add("passed");
    } else if (i === currentIdx) {
      if (currentPhase === "complete") {
        dot.classList.add("done");
        dot.innerHTML = checkmarkSVG();
      } else if (currentPhase === "error") {
        dot.classList.add("error");
        dot.innerHTML = errorSVG();
      } else {
        dot.classList.add("active");
        dot.innerHTML = spinnerSVG();
      }
      label?.classList.add("active");
    } else {
      dot.innerHTML = "";
      dot.parentElement.classList.remove("passed");
    }
  });
}
```

---

## 3. 结构化结果展示

替换原始 JSON `<pre>` 为 `<dl>` 键值对列表：

| 字段 | 标签 | 渲染方式 |
|------|------|----------|
| `status` | 状态 | 彩色标签（成功/失败/处理中/跳过重复） |
| `document_id` | 文档 ID | 等宽字体 |
| `job_id` | 任务 ID | 等宽字体 |
| `embedding_status` | Embedding | 彩色标签 |
| `trace_id` | 追踪 ID | 等宽字体 |
| `source_uri` | 来源 | 普通文本 |
| `doc_type` | 文档类型 | 普通文本 |
| `parser_used` | 解析器 | 普通文本 |
| `chunker_used` | 切片器 | 普通文本 |
| `parent_chunk_count` | Parent 切片数 | 数字 |
| `child_chunk_count` | Child 切片数 | 数字 |
| `version` | 版本 | 数字 |
| `warnings` | 警告 | 数量 + 展开详情 |
| `error_message` | 错误信息 | 红色文本 |
| `created_at` | 创建时间 | 格式化时间 |
| `finished_at` | 完成时间 | 格式化时间 |

### 状态标签中文映射

| status 值 | 标签文字 | 标签样式 |
|-----------|----------|----------|
| `success` | 成功 | `.result-tag.success`（绿底绿字） |
| `failed` | 失败 | `.result-tag.failed`（红底红字） |
| `running` | 处理中 | `.result-tag.running`（紫底紫字） |
| `pending` | 等待中 | `.result-tag.pending`（黄底黄字） |
| `skipped_duplicate` | 跳过重复 | `.result-tag.skipped_duplicate`（蓝底蓝字） |

保留原始 JSON 在 `<details>` 折叠区中，供调试使用。

### CSS 新增

```css
.result-fields {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 16px;
  padding: 4px 0;
}

.result-field-label {
  font-size: 12px;
  color: var(--muted);
  font-weight: 550;
  text-align: right;
  white-space: nowrap;
}

.result-field-value {
  font-size: 13px;
  color: var(--ink);
  word-break: break-all;
}

.result-field-value.mono {
  font-family: "SF Mono", "Cascadia Code", Consolas, monospace;
  font-size: 12px;
}

.result-field-value.error {
  color: #ef4444;
}

.result-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11.5px;
  font-weight: 600;
}

.result-tag.success { background: #dcfce7; color: #166534; }
.result-tag.failed { background: #fee2e2; color: #991b1b; }
.result-tag.running { background: var(--accent-bg); color: var(--accent); }
.result-tag.pending { background: #fef3c7; color: #92400e; }
.result-tag.skipped_duplicate { background: #e0e7ff; color: #3730a3; }

/* 结果卡片竖线颜色变体 */
.result-card.bar-success .result-card-bar { background: #22c55e; }
.result-card.bar-error .result-card-bar { background: #ef4444; }
.result-card.bar-warning .result-card-bar { background: #f59e0b; }
```

### HTML 结构

```html
<div class="result-card-head">
  <h3>导入结果</h3>
  <div style="display:flex; gap:8px; align-items:center;">
    <span id="result-status-tag" class="result-tag" style="display:none"></span>
    <button type="button" class="icon-btn" id="refresh-job" title="刷新任务状态">
      <!-- 刷新图标 SVG -->
    </button>
  </div>
</div>

<div class="phase-stepper" id="phase-stepper" style="display:none">
  <!-- 4 个 phase-step，见上文 -->
</div>

<dl class="result-fields" id="result-fields" style="display:none"></dl>

<details style="margin-top:12px" id="raw-details">
  <summary style="cursor:pointer; font-size:12px; color:var(--muted); user-select:none">原始 JSON</summary>
  <pre id="job-output" class="output-block small" style="margin-top:8px"></pre>
</details>
```

### JS 核心逻辑

```javascript
const STATUS_LABELS = {
  success: "成功",
  failed: "失败",
  running: "处理中",
  pending: "等待中",
  skipped_duplicate: "跳过重复",
};

function renderStructuredResult(data, source) {
  const fields = document.getElementById("result-fields");
  fields.style.display = "grid";
  fields.innerHTML = "";

  const entries = [];

  if (source === "ingest") {
    // DocumentIngestResponse 字段
    if (data.status) entries.push(["状态", statusTag(data.status)]);
    if (data.document_id != null) entries.push(["文档 ID", mono(data.document_id)]);
    if (data.job_id) entries.push(["任务 ID", mono(data.job_id)]);
    if (data.embedding_status) entries.push(["Embedding", statusTag(data.embedding_status)]);
    if (data.trace_id) entries.push(["追踪 ID", mono(data.trace_id)]);
  }

  if (source === "job") {
    // IngestJobResponse 字段
    if (data.status) entries.push(["状态", statusTag(data.status)]);
    if (data.source_uri) entries.push(["来源", data.source_uri]);
    if (data.doc_type) entries.push(["文档类型", data.doc_type]);
    if (data.parser_used) entries.push(["解析器", data.parser_used]);
    if (data.chunker_used) entries.push(["切片器", data.chunker_used]);
    if (data.parent_chunk_count != null) entries.push(["Parent 切片数", data.parent_chunk_count]);
    if (data.child_chunk_count != null) entries.push(["Child 切片数", data.child_chunk_count]);
    if (data.error_message) entries.push(["错误信息", errorText(data.error_message)]);
    if (data.warnings?.length) entries.push(["警告", `${data.warnings.length} 条`]);
    if (data.created_at) entries.push(["创建时间", formatTime(data.created_at)]);
    if (data.finished_at) entries.push(["完成时间", formatTime(data.finished_at)]);
  }

  entries.forEach(([label, value]) => {
    fields.innerHTML += `<dt class="result-field-label">${label}</dt><dd class="result-field-value">${value}</dd>`;
  });

  // 更新顶部状态标签
  if (data.status) {
    const tag = document.getElementById("result-status-tag");
    tag.style.display = "inline-block";
    tag.className = `result-tag ${data.status}`;
    tag.textContent = STATUS_LABELS[data.status] || data.status;
  }

  // 同时写入原始 JSON
  const output = document.getElementById("job-output");
  if (output) output.textContent = JSON.stringify(data, null, 2);
}

function statusTag(status) {
  const label = STATUS_LABELS[status] || status;
  return `<span class="result-tag ${status}">${label}</span>`;
}

function mono(text) {
  return `<span class="mono">${text}</span>`;
}

function errorText(msg) {
  return `<span class="error">${msg}</span>`;
}

function formatTime(iso) {
  try { return new Date(iso).toLocaleString("zh-CN"); }
  catch { return iso; }
}
```

---

## 4. 自动轮询 Job 状态

上传完成后自动轮询 `GET /api/knowledge/ingest-jobs/{job_id}`：

- 间隔：2 秒
- 超时：90 秒，超时后显示"轮询超时，可手动刷新"
- Job `status` 为 `success` / `failed` / `skipped_duplicate` 时停止轮询
- 切换页面或开始新上传时清理轮询定时器

### JS 核心逻辑

```javascript
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 90000;
let pollTimer = null;
let pollStartTime = 0;

function startJobPolling(jobId) {
  stopJobPolling();
  pollStartTime = Date.now();
  pollJobStatus(jobId);
  pollTimer = setInterval(() => pollJobStatus(jobId), POLL_INTERVAL_MS);
}

async function pollJobStatus(jobId) {
  if (Date.now() - pollStartTime > POLL_TIMEOUT_MS) {
    handlePollTimeout();
    return;
  }

  const data = await request(`/api/knowledge/ingest-jobs/${jobId}`);
  renderStructuredResult(data, "job");

  if (data.status === "success" || data.status === "skipped_duplicate") {
    setUploadPhase("complete");
    stopJobPolling();
  } else if (data.status === "failed") {
    setUploadPhase("error");
    stopJobPolling();
  } else if (data.status === "running" && uploadPhase === "uploading") {
    setUploadPhase("processing");
  }
}

function stopJobPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function handlePollTimeout() {
  stopJobPolling();
  // 在 phase stepper 后追加超时提示
  const hint = document.createElement("p");
  hint.className = "timeout-hint";
  hint.textContent = "轮询超时（90秒），任务可能仍在后台处理。点击刷新按钮手动检查。";
  document.getElementById("phase-stepper").after(hint);
}
```

### 页面切换清理

在 `switchPage()` 中加入轮询清理：

```javascript
function switchPage(pageId) {
  if (pageId !== "page-upload") stopJobPolling();
  // ... 其余不变
}
```

---

## 5. 错误状态

| 场景 | 进度条 | Phase Stepper | 结果卡片 |
|------|--------|---------------|----------|
| 网络/XHR 错误 | 红色填充 | 当前步骤红色 | 错误信息 |
| API 4xx/5xx | 不适用 | 标红失败步骤 | error.code + message |
| Job `status=failed` | 不适用 | 当前步骤红色 | error_message 高亮 |

结果卡片左侧竖线颜色跟随状态：成功=绿色（`bar-success`），错误=红色（`bar-error`）。

### JS 错误处理

```javascript
function handleUploadError(error) {
  setUploadPhase("error");

  const fill = document.getElementById("progress-fill");
  if (fill) fill.classList.add("error");

  const fields = document.getElementById("result-fields");
  fields.style.display = "grid";
  fields.innerHTML = `
    <dt class="result-field-label">状态</dt>
    <dd class="result-field-value"><span class="result-tag failed">失败</span></dd>
    <dt class="result-field-label">错误</dt>
    <dd class="result-field-value error">${error.code ? `${error.code}: ` : ""}${error.message || "上传失败"}</dd>
  `;

  const tag = document.getElementById("result-status-tag");
  tag.style.display = "inline-block";
  tag.className = "result-tag failed";
  tag.textContent = "失败";
}
```

---

## 6. 上传提交处理器（完整替换）

替换当前 `$("#upload-form").addEventListener("submit", ...)` 处理器：

```javascript
$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (uploadPhase !== "idle") return; // 防止重复提交

  const form = new FormData();
  const file = fileInput.files[0];
  if (!file) return;
  form.append("file", file);
  appendIfPresent(form, "source_uri", "#source-uri");
  appendIfPresent(form, "doc_type", "#doc-type");
  appendIfPresent(form, "title", "#title");

  const btn = event.target.querySelector(".primary-btn");
  setLoading(btn, true);
  setUploadPhase("uploading");

  try {
    const result = await uploadWithProgress(form);

    // 恢复 drop zone
    document.getElementById("drop-zone").classList.remove("uploading");

    if (result.ok && result.data.job_id) {
      lastJobId = result.data.job_id;
      renderStructuredResult(result.data, "ingest");

      if (result.data.status === "success" && result.data.embedding_status === "succeeded") {
        setUploadPhase("complete");
      } else {
        setUploadPhase("processing");
        startJobPolling(result.data.job_id);
      }
    } else if (result.ok) {
      renderStructuredResult(result.data, "ingest");
      setUploadPhase("error");
    } else {
      handleUploadError(result.data.error || { message: `HTTP ${result.status}` });
    }
  } catch (err) {
    document.getElementById("drop-zone").classList.remove("uploading");
    handleUploadError({ message: err.message });
  }

  setLoading(btn, false);

  // 清除超时提示
  document.querySelectorAll(".timeout-hint").forEach((el) => el.remove());
});
```

---

## 7. 问答页发送反馈优化

### 当前问题

点击发送按钮（圆形 `.send-btn`）后：

1. 按钮仅通过 `setLoading()` 设置 `opacity: 0.6` 和 `pointerEvents: none`——在 38px 圆形按钮上视觉变化极弱
2. 无旋转或呼吸动画，用户无法判断"是否正在处理"
3. 回答区 `#answer-area` 直到响应返回才出现，无"思考中"占位提示
4. textarea 未禁用，用户可以修改内容并重复发送

### 改进方案

#### 7.1 发送按钮 loading 状态

发送按钮在 loading 时替换图标为旋转 spinner，并降低背景色亮度。

**CSS 新增：**

```css
.send-btn.loading {
  background: var(--accent-2);
  cursor: wait;
  pointer-events: none;
}

.send-btn.loading svg {
  animation: spin 1s linear infinite;
}
```

**JS 变更：**

改写 `setLoading()` 函数，对 `.send-btn` 增加类名切换（而非仅用 inline style）：

```javascript
function setLoading(btn, loading) {
  if (!btn) return;
  btn.disabled = loading;
  btn.classList.toggle("loading", loading);

  // 对非 send-btn 的按钮（如 primary-btn）保留 inline style 兼容
  if (!btn.classList.contains("send-btn")) {
    btn.style.opacity = loading ? "0.6" : "";
    btn.style.pointerEvents = loading ? "none" : "";
  }
}
```

#### 7.2 回答区"思考中"占位

点击发送后立即显示回答区，内部展示"思考中…"动画，替代空白等待。

**CSS 新增：**

```css
.thinking-placeholder {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  color: var(--muted);
  font-size: 13.5px;
}

.thinking-dots span {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--soft);
  animation: thinking-bounce 1.2s ease-in-out infinite;
}

.thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
.thinking-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes thinking-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
  40% { transform: translateY(-4px); opacity: 1; }
}
```

**JS 变更：**

```javascript
const THINKING_HTML = `
  <div class="thinking-placeholder">
    <span>思考中</span>
    <span class="thinking-dots"><span></span><span></span><span></span></span>
  </div>
`;

$("#answer").addEventListener("click", async () => {
  const btn = $("#answer");
  const question = $("#question").value.trim();
  if (!question) return; // 空问题不发请求
  if (btn.disabled) return; // 防重复

  setLoading(btn, true);

  // 立即显示思考中占位
  if (answerArea) answerArea.classList.remove("hidden");
  answerOutput.innerHTML = THINKING_HTML;
  queryOutput.textContent = "";
  referencesOutput.textContent = "";
  hitsOutput.textContent = "";

  // 禁用 textarea 防重复发送
  const textarea = $("#question");
  textarea.disabled = true;

  const result = await postQuery("/api/knowledge/answer");
  renderQuery(result);
  setLoading(btn, false);
  textarea.disabled = false;
});
```

#### 7.3 空问题校验

当前代码不做前端校验，空问题也会发出请求。增加空值拦截：

```javascript
// 在 click handler 顶部
const question = $("#question").value.trim();
if (!question) return;
```

#### 7.4 Ctrl+Enter 重复发送防护

当前 `keydown` 监听直接调用 `click()`，若按钮已 disabled 则不应触发：

```javascript
$("#question").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    const btn = $("#answer");
    if (!btn.disabled) btn.click();
  }
});
```

---

## 8. Drop zone 上传后重置

上传 XHR 完成后（无论成功失败），恢复 drop zone：

```javascript
function resetDropZone() {
  const dropZone = document.getElementById("drop-zone");
  dropZone.classList.remove("uploading");
  const fill = document.getElementById("progress-fill");
  if (fill) {
    fill.style.width = "0%";
    fill.classList.remove("error");
  }
  fileInput.value = "";
  if (fileName) fileName.textContent = "";
}
```

---

## 实施步骤

### 第一步：落 spec 到工程（当前步骤）

将本设计规格文档写入 `docs/console-upload-ux-spec.md`，与项目其他设计文档保持同一位置。

**不写任何实现代码。**

### 后续开发（另行安排）

1. CSS — 上传进度条、阶段指示器、结构化结果、发送按钮 loading、思考动画的样式类
2. HTML — 上传 DOM 重构（drop zone 内嵌进度条、结果卡片 stepper + fields + 折叠 JSON）
3. JS — 上传逻辑替换（XHR + 状态机 + 轮询）、发送反馈（loading + 思考中 + 禁用 + 空值校验）

---

## 验证方式

### 文档导入页

1. 启动服务，打开控制台 `/console`
2. 上传一个小文件（如 .md），验证：进度条从 0% 到 100%，阶段指示器从"上传"跳到"完成"，结构化结果展示正常
3. 上传一个不支持的文件类型，验证错误状态显示
4. 上传同一文件两次，验证 `skipped_duplicate` 状态标签
5. 手动点击刷新按钮，验证 Job 详情刷新
6. 检查 `<details>` 中原始 JSON 是否可展开
7. 检查网络断开时的错误提示

### 智能问答页

1. 输入问题后点击发送，验证按钮出现旋转动画、背景色变浅
2. 验证回答区立即出现"思考中…"跳动动画
3. 验证发送期间 textarea 被禁用，无法修改或重复发送
4. 验证 Ctrl+Enter 在按钮 disabled 时不触发发送
5. 空问题直接点击发送，验证不发请求
6. 响应返回后验证按钮恢复、textarea 可编辑、"思考中"被回答内容替换
