let lastJobId = "";
let uploadPhase = "idle";
let pollTimer = null;
let pollStartTime = 0;
let selectedKbId = null;
let selectedKbName = "";
let pendingDeleteDocId = null;
let pendingDeleteDocName = "";

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
const pipelineOutput = $("#pipeline-output");
const diagnosticOutput = $("#diagnostic-output");
const pipelineScore = $("#pipeline-score");

const pageTitles = {
  "page-kbs": "知识库管理",
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

function fileIconSVG() {
  return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M10 1H4a1 1 0 00-1 1v12a1 1 0 001 1h8a1 1 0 001-1V5l-3-4z"/><path d="M10 1v4H7"/></svg>';
}

function deleteIconSVG() {
  return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M2 4h12M5 4V3a1 1 0 011-1h4a1 1 0 011 1v1"/><path d="M3 4l1 10a1 1 0 001 1h6a1 1 0 001-1l1-10"/><path d="M6 7v5M10 7v5"/></svg>';
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

/* ---- Modal Controls ---- */

function openUploadModal() {
  if (!selectedKbId) {
    alert("请先选择一个知识库");
    return;
  }
  $("#upload-kb-name").textContent = selectedKbName;
  $("#upload-modal").style.display = "flex";
  resetUploadForm();
}

function closeUploadModal() {
  $("#upload-modal").style.display = "none";
  stopJobPolling();
  resetUploadForm();
}

function openDeleteModal(docId, docName) {
  pendingDeleteDocId = docId;
  pendingDeleteDocName = docName;
  $("#delete-doc-name").textContent = docName;
  $("#delete-modal").style.display = "flex";
}

function closeDeleteModal() {
  pendingDeleteDocId = null;
  pendingDeleteDocName = "";
  $("#delete-modal").style.display = "none";
}

// Modal event listeners
$("#upload-to-kb")?.addEventListener("click", openUploadModal);
$("#modal-close")?.addEventListener("click", closeUploadModal);
$("#modal-cancel")?.addEventListener("click", closeUploadModal);
$("#upload-modal")?.addEventListener("click", (e) => {
  if (e.target.id === "upload-modal") closeUploadModal();
});

$("#modal-close")?.addEventListener("click", closeUploadModal);
$("#delete-modal-close")?.addEventListener("click", closeDeleteModal);
$("#delete-cancel")?.addEventListener("click", closeDeleteModal);
$("#delete-modal")?.addEventListener("click", (e) => {
  if (e.target.id === "delete-modal") closeDeleteModal();
});

$("#delete-confirm")?.addEventListener("click", async () => {
  if (!pendingDeleteDocId || !selectedKbId) return;
  
  const btn = $("#delete-confirm");
  setLoading(btn, true);
  
  try {
    await request(`/api/knowledge-bases/${selectedKbId}/documents/${pendingDeleteDocId}`, { method: "DELETE" });
    closeDeleteModal();
    await loadKbDocuments();
    await loadKnowledgeBases();
  } catch (err) {
    console.error("删除失败:", err);
  } finally {
    setLoading(btn, false);
  }
});

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
  selectedKbName = kb.name;
  const detail = $("#kb-detail");
  if (detail) {
    detail.className = "kb-detail";
    detail.innerHTML = `
      <strong>${escapeHtml(kb.name)}</strong>
      <span>状态: ${escapeHtml(kb.status)} · 部门: ${escapeHtml(kb.default_department || "-")} · 权限: ${escapeHtml(kb.default_access_level || "-")}</span>
      <span>模型: ${escapeHtml(kb.embedding_model || "-")} · 重排: ${escapeHtml(kb.reranker_model || "-")}</span>
    `;
  }
  await loadKbDocuments();
  await loadKnowledgeBases();
}

async function loadKbDocuments() {
  const target = $("#kb-documents");
  if (!target || !selectedKbId) return;
  
  target.innerHTML = '<div class="detail-empty">加载中...</div>';
  
  try {
    const data = await request(`/api/knowledge-bases/${selectedKbId}/documents`);
    const docs = data.items || [];
    
    if (!docs.length) {
      target.innerHTML = '<div class="detail-empty">暂无文档。点击右上角上传按钮添加文档。</div>';
      return;
    }
    
    target.innerHTML = "";
    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "doc-row";
      
      const title = doc.title || doc.source_name || doc.source_uri || "未命名文档";
      const statusClass = doc.status === "active" ? "active" : doc.status === "pending" ? "pending" : "failed";
      const statusLabel = STATUS_LABELS[doc.last_ingest_status] || doc.last_ingest_status || doc.status;
      
      row.innerHTML = `
        <div class="doc-info">
          <div class="doc-title">${escapeHtml(title)}</div>
          <div class="doc-meta">
            <span class="doc-status ${statusClass}">${escapeHtml(statusLabel)}</span>
            <span>${escapeHtml(doc.doc_type || "-")}</span>
            <span>v${doc.version}</span>
            <span>${doc.parent_chunk_count || 0}P / ${doc.child_chunk_count || 0}C</span>
          </div>
        </div>
        <div class="doc-actions">
          <button type="button" class="text-btn danger" data-document-id="${doc.document_id}" title="删除文档">
            ${deleteIconSVG()}
            删除
          </button>
        </div>
      `;
      
      row.querySelector(".text-btn").addEventListener("click", () => {
        openDeleteModal(doc.document_id, title);
      });
      
      target.appendChild(row);
    });
  } catch (err) {
    target.innerHTML = '<div class="detail-empty">加载失败</div>';
    console.error("加载文档列表失败:", err);
  }
}

$("#refresh-kbs")?.addEventListener("click", loadKnowledgeBases);
$("#refresh-docs")?.addEventListener("click", loadKbDocuments);

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
  if (detail) {
    const existing = detail.querySelector(".reindex-hint");
    if (existing) existing.remove();
    
    const hint = document.createElement("span");
    hint.className = "reindex-hint";
    hint.style.cssText = "color: var(--accent); font-size: 12px; margin-top: 4px;";
    hint.textContent = `Reindex 预检: 约 ${data.estimated_documents || 0} 个文件`;
    detail.appendChild(hint);
  }
});

$("#go-to-kb")?.addEventListener("click", () => {
  switchPage("page-kbs");
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
  dropZone.addEventListener("click", (e) => {
    // 如果上传进行中，或者已经有文件选中，不要再次触发
    if (uploadPhase !== "idle") return;
    if (fileInput.files && fileInput.files.length > 0) return;
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
  const card = document.getElementById("result-card");
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
    // 刷新文档列表
    if (selectedKbId) await loadKbDocuments();
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

function resetUploadForm() {
  uploadPhase = "idle";
  lastJobId = "";
  resetDropZone();
  
  const stepper = document.getElementById("phase-stepper");
  if (stepper) stepper.style.display = "none";
  
  const fields = document.getElementById("result-fields");
  if (fields) {
    fields.style.display = "none";
    fields.innerHTML = "";
  }
  
  const tag = document.getElementById("result-status-tag");
  if (tag) tag.style.display = "none";
  
  const result = document.getElementById("upload-result");
  if (result) result.style.display = "none";
  
  const submit = document.getElementById("upload-submit");
  if (submit) {
    submit.disabled = false;
    submit.style.opacity = "";
    submit.style.pointerEvents = "";
  }
  
  if (jobOutput) jobOutput.textContent = "";
}

/* ---- Upload Submit ---- */

$("#upload-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (uploadPhase !== "idle") return;

  const form = new FormData();
  const file = fileInput.files[0];
  if (!file) {
    alert("请选择要上传的文件");
    return;
  }
  form.append("file", file);
  appendIfPresent(form, "source_uri", "#source-uri");
  appendIfPresent(form, "doc_type", "#doc-type");
  appendIfPresent(form, "title", "#title");

  const btn = document.getElementById("upload-submit");
  setLoading(btn, true);

  // 显示上传结果区域
  const result = document.getElementById("upload-result");
  if (result) result.style.display = "block";

  // clear previous timeout hints
  document.querySelectorAll(".timeout-hint").forEach((el) => el.remove());

  setUploadPhase("uploading");

  try {
    const res = await uploadWithProgress(form);

    // restore drop zone
    if (dropZone) dropZone.classList.remove("uploading");

    if (res.ok && res.data.job_id) {
      lastJobId = res.data.job_id;
      renderStructuredResult(res.data, "ingest");

      if (res.data.status === "success" && res.data.embedding_status === "succeeded") {
        setUploadPhase("complete");
        // 刷新文档列表
        if (selectedKbId) await loadKbDocuments();
      } else {
        setUploadPhase("processing");
        startJobPolling(res.data.job_id);
      }
    } else if (res.ok) {
      renderStructuredResult(res.data, "ingest");
      setUploadPhase("error");
    } else {
      handleUploadError(res.data.error || { message: `HTTP ${res.status}` });
    }
  } catch (err) {
    if (dropZone) dropZone.classList.remove("uploading");
    handleUploadError({ message: err.message });
  }

  setLoading(btn, false);
  // reset phase to idle after a short delay so user can upload again
  setTimeout(() => { uploadPhase = "idle"; }, 500);
});

/* ---- Answer ---- */

$("#answer")?.addEventListener("click", async () => {
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
  if (pipelineOutput) pipelineOutput.innerHTML = "";
  if (diagnosticOutput) diagnosticOutput.innerHTML = "";
  if (pipelineScore) {
    pipelineScore.className = "quality-pill neutral";
    pipelineScore.textContent = "评估中";
  }

  // disable textarea during request
  const textarea = $("#question");
  if (textarea) textarea.disabled = true;

  const result = await postQuery("/api/knowledge/answer");
  renderQuery(result);
  setLoading(btn, false);
  if (textarea) textarea.disabled = false;
});

/* ---- Keyboard: Ctrl/Cmd + Enter ---- */

$("#question")?.addEventListener("keydown", (e) => {
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
  renderPipelineDiagnostics(result);
}

function renderPipelineDiagnostics(result) {
  const metadata = result.metadata || {};
  const latencies = result.latencies_ms || {};
  const hits = result.hit_summary || [];
  const refs = result.references || [];
  const refusal = metadata.refusal_decision || {};
  const config = metadata.search_config || {};
  const context = metadata.context || {};
  const answerGeneration = metadata.answer_generation || {};
  const answerValidation = answerGeneration.answer_validation || {};
  const warningList = Array.isArray(metadata.warnings) ? metadata.warnings : [];
  const selectedHits = hits.filter((hit) => hit.selected);
  const rerankedHits = hits.filter((hit) => hit.rerank_rank != null);

  const steps = [
    {
      title: "问题接收",
      tone: result.error ? "bad" : "good",
      metric: result.trace_id ? `追踪 ${result.trace_id}` : "已提交",
      body: "前端只发送问题和业务筛选条件，身份与权限范围由服务端上下文决定。",
      details: [
        ["问题长度", `${($("#question")?.value || "").trim().length} 字`],
        ["知识库", selectedKbName || "未限定"],
      ],
    },
    {
      title: "问题理解",
      tone: result.status === "refused" && result.refusal_reason ? "warn" : "good",
      metric: latencyText(latencies.query_understanding_ms),
      body: result.effective_query ? `实际检索语句：${result.effective_query}` : "完成空问题、多意图和改写开关判断。",
      details: [
        ["改写结果", result.rewritten_query || "未改写"],
        ["多意图", metadata.multi_intent_detected ? `是，约 ${metadata.intent_count || 0} 个` : "否"],
      ],
    },
    {
      title: "权限与业务过滤",
      tone: "good",
      metric: latencyText(latencies.filter_build_ms),
      body: "服务端合并当前用户可访问范围、知识库范围、文档类型、来源和版本筛选。",
      details: [
        ["知识库范围", selectedKbId ? `#${selectedKbId}` : "可访问范围"],
        ["越权字段", result.error?.code === "forbidden_filter" ? "已拒绝" : "未发现"],
      ],
    },
    {
      title: "Query Embedding",
      tone: latencyTone(latencies.embedding_ms, 1200),
      metric: latencyText(latencies.embedding_ms),
      body: "使用配置中的 query embedding 生成向量，随后按模型名路由到对应向量列或表。",
      details: [
        ["检索模式", config.effective_search_mode || config.search_mode || "vector"],
        ["候选上限", config.top_k != null ? config.top_k : "-"],
      ],
    },
    {
      title: "Child Chunk 向量召回",
      tone: hits.length ? "good" : "bad",
      metric: `${hits.length} 个候选 · ${latencyText(latencies.vector_search_ms)}`,
      body: topHitText(hits),
      details: [
        ["最高向量分", formatScore(maxScore(hits, "vector_score"))],
        ["最低阈值", config.min_vector_score != null ? formatScore(config.min_vector_score) : "-"],
      ],
    },
    {
      title: "Rerank 重排",
      tone: rerankedHits.length ? "good" : (hits.length ? "warn" : "bad"),
      metric: `${rerankedHits.length || 0} 个已重排 · ${latencyText(latencies.rerank_ms)}`,
      body: rerankedHits.length ? rerankText(rerankedHits, refusal) : "没有可展示的 rerank 排序，可能未配置重排或没有召回候选。",
      details: [
        ["Top1 分数", formatScore(refusal.top1_score)],
        ["Top1 间隔", formatScore(refusal.top1_margin)],
      ],
    },
    {
      title: "证据判定",
      tone: refusal.should_refuse ? "bad" : (refusal.confidence === "medium" ? "warn" : "good"),
      metric: refusal.confidence ? `置信度 ${refusal.confidence}` : result.status || "-",
      body: refusal.should_refuse ? `触发拒答：${refusal.reason || result.refusal_reason || "证据不足"}` : "证据通过阈值检查，可以进入 parent 扩展和上下文组装。",
      details: [
        ["高于阈值候选", refusal.candidates_above_threshold ?? "-"],
        ["Rerank 阈值", config.min_rerank_score != null ? formatScore(config.min_rerank_score) : "-"],
      ],
    },
    {
      title: "Parent 扩展与上下文",
      tone: refs.length ? "good" : (result.status === "refused" ? "warn" : "bad"),
      metric: `${refs.length} 条引用 · ${latencyText((latencies.parent_expansion_ms || 0) + (latencies.context_assembly_ms || 0))}`,
      body: context.candidates_included != null ? `已纳入 ${context.candidates_included} 个 parent，丢弃 ${context.candidates_dropped || 0} 个。` : "未生成可用上下文。",
      details: [
        ["上下文 tokens", context.total_tokens != null ? `${context.total_tokens}/${config.max_context_tokens || "-"}` : "-"],
        ["截断", context.truncation_applied ? "有" : "无"],
      ],
    },
    {
      title: "带引用回答",
      tone: result.status === "success" ? "good" : (result.status === "refused" ? "warn" : "bad"),
      metric: latencyText(answerGeneration.latency_ms),
      body: answerValidation.valid === false ? `引用校验失败：${answerValidation.reason || "-"}` : answerStatusText(result),
      details: [
        ["回答状态", result.status || "-"],
        ["引用数量", refs.length],
      ],
    },
  ];

  if (pipelineOutput) {
    pipelineOutput.innerHTML = steps.map(renderPipelineStep).join("");
  }
  if (diagnosticOutput) {
    diagnosticOutput.innerHTML = renderQualityCards(result, steps, warningList, selectedHits);
  }
  if (pipelineScore) {
    const score = overallQuality(result, steps, warningList);
    pipelineScore.className = `quality-pill ${score.tone}`;
    pipelineScore.textContent = score.label;
  }
}

function renderPipelineStep(step, index) {
  const details = (step.details || [])
    .map(([label, value]) => `
      <span class="pipeline-kv">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(String(value ?? "-"))}</strong>
      </span>
    `)
    .join("");
  return `
    <article class="pipeline-step ${step.tone}">
      <div class="pipeline-index">${index + 1}</div>
      <div class="pipeline-content">
        <div class="pipeline-head">
          <strong>${escapeHtml(step.title)}</strong>
          <span>${escapeHtml(step.metric || "-")}</span>
        </div>
        <p>${escapeHtml(step.body || "")}</p>
        <div class="pipeline-details">${details}</div>
      </div>
    </article>
  `;
}

function renderQualityCards(result, steps, warnings, selectedHits) {
  const badCount = steps.filter((step) => step.tone === "bad").length;
  const warnCount = steps.filter((step) => step.tone === "warn").length + warnings.length;
  const refs = result.references || [];
  const latencies = result.latencies_ms || {};
  const topSelected = selectedHits[0];
  const cards = [
    ["总体状态", result.status || "unknown", badCount ? "bad" : (warnCount ? "warn" : "good")],
    ["可用引用", `${refs.length} 条`, refs.length ? "good" : "warn"],
    ["选中命中", `${selectedHits.length} 条`, selectedHits.length ? "good" : "warn"],
    ["最高相关分", topSelected ? formatScore(topSelected.rerank_score || topSelected.vector_score) : "-", topSelected ? "good" : "warn"],
    ["总耗时", latencyText(latencies.total_ms), latencyTone(latencies.total_ms, 3500)],
    ["风险提示", warnings.length ? `${warnings.length} 条` : "无", warnings.length ? "warn" : "good"],
  ];
  return cards.map(([label, value, tone]) => `
    <div class="diagnostic-item ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
}

function overallQuality(result, steps, warnings) {
  if (result.error || result.status === "failed" || steps.some((step) => step.tone === "bad")) {
    return { tone: "bad", label: "需要排查" };
  }
  if (result.status === "refused") {
    return { tone: "warn", label: "已拒答" };
  }
  if (warnings.length || steps.some((step) => step.tone === "warn")) {
    return { tone: "warn", label: "可用但需复核" };
  }
  return { tone: "good", label: "链路健康" };
}

function topHitText(hits) {
  if (!hits.length) return "没有召回 child chunk，需要检查知识库范围、embedding 或切片质量。";
  const top = hits.reduce((best, item) => (item.vector_score > best.vector_score ? item : best), hits[0]);
  return `Top${top.vector_rank} 命中 chunk ${top.chunk_key}，parent ${top.parent_key}。`;
}

function rerankText(hits, refusal) {
  const top = hits.reduce((best, item) => (item.rerank_rank < best.rerank_rank ? item : best), hits[0]);
  const reason = refusal.confidence === "medium" ? "Top1 间隔偏小，建议人工复核。" : "重排后候选已按相关性排序。";
  return `Top${top.rerank_rank} 为 chunk ${top.chunk_key}，${reason}`;
}

function answerStatusText(result) {
  if (result.status === "success") return "答案已生成，并通过引用编号约束。";
  if (result.status === "refused") return `当前资料无法确认：${result.refusal_reason || "证据不足"}`;
  return result.error?.message || result.refusal_reason || "链路未完成。";
}

function maxScore(items, key) {
  if (!items.length) return null;
  return Math.max(...items.map((item) => Number(item[key] || 0)));
}

function formatScore(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(4);
}

function latencyText(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return `${Number(value)} ms`;
}

function latencyTone(value, warnAt) {
  if (value == null || Number.isNaN(Number(value))) return "neutral";
  return Number(value) > warnAt ? "warn" : "good";
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

/* ---- Initialize ---- */

// Load knowledge bases on page load
loadKnowledgeBases();
