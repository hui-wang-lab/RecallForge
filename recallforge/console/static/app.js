let lastJobId = "";
let uploadPhase = "idle";
let pollTimer = null;
let pollStartTime = 0;
let selectedKbId = null;

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 90000;

const PHASES = ["uploading", "processing", "embedding", "complete"];

const STATUS_LABELS = {
  success: "成功",
  failed: "失败",
  running: "处理中",
  pending: "等待中",
  skipped_duplicate: "跳过重复",
  succeeded: "成功",
  not_requested: "未请求",
  not_configured: "未配置",
};

const THINKING_HTML = `
  <div class="thinking-placeholder">
    <span>思考中</span>
    <span class="thinking-dots"><span></span><span></span><span></span></span>
  </div>
`;

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const statusEl = $("#status");
const statusDot = $("#status-dot");
const jobOutput = $("#job-output");
const queryOutput = $("#query-output");
const answerOutput = $("#answer-output");
const referencesOutput = $("#references-output");
const hitsOutput = $("#hits-output");
const answerArea = $("#answer-area");

const pageTitles = {
  "page-kbs": "知识库治理",
  "page-answer": "智能问答",
  "page-upload": "文档导入",
  "page-references": "引用追踪",
  "page-hits": "命中诊断",
};

/* ---- SVG helpers ---- */

function checkmarkSVG() {
  return '<svg viewBox="0 0 12 12" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6l2.5 2.5 4.5-5"/></svg>';
}

function spinnerSVG() {
  return '<svg class="spin-icon" viewBox="0 0 12 12" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linecap="round"><path d="M6 1a5 5 0 015 5"/></svg>';
}

function errorSVG() {
  return '<svg viewBox="0 0 12 12" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round"><path d="M3 3l6 6M9 3l-6 6"/></svg>';
}

/* ---- Panel Navigation ---- */

$$("[data-panel]").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    switchPage(link.dataset.panel);
    closeMobileSidebar();
  });
});

function switchPage(pageId) {
  if (pageId !== "page-upload") stopJobPolling();
  $$(".page").forEach((p) => p.classList.remove("active"));
  $$(".nav-item").forEach((n) => n.classList.remove("active"));

  const page = document.getElementById(pageId);
  if (page) page.classList.add("active");

  const nav = $(`.nav-item[data-panel="${pageId}"]`);
  if (nav) nav.classList.add("active");

  const title = $("#topbar-title");
  if (title) title.textContent = pageTitles[pageId] || "";
  if (pageId === "page-kbs") loadKnowledgeBases();
}

/* ---- Knowledge-base governance ---- */

async function loadKnowledgeBases() {
  const data = await request("/api/knowledge-bases");
  const items = data.items || [];
  const count = $("#kb-count");
  if (count) count.textContent = `${items.length} 个`;
  const list = $("#kb-list");
  if (!list) return;
  list.innerHTML = "";
  items.forEach((kb) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `kb-row${selectedKbId === kb.knowledge_base_id ? " active" : ""}`;
    button.innerHTML = `
      <span class="kb-row-title">${escapeHtml(kb.name)}</span>
      <span class="kb-row-meta">${escapeHtml(kb.role || "-")} · ${kb.document_count || 0} 文件 · ${kb.active_chunk_count || 0} chunks</span>
    `;
    button.addEventListener("click", () => selectKnowledgeBase(kb));
    list.appendChild(button);
  });
  if (!selectedKbId && items[0]) selectKnowledgeBase(items[0]);
}

async function selectKnowledgeBase(kb) {
  selectedKbId = kb.knowledge_base_id;
  const detail = $("#kb-detail");
  if (detail) {
    detail.className = "kb-detail";
    detail.innerHTML = `
      <strong>${escapeHtml(kb.name)}</strong>
      <span>${escapeHtml(kb.status)} · ${escapeHtml(kb.default_department)} · ${escapeHtml(kb.default_access_level)}</span>
    `;
  }
  await loadKbDocuments();
  await loadKnowledgeBases();
}

async function loadKbDocuments() {
  const target = $("#kb-documents");
  if (!target || !selectedKbId) return;
  const data = await request(`/api/knowledge-bases/${selectedKbId}/documents`);
  const docs = data.items || [];
  target.innerHTML = docs.length ? "" : '<div class="detail-empty">暂无文件。</div>';
  docs.forEach((doc) => {
    const row = document.createElement("div");
    row.className = "doc-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(doc.title || doc.source_name || doc.source_uri)}</strong>
        <span>${escapeHtml(doc.doc_type)} · v${doc.version} · ${escapeHtml(doc.status)}</span>
      </div>
      <button type="button" class="text-btn" data-document-id="${doc.document_id}">删除</button>
    `;
    row.querySelector("button").addEventListener("click", () => deleteKbDocument(doc.document_id));
    target.appendChild(row);
  });
}

async function deleteKbDocument(documentId) {
  if (!selectedKbId || !confirm("确认删除该文件？")) return;
  await request(`/api/knowledge-bases/${selectedKbId}/documents/${documentId}`, { method: "DELETE" });
  await loadKbDocuments();
}

$("#refresh-kbs")?.addEventListener("click", loadKnowledgeBases);

$("#kb-create-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#kb-name");
  const name = input.value.trim();
  if (!name) return;
  const data = await request("/api/knowledge-bases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  input.value = "";
  if (data.knowledge_base_id) selectedKbId = data.knowledge_base_id;
  await loadKnowledgeBases();
});

$("#reindex-kb")?.addEventListener("click", async () => {
  if (!selectedKbId) return;
  const data = await request(`/api/knowledge-bases/${selectedKbId}/reindex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dry_run: true }),
  });
  const detail = $("#kb-detail");
  if (detail) detail.innerHTML += `<span>Reindex dry run: ${data.estimated_documents || 0} 文件</span>`;
});

/* ---- Mobile Sidebar ---- */

const sidebar = $(".sidebar");
const sidebarToggle = $("#sidebar-toggle");

let overlay = $(".sidebar-overlay");
if (!overlay) {
  overlay = document.createElement("div");
  overlay.className = "sidebar-overlay";
  document.body.appendChild(overlay);
}

if (sidebarToggle) {
  sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("open");
    overlay.classList.toggle("visible");
  });
}

overlay.addEventListener("click", closeMobileSidebar);

function closeMobileSidebar() {
  sidebar.classList.remove("open");
  overlay.classList.remove("visible");
}

/* ---- Upload: drag & drop ---- */

const dropZone = $("#drop-zone");
const fileInput = $("#file");
const fileNameEl = $("#file-name");

if (dropZone && fileInput) {
  dropZone.addEventListener("click", () => {
    if (uploadPhase !== "idle") return;
    fileInput.click();
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (uploadPhase !== "idle") return;
    dropZone.classList.add("dragover");
  });

  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (uploadPhase !== "idle") return;
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      showFileName(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) showFileName(fileInput.files[0]);
  });
}

function showFileName(file) {
  if (fileNameEl) fileNameEl.textContent = file.name;
}

/* ---- Upload with Progress (XHR) ---- */

function uploadWithProgress(formData) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const endpoint = selectedKbId ? `/api/knowledge-bases/${selectedKbId}/documents` : "/api/knowledge/documents";
    xhr.open("POST", endpoint);
    xhr.timeout = 300000;

    xhr.upload.onprogress = (e) => {
      const percent = e.total > 0 ? (e.loaded / e.total) * 100 : 0;
      updateUploadProgress(percent);
    };

    xhr.onload = () => {
      let data;
      try {
        data = JSON.parse(xhr.responseText || "{}");
      } catch {
        data = {};
      }
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

/* ---- Phase Stepper ---- */

function setUploadPhase(phase) {
  uploadPhase = phase;

  // update drop zone state
  if (dropZone) {
    if (phase === "uploading") {
      dropZone.classList.add("uploading");
    } else {
      dropZone.classList.remove("uploading");
    }
  }

  // show & update phase stepper
  const stepper = document.getElementById("phase-stepper");
  if (stepper && phase !== "idle") stepper.style.display = "flex";

  if (phase !== "idle") renderPhaseDots(phase);

  // update result card bar color
  const card = document.querySelector("#page-upload .result-card");
  if (card) {
    card.classList.remove("bar-success", "bar-error", "bar-warning");
    if (phase === "complete") card.classList.add("bar-success");
    if (phase === "error") card.classList.add("bar-error");
  }
}

function renderPhaseDots(currentPhase) {
  const currentIdx = PHASES.indexOf(currentPhase);

  PHASES.forEach((p, i) => {
    const dot = document.getElementById(`dot-${p}`);
    if (!dot) return;
    const step = dot.parentElement;
    const label = step.querySelector(".phase-label");

    dot.classList.remove("done", "active", "error");
    step.classList.remove("passed");
    label?.classList.remove("active");

    if (i < currentIdx) {
      dot.classList.add("done");
      dot.innerHTML = checkmarkSVG();
      step.classList.add("passed");
    } else if (i === currentIdx) {
      if (currentPhase === "complete") {
        dot.classList.add("done");
        dot.innerHTML = checkmarkSVG();
        step.classList.add("passed");
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
    }
  });
}

/* ---- Structured Result Display ---- */

function renderStructuredResult(data, source) {
  const fields = document.getElementById("result-fields");
  if (!fields) return;
  fields.style.display = "grid";
  fields.innerHTML = "";

  const entries = [];

  if (source === "ingest") {
    if (data.status) entries.push(["状态", statusTag(data.status)]);
    if (data.document_id != null) entries.push(["文档 ID", mono(data.document_id)]);
    if (data.job_id) entries.push(["任务 ID", mono(data.job_id)]);
    if (data.embedding_status) entries.push(["Embedding", statusTag(data.embedding_status)]);
    if (data.trace_id) entries.push(["追踪 ID", mono(data.trace_id)]);
  }

  if (source === "job") {
    if (data.status) entries.push(["状态", statusTag(data.status)]);
    if (data.source_uri) entries.push(["来源", escapeHtml(data.source_uri)]);
    if (data.doc_type) entries.push(["文档类型", escapeHtml(data.doc_type)]);
    if (data.parser_used) entries.push(["解析器", escapeHtml(data.parser_used)]);
    if (data.chunker_used) entries.push(["切片器", escapeHtml(data.chunker_used)]);
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

  // update top status tag
  if (data.status) {
    const tag = document.getElementById("result-status-tag");
    if (tag) {
      tag.style.display = "inline-block";
      tag.className = `result-tag ${data.status}`;
      tag.textContent = STATUS_LABELS[data.status] || data.status;
    }
  }

  // write raw JSON to details section
  if (jobOutput) jobOutput.textContent = JSON.stringify(data, null, 2);
}

function statusTag(status) {
  const label = STATUS_LABELS[status] || status;
  return `<span class="result-tag ${status}">${label}</span>`;
}

function mono(text) {
  return `<span class="mono">${text}</span>`;
}

function errorText(msg) {
  return `<span class="error">${escapeHtml(msg)}</span>`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatTime(iso) {
  try { return new Date(iso).toLocaleString("zh-CN"); }
  catch { return iso; }
}

/* ---- Auto Job Polling ---- */

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
  const stepper = document.getElementById("phase-stepper");
  if (stepper) {
    const hint = document.createElement("p");
    hint.className = "timeout-hint";
    hint.textContent = "轮询超时（90秒），任务可能仍在后台处理。点击刷新按钮手动检查。";
    stepper.after(hint);
  }
}

/* ---- Upload Error Handler ---- */

function handleUploadError(error) {
  setUploadPhase("error");

  const fill = document.getElementById("progress-fill");
  if (fill) fill.classList.add("error");

  const fields = document.getElementById("result-fields");
  if (fields) {
    fields.style.display = "grid";
    fields.innerHTML = `
      <dt class="result-field-label">状态</dt>
      <dd class="result-field-value"><span class="result-tag failed">失败</span></dd>
      <dt class="result-field-label">错误</dt>
      <dd class="result-field-value error">${error.code ? `${escapeHtml(error.code)}: ` : ""}${escapeHtml(error.message || "上传失败")}</dd>
    `;
  }

  const tag = document.getElementById("result-status-tag");
  if (tag) {
    tag.style.display = "inline-block";
    tag.className = "result-tag failed";
    tag.textContent = "失败";
  }
}

/* ---- Drop Zone Reset ---- */

function resetDropZone() {
  if (dropZone) dropZone.classList.remove("uploading");
  const fill = document.getElementById("progress-fill");
  if (fill) {
    fill.style.width = "0%";
    fill.classList.remove("error");
  }
  if (fileInput) fileInput.value = "";
  if (fileNameEl) fileNameEl.textContent = "";
}

/* ---- Upload Submit ---- */

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (uploadPhase !== "idle") return;

  const form = new FormData();
  const file = fileInput.files[0];
  if (!file) return;
  form.append("file", file);
  appendIfPresent(form, "source_uri", "#source-uri");
  appendIfPresent(form, "doc_type", "#doc-type");
  appendIfPresent(form, "title", "#title");

  const btn = event.target.querySelector(".primary-btn");
  setLoading(btn, true);

  // clear previous timeout hints
  document.querySelectorAll(".timeout-hint").forEach((el) => el.remove());

  setUploadPhase("uploading");

  try {
    const result = await uploadWithProgress(form);

    // restore drop zone
    if (dropZone) dropZone.classList.remove("uploading");

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
    if (dropZone) dropZone.classList.remove("uploading");
    handleUploadError({ message: err.message });
  }

  setLoading(btn, false);
  // reset phase to idle after a short delay so user can upload again
  setTimeout(() => { uploadPhase = "idle"; }, 500);
  resetDropZone();
});

/* ---- Refresh Job ---- */

$("#refresh-job").addEventListener("click", async () => {
  if (!lastJobId) return;
  const data = await request(`/api/knowledge/ingest-jobs/${lastJobId}`);
  renderStructuredResult(data, "job");
});

/* ---- Answer ---- */

$("#answer").addEventListener("click", async () => {
  const btn = $("#answer");
  const question = $("#question").value.trim();
  if (!question) return;
  if (btn.disabled) return;

  setLoading(btn, true);

  // show thinking placeholder immediately
  if (answerArea) answerArea.classList.remove("hidden");
  if (answerOutput) answerOutput.innerHTML = THINKING_HTML;
  if (queryOutput) queryOutput.textContent = "";
  if (referencesOutput) referencesOutput.textContent = "";
  if (hitsOutput) hitsOutput.textContent = "";

  // disable textarea during request
  const textarea = $("#question");
  if (textarea) textarea.disabled = true;

  const result = await postQuery("/api/knowledge/answer");
  renderQuery(result);
  setLoading(btn, false);
  if (textarea) textarea.disabled = false;
});

/* ---- Keyboard: Ctrl/Cmd + Enter ---- */

$("#question").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    const btn = $("#answer");
    if (!btn.disabled) btn.click();
  }
});

/* ---- Core Functions ---- */

async function postQuery(endpoint) {
  const question = $("#question").value.trim();
  return request(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      filters: selectedKbId ? { knowledge_base_id: selectedKbId } : {},
    }),
  });
}

async function request(endpoint, options = {}) {
  try {
    const response = await fetch(endpoint, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      statusEl.textContent = `${response.status} ${data.error?.code || "error"}`;
      statusDot.classList.remove("connected");
    } else {
      statusEl.textContent = data.trace_id ? `OK · ${data.trace_id}` : "OK";
      statusDot.classList.add("connected");
    }
    return data;
  } catch (err) {
    statusEl.textContent = "网络错误";
    statusDot.classList.remove("connected");
    return { error: { code: "network_error", message: err.message } };
  }
}

function renderQuery(result) {
  if (answerArea) answerArea.classList.remove("hidden");
  render(queryOutput, result);
  answerOutput.textContent = result.answer || result.context_text || result.refusal_reason || "";
  render(referencesOutput, result.references || []);
  render(hitsOutput, result.hit_summary || []);
}

function appendIfPresent(form, name, selector) {
  const value = $(selector).value.trim();
  if (value) form.append(name, value);
}

function render(target, value) {
  target.textContent = JSON.stringify(value, null, 2);
}

function setLoading(btn, loading) {
  if (!btn) return;
  btn.disabled = loading;
  btn.classList.toggle("loading", loading);

  // for non-send-btn buttons (like primary-btn), keep inline style compatibility
  if (!btn.classList.contains("send-btn")) {
    btn.style.opacity = loading ? "0.6" : "";
    btn.style.pointerEvents = loading ? "none" : "";
  }
}
