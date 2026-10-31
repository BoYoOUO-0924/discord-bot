"use strict";
// 嘎哇管理後台前端：不依賴任何外部函式庫。所有文字都用 textContent 放進畫面，避免 XSS。

// ---------------------------------------------------------------- 小工具
const $ = (sel, root = document) => root.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "style") Object.assign(el.style, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function s(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") el.textContent = value;
    else el.setAttribute(key, value);
  }
  return el;
}

const nf = new Intl.NumberFormat("zh-TW");
const fmt = (n) => nf.format(n ?? 0);
const signed = (n) => (n > 0 ? "+" : "") + fmt(n);
const timeFmt = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const fmtTime = (unix) => (unix ? timeFmt.format(new Date(unix * 1000)) : "—");
const fmtDay = (iso) => iso.slice(5).replace("-", "/");

function fmtDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const d = Math.floor(seconds / 86400), hr = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} 天 ${hr} 小時`;
  if (hr) return `${hr} 小時 ${m} 分`;
  return `${m} 分`;
}

function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (bad ? " bad" : "");
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3500);
}

function avatar(url, big = false) {
  return url ? h("img", { class: "avatar" + (big ? " big" : ""), src: url, alt: "" })
             : h("span", { class: "avatar" + (big ? " big" : "") });
}

function userCell(user) {
  return h("div", { class: "user-cell" }, avatar(user.avatar),
    h("div", {}, h("div", { text: user.name || "（未知名稱）" }), h("div", { class: "id", text: user.id })));
}

// ---------------------------------------------------------------- API
async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-Requested-With": "gawa-admin" };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("請先登入");
  }
  if (!res.ok) throw new Error(data.error || `發生錯誤（${res.status}）`);
  return data;
}

// ---------------------------------------------------------------- 主題
function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
}
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
try { applyTheme(localStorage.getItem("theme")); } catch (e) { /* 無法使用 localStorage 時維持系統設定 */ }
$("#theme-toggle").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("theme", next); } catch (e) { /* 忽略 */ }
  render();   // 圖表顏色跟著重畫
});

// ---------------------------------------------------------------- 登入
function showLogin() {
  stopTimers();
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#password").focus();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password: $("#password").value } });
    $("#password").value = "";
    startApp();
  } catch (e) {
    $("#login-error").textContent = e.message;
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
});

// ---------------------------------------------------------------- 路由與計時器
let timers = [];
function every(ms, fn) { timers.push(setInterval(fn, ms)); }
function stopTimers() { timers.forEach(clearInterval); timers = []; }

async function startApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  if (!location.hash) location.hash = "#/overview";
  render();
}

async function render() {
  stopTimers();
  const [, page, id] = location.hash.split("/");
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === (page || "overview")) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const view = $("#view");
  view.replaceChildren(h("p", { class: "muted", text: "載入中…" }));
  try {
    if (page === "users" && id) await renderUser(view, id);
    else if (page === "users") await renderUsers(view);
    else if (page === "games") await renderGames(view);
    else if (page === "logs") await renderLogs(view);
    else await renderOverview(view);
  } catch (e) {
    if (e.message !== "請先登入") view.replaceChildren(h("div", { class: "card error", text: e.message }));
  }
  refreshStatus();
  every(30000, refreshStatus);
}
window.addEventListener("hashchange", render);

async function refreshStatus() {
  try {
    const { bot } = await api("/api/overview");
    const color = bot.online ? "var(--good)" : "var(--critical)";
    const text = bot.online ? `線上・延遲 ${bot.latency_ms ?? "?"} ms・版本 ${bot.version}` : "離線";
    const ai = !bot.ai_configured ? "AI 未設定" : bot.ai ? "AI 湯主正常" : "⚠️ AI 湯主暫停";
    const pill = $("#bot-status");
    pill.replaceChildren(h("span", { class: "dot", style: { background: color } }), `${text}・${ai}`);
    pill.title = bot.ai_configured ? `${bot.ai_name}：${bot.ai_status}` : "沒有設定 OLLAMA_URL 或 GEMINI_API_KEY";
  } catch (e) { /* 狀態列失敗不影響頁面 */ }
}

// ---------------------------------------------------------------- 圖表
const SERIES = [
  { key: "checkin", label: "簽到", color: "--series-1" },
  { key: "work", label: "打工", color: "--series-2" },
  { key: "quest", label: "每日任務", color: "--series-3" },
  { key: "other", label: "其他（遊戲獎勵、聊天）", color: "--series-other" },
];
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function niceMax(value) {
  if (value <= 0) return 10;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * exp >= value) return step * exp;
  return 10 * exp;
}

function showTip(event, children) {
  const tip = $("#tooltip");
  tip.replaceChildren(...children);
  tip.hidden = false;
  const pad = 14, rect = tip.getBoundingClientRect();
  let x = event.clientX + pad, y = event.clientY + pad;
  if (x + rect.width > innerWidth - 8) x = event.clientX - rect.width - pad;
  if (y + rect.height > innerHeight - 8) y = event.clientY - rect.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
const hideTip = () => ($("#tooltip").hidden = true);

function roundedTop(x, y, w, hgt, r) {
  r = Math.min(r, hgt, w / 2);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

function stackedColumns(container, days) {
  const draw = () => {
    const W = Math.max(320, container.clientWidth), H = 260;
    const m = { l: 52, r: 8, t: 10, b: 26 }, innerW = W - m.l - m.r, innerH = H - m.t - m.b;
    const totals = days.map((d) => SERIES.reduce((sum, ser) => sum + (d[ser.key] || 0), 0));
    const max = niceMax(Math.max(...totals));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
                           "aria-label": "每日發放積分堆疊長條圖" });
    for (let i = 0; i <= 4; i++) {
      const value = (max / 4) * i, y = m.t + innerH - (value / max) * innerH;
      svg.append(s("line", { class: i ? "gridline" : "baseline", x1: m.l, x2: W - m.r, y1: y, y2: y }));
      svg.append(s("text", { class: "tick", x: m.l - 8, y: y + 4, "text-anchor": "end", text: fmt(value) }));
    }
    const band = innerW / days.length, bw = Math.min(32, band * 0.62);
    const labelEvery = Math.ceil((days.length * 44) / innerW);
    const columns = [];
    days.forEach((day, i) => {
      const x = m.l + i * band + (band - bw) / 2;
      const col = s("g", { class: "col" });
      let cursor = m.t + innerH;
      const parts = SERIES.filter((ser) => (day[ser.key] || 0) > 0);
      parts.forEach((ser, j) => {
        const hgt = ((day[ser.key] || 0) / max) * innerH;
        const isTop = j === parts.length - 1;
        // 段與段之間留 2px 縫隙，最上面一段頂端做 4px 圓角
        const drawH = Math.max(1, isTop ? hgt : hgt - 2);
        const y = cursor - hgt;
        const fill = cssVar(ser.color);
        col.append(isTop ? s("path", { d: roundedTop(x, y, bw, drawH, 4), fill })
                         : s("rect", { x, y: y + 2, width: bw, height: drawH, fill }));
        cursor -= hgt;
      });
      columns.push(col);
      svg.append(col);
      if (i % labelEvery === 0 || i === days.length - 1) {
        svg.append(s("text", { class: "tick", x: x + bw / 2, y: H - 8, "text-anchor": "middle", text: fmtDay(day.day) }));
      }
      // 比柱子更大的感應區，滑鼠移上去顯示這天的明細
      const hit = s("rect", { class: "hit", x: m.l + i * band, y: m.t, width: band, height: innerH });
      hit.addEventListener("mousemove", (event) => {
        columns.forEach((c, k) => c.classList.toggle("dim", k !== i));
        showTip(event, [
          h("div", { class: "title", text: `${day.day.replaceAll("-", "/")}　共 ${fmt(totals[i])} 分` }),
          ...SERIES.slice().reverse().map((ser) => h("div", { class: "row" },
            h("span", { class: "swatch", style: { background: cssVar(ser.color) } }), ser.label,
            h("span", { class: "num", text: fmt(day[ser.key] || 0) }))),
        ]);
      });
      hit.addEventListener("mouseleave", () => { columns.forEach((c) => c.classList.remove("dim")); hideTip(); });
      svg.append(hit);
    });
    container.replaceChildren(svg);
  };
  draw();
  new ResizeObserver(() => draw()).observe(container);
}

function sparkline(values) {
  const W = 150, H = 32, max = Math.max(1, ...values);
  const pts = values.map((v, i) => [(i / Math.max(1, values.length - 1)) * (W - 6) + 3, H - 3 - (v / max) * (H - 6)]);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
  svg.append(s("polyline", { points: pts.map((p) => p.join(",")).join(" "), fill: "none",
                             stroke: cssVar("--series-1"), "stroke-width": 2, "stroke-linejoin": "round" }));
  const last = pts[pts.length - 1];
  svg.append(s("circle", { cx: last[0], cy: last[1], r: 3, fill: cssVar("--series-1") }));
  return svg;
}

// ---------------------------------------------------------------- 總覽
async function renderOverview(view) {
  const data = await api("/api/overview");
  const { kpi, days, games } = data;
  const actives = days.map((d) => d.active);
  const tile = (label, value, sub, extra) =>
    h("div", { class: "card kpi" }, h("div", { class: "label", text: label }),
      h("div", { class: "value", text: value }), sub && h("div", { class: "sub", text: sub }), extra);

  const chartBox = h("div", { class: "chart" });
  const tableBox = h("div", { class: "table-wrap", hidden: true },
    h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "日期" }),
      SERIES.map((ser) => h("th", { class: "num", text: ser.label })), h("th", { class: "num", text: "合計" }))),
      h("tbody", {}, days.slice().reverse().map((d) => h("tr", {}, h("td", { text: d.day }),
        SERIES.map((ser) => h("td", { class: "num", text: fmt(d[ser.key]) })),
        h("td", { class: "num", text: fmt(SERIES.reduce((sum, ser) => sum + (d[ser.key] || 0), 0)) }))))));
  const toggle = h("button", { class: "btn small", type: "button", text: "表格檢視" });
  toggle.addEventListener("click", () => {
    const showTable = tableBox.hidden;
    tableBox.hidden = !showTable;
    chartBox.hidden = showTable;
    toggle.textContent = showTable ? "圖表檢視" : "表格檢視";
  });

  const maxAbs = Math.max(1, ...games.map((g) => Math.abs(g.net)));
  const gameRows = games.map((g) => {
    const pct = (Math.abs(g.net) / maxAbs) * 50;
    return h("tr", {},
      h("td", { text: g.name }), h("td", { class: "num", text: fmt(g.played) }), h("td", { class: "num", text: fmt(g.players) }),
      h("td", { class: "num", text: g.played ? `${Math.round((g.won / g.played) * 100)}%` : "—" }),
      h("td", {}, h("div", { class: "divbar", title: `玩家累計 ${signed(g.net)}` },
        h("span", { class: "zero" }), g.net ? h("span", { class: `bar ${g.net > 0 ? "pos" : "neg"}`, style: { width: `${pct}%` } }) : null)),
      h("td", { class: `num ${g.net > 0 ? "pos" : g.net < 0 ? "neg" : ""}`, text: signed(g.net) }));
  });

  view.replaceChildren(
    h("div", { class: "kpis" },
      tile("使用者", fmt(kpi.users), "有積分紀錄的人數"),
      tile("流通積分", fmt(kpi.points), "所有人的積分總和"),
      tile("保管中的賭注", fmt(kpi.held), "進行中遊戲先扣下的積分"),
      tile("今日活躍", fmt(kpi.active_today), "近 14 天", sparkline(actives)),
      tile("今日簽到", fmt(kpi.checkins_today), "人")),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: "每日發放積分（近 14 天）" }), h("span", { class: "spacer" }), toggle),
      h("div", { class: "legend" }, SERIES.map((ser) =>
        h("span", {}, h("span", { class: "swatch", style: { background: cssVar(ser.color) } }), ser.label))),
      chartBox, tableBox,
      h("p", { class: "muted", text: "遊戲輸贏不計入發放（是玩家之間或與莊家的移轉），請看下方各遊戲損益。" })),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: "各遊戲玩家累計損益" }),
        h("span", { class: "muted", text: "正數＝玩家整體贏、莊家發出積分；負數＝玩家整體輸、積分被回收" })),
      games.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["遊戲", "場次", "玩家數", "勝率", "損益分布", "玩家累計損益"].map((t, i) =>
          h("th", { class: [1, 2, 3, 5].includes(i) ? "num" : "", text: t })))),
        h("tbody", {}, gameRows)))
        : h("p", { class: "empty", text: "還沒有遊戲紀錄。" })),
  );
  stackedColumns(chartBox, days);
}

// ---------------------------------------------------------------- 使用者列表
const usersState = { q: "", sort: "points", page: 1 };

async function renderUsers(view) {
  const search = h("input", { type: "search", placeholder: "搜尋名稱或 Discord ID…", value: usersState.q, "aria-label": "搜尋使用者" });
  const sort = h("select", { "aria-label": "排序" },
    [["points", "依積分"], ["streak", "依連續簽到"], ["checkins", "依累計簽到"], ["games", "依遊戲場次"]].map(([v, t]) =>
      h("option", { value: v, text: t, selected: usersState.sort === v })));
  const body = h("div", {});
  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "使用者" })), h("div", { class: "toolbar" }, search, sort), body));

  const load = async () => {
    const params = new URLSearchParams({ q: usersState.q, sort: usersState.sort, page: usersState.page });
    const data = await api(`/api/users?${params}`);
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    const rows = data.items.map((u) => h("tr", { class: "clickable", tabindex: 0,
      onclick: () => (location.hash = `#/users/${u.id}`),
      onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/users/${u.id}`; } },
      h("td", {}, userCell(u)), h("td", { class: "num", text: fmt(u.points) }),
      h("td", { class: "num", text: `${u.streak} 天` }),
      h("td", {}, u.checked_today ? h("span", { class: "badge good", text: "✓ 已簽到" }) : h("span", { class: "badge", text: "未簽到" })),
      h("td", { class: "num", text: fmt(u.checkins) }), h("td", { class: "num", text: fmt(u.games) })));
    const prev = h("button", { class: "btn small", type: "button", text: "上一頁", disabled: usersState.page <= 1,
      onclick: () => { usersState.page--; load(); } });
    const next = h("button", { class: "btn small", type: "button", text: "下一頁", disabled: usersState.page >= pages,
      onclick: () => { usersState.page++; load(); } });
    body.replaceChildren(
      data.items.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["使用者", "積分", "連續簽到", "今日", "累計簽到", "遊戲場次"].map((t, i) =>
          h("th", { class: [1, 2, 4, 5].includes(i) ? "num" : "", text: t })))), h("tbody", {}, rows)))
        : h("p", { class: "empty", text: "找不到符合的使用者。" }),
      h("div", { class: "pager" }, `共 ${fmt(data.total)} 人・第 ${usersState.page} / ${pages} 頁`, prev, next));
  };

  let debounce;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { usersState.q = search.value; usersState.page = 1; load(); }, 300);
  });
  sort.addEventListener("change", () => { usersState.sort = sort.value; usersState.page = 1; load(); });
  await load();
}

// ---------------------------------------------------------------- 使用者詳情
async function renderUser(view, id) {
  const u = await api(`/api/users/${id}`);
  const name = u.name || `使用者 ${u.id}`;
  const workReady = !u.last_work || Date.now() / 1000 - u.last_work >= 3600;

  // 調整積分
  let mode = "add";
  const modeButtons = [["add", "增減"], ["set", "設定為"]].map(([value, label]) =>
    h("button", { type: "button", "aria-pressed": value === mode ? "true" : "false", text: label,
      onclick: (e) => { mode = value; modeButtons.forEach((b) => b.setAttribute("aria-pressed", b === e.target ? "true" : "false"));
                        amount.placeholder = mode === "add" ? "例如 500 或 -200" : "新的積分"; } }));
  const amount = h("input", { type: "number", placeholder: "例如 500 或 -200", required: true, step: 1, "aria-label": "金額" });
  const reason = h("input", { type: "text", placeholder: "原因（會記在對方的收支紀錄）", maxlength: 100, required: true, "aria-label": "原因" });
  const pointsForm = h("form", { class: "form" },
    h("div", { class: "row" }, h("div", { class: "segmented" }, modeButtons), amount),
    reason, h("div", {}, h("button", { class: "btn primary", type: "submit", text: "確認調整" })));
  pointsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = parseInt(amount.value, 10);
    if (Number.isNaN(value)) return toast("請輸入整數金額", true);
    const ask = mode === "add" ? `確定幫「${name}」${value >= 0 ? "加" : "扣"} ${fmt(Math.abs(value))} 分？`
                               : `確定把「${name}」的積分從 ${fmt(u.points)} 設為 ${fmt(value)}？`;
    if (!confirm(`${ask}\n原因：${reason.value}`)) return;
    try {
      const res = await api(`/api/users/${id}/points`, { method: "POST", body: { mode, amount: value, reason: reason.value } });
      toast(`已調整：${fmt(res.old)} → ${fmt(res.new)}`);
      render();
    } catch (e) { toast(e.message, true); }
  });

  // 連續簽到與打工
  const streak = h("input", { type: "number", min: 0, step: 1, value: u.streak, "aria-label": "連續簽到天數" });
  const streakForm = h("form", { class: "form" },
    h("label", { text: "連續簽到天數（例如 bot 離線害大家斷簽時補回來）" }),
    h("div", { class: "row" }, streak, h("button", { class: "btn", type: "submit", text: "儲存" })));
  streakForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!confirm(`確定把「${name}」的連續簽到改為 ${streak.value} 天？`)) return;
    try {
      await api(`/api/users/${id}/streak`, { method: "POST", body: { streak: parseInt(streak.value, 10) } });
      toast("已更新連續簽到");
      render();
    } catch (e) { toast(e.message, true); }
  });
  const resetWork = h("button", { class: "btn", type: "button", text: "重設打工冷卻", disabled: workReady,
    onclick: async () => {
      try { await api(`/api/users/${id}/reset-work`, { method: "POST" }); toast("已重設，現在可以打工了"); render(); }
      catch (e) { toast(e.message, true); }
    } });

  const quests = u.quests.map((q) => h("div", { class: "quest" },
    h("span", { text: `${q.done ? "✅" : "▫️"} ${q.title}` }), h("span", { class: "num", text: `${q.progress}/${q.target}・+${q.reward}` }),
    h("div", { class: "meter" }, h("div", { style: { width: `${Math.min(100, (q.progress / q.target) * 100)}%` } }))));

  const txBody = h("div", {});
  let txPage = 1;
  const loadTx = async () => {
    const data = await api(`/api/users/${id}/transactions?page=${txPage}`);
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    txBody.replaceChildren(
      data.items.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", { text: "時間" }), h("th", { class: "num", text: "金額" }), h("th", { text: "原因" }))),
        h("tbody", {}, data.items.map((t) => h("tr", {}, h("td", { text: fmtTime(t.at) }),
          h("td", { class: `num ${t.amount > 0 ? "pos" : t.amount < 0 ? "neg" : ""}`, text: t.amount ? signed(t.amount) : "±0" }),
          h("td", { text: t.reason }))))))
        : h("p", { class: "empty", text: "沒有收支紀錄。" }),
      h("div", { class: "pager" }, `共 ${fmt(data.total)} 筆・第 ${txPage} / ${pages} 頁`,
        h("button", { class: "btn small", type: "button", text: "上一頁", disabled: txPage <= 1, onclick: () => { txPage--; loadTx(); } }),
        h("button", { class: "btn small", type: "button", text: "下一頁", disabled: txPage >= pages, onclick: () => { txPage++; loadTx(); } })));
  };

  view.replaceChildren(...[
    h("div", {}, h("a", { href: "#/users", text: "← 回到使用者列表" })),
    h("section", { class: "card" },
      h("div", { class: "profile-head" }, avatar(u.avatar, true),
        h("div", {}, h("h2", { text: name }), h("div", { class: "id", text: `Discord ID ${u.id}` })),
        h("div", { class: "points" }, h("div", { class: "muted", text: "積分" }), h("div", { class: "value", text: fmt(u.points) }))),
      h("div", { class: "facts" },
        h("span", { text: `🔥 連續簽到 ${u.streak} 天` }), h("span", { text: `🗓️ 累計簽到 ${fmt(u.checkins)} 天` }),
        h("span", { text: `📅 最後簽到 ${u.last_checkin || "—"}` }),
        h("span", { text: workReady ? "💼 現在可以打工" : `💼 ${fmtDuration(u.last_work + 3600 - Date.now() / 1000)}後可打工` }),
        h("span", { text: `💬 今日聊天獎勵 ${u.chat_today}/200` }))),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "調整積分" })), pointsForm),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "簽到與打工" })), streakForm,
        h("div", { style: { marginTop: "12px" } }, resetWork))),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "今日任務" })), quests),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "遊戲戰績" })),
        u.games.length ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, ["遊戲", "場次", "勝率", "累計損益"].map((t, i) => h("th", { class: i ? "num" : "", text: t })))),
          h("tbody", {}, u.games.map((g) => h("tr", {}, h("td", { text: g.name }), h("td", { class: "num", text: fmt(g.played) }),
            h("td", { class: "num", text: g.played ? `${Math.round((g.won / g.played) * 100)}%` : "—" }),
            h("td", { class: `num ${g.net > 0 ? "pos" : g.net < 0 ? "neg" : ""}`, text: signed(g.net) }))))))
          : h("p", { class: "empty", text: "還沒玩過遊戲。" }))),
    (u.holds.length || u.reminders.length) ? h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "保管中的賭注" })),
        u.holds.length ? u.holds.map((hd) => h("div", { class: "quest" }, h("span", { text: hd.reason }),
          h("span", { class: "num", text: `${fmt(hd.amount)}・${fmtTime(hd.created_at)}` })))
          : h("p", { class: "empty", text: "沒有。" })),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "提醒" })),
        u.reminders.length ? u.reminders.map((r) => h("div", { class: "quest" }, h("span", { text: r.content }),
          h("span", { class: "num", text: fmtTime(r.due_at) })))
          : h("p", { class: "empty", text: "沒有。" }))) : null,
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "收支紀錄" })), txBody),
  ].filter(Boolean));   // 沒有保管中的賭注與提醒時，那一區是 null，要濾掉
  await loadTx();
}

// ---------------------------------------------------------------- 遊戲
async function renderGames(view) {
  const gamesBox = h("div", {}), holdsBox = h("div", {});
  const load = async () => {
    const data = await api("/api/games");
    gamesBox.replaceChildren(...(data.games.length ? data.games.map((g) => h("div", { class: "game" },
      h("div", { class: "head" }, h("strong", { text: g.game }), h("span", { class: "badge", text: g.where }),
        h("button", { class: "btn small danger", type: "button", text: "結束這局", onclick: async () => {
          if (!confirm(`確定要結束「${g.game}」（${g.where}）嗎？\n賭注會依目前狀況結算或退還。`)) return;
          try { await api("/api/games/end", { method: "POST", body: { type: g.type, id: g.id } }); toast("已結束"); load(); }
          catch (e) { toast(e.message, true); }
        } })),
      h("div", { class: "muted", text: g.detail }), h("div", { text: g.players.join("、") })))
      : [h("p", { class: "empty", text: "目前沒有進行中的遊戲。" })]));
    holdsBox.replaceChildren(data.holds.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["使用者", "金額", "遊戲", "扣下時間", "狀態"].map((t, i) => h("th", { class: i === 1 ? "num" : "", text: t })))),
      h("tbody", {}, data.holds.map((hd) => h("tr", {},
        h("td", {}, h("a", { href: `#/users/${hd.user_id}`, text: hd.name || hd.user_id })),
        h("td", { class: "num", text: fmt(hd.amount) }), h("td", { text: hd.reason }), h("td", { text: fmtTime(hd.created_at) }),
        h("td", {}, hd.in_use ? h("span", { class: "badge", text: `進行中（${hd.in_use}）` })
          : h("button", { class: "btn small", type: "button", text: "退還", onclick: async () => {
              if (!confirm(`確定把 ${fmt(hd.amount)} 分退還給「${hd.name || hd.user_id}」？`)) return;
              try { await api(`/api/holds/${hd.id}/refund`, { method: "POST" }); toast("已退還"); load(); }
              catch (e) { toast(e.message, true); }
            } })))))))
      : h("p", { class: "empty", text: "沒有保管中的賭注。" }));
  };
  view.replaceChildren(
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "進行中的遊戲" }),
      h("span", { class: "muted", text: "每 10 秒自動更新" })), gamesBox),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "保管中的賭注" }),
      h("span", { class: "muted", text: "遊戲開局時先扣下的積分。沒有對應遊戲的（例如卡住的）可以手動退還。" })), holdsBox));
  await load();
  every(10000, () => load().catch(() => {}));
}

// ---------------------------------------------------------------- 紀錄
const ACTIONS = { login: "登入後台", points: "調整積分", streak: "修改連續簽到", reset_work: "重設打工冷卻",
                  end_game: "結束遊戲", refund_hold: "退還賭注" };
let logsTab = "audit";

async function renderLogs(view) {
  const body = h("div", {});
  const tabs = [["audit", "管理操作紀錄"], ["bot", "bot 執行紀錄"]].map(([value, label]) =>
    h("button", { type: "button", "aria-pressed": logsTab === value ? "true" : "false", text: label,
      onclick: () => { logsTab = value; render(); } }));
  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "紀錄" }), h("span", { class: "spacer" }), h("div", { class: "segmented" }, tabs)), body));

  if (logsTab === "audit") {
    const data = await api("/api/audit");
    body.replaceChildren(data.items.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["時間", "動作", "對象", "內容", "來源 IP"].map((t) => h("th", { text: t })))),
      h("tbody", {}, data.items.map((a) => h("tr", {}, h("td", { text: fmtTime(a.at) }), h("td", { text: ACTIONS[a.action] || a.action }),
        h("td", {}, a.user_id ? h("a", { href: `#/users/${a.user_id}`, text: a.name || a.user_id }) : "—"),
        h("td", { text: a.detail }), h("td", { class: "id", text: a.ip || "" }))))))
      : h("p", { class: "empty", text: "還沒有管理操作。" }));
    return;
  }

  const level = h("select", { "aria-label": "紀錄等級" },
    [["", "全部"], ["WARNING", "警告以上"], ["ERROR", "只看錯誤"]].map(([v, t]) => h("option", { value: v, text: t })));
  const box = h("div", { class: "logbox" });
  const load = async () => {
    const data = await api(`/api/logs?lines=400&level=${level.value}`);
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
    box.replaceChildren(...(data.lines.length ? data.lines.map((line) => {
      const lv = (line.match(/\[(ERROR|WARNING|INFO)/) || [])[1] || "";
      return h("div", { class: lv ? `lv-${lv}` : "", text: line });
    }) : [h("div", { class: "muted", text: "還沒有紀錄（logs/bot.log）。" })]));
    if (atBottom) box.scrollTop = box.scrollHeight;
  };
  level.addEventListener("change", load);
  body.replaceChildren(h("div", { class: "toolbar", style: { marginBottom: "10px" } }, level,
    h("button", { class: "btn small", type: "button", text: "重新整理", onclick: load }),
    h("span", { class: "muted", text: "每 5 秒自動更新" })), box);
  await load();
  box.scrollTop = box.scrollHeight;
  every(5000, () => load().catch(() => {}));
}

// ---------------------------------------------------------------- 啟動
(async () => {
  try {
    await api("/api/session");
    startApp();
  } catch (e) {
    showLogin();
  }
})();
