/* ============================================================
   知识消化平台前端逻辑（原生 JS，无框架）
   v1.1：
   - tab 切换、toast、数字滚动、按钮波纹（v1.0 保留）
   - 链接弹窗：输入 → 处理中 → 预览 → 保存
   - 笔记库：列表加载、搜索、展开详情、删除
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
      // 每次切到笔记库都重新拉列表，保证保存后数据最新
      if (target === "notes") {
        loadNotes();
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
    const targets = [d.total_notes ?? 0, d.today_notes ?? 0, d.pending ?? 0];
    $$(".counter").forEach((el, i) => animateCounter(el, targets[i]));
  } catch (_) { /* toast 逻辑不打扰首页 */ }
}

/* ============ 链接弹窗 ============ */
let pendingUrl = "";   // 本次处理的链接，重新生成时复用
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
    // 失败回到输入阶段，在弹窗内显示原因，可直接重试
    $("#link-url-input").value = pendingUrl;
    errorBox.textContent = err.message || "处理失败，请重试";
    errorBox.hidden = false;
    showStage("input");
  }
}

function renderPreview(data) {
  $("#preview-title").textContent = data.title || "无标题";
  // 标签 chip
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
  // 重新生成：用同一链接再跑一次
  $("#btn-regenerate").addEventListener("click", processUrl);
  // 输入框回车直接开始处理
  $("#link-url-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") processUrl();
  });
  // 点遮罩空白处关闭
  $("#link-modal").addEventListener("click", (e) => {
    if (e.target === $("#link-modal")) closeLinkModal();
  });
}


/* ============ 笔记库 ============ */
async function loadNotes(keyword = "") {
  const listEl = $("#notes-list");
  try {
    const query = keyword ? `?keyword=${encodeURIComponent(keyword)}` : "";
    const res = await fetchJson(`/api/notes${query}`);
    const items = res.data.items || [];
    renderNotes(items);
  } catch (err) {
    // 列表区直接显示错误，不再弹 toast 重复打扰
    listEl.innerHTML = "";
    const card = document.createElement("div");
    card.className = "card glass empty";
    card.innerHTML = `<p>笔记加载失败：${escapeHtml(err.message)}</p>`;
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
      <p>暂无笔记</p>
      <p class="hint">回首页点「输入链接」，生成第一篇笔记</p>`;
    listEl.appendChild(empty);
    return;
  }
  items.forEach((note) => listEl.appendChild(buildNoteCard(note)));
}

function buildNoteCard(note) {
  const card = document.createElement("div");
  card.className = "card glass note-card";

  // 卡片头部：标题、标签、时间；点卡片任意位置展开
  const head = document.createElement("div");
  head.className = "note-head";
  const timeText = formatTime(note.created_at);
  head.innerHTML = `
    <div class="note-title">${escapeHtml(note.title || "无标题")}</div>
    <div class="note-meta">
      <span class="note-source">${escapeHtml(note.source || "手动输入")}</span>
      <span class="note-time">${timeText}</span>
    </div>`;
  card.appendChild(head);

  // 标签 chip
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

  // 展开区：正文 + 原文链接 + 删除按钮，默认隐藏
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
  const delBtn = document.createElement("button");
  delBtn.className = "btn btn-danger btn-sm";
  delBtn.textContent = "🗑 删除";
  detailActions.appendChild(delBtn);
  detail.appendChild(detailActions);
  card.appendChild(detail);

  // 点头部或标签区展开/收起
  head.addEventListener("click", () => {
    detail.hidden = !detail.hidden;
    card.classList.toggle("expanded", !detail.hidden);
  });

  delBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm("确定删除这篇笔记吗？删除后无法恢复。")) return;
    try {
      await fetchJson(`/api/notes/${note.id}`, { method: "DELETE" });
      card.remove();
      toast("笔记已删除", "success");
      // 删完若列表空了，刷新出空状态
      if ($("#notes-list").children.length === 0) loadNotes();
    } catch (err) {
      toast(err.message || "删除失败", "error");
    }
  });

  return card;
}

function bindNotesSearch() {
  let timer = null;
  $("#notes-search").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => loadNotes(e.target.value.trim()), 300);
  });
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
    toast(res.message || "测试完成", res.code === 0 ? "info" : "warning");
  } catch (_) { /* ignore */ }
}

function bindSettings() {
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-test").addEventListener("click", testConnection);
  $$('[data-action="export"], [data-action="import"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("数据导入导出将在后续版本实现", "warning"));
  });
}

/* ============ 上传按钮仍为占位 ============ */
function bindUploadAction() {
  $('[data-action="upload"]').addEventListener("click", () => {
    toast("文件上传将在后续版本实现", "warning");
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
  bindNotesSearch();
  bindSettings();
  bindUploadAction();
  bindRipple();
  loadStats();
  loadConfig();
});
