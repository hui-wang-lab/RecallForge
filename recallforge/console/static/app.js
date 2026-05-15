let lastJobId = "";
let uploadPhase = "idle";
let pollTimer = null;
let pollStartTime = 0;
let selectedKbId = null;
let selectedKbName = "";
let selectedKb = null;
let selectedDocumentId = null;
let selectedDocumentTitle = "";
let currentKbView = "overview";
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
const answerOutput = $("#answer-output");
const answerArea = $("#answer-area");
const pipelineOutput = $("#pipeline-output");
const diagnosticOutput = $("#diagnostic-output");
const pipelineScore = $("#pipeline-score");

const pageTitles = {
  "page-kbs": "知识库管理",
  "page-answer": "召回调试",
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

$$("[data-kb-view-target]").forEach((button) => {
  button.addEventListener("click", () => switchKbView(button.dataset.kbViewTarget));
});

function switchPage(pageId) {
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

function switchKbView(view) {
  currentKbView = view || "overview";
  $$("[data-kb-view]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.kbView === currentKbView);
  });
  $$("[data-kb-view-target]").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.kbViewTarget === currentKbView);
  });
  if (currentKbView === "jobs") loadKbJobs();
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
    const deletedId = pendingDeleteDocId;
    const data = await request(`/api/knowledge-bases/${selectedKbId}/documents/${deletedId}`, { method: "DELETE" });
    if (apiFailed(data)) {
      alert(`删除失败：${apiErrorText(data)}`);
      return;
    }
    closeDeleteModal();
    if (selectedDocumentId === deletedId) hideDocumentDetail();
    await loadKbDocuments();
    await loadKbJobs();
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
  if (apiFailed(data)) {
    renderKbEmpty(`加载失败：${apiErrorText(data)}`);
    return;
  }
  const items = data.items || [];
  const count = $("#kb-count");
  if (count) count.textContent = `${items.length} 个`;
  const list = $("#kb-list");
  if (!list) return;

  const matched = selectedKbId ? items.find((kb) => kb.knowledge_base_id === selectedKbId) : null;
  if (selectedKbId && !matched) clearKnowledgeBaseSelection();
  if (!selectedKbId && items[0]) {
    selectedKb = items[0];
    selectedKbId = items[0].knowledge_base_id;
    selectedKbName = items[0].name;
  } else if (matched) {
    selectedKb = matched;
    selectedKbName = matched.name;
  }

  list.innerHTML = "";
  items.forEach((kb) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `kb-row${selectedKbId === kb.knowledge_base_id ? " active" : ""}`;
    button.dataset.kbId = String(kb.knowledge_base_id);
    button.innerHTML = `
      <span class="kb-row-title">${escapeHtml(kb.name)}</span>
      <span class="kb-row-meta">${escapeHtml(kb.role || "-")} · ${kb.document_count || 0} 文件 · ${kb.active_chunk_count || 0} chunks</span>
    `;
    button.addEventListener("click", () => selectKnowledgeBase(kb));
    list.appendChild(button);
  });

  if (selectedKb) {
    renderKnowledgeBaseDetail(selectedKb);
    await Promise.all([loadKbDocuments(), loadKbJobs()]);
  } else {
    renderKbEmpty("暂无知识库，先创建一个知识库。");
  }
}

async function selectKnowledgeBase(kb) {
  selectedKb = kb;
  selectedKbId = kb.knowledge_base_id;
  selectedKbName = kb.name;
  selectedDocumentId = null;
  selectedDocumentTitle = "";
  updateKbActiveRows();
  renderKnowledgeBaseDetail(kb);
  hideKbEditor();
  hideDocumentDetail();
  renderChunkEmpty("选择一个文件后查看切片。");
  await Promise.all([loadKbDocuments(), loadKbJobs()]);
}

function clearKnowledgeBaseSelection() {
  selectedKb = null;
  selectedKbId = null;
  selectedKbName = "";
  selectedDocumentId = null;
  selectedDocumentTitle = "";
}

function renderKbEmpty(message) {
  const detail = $("#kb-detail");
  const docs = $("#kb-documents");
  const jobs = $("#kb-jobs");
  const docCount = $("#doc-count");
  if (detail) detail.innerHTML = `<div class="detail-empty">${escapeHtml(message)}</div>`;
  if (docs) docs.innerHTML = "";
  if (jobs) jobs.innerHTML = "";
  if (docCount) docCount.textContent = "";
  renderChunkEmpty("选择一个文件后查看切片。");
  renderOverview(null);
  hideKbEditor();
  hideDocumentDetail();
}

function updateKbActiveRows() {
  $$(".kb-row").forEach((row) => {
    row.classList.toggle("active", Number(row.dataset.kbId) === selectedKbId);
  });
}

function renderKnowledgeBaseDetail(kb) {
  const detail = $("#kb-detail");
  if (!detail) return;
  const kbDept = getKbField(kb, "default_" + "depart" + "ment") || "-";
  const kbLevel = getKbField(kb, "default_" + "access" + "_level") || "-";
  const actions = kb.actions || {};
  detail.className = "kb-detail rich";
  detail.innerHTML = `
    <div class="kb-detail-head">
      <div>
        <strong>${escapeHtml(kb.name)}</strong>
        <span>${escapeHtml(kb.description || "无描述")}</span>
      </div>
      <span class="status-pill ${escapeHtml(kb.status || "active")}">${escapeHtml(kb.status || "-")}</span>
    </div>
    <div class="metric-grid">
      <span><strong>${kb.document_count || 0}</strong> 文件</span>
      <span><strong>${kb.active_chunk_count || 0}</strong> chunks</span>
      <span><strong>${escapeHtml(kb.role || "-")}</strong> 角色</span>
    </div>
    <div class="detail-lines">
      <span>默认范围: ${escapeHtml(kbDept)} · ${escapeHtml(kbLevel)}</span>
      <span>解析: ${escapeHtml(kb.default_parser || "auto")} · 模板: ${escapeHtml(kb.default_template || "auto")} · 检索: ${escapeHtml(kb.default_search_mode || "vector")}</span>
      <span>top_k: ${escapeHtml(kb.default_top_k ?? "-")} · final_top_k: ${escapeHtml(kb.default_final_top_k ?? "-")}</span>
      <span>标签: ${escapeHtml((kb.tags || []).join(", ") || "-")}</span>
    </div>
    <div class="detail-actions">
      <button type="button" class="text-btn" data-kb-action="edit" ${actions.can_update === false ? "disabled" : ""}>编辑</button>
      <button type="button" class="text-btn" data-kb-action="reindex" ${actions.can_reindex === false ? "disabled" : ""}>reindex 预检</button>
      <button type="button" class="text-btn danger" data-kb-action="archive" ${actions.can_delete === false ? "disabled" : ""}>归档</button>
    </div>
  `;
  detail.querySelector('[data-kb-action="edit"]')?.addEventListener("click", showKbEditForm);
  detail.querySelector('[data-kb-action="reindex"]')?.addEventListener("click", dryRunReindex);
  detail.querySelector('[data-kb-action="archive"]')?.addEventListener("click", () => changeKnowledgeBaseStatus("archive"));
  renderOverview(kb);
}

function renderOverview(kb) {
  const target = $("#kb-overview");
  if (!target) return;
  if (!kb) {
    target.innerHTML = '<div class="detail-empty">暂无知识库。</div>';
    return;
  }
  target.innerHTML = `
    <article class="overview-item">
      <span>文件治理</span>
      <strong>${kb.document_count || 0}</strong>
      <p>进入“文件”页上传、查看详情、编辑 metadata 或删除文件。</p>
      <button type="button" class="text-btn" data-overview-jump="files">查看文件</button>
    </article>
    <article class="overview-item">
      <span>切片诊断</span>
      <strong>${kb.active_chunk_count || 0}</strong>
      <p>选择单个文件后查看 parent / child 切片、页码和 embedding 信息。</p>
      <button type="button" class="text-btn" data-overview-jump="chunks">查看切片</button>
    </article>
    <article class="overview-item">
      <span>任务中心</span>
      <strong>${escapeHtml(kb.last_ingest_status || "-")}</strong>
      <p>查看最近导入任务状态，上传完成后会自动刷新。</p>
      <button type="button" class="text-btn" data-overview-jump="jobs">查看任务</button>
    </article>
  `;
  target.querySelectorAll("[data-overview-jump]").forEach((button) => {
    button.addEventListener("click", () => switchKbView(button.dataset.overviewJump));
  });
}

function getKbField(kb, key) {
  return kb ? kb[key] : null;
}

function showKbEditForm() {
  if (!selectedKb) return;
  const target = $("#kb-editor");
  if (!target) return;
  target.style.display = "grid";
  target.innerHTML = `
    <form id="kb-edit-form" class="editor-form">
      <div class="form-grid compact-grid">
        <div class="field">
          <label for="kb-edit-name">名称</label>
          <input id="kb-edit-name" type="text" value="${escapeAttr(selectedKb.name || "")}" required />
        </div>
        <div class="field">
          <label for="kb-edit-tags">标签</label>
          <input id="kb-edit-tags" type="text" value="${escapeAttr((selectedKb.tags || []).join(", "))}" placeholder="产品, 制度" />
        </div>
        <div class="field full">
          <label for="kb-edit-description">描述</label>
          <input id="kb-edit-description" type="text" value="${escapeAttr(selectedKb.description || "")}" />
        </div>
        <div class="field">
          <label for="kb-edit-parser">默认解析器</label>
          <input id="kb-edit-parser" type="text" value="${escapeAttr(selectedKb.default_parser || "auto")}" />
        </div>
        <div class="field">
          <label for="kb-edit-template">默认模板</label>
          <input id="kb-edit-template" type="text" value="${escapeAttr(selectedKb.default_template || "auto")}" />
        </div>
        <div class="field">
          <label for="kb-edit-topk">召回 top_k</label>
          <input id="kb-edit-topk" type="number" min="1" value="${escapeAttr(selectedKb.default_top_k || "")}" />
        </div>
        <div class="field">
          <label for="kb-edit-final-topk">最终 top_k</label>
          <input id="kb-edit-final-topk" type="number" min="1" value="${escapeAttr(selectedKb.default_final_top_k || "")}" />
        </div>
      </div>
      <div class="editor-actions">
        <button type="button" class="secondary-btn" id="kb-edit-cancel">取消</button>
        <button type="submit" class="primary-btn compact">保存</button>
      </div>
    </form>
  `;
  $("#kb-edit-cancel")?.addEventListener("click", hideKbEditor);
  $("#kb-edit-form")?.addEventListener("submit", submitKbEdit);
}

function hideKbEditor() {
  const target = $("#kb-editor");
  if (target) {
    target.style.display = "none";
    target.innerHTML = "";
  }
}

async function submitKbEdit(event) {
  event.preventDefault();
  if (!selectedKbId) return;
  const form = event.currentTarget;
  const payload = {
    name: $("#kb-edit-name").value.trim(),
    description: $("#kb-edit-description").value.trim() || null,
    tags: $("#kb-edit-tags").value.split(",").map((item) => item.trim()).filter(Boolean),
    default_parser: $("#kb-edit-parser").value.trim() || "auto",
    default_template: $("#kb-edit-template").value.trim() || "auto",
  };
  const topK = $("#kb-edit-topk").value.trim();
  const finalTopK = $("#kb-edit-final-topk").value.trim();
  if (topK) payload.default_top_k = Number(topK);
  if (finalTopK) payload.default_final_top_k = Number(finalTopK);
  setLoading(form.querySelector('button[type="submit"]'), true);
  const data = await request(`/api/knowledge-bases/${selectedKbId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setLoading(form.querySelector('button[type="submit"]'), false);
  if (apiFailed(data)) {
    alert(`保存失败：${apiErrorText(data)}`);
    return;
  }
  hideKbEditor();
  selectedKb = data;
  selectedKbName = data.name;
  await loadKnowledgeBases();
}

async function changeKnowledgeBaseStatus(mode) {
  if (!selectedKbId || !selectedKb) return;
  const label = mode === "delete" ? "删除" : "归档";
  if (!window.confirm(`确认${label}知识库「${selectedKbName}」？关联文件将不再出现在 active 列表中。`)) return;
  const data = await request(`/api/knowledge-bases/${selectedKbId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, reason: "console action" }),
  });
  if (apiFailed(data)) {
    alert(`${label}失败：${apiErrorText(data)}`);
    return;
  }
  clearKnowledgeBaseSelection();
  await loadKnowledgeBases();
}

async function loadKbDocuments() {
  const target = $("#kb-documents");
  const count = $("#doc-count");
  if (!target || !selectedKbId) return;
  
  target.innerHTML = '<div class="detail-empty">加载中...</div>';
  
  try {
    const data = await request(`/api/knowledge-bases/${selectedKbId}/documents`);
    if (apiFailed(data)) {
      target.innerHTML = `<div class="detail-empty">加载失败：${escapeHtml(apiErrorText(data))}</div>`;
      if (count) count.textContent = "";
      return;
    }
    const docs = data.items || [];
    if (count) count.textContent = `${docs.length} 个`;
    
    if (!docs.length) {
      target.innerHTML = '<div class="detail-empty">暂无文档。点击右上角上传按钮添加文档。</div>';
      return;
    }
    
    target.innerHTML = "";
    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = `doc-row${selectedDocumentId === doc.document_id ? " active" : ""}`;
      row.dataset.documentId = String(doc.document_id);
      
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
          <button type="button" class="text-btn" data-doc-action="detail" title="查看详情">详情</button>
          <button type="button" class="text-btn" data-doc-action="edit" title="编辑文档">编辑</button>
          <button type="button" class="text-btn" data-doc-action="chunks" title="查看切片">切片</button>
          <button type="button" class="text-btn danger" data-doc-action="delete" data-document-id="${doc.document_id}" title="删除文档">
            ${deleteIconSVG()}
            删除
          </button>
        </div>
      `;
      
      row.addEventListener("click", () => loadDocumentDetail(doc.document_id, false));
      row.querySelector('[data-doc-action="detail"]').addEventListener("click", (event) => {
        event.stopPropagation();
        loadDocumentDetail(doc.document_id, false);
      });
      row.querySelector('[data-doc-action="edit"]').addEventListener("click", (event) => {
        event.stopPropagation();
        loadDocumentDetail(doc.document_id, true);
      });
      row.querySelector('[data-doc-action="chunks"]').addEventListener("click", (event) => {
        event.stopPropagation();
        loadDocumentChunks(doc.document_id, title);
      });
      row.querySelector('[data-doc-action="delete"]').addEventListener("click", (event) => {
        event.stopPropagation();
        openDeleteModal(doc.document_id, title);
      });
      
      target.appendChild(row);
    });
  } catch (err) {
    target.innerHTML = '<div class="detail-empty">加载失败</div>';
    console.error("加载文档列表失败:", err);
  }
}

async function loadDocumentDetail(documentId, editing) {
  if (!selectedKbId) return;
  selectedDocumentId = documentId;
  $$(".doc-row").forEach((row) => row.classList.toggle("active", Number(row.dataset.documentId) === documentId));
  const target = $("#document-detail");
  if (!target) return;
  target.style.display = "grid";
  target.innerHTML = '<div class="detail-empty">加载文档详情...</div>';
  const data = await request(`/api/knowledge-bases/${selectedKbId}/documents/${documentId}`);
  if (apiFailed(data)) {
    target.innerHTML = `<div class="detail-empty">加载失败：${escapeHtml(apiErrorText(data))}</div>`;
    return;
  }
  selectedDocumentTitle = data.title || data.source_name || data.source_uri || "未命名文档";
  renderDocumentDetail(data, editing);
}

function renderDocumentDetail(doc, editing) {
  const target = $("#document-detail");
  if (!target) return;
  const title = doc.title || doc.source_name || doc.source_uri || "未命名文档";
  target.style.display = "grid";
  target.innerHTML = `
    <div class="doc-detail-head">
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(doc.source_uri || "-")}</span>
      </div>
      <span class="status-pill ${escapeHtml(doc.status || "active")}">${escapeHtml(doc.status || "-")}</span>
    </div>
    <div class="metric-grid">
      <span><strong>v${doc.version}</strong> 版本</span>
      <span><strong>${doc.parent_chunk_count || 0}</strong> parent</span>
      <span><strong>${doc.child_chunk_count || 0}</strong> child</span>
      <span><strong>${escapeHtml(doc.embedding_status || "-")}</strong> embedding</span>
    </div>
    <div class="detail-lines">
      <span>类型: ${escapeHtml(doc.doc_type || "-")} · 最近任务: ${escapeHtml(doc.last_ingest_status || "-")}</span>
      <span>hash: ${escapeHtml(doc.content_hash || "-")}</span>
      <span>创建: ${escapeHtml(formatDate(doc.created_at))} · 更新: ${escapeHtml(formatDate(doc.updated_at))}</span>
    </div>
    <div class="detail-actions">
      <button type="button" class="text-btn" id="doc-edit-toggle">${editing ? "收起编辑" : "编辑 metadata"}</button>
      <button type="button" class="text-btn" id="doc-chunks-inline">查看切片</button>
      <button type="button" class="text-btn danger" id="doc-delete-inline">删除</button>
    </div>
    <form id="doc-edit-form" class="editor-form" style="display:${editing ? "grid" : "none"}">
      <div class="form-grid compact-grid">
        <div class="field">
          <label for="doc-edit-title">标题</label>
          <input id="doc-edit-title" type="text" value="${escapeAttr(doc.title || "")}" />
        </div>
        <div class="field">
          <label for="doc-edit-source-name">显示文件名</label>
          <input id="doc-edit-source-name" type="text" value="${escapeAttr(doc.source_name || "")}" />
        </div>
        <div class="field">
          <label for="doc-edit-type">文档类型</label>
          <input id="doc-edit-type" type="text" value="${escapeAttr(doc.doc_type || "")}" />
        </div>
        <div class="field full">
          <label for="doc-edit-metadata">业务 metadata JSON</label>
          <input id="doc-edit-metadata" type="text" placeholder='{"product":"demo"}' />
        </div>
      </div>
      <div class="editor-actions">
        <button type="button" class="secondary-btn" id="doc-edit-cancel">取消</button>
        <button type="submit" class="primary-btn compact">保存文档</button>
      </div>
    </form>
  `;
  $("#doc-edit-toggle")?.addEventListener("click", () => {
    const form = $("#doc-edit-form");
    if (form) form.style.display = form.style.display === "none" ? "grid" : "none";
  });
  $("#doc-edit-cancel")?.addEventListener("click", () => {
    const form = $("#doc-edit-form");
    if (form) form.style.display = "none";
  });
  $("#doc-delete-inline")?.addEventListener("click", () => openDeleteModal(doc.document_id, title));
  $("#doc-chunks-inline")?.addEventListener("click", () => loadDocumentChunks(doc.document_id, title));
  $("#doc-edit-form")?.addEventListener("submit", submitDocumentEdit);
}

function hideDocumentDetail() {
  selectedDocumentId = null;
  const target = $("#document-detail");
  if (target) {
    target.style.display = "none";
    target.innerHTML = "";
  }
}

async function loadDocumentChunks(documentId, title) {
  if (!selectedKbId) return;
  selectedDocumentId = documentId;
  selectedDocumentTitle = title || selectedDocumentTitle || "当前文件";
  $$(".doc-row").forEach((row) => row.classList.toggle("active", Number(row.dataset.documentId) === documentId));
  switchKbView("chunks");
  const target = $("#chunk-detail");
  const count = $("#chunk-count");
  if (!target) return;
  target.innerHTML = '<div class="detail-empty">加载切片...</div>';
  if (count) count.textContent = "";
  const data = await request(`/api/knowledge-bases/${selectedKbId}/documents/${documentId}/chunks`);
  if (apiFailed(data)) {
    target.innerHTML = `<div class="detail-empty">加载失败：${escapeHtml(apiErrorText(data))}</div>`;
    return;
  }
  renderDocumentChunks(data, selectedDocumentTitle);
}

function renderDocumentChunks(data, title) {
  const target = $("#chunk-detail");
  const count = $("#chunk-count");
  if (!target) return;
  const parents = data.items || [];
  if (count) count.textContent = `${data.parent_chunk_count || parents.length} parent / ${data.child_chunk_count || 0} child`;
  if (!parents.length) {
    target.innerHTML = '<div class="detail-empty">这个文件还没有 active 切片。</div>';
    return;
  }
  target.innerHTML = `
    <div class="chunk-toolbar">
      <div>
        <strong>${escapeHtml(title || "当前文件")}</strong>
        <span>${data.parent_chunk_count || parents.length} parent · ${data.child_chunk_count || 0} child</span>
      </div>
      <button type="button" class="text-btn" id="back-to-files">返回文件</button>
    </div>
    ${parents.map(renderParentChunk).join("")}
  `;
  $("#back-to-files")?.addEventListener("click", () => switchKbView("files"));
}

function renderParentChunk(parent) {
  const heading = Array.isArray(parent.heading_path) && parent.heading_path.length ? parent.heading_path.join(" / ") : "无标题路径";
  const children = parent.child_chunks || [];
  return `
    <article class="chunk-parent">
      <details open>
        <summary>
          <span>Parent #${parent.chunk_index} · ${escapeHtml(parent.parent_key)}</span>
          <small>${children.length} child · ${escapeHtml(pageLabel(parent.page_start, parent.page_end))}</small>
        </summary>
        <div class="chunk-meta">
          <span>${escapeHtml(heading)}</span>
          <span>tokens ${parent.token_count ?? "-"}</span>
          <span>${escapeHtml(parent.status || "-")}</span>
        </div>
        <pre class="chunk-content">${escapeHtml(parent.content || "")}</pre>
        <div class="child-chunk-list">
          ${children.map(renderChildChunk).join("") || '<div class="detail-empty">没有 child chunk。</div>'}
        </div>
      </details>
    </article>
  `;
}

function renderChildChunk(child) {
  const heading = Array.isArray(child.heading_path) && child.heading_path.length ? child.heading_path.join(" / ") : "-";
  return `
    <article class="chunk-child">
      <div class="chunk-child-head">
        <strong>Child #${child.chunk_index} · ${escapeHtml(child.chunk_key)}</strong>
        <span>${escapeHtml(child.embedding_model || "-")} / ${child.embedding_dim || "-"}</span>
      </div>
      <div class="chunk-meta">
        <span>${escapeHtml(heading)}</span>
        <span>页码 ${escapeHtml(pageLabel(child.page_start, child.page_end))}</span>
        <span>${escapeHtml(child.status || "-")}</span>
      </div>
      <pre class="chunk-content child">${escapeHtml(child.content || "")}</pre>
    </article>
  `;
}

function renderChunkEmpty(message) {
  const target = $("#chunk-detail");
  const count = $("#chunk-count");
  if (target) target.innerHTML = `<div class="detail-empty">${escapeHtml(message)}</div>`;
  if (count) count.textContent = "";
}

async function submitDocumentEdit(event) {
  event.preventDefault();
  if (!selectedKbId || !selectedDocumentId) return;
  const form = event.currentTarget;
  const payload = {};
  addTrimmed(payload, "title", "#doc-edit-title");
  addTrimmed(payload, "source_name", "#doc-edit-source-name");
  addTrimmed(payload, "doc_type", "#doc-edit-type");
  const metadataText = $("#doc-edit-metadata").value.trim();
  if (metadataText) {
    try {
      payload.metadata = JSON.parse(metadataText);
    } catch {
      alert("metadata 必须是合法 JSON 对象");
      return;
    }
    if (!payload.metadata || Array.isArray(payload.metadata) || typeof payload.metadata !== "object") {
      alert("metadata 必须是 JSON 对象");
      return;
    }
  }
  if (!Object.keys(payload).length) {
    alert("没有需要保存的变更");
    return;
  }
  const submit = form.querySelector('button[type="submit"]');
  setLoading(submit, true);
  const data = await request(`/api/knowledge-bases/${selectedKbId}/documents/${selectedDocumentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setLoading(submit, false);
  if (apiFailed(data)) {
    alert(`保存失败：${apiErrorText(data)}`);
    return;
  }
  renderDocumentDetail(data, false);
  await loadKbDocuments();
}

async function loadKbJobs() {
  const target = $("#kb-jobs");
  if (!target || !selectedKbId) return;
  target.innerHTML = '<div class="detail-empty">加载任务...</div>';
  const data = await request(`/api/knowledge-bases/${selectedKbId}/ingest-jobs?limit=8`);
  if (apiFailed(data)) {
    target.innerHTML = `<div class="detail-empty">加载失败：${escapeHtml(apiErrorText(data))}</div>`;
    return;
  }
  const jobs = Array.isArray(data) ? data : [];
  if (!jobs.length) {
    target.innerHTML = '<div class="detail-empty">暂无导入任务。</div>';
    return;
  }
  target.innerHTML = jobs.map((job) => `
    <article class="job-row">
      <div>
        <strong>${escapeHtml(job.source_name || job.source_uri || "未命名任务")}</strong>
        <span>${escapeHtml(job.job_id || "-")}</span>
      </div>
      <div class="job-row-meta">
        <span class="doc-status ${job.status === "success" || job.status === "skipped_duplicate" ? "active" : (job.status === "failed" ? "failed" : "pending")}">${escapeHtml(STATUS_LABELS[job.status] || job.status || "-")}</span>
        <span>${escapeHtml(job.doc_type || "-")}</span>
        <span>${job.parent_chunk_count || 0}P / ${job.child_chunk_count || 0}C</span>
        <span>${escapeHtml(formatDate(job.updated_at || job.created_at))}</span>
      </div>
    </article>
  `).join("");
}

$("#refresh-kbs")?.addEventListener("click", loadKnowledgeBases);
$("#refresh-docs")?.addEventListener("click", loadKbDocuments);
$("#refresh-jobs")?.addEventListener("click", loadKbJobs);
$("#edit-kb")?.addEventListener("click", showKbEditForm);
$("#archive-kb")?.addEventListener("click", () => changeKnowledgeBaseStatus("archive"));

$("#kb-create-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#kb-name");
  const name = input.value.trim();
  if (!name) return;
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  setLoading(submit, true);
  const data = await request("/api/knowledge-bases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  setLoading(submit, false);
  if (apiFailed(data)) {
    alert(`创建失败：${apiErrorText(data)}`);
    return;
  }
  input.value = "";
  if (data.knowledge_base_id) {
    selectedKb = data;
    selectedKbId = data.knowledge_base_id;
    selectedKbName = data.name;
  }
  await loadKnowledgeBases();
});

$("#reindex-kb")?.addEventListener("click", dryRunReindex);

async function dryRunReindex() {
  if (!selectedKbId) return;
  const data = await request(`/api/knowledge-bases/${selectedKbId}/reindex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dry_run: true }),
  });
  if (apiFailed(data)) {
    alert(`reindex 预检失败：${apiErrorText(data)}`);
    return;
  }
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
  if ((data.estimated_documents || 0) > 0 && window.confirm(`预检约 ${data.estimated_documents} 个文件。现在提交 reindex 任务？`)) {
    await submitReindexRun();
  }
}

async function submitReindexRun() {
  const data = await request(`/api/knowledge-bases/${selectedKbId}/reindex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dry_run: false }),
  });
  if (apiFailed(data)) {
    alert(`reindex 提交失败：${apiErrorText(data)}`);
    return;
  }
  alert(`reindex 已提交：${data.status || "queued"}`);
  await loadKbJobs();
}

function addTrimmed(payload, key, selector) {
  const value = $(selector)?.value.trim();
  if (value) payload[key] = value;
}

function apiFailed(data) {
  return Boolean(data && data.error);
}

function apiErrorText(data) {
  return data?.error?.message || data?.error?.code || "请求失败";
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

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

function escapeAttr(str) {
  return escapeHtml(String(str ?? "")).replace(/"/g, "&quot;");
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
    if (selectedKbId) await loadKbJobs();
  } else if (data.status === "failed") {
    setUploadPhase("error");
    stopJobPolling();
    if (selectedKbId) await loadKbJobs();
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
        if (selectedKbId) await loadKbJobs();
      } else {
        setUploadPhase("processing");
        if (selectedKbId) await loadKbJobs();
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
      if (!data.error) data.error = { code: "http_error", message: `HTTP ${response.status}` };
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
  if (answerOutput) answerOutput.textContent = debugConclusion(result);
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
  const vectorHits = [...hits].sort((a, b) => (a.vector_rank || 9999) - (b.vector_rank || 9999));
  const rerankOutput = [...rerankedHits].sort((a, b) => (a.rerank_rank || 9999) - (b.rerank_rank || 9999));
  const selectedOutput = selectedHits.length ? selectedHits : refs;
  const question = ($("#question")?.value || "").trim();

  const steps = [
    {
      title: "问题接收",
      tone: result.error ? "bad" : "good",
      metric: result.trace_id ? `追踪 ${result.trace_id}` : "已提交",
      body: "确认本次调试请求的用户输入和业务范围。",
      input: [
        ["原始问题", question || "-"],
        ["页面选择", selectedKbName || "未限定知识库"],
      ],
      output: [
        ["请求端点", "/api/knowledge/answer"],
        ["业务筛选", selectedKbId ? `knowledge_base_id=${selectedKbId}` : "无"],
      ],
      details: [
        ["问题长度", `${question.length} 字`],
        ["状态", result.error ? "请求失败" : "已接收"],
      ],
    },
    {
      title: "问题理解",
      tone: result.status === "refused" && result.refusal_reason ? "warn" : "good",
      metric: latencyText(latencies.query_understanding_ms),
      body: "判断是否拒绝空问题，是否存在多意图，并确定最终进入 embedding 的问题文本。",
      input: [["原始问题", question || "-"]],
      output: [
        ["有效检索语句", result.effective_query || "-"],
        ["改写结果", result.rewritten_query || "未改写"],
        ["多意图", metadata.multi_intent_detected ? `是，约 ${metadata.intent_count || 0} 个` : "否"],
      ],
      details: [
        ["rewrite", config.query_rewrite_enabled ? "开启" : "关闭"],
        ["HyDE", config.hyde_enabled ? "开启" : "关闭"],
      ],
    },
    {
      title: "权限与业务过滤",
      tone: "good",
      metric: latencyText(latencies.filter_build_ms),
      body: "前端不传身份和权限字段，服务端注入可访问范围并校验业务筛选白名单。",
      input: [
        ["客户端筛选", selectedKbId ? `knowledge_base_id=${selectedKbId}` : "无"],
      ],
      output: [
        ["知识库范围", selectedKbId ? `#${selectedKbId}` : "当前用户可访问知识库"],
        ["固定状态", "active"],
        ["越权字段", result.error?.code === "forbidden_filter" ? "已拒绝" : "未发现"],
      ],
      details: [
        ["白名单", "doc_type / source_uri / version / knowledge_base_id"],
      ],
    },
    {
      title: "Query Embedding",
      tone: latencyTone(latencies.embedding_ms, 1200),
      metric: latencyText(latencies.embedding_ms),
      body: "把有效检索语句转换为 query embedding，随后按 embedding_model 路由向量列。",
      input: [["Embedding 输入", result.effective_query || question || "-"]],
      output: [
        ["检索模式", config.effective_search_mode || config.search_mode || "vector"],
        ["召回 top_k", config.top_k != null ? config.top_k : "-"],
      ],
      details: [
        ["耗时", latencyText(latencies.embedding_ms)],
      ],
    },
    {
      title: "Child Chunk 向量召回",
      tone: hits.length ? "good" : "bad",
      metric: `${hits.length} 个候选 · ${latencyText(latencies.vector_search_ms)}`,
      body: "输出按向量相似度排序的 child chunk 候选，片段用于判断是否命中关键条款条件。",
      input: [
        ["向量输入", result.effective_query || question || "-"],
        ["候选上限", config.top_k != null ? config.top_k : "-"],
      ],
      output: [
        ["最高向量分", formatScore(maxScore(hits, "vector_score"))],
        ["最低向量阈值", config.min_vector_score != null ? formatScore(config.min_vector_score) : "-"],
      ],
      hits: vectorHits.slice(0, 8).map((hit) => hitCard(hit, "vector")),
      details: [
        ["chunk 读取", latencyText(latencies.chunk_read_ms)],
      ],
    },
    {
      title: "Rerank 重排",
      tone: rerankedHits.length ? "good" : (hits.length ? "warn" : "bad"),
      metric: `${rerankedHits.length || 0} 个已重排 · ${latencyText(latencies.rerank_ms)}`,
      body: "只重排向量召回后的 child chunk，检查更强相关证据是否被推到前列。",
      input: [
        ["Rerank 输入", `${hits.length} 个 child chunk`],
        ["最终 top_k", config.final_top_k != null ? config.final_top_k : "-"],
      ],
      output: [
        ["Top1 分数", formatScore(refusal.top1_score)],
        ["Top1 间隔", formatScore(refusal.top1_margin)],
      ],
      hits: rerankOutput.slice(0, 8).map((hit) => hitCard(hit, "rerank")),
      details: [
        ["分数来源", rerankedHits.length ? "rerank" : "vector fallback"],
      ],
    },
    {
      title: "证据判定",
      tone: refusal.should_refuse ? "warn" : (refusal.confidence === "medium" ? "warn" : "good"),
      metric: refusal.confidence ? `置信度 ${refusal.confidence}` : result.status || "-",
      body: "基于重排分数、Top1 间隔和阈值判断证据是否足够进入上下文。",
      input: [
        ["候选数量", `${rerankedHits.length || hits.length} 个`],
        ["分数阈值", config.min_rerank_score != null ? formatScore(config.min_rerank_score) : "-"],
      ],
      output: [
        ["判定", refusal.should_refuse ? "拒答" : "通过"],
        ["原因", refusal.reason || result.refusal_reason || "无"],
        ["高于阈值", refusal.candidates_above_threshold ?? "-"],
      ],
      details: [
        ["Top1 间隔阈值", config.min_top1_margin != null ? formatScore(config.min_top1_margin) : "-"],
      ],
    },
    {
      title: "Parent 扩展与上下文",
      tone: refs.length ? "good" : (result.status === "refused" ? "warn" : "bad"),
      metric: `${refs.length} 条引用 · ${combinedLatencyText(latencies.parent_expansion_ms, latencies.context_assembly_ms)}`,
      body: "命中 child 后回查 parent chunk，按上下文预算组装可引用证据。",
      input: [
        ["输入 child", `${rerankedHits.length || selectedHits.length || 0} 个候选`],
        ["预算", config.max_context_tokens ? `${config.max_context_tokens} tokens` : "-"],
      ],
      output: [
        ["纳入 parent", context.candidates_included != null ? context.candidates_included : "-"],
        ["丢弃候选", context.candidates_dropped != null ? context.candidates_dropped : "-"],
        ["上下文 tokens", context.total_tokens != null ? context.total_tokens : "-"],
        ["截断", context.truncation_applied ? "有" : "无"],
      ],
      hits: selectedOutput.slice(0, 8).map(referenceCard),
      details: [
        ["parent 扩展", latencyText(latencies.parent_expansion_ms)],
        ["上下文组装", latencyText(latencies.context_assembly_ms)],
      ],
    },
    {
      title: "带引用回答",
      tone: result.status === "success" ? "good" : (result.status === "refused" ? "warn" : "bad"),
      metric: latencyText(answerGeneration.latency_ms),
      body: "答案必须使用上下文阶段生成的引用编号；引用无效或证据不足会转为拒答。",
      input: [
        ["上下文引用", `${refs.length} 条`],
        ["问题", question || "-"],
      ],
      output: [
        ["回答状态", result.status || "-"],
        ["引用校验", answerValidation.valid === false ? `失败：${answerValidation.reason || "-"}` : "通过或无需校验"],
      ],
      details: [
        ["LLM 耗时", latencyText(answerGeneration.latency_ms)],
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
        <div class="io-grid">
          ${renderIoBox("输入", step.input || [])}
          ${renderIoBox("输出", step.output || [])}
        </div>
        <div class="pipeline-details">${details}</div>
        ${step.hits?.length ? `<div class="hit-list">${step.hits.join("")}</div>` : ""}
      </div>
    </article>
  `;
}

function renderIoBox(title, rows) {
  const body = rows.length
    ? rows.map(([label, value]) => `
      <div class="io-row">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(String(value ?? "-"))}</strong>
      </div>
    `).join("")
    : '<div class="io-empty">无</div>';
  return `
    <section class="io-box">
      <h4>${escapeHtml(title)}</h4>
      ${body}
    </section>
  `;
}

function hitCard(hit, mode) {
  const rank = mode === "rerank" ? hit.rerank_rank : hit.vector_rank;
  const score = mode === "rerank" ? hit.rerank_score : hit.vector_score;
  const title = mode === "rerank" ? `Rerank #${rank || "-"}` : `Vector #${rank || "-"}`;
  return `
    <article class="hit-card ${hit.selected ? "selected" : ""}">
      <div class="hit-card-head">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(formatScore(score))}</span>
      </div>
      <div class="hit-card-meta">
        <span>chunk ${escapeHtml(hit.chunk_key || hit.chunk_id)}</span>
        <span>parent ${escapeHtml(hit.parent_key || hit.parent_id)}</span>
        <span>${hit.selected ? "已进入上下文" : "未进入上下文"}</span>
      </div>
      <p>${escapeHtml(hit.content_snippet || "暂无片段，请确认后端是否返回 content_snippet。")}</p>
    </article>
  `;
}

function referenceCard(item) {
  const refId = item.ref_id || (item.selected ? "selected" : "-");
  const score = item.rerank_score ?? item.vector_score;
  const heading = Array.isArray(item.heading_path) && item.heading_path.length ? item.heading_path.join(" / ") : "";
  return `
    <article class="hit-card selected">
      <div class="hit-card-head">
        <strong>${escapeHtml(refId)}</strong>
        <span>${escapeHtml(formatScore(score))}</span>
      </div>
      <div class="hit-card-meta">
        <span>doc ${escapeHtml(item.document_title || item.document_id || "-")}</span>
        <span>chunk ${escapeHtml(item.chunk_key || item.chunk_id || "-")}</span>
        <span>页码 ${escapeHtml(pageLabel(item.page_start, item.page_end))}</span>
      </div>
      <p>${escapeHtml(item.content_snippet || heading || item.source_uri || "已进入引用，但当前响应没有返回 parent 原文片段。")}</p>
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
  if (result.error || result.status === "failed") {
    return { tone: "bad", label: "需要排查" };
  }
  if (result.status === "refused") {
    return { tone: "warn", label: "已拒答" };
  }
  if (steps.some((step) => step.tone === "bad")) {
    return { tone: "bad", label: "需要排查" };
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

function debugConclusion(result) {
  const refs = result.references || [];
  const hits = result.hit_summary || [];
  const metadata = result.metadata || {};
  const refusal = metadata.refusal_decision || {};
  const lines = [];
  lines.push(`状态：${result.status || (result.error ? "failed" : "unknown")}`);
  if (result.error?.message) lines.push(`错误：${result.error.message}`);
  if (result.refusal_reason) lines.push(`拒答原因：${result.refusal_reason}`);
  if (refusal.confidence) lines.push(`证据置信度：${refusal.confidence}`);
  lines.push(`召回候选：${hits.length} 个`);
  lines.push(`进入引用：${refs.length} 条`);
  if (result.answer) {
    lines.push("");
    lines.push("回答预览：");
    lines.push(result.answer);
  }
  return lines.join("\n");
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

function combinedLatencyText(...values) {
  const present = values.filter((value) => value != null && !Number.isNaN(Number(value)));
  if (!present.length) return "-";
  return latencyText(present.reduce((sum, value) => sum + Number(value), 0));
}

function latencyTone(value, warnAt) {
  if (value == null || Number.isNaN(Number(value))) return "neutral";
  return Number(value) > warnAt ? "warn" : "good";
}

function pageLabel(start, end) {
  if (start == null && end == null) return "未知";
  if (start == null) return String(end);
  if (end == null || start === end) return String(start);
  return `${start}-${end}`;
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
