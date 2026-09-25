/* 知识消化平台前端逻辑：tab 切换、fetch、toast、按钮交互。*/

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ============ tab 切换 ============ */
function initTabs() {
  $$(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      $$(".nav-btn").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === target));
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
  setTimeout(() => {
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 200);
  }, 3000);
}

/* ============ 通用 fetch ============ */
async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  let body = null;
  try { body = await res.json(); } catch (_) { /* 非 JSON 响应忽略 */ }
  if (!res.ok || (body && body.code !== 0)) {
    const msg = body?.message || `请求失败（${res.status}）`;
    toast(msg, "error");
    throw new Error(msg);
  }
  return body;
}

/* ============ 首页 ============ */
async function loadStats() {
  try {
    const res = await fetchJson("/api/stats");
    const d = res.data || {};
    $("#stat-total").textContent = d.total_notes ?? 0;
    $("#stat-today").textContent = d.today_new ?? 0;
    $("#stat-pending").textContent = d.pending_count ?? 0;
  } catch (_) { /* toast 已提示 */ }
}

function bindHomeActions() {
  $$('[data-action="add-link"], [data-action="upload"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("开发中（v1.1 实现）", "warning"));
  });
}

/* ============ 笔记库 ============ */
async function loadNotes() {
  const keyword = $("#notes-search").value.trim();
  const tag = $("#notes-tag").value;
  try {
    const res = await fetchJson(`/api/notes?keyword=${encodeURIComponent(keyword)}&tag=${encodeURIComponent(tag)}`);
    const list = res.data || [];
    const host = $("#notes-list");
    if (list.length === 0) {
      host.innerHTML = "<p>暂无笔记</p><p class=\"hint\">v1.1 将在这里显示笔记列表</p>";
      host.classList.add("empty");
      return;
    }
    host.classList.remove("empty");
    host.innerHTML = list.map((n) =>
      `<div class="note-item"><strong>${escapeHtml(n.title || "未命名")}</strong><br/>` +
      `<small>${escapeHtml(n.category || "")}</small></div>`
    ).join("");
  } catch (_) { /* ignore */ }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
}
function bindNotesToolbar() {
  let timer;
  $("#notes-search").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(loadNotes, 300);
  });
  $("#notes-tag").addEventListener("change", loadNotes);
}

/* ============ 设置页 ============ */
async function loadConfig() {
  try {
    const res = await fetchJson("/api/ai-config");
    const c = res.data || {};
    $("#ai-provider").value = c.provider || "doubao";
    $("#ai-base-url").value = c.base_url || "";
    $("#ai-model").value = c.model || "";
  } catch (_) { /* ignore */ }
}

async function saveConfig() {
  const payload = {
    provider: $("#ai-provider").value,
    base_url: $("#ai-base-url").value.trim(),
    model: $("#ai-model").value.trim(),
    api_key: $("#ai-api-key").value,
  };
  try {
    const res = await fetchJson("/api/ai-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#ai-api-key").value = "";
    $("#ai-key-status").textContent = res.data?.key_saved ? "已保存" : "未修改";
    toast("配置已保存", "success");
  } catch (_) { /* toast 已提示 */ }
}

async function testConnection() {
  try {
    const res = await fetchJson("/api/ai-test", { method: "POST" });
    toast(res.message || "测试完成", res.code === 0 ? "success" : "warning");
  } catch (_) { /* ignore */ }
}

function bindSettings() {
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-test").addEventListener("click", testConnection);
  $$('[data-action="export"], [data-action="import"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("数据导入导出将在 v1.2 实现", "warning"));
  });
}

/* ============ 启动 ============ */
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  bindHomeActions();
  bindNotesToolbar();
  bindSettings();
  loadStats();
  loadNotes();
  loadConfig();
});
