let tasks = [];
let busy = false;

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  loadStatus();
  loadTasks();
  loadLogs();
  setInterval(loadLogs, 2000);
  setInterval(loadStatus, 15000);
  $("btnRunAll").addEventListener("click", runAll);
  $("btnRelogin").addEventListener("click", relogin);
  $("btnClearLogs").addEventListener("click", clearLogs);
  $("btnAddBot").addEventListener("click", openAddModal);
  $("btnCloseAdd").addEventListener("click", closeAddModal);
  $("btnCancelAdd").addEventListener("click", closeAddModal);
  $("btnProbe").addEventListener("click", probeBot);
  $("btnSaveAdd").addEventListener("click", saveNewTask);
});

/* ---------------- 添加机器人 ---------------- */

let probeCache = null;

function openAddModal() {
  $("addTarget").value = "";
  $("addName").value = "";
  $("addMode").value = "button";
  $("addCommand").value = "/start";
  $("addMatch").value = "签到";
  $("addFresh").checked = true;
  $("addCron").value = suggestNextCron();
  $("probeResult").innerHTML = "";
  probeCache = null;
  $("addModal").classList.add("open");
  setTimeout(() => $("addTarget").focus(), 50);
}

function closeAddModal() {
  $("addModal").classList.remove("open");
}

/** 顺延已有任务的最晚时间 2 分钟，避免同一时刻并发 */
function suggestNextCron() {
  if (!tasks.length) return "08:36";
  let latest = 0;
  tasks.forEach((t) => {
    const m = /^(\d{1,2}):(\d{1,2})$/.exec(t.cron_time || "");
    if (!m) return;
    latest = Math.max(latest, parseInt(m[1], 10) * 60 + parseInt(m[2], 10));
  });
  const next = Math.min(latest + 2, 23 * 60 + 59);
  return String(Math.floor(next / 60)).padStart(2, "0") + ":" + String(next % 60).padStart(2, "0");
}

function normalizeTarget(raw) {
  let s = (raw || "").trim();
  if (!s) return "";
  // 支持 t.me/xxx、https://t.me/xxx、@xxx、xxx
  s = s.replace(/^https?:\/\//i, "").replace(/^(t\.me|telegram\.me)\//i, "");
  s = s.split("?")[0].split("/")[0];
  if (!s.startsWith("@") && !/^-?\d+$/.test(s)) s = "@" + s;
  return s;
}

async function probeBot() {
  const target = normalizeTarget($("addTarget").value);
  if (!target) {
    $("probeResult").innerHTML = '<span class="probe-err">请先填写机器人用户名</span>';
    return;
  }
  $("addTarget").value = target;
  const btn = $("btnProbe");
  btn.disabled = true;
  btn.textContent = "探测中…";
  $("probeResult").innerHTML = '<span class="probe-sub">正在读取历史消息…</span>';

  try {
    // 默认 send=false：只读历史，绝不给 bot 发消息
    const r = await fetch(`/api/probe?target=${encodeURIComponent(target)}`);
    const d = await r.json();
    if (!d.ok) {
      $("probeResult").innerHTML = `<span class="probe-err">${esc(d.error || "探测失败")}</span>`;
      return;
    }
    probeCache = d;

    if (!$("addName").value.trim()) $("addName").value = d.title || target;

    if (!d.buttons || !d.buttons.length) {
      $("addMode").value = "command";
      $("probeResult").innerHTML = `
        <div class="probe-title">${esc(d.title)}</div>
        <div class="probe-sub">@${esc(d.username || "")} · 机器人</div>
        <div class="probe-note">历史里没有找到按钮，已切换为「发命令」模式。
        请把「先发送的指令」填成该 bot 实际支持的命令。</div>`;
      return;
    }

    const best = d.suggest_button;
    const chips = d.buttons
      .map(
        (b) =>
          `<button class="probe-btn ${b.text === best ? "best" : ""}" data-btn="${esc(
            b.text
          )}">${esc(b.text)}</button>`
      )
      .join("");

    $("probeResult").innerHTML = `
      <div class="probe-title">${esc(d.title)}</div>
      <div class="probe-sub">@${esc(d.username || "")} · 机器人 · 未发送任何消息</div>
      <div class="probe-btns">${chips}</div>
      ${d.note ? `<div class="probe-note">${esc(d.note)}</div>` : ""}`;

    if (best) {
      $("addMode").value = "button";
      $("addMatch").value = escapeRegex(best);
    }

    $("probeResult").querySelectorAll(".probe-btn").forEach((el) => {
      el.addEventListener("click", () => {
        $("addMatch").value = escapeRegex(el.dataset.btn);
        $("addMode").value = "button";
        $("probeResult").querySelectorAll(".probe-btn").forEach((x) => x.classList.remove("best"));
        el.classList.add("best");
      });
    });
  } catch (e) {
    $("probeResult").innerHTML = `<span class="probe-err">请求失败：${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "探测";
  }
}

function escapeRegex(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function saveNewTask() {
  const target = normalizeTarget($("addTarget").value);
  const name = $("addName").value.trim() || target;
  const cron = $("addCron").value.trim() || "08:36";

  if (!target) {
    alert("请先填写机器人用户名");
    return;
  }
  if (!/^\d{1,2}:\d{1,2}$/.test(cron)) {
    alert("时间格式应为 HH:MM，例如 08:36");
    return;
  }
  if (tasks.some((t) => t.target_bot.toLowerCase() === target.toLowerCase())) {
    alert("这个机器人已经添加过了");
    return;
  }

  const task = {
    id: "bot_" + Date.now().toString(36),
    name,
    target_bot: target,
    mode: $("addMode").value,
    command: $("addCommand").value.trim() || "/start",
    button_match: $("addMatch").value.trim() || "签到",
    wait_seconds: 4,
    cron_time: cron,
    success_keywords: ["签到成功", "成功", "已完成", "已经签过", "已签到"],
    fresh_menu: $("addFresh").checked,
    cleanup_command: true,
    enabled: true,
    last_run: null,
    last_status: "未运行",
    last_ok: null,
  };

  tasks.push(task);
  await saveTasks();
  closeAddModal();

  // 加完不要立刻点它 —— 先让用户确认配置
  const card = $("task-" + task.id);
  if (card) {
    card.classList.add("running");
    setTimeout(() => card.classList.remove("running"), 1600);
  }
}

/* ---------------- 状态 ---------------- */

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    const badge = $("connBadge");
    const text = $("connText");

    if (d.connected && d.user) {
      badge.className = "badge ok";
      text.textContent = `${d.user.name}${d.user.username ? " @" + d.user.username : ""}`;
      $("metaUser").textContent = d.user.name + (d.user.username ? " (@" + d.user.username + ")" : "");
    } else {
      badge.className = "badge err";
      text.textContent = d.error ? "未连接" : "连接中…";
      $("metaUser").textContent = d.error || "—";
    }

    const enabled = tasks.filter((t) => t.enabled).length;
    $("metaTasks").textContent = `${enabled} / ${tasks.length} 已启用`;

    const ip = d.local_ip && d.local_ip !== "127.0.0.1" ? d.local_ip : null;
    $("footInfo").textContent = ip
      ? `本机 http://127.0.0.1:${d.port} · 局域网 http://${ip}:${d.port}`
      : `http://127.0.0.1:${d.port}`;
  } catch (e) {
    $("connBadge").className = "badge err";
    $("connText").textContent = "面板未响应";
  }
}

/* ---------------- 任务 ---------------- */

async function loadTasks() {
  const r = await fetch("/api/tasks");
  tasks = await r.json();
  renderTasks();
  const enabled = tasks.filter((t) => t.enabled).length;
  $("metaTasks").textContent = `${enabled} / ${tasks.length} 已启用`;
}

function renderTasks() {
  const grid = $("taskGrid");
  grid.innerHTML = "";
  tasks.forEach((t) => {
    const el = document.createElement("div");
    const stClass = t.last_ok === true ? "ok" : t.last_ok === false ? "fail" : "";
    // "今日已签到" 与 "本次签到成功" 视觉上要分得开
    const isCached = /今日已签/.test(t.last_status || "");
    el.className = `task ${stClass} ${isCached ? "cached" : ""} ${t.enabled ? "" : "off"}`;
    el.id = "task-" + t.id;

    const modeLabel = t.mode === "command" ? "命令签到" : "按钮签到";
    const modeHint = t.mode === "command"
      ? t.command
      : `${t.command} → 点「${t.button_match}」`;
    // 菜单策略：每次刷新 vs 复用。前者稳，后者省一条消息
    const menuChip = t.mode === "command"
      ? ""
      : `<span class="chip ${t.fresh_menu === false ? "" : "fresh"}" title="${
          t.fresh_menu === false
            ? "复用现有菜单，不额外发指令"
            : "每次先发指令刷新菜单，避免点到已被覆盖的旧面板"
        }">${t.fresh_menu === false ? "复用菜单" : "每次刷新菜单"}</span>`;
    const btnLabel = stClass === "ok" ? (isCached ? "今日已签" : "已完成") : "签到";

    el.innerHTML = `
      <div class="task-main">
        <div class="task-head">
          <span class="task-name">${esc(t.name)}</span>
          <span class="chip mode">${modeLabel}</span>
          ${menuChip}
          <span class="chip">每天 ${esc(t.cron_time)}</span>
        </div>
        <div class="task-bot">${esc(t.target_bot)} · ${esc(modeHint)}</div>
        <div class="task-status ${stClass}">
          <span class="st-dot"></span>
          <span>${esc(t.last_status || "未运行")}</span>
        </div>
        <div class="task-time">${t.last_run ? "上次执行 " + esc(t.last_run) : "尚未执行"}</div>
      </div>
      <div class="task-actions">
        <button class="go-btn ${stClass === "ok" ? "done" : ""}" data-id="${esc(t.id)}">${btnLabel}</button>
        <label class="toggle">
          <input type="checkbox" ${t.enabled ? "checked" : ""} data-toggle="${esc(t.id)}">
          启用
        </label>
        <button class="del-btn" data-del="${esc(t.id)}" title="删除此任务">删除</button>
      </div>`;

    el.querySelector(".go-btn").addEventListener("click", (e) => runOne(t.id, e.target));
    el.querySelector("input[type=checkbox]").addEventListener("change", (e) => {
      t.enabled = e.target.checked;
      saveTasks();
    });
    el.querySelector(".del-btn").addEventListener("click", () => {
      if (!confirm(`删除任务「${t.name}」？\n只是不再签到，不会在 Telegram 里做任何操作。`)) return;
      tasks = tasks.filter((x) => x.id !== t.id);
      saveTasks();
    });
    grid.appendChild(el);
  });
}

async function saveTasks() {
  await fetch("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(tasks),
  });
  loadTasks();
}

/* ---------------- 执行 ---------------- */

async function runOne(id, btn) {
  if (busy) return;
  busy = true;
  const card = $("task-" + id);
  if (card) card.classList.add("running");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "执行中…";
  }
  try {
    await fetch(`/api/run/${id}`, { method: "POST" });
  } catch (e) {
    /* 日志里会体现 */
  }
  await loadTasks();
  await loadLogs();
  busy = false;
}

async function runAll() {
  if (busy) return;
  busy = true;
  const btn = $("btnRunAll");
  const hint = $("mainBtnHint");
  const original = hint.textContent;
  btn.disabled = true;
  hint.textContent = "正在依次签到，请稍候…";

  try {
    const r = await fetch("/api/run_all", { method: "POST" });
    const d = await r.json();
    $("metaLast").textContent = `${d.success} / ${d.total} 成功`;
    hint.textContent =
      d.success === d.total
        ? `全部完成：${d.success}/${d.total} 成功`
        : `完成：${d.success}/${d.total} 成功，详见日志`;
  } catch (e) {
    hint.textContent = "执行失败，请查看日志";
  }

  await loadTasks();
  await loadLogs();
  btn.disabled = false;
  busy = false;
  setTimeout(() => {
    hint.textContent = original;
  }, 6000);
}

async function relogin() {
  const b = $("btnRelogin");
  b.classList.add("spin");
  try {
    await fetch("/api/relogin", { method: "POST" });
  } catch (e) {}
  await loadStatus();
  await loadLogs();
  b.classList.remove("spin");
}

/* ---------------- 日志 ---------------- */

let lastLogLen = 0;

async function loadLogs() {
  try {
    const r = await fetch("/api/logs");
    const d = await r.json();
    const body = $("consoleBody");
    if (!d.logs) return;
    if (d.logs.length === lastLogLen) return;
    lastLogLen = d.logs.length;
    body.innerHTML = d.logs
      .map(
        (l) =>
          `<div class="line ${l.level}"><span class="t">${esc(l.time)}</span><span class="m">${esc(
            l.text
          )}</span></div>`
      )
      .join("");
    body.scrollTop = body.scrollHeight;
    $("logCount").textContent = `${d.logs.length} 条`;
  } catch (e) {}
}

async function clearLogs() {
  await fetch("/api/logs/clear", { method: "POST" });
  lastLogLen = 0;
  $("consoleBody").innerHTML = "";
  $("logCount").textContent = "0 条";
}

/* ---------------- 工具 ---------------- */

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
