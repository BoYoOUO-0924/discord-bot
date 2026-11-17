"use strict";
// 管理後台前端：不依賴任何外部函式庫。所有文字都用 textContent 放進畫面，避免 XSS。
// 動畫與特效在 motion.js（Motion）。

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

// ---------------------------------------------------------------- 立即更新
$("#update-now").addEventListener("click", async () => {
  if (!confirm("立刻從 GitHub 更新並重新啟動 bot？\n進行中的遊戲會被中斷，未結算的賭注會自動退還。")) return;
  const button = $("#update-now");
  button.disabled = true;
  try {
    const { message } = await api("/api/update", { method: "POST" });
    toast(message);
  } catch (e) {
    toast(e.message, true);
  }
  // 重新啟動期間後台會斷線，過一下再更新狀態列（版本號）
  setTimeout(() => { button.disabled = false; refreshStatus(); }, 20000);
});

// ---------------------------------------------------------------- 路由與計時器
let timers = [];
let cleanups = [];
function every(ms, fn) { timers.push(setInterval(fn, ms)); }
function onLeave(fn) { cleanups.push(fn); }
function stopTimers() {
  timers.forEach(clearInterval); timers = [];
  cleanups.forEach((fn) => { try { fn(); } catch (e) { /* 忽略 */ } }); cleanups = [];
}

const PAGES = { overview: "總覽", users: "使用者", games: "遊戲牌局", features: "功能開關", board: "待辦看板",
                ai: "AI 與個性", serverlog: "伺服器紀錄", logs: "操作紀錄", changelog: "更新日誌" };
let botInfo = null;

async function startApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  Motion.bg.start();
  if (!location.hash) location.hash = "#/overview";
  let booted = false;
  try { booted = sessionStorage.getItem("booted") === "1"; sessionStorage.setItem("booted", "1"); } catch (e) { /* 忽略 */ }
  if (!booted) await playBoot();
  render(true);
}

async function playBoot() {
  let info = botInfo;
  try { if (!info) info = (await api("/api/overview")).bot; } catch (e) { /* 開機畫面拿不到資料也照播 */ }
  const name = (info && info.display) || "BOT";
  await Motion.boot([
    `> ${name.toUpperCase()} CONTROL CENTER ${info ? "v" + info.version : ""}`,
    `> 連線 Discord Gateway ........ `,
    `> 載入功能模組 ................ `,
    `> 連線 AI 核心（${info && info.ai_configured ? info.ai_name : "未設定"}）`.padEnd(30, " ") + " ",
    `> 掛載 ${info ? info.guilds : "?"} 個伺服器 ............ `,
    "> 所有系統正常，進入控制中心",
  ]);
}

async function render(first = false) {
  stopTimers();
  const [, page, id] = location.hash.split("/");
  const key = PAGES[page] ? page : "overview";
  document.querySelectorAll(".side a[data-tab]").forEach((a) => {
    if (a.dataset.tab === key) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("#page-title").textContent = PAGES[key];
  document.title = `${PAGES[key]}・${(botInfo && botInfo.display) || "管理後台"}`;
  if (!first) Motion.wipe();
  const view = $("#view");
  view.replaceChildren(h("p", { class: "empty", text: "> 載入中…" }));
  try {
    if (page === "users" && id) await renderUser(view, id);
    else if (page === "users") await renderUsers(view);
    else if (page === "games") await renderGames(view);
    else if (page === "board") await renderBoard(view);
    else if (page === "features") await renderFeatures(view);
    else if (page === "serverlog") await renderServerLog(view);
    else if (page === "logs") await renderAudit(view);
    else if (page === "ai") await renderAI(view);
    else if (page === "changelog") await renderChangelog(view);
    else await renderOverview(view);
  } catch (e) {
    if (e.message !== "請先登入") view.replaceChildren(h("div", { class: "card error", text: e.message }));
  }
  refreshStatus();
  every(30000, refreshStatus);
}
window.addEventListener("hashchange", () => render());

async function refreshStatus() {
  try {
    const { bot } = await api("/api/overview");
    setBotInfo(bot);
    const color = bot.online ? "var(--good)" : "var(--critical)";
    const text = bot.online ? `ONLINE ${bot.latency_ms ?? "?"}ms・${bot.version}` : "OFFLINE";
    const ai = !bot.ai_configured ? "AI 未設定" : bot.ai ? "AI 正常" : "⚠ AI 暫停";
    const pill = $("#bot-status");
    pill.replaceChildren(h("span", { class: "dot", style: { background: color, color } }), `${text}・${ai}`);
    pill.title = bot.ai_configured ? `${bot.ai_name}：${bot.ai_status}` : "沒有設定 OLLAMA_URL";
    $("#update-now").hidden = !bot.auto_update;
  } catch (e) { /* 狀態列失敗不影響頁面 */ }
}

function setBotInfo(bot) {
  botInfo = bot;
  $("#brand-name").textContent = bot.display || "管理後台";
  const av = $("#brand-av");
  if (bot.avatar && av.tagName !== "IMG") av.replaceWith(h("img", { class: "mini-av", id: "brand-av", src: bot.avatar, alt: "" }));
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
                           "aria-label": "每日發放金額堆疊長條圖" });
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
      col.style.setProperty("--i", i);
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


// ---------------------------------------------------------------- 總覽（首頁：開機動畫、核心全像、即時資料）
function fmtUptime(sec) {
  const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), mm = Math.floor((sec % 3600) / 60), ss = Math.floor(sec % 60);
  return `${d}d ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function liveChart(svg, values) {
  const W = 400, H = 190, n = values.length, step = W / Math.max(1, n - 1);
  const max = niceMax(Math.max(4, ...values));
  const pts = values.map((v, i) => [i * step, H - 16 - (v / max) * (H - 30)]);
  const line = pts.map((p) => p.map((x) => x.toFixed(1)).join(",")).join(" ");
  const peak = values.indexOf(Math.max(...values)), [px, py] = pts[peak], [lx, ly] = pts[n - 1];
  svg.replaceChildren();
  const defs = s("defs");
  const fill = s("linearGradient", { id: "lfill", x1: 0, x2: 0, y1: 0, y2: 1 });
  fill.append(s("stop", { offset: 0, "stop-color": "#35d9ff", "stop-opacity": ".45" }), s("stop", { offset: 1, "stop-color": "#b26bff", "stop-opacity": "0" }));
  const stroke = s("linearGradient", { id: "lstroke", x1: 0, x2: 1 });
  stroke.append(s("stop", { offset: 0, "stop-color": "#b26bff" }), s("stop", { offset: 1, "stop-color": "#35d9ff" }));
  defs.append(fill, stroke);
  svg.append(defs);
  [.25, .5, .75].forEach((f) => svg.append(s("line", { x1: 0, x2: W, y1: H * f, y2: H * f, stroke: "rgba(80,220,255,.1)" })));
  svg.append(s("polygon", { points: `0,${H} ${line} ${W},${H}`, fill: "url(#lfill)" }));
  svg.append(s("polyline", { points: line, fill: "none", stroke: "url(#lstroke)", "stroke-width": 2.4, "vector-effect": "non-scaling-stroke" }));
  if (values[peak] > 0) {
    svg.append(s("line", { x1: px, x2: px, y1: py, y2: H, stroke: "rgba(255,200,87,.5)", "stroke-dasharray": "3 3" }));
    svg.append(s("circle", { cx: px, cy: py, r: 3.5, fill: "#ffc857" }));
    svg.append(s("text", { x: Math.min(px + 6, W - 80), y: Math.max(py - 8, 12), fill: "#ffc857", "font-size": 11, "font-family": "Consolas, monospace", text: `PEAK ${values[peak]}/分` }));
  }
  svg.append(s("line", { x1: lx, x2: lx, y1: 0, y2: H, stroke: "rgba(124,255,156,.35)" }));
  const dot = s("circle", { cx: lx, cy: ly, r: 4, fill: "#7cff9c" });
  if (!Motion.reduce) dot.append(s("animate", { attributeName: "r", values: "3;7;3", dur: "1s", repeatCount: "indefinite" }));
  svg.append(dot);
}

async function renderOverview(view) {
  const data = await api("/api/overview");
  const { kpi, days, games, bot } = data;
  setBotInfo(bot);
  const totals = days.map((d) => SERIES.reduce((sum, ser) => sum + (d[ser.key] || 0), 0));

  // ---- 核心全像
  const holoCanvas = h("canvas", { "aria-label": "機器人核心：中間是核心，外圈每個光點是一個伺服器，有人使用指令時會射出資料封包" });
  const uptime = h("b", { text: fmtUptime(bot.uptime) });
  const gaugeBox = h("div", { class: "gauges" });
  const gLatency = Motion.gauge("延遲 ms", "var(--accent)");
  const gActive = Motion.gauge("今日活躍 %", "var(--accent-3)");
  const gCheckin = Motion.gauge("今日簽到 %", "var(--accent-2)");
  gaugeBox.append(gLatency.el, gActive.el, gCheckin.el);
  const setGauges = (b, k) => {
    gLatency.set((b.latency_ms ?? 0) / 500, b.latency_ms ?? "—");
    const pct = (n) => (k.users ? Math.round((n / k.users) * 100) : 0);
    gActive.set(pct(k.active_today) / 100, pct(k.active_today));
    gCheckin.set(pct(k.checkins_today) / 100, pct(k.checkins_today));
  };
  const sys = h("div", { class: "sys" + (bot.online ? "" : " off"), text: bot.online ? "System online" : "System offline" });
  const name = (bot.display || "BOT").toUpperCase();
  const core = h("section", { class: "card core beam scan" }, holoCanvas,
    h("div", {}, sys, h("div", { class: "glitch", "data-text": name, text: name }),
      h("div", { class: "stat-lines" },
        h("span", {}, "運行　", uptime),
        h("span", {}, "伺服器 ", h("b", { text: String(bot.guilds) }), "・AI ", h("b", { text: bot.ai_configured ? bot.ai_name : "未設定" })),
        h("span", {}, "版本 ", h("b", { text: bot.version }), "・", h("b", { text: bot.ai ? "AI 正常" : "AI 暫停" }))),
      gaugeBox,
      h("button", { class: "replay", type: "button", text: "↻ 重播開機動畫", onclick: () => playBoot() })));

  // ---- 數字卡片
  const kpiCard = (label, value, sub, sparkValues, cls = "", color) => {
    const v = h("div", { class: "value", text: "0" });
    const card = h("div", { class: `card kpi ${cls}` }, h("div", { class: "label", text: label }), v, h("div", { class: "sub", text: sub }),
      Motion.spark(sparkValues, color));
    Motion.scramble(v, value);
    return { card, v };
  };
  const kUsers = kpiCard("使用者", kpi.users, `USERS・今日活躍 ${fmt(kpi.active_today)}`, days.map((d) => d.active));
  const kPoints = kpiCard("流通存款", kpi.points, `保管中 ${fmt(kpi.held)}`, totals, "", "var(--accent-3)");
  const kCmds = kpiCard("今日指令", 0, "CMDS・bot 啟動後累計", [0], "good", "var(--accent-2)");
  const kErr = kpiCard("錯誤", 0, "ERRORS・近 24 小時", [0], "bad", "var(--critical)");

  // ---- 即時圖表與紀錄
  const liveSvg = s("svg", { viewBox: "0 0 400 190", preserveAspectRatio: "none", role: "img", "aria-label": "最近 60 分鐘每分鐘指令數" });
  const term = h("div", { class: "term", "aria-live": "off" });
  const termCard = h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "伺服器紀錄・即時" }),
    h("span", { class: "spacer" }), h("a", { class: "btn small", href: "#/serverlog", text: "完整紀錄" })), term);
  const ticker = h("div", { class: "track" });

  // ---- 每日發放與遊戲損益（原本的總覽內容）
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
    core,
    h("div", { class: "kpis" }, kUsers.card, kPoints.card, kCmds.card, kErr.card),
    h("div", { class: "home-row" },
      h("section", { class: "card live" }, h("div", { class: "card-head" }, h("h2", { text: "每分鐘指令數・最近 60 分鐘" })), liveSvg),
      termCard),
    h("div", { class: "ticker", "aria-hidden": "true" }, ticker),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: "每日發放金額・近 14 天" }), h("span", { class: "spacer" }), toggle),
      h("div", { class: "legend" }, SERIES.map((ser) =>
        h("span", {}, h("span", { class: "swatch", style: { background: cssVar(ser.color), color: cssVar(ser.color) } }), ser.label))),
      chartBox, tableBox,
      h("p", { class: "muted", text: "遊戲輸贏不計入發放（是玩家之間或與莊家的移轉），請看下方各遊戲損益。" })),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { text: "各遊戲玩家累計損益" }),
        h("span", { class: "muted", text: "正數＝玩家整體贏、莊家發出存款；負數＝玩家整體輸、存款被回收" })),
      games.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["遊戲", "場次", "玩家數", "勝率", "損益分布", "玩家累計損益"].map((t, i) =>
          h("th", { class: [1, 2, 3, 5].includes(i) ? "num" : "", text: t })))),
        h("tbody", {}, gameRows)))
        : h("p", { class: "empty", text: "還沒有遊戲紀錄。" })),
  );
  stackedColumns(chartBox, days);
  setGauges(bot, kpi);

  const holo = new Motion.Holo(holoCanvas);
  holo.setNodes(bot.guilds);
  onLeave(() => holo.stop());

  // ---- 即時更新：每 3 秒讀 /api/pulse
  let seq = null, lastLog = null, guildIndex = {};
  const swapSpark = (card, values, color) => card.querySelector("svg").replaceWith(Motion.spark(values, color));
  const tickerItems = (pulse) => {
    const items = [
      ["伺服器", `${pulse.guilds.length} 個`], ["今日簽到", `${fmt(kpi.checkins_today)} 人`], ["今日活躍", `${fmt(kpi.active_today)} 人`],
      ["流通存款", fmt(kpi.points)], ["AI", bot.ai ? "正常" : "暫停"],
      ...pulse.guilds.slice(0, 12).map((g) => [g.name, g.members ? `${fmt(g.members)} 位成員` : "已連線"]),
    ];
    const once = items.map(([a, b]) => h("span", {}, h("b", { text: a }), "　", h("span", { class: "up", text: b })));
    ticker.replaceChildren(...once, ...once.map((n) => n.cloneNode(true)));
  };
  const pulse = async (firstTime) => {
    const p = await api(`/api/pulse?since=${seq ?? 0}`);
    p.guilds.forEach((g, i) => { guildIndex[g.id] = i; });
    holo.setNodes(p.guilds.length || bot.guilds);
    if (!firstTime) p.events.forEach((e, i) => setTimeout(() => holo.packet(guildIndex[e.guild] ?? i), i * 150));
    seq = p.seq;
    liveChart(liveSvg, p.per_minute);
    Motion.scramble(kCmds.v, p.today_commands);
    Motion.scramble(kErr.v, p.errors_24h);
    if (firstTime) {
      swapSpark(kCmds.card, p.per_minute, "var(--accent-2)");
      swapSpark(kErr.card, p.error_hours, "var(--critical)");
      tickerItems(p);
    }
    const key = (r) => `${r.time}|${r.text}`;
    const start = lastLog ? p.logs.findIndex((r) => key(r) === lastLog) + 1 : 0;
    const fresh = lastLog && start === 0 ? p.logs : p.logs.slice(start);
    let alarm = false;
    fresh.forEach((r) => {
      const bad = r.level === "ERROR" || r.level === "CRITICAL";
      if (bad && !firstTime) alarm = true;
      term.append(h("div", { class: (bad ? "err " : "") + (firstTime ? "" : "new") },
        h("span", { class: "t", text: `${r.time} ` }), h("span", { class: r.level, text: `${r.level.padEnd(7)} ` }), r.text));
    });
    while (term.children.length > 12) term.firstChild.remove();
    if (!p.logs.length && !term.children.length) term.append(h("div", { class: "t", text: "還沒有紀錄（logs/bot.log）" }));
    if (p.logs.length) lastLog = key(p.logs[p.logs.length - 1]);
    if (alarm) { Motion.alarm(termCard); holo.packet(0, true); }
  };
  await pulse(true).catch(() => liveChart(liveSvg, new Array(60).fill(0)));
  every(3000, () => pulse(false).catch(() => {}));
  let up = bot.uptime;
  every(1000, () => { up += 1; uptime.textContent = fmtUptime(up); });
  every(20000, async () => {
    try { const d = await api("/api/overview"); setGauges(d.bot, d.kpi); } catch (e) { /* 忽略 */ }
  });
}

// ---------------------------------------------------------------- 使用者列表
const usersState = { q: "", sort: "points", page: 1 };

async function renderUsers(view) {
  const search = h("input", { type: "search", placeholder: "搜尋名稱或 Discord ID…", value: usersState.q, "aria-label": "搜尋使用者" });
  const sort = h("select", { "aria-label": "排序" },
    [["points", "依存款"], ["streak", "依連續簽到"], ["checkins", "依累計簽到"], ["games", "依遊戲場次"]].map(([v, t]) =>
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
        h("thead", {}, h("tr", {}, ["使用者", "存款", "連續簽到", "今日", "累計簽到", "遊戲場次"].map((t, i) =>
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

  // 調整存款
  let mode = "add";
  const modeButtons = [["add", "增減"], ["set", "設定為"]].map(([value, label]) =>
    h("button", { type: "button", "aria-pressed": value === mode ? "true" : "false", text: label,
      onclick: (e) => { mode = value; modeButtons.forEach((b) => b.setAttribute("aria-pressed", b === e.target ? "true" : "false"));
                        amount.placeholder = mode === "add" ? "例如 500 或 -200" : "新的存款"; } }));
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
                               : `確定把「${name}」的存款從 ${fmt(u.points)} 設為 ${fmt(value)}？`;
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
        h("div", { class: "points" }, h("div", { class: "muted", text: "存款" }), h("div", { class: "value", text: fmt(u.points) }))),
      h("div", { class: "facts" },
        h("span", { text: `🔥 連續簽到 ${u.streak} 天` }), h("span", { text: `🗓️ 累計簽到 ${fmt(u.checkins)} 天` }),
        h("span", { text: `📅 最後簽到 ${u.last_checkin || "—"}` }),
        h("span", { text: workReady ? "💼 現在可以打工" : `💼 ${fmtDuration(u.last_work + 3600 - Date.now() / 1000)}後可打工` }),
        h("span", { text: `💬 今日聊天獎勵 ${u.chat_today}/200` }))),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "調整存款" })), pointsForm),
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
      h("span", { class: "muted", text: "遊戲開局時先扣下的存款。沒有對應遊戲的（例如卡住的）可以手動退還。" })), holdsBox));
  await load();
  every(10000, () => load().catch(() => {}));
}

// ---------------------------------------------------------------- 待辦看板
const BOARD_COLUMNS = [["todo", "📝 待辦"], ["doing", "🔨 進行中"], ["done", "✅ 完成"]];
const PRIORITY_LABEL = { 0: "🟢 低", 1: "🟡 中", 2: "🔴 高" };
const REPEAT_LABEL = { daily: "每天", weekdays: "平日", weekly: "每週", monthly: "每月" };
const boardFilter = { guild: "", project: "" };

async function renderBoard(view) {
  const filters = h("div", { class: "toolbar" });
  const columns = h("div", { class: "board" });
  const note = h("span", { class: "muted" });

  const move = async (taskId, status) => {
    try {
      await api(`/api/tasks/${taskId}/status`, { method: "POST", body: { status } });
      await load();
    } catch (e) { toast(e.message, true); }
  };

  const card = (t, index) => {
    const step = (delta) => BOARD_COLUMNS[index + delta] && h("button", {
      class: "btn small ghost", type: "button", text: delta < 0 ? "◀" : "▶",
      "aria-label": `移到「${BOARD_COLUMNS[index + delta][1]}」`, onclick: () => move(t.id, BOARD_COLUMNS[index + delta][0]),
    });
    return h("article", { class: "task" + (t.overdue ? " overdue" : ""), draggable: "true", "data-id": t.id,
        ondragstart: (e) => { e.dataTransfer.setData("text/plain", String(t.id)); e.currentTarget.classList.add("dragging"); },
        ondragend: (e) => e.currentTarget.classList.remove("dragging") },
      h("div", { class: "task-title" }, h("span", { class: "muted", text: `#${t.id} ` }), t.title),
      t.note ? h("div", { class: "task-note muted", text: t.note.slice(0, 120) }) : null,
      h("div", { class: "task-meta" },
        h("span", { class: "badge", text: PRIORITY_LABEL[t.priority] }),
        t.project ? h("span", { class: "badge", text: `📁 ${t.project}` }) : null,
        t.repeat ? h("span", { class: "badge", text: `🔁 ${REPEAT_LABEL[t.repeat] || t.repeat}` }) : null,
        t.due_at ? h("span", { class: "badge" + (t.overdue ? " bad" : ""), text: `${t.overdue ? "⚠️ " : ""}${fmtTime(t.due_at)} 截止` }) : null),
      h("div", { class: "task-foot" },
        h("span", { text: t.assignee ? `👤 ${t.assignee.name}` : "沒人負責", class: t.assignee ? "" : "muted" }),
        h("span", { class: "spacer" }), step(-1), step(1)));
  };

  const load = async () => {
    const query = new URLSearchParams();
    if (boardFilter.guild) query.set("guild", boardFilter.guild);
    if (boardFilter.project) query.set("project", boardFilter.project);
    const data = await api(`/api/board?${query}`);

    const select = (label, items, key, onchange) => h("label", { class: "inline" }, label,
      h("select", { onchange }, h("option", { value: "", text: "全部" }),
        items.map((it) => h("option", { value: it.id, text: it.name, selected: String(it.id) === boardFilter[key] }))));
    filters.replaceChildren(
      select("伺服器 ", data.guilds, "guild", (e) => { boardFilter.guild = e.target.value; boardFilter.project = ""; load(); }),
      select("專案 ", data.projects, "project", (e) => { boardFilter.project = e.target.value; load(); }));
    note.textContent = `完成欄只顯示最近 ${data.done_days} 天・拖曳卡片或按 ◀ ▶ 移動・在這裡完成也會發獎勵、建立週期任務，Discord 上的卡片會同步更新`;

    columns.replaceChildren(...BOARD_COLUMNS.map(([status, title], index) => {
      const items = data.tasks.filter((t) => t.status === status);
      const list = h("div", { class: "board-list" }, items.length ? items.map((t) => card(t, index))
        : h("p", { class: "empty", text: "沒有待辦" }));
      return h("section", { class: "board-col", "data-status": status,
          ondragover: (e) => { e.preventDefault(); e.currentTarget.classList.add("drop"); },
          ondragleave: (e) => e.currentTarget.classList.remove("drop"),
          ondrop: (e) => {
            e.preventDefault();
            e.currentTarget.classList.remove("drop");
            const id = e.dataTransfer.getData("text/plain");
            if (id) move(id, status);
          } },
        h("h3", {}, title, h("span", { class: "badge", text: String(items.length) })), list);
    }));
  };

  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "待辦看板" }), h("span", { class: "spacer" }), filters), note),
    columns);
  await load();
  every(30000, () => { if (!document.querySelector(".task.dragging")) load().catch(() => {}); });
}

// ---------------------------------------------------------------- 功能開關
let featureGuild = "";

async function renderFeatures(view) {
  const box = h("div");
  const save = async (guild, feature, enabled) => {
    try {
      await api("/api/features", { method: "POST", body: { guild, feature, enabled } });
      toast("已儲存，立即生效");
    } catch (e) { toast(e.message, true); }
    await load();
  };

  const load = async () => {
    const data = await api("/api/features");
    if (featureGuild && !data.guilds.some((g) => g.id === featureGuild)) featureGuild = "";
    const overrides = data.overrides[featureGuild] || {};
    const guildName = (data.guilds.find((g) => g.id === featureGuild) || {}).name;

    const picker = h("label", { class: "inline" }, "個別伺服器 ",
      h("select", { onchange: (e) => { featureGuild = e.target.value; load(); } },
        h("option", { value: "", text: "（不選，只看全部伺服器）" }),
        data.guilds.map((g) => h("option", { value: g.id, text: g.name, selected: g.id === featureGuild }))));

    const rows = data.features.map((f) => {
      const globalOn = data.global[f.key];
      const globalSwitch = h("label", { class: "switch" },
        h("input", { type: "checkbox", checked: globalOn, "aria-label": `${f.name}：所有伺服器`,
          onchange: (e) => save("0", f.key, e.target.checked) }),
        h("span", { text: globalOn ? "開啟" : "關閉" }));
      let guildCell = null;
      if (featureGuild) {
        const value = f.key in overrides ? String(overrides[f.key]) : "";
        guildCell = h("td", {}, globalOn
          ? h("select", { "aria-label": `${f.name}：${guildName}`,
              onchange: (e) => save(featureGuild, f.key, e.target.value === "" ? null : e.target.value === "true") },
              [["", "跟隨全部伺服器（開啟）"], ["true", "開啟"], ["false", "關閉"]].map(([v, t]) =>
                h("option", { value: v, text: t, selected: v === value })))
          : h("span", { class: "muted", text: "全部伺服器已關閉" }));
      }
      const off = !globalOn || overrides[f.key] === false;
      return h("tr", { class: off ? "off" : "" },
        h("td", {}, h("div", { class: "feature-name", text: `${f.emoji} ${f.name}` }), h("div", { class: "muted", text: f.description })),
        h("td", {}, globalSwitch), guildCell);
    });

    box.replaceChildren(
      h("div", { class: "card-head" }, h("h2", { text: "功能開關" }), h("span", { class: "spacer" }), picker),
      h("p", { class: "muted", text: "關掉的功能：指令會回覆「已關閉」、說明頁和大廳不再列出、自動通知（摘要、週報、提醒、天氣、地震）停止發送。" +
        "大廳、說明、清除訊息是基本功能，不能關。天氣預報和地震通報是用 Webhook 發的，只能在「所有伺服器」關閉。" }),
      h("div", { class: "table-wrap" }, h("table", { class: "features" },
        h("thead", {}, h("tr", {}, h("th", { text: "功能" }), h("th", { text: "所有伺服器" }),
          featureGuild ? h("th", { text: guildName }) : null)),
        h("tbody", {}, rows))));
  };

  view.replaceChildren(h("section", { class: "card" }, box));
  await load();
}

// ---------------------------------------------------------------- 管理操作紀錄
const ACTIONS = { login: "登入後台", points: "調整存款", streak: "修改連續簽到", reset_work: "重設打工冷卻",
                  end_game: "結束遊戲", refund_hold: "退還賭注", update: "立即更新",
                  move_task: "移動待辦", feature: "功能開關", ai_profile: "AI 個性設定" };

async function renderAudit(view) {
  const data = await api("/api/audit");
  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "管理操作紀錄" })),
    data.items.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["時間", "動作", "對象", "內容", "來源 IP"].map((t) => h("th", { text: t })))),
      h("tbody", {}, data.items.map((a) => h("tr", {}, h("td", { text: fmtTime(a.at) }), h("td", { text: ACTIONS[a.action] || a.action }),
        h("td", {}, a.user_id ? h("a", { href: `#/users/${a.user_id}`, text: a.name || a.user_id }) : "—"),
        h("td", { text: a.detail }), h("td", { class: "id", text: a.ip || "" }))))))
      : h("p", { class: "empty", text: "還沒有管理操作。" })));
}

// ---------------------------------------------------------------- 伺服器紀錄（logs/bot.log）
const logState = { level: "", q: "", live: true };

function copyText(text) {
  // 區域網路用 http 開時沒有 navigator.clipboard，退回用隱藏的文字框複製
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = h("textarea", { style: { position: "fixed", opacity: "0" } });
  area.value = text;
  document.body.append(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  return ok ? Promise.resolve() : Promise.reject(new Error("無法複製"));
}

async function renderServerLog(view) {
  const level = h("select", { "aria-label": "紀錄等級" },
    [["", "全部"], ["WARNING", "警告以上"], ["ERROR", "只看錯誤"]].map(([v, t]) =>
      h("option", { value: v, text: t, selected: logState.level === v })));
  const search = h("input", { type: "search", placeholder: "搜尋紀錄（例如 poker、天氣、Traceback）", "aria-label": "搜尋紀錄" });
  search.value = logState.q;
  const live = h("input", { type: "checkbox", id: "log-live", checked: logState.live });
  const summary = h("button", { type: "button", class: "btn small ghost", title: "只看錯誤" });
  const box = h("div", { class: "logbox", tabindex: 0, "aria-label": "伺服器紀錄" });
  let shown = [];

  const load = async () => {
    const params = new URLSearchParams({ lines: 500, level: logState.level, q: logState.q });
    const data = await api(`/api/logs?${params}`);
    shown = data.records;
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
    box.replaceChildren(...(shown.length ? shown.map((r) => h("div", { class: r.level ? `lv-${r.level}` : "", text: r.text }))
      : [h("div", { class: "muted", text: logState.q || logState.level ? "沒有符合條件的紀錄。" : "還沒有紀錄（logs/bot.log）。" })]));
    if (atBottom) box.scrollTop = box.scrollHeight;
    summary.textContent = data.errors_24h
      ? `⚠️ 最近 24 小時：${data.errors_24h} 個錯誤、${data.warnings_24h} 個警告`
      : `✅ 最近 24 小時沒有錯誤（${data.warnings_24h} 個警告）`;
    summary.classList.toggle("bad", data.errors_24h > 0);
  };

  level.addEventListener("change", () => { logState.level = level.value; load(); });
  let typing;
  search.addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(() => { logState.q = search.value.trim(); load(); }, 300);
  });
  live.addEventListener("change", () => { logState.live = live.checked; });
  summary.addEventListener("click", () => { logState.level = level.value = "ERROR"; load(); });

  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "伺服器紀錄" }), h("span", { class: "spacer" }), summary),
    h("div", { class: "toolbar", style: { marginBottom: "10px" } }, search, level,
      h("label", { class: "muted", for: "log-live" }, live, " 每 5 秒自動更新"),
      h("button", { class: "btn small", type: "button", text: "重新整理", onclick: load }),
      h("button", { class: "btn small", type: "button", text: "複製目前顯示的紀錄", onclick: () =>
        copyText(shown.map((r) => r.text).join("\n")).then(() => toast(`已複製 ${shown.length} 筆紀錄`), (e) => toast(e.message, true)) }),
      h("a", { class: "btn small", href: "/api/logs/download", text: "下載完整紀錄檔" })),
    box,
    h("p", { class: "muted", style: { marginTop: "8px" },
      text: "遇到問題時，把「只看錯誤」的內容複製起來，或下載完整紀錄檔傳給開發者，比較容易找到原因。" })));
  await load();
  box.scrollTop = box.scrollHeight;
  every(5000, () => { if (logState.live) load().catch(() => {}); });
}

// ---------------------------------------------------------------- AI 與個性
let aiScope = "0";

async function renderAI(view) {
  const data = await api("/api/ai");
  if (aiScope !== "0" && !data.guilds.some((g) => g.id === aiScope)) aiScope = "0";
  const isGlobal = aiScope === "0";
  const current = isGlobal ? { ...data.effective } : { ...data.effective, ...(data.overrides[aiScope] || {}) };
  const hasOverride = !isGlobal && !!data.overrides[aiScope];
  let dirty = false;
  const dirtyBadge = h("span", { class: "badge bad", text: "尚未儲存", hidden: true });
  const markDirty = () => { dirty = true; dirtyBadge.hidden = false; };

  // ---- 範圍
  const scope = h("select", { "aria-label": "設定範圍", onchange: (e) => {
    if (dirty && !confirm("有還沒儲存的修改，確定要切換嗎？")) { e.target.value = aiScope; return; }
    aiScope = e.target.value; render(true);
  } }, h("option", { value: "0", text: "所有伺服器（全域）" }),
    data.guilds.map((g) => h("option", { value: g.id, text: `${g.name}${data.overrides[g.id] ? "（自訂）" : ""}`, selected: g.id === aiScope })));

  // ---- 角色設定
  const name = h("input", { id: "ai-name", maxlength: data.limits.name, value: current.name || "", placeholder: `不填就用 bot 的名稱（${data.bot_name || "Bot"}）`, oninput: markDirty });
  const personality = h("textarea", { id: "ai-personality", rows: 6, maxlength: data.limits.personality });
  personality.value = current.personality || "";
  const count = h("span", { class: "count" });
  const updateCount = () => { count.textContent = `${personality.value.length} / ${data.limits.personality}`; };
  personality.addEventListener("input", () => { updateCount(); markDirty(); });
  updateCount();
  const lineBoxes = {};
  const presetChips = Object.keys(data.presets).map((key) => h("button", { class: "chip", type: "button", "aria-pressed": "false", text: key,
    onclick: (e) => {
      const preset = data.presets[key];
      personality.value = preset.personality; updateCount();
      Object.entries(lineBoxes).forEach(([intent, box]) => { box.value = (preset.lines[intent] || []).join("\n"); });
      presetChips.forEach((c) => c.setAttribute("aria-pressed", String(c === e.currentTarget)));
      markDirty(); toast(`已套用「${key}」，記得按儲存`);
    } }));

  // ---- 回覆參數
  const temp = h("input", { id: "ai-temp", type: "range", min: 0, max: 1.2, step: 0.05, value: current.temperature });
  const tempOut = h("output", { for: "ai-temp", text: Number(current.temperature).toFixed(2) });
  temp.addEventListener("input", () => { tempOut.textContent = Number(temp.value).toFixed(2); markDirty(); });
  const maxReply = h("input", { id: "ai-max", type: "number", min: 40, max: 500, step: 10, value: current.max_reply, oninput: markDirty });
  const turns = h("input", { id: "ai-turns", type: "number", min: 0, max: 30, step: 1, value: current.history_turns, oninput: markDirty });

  // ---- 叫功能台詞
  const lines = data.intents.map((it) => {
    const box = h("textarea", { rows: 3, placeholder: it.defaults.join("\n"), "aria-label": `${it.label}的台詞` });
    box.value = ((current.lines || {})[it.key] || []).join("\n");
    box.addEventListener("input", markDirty);
    lineBoxes[it.key] = box;
    const custom = (current.lines || {})[it.key];
    return h("details", { class: "lines" }, h("summary", {}, it.label, custom ? h("span", { class: "badge good", text: `自訂 ${custom.length} 句` }) : h("span", { class: "muted", text: "預設" })), box);
  });

  // ---- 其他 AI 功能的語氣
  const catchupTone = h("input", { id: "ai-catchup", maxlength: data.limits.tone, value: current.catchup_tone || "", placeholder: "例如：用輕鬆的口吻、多用表情符號", oninput: markDirty });
  const reportTone = h("input", { id: "ai-report", maxlength: data.limits.tone, value: current.report_tone || "", placeholder: "例如：語氣正式、像主管簡報", oninput: markDirty });

  const collect = () => {
    const out = {
      name: name.value, personality: personality.value, temperature: Number(temp.value),
      max_reply: Number(maxReply.value), history_turns: Number(turns.value),
      catchup_tone: catchupTone.value, report_tone: reportTone.value,
      lines: Object.fromEntries(Object.entries(lineBoxes).map(([k, box]) => [k, box.value.split("\n").map((x) => x.trim()).filter(Boolean)])
        .filter(([, v]) => v.length)),
    };
    if (isGlobal) out.model = modelSelect.value;
    return out;
  };

  // ---- 連線
  const ai = data.ai;
  const connInfo = h("div", { class: "conn" });
  const drawConn = (info, extra = "") => connInfo.replaceChildren(
    h("span", {}, "狀態　", h("b", { text: info.available ? "● 正常" : "● 無法使用", style: { color: info.available ? "var(--accent-2)" : "var(--critical)" } }), `　${info.status}`),
    h("span", {}, "服務　", h("b", { text: info.configured ? `${info.provider}・${info.url || "—"}` : "沒有設定 OLLAMA_URL" })),
    h("span", {}, "模型　", h("b", { text: info.model || "—" })), ...(extra ? [h("span", { text: extra })] : []));
  drawConn(ai);
  const modelSelect = h("select", { "aria-label": "模型", disabled: !isGlobal, onchange: markDirty },
    h("option", { value: "", text: `.env 設定（${ai.default_model || "—"}）`, selected: !current.model }),
    current.model ? h("option", { value: current.model, text: current.model, selected: true }) : null);
  const testBtn = h("button", { class: "btn small", type: "button", text: "測試連線", onclick: async () => {
    testBtn.disabled = true; testBtn.textContent = "測試中…";
    try {
      const r = await api("/api/ai/test", { method: "POST" });
      drawConn(r.ai, `回應時間　${r.ms} ms`);
      const chosen = modelSelect.value;
      modelSelect.replaceChildren(h("option", { value: "", text: `.env 設定（${r.ai.default_model || "—"}）` }),
        ...[...new Set([...r.models, chosen].filter(Boolean))].map((m) => h("option", { value: m, text: m })));
      modelSelect.value = chosen;
      toast(r.ok ? `連線正常（${r.ms} ms），找到 ${r.models.length} 個模型` : `無法使用：${r.status}`, !r.ok);
    } catch (e) { toast(e.message, true); }
    testBtn.disabled = false; testBtn.textContent = "測試連線";
  } });

  // ---- 試聊
  const chat = h("div", { class: "chat", "aria-live": "polite" },
    h("div", { class: "bubble bot", text: "在這裡試試看：用還沒儲存的設定請 AI 回一句話，也會顯示判斷出的功能。" }));
  const ask = h("input", { id: "ai-ask", placeholder: "跟機器人說一句話", maxlength: 300 });
  const send = h("button", { class: "btn primary", type: "button", text: "試聊" });
  const go = async () => {
    const text = ask.value.trim();
    if (!text) return;
    ask.value = "";
    chat.append(h("div", { class: "bubble me", text }));
    const bubble = h("div", { class: "bubble bot" }, h("span", { class: "dots" }, h("i"), h("i"), h("i")));
    chat.append(bubble); chat.scrollTop = chat.scrollHeight;
    send.disabled = true;
    try {
      const r = await api("/api/ai/preview", { method: "POST", body: { guild: aiScope, text, data: collect() } });
      const body = h("span");
      bubble.replaceChildren(body);
      await Motion.typewriter(body, `${r.name}：${r.reply}`);
      bubble.append(h("span", { class: "meta", text: `判斷：${r.intent_label}・${r.ms} ms・試聊不會儲存` }));
    } catch (e) {
      bubble.classList.add("bad"); bubble.textContent = e.message;
    }
    chat.scrollTop = chat.scrollHeight;
    send.disabled = false;
  };
  send.addEventListener("click", go);
  ask.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });

  // ---- 儲存、恢復預設、版本
  const save = h("button", { class: "btn primary morph", type: "button", text: "儲存" });
  save.addEventListener("click", async () => {
    if (save.classList.contains("loading")) return;
    save.classList.add("loading");
    try {
      await api("/api/ai/profile", { method: "POST", body: { guild: aiScope, data: collect(), note: "後台儲存" } });
      save.classList.replace("loading", "done");
      dirty = false;
      setTimeout(() => render(true), 900);
    } catch (e) { save.classList.remove("loading"); toast(e.message, true); }
  });
  const reset = h("button", { class: "btn danger", type: "button", text: isGlobal ? "全部恢復預設" : "改回跟隨全域", disabled: !isGlobal ? !hasOverride : false,
    onclick: async () => {
      if (!confirm(isGlobal ? "確定把所有伺服器共用的 AI 設定恢復成程式預設嗎？（可以從版本紀錄還原）" : "確定讓這個伺服器改回跟隨全域設定嗎？")) return;
      try { await api("/api/ai/profile", { method: "POST", body: { guild: aiScope, data: null } }); dirty = false; toast("已恢復"); render(true); }
      catch (e) { toast(e.message, true); }
    } });
  const versions = h("div", { class: "versions" }, h("p", { class: "empty", text: "載入中…" }));
  api(`/api/ai/history?guild=${aiScope}`).then((r) => {
    versions.replaceChildren(...(r.items.length ? r.items.map((v, i) => h("div", { class: "v" },
      h("span", { class: "when", text: fmtTime(v.at) }),
      h("span", { class: "note", text: `${v.note || "儲存"}${v.data.name ? `・${v.data.name}` : ""}` }),
      i === 0 ? h("span", { class: "badge good", text: "目前" }) : h("button", { class: "btn small", type: "button", text: "還原", onclick: async () => {
        if (!confirm(`確定還原到 ${fmtTime(v.at)} 的版本嗎？`)) return;
        try { await api("/api/ai/restore", { method: "POST", body: { id: v.id } }); toast("已還原"); render(true); }
        catch (e) { toast(e.message, true); }
      } })))
      : [h("p", { class: "empty", text: "還沒有儲存過。" })]));
  }).catch(() => versions.replaceChildren(h("p", { class: "empty", text: "讀不到版本紀錄。" })));

  const field = (label, control, hint) => h("div", { class: "field" }, h("label", { for: control.id, text: label }), control, hint ? h("span", { class: "hint", text: hint }) : null);
  view.replaceChildren(
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "AI 與個性" }), dirtyBadge, h("span", { class: "spacer" }),
      h("label", { class: "inline" }, "設定範圍 ", scope)),
      h("p", { class: "muted", text: isGlobal
        ? "這裡的設定給所有伺服器用。某個伺服器想要不同的個性，在右上角選那個伺服器另外設定。"
        : hasOverride ? "這個伺服器有自己的設定（模型仍跟隨全域）。" : "這個伺服器目前跟隨全域設定；在這裡儲存後就會有自己的個性。" })),
    h("div", { class: "ai-layout" },
      h("div", { style: { display: "grid", gap: "16px", minWidth: "0" } },
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "角色設定" })),
          h("div", { class: "chips", role: "group", "aria-label": "風格範本" }, presetChips),
          field("機器人稱呼", name, "聊天時機器人怎麼稱呼自己。"),
          h("div", { class: "field" }, h("label", { for: "ai-personality", text: "個性描述" }), personality, count),
          h("div", { class: "locked", text: `🔒 固定安全規則（每次都會附加，不能修改）\n${data.safety}` })),
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "回覆參數" })),
          h("div", { class: "params" },
            h("div", { class: "field" }, h("label", { for: "ai-temp" }, "創意程度 ", tempOut), temp, h("span", { class: "hint", text: "越高越天馬行空，越低越穩定" })),
            field("回覆長度上限（字）", maxReply),
            field("記住幾句對話", turns))),
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "叫功能時的台詞" })),
          h("p", { class: "muted", text: "有人用講的叫功能時，機器人會從這裡隨機挑一句再附上按鈕。一行一句，留空就用預設（灰字）。" }), lines),
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "其他 AI 功能的語氣" })),
          h("p", { class: "muted", text: "只會補充語氣，不會改到判斷規則（海龜湯判題不開放修改）。" }),
          field("我錯過了什麼", catchupTone), field("每週週報", reportTone)),
        h("div", { class: "save-bar" }, reset, save)),
      h("div", { class: "ai-side" },
        h("section", { class: "card beam" }, h("div", { class: "card-head" }, h("h2", { text: "試聊" })), chat, h("div", { class: "chat-input" }, ask, send)),
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "AI 連線" }), h("span", { class: "spacer" }), testBtn),
          connInfo, h("div", { class: "field", style: { marginTop: "12px" } }, h("label", { text: isGlobal ? "使用的模型（按「測試連線」載入清單）" : "模型只能在全域設定" }), modelSelect)),
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "版本紀錄" })), versions))),
  );
  const leave = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", leave);
  onLeave(() => removeEventListener("beforeunload", leave));
}

// ---------------------------------------------------------------- 更新日誌
let changelogPage = 1;

async function renderChangelog(view) {
  const data = await api(`/api/changelog?page=${changelogPage}`);
  changelogPage = data.page;
  const go = (p) => { changelogPage = p; render(true); };
  view.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "更新日誌" }), h("span", { class: "muted", text: `共 ${fmt(data.total)} 筆更新，最新的在最上面` })),
    h("div", { class: "timeline" }, data.items.map((e, i) => {
      const entry = h("article", { class: "entry" },
        h("div", { class: "when" }, `${e.date}・`, e.url ? h("a", { href: e.url, target: "_blank", rel: "noopener", text: e.short }) : e.short),
        h("h3", { text: e.title }),
        e.details.length ? h("ul", {}, e.details.map((d) => h("li", { text: d }))) : null);
      entry.style.setProperty("--i", i);
      return entry;
    })),
    h("div", { class: "pager" }, `第 ${data.page} / ${data.pages} 頁`,
      h("button", { class: "btn small", type: "button", text: "⏮ 最新", disabled: data.page <= 1, onclick: () => go(1) }),
      h("button", { class: "btn small", type: "button", text: "◀ 上一頁", disabled: data.page <= 1, onclick: () => go(data.page - 1) }),
      h("button", { class: "btn small", type: "button", text: "下一頁 ▶", disabled: data.page >= data.pages, onclick: () => go(data.page + 1) }),
      h("button", { class: "btn small", type: "button", text: "最舊 ⏭", disabled: data.page >= data.pages, onclick: () => go(data.pages) }))));
}

// ---------------------------------------------------------------- Ctrl+K 指令面板
let palette = null;

function openPalette() {
  if (palette || $("#app").hidden) return;
  const input = h("input", { placeholder: "搜尋頁面、使用者、伺服器…", "aria-label": "搜尋" });
  const list = h("ul", { role: "listbox" });
  let items = [], sel = 0, guilds = [], users = [], typing;
  const pages = Object.entries(PAGES).map(([key, label]) => ({ label, hint: "頁面", go: () => (location.hash = `#/${key}`) }));
  const close = () => { palette?.remove(); palette = null; };
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    const match = (t) => !q || t.toLowerCase().includes(q);
    items = [...pages.filter((p) => match(p.label)),
      ...guilds.filter((g) => match(g.name)).map((g) => ({ label: g.name, hint: "伺服器・功能開關", go: () => { featureGuild = g.id; if (location.hash === "#/features") render(true); else location.hash = "#/features"; } })),
      ...users.map((u) => ({ label: u.name || u.id, hint: `使用者・${fmt(u.points)}`, go: () => (location.hash = `#/users/${u.id}`) }))].slice(0, 12);
    sel = Math.min(sel, Math.max(0, items.length - 1));
    list.replaceChildren(...(items.length ? items.map((it, i) => {
      const li = h("li", { class: i === sel ? "sel" : "", role: "option", onclick: () => { close(); it.go(); } }, it.label, h("small", { text: it.hint }));
      li.style.animationDelay = `${i * 25}ms`;
      return li;
    }) : [h("li", { text: "找不到符合的項目" })]));
  };
  input.addEventListener("input", () => {
    sel = 0; draw();
    clearTimeout(typing);
    const q = input.value.trim();
    if (q.length < 1) { users = []; return; }
    typing = setTimeout(async () => {
      try { users = (await api(`/api/users?${new URLSearchParams({ q })}`)).items.slice(0, 6); draw(); } catch (e) { /* 忽略 */ }
    }, 250);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowDown") { sel = Math.min(sel + 1, items.length - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && items[sel]) { const it = items[sel]; close(); it.go(); }
  });
  palette = h("div", { class: "palette-backdrop", onclick: (e) => { if (e.target === palette) close(); } },
    h("div", { class: "palette", role: "dialog", "aria-label": "搜尋" }, input, list));
  document.body.append(palette);
  draw(); input.focus();
  api("/api/pulse").then((p) => { guilds = p.guilds; draw(); }).catch(() => {});
}
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
});
$("#palette-open").addEventListener("click", openPalette);

// ---------------------------------------------------------------- 啟動
(async () => {
  try {
    await api("/api/session");
    startApp();
  } catch (e) {
    showLogin();
  }
})();
