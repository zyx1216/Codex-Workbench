/* ============================================================
   知识消化平台前端逻辑（原生 JS，无框架）
   - tab 切换、toast、数字滚动、按钮波纹
   - 链接弹窗：输入 → 处理中 → 预览 → 保存
   - 笔记库：列表、搜索、标签/分类筛选、展开、编辑、删除
   - 文件上传：拖拽/选择、进度、预览、保存
   - 首页：真实统计数字 + 最近笔记
   ============================================================ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ============ tab 切换 ============ */
function initTabs() {
  $$(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      $$(".nav-btn").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === target));
      // 切到笔记库：拉筛选选项和列表；切回首页：刷新统计和最近笔记
      if (target === "notes") {
        initNotesFilters();
        loadNotes();
      } else if (target === "home") {
        loadStats();
        loadRecentNotes();
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

/* ============ 链接弹窗 ============ */
let pendingUrl = "";    // 本次处理的链接，重新生成时复用
let previewData = null; // 预览数据，保存时提交

function showStage(name) {
  $$(".modal-stage").forEach((stage) => {
    stage.hidden = stage.id !== `modal-stage-${name}`;
  });
}

function openLinkModal() {
  pendingUrl = "";
  previewData = null;
  $("#link-url-input").value = "";
  $("#link-modal-error").hidden = true;
  showStage("input");
  $("#link-modal").hidden = false;
}

function closeLinkModal() {
  $("#link-modal").hidden = true;
}

async function processUrl() {
  const url = $("#link-url-input").value.trim();
  const errorBox = $("#link-modal-error");
  errorBox.hidden = true;
  if (!url) {
    errorBox.textContent = "请输入网页链接";
    errorBox.hidden = false;
    return;
  }
  pendingUrl = url;
  showStage("loading");
  try {
    const res = await fetchJson("/api/process-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    previewData = res.data;
    renderPreview(previewData);
    showStage("preview");
  } catch (err) {
    // 失败回到输入阶段显示原因，可直接重试
    $("#link-url-input").value = pendingUrl;
    errorBox.textContent = err.message || "处理失败，请重试";
    errorBox.hidden = false;
    showStage("input");
  }
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
    await fetchJson("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(previewData),
    });
    toast("已保存到笔记库", "success");
    closeLinkModal();
  } catch (err) {
    toast(err.message || "保存失败", "error");
  } finally {
    btn.disabled = false;
  }
}

function bindLinkModal() {
  $('[data-action="add-link"]').addEventListener("click", openLinkModal);
  $("#link-modal-close").addEventListener("click", closeLinkModal);
  $("#btn-process-url").addEventListener("click", processUrl);
  $("#btn-save-note").addEventListener("click", saveNote);
  // 重新生成：同一链接再跑一次
  $("#btn-regenerate").addEventListener("click", processUrl);
  $("#link-url-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") processUrl();
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

function currentFilters() {
  return {
    keyword: $("#notes-search").value.trim(),
    tag: $("#notes-tag-filter").value,
    category: $("#notes-category-filter").value,
  };
}

function bindNotesFilters() {
  $("#notes-tag-filter").addEventListener("change", loadNotes);
  $("#notes-category-filter").addEventListener("change", loadNotes);
}

/* ============ 笔记库：列表 ============ */
async function loadNotes() {
  const listEl = $("#notes-list");
  const f = currentFilters();
  const params = new URLSearchParams();
  if (f.keyword) params.set("keyword", f.keyword);
  if (f.tag) params.set("tag", f.tag);
  if (f.category) params.set("category", f.category);
  const query = params.toString() ? `?${params.toString()}` : "";

  try {
    const res = await fetchJson(`/api/notes${query}`);
    renderNotes(res.data.items || []);
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

async function goToNote(noteId) {
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
    await fetchJson("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(uploadPreview),
    });
    toast("已保存到笔记库", "success");
    closeUploadModal();
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

function bindSettings() {
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-test").addEventListener("click", testConnection);
  $$('[data-action="export"], [data-action="import"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("数据导入导出将在后续版本实现", "warning"));
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
  bindSettings();
  bindUploadModal();
  bindRipple();
  loadStats();
  loadRecentNotes();
  loadConfig();
});
