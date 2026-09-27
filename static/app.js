/* ============================================================
   知识消化平台前端逻辑（原生 JS，无框架）
   - tab 切换、toast、数字滚动、按钮波纹
   - 链接弹窗：输入 → 处理中 → 预览 → 保存
   - 笔记库：列表、搜索、标签/分类筛选、展开、编辑、删除
   - 文件上传：拖拽/选择、进度、预览、保存
   - RSS 订阅：源管理、待处理队列、预览后保存
   - 首页：真实统计数字 + 最近笔记
   ============================================================ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ============ tab 切换 ============ */
function initTabs() {
  $$$(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      $$$(".nav-btn").forEach((b) => b.classList.toggle("active", b === btn));
      $$$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === target));
      // 切到笔记库：拉筛选选项和列表；切回首页：刷新统计和最近笔记
      if (target === "notes") {
        initNotesFilters();
        loadNotes();
      } else if (target === "feed") {
        loadRssSources();
        loadPendingItems();
      } else if (target === "home") {
        loadStats();
        loadRecentNotes();
        loadHomeTaskPanels();
      } else if (target === "qa") {
        loadVectorStats();
      } else if (target === "tasks") {
        loadUserTasks();
      } else if (target === "async-tasks") {
        loadTaskList();
      }
    });
  });
}

/* ============ toast ============ */
function toast(message, type = "info") {
  const host = $("#toast-host");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, 3000);
}

/* ============ 通用 fetch ============ */
async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  let body = null;
  try { body = await res.json(); } catch (_) { /* 非 JSON 响应忽略 */ }
  if (!res.ok || (body && body.code !== 0)) {
    const msg = body?.message || body?.detail || `请求失败（${res.status}）`;
    throw new Error(msg);
  }
  return body;
}


/* ============ 数字滚动动画 ============ */
function animateCounter(el, target, duration = 800) {
  const startTime = performance.now();
  function step(now) {
    const t = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);  // ease-out cubic
    el.textContent = Math.floor(target * eased);
    if (t < 1) requestAnimationFrame(step);
    else el.textContent = target;
  }
  requestAnimationFrame(step);
}

async function loadStats() {
  try {
    const res = await fetchJson("/api/stats");
    const d = res.data || {};
    const targets = [
      d.total_notes ?? 0,
      d.today_notes ?? 0,
      d.pending_count ?? 0,
    ];
    $$(".counter").forEach((el, i) => animateCounter(el, targets[i]));
  } catch (_) { /* 首页统计失败不打扰 */ }
}

/* ============ 日常/工作任务状态 ============ */
let taskFilters = {category: "", completed: ""};
let editingTaskId = null;

/* ============ 链接 / 手动粘贴弹窗 ============ */
let inputMode = "url";
let lastProcessPayload = null; // 上次请求参数，重新生成时复用
let previewData = null;        // 预览数据，保存时提交

function showStage(name) {
  $$(".modal-stage").forEach((stage) => {
    stage.hidden = stage.id !== `modal-stage-${name}`;
  });
}

function setInputMode(mode, keepUrl = false) {
  inputMode = mode === "text" ? "text" : "url";
  $$(".modal-tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.inputMode === inputMode);
  });
  $("#input-mode-url").hidden = inputMode !== "url";
  $("#input-mode-text").hidden = inputMode !== "text";
  const oldUrl = keepUrl ? $("#link-url-input").value.trim() : "";
  if (keepUrl && oldUrl) $("#manual-url-input").value = oldUrl;
  $("#btn-process-url").textContent = inputMode === "url" ? "开始处理" : "开始改写";
}

function openLinkModal() {
  lastProcessPayload = null;
  previewData = null;
  $("#link-url-input").value = "";
  $("#manual-title-input").value = "";
  $("#manual-content-input").value = "";
  $("#manual-url-input").value = "";
  $("#link-modal-error").hidden = true;
  setInputMode("url");
  showStage("input");
  $("#link-modal").hidden = false;
}

function closeLinkModal() {
  $("#link-modal").hidden = true;
}

function pollTaskOnce(taskId) {
  return fetchJson(`/api/async-tasks/${taskId}`);
}

function startTaskPolling(taskId, handlers) {
  let stopped = false;

  async function checkTask() {
    try {
      const body = await pollTaskOnce(taskId);
      const task = body.data;
      if (task.status === "running") {
        if (handlers.onProgress) handlers.onProgress(task);
        return false;
      }
      stopped = true;
      if (task.status === "success" && handlers.onSuccess) {
        handlers.onSuccess(task);
      }
      if (task.status === "failed" && handlers.onFailed) {
        handlers.onFailed(task);
      }
    } catch (err) {
      stopped = true;
      if (handlers.onError) handlers.onError(err);
    }
    return stopped;
  }

  const timer = setInterval(async () => {
    if (stopped) return;
    const done = await checkTask();
    if (done) clearInterval(timer);
  }, 2000);

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

function buildProcessPayload() {
  if (inputMode === "text") {
    return {
      mode: "text",
      title: $("#manual-title-input").value.trim(),
      content: $("#manual-content-input").value,
      url: $("#manual-url-input").value.trim(),
    };
  }
  return {
    mode: "url",
    url: $("#link-url-input").value.trim(),
  };
}

async function processCurrent() {
  const payload = buildProcessPayload();
  const errorBox = $("#link-modal-error");
  errorBox.hidden = true;

  if (payload.mode === "url" && !payload.url) {
    errorBox.textContent = "请输入网页链接";
    errorBox.hidden = false;
    return;
  }
  if (payload.mode === "text" && !payload.content.trim()) {
    errorBox.textContent = "请粘贴正文内容";
    errorBox.hidden = false;
    return;
  }

  lastProcessPayload = payload;
  $("#process-loading-text").textContent = "任务已创建，正在处理…";
  showStage("loading");

  try {
    const body = await fetchJson("/api/process-url", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    startTaskPolling(body.data.task_id, {
      onProgress: (task) => {
        $("#process-loading-text").textContent = task.progress_message || "正在处理…";
      },
      onSuccess: (task) => {
        previewData = task.result;
        renderPreview(previewData);
        showStage("preview");
      },
      onFailed: (task) => {
        handleProcessFailure(
          {message: task.error, data: task.result},
          payload,
          errorBox,
        );
      },
      onError: (err) => {
        errorBox.textContent = err.message || "处理失败，请重试";
        errorBox.hidden = false;
        showStage("input");
      },
    });
  } catch (err) {
    errorBox.textContent = err.message || "任务创建失败";
    errorBox.hidden = false;
    showStage("input");
  }
}

async function handleProcessFailure(body, payload, errorBox) {
  const message = body.message || "处理失败，请重试";
  const stage = body.data?.stage || "";
  // 只有链接抓取阶段失败，才提示切换到手动粘贴
  if (payload.mode === "url" && stage === "crawl") {
    const shouldSwitch = confirm("该网站无法自动抓取，是否切换到手动粘贴模式？");
    if (shouldSwitch) {
      setInputMode("text", true);
      errorBox.hidden = true;
      showStage("input");
      return;
    }
  }
  errorBox.textContent = message;
  errorBox.hidden = false;
  showStage("input");
}

function renderPreview(data) {
  $("#preview-title").textContent = data.title || "无标题";
  const tagBox = $("#preview-tags");
  tagBox.innerHTML = "";
  (data.tags || []).forEach((tag) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = tag;
    tagBox.appendChild(chip);
  });
  $("#preview-content").textContent = data.content || "";
}

async function saveNote() {
  if (!previewData) return;
  const btn = $("#btn-save-note");
  btn.disabled = true;
  try {
    const body = await fetchJson("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(previewData),
    });
    toast("已保存到笔记库", "success");
    closeLinkModal();
    startQualityPolling(body.data);
  } catch (err) {
    toast(err.message || "保存失败", "error");
  } finally {
    btn.disabled = false;
  }
}

function bindLinkModal() {
  $('[data-action="add-link"]').addEventListener("click", openLinkModal);
  $("#link-modal-close").addEventListener("click", closeLinkModal);
  $("#btn-process-url").addEventListener("click", processCurrent);
  $("#btn-save-note").addEventListener("click", saveNote);
  // 重新生成：复用上次链接或手动粘贴内容
  $("#btn-regenerate").addEventListener("click", () => {
    if (lastProcessPayload) processCurrent();
  });
  $$(".modal-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setInputMode(btn.dataset.inputMode));
  });
  $("#link-url-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") processCurrent();
  });
  // 点遮罩空白处关闭
  $("#link-modal").addEventListener("click", (e) => {
    if (e.target === $("#link-modal")) closeLinkModal();
  });
}


/* ============ 笔记库：筛选下拉 ============ */
let filtersReady = false;

async function initNotesFilters() {
  // 每次进入笔记库都刷新选项，保证新标签/分类可选
  await Promise.all([fillTagFilter(), fillCategoryFilter()]);
  filtersReady = true;
}

async function fillTagFilter() {
  const sel = $("#notes-tag-filter");
  try {
    const res = await fetchJson("/api/tags");
    const tags = res.data || [];
    const current = sel.value;
    sel.innerHTML = '<option value="">全部标签</option>';
    tags.forEach((tag) => {
      const opt = document.createElement("option");
      opt.value = tag.name;
      opt.textContent = `${tag.name}（${tag.count}）`;
      sel.appendChild(opt);
    });
    // 保留之前的筛选选择（若该标签还在）
    if (current) sel.value = current;
  } catch (_) { /* 标签加载失败不阻塞页面 */ }
}

async function fillCategoryFilter() {
  const sel = $("#notes-category-filter");
  try {
    const res = await fetchJson("/api/categories");
    const categories = res.data || [];
    const current = sel.value;
    sel.innerHTML = '<option value="">全部分类</option>';
    categories.forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat.name;
      opt.textContent = `${cat.name}（${cat.count}）`;
      sel.appendChild(opt);
    });
    if (current) sel.value = current;
  } catch (_) { /* 分类加载失败不阻塞页面 */ }
}

let searchMode = "keyword";

function currentFilters() {
  return {
    keyword: $("#notes-search").value.trim(),
    tag: $("#notes-tag-filter").value,
    category: $("#notes-category-filter").value,
  };
}

function setSearchMode(mode) {
  searchMode = mode;
  $$(".segmented-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  const semantic = mode === "semantic";
  $("#notes-tag-filter").disabled = semantic;
  $("#notes-category-filter").disabled = semantic;
}

function bindNotesFilters() {
  $("#notes-tag-filter").addEventListener("change", loadNotes);
  $("#notes-category-filter").addEventListener("change", loadNotes);
  $$(".segmented-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setSearchMode(btn.dataset.mode);
      loadNotes();
    });
  });
}

/* ============ 笔记库：列表 ============ */
async function loadNotes() {
  const listEl = $("#notes-list");
  const f = currentFilters();

  try {
    let items = [];
    if (searchMode === "semantic" && f.keyword) {
      const res = await fetchJson("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: f.keyword }),
      });
      items = res.data.items || [];
    } else {
      const params = new URLSearchParams();
      if (f.keyword) params.set("keyword", f.keyword);
      if (f.tag) params.set("tag", f.tag);
      if (f.category) params.set("category", f.category);
      const query = params.toString() ? `?${params.toString()}` : "";
      const res = await fetchJson(`/api/notes${query}`);
      items = res.data.items || [];
    }
    renderNotes(items);
  } catch (err) {
    listEl.innerHTML = "";
    const card = document.createElement("div");
    card.className = "card glass empty";
    card.textContent = `笔记加载失败：${err.message}`;
    listEl.appendChild(card);
  }
}

function renderNotes(items) {
  const listEl = $("#notes-list");
  listEl.innerHTML = "";
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card glass empty";
    empty.innerHTML = `
      <div class="empty-icon">📝</div>
      <p>暂无笔记，去首页输入链接生成第一篇吧</p>`;
    listEl.appendChild(empty);
    return;
  }
  items.forEach((note) => listEl.appendChild(buildNoteCard(note)));
}


function buildNoteCard(note) {
  const card = document.createElement("div");
  card.className = "card glass note-card";
  if (note.quality_score !== null && note.quality_score !== undefined && note.quality_score < 3) {
    card.classList.add("low-quality");
  }
  card.dataset.noteId = note.id;

  // 卡片头部：标题、分类、来源、时间；点头部展开
  const head = document.createElement("div");
  head.className = "note-head";
  head.innerHTML = `
    <div class="note-title">${escapeHtml(note.title || "无标题")}</div>
    <div class="note-meta">
      <span class="note-category">📁 ${escapeHtml(note.category || "默认")}</span>
      <span class="note-source">${escapeHtml(note.source || "手动输入")}</span>
      <span class="note-time">${formatTime(note.created_at)}</span>
    </div>`;
  head.appendChild(buildQualityBadge(note));
  if (note.score !== undefined) {
    const score = document.createElement("span");
    score.className = "score-badge";
    score.textContent = `相关度 ${Math.round(note.score * 100)}%`;
    head.appendChild(score);
  }
  card.appendChild(head);

  // 标签 chip（nth-child 由 CSS 轮换色调）
  if (note.tags && note.tags.length) {
    const chipRow = document.createElement("div");
    chipRow.className = "chip-row";
    note.tags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = tag;
      chipRow.appendChild(chip);
    });
    card.appendChild(chipRow);
  }

  // 展开区：正文 + 操作按钮（原文链接/编辑/删除），默认隐藏
  const detail = document.createElement("div");
  detail.className = "note-detail";
  detail.hidden = true;
  const body = document.createElement("div");
  body.className = "note-body";
  body.textContent = note.content || "";
  detail.appendChild(body);

  const qualityRow = document.createElement("div");
  qualityRow.className = "note-quality-row";
  qualityRow.appendChild(buildQualityBadge(note, true));
  detail.appendChild(qualityRow);

  const relatedSection = document.createElement("div");
  relatedSection.className = "related-section";
  relatedSection.innerHTML = `<div class="related-title">🔗 相关笔记</div>`;
  const relatedList = document.createElement("div");
  relatedList.className = "related-list";
  relatedList.textContent = "展开后加载";
  relatedSection.appendChild(relatedList);
  detail.appendChild(relatedSection);

  const detailActions = document.createElement("div");
  detailActions.className = "note-detail-actions";
  if (note.original_url) {
    const link = document.createElement("a");
    link.className = "note-link";
    link.href = note.original_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "🔗 查看原文";
    detailActions.appendChild(link);
  }

  const regenerateBtn = document.createElement("button");
  regenerateBtn.className = "btn btn-ghost btn-sm";
  regenerateBtn.textContent = "🔁 重新生成";
  detailActions.appendChild(regenerateBtn);

  const evaluateBtn = document.createElement("button");
  evaluateBtn.className = "btn btn-ghost btn-sm";
  evaluateBtn.textContent = "⭐ 立即评估";
  detailActions.appendChild(evaluateBtn);

  const editBtn = document.createElement("button");
  editBtn.className = "btn btn-ghost btn-sm";
  editBtn.textContent = "✏️ 编辑";
  detailActions.appendChild(editBtn);

  const delBtn = document.createElement("button");
  delBtn.className = "btn btn-danger btn-sm";
  delBtn.textContent = "🗑 删除";
  detailActions.appendChild(delBtn);

  detail.appendChild(detailActions);
  card.appendChild(detail);

  // 点头部展开/收起
  head.addEventListener("click", () => {
    detail.hidden = !detail.hidden;
    card.classList.toggle("expanded", !detail.hidden);
    if (!detail.hidden) ensureRelatedLoaded(card, note);
  });

  regenerateBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    openStyleModal(note);
  });

  evaluateBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    evaluateNoteNow(note, evaluateBtn);
  });

  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    openEditModal(note);
  });

  delBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm("确定删除这篇笔记吗？删除后无法恢复。")) return;
    try {
      await fetchJson(`/api/notes/${note.id}`, { method: "DELETE" });
      card.remove();
      toast("笔记已删除", "success");
      // 删完列表空了就刷出空状态
      if (!$("#notes-list").children.length) loadNotes();
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });

  return card;
}


/* ============ 编辑弹窗 ============ */
let editingNote = null;

async function openEditModal(note) {
  editingNote = note;
  const errorBox = $("#edit-modal-error");
  errorBox.hidden = true;

  // 预填当前数据
  $("#edit-title").value = note.title || "";
  $("#edit-content").value = note.content || "";
  $("#edit-tags").value = tagsToText(note.tags || []);

  // 分类下拉：用聚合分类填选项，保证当前分类必在其中
  const catSel = $("#edit-category");
  catSel.innerHTML = "";
  try {
    const res = await fetchJson("/api/categories");
    (res.data || []).forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat.name;
      opt.textContent = cat.name;
      catSel.appendChild(opt);
    });
  } catch (_) { /* 分类加载失败继续，至少保证当前分类可选 */ }
  if (!Array.from(catSel.options).some((o) => o.value === note.category)) {
    const opt = document.createElement("option");
    opt.value = note.category;
    opt.textContent = note.category;
    catSel.appendChild(opt);
  }
  catSel.value = note.category;

  $("#edit-modal").hidden = false;
}

function closeEditModal() {
  $("#edit-modal").hidden = true;
  editingNote = null;
}

async function saveEdit() {
  if (!editingNote) return;
  const btn = $("#btn-save-edit");
  btn.disabled = true;
  const payload = {
    title: $("#edit-title").value.trim(),
    content: $("#edit-content").value,
    tags: parseTagsText($("#edit-tags").value),
    category: $("#edit-category").value,
  };
  try {
    await fetchJson(`/api/notes/${editingNote.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast("笔记已更新", "success");
    closeEditModal();
    // 重拉列表，保证卡片实时更新
    loadNotes();
  } catch (err) {
    const errorBox = $("#edit-modal-error");
    errorBox.textContent = err.message || "保存失败";
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

function bindEditModal() {
  $("#edit-modal-close").addEventListener("click", closeEditModal);
  $("#btn-cancel-edit").addEventListener("click", closeEditModal);
  $("#btn-save-edit").addEventListener("click", saveEdit);
  // 点遮罩空白处关闭
  $("#edit-modal").addEventListener("click", (e) => {
    if (e.target === $("#edit-modal")) closeEditModal();
  });
}


/* ============ 标签工具（浏览器端，与服务端切分规则一致） ============ */
function parseTagsText(text) {
  const result = [];
  (text || "").split(/[,，]/).forEach((part) => {
    const tag = part.trim();
    if (tag && !result.includes(tag)) result.push(tag);
  });
  return result;
}

function tagsToText(tags) {
  return (tags || []).join(", ");
}

/* ============ 搜索防抖 500ms ============ */
function bindNotesSearch() {
  let timer = null;
  $("#notes-search").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => loadNotes(), 500);
  });
}

/* ============ 首页：最近笔记 ============ */
async function loadRecentNotes() {
  const box = $("#recent-notes");
  try {
    const res = await fetchJson("/api/notes?size=5");
    const items = res.data.items || [];
    box.innerHTML = "";
    if (items.length === 0) {
      // 无笔记：恢复空状态
      box.className = "card glass empty";
      box.innerHTML = `
        <div class="empty-icon">📭</div>
        <p>暂无笔记</p>
        <p class="hint">添加链接或上传文件后，这里会显示最近的 5 条</p>`;
      return;
    }
    box.className = "recent-list";
    items.forEach((note) => {
      const row = document.createElement("div");
      row.className = "recent-item";
      row.innerHTML = `
        <span class="recent-title">${escapeHtml(note.title || "无标题")}</span>
        <span class="recent-time">${formatTime(note.created_at)}</span>`;
      // 点击切到笔记库并展开对应笔记
      row.addEventListener("click", () => goToNote(note.id));
      box.appendChild(row);
    });
  } catch (_) { /* 最近笔记加载失败保持空状态 */ }
}

async function goToNote(noteId, resetView = true) {
  // 切到笔记库
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === "notes"));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "notes"));
  await initNotesFilters();
  await loadNotes();
  // 展开对应卡片
  const card = $(`.note-card[data-note-id="${noteId}"]`);
  if (card) {
    const detail = $(".note-detail", card);
    detail.hidden = false;
    card.classList.add("expanded");
    const note = { id: noteId };
    ensureRelatedLoaded(card, note);
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}


/* ============ 小工具 ============ */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ============ 文件上传弹窗 ============ */
const UPLOAD_ALLOWED_EXT = [".txt", ".pdf", ".docx"];
const UPLOAD_MAX_SIZE = 20 * 1024 * 1024;
let selectedUploadFile = null;
let uploadPreview = null;

function showUploadStage(name) {
  $$(".upload-stage").forEach((stage) => {
    stage.hidden = stage.id !== `upload-stage-${name}`;
  });
}

function openUploadModal() {
  selectedUploadFile = null;
  uploadPreview = null;
  $("#upload-file-input").value = "";
  $("#selected-file").hidden = true;
  $("#upload-modal-error").hidden = true;
  $("#btn-start-upload").disabled = true;
  $("#upload-progress-bar").style.width = "0";
  showUploadStage("select");
  $("#upload-modal").hidden = false;
}

function closeUploadModal() {
  $("#upload-modal").hidden = true;
}

function validateUploadFile(file) {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!UPLOAD_ALLOWED_EXT.includes(ext)) {
    return "不支持的文件格式，仅支持 PDF、Word（docx）和 txt";
  }
  if (file.size > UPLOAD_MAX_SIZE) {
    return "文件超过 20MB 大小限制";
  }
  return "";
}

function pickUploadFile(file) {
  const errorBox = $("#upload-modal-error");
  errorBox.hidden = true;
  const error = validateUploadFile(file);
  if (error) {
    errorBox.textContent = error;
    errorBox.hidden = false;
    selectedUploadFile = null;
    $("#btn-start-upload").disabled = true;
    return;
  }
  selectedUploadFile = file;
  $("#selected-file-name").textContent = file.name;
  $("#selected-file").hidden = false;
  $("#btn-start-upload").disabled = false;
}

function uploadSelectedFile() {
  if (!selectedUploadFile) return;
  const progressBar = $("#upload-progress-bar");
  const progressText = $("#upload-progress-text");
  $("#processing-file-name").textContent = selectedUploadFile.name;
  progressBar.style.width = "0";
  progressText.textContent = "正在上传…";
  showUploadStage("processing");

  const formData = new FormData();
  formData.append("file", selectedUploadFile);
  const xhr = new XMLHttpRequest();
  let uploadBytesFinished = false;

  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.round((event.loaded / event.total) * 100);
    progressBar.style.width = `${percent}%`;
    progressText.textContent = `正在上传：${percent}%`;
  };
  xhr.upload.onload = () => {
    uploadBytesFinished = true;
    progressBar.style.width = "100%";
    progressText.textContent = "上传完成，正在解析和改写…";
  };

  xhr.onreadystatechange = () => {
    if (xhr.readyState !== XMLHttpRequest.DONE) return;
    let body = null;
    try { body = JSON.parse(xhr.responseText); } catch (_) { /* 非 JSON 按失败处理 */ }

    if (xhr.status < 200 || xhr.status >= 300 || !body || body.code !== 0) {
      const message = body?.message || `上传失败（${xhr.status || "网络错误"}）`;
      const errorBox = $("#upload-modal-error");
      errorBox.textContent = message;
      errorBox.hidden = false;
      showUploadStage("select");
      return;
    }

    uploadPreview = { ...body.data, source: "文件上传" };
    renderUploadPreview(uploadPreview);
    showUploadStage("preview");
  };

  xhr.onerror = () => {
    const errorBox = $("#upload-modal-error");
    errorBox.textContent = "网络连接失败，请确认服务正在运行";
    errorBox.hidden = false;
    showUploadStage("select");
  };

  xhr.open("POST", "/api/upload-file");
  xhr.send(formData);
  // 解析和 AI 改写耗时无法按字节估算；字节传完后由 upload.onload 切文案
  if (uploadBytesFinished) {
    progressText.textContent = "上传完成，正在解析和改写…";
  }
}

function renderUploadPreview(data) {
  $("#upload-preview-title").textContent = data.title || "无标题";
  const tagBox = $("#upload-preview-tags");
  tagBox.innerHTML = "";
  (data.tags || []).forEach((tag) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = tag;
    tagBox.appendChild(chip);
  });
  $("#upload-preview-content").textContent = data.content || "";
}

async function saveUploadNote() {
  if (!uploadPreview) return;
  const btn = $("#btn-save-upload");
  btn.disabled = true;
  try {
    const body = await fetchJson("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(uploadPreview),
    });
    toast("已保存到笔记库", "success");
    closeUploadModal();
    startQualityPolling(body.data);
  } catch (err) {
    toast(err.message || "保存失败", "error");
  } finally {
    btn.disabled = false;
  }
}

function bindUploadModal() {
  $('[data-action="upload"]').addEventListener("click", openUploadModal);
  $("#upload-modal-close").addEventListener("click", closeUploadModal);
  $("#btn-start-upload").addEventListener("click", uploadSelectedFile);
  $("#btn-save-upload").addEventListener("click", saveUploadNote);
  $("#btn-reupload").addEventListener("click", uploadSelectedFile);

  const dropzone = $("#upload-dropzone");
  const fileInput = $("#upload-file-input");
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) pickUploadFile(fileInput.files[0]);
  });

  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) pickUploadFile(file);
  });

  $("#upload-modal").addEventListener("click", (e) => {
    if (e.target === $("#upload-modal")) closeUploadModal();
  });
}


/* ============ v1.4：RSS 订阅 ============ */
let processedPreview = null;

async function addRssSource() {
  const nameBox = $("#rss-name");
  const urlBox = $("#rss-url");
  const name = nameBox.value.trim();
  const url = urlBox.value.trim();
  try {
    await fetchJson("/api/rss-sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url }),
    });
    nameBox.value = "";
    urlBox.value = "";
    toast("RSS 源已添加", "success");
    await loadRssSources();
  } catch (err) {
    toast(err.message || "添加失败", "error");
  }
}

async function loadRssSources() {
  const box = $("#rss-source-list");
  box.innerHTML = "";
  try {
    const res = await fetchJson("/api/rss-sources");
    const sources = res.data || [];
    if (!sources.length) {
      box.appendChild(makeFeedEmpty("📡", "暂无 RSS 源，先添加一个订阅地址"));
      return;
    }
    sources.forEach((source) => box.appendChild(buildSourceCard(source)));
  } catch (err) {
    box.appendChild(makeFeedEmpty("⚠️", `RSS 源加载失败：${err.message}`));
  }
}

function buildSourceCard(source) {
  const card = document.createElement("div");
  card.className = "card glass feed-item-card";

  const head = document.createElement("div");
  head.className = "feed-item-head";
  head.innerHTML = `
    <div class="feed-item-main">
      <div class="feed-item-title">${escapeHtml(source.name)}</div>
      <div class="feed-item-url">${escapeHtml(source.url)}</div>
      <div class="feed-item-meta">
        <span>最后抓取：${source.last_fetched ? formatTime(source.last_fetched) : "尚未抓取"}</span>
      </div>
    </div>`;

  const actions = document.createElement("div");
  actions.className = "feed-item-actions";
  const fetchBtn = makeButton("btn btn-ghost btn-sm", "抓取");
  const deleteBtn = makeButton("btn btn-danger btn-sm", "删除");
  actions.append(fetchBtn, deleteBtn);
  head.appendChild(actions);
  card.appendChild(head);

  fetchBtn.addEventListener("click", async () => {
    fetchBtn.disabled = true;
    const oldText = fetchBtn.textContent;
    fetchBtn.textContent = "抓取中…";
    try {
      const res = await fetchJson(`/api/rss-sources/${source.id}/fetch`, { method: "POST" });
      toast(res.message, "success");
      await Promise.all([loadRssSources(), loadPendingItems(), loadStats()]);
    } catch (err) {
      toast(err.message || "抓取失败", "error");
      fetchBtn.disabled = false;
      fetchBtn.textContent = oldText;
    }
  });

  deleteBtn.addEventListener("click", async () => {
    if (!confirm(`确定删除 RSS 源「${source.name}」吗？已进入队列的内容会保留。`)) return;
    try {
      await fetchJson(`/api/rss-sources/${source.id}`, { method: "DELETE" });
      toast("RSS 源已删除", "success");
      await loadRssSources();
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });

  return card;
}

async function fetchAllRssSources() {
  const btn = $("#btn-fetch-all-rss");
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "全部抓取中…";
  try {
    const res = await fetchJson("/api/rss/fetch-all", { method: "POST" });
    const results = res.data || [];
    const totalAdded = results.reduce((sum, item) => sum + (item.added || 0), 0);
    const failed = results.filter((item) => item.error);
    if (failed.length) {
      toast(`抓取完成：新增 ${totalAdded} 条，${failed.length} 个源失败`, "warning");
    } else {
      toast(`全部抓取完成，新增 ${totalAdded} 条`, "success");
    }
    await Promise.all([loadRssSources(), loadPendingItems(), loadStats()]);
  } catch (err) {
    toast(err.message || "全部抓取失败", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

async function loadPendingItems() {
  const box = $("#pending-list");
  box.innerHTML = "";
  resetBatchToolbar();
  try {
    const res = await fetchJson("/api/pending");
    const items = res.data || [];
    if (!items.length) {
      box.appendChild(makeFeedEmpty("📭", "暂无待处理内容，去添加 RSS 源吧"));
      return;
    }
    items.forEach((item) => box.appendChild(buildPendingCard(item)));
  } catch (err) {
    box.appendChild(makeFeedEmpty("⚠️", `待处理队列加载失败：${err.message}`));
  }
}

function buildPendingCard(item) {
  const card = document.createElement("div");
  card.className = "card glass feed-item-card pending-card";
  card.dataset.itemId = item.id;
  if (item.status === "skipped") card.classList.add("is-disabled");

  const head = document.createElement("div");
  head.className = "feed-item-head";
  head.innerHTML = `
    <label class="pending-check" title="选择后可批量处理">
      <input type="checkbox" class="pending-item-check" />
    </label>
    <div class="feed-item-main">
      <div class="feed-item-title">${escapeHtml(item.title || "无标题")}</div>
      <div class="feed-item-url">${escapeHtml(item.url)}</div>
      <div class="feed-item-meta">
        <span>来源：${escapeHtml(item.source || "RSS")}</span>
        <span>${formatTime(item.created_at)}</span>
      </div>
      <div class="pending-item-error" hidden></div>
    </div>`;

  const status = document.createElement("span");
  status.className = `status-badge ${item.status === "skipped" ? "skipped" : ""}`;
  status.textContent = item.status === "skipped" ? "已跳过" : "待处理";

  const actions = document.createElement("div");
  actions.className = "feed-item-actions";
  const processBtn = makeButton(
    "btn btn-primary btn-sm",
    item.status === "skipped" ? "重新处理" : "处理"
  );
  const skipBtn = makeButton("btn btn-ghost btn-sm", "跳过");
  const deleteBtn = makeButton("btn btn-danger btn-sm", "删除");
  if (item.status === "skipped") skipBtn.disabled = true;
  actions.append(status, processBtn, skipBtn, deleteBtn);
  head.appendChild(actions);
  card.appendChild(head);

  const checkbox = $(".pending-item-check", card);
  checkbox.addEventListener("change", syncSelectAllState);
  processBtn.addEventListener("click", () => processPendingItem(item, processBtn));

  skipBtn.addEventListener("click", async () => {
    try {
      await fetchJson(`/api/pending/${item.id}/skip`, { method: "POST" });
      toast("已跳过", "success");
      await Promise.all([loadPendingItems(), loadStats()]);
    } catch (err) {
      toast(err.message || "跳过失败", "error");
    }
  });

  deleteBtn.addEventListener("click", async () => {
    if (!confirm("确定删除这条待处理内容吗？删除后无法恢复。")) return;
    try {
      await fetchJson(`/api/pending/${item.id}`, { method: "DELETE" });
      toast("待处理项已删除", "success");
      await Promise.all([loadPendingItems(), loadStats()]);
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });

  return card;
}

async function processPendingItem(item, button) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "处理中…";
  try {
    const res = await fetchJson(`/api/pending/${item.id}/process`, { method: "POST" });
    processedPreview = res.data;
    renderProcessedPreview(processedPreview);
    $("#pending-modal").hidden = false;
  } catch (err) {
    toast(err.message || "处理失败", "error");
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function renderProcessedPreview(data) {
  $("#pending-modal-error").hidden = true;
  $("#pending-preview-title").textContent = data.title || "无标题";
  const tagBox = $("#pending-preview-tags");
  tagBox.innerHTML = "";
  (data.tags || []).forEach((tag) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = tag;
    tagBox.appendChild(chip);
  });
  $("#pending-preview-content").textContent = data.content || "";
}

async function saveProcessedPreview() {
  if (!processedPreview) return;
  const btn = $("#btn-save-pending");
  btn.disabled = true;
  try {
    await fetchJson(`/api/pending/${processedPreview.item_id}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: processedPreview.title,
        content: processedPreview.content,
        tags: processedPreview.tags,
      }),
    });
    toast("已保存到笔记库", "success");
    $("#pending-modal").hidden = true;
    await Promise.all([loadPendingItems(), loadStats()]);
  } catch (err) {
    const errorBox = $("#pending-modal-error");
    errorBox.textContent = err.message || "保存失败";
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function reprocessPreview() {
  if (!processedPreview) return;
  const btn = $("#btn-reprocess-pending");
  btn.disabled = true;
  btn.textContent = "重新生成中…";
  try {
    const res = await fetchJson(`/api/pending/${processedPreview.item_id}/process`, {
      method: "POST",
    });
    processedPreview = res.data;
    renderProcessedPreview(processedPreview);
  } catch (err) {
    const errorBox = $("#pending-modal-error");
    errorBox.textContent = err.message || "重新生成失败";
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 重新生成";
  }
}

function bindFeedPage() {
  $("#btn-add-rss").addEventListener("click", addRssSource);
  $("#btn-fetch-all-rss").addEventListener("click", fetchAllRssSources);
  $("#pending-select-all").addEventListener("change", toggleAllPendingChecks);
  $("#btn-batch-process").addEventListener("click", batchProcessSelected);
  $("#pending-modal-close").addEventListener("click", () => {
    $("#pending-modal").hidden = true;
  });
  $("#btn-cancel-pending").addEventListener("click", () => {
    $("#pending-modal").hidden = true;
  });
  $("#btn-save-pending").addEventListener("click", saveProcessedPreview);
  $("#btn-reprocess-pending").addEventListener("click", reprocessPreview);
  $("#pending-modal").addEventListener("click", (event) => {
    if (event.target === $("#pending-modal")) {
      $("#pending-modal").hidden = true;
    }
  });

  $("#rss-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") addRssSource();
  });
}

function makeButton(className, text) {
  const button = document.createElement("button");
  button.className = className;
  button.textContent = text;
  return button;
}

function resetBatchToolbar() {
  $("#pending-select-all").checked = false;
  $("#batch-progress").hidden = true;
  $("#batch-progress-bar").style.width = "0";
  $("#batch-progress-text").textContent = "准备中…";
  $("#btn-batch-process").disabled = false;
  $("#btn-batch-process").textContent = "批量处理";
}

function checkedPendingCards() {
  return $$(".pending-item-check:checked").map((check) => check.closest(".pending-card"));
}

function syncSelectAllState() {
  const all = $$(".pending-item-check");
  const checked = checkedPendingCards();
  $("#pending-select-all").checked = all.length > 0 && checked.length === all.length;
}

function toggleAllPendingChecks(event) {
  $$(".pending-item-check").forEach((check) => {
    check.checked = event.target.checked;
  });
}

async function batchProcessSelected() {
  const cards = checkedPendingCards();
  if (!cards.length) {
    toast("请先勾选要处理的内容", "warning");
    return;
  }
  const itemIds = cards.map((card) => Number(card.dataset.itemId));
  const btn = $("#btn-batch-process");
  btn.disabled = true;
  btn.textContent = "正在创建任务…";
  $("#batch-progress").hidden = false;
  $("#batch-progress-bar").style.width = "0";
  $("#batch-progress-text").textContent = "等待后台执行…";

  try {
    const body = await fetchJson("/api/pending/batch-process", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({item_ids: itemIds}),
    });
    startTaskPolling(body.data.task_id, {
      onProgress: (task) => {
        $("#batch-progress-bar").style.width = `${task.progress}%`;
        $("#batch-progress-text").textContent = task.progress_message || "正在处理…";
      },
      onSuccess: async (task) => {
        applyBatchSummary(task.result);
        await Promise.all([loadStats(), loadRecentNotes()]);
      },
      onFailed: (task) => {
        toast(task.error || "批量处理失败", "error");
        resetBatchToolbar();
      },
      onError: (err) => {
        toast(err.message || "批量处理失败", "error");
        resetBatchToolbar();
      },
    });
  } catch (err) {
    toast(err.message || "批量处理失败", "error");
    resetBatchToolbar();
  }
}

function applyBatchSummary(result) {
  const items = result.results || [];
  items.forEach((item) => {
    const card = $(`.pending-card[data-item-id="${item.item_id}"]`);
    if (!card) return;
    if (item.status === "success") {
      card.remove();
      return;
    }
    const check = $(".pending-item-check", card);
    const errorBox = $(".pending-item-error", card);
    check.checked = false;
    errorBox.textContent = item.error || "处理失败";
    errorBox.hidden = false;
  });

  if (!$(".pending-card")) {
    $("#pending-list").appendChild(
      makeFeedEmpty("📭", "暂无待处理内容，去添加 RSS 源吧")
    );
  }
  syncSelectAllState();
  resetBatchToolbar();
  toast(`批量处理完成：成功 ${result.success} 条，失败 ${result.failed} 条`,
    result.failed ? "warning" : "success");
}

function makeFeedEmpty(icon, text) {
  const empty = document.createElement("div");
  empty.className = "card glass empty";
  empty.innerHTML = `<div class="empty-icon">${icon}</div><p>${escapeHtml(text)}</p>`;
  return empty;
}


/* ============ 设置页：配置读写 ============ */
async function loadConfig() {
  try {
    const res = await fetchJson("/api/ai-config");
    const c = res.data || {};
    $("#ai-base-url").value = c.base_url || "";
    $("#ai-model").value = c.model || "";
  } catch (_) { /* 设置页加载失败不打扰 */ }
}

async function saveConfig() {
  const payload = {
    base_url: $("#ai-base-url").value.trim(),
    model: $("#ai-model").value.trim(),
    api_key: $("#ai-api-key").value,
  };
  try {
    await fetchJson("/api/ai-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#ai-api-key").value = "";
    toast("配置已保存", "success");
  } catch (_) { /* 保存失败已有反馈路径 */ }
}

async function testConnection() {
  try {
    const res = await fetchJson("/api/ai-test", { method: "POST" });
    toast(res.message || "测试完成", "info");
  } catch (_) { /* ignore */ }
}

/* ============ 设置页：定时抓取 ============ */
function updateSwitchText() {
  const input = $("#scheduler-enabled");
  const text = input.closest(".field").querySelector(".switch-text");
  text.textContent = input.checked ? "开启" : "关闭";
}

async function loadSchedulerConfig() {
  try {
    const res = await fetchJson("/api/scheduler/config");
    const cfg = res.data || {};
    $("#scheduler-enabled").checked = Boolean(cfg.enabled);
    $("#scheduler-time").value = cfg.fetch_time || "08:00";
    updateSwitchText();
  } catch (_) { /* 配置加载失败不打扰 */ }
}

async function saveSchedulerSettings() {
  const payload = {
    enabled: $("#scheduler-enabled").checked,
    fetch_time: $("#scheduler-time").value || "08:00",
  };
  try {
    const res = await fetchJson("/api/scheduler/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const cfg = res.data || {};
    $("#scheduler-enabled").checked = Boolean(cfg.enabled);
    $("#scheduler-time").value = cfg.fetch_time || "08:00";
    updateSwitchText();
    toast(res.message || "定时抓取设置已保存", "success");
    await loadSchedulerLogs();
  } catch (_) { /* 失败已有统一 toast */ }
}

async function runFetchNow(button) {
  const oldHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = "⏳ 正在抓取…";
  try {
    const res = await fetchJson("/api/scheduler/run-now", { method: "POST" });
    toast(res.message || "抓取完成", "success");
    await Promise.all([
      loadSchedulerLogs(),
      loadPendingItems(),
      loadStats(),
    ]);
  } catch (_) {
    /* 失败已有统一 toast */
  } finally {
    button.disabled = false;
    button.innerHTML = oldHtml;
  }
}

async function loadSchedulerLogs() {
  const host = $("#scheduler-logs");
  try {
    const res = await fetchJson("/api/scheduler/logs?limit=50");
    const logs = res.data || [];
    if (!logs.length) {
      host.innerHTML = '<div class="log-empty">暂无抓取日志</div>';
      return;
    }

    const statusText = {
      success: "成功",
      failed: "失败",
      running: "执行中",
    };

    host.innerHTML = logs.map((log) => `
      <div class="log-item">
        <div class="log-head">
          <span class="log-status ${escapeHtml(log.status)}">${statusText[log.status] || log.status}</span>
          <span class="log-source">${escapeHtml(log.source_name || "")}</span>
          <span class="log-time">${formatTime(log.created_at)}</span>
        </div>
        <div class="log-message">${escapeHtml(log.message || "")}</div>
        <div class="log-count">新增 ${Number(log.new_count || 0)} 条</div>
      </div>
    `).join("");
  } catch (_) {
    host.innerHTML = '<div class="log-empty">日志加载失败</div>';
  }
}

function bindSettings() {
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-test").addEventListener("click", testConnection);
  $("#scheduler-enabled").addEventListener("change", updateSwitchText);
  $("#btn-save-scheduler").addEventListener("click", saveSchedulerSettings);
  $("#btn-run-now").addEventListener("click", (event) => runFetchNow(event.currentTarget));
  $$('[data-action="export"], [data-action="import"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("数据导入导出将在后续版本实现", "warning"));
  });
}


/* ============ v2.0：AI 助手聊天和向量同步 ============ */
let qaPollTimer = null;
let agentHistory = [];

const TOOL_NAME_TEXT = {
  search_notes: "搜索笔记",
  create_note: "创建笔记",
  add_rss_source: "添加 RSS",
  fetch_rss: "抓取 RSS",
  get_stats: "获取统计",
  get_pending: "查看待处理",
  process_pending: "处理待办",
  save_pending_item: "保存预览",
  answer_question: "知识问答",
};

function stateText(status) {
  return {
    idle: "空闲",
    running: "同步中",
    done: "同步完成",
    failed: "同步失败",
  }[status] || status;
}

async function loadVectorStats() {
  try {
    const res = await fetchJson("/api/vector/stats");
    const d = res.data || {};
    $("#qa-vector-count").textContent = `向量笔记：${d.note_count ?? 0}`;
    if (d.status === "running") {
      const doneCount = Number(d.progress || 0);
      const totalCount = Number(d.total || 0);
      $("#qa-vector-state").textContent =
        totalCount ? `状态：同步中 ${doneCount}/${totalCount}` : "状态：同步中";
      startVectorPolling(false);
    } else {
      $("#qa-vector-state").textContent = `状态：${stateText(d.status || "idle")}`;
      stopVectorPolling();
    }
  } catch (err) {
    $("#qa-vector-state").textContent = `状态不可用：${err.message}`;
  }
}

function startVectorPolling(showStartToast = true) {
  if (showStartToast) toast("向量同步已开始，首次下载模型可能较慢", "info");
  if (qaPollTimer) return;
  qaPollTimer = setInterval(loadVectorStats, 1000);
}

function stopVectorPolling() {
  if (!qaPollTimer) return;
  clearInterval(qaPollTimer);
  qaPollTimer = null;
}

async function syncVectorIndex() {
  await fetchJson("/api/vector/sync", { method: "POST" });
  startVectorPolling(true);
  setTimeout(loadVectorStats, 500);
}

async function sendAgentMessage(rawText) {
  const text = (rawText || "").trim();
  const input = $("#agent-message-input");
  const sendBtn = $("#btn-agent-send");
  if (!text) {
    toast("请输入消息", "warning");
    return;
  }

  input.value = "";
  appendUserMessage(text);
  const typing = appendTypingMessage();
  sendBtn.disabled = true;
  scrollAgentLog();

  try {
    const res = await fetchJson("/api/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        history: agentHistory,
      }),
    });
    renderAgentAnswer(res.data || {}, typing);
    agentHistory.push({ role: "user", content: text });
    agentHistory.push({
      role: "assistant",
      content: res.data?.answer || "",
    });
  } catch (err) {
    typing.remove();
    appendAgentError(err.message || "发送失败");
  } finally {
    sendBtn.disabled = false;
    input.focus();
    scrollAgentLog();
  }
}

function appendUserMessage(text) {
  const el = document.createElement("div");
  el.className = "agent-message agent-message-user";
  el.textContent = text;
  appendChatNode(el);
}

function appendTypingMessage() {
  const wrap = document.createElement("div");
  wrap.className = "agent-message agent-message-ai";
  wrap.innerHTML = '<span class="typing-dots"><i></i><i></i><i></i></span><span class="typing-text">思考中...</span>';
  appendChatNode(wrap);
  return wrap;
}

function renderAgentAnswer(data, typingNode) {
  typingNode.remove();
  const card = document.createElement("div");
  card.className = "agent-message agent-message-ai";

  const answer = document.createElement("div");
  answer.className = "agent-answer";
  answer.textContent = data.answer || "";
  card.appendChild(answer);

  if (Array.isArray(data.actions) && data.actions.length) {
    const actions = document.createElement("div");
    actions.className = "agent-actions";
    data.actions.forEach((action) => {
      const item = document.createElement("div");
      item.className = `agent-action ${action.ok ? "ok" : "failed"}`;
      const toolName = TOOL_NAME_TEXT[action.name] || action.name;
      item.innerHTML = `
        <span class="agent-action-name">${escapeHtml(toolName)}</span>
        <span class="agent-action-summary">${escapeHtml(action.summary || (action.ok ? "完成" : action.error || "失败"))}</span>`;
      actions.appendChild(item);
    });
    card.appendChild(actions);
  }

  appendChatNode(card);
}

function appendAgentError(message) {
  const card = document.createElement("div");
  card.className = "agent-message agent-message-ai agent-message-error";
  card.textContent = message;
  appendChatNode(card);
}

function appendChatNode(node) {
  const log = $("#agent-chat-log");
  const empty = $(".agent-empty", log);
  if (empty) empty.remove();
  log.appendChild(node);
  scrollAgentLog();
}

function scrollAgentLog() {
  const log = $("#agent-chat-log");
  log.scrollTop = log.scrollHeight;
}

function bindQaPage() {
  const input = $("#agent-message-input");
  $("#btn-agent-send").addEventListener("click", () => sendAgentMessage(input.value));

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendAgentMessage(input.value);
    }
  });

  $$(".agent-quick-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = btn.textContent.trim() === "添加RSS源"
        ? "我要添加 RSS 源"
        : btn.textContent.trim();
      sendAgentMessage(text);
    });
  });

  $("#btn-vector-sync").addEventListener("click", () => {
    syncVectorIndex().catch((err) => toast(err.message || "同步启动失败", "error"));
  });
}


/* ============ v2.3：质量评分、关联笔记、风格重生成 ============ */
let styleTargetNote = null;
const qualityPollers = {};

function hasScore(note) {
  return note?.quality_score !== null && note?.quality_score !== undefined;
}

function buildQualityBadge(note, detailed = false) {
  const reason = note.quality_reason || "可重新评估获取详细理由";
  if (detailed) {
    const wrap = document.createElement("span");
    wrap.className = "quality-detail-wrap";
    const badge = buildQualityBadge(note, false);
    const reasonEl = document.createElement("span");
    reasonEl.className = "quality-reason";
    reasonEl.textContent = reason;
    wrap.append(badge, reasonEl);
    return wrap;
  }

  const badge = document.createElement("span");
  const scored = hasScore(note);
  badge.className = `quality-badge ${scored && note.quality_score < 3 ? "is-low" : ""} ${scored ? "" : "is-pending"}`;
  badge.title = scored ? reason : "评分正在后台执行";
  badge.textContent = scored
    ? `⭐ ${Number(note.quality_score).toFixed(1)}/5`
    : "⭐ 评估中";
  return badge;
}

async function ensureRelatedLoaded(card, note) {
  if (!card || !note?.id) return;
  if (card.dataset.relatedState === "loaded" || card.dataset.relatedState === "loading") return;

  const list = $(".related-list", card);
  card.dataset.relatedState = "loading";
  list.className = "related-list";
  list.textContent = "正在加载关联笔记…";

  try {
    const body = await fetchJson(`/api/notes/${note.id}/related`);
    renderRelatedList(list, body.data || []);
    card.dataset.relatedState = "loaded";
  } catch (err) {
    list.className = "related-list related-error";
    list.textContent = err.message || "关联笔记加载失败";
    card.dataset.relatedState = "error";
  }
}

function renderRelatedList(list, items) {
  list.className = "related-list";
  list.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "related-empty";
    empty.textContent = "暂无相关笔记";
    list.appendChild(empty);
    return;
  }

  items.forEach((item) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "related-card";
    const tagText = (item.tags || []).map((tag) => `#${tag}`).join(" ");
    card.innerHTML = `
      <span class="related-card-title">${escapeHtml(item.title || "无标题")}</span>
      <span class="related-card-tags">${escapeHtml(tagText)}</span>
      <span class="related-card-summary">${escapeHtml(item.summary || "")}</span>`;
    card.addEventListener("click", (event) => {
      event.stopPropagation();
      openRelatedNote(item.id);
    });
    list.appendChild(card);
  });
}

function resetNotesView() {
  setSearchMode("keyword");
  $("#notes-search").value = "";
  $("#notes-tag-filter").value = "";
  $("#notes-category-filter").value = "";
}

async function openRelatedNote(noteId) {
  resetNotesView();
  // 已手动清空搜索和筛选，不需要跳转方法再改变视图状态
  await goToNote(noteId, false);
}

function replaceNoteCard(fresh) {
  const oldCard = $(`.note-card[data-note-id="${fresh.id}"]`);
  if (!oldCard) return null;
  const wasExpanded = oldCard.classList.contains("expanded");
  const newCard = buildNoteCard(fresh);
  oldCard.replaceWith(newCard);
  if (wasExpanded) {
    const detail = $(".note-detail", newCard);
    detail.hidden = false;
    newCard.classList.add("expanded");
    ensureRelatedLoaded(newCard, fresh);
  }
  return newCard;
}

function startQualityPolling(note) {
  if (!note?.id || qualityPollers[note.id]) return;
  let attempts = 0;

  qualityPollers[note.id] = setInterval(async () => {
    attempts += 1;
    try {
      const body = await fetchJson(`/api/notes/${note.id}`);
      const fresh = body.data;
      if (hasScore(fresh)) {
        stopQualityPolling(note.id);
        const replaced = replaceNoteCard(fresh);
        if (!replaced && $(".tab.active")?.dataset.tab === "home") {
          loadRecentNotes();
        }
      }
    } catch (_) {
      // 单次轮询失败继续重试，达到上限后停止
    }
    if (attempts >= 5) stopQualityPolling(note.id);
  }, 2500);
}

function stopQualityPolling(noteId) {
  const timer = qualityPollers[noteId];
  if (!timer) return;
  clearInterval(timer);
  delete qualityPollers[noteId];
}

let taskCenterTimer = null;

const TASK_TYPE_TEXT = {
  rewrite_url: "链接抓取改写",
  rewrite_text: "手动粘贴改写",
  batch_process: "批量处理",
  evaluate: "质量评估",
  regenerate: "风格重新生成",
};

const TASK_STATUS_TEXT = {
  pending: "等待中",
  running: "进行中",
  success: "成功",
  failed: "失败",
};

async function evaluateNoteNow(note, button) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "正在创建任务…";
  try {
    const body = await fetchJson(`/api/notes/${note.id}/evaluate`, {method: "POST"});
    button.textContent = "评估中…";
    startTaskPolling(body.data.task_id, {
      onProgress: (task) => {
        button.textContent = task.progress_message || "评估中…";
      },
      onSuccess: async (task) => {
        const freshBody = await fetchJson(`/api/notes/${note.id}`);
        const card = replaceNoteCard(freshBody.data);
        if (card) card.scrollIntoView({behavior: "smooth", block: "center"});
        toast(`质量评分：${task.result.score} 分`, "success");
      },
      onFailed: (task) => {
        button.textContent = oldText;
        toast(task.error || "质量评估失败", "error");
      },
      onError: (err) => {
        button.textContent = oldText;
        toast(err.message || "质量评估失败", "error");
      },
    });
  } catch (err) {
    toast(err.message || "任务创建失败", "error");
  } finally {
    // 轮询在后台继续，恢复按钮可操作状态
    button.disabled = false;
    if ($("#style-modal").hidden !== false) button.textContent = oldText;
  }
}

async function loadTaskList() {
  const box = $("#task-list");
  try {
    const body = await fetchJson("/api/async-tasks?limit=30");
    const tasks = body.data || [];
    renderTaskList(tasks);
    const active = tasks.some((task) => ["pending", "running"].includes(task.status));
    if (active) {
      if (!taskCenterTimer) taskCenterTimer = setInterval(loadTaskList, 2000);
    } else if (taskCenterTimer) {
      clearInterval(taskCenterTimer);
      taskCenterTimer = null;
    }
  } catch (err) {
    box.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "card glass empty";
    empty.textContent = `任务加载失败：${err.message}`;
    box.appendChild(empty);
  }
}

function renderTaskList(tasks) {
  const box = $("#task-list");
  box.innerHTML = "";
  if (!tasks.length) {
    const empty = document.createElement("div");
    empty.className = "card glass empty";
    empty.innerHTML = '<div class="empty-icon">📋</div><p>暂无后台任务</p>';
    box.appendChild(empty);
    return;
  }
  tasks.forEach((task) => box.appendChild(buildTaskCard(task)));
}

function buildTaskCard(task) {
  const card = document.createElement("div");
  card.className = `card glass task-card status-${task.status}`;

  const header = document.createElement("div");
  header.className = "task-card-head";
  header.innerHTML = `
    <div class="task-title">${escapeHtml(TASK_TYPE_TEXT[task.task_type] || task.task_type)}</div>
    <span class="task-status-badge">${escapeHtml(TASK_STATUS_TEXT[task.status] || task.status)}</span>
  `;
  card.appendChild(header);

  const progressTrack = document.createElement("div");
  progressTrack.className = "task-progress-track";
  const progressBar = document.createElement("div");
  progressBar.className = "task-progress-bar";
  progressBar.style.width = `${task.progress}%`;
  progressTrack.appendChild(progressBar);
  card.appendChild(progressTrack);

  const message = document.createElement("div");
  message.className = "task-message";
  message.textContent = task.progress_message || "等待执行";
  card.appendChild(message);

  const meta = document.createElement("div");
  meta.className = "task-meta";
  meta.textContent = `创建：${formatTime(task.created_at)}　更新：${formatTime(task.updated_at)}`;
  card.appendChild(meta);

  if (task.status === "success") {
    const result = document.createElement("div");
    result.className = "task-result";
    result.textContent = summarizeTaskResult(task.task_type, task.result);
    card.appendChild(result);
  }
  if (task.status === "failed") {
    const error = document.createElement("div");
    error.className = "task-error";
    error.textContent = task.error || "任务失败";
    card.appendChild(error);
  }

  return card;
}

function summarizeTaskResult(type, result) {
  if (!result) return "处理完成";
  if (type === "batch_process") {
    return `成功 ${result.success ?? 0} 条，失败 ${result.failed ?? 0} 条`;
  }
  if (type === "evaluate") {
    return `评分：${result.score ?? "-"} 分；${result.reason || ""}`;
  }
  if (type === "regenerate") {
    return result.note?.title ? `已更新：${result.note.title}` : "重新生成完成";
  }
  return result.title ? `已生成预览：${result.title}` : "处理完成";
}

function bindTaskCenter() {
  $("#btn-clear-cache").addEventListener("click", async () => {
    try {
      const body = await fetchJson("/api/cache/clear", {method: "POST"});
      toast(body.message, "success");
      loadTaskList();
    } catch (err) {
      toast(err.message || "清空缓存失败", "error");
    }
  });
}

/* ============ 日常/工作任务 ============ */
async function loadTaskNoteOptions() {
  const select = $("#task-form-note");
  const current = select.value;
  try {
    const body = await fetchJson("/api/notes?size=200");
    select.innerHTML = '<option value="">不关联</option>';
    (body.data.items || []).forEach((note) => {
      const option = document.createElement("option");
      option.value = note.id;
      option.textContent = note.title || `笔记 ${note.id}`;
      select.appendChild(option);
    });
    select.value = current;
  } catch (_) {
    select.innerHTML = '<option value="">不关联</option>';
  }
}

function resetTaskForm() {
  editingTaskId = null;
  $("#task-modal-title").textContent = "新增任务";
  $("#task-form-title").value = "";
  $("#task-form-category").value = "日常";
  $("#task-form-priority").value = "中";
  $("#task-form-due-date").value = "";
  $("#task-form-note").value = "";
  $("#task-modal-error").hidden = true;
}

function openTaskModal(task = null) {
  resetTaskForm();
  if (task) {
    editingTaskId = task.id;
    $("#task-modal-title").textContent = "编辑任务";
    $("#task-form-title").value = task.title || "";
    $("#task-form-category").value = task.category || "日常";
    $("#task-form-priority").value = task.priority || "中";
    $("#task-form-due-date").value = task.due_date || "";
    $("#task-form-note").value = task.note_id ? String(task.note_id) : "";
  }
  $("#task-modal").hidden = false;
  $("#task-form-title").focus();
}

function closeTaskModal() {
  $("#task-modal").hidden = true;
  editingTaskId = null;
}

async function saveTaskForm() {
  const payload = {
    title: $("#task-form-title").value.trim(),
    category: $("#task-form-category").value,
    priority: $("#task-form-priority").value,
    due_date: $("#task-form-due-date").value || null,
    note_id: $("#task-form-note").value ? Number($("#task-form-note").value) : null,
  };
  const errorBox = $("#task-modal-error");
  errorBox.hidden = true;

  if (!payload.title) {
    errorBox.textContent = "任务标题不能为空";
    errorBox.hidden = false;
    return;
  }

  const url = editingTaskId ? `/api/tasks/${editingTaskId}` : "/api/tasks";
  const method = editingTaskId ? "PUT" : "POST";
  try {
    const body = await fetchJson(url, {
      method,
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    toast(body.message, "success");
    closeTaskModal();
    await loadUserTasks();
    if ($(".tab.active")?.dataset.tab === "home") loadHomeTaskPanels();
    loadStats();
  } catch (err) {
    errorBox.textContent = err.message || "任务保存失败";
    errorBox.hidden = false;
  }
}

async function loadUserTasks() {
  const params = new URLSearchParams();
  if (taskFilters.category) params.set("category", taskFilters.category);
  if (taskFilters.completed !== "") params.set("completed", taskFilters.completed);
  const url = `/api/tasks${params.toString() ? `?${params}` : ""}`;
  try {
    const body = await fetchJson(url);
    renderUserTasks(body.data || []);
  } catch (err) {
    $("#user-task-list").innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "card glass empty";
    empty.textContent = `任务加载失败：${err.message}`;
    $("#user-task-list").appendChild(empty);
  }
}

function renderUserTasks(tasks) {
  const box = $("#user-task-list");
  box.innerHTML = "";
  if (!tasks.length) {
    const empty = document.createElement("div");
    empty.className = "card glass empty";
    empty.innerHTML = '<div class="empty-icon">🗒️</div><p>暂无任务，点下方按钮添加</p>';
    box.appendChild(empty);
    return;
  }
  tasks.forEach((task) => box.appendChild(buildUserTaskCard(task)));
}

function buildUserTaskCard(task) {
  const card = document.createElement("div");
  card.className = `card glass user-task-card ${task.completed ? "is-completed" : ""} ${task.is_overdue ? "is-overdue" : ""}`;

  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "user-task-check";
  check.checked = task.completed;
  check.addEventListener("click", (event) => event.stopPropagation());
  check.addEventListener("change", () => toggleTaskDone(task, check));
  card.appendChild(check);

  const main = document.createElement("div");
  main.className = "user-task-main";

  const title = document.createElement("div");
  title.className = "user-task-title";
  title.textContent = task.title;
  main.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "user-task-meta";
  meta.innerHTML = `
    <span class="todo-priority priority-${escapeHtml(task.priority)}">${escapeHtml(task.priority)}</span>
    <span class="todo-category">${escapeHtml(task.category)}</span>
    <span class="todo-due ${task.is_overdue ? "overdue" : ""}">${task.due_date ? `📅 ${escapeHtml(task.due_date)}` : "无截止日期"}</span>
    ${task.note_title ? `<span class="todo-note">📎 ${escapeHtml(task.note_title)}</span>` : ""}
  `;
  main.appendChild(meta);
  card.appendChild(main);

  const actions = document.createElement("div");
  actions.className = "user-task-actions";
  const editBtn = makeButton("icon-btn", "✏️");
  editBtn.title = "编辑";
  editBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    openTaskModal(task);
  });
  const deleteBtn = makeButton("icon-btn danger", "🗑️");
  deleteBtn.title = "删除";
  deleteBtn.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!confirm(`确定删除任务“${task.title}”吗？`)) return;
    try {
      await fetchJson(`/api/tasks/${task.id}`, {method: "DELETE"});
      toast("任务已删除", "success");
      await loadUserTasks();
      loadStats();
      loadHomeTaskPanels();
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });
  actions.appendChild(editBtn);
  actions.appendChild(deleteBtn);
  card.appendChild(actions);

  return card;
}

async function toggleTaskDone(task, check) {
  const action = check.checked ? "complete" : "uncomplete";
  try {
    await fetchJson(`/api/tasks/${task.id}/${action}`, {method: "POST"});
    await loadUserTasks();
    loadStats();
    loadHomeTaskPanels();
  } catch (err) {
    check.checked = !check.checked;
    toast(err.message || "操作失败", "error");
  }
}

function renderSimpleTaskList(box, tasks, emptyText) {
  box.innerHTML = "";
  if (!tasks.length) {
    const empty = document.createElement("div");
    empty.className = "simple-task-empty";
    empty.textContent = emptyText;
    box.appendChild(empty);
    return;
  }

  tasks.forEach((task) => {
    const row = document.createElement("div");
    row.className = `simple-task-row ${task.is_overdue ? "overdue" : ""}`;
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = false;
    check.addEventListener("change", async () => {
      try {
        await fetchJson(`/api/tasks/${task.id}/complete`, {method: "POST"});
        loadHomeTaskPanels();
        loadStats();
        if ($(".tab.active")?.dataset.tab === "tasks") loadUserTasks();
      } catch (err) {
        check.checked = false;
        toast(err.message || "完成失败", "error");
      }
    });

    const info = document.createElement("div");
    info.className = "simple-task-info";
    info.innerHTML = `
      <span class="simple-task-title">${escapeHtml(task.title)}</span>
      <span class="simple-task-meta">
        <span class="todo-priority priority-${escapeHtml(task.priority)}">${escapeHtml(task.priority)}</span>
        <span class="${task.is_overdue ? "overdue" : ""}">${task.due_date ? escapeHtml(task.due_date) : ""}</span>
      </span>
    `;
    row.appendChild(check);
    row.appendChild(info);
    box.appendChild(row);
  });
}

async function loadHomeTaskPanels() {
  try {
    const [todayBody, upcomingBody] = await Promise.all([
      fetchJson("/api/tasks/today"),
      fetchJson("/api/tasks?completed=false"),
    ]);
    const now = new Date();
    const end = new Date(now);
    end.setDate(end.getDate() + 7);
    const upcoming = (upcomingBody.data || []).filter((task) => {
      if (!task.due_date) return false;
      const d = new Date(`${task.due_date}T00:00:00`);
      const tomorrow = new Date(now);
      tomorrow.setHours(0, 0, 0, 0);
      tomorrow.setDate(tomorrow.getDate() + 1);
      return d >= tomorrow && d <= end;
    });
    renderSimpleTaskList($("#home-today-tasks"), todayBody.data || [], "今天没有待办");
    renderSimpleTaskList($("#home-upcoming-tasks"), upcoming, "未来 7 天没有到期任务");
  } catch (_) {
    // 首页任务失败不打断主要内容
  }
}

function bindTaskPage() {
  $("#btn-add-user-task").addEventListener("click", () => openTaskModal());
  $("#task-modal-close").addEventListener("click", closeTaskModal);
  $("#btn-cancel-user-task").addEventListener("click", closeTaskModal);
  $("#btn-save-user-task").addEventListener("click", saveTaskForm);
  $("#task-modal").addEventListener("click", (event) => {
    if (event.target === $("#task-modal")) closeTaskModal();
  });
  $("#task-form-title").addEventListener("keydown", (event) => {
    if (event.key === "Enter") saveTaskForm();
  });

  $$("#task-category-filter .segmented-btn").forEach((button) => {
    button.addEventListener("click", () => {
      taskFilters.category = button.dataset.value;
      $$("#task-category-filter .segmented-btn").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadUserTasks();
    });
  });
  $$("#task-status-filter .segmented-btn").forEach((button) => {
    button.addEventListener("click", () => {
      taskFilters.completed = button.dataset.value;
      $$("#task-status-filter .segmented-btn").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadUserTasks();
    });
  });
}

function openStyleModal(note) {
  styleTargetNote = note;
  $("#style-modal-note-title").textContent = note.title || "无标题";
  $("#style-modal-error").hidden = true;
  $$(".style-option").forEach((button) => {
    button.disabled = false;
    button.classList.remove("is-loading");
  });
  $("#style-modal").hidden = false;
}

function closeStyleModal() {
  $("#style-modal").hidden = true;
  styleTargetNote = null;
}

async function chooseRegenerateStyle(button) {
  if (!styleTargetNote) return;
  const errorBox = $("#style-modal-error");
  const buttons = $$(".style-option");
  const oldHtml = button.innerHTML;
  errorBox.hidden = true;
  buttons.forEach((item) => { item.disabled = true; });
  button.innerHTML = "⏳ 正在创建任务…";

  try {
    const body = await fetchJson(`/api/notes/${styleTargetNote.id}/regenerate`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({style: button.dataset.style}),
    });
    button.innerHTML = "⏳ 正在重新生成…";
    startTaskPolling(body.data.task_id, {
      onProgress: (task) => {
        button.innerHTML = `⏳ ${task.progress_message || "正在重新生成…"}`;
      },
      onSuccess: (task) => {
        closeStyleModal();
        const card = replaceNoteCard(task.result.note);
        if (card) card.scrollIntoView({behavior: "smooth", block: "center"});
        const warnings = task.result.warnings || [];
        toast(warnings.length ? `已完成，但有警告：${warnings.join("；")}` : "笔记已重新生成",
          warnings.length ? "warning" : "success");
      },
      onFailed: (task) => {
        errorBox.textContent = task.error || "重新生成失败";
        errorBox.hidden = false;
      },
      onError: (err) => {
        errorBox.textContent = err.message || "重新生成失败";
        errorBox.hidden = false;
      },
    });
  } catch (err) {
    errorBox.textContent = err.message || "任务创建失败";
    errorBox.hidden = false;
  } finally {
    buttons.forEach((item) => {
      item.disabled = false;
      item.classList.remove("is-loading");
    });
    if ($("#style-modal").hidden === false) button.innerHTML = oldHtml;
  }
}

function bindStyleModal() {
  $("#style-modal-close").addEventListener("click", closeStyleModal);
  $("#btn-cancel-style").addEventListener("click", closeStyleModal);
  $$(".style-option").forEach((button) => {
    button.addEventListener("click", () => chooseRegenerateStyle(button));
  });
  $("#style-modal").addEventListener("click", (event) => {
    if (event.target === $("#style-modal")) closeStyleModal();
  });
}

/* ============ 按钮波纹 ============ */
function bindRipple() {
  $$(".btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const rect = btn.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      const ripple = document.createElement("span");
      ripple.className = "ripple";
      ripple.style.width = ripple.style.height = `${size}px`;
      ripple.style.left = `${e.clientX - rect.left - size / 2}px`;
      ripple.style.top = `${e.clientY - rect.top - size / 2}px`;
      btn.appendChild(ripple);
      setTimeout(() => ripple.remove(), 600);
    });
  });
}

/* ============ 启动 ============ */
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  bindLinkModal();
  bindNotesFilters();
  bindNotesSearch();
  bindEditModal();
  bindStyleModal();
  bindSettings();
  bindUploadModal();
  bindFeedPage();
  bindQaPage();
  bindTaskCenter();
  bindTaskPage();
  bindRipple();
  loadStats();
  loadRecentNotes();
  loadConfig();
  loadSchedulerConfig();
  loadSchedulerLogs();
  loadVectorStats();
  loadTaskNoteOptions();
  loadHomeTaskPanels();
});
