/* ============================================================
   个人工作台前端逻辑（原生 JS，无框架）
   - tab 切换、toast、数字滚动、按钮波纹
   - 链接弹窗：输入 → 处理中 → 预览 → 保存
   - 笔记库：列表、搜索、标签/分类筛选、展开、编辑、删除
   - 文件上传：拖拽/选择、进度、预览、保存
   - RSS 订阅：源管理、待处理队列、预览后保存
   - 首页：数据看板、趋势图、备份恢复入口
   - v2.11：全局搜索、快捷键、搜索历史
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
      } else if (target === "feed") {
        loadRssSources();
        loadPendingItems();
      } else if (target === "home") {
        loadStats();
        loadRecentNotes();
        loadHomeTaskPanels();
        loadHomeSchedules();
      } else if (target === "qa") {
        loadVectorStats();
      } else if (target === "tasks") {
        loadUserTasks();
      } else if (target === "calendar") {
        loadCalendarMonth();
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

/* ============ 首页数据看板：统计卡 + 趋势图 ============ */
async function loadStats() {
  // 统计卡和趋势图互不依赖，并行加载
  await Promise.all([renderDashSummary(), drawTrendChart()]);
}

async function renderDashSummary() {
  try {
    const res = await fetchJson("/api/dashboard/summary");
    const d = res.data || {};
    renderDashCard("notes", d.notes);
    renderDashCard("tasks", d.tasks);
    renderDashCard("schedules", d.schedules);
    renderDashCard("pending", d.pending);

    const infoEl = $("#auto-backup-info");
    if (infoEl) {
      infoEl.textContent = d.auto_backup
        ? `已开启，最近备份：${d.auto_backup}`
        : "已开启，今日启动后生成";
    }
  } catch (_) { /* 看板统计失败不打扰用户 */ }
}

function renderDashCard(key, card) {
  if (!card) return;
  const valueEl = $(`#dash-${key}-value`);
  const deltaEl = $(`#dash-${key}-delta`);
  if (valueEl) valueEl.textContent = card.value ?? 0;
  if (!deltaEl) return;
  // 任务卡额外显示今日已完成数
  deltaEl.innerHTML = (key === "tasks" && card.sub != null)
    ? `已完成 ${card.sub} · ${formatDelta(card.delta_percent)}`
    : formatDelta(card.delta_percent);
}

function formatDelta(percent) {
  if (percent === null || percent === undefined) return '<span class="dash-new">新增</span>';
  if (Number(percent) === 0) return "—";
  return percent > 0
    ? `<span class="dash-up">▲ ${Math.abs(percent)}%</span>`
    : `<span class="dash-down">▼ ${Math.abs(percent)}%</span>`;
}

async function drawTrendChart() {
  const canvas = $("#trend-canvas");
  if (!canvas) return;
  let points = [];
  try {
    const res = await fetchJson("/api/dashboard/trend?days=7");
    points = res.data || [];
  } catch (_) { return; }

  // 按设备像素比放大，高清屏不糊
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.parentElement.clientWidth - 40;
  const cssHeight = 200;
  canvas.width = cssWidth * dpr;
  canvas.height = cssHeight * dpr;
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const padL = 34, padR = 16, padT = 18, padB = 28;
  const plotW = cssWidth - padL - padR;
  const plotH = cssHeight - padT - padB;
  const maxVal = Math.max(1, ...points.map((p) => p.count));
  const xAt = (i) => padL + (points.length === 1 ? plotW / 2 : plotW * i / (points.length - 1));
  const yAt = (v) => padT + plotH - plotH * v / maxVal;

  ctx.clearRect(0, 0, cssWidth, cssHeight);
  ctx.font = "11px sans-serif";
  ctx.textAlign = "right";
  ctx.fillStyle = "rgba(255,255,255,0.55)";
  [0, Math.round(maxVal / 2), maxVal].forEach((v) => {
    ctx.fillText(String(v), padL - 8, yAt(v) + 4);
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.beginPath(); ctx.moveTo(padL, yAt(v)); ctx.lineTo(cssWidth - padR, yAt(v)); ctx.stroke();
  });

  if (!points.length) return;
  const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
  grad.addColorStop(0, "rgba(6,182,212,0.35)");
  grad.addColorStop(1, "rgba(6,182,212,0)");
  ctx.beginPath();
  points.forEach((p, i) => { i ? ctx.lineTo(xAt(i), yAt(p.count)) : ctx.moveTo(xAt(i), yAt(p.count)); });
  ctx.lineTo(xAt(points.length - 1), padT + plotH);
  ctx.lineTo(xAt(0), padT + plotH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.beginPath();
  points.forEach((p, i) => { i ? ctx.lineTo(xAt(i), yAt(p.count)) : ctx.moveTo(xAt(i), yAt(p.count)); });
  ctx.strokeStyle = "#06b6d4";
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.textAlign = "center";
  points.forEach((p, i) => {
    const x = xAt(i), y = yAt(p.count);
    ctx.fillStyle = "#06b6d4";
    ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.fillText(String(p.count), x, y - 8);
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.fillText(p.date, x, padT + plotH + 18);
  });
}

/* ============ 日常/工作任务状态 ============ */
let taskFilters = {category: "", completed: ""};
let editingTaskId = null;
let currentUserTasks = [];
let taskNoteSelection = new Map();
let noteTaskSelection = new Map();
let taskNoteSearchTimer = null;
let noteTaskSearchTimer = null;
let completingTaskId = null;
let draggedTaskId = null;

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

let noteTypeFilter = "";

// v2.10 笔记批量管理状态
let currentPageNotes = [];
let batchMode = false;
let selectedNoteIds = new Set();
let batchBusy = false;
let batchPromptMode = "category";

function currentFilters() {
  return {
    keyword: $("#notes-search").value.trim(),
    tag: $("#notes-tag-filter").value,
    category: $("#notes-category-filter").value,
    note_type: noteTypeFilter,
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
  $$("#search-mode .segmented-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setSearchMode(btn.dataset.mode);
      loadNotes();
    });
  });
  $$("#notes-type-mode .segmented-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      noteTypeFilter = btn.dataset.noteType || "";
      $$("#notes-type-mode .segmented-btn").forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
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
      if (f.note_type) params.set("note_type", f.note_type);
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
  currentPageNotes = items;
  listEl.classList.toggle("batch-mode", batchMode);
  listEl.innerHTML = "";
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "card glass empty";
    empty.innerHTML = `
      <div class="empty-icon">📝</div>
      <p>暂无笔记，去首页输入链接生成第一篇吧</p>`;
    listEl.appendChild(empty);
    pruneSelectedNotes();
    updateBatchUI();
    return;
  }
  items.forEach((note) => listEl.appendChild(buildNoteCard(note)));
  pruneSelectedNotes();
  updateBatchUI();
}


function buildNoteCard(note) {
  const card = document.createElement("div");
  card.className = "card glass note-card";
  if (note.quality_score !== null && note.quality_score !== undefined && note.quality_score < 3) {
    card.classList.add("low-quality");
  }
  card.dataset.noteId = note.id;
  card.classList.toggle("batch-selectable", batchMode);
  card.classList.toggle("batch-selected", selectedNoteIds.has(note.id));

  if (batchMode) {
    const checkbox = buildNoteBatchCheckbox(note);
    card.appendChild(checkbox);
  }

  // 卡片头部：标题、分类、来源、时间；点头部展开
  const head = document.createElement("div");
  head.className = "note-head";
  card.classList.toggle("meeting-note", note.note_type === "会议");
  head.innerHTML = `
    <div class="note-title">${escapeHtml(note.title || "无标题")}</div>
    <div class="note-meta">
      <span class="note-category">📁 ${escapeHtml(note.category || "默认")}</span>
      <span class="note-source">${escapeHtml(note.source || "手动输入")}</span>
      <span class="note-time">${formatTime(note.created_at)}</span>
      ${note.note_type === "会议" ? `<span class="meeting-flag">📅 ${escapeHtml(note.meeting_time ? note.meeting_time.replace("T", " ") : "会议")}</span>` : ""}
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

  if (note.note_type === "会议") {
    const meetingBox = document.createElement("div");
    meetingBox.className = "meeting-detail-box";
    meetingBox.innerHTML = `
      <div class="meeting-detail-topic">${escapeHtml(note.meeting_topic || note.title)}</div>
      <div class="meeting-detail-meta">
        <span>🕒 ${escapeHtml(note.meeting_time ? note.meeting_time.replace("T", " ") : "未定时间")}</span>
        <span>👥 ${escapeHtml((note.attendee_list || []).join("、") || "参会人未记录")}</span>
      </div>`;

    if (note.meeting_todos && note.meeting_todos.length) {
      const todoBox = document.createElement("div");
      todoBox.className = "meeting-detail-todos";
      todoBox.innerHTML = `<div class="meeting-detail-todos-title">后续待办</div>`;
      note.meeting_todos.forEach((todo, index) => {
        const row = document.createElement("div");
        row.className = "meeting-detail-todo-row";
        row.innerHTML = `
          <span class="todo-text">${escapeHtml(todo.content)}</span>
          <span class="todo-assignee">${escapeHtml(todo.assignee ? "负责人：" + todo.assignee : "")}</span>`;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-ghost btn-sm todo-create-btn";
        btn.textContent = "创建任务";
        btn.addEventListener("click", async (event) => {
          event.stopPropagation();
          btn.disabled = true;
          try {
            const titleText = todo.assignee ? `[${todo.assignee}] ${todo.content}` : todo.content;
            await fetchJson("/api/tasks", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({title: titleText, category: "工作", note_id: note.id}),
            });
            btn.textContent = "已创建";
            toast("已加入任务管理", "success");
          } catch (err) {
            btn.disabled = false;
            toast(err.message || "创建任务失败", "error");
          }
        });
        row.appendChild(btn);
        todoBox.appendChild(row);
      });
      meetingBox.appendChild(todoBox);
    }
    detail.appendChild(meetingBox);
  }

  const relatedSection = document.createElement("div");
  relatedSection.className = "related-section";
  relatedSection.innerHTML = `<div class="related-title">🔗 相关笔记</div>`;
  const relatedList = document.createElement("div");
  relatedList.className = "related-list";
  relatedList.textContent = "展开后加载";
  relatedSection.appendChild(relatedList);
  detail.appendChild(relatedSection);

  if (note.task_count > 0) {
    const taskSection = document.createElement("div");
    taskSection.className = "related-section note-task-related";
    taskSection.innerHTML = `<div class="related-title">✅ 关联任务</div>`;
    const taskList = document.createElement("div");
    taskList.className = "related-list";
    (note.task_summaries || []).forEach((task) => {
      const taskButton = document.createElement("button");
      taskButton.type = "button";
      taskButton.className = "note-task-link";
      taskButton.textContent = `${task.completed ? "✓ " : ""}${task.title || `任务 ${task.id}`}`;
      taskButton.addEventListener("click", (event) => {
        event.stopPropagation();
        openTaskFromNoteId(task.id);
      });
      taskList.appendChild(taskButton);
    });
    taskSection.appendChild(taskList);
    detail.appendChild(taskSection);
  }

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

  const exportBtn = document.createElement("button");
  exportBtn.className = "btn btn-ghost btn-sm";
  exportBtn.textContent = "📤 导出";
  detailActions.appendChild(exportBtn);

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

  exportBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    downloadSingleNote(note);
  });

  delBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const linkedCount = Number(note.task_count || 0);
    const prompt = linkedCount > 0
      ? `该笔记关联了${linkedCount}个任务，删除后关联将解除。\n确定删除这篇笔记吗？删除后无法恢复。`
      : "确定删除这篇笔记吗？删除后无法恢复。";
    if (!confirm(prompt)) return;
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


/* ============ v2.10 笔记批量管理 ============ */
function buildNoteBatchCheckbox(note) {
  const label = document.createElement("label");
  label.className = "note-batch-check";
  label.title = "选择这篇笔记";

  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = selectedNoteIds.has(note.id);
  input.addEventListener("click", (event) => event.stopPropagation());
  input.addEventListener("change", () => {
    toggleNoteSelection(note.id, input.checked);
  });

  label.appendChild(input);
  return label;
}

function toggleNoteSelection(noteId, checked) {
  if (checked) selectedNoteIds.add(noteId);
  else selectedNoteIds.delete(noteId);
  const card = $(`.note-card[data-note-id="${noteId}"]`);
  if (card) card.classList.toggle("batch-selected", checked);
  updateBatchUI();
}

function pruneSelectedNotes() {
  const currentIds = new Set(currentPageNotes.map((note) => note.id));
  selectedNoteIds = new Set(
    Array.from(selectedNoteIds).filter((id) => currentIds.has(id))
  );
}

function getSelectedNotes() {
  return currentPageNotes.filter((note) => selectedNoteIds.has(note.id));
}

function updateBatchUI() {
  if (!batchMode) return;
  const count = selectedNoteIds.size;
  $("#notes-batch-count").textContent = `已选 ${count} 篇`;
  const allInput = $("#notes-batch-all");
  const total = currentPageNotes.length;
  allInput.checked = total > 0 && count === total;
  allInput.indeterminate = count > 0 && count < total;
}

function setBatchMode(enabled) {
  batchMode = enabled;
  if (!enabled) selectedNoteIds.clear();
  $("#notes-batch-bar").hidden = !enabled;
  renderNotes(currentPageNotes);
}

function setBatchBusy(busy) {
  batchBusy = busy;
  $$("#notes-batch-bar button").forEach((btn) => {
    btn.disabled = busy;
  });
  $("#notes-batch-all").disabled = busy;
}

async function batchDeleteNotes() {
  const notes = getSelectedNotes();
  if (!notes.length) {
    toast("请先选择要删除的笔记", "error");
    return;
  }
  if (!confirm(`确定删除${notes.length}篇笔记？此操作不可恢复`)) return;

  setBatchBusy(true);
  const failed = [];
  const successIds = new Set();
  for (const note of notes) {
    try {
      await fetchJson(`/api/notes/${note.id}`, { method: "DELETE" });
      successIds.add(note.id);
      selectedNoteIds.delete(note.id);
    } catch (err) {
      failed.push({ note, message: err.message });
    }
  }

  // 成功删除的卡片移出当前页；失败项保留方便重试
  currentPageNotes = currentPageNotes.filter(
    (note) => !successIds.has(note.id)
  );
  renderNotes(currentPageNotes);
  setBatchBusy(false);

  const successCount = notes.length - failed.length;
  if (failed.length === 0) {
    toast(`已成功操作${successCount}篇笔记`, "success");
  } else {
    failed.forEach((item) => {
      toast(`《${item.note.title || "无标题"}》删除失败：${item.message}`, "error");
    });
    toast(`成功 ${successCount} 篇，失败 ${failed.length} 篇`, "warning");
  }
}

async function fillBatchCategorySelect() {
  const select = $("#note-batch-category");
  select.innerHTML = "";
  const res = await fetchJson("/api/categories");
  (res.data || []).forEach((category) => {
    const option = document.createElement("option");
    option.value = category.name;
    option.textContent = category.name;
    select.appendChild(option);
  });
}

async function openBatchPrompt(mode) {
  if (!getSelectedNotes().length) {
    toast("请先选择笔记", "error");
    return;
  }
  batchPromptMode = mode;
  $("#note-batch-prompt-error").hidden = true;
  $("#note-batch-tags").value = "";

  try {
    await fillBatchCategorySelect();
  } catch (err) {
    toast(err.message || "分类加载失败", "error");
    return;
  }

  if (mode === "category") {
    $("#note-batch-prompt-title").textContent = "批量改分类";
    $("#note-batch-category-field").hidden = false;
    $("#note-batch-tags-field").hidden = true;
  } else {
    $("#note-batch-prompt-title").textContent = "批量加标签";
    $("#note-batch-category-field").hidden = true;
    $("#note-batch-tags-field").hidden = false;
  }
  $("#note-batch-prompt-modal").hidden = false;
}

function closeBatchPrompt() {
  $("#note-batch-prompt-modal").hidden = true;
}

async function confirmBatchPrompt() {
  const notes = getSelectedNotes();
  let updateItems = [];

  if (batchPromptMode === "category") {
    const category = $("#note-batch-category").value;
    if (!category) {
      toast("请选择分类", "error");
      return;
    }
    updateItems = notes.map((note) => ({
      note,
      payload: {
        title: note.title,
        content: note.content,
        tags: note.tags || [],
        category,
      },
    }));
  } else {
    const newTags = parseTagsText($("#note-batch-tags").value);
    if (!newTags.length) {
      const error = $("#note-batch-prompt-error");
      error.textContent = "请填写至少一个标签";
      error.hidden = false;
      return;
    }
    updateItems = notes.map((note) => ({
      note,
      payload: {
        title: note.title,
        content: note.content,
        tags: Array.from(new Set([...(note.tags || []), ...newTags])),
        category: note.category || "默认",
      },
    }));
  }

  const button = $("#btn-note-batch-confirm");
  button.disabled = true;
  setBatchBusy(true);
  const failed = [];

  for (const item of updateItems) {
    try {
      await fetchJson(`/api/notes/${item.note.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(item.payload),
      });
    } catch (err) {
      failed.push({ note: item.note, message: err.message });
    }
  }

  const failedIds = new Set(failed.map((item) => item.note.id));
  selectedNoteIds = new Set(
    Array.from(selectedNoteIds).filter((id) => failedIds.has(id))
  );
  closeBatchPrompt();
  await loadNotes();
  setBatchBusy(false);
  button.disabled = false;

  const successCount = updateItems.length - failed.length;
  if (!failed.length) {
    toast(`已成功操作${successCount}篇笔记`, "success");
  } else {
    failed.forEach((item) => {
      toast(`《${item.note.title || "无标题"}》操作失败：${item.message}`, "error");
    });
    toast(`成功 ${successCount} 篇，失败 ${failed.length} 篇`, "warning");
  }
}

function parseDownloadFileName(response, fallbackName) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const matched = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (matched) return decodeURIComponent(matched[1]);
  return fallbackName;
}

async function readDownloadResponse(response, fallbackName) {
  if (!response.ok) {
    let body = null;
    try { body = await response.json(); } catch (_) { /* 非 JSON 忽略 */ }
    throw new Error(body?.message || body?.detail || `下载失败（${response.status}）`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = parseDownloadFileName(response, fallbackName);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadSingleNote(note) {
  const link = document.createElement("a");
  link.href = `/api/notes/${note.id}/export`;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function batchExportNotes() {
  const notes = getSelectedNotes();
  if (!notes.length) {
    toast("请先选择要导出的笔记", "error");
    return;
  }

  const button = $("#btn-batch-export");
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "导出中…";
  try {
    const response = await fetch("/api/notes/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note_ids: notes.map((note) => note.id) }),
    });
    await readDownloadResponse(response, "笔记导出.zip");
    toast("笔记导出完成", "success");
  } catch (err) {
    toast(err.message || "笔记导出失败", "error");
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function bindNoteBatchActions() {
  $("#btn-batch-manage").addEventListener("click", () => setBatchMode(true));
  $("#btn-batch-cancel").addEventListener("click", () => setBatchMode(false));
  $("#notes-batch-all").addEventListener("change", () => {
    // change 触发时复选框状态已变化，按变更前的选择数量判断更可靠
    const shouldSelectAll = selectedNoteIds.size < currentPageNotes.length;
    currentPageNotes.forEach((note) => toggleNoteSelection(note.id, shouldSelectAll));
  });
  $("#btn-batch-delete").addEventListener("click", batchDeleteNotes);
  $("#btn-batch-category").addEventListener("click", () => openBatchPrompt("category"));
  $("#btn-batch-tags").addEventListener("click", () => openBatchPrompt("tags"));
  $("#btn-batch-export").addEventListener("click", batchExportNotes);
  $("#note-batch-prompt-close").addEventListener("click", closeBatchPrompt);
  $("#btn-note-batch-cancel").addEventListener("click", closeBatchPrompt);
  $("#btn-note-batch-confirm").addEventListener("click", confirmBatchPrompt);
  $("#note-batch-prompt-modal").addEventListener("click", (event) => {
    if (event.target.id === "note-batch-prompt-modal") closeBatchPrompt();
  });
}


/* ============ 编辑弹窗 ============ */
let editingNote = null;

async function fillNoteCategorySelect(currentCategory = "默认") {
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
  } catch (_) { /* 分类加载失败继续，至少保证默认和当前分类可选 */ }
  if (!Array.from(catSel.options).some((option) => option.value === "默认")) {
    const defaultOpt = document.createElement("option");
    defaultOpt.value = "默认";
    defaultOpt.textContent = "默认";
    catSel.prepend(defaultOpt);
  }
  if (currentCategory && !Array.from(catSel.options).some((option) => option.value === currentCategory)) {
    const opt = document.createElement("option");
    opt.value = currentCategory;
    opt.textContent = currentCategory;
    catSel.appendChild(opt);
  }
  catSel.value = currentCategory || "默认";
}

async function openCreateNoteModal() {
  editingNote = null;
  noteTaskSelection.clear();
  $("#edit-modal-title").textContent = "📝 新建笔记";
  $("#edit-modal-error").hidden = true;
  $("#edit-title").value = "";
  $("#edit-content").value = "";
  $("#edit-tags").value = "";
  $("#note-task-field").hidden = true;
  $("#note-task-search").value = "";
  hideLinkResults($("#note-task-results"));
  renderNoteTaskChips();
  await fillNoteCategorySelect("默认");
  $("#edit-modal").hidden = false;
  $("#edit-title").focus();
}

async function openCreateNoteShortcut() {
  // 从全局搜索触发新建时，先关闭搜索弹窗，避免编辑弹窗被遮住
  if (!$('#global-search-modal').hidden) closeGlobalSearch();
  $('.nav-btn[data-tab="notes"]').click();
  await openCreateNoteModal();
}

async function openEditModal(note) {
  editingNote = note;
  $("#edit-modal-title").textContent = "✏️ 编辑笔记";
  const errorBox = $("#edit-modal-error");
  errorBox.hidden = true;

  // 预填当前数据
  $("#edit-title").value = note.title || "";
  $("#edit-content").value = note.content || "";
  $("#edit-tags").value = tagsToText(note.tags || []);
  noteTaskSelection.clear();
  (note.task_summaries || []).forEach((task) => noteTaskSelection.set(task.id, task));
  $("#note-task-field").hidden = false;
  $("#note-task-search").value = "";
  hideLinkResults($("#note-task-results"));
  renderNoteTaskChips();
  await fillNoteCategorySelect(note.category || "默认");

  $("#edit-modal").hidden = false;
}

function closeEditModal() {
  $("#edit-modal").hidden = true;
  editingNote = null;
  hideLinkResults($("#note-task-results"));
}

async function saveEdit() {
  const btn = $("#btn-save-edit");
  btn.disabled = true;
  const payload = {
    title: $("#edit-title").value.trim(),
    content: $("#edit-content").value,
    tags: parseTagsText($("#edit-tags").value),
    category: $("#edit-category").value || "默认",
  };
  if (editingNote) {
    payload.task_ids = Array.from(noteTaskSelection.keys());
  }
  if (!payload.title) {
    const errorBox = $("#edit-modal-error");
    errorBox.textContent = "笔记标题不能为空";
    errorBox.hidden = false;
    btn.disabled = false;
    return;
  }
  try {
    let response;
    if (editingNote) {
      response = await fetchJson(`/api/notes/${editingNote.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      response = await fetchJson("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    toast(response.message, "success");
    closeEditModal();
    // 重拉列表，保证卡片实时更新
    loadNotes();
    loadStats();
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

/* 最近笔记：横向滚动卡片 */
async function loadRecentNotes() {
  const box = $("#dash-recent-notes");
  if (!box) return;
  box.innerHTML = "";
  try {
    const res = await fetchJson("/api/notes?size=5");
    const items = res.data.items || [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "dash-recent-empty";
      empty.textContent = "暂无笔记";
      box.appendChild(empty);
      return;
    }
    items.forEach((note) => {
      const card = document.createElement("div");
      card.className = "card glass dash-note-card";
      card.innerHTML = `
        <div class="dash-note-title">${escapeHtml(note.title || "无标题")}</div>
        <div class="dash-note-meta">
          <span class="dash-note-cat">${escapeHtml(note.category || "默认")}</span>
          <span class="dash-note-date">${escapeHtml((note.created_at || "").slice(0, 10))}</span>
        </div>`;
      card.addEventListener("click", () => goToNote(note.id));
      box.appendChild(card);
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

/* RSS 源新增/编辑弹窗状态 */
let rssEditingId = null;

function collectRssForm() {
  return {
    name: $("#rss-form-name").value.trim(),
    url: $("#rss-form-url").value.trim(),
    focus_topics: $("#rss-form-topics").value.trim(),
    auto_process: $("#rss-form-auto").checked,
    ai_filter_enabled: $("#rss-form-ai-filter").checked,
  };
}

function resetRssModal() {
  rssEditingId = null;
  $("#rss-modal-title").textContent = "添加 RSS 源";
  $("#rss-form-name").value = "";
  $("#rss-form-url").value = "";
  $("#rss-form-topics").value = "";
  $("#rss-form-ai-filter").checked = true;
  $("#rss-form-auto").checked = false;
  $("#rss-modal-error").hidden = true;
}

function openRssModal() {
  resetRssModal();
  $("#rss-modal").hidden = false;
}

function fillRssModal(source) {
  rssEditingId = source.id;
  $("#rss-modal-title").textContent = "编辑 RSS 源";
  $("#rss-form-name").value = source.name || "";
  $("#rss-form-url").value = source.url || "";
  $("#rss-form-topics").value = source.focus_topics || "";
  $("#rss-form-ai-filter").checked = !!source.ai_filter_enabled;
  $("#rss-form-auto").checked = !!source.auto_process;
  $("#rss-modal-error").hidden = true;
}

async function saveRssSource() {
  const payload = collectRssForm();
  const errorBox = $("#rss-modal-error");
  errorBox.hidden = true;
  if (!payload.name) {
    errorBox.textContent = "请填写 RSS 源名称";
    errorBox.hidden = false;
    return;
  }
  if (!payload.url) {
    errorBox.textContent = "请填写 RSS 地址";
    errorBox.hidden = false;
    return;
  }

  const btn = $("#btn-save-rss");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    let res;
    if (rssEditingId === null) {
      res = await fetchJson("/api/rss-sources", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
    } else {
      res = await fetchJson(`/api/rss-sources/${rssEditingId}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
    }
    $("#rss-modal").hidden = true;
    toast(res.message, "success");
    await loadRssSources();
  } catch (err) {
    errorBox.textContent = err.message || "保存失败";
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "保存";
  }
}

async function loadRssSources() {
  const box = $("#rss-source-list");
  box.innerHTML = "";
  try {
    const res = await fetchJson("/api/rss-sources");
    const sources = res.data || [];
    if (!sources.length) {
      box.appendChild(makeFeedEmpty("📡", "暂无 RSS 源，点上方按钮添加"));
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
  const topicText = source.focus_topics || "未设置，全部进待处理";
  head.innerHTML = `
    <div class="feed-item-main">
      <div class="feed-item-title">${escapeHtml(source.name)}</div>
      <div class="feed-item-url">${escapeHtml(source.url)}</div>
      <div class="feed-item-meta">
        <span class="source-topic">🎯 ${escapeHtml(topicText)}</span>
        <span class="source-badges">
          <span class="badge-pill ${source.ai_filter_enabled ? "on" : "off"}">AI筛选 ${source.ai_filter_enabled ? "开" : "关"}</span>
          <span class="badge-pill ${source.auto_process ? "on warn" : "off"}">自动保存 ${source.auto_process ? "开" : "关"}</span>
        </span>
      </div>
      <div class="feed-item-meta">
        <span>最后抓取：${source.last_fetched ? formatTime(source.last_fetched) : "尚未抓取"}</span>
      </div>
    </div>`;

  const actions = document.createElement("div");
  actions.className = "feed-item-actions";
  const editBtn = makeButton("btn btn-ghost btn-sm", "编辑");
  const fetchBtn = makeButton("btn btn-ghost btn-sm", "抓取");
  const deleteBtn = makeButton("btn btn-danger btn-sm", "删除");
  actions.append(editBtn, fetchBtn, deleteBtn);
  head.appendChild(actions);
  card.appendChild(head);

  editBtn.addEventListener("click", () => {
    fillRssModal(source);
    $("#rss-modal").hidden = false;
  });

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
    const sum = {
      added: results.reduce((s, i) => s + (i.added || 0), 0),
      pending_count: results.reduce((s, i) => s + (i.pending_count || 0), 0),
      auto_saved_count: results.reduce((s, i) => s + (i.auto_saved_count || 0), 0),
      filtered_count: results.reduce((s, i) => s + (i.filtered_count || 0), 0),
    };
    const failed = results.filter((item) => item.error);
    let message = `本次发现 ${sum.added} 条，相关进入待处理 ${sum.pending_count} 条，自动保存 ${sum.auto_saved_count} 条，筛选掉 ${sum.filtered_count} 条`;
    if (failed.length) {
      message += `，${failed.length} 个源失败`;
      toast(message, "warning");
    } else {
      toast(message, "success");
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
  $("#btn-add-rss").addEventListener("click", openRssModal);
  $("#btn-fetch-all-rss").addEventListener("click", fetchAllRssSources);
  $("#btn-save-rss").addEventListener("click", saveRssSource);
  $("#rss-modal-close").addEventListener("click", () => {
    $("#rss-modal").hidden = true;
  });
  $("#rss-modal").addEventListener("click", (event) => {
    if (event.target.id === "rss-modal") $("#rss-modal").hidden = true;
  });
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
    $("#embedding-base-url").value = c.embedding_base_url || "https://ark.cn-beijing.volces.com/api/v3";
    $("#embedding-model").value = c.embedding_model || "doubao-embedding-text-240715";
    $("#embedding-use-local").checked = Boolean(c.use_local_embedding);
    $("#rerank-enabled").checked = Boolean(c.rerank_enabled);
    $("#rerank-model").value = c.rerank_model || "doubao-rerank-32k";
    updateVectorConfigText();
  } catch (_) { /* 设置页加载失败不打扰 */ }
}

async function saveConfig() {
  const payload = {
    base_url: $("#ai-base-url").value.trim(),
    model: $("#ai-model").value.trim(),
    api_key: $("#ai-api-key").value,
    embedding_base_url: $("#embedding-base-url").value.trim(),
    embedding_model: $("#embedding-model").value.trim(),
    use_local_embedding: $("#embedding-use-local").checked,
    rerank_enabled: $("#rerank-enabled").checked,
    rerank_model: $("#rerank-model").value.trim(),
  };
  try {
    const res = await fetchJson("/api/ai-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#ai-api-key").value = "";
    updateVectorConfigText();
    const needRebuild = Boolean(res.data?.rebuild_required);
    $("#embedding-config-hint").textContent = needRebuild
      ? "向量模型已变化，请点击 AI 助手页的“重建向量库”。"
      : "切换向量模型后需要重建向量库。";
    toast(res.message || (needRebuild ? "配置已保存，需要重建向量库" : "配置已保存"), needRebuild ? "warning" : "success");
  } catch (_) { /* 保存失败已有反馈路径 */ }
}

function updateVectorConfigText() {
  const local = $("#embedding-use-local");
  const localText = local.closest(".field").querySelector(".switch-text");
  if (localText) localText.textContent = local.checked ? "使用本地模型" : "使用火山方舟";
  const rerank = $("#rerank-enabled");
  const rerankText = rerank.closest(".field").querySelector(".switch-text");
  if (rerankText) rerankText.textContent = rerank.checked ? "开启" : "关闭";
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
  $("#embedding-use-local").addEventListener("change", updateVectorConfigText);
  $("#rerank-enabled").addEventListener("change", updateVectorConfigText);
  $("#btn-save-scheduler").addEventListener("click", saveSchedulerSettings);
  $("#btn-run-now").addEventListener("click", (event) => runFetchNow(event.currentTarget));
  $("#btn-backup-export").addEventListener("click", downloadBackup);
  $("#btn-backup-restore").addEventListener("click", () => $("#restore-file-input").click());
  $("#restore-file-input").addEventListener("change", onRestoreFilePicked);
  $("#restore-confirm-close").addEventListener("click", closeRestoreConfirm);
  $("#btn-cancel-restore").addEventListener("click", closeRestoreConfirm);
  $("#btn-confirm-restore").addEventListener("click", applyPreparedRestore);
  $("#restore-confirm-modal").addEventListener("click", (event) => {
    if (event.target.id === "restore-confirm-modal") closeRestoreConfirm();
  });
}

let pendingRestore = null;

/* 一键导出备份：取到完整 zip 后再触发浏览器下载 */
async function downloadBackup() {
  const btn = $("#btn-backup-export");
  btn.disabled = true;
  btn.textContent = "正在备份…";
  try {
    const res = await fetch("/api/backup");
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const body = await res.json();
      throw new Error(body.message || "备份失败");
    }
    if (!res.ok) throw new Error(`备份失败（${res.status}）`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filenameFromDisposition(res.headers.get("content-disposition")) || "工作台备份.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("备份成功", "success");
  } catch (err) {
    toast(err.message || "备份失败", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "一键导出全部数据";
  }
}

function filenameFromDisposition(header) {
  if (!header) return "";
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) return decodeURIComponent(encoded[1]);
  return "";
}

/* 选中 zip 后先上传预检，通过后打开确认弹窗 */
async function onRestoreFilePicked(event) {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (!file) return;

  const btn = $("#btn-backup-restore");
  btn.disabled = true;
  btn.textContent = "正在校验备份…";
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetchJson("/api/backup/restore/prepare", {method: "POST", body: form});
    pendingRestore = res.data || {};
    openRestoreConfirm(pendingRestore);
  } catch (err) {
    toast(err.message || "备份校验失败，当前数据未改动", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "从备份恢复";
  }
}

function openRestoreConfirm(data) {
  $("#restore-backup-time").textContent = data.backup_time || "未知";
  const list = $("#restore-file-list");
  list.innerHTML = "";
  (data.files || []).forEach((name) => {
    const item = document.createElement("li");
    item.textContent = name;
    list.appendChild(item);
  });
  $("#restore-confirm-modal").hidden = false;
}

function closeRestoreConfirm() {
  $("#restore-confirm-modal").hidden = true;
  pendingRestore = null;
}

/* 确认恢复：服务端会先自动备份当前数据，再替换文件 */
async function applyPreparedRestore() {
  if (!pendingRestore?.token) return;
  const btn = $("#btn-confirm-restore");
  btn.disabled = true;
  btn.textContent = "正在恢复…";
  try {
    const res = await fetchJson("/api/backup/restore/apply", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: pendingRestore.token}),
    });
    pendingRestore = null;
    $("#restore-confirm-modal").hidden = true;
    toast(res.message || "恢复成功，请刷新页面", "success");
  } catch (err) {
    toast(err.message || "恢复失败，当前数据未改动", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "确认恢复";
  }
}


/* ============ v2.0：AI 助手聊天和向量同步 ============ */
let qaPollTimer = null;
const AGENT_HISTORY_KEY = "personalWorkbenchAgentHistory";
const AGENT_REMINDER_KEY = "personalWorkbenchAgentReminders";
const AGENT_MAX_MESSAGES = 20;
const AGENT_MAX_SUMMARY = 1500;
let agentMemory = {summary: "", messages: []};
let agentReminderTimer = null;

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
  create_reminder: "创建提醒",
  fetch_webpage: "抓取网页",
};

function stateText(status) {
  return {
    idle: "空闲",
    running: "同步中",
    rebuilding: "重建中",
    done: "同步完成",
    failed: "同步失败",
  }[status] || status;
}

async function loadVectorStats() {
  try {
    const res = await fetchJson("/api/vector/stats");
    const d = res.data || {};
    $("#qa-vector-count").textContent = `向量笔记：${d.note_count ?? 0}`;
    const mode = d.mode === "local" ? "本地 384 维" : "火山方舟 2048 维";
    $("#qa-vector-mode").textContent = `向量：${mode}${d.fallback_active ? "（已降级本地）" : ""}`;
    if (d.status === "running" || d.status === "rebuilding") {
      const doneCount = Number(d.progress || 0);
      const totalCount = Number(d.total || 0);
      $("#qa-vector-state").textContent =
        totalCount ? `状态：重建中 ${doneCount}/${totalCount}` : "状态：重建中";
      startVectorPolling(false);
    } else {
      $("#qa-vector-state").textContent = `状态：${stateText(d.status || "idle")}`;
      stopVectorPolling();
    }
    const lock = d.status === "running" || d.status === "rebuilding";
    const input = $("#agent-message-input");
    const send = $("#btn-agent-send");
    if (input) input.disabled = lock;
    if (send) send.disabled = lock;
  } catch (err) {
    $("#qa-vector-state").textContent = `状态不可用：${err.message}`;
  }
}

function startVectorPolling(showStartToast = true) {
  if (showStartToast) toast("向量库重建已开始，正在预热远端和本地索引", "info");
  if (qaPollTimer) return;
  qaPollTimer = setInterval(loadVectorStats, 1000);
}

function stopVectorPolling() {
  if (!qaPollTimer) return;
  clearInterval(qaPollTimer);
  qaPollTimer = null;
}

async function syncVectorIndex() {
  await fetchJson("/api/vector/rebuild", { method: "POST" });
  startVectorPolling(true);
  setTimeout(loadVectorStats, 500);
}
function readAgentMemory() {
  try {
    const raw = JSON.parse(localStorage.getItem(AGENT_HISTORY_KEY) || '{}');
    if (raw && Array.isArray(raw.messages)) {
      agentMemory = {
        summary: String(raw.summary || '').slice(0, AGENT_MAX_SUMMARY),
        messages: raw.messages.filter((item) => item && (item.role === 'user' || item.role === 'assistant')).slice(-AGENT_MAX_MESSAGES),
      };
      return;
    }
  } catch (_) { /* 损坏历史按空处理 */ }
  agentMemory = {summary: '', messages: []};
}

function saveAgentMemory() {
  try {
    localStorage.setItem(AGENT_HISTORY_KEY, JSON.stringify(agentMemory));
  } catch (_) { /* localStorage 不可用时保持当前会话 */ }
}

function updateAgentSummaryState() {
  const el = $('#agent-summary-state');
  if (!el) return;
  el.textContent = agentMemory.summary ? '历史：已摘要' : '历史：无摘要';
}

function formatAgentTime(timestamp) {
  const date = new Date(timestamp || Date.now());
  if (Number.isNaN(date.getTime())) return '';
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function inlineMarkdown(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function markdownToHtml(markdown) {
  const source = escapeHtml(markdown || '');
  const lines = source.split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let listType = '';
  let codeLines = [];
  let inCode = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map(inlineMarkdown).join(' ')}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (!listType) return;
    html.push(listType === 'ul' ? '</ul>' : '</ol>');
    listType = '';
  };

  lines.forEach((line) => {
    if (line.startsWith('```')) {
      flushParagraph();
      closeList();
      if (inCode) {
        html.push(`<pre><code>${codeLines.join('\n')}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        inCode = true;
      }
      return;
    }
    if (inCode) {
      codeLines.push(line);
      return;
    }
    if (!line.trim()) {
      flushParagraph();
      closeList();
      return;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length + 2;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      return;
    }
    const unordered = line.match(/^[-*]\s+(.+)$/);
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? 'ul' : 'ol';
      if (listType !== nextType) {
        closeList();
        listType = nextType;
        html.push(nextType === 'ul' ? '<ul>' : '<ol>');
      }
      html.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
      return;
    }
    closeList();
    paragraph.push(line);
  });

  if (inCode) html.push(`<pre><code>${codeLines.join('\n')}</code></pre>`);
  flushParagraph();
  closeList();
  return html.join('');
}

function buildAgentActions(actions) {
  const details = document.createElement('details');
  details.className = 'agent-thinking';
  const summary = document.createElement('summary');
  summary.textContent = `思考过程（${actions.length}）`;
  details.appendChild(summary);
  const body = document.createElement('div');
  body.className = 'agent-thinking-body';
  actions.forEach((action) => {
    const row = document.createElement('div');
    row.className = `agent-action ${action.ok ? 'ok' : 'failed'}`;
    const toolName = TOOL_NAME_TEXT[action.name] || action.name;
    row.innerHTML = `<span class="agent-action-name">${escapeHtml(toolName)}</span><span class="agent-action-summary">${escapeHtml(action.summary || action.error || (action.ok ? '完成' : '失败'))}</span>`;
    body.appendChild(row);
  });
  details.appendChild(body);
  return details;
}

function buildAgentSources(sources) {
  const wrap = document.createElement('div');
  wrap.className = 'agent-sources';
  const title = document.createElement('div');
  title.className = 'agent-sources-title';
  title.textContent = '参考资料';
  wrap.appendChild(title);
  sources.forEach((source, index) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'agent-source-item';
    const score = Number(source.score || 0);
    row.innerHTML = `<span class="agent-source-index">[${index + 1}]</span><span class="agent-source-name">${escapeHtml(source.title || '无标题')}</span><span class="agent-source-meta">${escapeHtml(source.category || '默认')} · ${Math.round(score * 100)}%</span>`;
    row.addEventListener('click', () => goToNote(source.id));
    wrap.appendChild(row);
  });
  return wrap;
}
function buildAgentMessageNode(message) {
  const card = document.createElement('div');
  card.className = `agent-message agent-message-${message.role === 'user' ? 'user' : 'ai'}`;
  if (message.role === 'assistant') {
    const answer = document.createElement('div');
    answer.className = 'agent-answer';
    answer.innerHTML = markdownToHtml(message.content || '');
    card.appendChild(answer);
    if (Array.isArray(message.actions) && message.actions.length) {
      card.appendChild(buildAgentActions(message.actions));
    if (Array.isArray(message.sources) && message.sources.length) {
      card.appendChild(buildAgentSources(message.sources));
    }
    }
  } else {
    card.textContent = message.content || '';
  }
  const time = document.createElement('div');
  time.className = 'agent-message-time';
  time.textContent = formatAgentTime(message.timestamp);
  card.appendChild(time);
  return card;
}

function appendChatNode(node) {
  const log = $('#agent-chat-log');
  const empty = $('.agent-empty', log);
  if (empty) empty.remove();
  log.appendChild(node);
  scrollAgentLog();
}

function scrollAgentLog() {
  const log = $('#agent-chat-log');
  if (log) log.scrollTop = log.scrollHeight;
}

function renderAgentHistory() {
  const log = $('#agent-chat-log');
  if (!log) return;
  log.innerHTML = '';
  if (!agentMemory.messages.length) {
    const empty = document.createElement('div');
    empty.className = 'agent-empty';
    empty.textContent = '用大白话告诉我要查什么、记什么，或要操作 RSS。';
    log.appendChild(empty);
    return;
  }
  agentMemory.messages.forEach((message) => log.appendChild(buildAgentMessageNode(message)));
  scrollAgentLog();
}

function appendUserMessage(text) {
  const message = {role: 'user', content: text, timestamp: new Date().toISOString(), actions: []};
  appendChatNode(buildAgentMessageNode(message));
  return message;
}

function appendTypingMessage() {
  const wrap = document.createElement('div');
  wrap.className = 'agent-message agent-message-ai';
  wrap.innerHTML = '<span class="typing-dots"><i></i><i></i><i></i></span><span class="typing-text">正在输入...</span>';
  appendChatNode(wrap);
  return wrap;
}

function renderAgentAnswer(data, typingNode) {
  if (typingNode) typingNode.remove();
  const message = {
    role: 'assistant',
    content: data.answer || '',
    timestamp: new Date().toISOString(),
    actions: Array.isArray(data.actions) ? data.actions : [],
    sources: Array.isArray(data.sources) ? data.sources : [],
  };
  appendChatNode(buildAgentMessageNode(message));
  return message;
}
function appendAgentError(message) {
  const card = document.createElement('div');
  card.className = 'agent-message agent-message-ai agent-message-error';
  card.textContent = message;
  appendChatNode(card);
}

function readAgentReminders() {
  try {
    const items = JSON.parse(localStorage.getItem(AGENT_REMINDER_KEY) || '[]');
    return Array.isArray(items) ? items : [];
  } catch (_) {
    return [];
  }
}

function saveAgentReminders(items) {
  try {
    localStorage.setItem(AGENT_REMINDER_KEY, JSON.stringify(items));
  } catch (_) { /* localStorage 不可用时只在当前页面生效 */ }
}

async function requestAgentNotificationPermission() {
  if (!('Notification' in window)) return 'unsupported';
  if (Notification.permission !== 'default') return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch (_) {
    return 'denied';
  }
}

async function registerAgentReminder(action) {
  const content = String(action.content || '').trim();
  const rawTime = String(action.remind_time || '').trim();
  const when = new Date(rawTime.replace(' ', 'T'));
  if (!content || Number.isNaN(when.getTime())) {
    toast('提醒数据不完整，未保存', 'error');
    return;
  }
  const reminders = readAgentReminders();
  reminders.push({
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    content,
    remind_time: rawTime,
    timestamp: when.getTime(),
  });
  saveAgentReminders(reminders);
  await requestAgentNotificationPermission();
  toast(`提醒已创建：${rawTime}`, 'success');
}

function applyClientActions(actions) {
  (actions || []).forEach((action) => {
    if (action && action.client_action && action.client_action.type === 'create_reminder') {
      registerAgentReminder(action.client_action);
    }
  });
}

async function checkAgentReminders() {
  const reminders = readAgentReminders();
  if (!reminders.length) return;
  const now = Date.now();
  const due = reminders.filter((item) => Number(item.timestamp) <= now);
  const pending = reminders.filter((item) => Number(item.timestamp) > now);
  if (!due.length) return;
  saveAgentReminders(pending);
  for (const item of due) {
    const text = `提醒：${item.content}`;
    if ('Notification' in window && Notification.permission === 'granted') {
      try {
        new Notification('个人工作台提醒', { body: item.content });
      } catch (_) {
        toast(text, 'warning');
      }
    } else {
      toast(text, 'warning');
    }
  }
}

function startAgentReminderLoop() {
  if (agentReminderTimer) return;
  void checkAgentReminders();
  agentReminderTimer = setInterval(checkAgentReminders, 30000);
}

async function summarizeOldAgentHistory() {
  if (agentMemory.messages.length <= AGENT_MAX_MESSAGES) return;
  const overflow = agentMemory.messages.slice(0, agentMemory.messages.length - AGENT_MAX_MESSAGES);
  try {
    const body = await fetchJson('/api/agent/summarize', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({history: overflow, summary: agentMemory.summary}),
    });
    agentMemory.summary = String(body.data?.summary || '').slice(0, AGENT_MAX_SUMMARY);
    agentMemory.messages = agentMemory.messages.slice(-AGENT_MAX_MESSAGES);
    saveAgentMemory();
    renderAgentHistory();
    updateAgentSummaryState();
  } catch (err) {
    toast(`历史摘要暂未更新：${err.message || '请稍后重试'}`, 'warning');
  }
}

function clearAgentHistory() {
  if (!agentMemory.messages.length && !agentMemory.summary) return;
  if (!confirm('确定清空全部对话历史和摘要吗？此操作不可恢复。')) return;
  agentMemory = {summary: '', messages: []};
  saveAgentMemory();
  renderAgentHistory();
  updateAgentSummaryState();
  toast('已开始新对话', 'success');
}

function initAgentMemory() {
  readAgentMemory();
  renderAgentHistory();
  updateAgentSummaryState();
  startAgentReminderLoop();
}

async function sendAgentMessage(rawText) {
  const text = (rawText || '').trim();
  const input = $('#agent-message-input');
  const sendBtn = $('#btn-agent-send');
  if (!text) {
    toast('请输入消息', 'warning');
    return;
  }

  const requestHistory = agentMemory.messages.slice(-AGENT_MAX_MESSAGES);
  input.value = '';
  if (/提醒|remind/i.test(text)) {
    void requestAgentNotificationPermission();
  }
  const userMessage = appendUserMessage(text);
  agentMemory.messages.push(userMessage);
  saveAgentMemory();
  const typing = appendTypingMessage();
  sendBtn.disabled = true;
  scrollAgentLog();

  try {
    const res = await fetchJson('/api/agent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message: text,
        history: requestHistory,
        history_summary: agentMemory.summary,
      }),
    });
    const assistantMessage = renderAgentAnswer(res.data || {}, typing);
    agentMemory.messages.push(assistantMessage);
    saveAgentMemory();
    applyClientActions(assistantMessage.actions);
    await summarizeOldAgentHistory();
  } catch (err) {
    typing.remove();
    appendAgentError(err.message || '发送失败');
  } finally {
    sendBtn.disabled = false;
    input.focus();
    scrollAgentLog();
  }
}

function bindQaPage() {
  const input = $('#agent-message-input');
  $('#btn-agent-send').addEventListener('click', () => sendAgentMessage(input.value));
  $('#btn-agent-new-chat').addEventListener('click', clearAgentHistory);

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendAgentMessage(input.value);
    }
  });

  $$('.agent-quick-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const text = btn.textContent.trim() === '添加RSS源'
        ? '我要添加 RSS 源'
        : btn.textContent.trim();
      sendAgentMessage(text);
    });
  });

  $('#btn-vector-sync').addEventListener('click', () => {
    syncVectorIndex().catch((err) => toast(err.message || '同步启动失败', 'error'));
  });

  initAgentMemory();
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

/* ============ 日常/工作任务与关联选择 ============ */
function renderLinkChips(container, selection, onRemove) {
  container.innerHTML = "";
  Array.from(selection.values()).forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "link-chip";
    chip.textContent = item.title || `#${item.id}`;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "link-chip-close";
    close.textContent = "×";
    close.title = "取消关联";
    close.addEventListener("click", (event) => {
      event.stopPropagation();
      onRemove(item.id);
    });
    chip.appendChild(close);
    container.appendChild(chip);
  });
}

function renderTaskNoteChips() {
  renderLinkChips($("#task-note-chips"), taskNoteSelection, (id) => {
    taskNoteSelection.delete(id);
    renderTaskNoteChips();
  });
}

function renderNoteTaskChips() {
  renderLinkChips($("#note-task-chips"), noteTaskSelection, (id) => {
    noteTaskSelection.delete(id);
    renderNoteTaskChips();
  });
}

function hideLinkResults(box) {
  box.hidden = true;
  box.innerHTML = "";
}

function renderTaskNoteResults(items) {
  const box = $("#task-note-results");
  box.innerHTML = "";
  if (taskNoteSelection.size >= 3) {
    box.textContent = "最多关联 3 篇笔记";
    box.hidden = false;
    return;
  }
  const candidates = items.filter((note) => !taskNoteSelection.has(note.id));
  if (!candidates.length) {
    box.textContent = "没有匹配的笔记";
    box.hidden = false;
    return;
  }
  candidates.forEach((note) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-result-item";
    button.innerHTML = `<strong>${escapeHtml(note.title || `笔记 ${note.id}`)}</strong><span>${escapeHtml(note.category || "默认")}</span>`;
    button.addEventListener("click", () => {
      taskNoteSelection.set(note.id, note);
      renderTaskNoteChips();
      $("#task-note-search").value = "";
      hideLinkResults(box);
    });
    box.appendChild(button);
  });
  box.hidden = false;
}

async function searchTaskNotes() {
  const keyword = $("#task-note-search").value.trim();
  const box = $("#task-note-results");
  if (!keyword) {
    hideLinkResults(box);
    return;
  }
  try {
    const body = await fetchJson(`/api/notes?keyword=${encodeURIComponent(keyword)}&size=10`);
    renderTaskNoteResults(body.data.items || []);
  } catch (err) {
    box.textContent = err.message || "笔记搜索失败";
    box.hidden = false;
  }
}

function renderNoteTaskResults(items) {
  const box = $("#note-task-results");
  box.innerHTML = "";
  if (noteTaskSelection.size >= 3) {
    box.textContent = "最多关联 3 个任务";
    box.hidden = false;
    return;
  }
  const candidates = items.filter((task) => !noteTaskSelection.has(task.id));
  if (!candidates.length) {
    box.textContent = "没有匹配的未完成任务";
    box.hidden = false;
    return;
  }
  candidates.forEach((task) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-result-item";
    button.innerHTML = `<strong>${escapeHtml(task.title || `任务 ${task.id}`)}</strong><span>${escapeHtml(task.category || "日常")}</span>`;
    button.addEventListener("click", () => {
      noteTaskSelection.set(task.id, task);
      renderNoteTaskChips();
      $("#note-task-search").value = "";
      hideLinkResults(box);
    });
    box.appendChild(button);
  });
  box.hidden = false;
}

async function searchNoteTasks() {
  const keyword = $("#note-task-search").value.trim();
  const box = $("#note-task-results");
  if (!keyword) {
    hideLinkResults(box);
    return;
  }
  try {
    const body = await fetchJson(`/api/tasks?completed=false&keyword=${encodeURIComponent(keyword)}`);
    renderNoteTaskResults(body.data || []);
  } catch (err) {
    box.textContent = err.message || "任务搜索失败";
    box.hidden = false;
  }
}

function bindLinkPickers() {
  $("#task-note-search").addEventListener("input", () => {
    clearTimeout(taskNoteSearchTimer);
    taskNoteSearchTimer = setTimeout(searchTaskNotes, 300);
  });
  $("#note-task-search").addEventListener("input", () => {
    clearTimeout(noteTaskSearchTimer);
    noteTaskSearchTimer = setTimeout(searchNoteTasks, 300);
  });
  renderTaskNoteChips();
  renderNoteTaskChips();
}

function resetTaskForm() {
  editingTaskId = null;
  taskNoteSelection.clear();
  $("#task-modal-title").textContent = "新增任务";
  $("#task-form-title").value = "";
  $("#task-form-category").value = "日常";
  $("#task-form-priority").value = "中";
  $("#task-form-due-date").value = "";
  $("#task-note-search").value = "";
  hideLinkResults($("#task-note-results"));
  renderTaskNoteChips();
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
    (task.note_summaries || []).forEach((note) => taskNoteSelection.set(note.id, note));
    renderTaskNoteChips();
  }
  $("#task-modal").hidden = false;
  $("#task-form-title").focus();
}

function closeTaskModal() {
  $("#task-modal").hidden = true;
  editingTaskId = null;
  hideLinkResults($("#task-note-results"));
}

async function saveTaskForm() {
  const payload = {
    title: $("#task-form-title").value.trim(),
    category: $("#task-form-category").value,
    priority: $("#task-form-priority").value,
    due_date: $("#task-form-due-date").value || null,
    note_ids: Array.from(taskNoteSelection.keys()),
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
    currentUserTasks = body.data || [];
    renderUserTasks(currentUserTasks);
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
  card.dataset.taskId = task.id;
  card.className = `card glass user-task-card ${task.completed ? "is-completed" : ""} ${task.is_overdue ? "is-overdue" : ""}`;

  if (!task.completed) {
    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "task-drag-handle";
    handle.textContent = "⋮⋮";
    handle.title = "拖拽调整顺序";
    handle.draggable = true;
    handle.addEventListener("dragstart", (event) => {
      draggedTaskId = task.id;
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(task.id));
    });
    handle.addEventListener("dragend", () => {
      draggedTaskId = null;
      card.classList.remove("is-dragging");
      $$(".user-task-card").forEach((item) => {
        item.classList.remove("drag-over-before", "drag-over-after");
      });
    });
    card.appendChild(handle);
  }

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
  `;
  if (task.note_count > 0) {
    const noteCount = document.createElement("button");
    noteCount.type = "button";
    noteCount.className = "todo-note task-note-count";
    noteCount.textContent = `📎 ${task.note_count}篇`;
    noteCount.title = "展开关联笔记";
    const noteList = document.createElement("div");
    noteList.className = "task-note-list";
    noteList.hidden = true;
    (task.note_summaries || []).forEach((note) => {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "task-note-link";
      link.textContent = note.title || `笔记 ${note.id}`;
      link.addEventListener("click", async (event) => {
        event.stopPropagation();
        await goToNote(note.id);
      });
      noteList.appendChild(link);
    });
    noteCount.addEventListener("click", (event) => {
      event.stopPropagation();
      noteList.hidden = !noteList.hidden;
    });
    meta.appendChild(noteCount);
    main.appendChild(meta);
    main.appendChild(noteList);
  } else {
    main.appendChild(meta);
  }
  card.appendChild(main);

  const actions = document.createElement("div");
  actions.className = "user-task-actions";
  if (!task.completed) {
    const completeNoteBtn = makeButton("icon-btn", "📝");
    completeNoteBtn.title = "完成并生成笔记";
    completeNoteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      openCompleteTaskNoteModal(task);
    });
    actions.appendChild(completeNoteBtn);
  }
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

  if (!task.completed) {
    card.addEventListener("dragover", (event) => {
      if (!draggedTaskId || draggedTaskId === task.id) return;
      event.preventDefault();
      const before = event.clientY < card.getBoundingClientRect().top + card.offsetHeight / 2;
      card.classList.toggle("drag-over-before", before);
      card.classList.toggle("drag-over-after", !before);
    });
    card.addEventListener("dragleave", () => {
      card.classList.remove("drag-over-before", "drag-over-after");
    });
    card.addEventListener("drop", async (event) => {
      event.preventDefault();
      if (!draggedTaskId || draggedTaskId === task.id) return;
      const before = event.clientY < card.getBoundingClientRect().top + card.offsetHeight / 2;
      const draggedId = draggedTaskId;
      draggedTaskId = null;
      await dropTaskAt(draggedId, task.id, before);
    });
  }

  return card;
}

async function dropTaskAt(draggedId, targetId, before) {
  const list = $("#user-task-list");
  const dragged = list.querySelector(`.user-task-card[data-task-id="${draggedId}"]`);
  const target = list.querySelector(`.user-task-card[data-task-id="${targetId}"]`);
  if (!dragged || !target) return;
  const previous = [...currentUserTasks];
  list.insertBefore(dragged, before ? target : target.nextSibling);
  const orderedIds = Array.from(list.querySelectorAll(".user-task-card:not(.is-completed)"))
    .map((card) => Number(card.dataset.taskId));
  try {
    await fetchJson("/api/tasks/reorder", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ordered_ids: orderedIds}),
    });
    await loadUserTasks();
  } catch (err) {
    renderUserTasks(previous);
    toast(err.message || "任务排序保存失败", "error");
  }
}

function openCompleteTaskNoteModal(task) {
  completingTaskId = task.id;
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  const timeText = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
  $("#task-complete-note-task").textContent = `任务：${task.title}`;
  $("#task-complete-note-title").value = task.title || "";
  $("#task-complete-note-content").value = `完成了任务「${task.title}」，完成时间：${timeText}`;
  $("#task-complete-note-category").value = task.category || "日常";
  $("#task-complete-note-error").hidden = true;
  $("#task-complete-note-modal").hidden = false;
  $("#task-complete-note-title").focus();
}

function closeCompleteTaskNoteModal() {
  completingTaskId = null;
  $("#task-complete-note-modal").hidden = true;
}

async function saveCompleteTaskNote() {
  if (!completingTaskId) return;
  const errorBox = $("#task-complete-note-error");
  const payload = {
    title: $("#task-complete-note-title").value.trim(),
    content: $("#task-complete-note-content").value.trim(),
    category: $("#task-complete-note-category").value,
  };
  errorBox.hidden = true;
  if (!payload.title || !payload.content) {
    errorBox.textContent = "请填写笔记标题和内容";
    errorBox.hidden = false;
    return;
  }
  const button = $("#btn-save-complete-note");
  button.disabled = true;
  try {
    const body = await fetchJson(`/api/tasks/${completingTaskId}/complete-with-note`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    closeCompleteTaskNoteModal();
    toast(body.message, "success");
    await loadUserTasks();
    loadStats();
    loadHomeTaskPanels();
  } catch (err) {
    errorBox.textContent = err.message || "保存失败";
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
  }
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

/* 看板左栏：今日待办，按优先级排序，最多显示3条 */
function renderDashTodayTasks(box, tasks, emptyText) {
  box.innerHTML = "";
  if (!tasks.length) {
    const empty = document.createElement("div");
    empty.className = "simple-task-empty";
    empty.textContent = emptyText;
    box.appendChild(empty);
    return;
  }

  const priOrder = {"高": 0, "中": 1, "低": 2};
  const sorted = [...tasks].sort((a, b) =>
    (priOrder[a.priority] ?? 3) - (priOrder[b.priority] ?? 3));
  sorted.slice(0, 3).forEach((task) => {
    const row = document.createElement("div");
    row.className = `simple-task-row ${task.is_overdue ? "overdue" : ""}`;
    const check = document.createElement("input");
    check.type = "checkbox";
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
      <span class="simple-task-title dash-jump">${escapeHtml(task.title)}</span>
      <span class="simple-task-meta">
        <span class="todo-priority priority-${escapeHtml(task.priority)}">${escapeHtml(task.priority)}</span>
        <span class="${task.is_overdue ? "overdue" : ""}">${task.due_date ? escapeHtml(task.due_date) : "无截止"}</span>
        ${task.note_count > 0 ? '<span class="todo-note">📎</span>' : ""}
      </span>`;
    info.querySelector(".simple-task-title").addEventListener("click", () => {
      document.querySelector('.nav-btn[data-tab="tasks"]').click();
    });
    row.appendChild(check);
    row.appendChild(info);
    box.appendChild(row);
  });

  if (sorted.length > 3) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "dash-view-all";
    more.textContent = "查看全部";
    more.addEventListener("click", () => document.querySelector('.nav-btn[data-tab="tasks"]').click());
    box.appendChild(more);
  }
}

async function loadHomeTaskPanels() {
  try {
    const todayBody = await fetchJson("/api/tasks/today");
    renderDashTodayTasks($("#dash-today-tasks"), todayBody.data || [], "今天没有待办");
  } catch (_) { /* 首页任务加载失败不打断主要内容 */ }
}

async function openTaskFromNoteId(taskId) {
  document.querySelector('.nav-btn[data-tab="tasks"]').click();
  try {
    const body = await fetchJson(`/api/tasks/${taskId}`);
    openTaskModal(body.data);
  } catch (err) {
    toast(err.message || "任务加载失败", "error");
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
  $("#task-complete-note-close").addEventListener("click", closeCompleteTaskNoteModal);
  $("#btn-cancel-complete-note").addEventListener("click", closeCompleteTaskNoteModal);
  $("#btn-save-complete-note").addEventListener("click", saveCompleteTaskNote);
  $("#task-complete-note-modal").addEventListener("click", (event) => {
    if (event.target === $("#task-complete-note-modal")) closeCompleteTaskNoteModal();
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


/* ============ v2.6 日程与月视图日历 ============ */
let calendarState = {
  year: new Date().getFullYear(),
  month: new Date().getMonth() + 1,
};
let calendarRows = new Map();
let scheduleDetailData = null;
let editingScheduleId = null;

const SCHEDULE_TYPE_COLORS = {
  "日常": "#06b6d4",
  "旅行": "#8b5cf6",
  "工作": "#3b82f6",
  "其他": "#64748b",
};

function padNumber(value) {
  return String(value).padStart(2, "0");
}

function localDateKey(date) {
  return `${date.getFullYear()}-${padNumber(date.getMonth() + 1)}-${padNumber(date.getDate())}`;
}

function startOfDate(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, days) {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

function parseDateKey(key) {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatChineseDate(date) {
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function toDateTimeLocalValue(date) {
  return `${localDateKey(date)}T${padNumber(date.getHours())}:${padNumber(date.getMinutes())}`;
}

function calendarGridStart(year, month) {
  const firstDay = new Date(year, month - 1, 1);
  const offset = (firstDay.getDay() + 6) % 7;
  return addDays(firstDay, -offset);
}

function buildScheduleMap(schedules, gridStart, gridEnd) {
  const map = new Map();
  for (let i = 0; i < 42; i += 1) {
    map.set(localDateKey(addDays(gridStart, i)), []);
  }

  schedules.forEach((schedule) => {
    const start = new Date(schedule.start_time);
    let end = schedule.end_time ? new Date(schedule.end_time) : start;
    if (Number.isNaN(start.getTime())) return;
    if (Number.isNaN(end.getTime()) || end < start) end = start;

    let cursor = startOfDate(new Date(Math.max(startOfDate(start), startOfDate(gridStart))));
    const lastDay = startOfDate(new Date(Math.min(startOfDate(end), startOfDate(gridEnd))));

    while (cursor <= lastDay) {
      const key = localDateKey(cursor);
      if (map.has(key)) map.get(key).push(schedule);
      cursor = addDays(cursor, 1);
    }
  });

  map.forEach((items) => {
    items.sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)));
  });
  return map;
}

async function loadCalendarMonth() {
  const gridStart = calendarGridStart(calendarState.year, calendarState.month);
  const gridEnd = addDays(gridStart, 41);
  const params = new URLSearchParams({
    start_date: localDateKey(gridStart),
    end_date: localDateKey(gridEnd),
  });

  const body = await fetchJson(`/api/schedules?${params}`);
  calendarRows = buildScheduleMap(body.data || [], gridStart, gridEnd);
  renderCalendar(gridStart);
}

function renderCalendar(gridStart) {
  $("#calendar-month-title").textContent = `${calendarState.year}年${calendarState.month}月`;
  const grid = $("#calendar-grid");
  grid.innerHTML = "";

  const todayKey = localDateKey(new Date());

  for (let index = 0; index < 42; index += 1) {
    const day = addDays(gridStart, index);
    const key = localDateKey(day);
    const items = calendarRows.get(key) || [];

    const cell = document.createElement("div");
    cell.className = "calendar-day";
    if (day.getMonth() + 1 !== calendarState.month) cell.classList.add("outside-month");
    if (key === todayKey) cell.classList.add("is-today");

    const number = document.createElement("span");
    number.className = "calendar-day-number";
    number.textContent = day.getDate();
    cell.appendChild(number);

    const scheduleBox = document.createElement("div");
    scheduleBox.className = "calendar-day-schedules";
    items.slice(0, 3).forEach((schedule) => {
      scheduleBox.appendChild(buildMiniSchedule(schedule));
    });

    if (items.length > 3) {
      const more = document.createElement("span");
      more.className = "calendar-more";
      more.textContent = `+${items.length - 3}`;
      scheduleBox.appendChild(more);
    }

    cell.appendChild(scheduleBox);
    cell.addEventListener("click", () => openScheduleDay(day, items));
    grid.appendChild(cell);
  }
}

function buildMiniSchedule(schedule) {
  const item = document.createElement("button");
  item.type = "button";
  item.className = "calendar-schedule-item";
  item.style.setProperty("--schedule-color", schedule.effective_color || "#5b8def");

  const dot = document.createElement("span");
  dot.className = "schedule-dot";
  const title = document.createElement("span");
  title.className = "calendar-schedule-title";
  title.textContent = schedule.title;

  item.appendChild(dot);
  item.appendChild(title);
  item.addEventListener("click", (event) => {
    event.stopPropagation();
    openScheduleDetail(schedule);
  });
  return item;
}

function buildScheduleRow(schedule, showDate = false) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "schedule-list-row";
  row.style.setProperty("--schedule-color", schedule.effective_color || "#5b8def");
  const time = schedule.start_time ? schedule.start_time.slice(11, 16) : "--:--";
  const prefix = showDate ? `${schedule.start_time?.slice(5, 10) || ""} ${time}` : time;
  row.innerHTML = `
    <span class="schedule-row-time">${escapeHtml(prefix)}</span>
    <span class="schedule-row-title">${escapeHtml(schedule.title)}</span>
    <span class="schedule-row-type">${escapeHtml(schedule.schedule_type)}</span>
  `;
  row.addEventListener("click", () => openScheduleDetail(schedule));
  return row;
}

function openScheduleDay(day, items) {
  $("#schedule-day-title").textContent = formatChineseDate(day);
  const list = $("#schedule-day-list");
  list.innerHTML = "";

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "schedule-day-empty";
    empty.textContent = "这一天暂无日程";
    list.appendChild(empty);
  } else {
    items.forEach((schedule) => list.appendChild(buildScheduleRow(schedule)));
  }

  $("#schedule-day-add").onclick = () => {
    closeScheduleDayModal();
    openScheduleForm(day);
  };
  $("#schedule-day-modal").hidden = false;
}

function appendDetailLine(label, value) {
  const row = document.createElement("div");
  row.className = "schedule-detail-line";
  const labelEl = document.createElement("span");
  labelEl.className = "schedule-detail-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "schedule-detail-value";
  valueEl.textContent = value || "无";
  row.appendChild(labelEl);
  row.appendChild(valueEl);
  $("#schedule-detail-body").appendChild(row);
  return valueEl;
}

function openScheduleDetail(schedule) {
  scheduleDetailData = schedule;
  $("#schedule-detail-title").textContent = schedule.title;
  $("#schedule-detail-color").style.background = schedule.effective_color || "#5b8def";

  const body = $("#schedule-detail-body");
  body.innerHTML = "";
  appendDetailLine("类型", schedule.schedule_type);
  appendDetailLine("开始", schedule.start_time?.replace("T", " "));
  appendDetailLine("结束", schedule.end_time ? schedule.end_time.replace("T", " ") : "无");
  appendDetailLine("地点", schedule.location);
  appendDetailLine("关联笔记", schedule.note_title);

  const descRow = document.createElement("div");
  descRow.className = "schedule-detail-line schedule-description-row";
  const descLabel = document.createElement("span");
  descLabel.className = "schedule-detail-label";
  descLabel.textContent = "行程详情";
  const descValue = document.createElement("div");
  descValue.className = "schedule-detail-value schedule-description";
  descValue.textContent = schedule.description || "无";
  descRow.appendChild(descLabel);
  descRow.appendChild(descValue);
  body.appendChild(descRow);

  $("#schedule-detail-modal").hidden = false;
}

async function loadScheduleNoteOptions() {
  const select = $("#schedule-form-note");
  const current = select.value;
  try {
    const body = await fetchJson("/api/notes?size=500");
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

function resetScheduleForm(day = null) {
  editingScheduleId = null;
  const base = day ? new Date(day) : new Date();
  if (day) {
    base.setHours(9, 0, 0, 0);
  } else {
    base.setMinutes(0, 0, 0);
    base.setHours(base.getHours() + 1);
  }

  $("#schedule-form-modal-title").textContent = "新增日程";
  $("#schedule-form-title").value = "";
  $("#schedule-form-type").value = "日常";
  $("#schedule-form-start").value = toDateTimeLocalValue(base);
  $("#schedule-form-end").value = "";
  $("#schedule-form-location").value = "";
  $("#schedule-form-description").value = "";
  $("#schedule-form-note").value = "";
  $("#schedule-form-color").value = SCHEDULE_TYPE_COLORS["日常"];
  $("#schedule-form-error").hidden = true;
}

function openScheduleForm(day = null) {
  resetScheduleForm(day);
  $("#schedule-form-modal").hidden = false;
  $("#schedule-form-title").focus();
}

function editCurrentSchedule() {
  if (!scheduleDetailData) return;
  const schedule = scheduleDetailData;
  resetScheduleForm(null);
  editingScheduleId = schedule.id;
  $("#schedule-form-modal-title").textContent = "编辑日程";
  $("#schedule-form-title").value = schedule.title || "";
  $("#schedule-form-type").value = schedule.schedule_type;
  $("#schedule-form-start").value = schedule.start_time || "";
  $("#schedule-form-end").value = schedule.end_time || "";
  $("#schedule-form-location").value = schedule.location || "";
  $("#schedule-form-description").value = schedule.description || "";
  $("#schedule-form-note").value = schedule.note_id ? String(schedule.note_id) : "";
  $("#schedule-form-color").value = schedule.effective_color || SCHEDULE_TYPE_COLORS[schedule.schedule_type];
  closeScheduleDetailModal();
  $("#schedule-form-modal").hidden = false;
}

async function saveScheduleForm() {
  const errorBox = $("#schedule-form-error");
  errorBox.hidden = true;

  const payload = {
    title: $("#schedule-form-title").value.trim(),
    schedule_type: $("#schedule-form-type").value,
    start_time: $("#schedule-form-start").value,
    end_time: $("#schedule-form-end").value || null,
    location: $("#schedule-form-location").value.trim(),
    description: $("#schedule-form-description").value.trim(),
    note_id: $("#schedule-form-note").value ? Number($("#schedule-form-note").value) : null,
    color: $("#schedule-form-color").value,
  };

  if (!payload.title) {
    errorBox.textContent = "日程标题不能为空";
    errorBox.hidden = false;
    return;
  }
  if (!payload.start_time) {
    errorBox.textContent = "开始时间不能为空";
    errorBox.hidden = false;
    return;
  }
  if (payload.end_time && payload.end_time <= payload.start_time) {
    errorBox.textContent = "结束时间必须晚于开始时间";
    errorBox.hidden = false;
    return;
  }

  const url = editingScheduleId ? `/api/schedules/${editingScheduleId}` : "/api/schedules";
  const method = editingScheduleId ? "PUT" : "POST";

  try {
    const body = await fetchJson(url, {
      method,
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    toast(body.message, "success");
    const start = new Date(body.data.start_time);
    calendarState = {year: start.getFullYear(), month: start.getMonth() + 1};
    closeScheduleForm();
    await loadCalendarMonth();
    loadHomeSchedules();
    loadStats();
  } catch (err) {
    errorBox.textContent = err.message || "日程保存失败";
    errorBox.hidden = false;
  }
}

async function deleteCurrentSchedule() {
  if (!scheduleDetailData) return;
  const schedule = scheduleDetailData;
  if (!confirm(`确定删除日程“${schedule.title}”吗？`)) return;

  await fetchJson(`/api/schedules/${schedule.id}`, {method: "DELETE"});
  closeScheduleDetailModal();
  toast("日程已删除", "success");
  await loadCalendarMonth();
  loadHomeSchedules();
  loadStats();
}

function activateCalendarTab() {
  $$(".nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === "calendar");
  });
  $$(".tab").forEach((section) => {
    section.classList.toggle("active", section.dataset.tab === "calendar");
  });
}

async function openScheduleFromAnywhere(schedule) {
  const start = new Date(schedule.start_time);
  calendarState = {year: start.getFullYear(), month: start.getMonth() + 1};
  activateCalendarTab();
  await loadCalendarMonth();
  openScheduleDetail(schedule);
}

/* 看板右栏：今日日程，按时间排序，点击跳转日历页 */
async function loadHomeSchedules() {
  const box = $("#dash-today-schedules");
  if (!box) return;
  box.innerHTML = "";
  try {
    const body = await fetchJson("/api/schedules/today");
    const schedules = body.data || [];
    if (!schedules.length) {
      const empty = document.createElement("div");
      empty.className = "home-schedule-empty";
      empty.textContent = "今天暂无日程";
      box.appendChild(empty);
      return;
    }
    schedules.forEach((schedule) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "home-schedule-row";
      row.style.setProperty("--schedule-color", schedule.effective_color || "#5b8def");
      const locText = schedule.location ? ` · ${escapeHtml(schedule.location)}` : "";
      row.innerHTML = `
        <span class="home-schedule-time">${escapeHtml(schedule.start_time?.slice(11, 16) || "--:--")}</span>
        <span class="home-schedule-title">${escapeHtml(schedule.title)}${locText}</span>`;
      row.addEventListener("click", () => document.querySelector('.nav-btn[data-tab="calendar"]').click());
      box.appendChild(row);
    });
  } catch (_) {
    const empty = document.createElement("div");
    empty.className = "home-schedule-empty";
    empty.textContent = "今日日程加载失败";
    box.appendChild(empty);
  }
}

function closeScheduleDayModal() {
  $("#schedule-day-modal").hidden = true;
}

function closeScheduleDetailModal() {
  $("#schedule-detail-modal").hidden = true;
  scheduleDetailData = null;
}

function closeScheduleForm() {
  $("#schedule-form-modal").hidden = true;
  editingScheduleId = null;
}

function shiftCalendarMonth(delta) {
  const date = new Date(calendarState.year, calendarState.month - 1 + delta, 1);
  calendarState = {year: date.getFullYear(), month: date.getMonth() + 1};
  loadCalendarMonth();
}

function bindCalendarPage() {
  $("#btn-prev-month").addEventListener("click", () => shiftCalendarMonth(-1));
  $("#btn-next-month").addEventListener("click", () => shiftCalendarMonth(1));
  $("#btn-add-schedule").addEventListener("click", () => openScheduleForm());
  $("#schedule-day-close").addEventListener("click", closeScheduleDayModal);
  $("#schedule-detail-close").addEventListener("click", closeScheduleDetailModal);
  $("#schedule-form-close").addEventListener("click", closeScheduleForm);
  $("#schedule-form-cancel").addEventListener("click", closeScheduleForm);
  $("#btn-save-schedule").addEventListener("click", saveScheduleForm);
  $("#btn-edit-schedule").addEventListener("click", editCurrentSchedule);
  $("#btn-delete-schedule").addEventListener("click", deleteCurrentSchedule);

  $("#schedule-form-type").addEventListener("change", (event) => {
    $("#schedule-form-color").value = SCHEDULE_TYPE_COLORS[event.target.value] || "#5b8def";
  });

  $("#schedule-day-modal").addEventListener("click", (event) => {
    if (event.target.id === "schedule-day-modal") closeScheduleDayModal();
  });
  $("#schedule-detail-modal").addEventListener("click", (event) => {
    if (event.target.id === "schedule-detail-modal") closeScheduleDetailModal();
  });
  $("#schedule-form-modal").addEventListener("click", (event) => {
    if (event.target.id === "schedule-form-modal") closeScheduleForm();
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

/* ============ v2.7 会议记录弹窗 ============ */
let meetingOrganizedData = null;
let stopMeetingPolling = null;

function setMeetingPane(tab) {
  $$("#meeting-tabs .segmented-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.meetingTab === tab);
  });
  $$("[data-meeting-pane]").forEach((pane) => {
    pane.hidden = pane.dataset.meetingPane !== tab;
  });
}

function resetMeetingModal() {
  meetingOrganizedData = null;
  if (stopMeetingPolling) { stopMeetingPolling(); stopMeetingPolling = null; }
  $("#meeting-raw-text").value = "";
  $("#meeting-organized").hidden = true;
  $("#meeting-todo-pick").hidden = true;
  $("#meeting-save-organized-row").hidden = true;
  $("#btn-organize-meeting").disabled = false;
  $("#btn-organize-meeting").textContent = "AI 整理";
  $("#meeting-manual-title").value = "";
  $("#meeting-manual-time").value = "";
  $("#meeting-manual-attendees").value = "";
  $("#meeting-manual-topic").value = "";
  $("#meeting-manual-content").value = "";
  $("#meeting-manual-error").hidden = true;
  setMeetingPane("ai");
}

function openMeetingModal() {
  resetMeetingModal();
  $("#meeting-modal").hidden = false;
}

function renderMeetingOrganized(data) {
  meetingOrganizedData = data;
  const box = $("#meeting-organized");
  const todos = data.todos || [];
  box.innerHTML = `
    <div class="org-line"><b>主题：</b>${escapeHtml(data.topic || "未识别")}</div>
    <div class="org-line"><b>时间：</b>${escapeHtml(data.meeting_time || "未记录")}</div>
    <div class="org-line"><b>参会人：</b>${escapeHtml(data.attendees || "未记录")}</div>
    <div class="org-block"><b>议题讨论</b><div>${escapeHtml(data.discussion || "无")}</div></div>
    <div class="org-block"><b>决议事项</b>
      <ul>${(data.decisions || []).map((d) => `<li>${escapeHtml(d)}</li>`).join("") || "<li>无</li>"}</ul>
    </div>`;
  box.hidden = false;

  const pick = $("#meeting-todo-pick");
  const list = $("#meeting-todo-list");
  list.innerHTML = "";
  if (todos.length) {
    todos.forEach((todo, index) => {
      const label = document.createElement("label");
      label.className = "meeting-todo-item";
      label.innerHTML = `
        <input type="checkbox" data-todo-index="${index}" />
        <span>${escapeHtml(todo.content)}</span>
        <span class="todo-assignee">${escapeHtml(todo.assignee ? "负责人：" + todo.assignee : "")}</span>`;
      list.appendChild(label);
    });
    pick.hidden = false;
    $("#meeting-todo-all").checked = false;
  } else {
    pick.hidden = true;
  }
  $("#meeting-save-organized-row").hidden = false;
}

function selectedMeetingTodos() {
  return $$("#meeting-todo-list input[type=checkbox]:checked").map((input) => Number(input.dataset.todoIndex));
}

async function organizeCurrentMeeting() {
  const rawText = $("#meeting-raw-text").value.trim();
  const btn = $("#btn-organize-meeting");
  if (!rawText) { toast("请粘贴会议记录内容", "error"); return; }
  if (stopMeetingPolling) { stopMeetingPolling(); stopMeetingPolling = null; }
  btn.disabled = true;
  btn.textContent = "正在创建任务…";
  $("#meeting-organized").hidden = true;
  try {
    const res = await fetchJson("/api/notes/meeting/organize", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({raw_text: rawText}),
    });
    stopMeetingPolling = startTaskPolling(res.data.task_id, {
      onProgress: (task) => { btn.textContent = task.progress_message || "正在整理…"; },
      onSuccess: (task) => {
        btn.disabled = false;
        btn.textContent = "AI 整理";
        renderMeetingOrganized(task.result);
        toast("会议整理完成", "success");
      },
      onFailed: (task) => {
        btn.disabled = false;
        btn.textContent = "AI 整理";
        toast(task.error || "整理失败", "error");
      },
      onError: () => {
        btn.disabled = false;
        btn.textContent = "AI 整理";
      },
    });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "AI 整理";
    toast(err.message || "整理任务创建失败", "error");
  }
}

async function saveOrganizedMeeting() {
  if (!meetingOrganizedData) return;
  const btn = $("#btn-save-organized");
  btn.disabled = true;
  try {
    const res = await fetchJson("/api/notes", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        organized: meetingOrganizedData,
        selected_todos: selectedMeetingTodos(),
      }),
    });
    $("#meeting-modal").hidden = true;
    toast(res.message, "success");
    loadNotes();
    loadStats();
  } catch (err) {
    toast(err.message || "保存失败", "error");
  } finally {
    btn.disabled = false;
  }
}

async function saveManualMeeting() {
  const errorBox = $("#meeting-manual-error");
  errorBox.hidden = true;
  const payload = {
    title: $("#meeting-manual-title").value.trim(),
    meeting_time: $("#meeting-manual-time").value || null,
    attendees: $("#meeting-manual-attendees").value.trim(),
    topic: $("#meeting-manual-topic").value.trim(),
    content: $("#meeting-manual-content").value.trim(),
  };
  if (!payload.content) {
    errorBox.textContent = "请填写会议内容";
    errorBox.hidden = false;
    return;
  }
  const btn = $("#btn-save-manual-meeting");
  btn.disabled = true;
  try {
    const res = await fetchJson("/api/notes/meeting", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    $("#meeting-modal").hidden = true;
    toast(res.message, "success");
    loadNotes();
    loadStats();
  } catch (err) {
    errorBox.textContent = err.message || "保存失败";
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

function bindMeetingModal() {
  $("#btn-add-meeting").addEventListener("click", openMeetingModal);
  $("#meeting-close").addEventListener("click", () => { $("#meeting-modal").hidden = true; });
  $$("#meeting-tabs .segmented-btn").forEach((btn) => {
    btn.addEventListener("click", () => setMeetingPane(btn.dataset.meetingTab));
  });
  $("#btn-organize-meeting").addEventListener("click", organizeCurrentMeeting);
  $("#btn-save-organized").addEventListener("click", saveOrganizedMeeting);
  $("#btn-save-manual-meeting").addEventListener("click", saveManualMeeting);
  $("#meeting-todo-all").addEventListener("change", (event) => {
    $$("#meeting-todo-list input[type=checkbox]").forEach((input) => {
      input.checked = event.target.checked;
    });
  });
  $("#meeting-modal").addEventListener("click", (event) => {
    if (event.target.id === "meeting-modal") $("#meeting-modal").hidden = true;
  });
}


/* ============ v2.11 全局搜索 ============ */
const GLOBAL_SEARCH_HISTORY_KEY = "personalWorkbenchGlobalSearchHistory";
const GLOBAL_SEARCH_DEBOUNCE_MS = 300;
let globalSearchTimer = null;
let globalSearchLastQuery = "";

function readSearchHistory() {
  try {
    const data = JSON.parse(localStorage.getItem(GLOBAL_SEARCH_HISTORY_KEY) || "[]");
    return Array.isArray(data) ? data.map(String).slice(0, 10) : [];
  } catch (_) {
    return [];
  }
}

function writeSearchHistory(items) {
  localStorage.setItem(GLOBAL_SEARCH_HISTORY_KEY, JSON.stringify(items.slice(0, 10)));
}

function addSearchHistory(keyword) {
  keyword = keyword.trim();
  if (!keyword) return;
  const items = readSearchHistory().filter((item) => item !== keyword);
  items.unshift(keyword);
  writeSearchHistory(items);
}

function clearSearchHistory() {
  localStorage.removeItem(GLOBAL_SEARCH_HISTORY_KEY);
  renderSearchHistory();
}

function renderSearchHistory() {
  const box = $("#global-search-history");
  const list = $("#global-search-history-list");
  if (!box || !list) return;
  const items = readSearchHistory();
  list.innerHTML = "";
  if (!items.length || globalSearchLastQuery) {
    box.hidden = true;
    return;
  }
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "search-history-item";
    button.textContent = item;
    button.addEventListener("click", () => {
      $("#global-search-input").value = item;
      runGlobalSearch(item);
    });
    list.appendChild(button);
  });
  box.hidden = false;
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightKeywords(root, keyword) {
  const terms = keyword.split(/\s+/).map((item) => item.trim()).filter(Boolean);
  if (!terms.length || !root) return;
  const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  const testPattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "i");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !testPattern.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent || ["MARK", "INPUT", "TEXTAREA", "SELECT", "SCRIPT", "STYLE", "KBD"].includes(parent.tagName)) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const parts = node.nodeValue.split(pattern);
    const fragment = document.createDocumentFragment();
    parts.forEach((part) => {
      if (!part) return;
      if (testPattern.test(part)) {
        const mark = document.createElement("mark");
        mark.className = "search-mark";
        mark.textContent = part;
        fragment.appendChild(mark);
      } else {
        fragment.appendChild(document.createTextNode(part));
      }
    });
    node.parentNode.replaceChild(fragment, node);
  });
}

function resultIcon(type) {
  return {note: "📝", task: "✅", schedule: "📅"}[type] || "•";
}

function resultMetaText(result) {
  if (result.type === "note") return result.meta.category || "默认";
  if (result.type === "task") {
    return [result.meta.priority, result.meta.due_date?.slice(0, 10)].filter(Boolean).join(" · ");
  }
  return [result.meta.schedule_type, result.meta.location].filter(Boolean).join(" · ");
}

function buildResultItem(result, keyword) {
  const item = document.createElement("button");
  item.type = "button";
  item.className = `global-search-result search-result-${result.type}`;

  const icon = document.createElement("span");
  icon.className = "search-result-icon";
  icon.textContent = resultIcon(result.type);

  const body = document.createElement("span");
  body.className = "search-result-body";

  const title = document.createElement("span");
  title.className = "search-result-title";
  title.textContent = result.title || "无标题";

  const summary = document.createElement("span");
  summary.className = "search-result-summary";
  summary.textContent = result.summary || "暂无摘要";

  const meta = document.createElement("span");
  meta.className = "search-result-meta";
  meta.textContent = resultMetaText(result);

  body.append(title, summary);
  item.append(icon, body, meta);
  highlightKeywords(body, keyword);

  item.addEventListener("click", () => openGlobalSearchResult(result));
  return item;
}

function addResultGroup(container, title, results, keyword) {
  if (!results?.length) return;
  const head = document.createElement("div");
  head.className = "global-search-group-title";
  head.textContent = title;
  container.appendChild(head);
  results.forEach((result) => container.appendChild(buildResultItem(result, keyword)));
}

function renderGlobalResults(data, keyword) {
  const box = $("#global-search-results");
  box.innerHTML = "";
  const total = data.notes.length + data.tasks.length + data.schedules.length;
  if (!total) {
    const empty = document.createElement("div");
    empty.className = "global-search-empty";
    empty.textContent = "没有找到相关内容";
    box.appendChild(empty);
    return;
  }
  addResultGroup(box, "笔记", data.notes, keyword);
  addResultGroup(box, "任务", data.tasks, keyword);
  addResultGroup(box, "日程", data.schedules, keyword);
}

async function openGlobalSearchResult(result) {
  closeGlobalSearch();
  if (result.type === "note") {
    await goToNote(result.id);
    return;
  }
  if (result.type === "task") {
    $('.nav-btn[data-tab="tasks"]').click();
    const body = await fetchJson(`/api/tasks/${result.id}`);
    openTaskModal(body.data);
    return;
  }
  $('.nav-btn[data-tab="calendar"]').click();
  const body = await fetchJson(`/api/schedules/${result.id}`);
  await openScheduleFromAnywhere(body.data);
}

async function requestGlobalSearch(keyword) {
  const errorBox = $("#global-search-error");
  errorBox.hidden = true;
  try {
    const body = await fetchJson(`/api/search-global?q=${encodeURIComponent(keyword)}`);
    addSearchHistory(keyword);
    renderGlobalResults(body.data || {}, keyword);
    renderSearchHistory();
  } catch (err) {
    $("#global-search-results").innerHTML = "";
    errorBox.textContent = err.message || "搜索失败";
    errorBox.hidden = false;
  }
}

function runGlobalSearch(keyword) {
  keyword = (keyword || $("#global-search-input").value).trim();
  if (globalSearchLastQuery === keyword) return;
  globalSearchLastQuery = keyword;
  $("#global-search-results").innerHTML = "";
  if (!keyword) {
    $("#global-search-error").hidden = true;
    renderSearchHistory();
    return;
  }
  $("#global-search-history").hidden = true;
  requestGlobalSearch(keyword);
}

function openGlobalSearch() {
  $("#global-search-modal").hidden = false;
  $("#global-search-input").focus();
  renderSearchHistory();
}

function closeGlobalSearch() {
  if (document.activeElement === $("#global-search-input")) {
    $("#global-search-input").blur();
  }
  $("#global-search-modal").hidden = true;
  if (globalSearchTimer) clearTimeout(globalSearchTimer);
  $("#global-search-input").value = "";
  globalSearchLastQuery = "";
  $("#global-search-results").innerHTML = "";
  $("#global-search-error").hidden = true;
}

function bindGlobalSearch() {
  $("#sidebar-search-trigger").addEventListener("click", openGlobalSearch);
  $("#global-search-close").addEventListener("click", closeGlobalSearch);
  $("#global-search-clear-history").addEventListener("click", clearSearchHistory);
  $("#global-search-input").addEventListener("input", () => {
    if (globalSearchTimer) clearTimeout(globalSearchTimer);
    globalSearchTimer = setTimeout(() => runGlobalSearch(), GLOBAL_SEARCH_DEBOUNCE_MS);
  });
  $("#global-search-input").addEventListener("focus", renderSearchHistory);
  $("#global-search-modal").addEventListener("click", (event) => {
    if (event.target.id === "global-search-modal") closeGlobalSearch();
  });
}

/* ============ v2.11 快捷键 ============ */
function isEditableElement(element) {
  if (!element) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName)
    || element.isContentEditable;
}

function currentTabName() {
  return $(".tab.active")?.dataset.tab || "";
}

function closeTopModal() {
  const modals = $$(".modal-mask:not([hidden])");
  const modal = modals[modals.length - 1];
  if (!modal) return false;
  switch (modal.id) {
    case "global-search-modal": closeGlobalSearch(); break;
    case "edit-modal": closeEditModal(); break;
    case "link-modal": closeLinkModal(); break;
    case "note-batch-prompt-modal": closeBatchPrompt(); break;
    case "schedule-day-modal": closeScheduleDayModal(); break;
    case "schedule-detail-modal": closeScheduleDetailModal(); break;
    case "schedule-form-modal": closeScheduleForm(); break;
    case "task-modal": closeTaskModal(); break;
    case "task-complete-note-modal": closeCompleteTaskNoteModal(); break;
    case "upload-modal": closeUploadModal(); break;
    case "style-modal": closeStyleModal(); break;
    case "restore-confirm-modal": closeRestoreConfirm(); break;
    case "rss-modal": modal.hidden = true; break;
    case "meeting-modal": modal.hidden = true; break;
    case "pending-modal": modal.hidden = true; break;
    default: modal.hidden = true;
  }
  return true;
}

function openShortcutsHelp() {
  $("#shortcuts-help-modal").hidden = false;
}

function closeShortcutsHelp() {
  $("#shortcuts-help-modal").hidden = true;
}

function bindShortcutsHelp() {
  $("#shortcuts-help-close").addEventListener("click", closeShortcutsHelp);
  $("#shortcuts-help-modal").addEventListener("click", (event) => {
    if (event.target.id === "shortcuts-help-modal") closeShortcutsHelp();
  });
}

function initShortcuts() {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeTopModal();
      return;
    }
    if (isEditableElement(event.target)) return;
    if (!event.ctrlKey) return;

    const key = event.key.toLowerCase();
    if (key === "k") {
      event.preventDefault();
      openGlobalSearch();
    } else if (key === "n") {
      event.preventDefault();
      openCreateNoteShortcut();
    } else if (key === "/") {
      event.preventDefault();
      openShortcutsHelp();
    } else if (/^[1-8]$/.test(key)) {
      event.preventDefault();
      const index = Number(key) - 1;
      const button = $$(".sidebar .nav-btn")[index];
      button?.click();
    } else if (key === "f" && currentTabName() === "notes") {
      event.preventDefault();
      $("#notes-search").focus();
    } else if (key === "a" && currentTabName() === "notes" && batchMode) {
      event.preventDefault();
      const shouldSelectAll = selectedNoteIds.size < currentPageNotes.length;
      currentPageNotes.forEach((note) => toggleNoteSelection(note.id, shouldSelectAll));
    }
  });

  // Delete 不按修饰键，单独判断，避免影响普通页面
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Delete" || event.ctrlKey || event.metaKey || event.altKey) return;
    if (isEditableElement(event.target)) return;
    if (currentTabName() === "notes" && batchMode) {
      event.preventDefault();
      batchDeleteNotes();
    }
  });
}

/* ============ 启动 ============ */
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initShortcuts();
  bindLinkModal();
  bindNotesFilters();
  bindNotesSearch();
  bindEditModal();
  bindGlobalSearch();
  bindShortcutsHelp();
  bindNoteBatchActions();
  bindStyleModal();
  bindSettings();
  bindUploadModal();
  bindFeedPage();
  bindQaPage();
  bindTaskCenter();
  bindTaskPage();
  bindLinkPickers();
  bindCalendarPage();
  bindMeetingModal();
  bindRipple();
  loadStats();
  loadRecentNotes();
  loadConfig();
  loadSchedulerConfig();
  loadSchedulerLogs();
  loadVectorStats();
  loadHomeTaskPanels();
  loadScheduleNoteOptions();
  loadHomeSchedules();
});
