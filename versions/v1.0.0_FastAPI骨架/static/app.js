/* ============================================================
   知识消化平台前端逻辑（原生 JS，无框架）
   - tab 切换淡入淡出
   - 数字滚动
   - fetch /api/* + toast
   - 按钮波纹
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
  // 触发下一帧添加 .show，触发 transition
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
    const msg = body?.message || `请求失败（${res.status}）`;
    toast(msg, "error");
    throw new Error(msg);
  }
  return body;
}

/* ============ 数字滚动动画 ============ */
function animateCounter(el, target, duration = 800) {
  const start = 0;
  const startTime = performance.now();
  function step(now) {
    const t = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);  // ease-out cubic
    const value = Math.floor(start + (target - start) * eased);
    el.textContent = value;
    if (t < 1) requestAnimationFrame(step);
    else el.textContent = target;
  }
  requestAnimationFrame(step);
}

/* ============ 首页：拉概览数字并滚动 ============ */
async function loadStats() {
  try {
    const res = await fetchJson("/api/stats");
    const d = res.data || {};
    const targets = [d.total_notes ?? 0, d.today_notes ?? 0, d.pending ?? 0];
    $$(".counter").forEach((el, i) => {
      el.dataset.target = String(targets[i]);
      animateCounter(el, targets[i]);
    });
  } catch (_) { /* toast 已提示 */ }
}

/* ============ 快速操作按钮（开发中） ============ */
function bindHomeActions() {
  $$('[data-action="add-link"], [data-action="upload"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("开发中（v1.1 实现）", "warning"));
  });
}

/* ============ 笔记库（v1.0 占位） ============ */
function bindNotesToolbar() {
  // 留接口；v1.1 接真实查询
  $("#notes-search")?.addEventListener("input", () => {
    /* TODO v1.1：实时搜索 */
  });
}

/* ============ 设置页：配置读写 ============ */
async function loadConfig() {
  try {
    const res = await fetchJson("/api/ai-config");
    const c = res.data || {};
    $("#ai-base-url").value = c.base_url || "";
    $("#ai-model").value = c.model || "";
  } catch (_) { /* ignore */ }
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
  } catch (_) { /* toast 已提示 */ }
}

async function testConnection() {
  try {
    const res = await fetchJson("/api/ai-test", { method: "POST" });
    toast(res.message || "测试完成", res.code === 0 ? "success" : "warning");
  } catch (_) { /* ignore */ }
}

function bindSettings() {
  $("#btn-save")?.addEventListener("click", saveConfig);
  $("#btn-test")?.addEventListener("click", testConnection);
  $$('[data-action="export"], [data-action="import"]').forEach((btn) => {
    btn.addEventListener("click", () => toast("数据导入导出将在 v1.2 实现", "warning"));
  });
}

/* ============ 按钮波纹 ============ */
function bindRipple() {
  $$(".btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const rect = btn.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height);
      const x = e.clientX - rect.left - size / 2;
      const y = e.clientY - rect.top - size / 2;
      const ripple = document.createElement("span");
      ripple.className = "ripple";
      ripple.style.width = ripple.style.height = `${size}px`;
      ripple.style.left = `${x}px`;
      ripple.style.top = `${y}px`;
      btn.appendChild(ripple);
      setTimeout(() => ripple.remove(), 600);
    });
  });
}

/* ============ 启动 ============ */
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  bindHomeActions();
  bindNotesToolbar();
  bindSettings();
  bindRipple();
  loadStats();
  loadConfig();
});
