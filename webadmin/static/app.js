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

// ---------------------------------------------------------------- 動畫開關
(() => {
  const toggle = $("#motion-toggle");
  toggle.textContent = Motion.reduce ? "✨ 開啟動畫" : "動畫：開";
  toggle.title = Motion.reduce ? "目前是簡化動畫，按這裡開啟完整動畫" : "按這裡關閉動畫（記在這台電腦的瀏覽器）";
  toggle.addEventListener("click", () => Motion.setPreference(Motion.reduce ? "on" : "off"));
  // 系統開了「減少動態效果」又還沒選過：提示一次可以打開
  if (document.documentElement.classList.contains("rm-sys")) {
    const note = h("div", { class: "motion-note", role: "status" },
      h("p", { text: "你的電腦開了「減少動態效果」（Windows：設定 → 協助工具 → 視覺效果 → 動畫效果），所以後台先用簡化動畫。" }),
      h("button", { class: "btn small primary", type: "button", text: "開啟完整動畫", onclick: () => Motion.setPreference("on") }),
      h("button", { class: "btn small ghost", type: "button", text: "維持簡化",
        onclick: () => { try { localStorage.setItem("motion", "off"); } catch (e) { /* 忽略 */ } note.remove(); } }));
    document.body.append(note);
  }
  Motion.magnetic($("#update-now"));
})();

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

const PAGES = { overview: "總覽", users: "使用者", games: "遊戲牌局", stocks: "股票", features: "功能開關",
                guilds: "伺服器設定", board: "待辦看板", music: "音樂", minecraft: "Minecraft",
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
    else if (page === "guilds") await renderGuildSettings(view, id);
    else if (page === "music") await renderMusic(view);
    else if (page === "stocks") await renderStocks(view);
    else if (page === "minecraft") await renderMinecraft(view);
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
    checkVersion(bot.version);
  } catch (e) { /* 狀態列失敗不影響頁面 */ }
}

// bot 更新重開後，開著的後台還是舊的網頁程式：發現版本變了就重新載入。
// 正在打字時不強制重載，改成在狀態列放一顆「重新整理」按鈕，免得內容不見。
let loadedVersion = null;
function checkVersion(version) {
  if (!version || version === "?") return;
  if (loadedVersion === null) { loadedVersion = version; return; }
  if (version === loadedVersion) return;
  const typing = document.activeElement && document.activeElement.matches("input, textarea, select, [contenteditable]");
  if (!typing) {
    toast(`後台已更新到 ${version}，重新載入中…`);
    setTimeout(() => location.reload(), 2000);
    return;
  }
  if ($("#reload-new")) return;
  const btn = h("button", { id: "reload-new", class: "btn small primary", type: "button",
                            text: `⟳ 有新版本 ${version}，點這裡重新整理`, onclick: () => location.reload() });
  $("#bot-status").after(btn);
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
    // 滑鼠移上去：發光十字準星對準那天的柱頂
    const guide = s("g", { class: "xhair", visibility: "hidden" });
    guide.append(s("line", { class: "xh", x1: m.l, x2: W - m.r, y1: 0, y2: 0 }), s("line", { class: "xv", x1: 0, x2: 0, y1: m.t, y2: m.t + innerH }),
                 s("circle", { class: "xd", r: 4.5, cx: 0, cy: 0 }));
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
        const topY = m.t + innerH - (totals[i] / max) * innerH, cx = x + bw / 2;
        guide.setAttribute("visibility", "visible");
        guide.children[0].setAttribute("y1", topY); guide.children[0].setAttribute("y2", topY);
        guide.children[1].setAttribute("x1", cx); guide.children[1].setAttribute("x2", cx);
        guide.children[2].setAttribute("cx", cx); guide.children[2].setAttribute("cy", topY);
        showTip(event, [
          h("div", { class: "title", text: `${day.day.replaceAll("-", "/")}　共 ${fmt(totals[i])} 分` }),
          ...SERIES.slice().reverse().map((ser) => h("div", { class: "row" },
            h("span", { class: "swatch", style: { background: cssVar(ser.color) } }), ser.label,
            h("span", { class: "num", text: fmt(day[ser.key] || 0) }))),
        ]);
      });
      hit.addEventListener("mouseleave", () => { columns.forEach((c) => c.classList.remove("dim")); guide.setAttribute("visibility", "hidden"); hideTip(); });
      svg.append(hit);
    });
    svg.insertBefore(guide, svg.querySelector(".hit"));
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

// 每分鐘指令數：第一次由底部彈跳長出來；滑鼠移上去有發光十字準星與提示框
function liveChart(svg, values, grow = false) {
  const W = 400, H = 190, n = values.length, step = W / Math.max(1, n - 1);
  const max = niceMax(Math.max(4, ...values));
  const pts = values.map((v, i) => [i * step, H - 16 - (v / max) * (H - 30)]);
  const line = pts.map((p) => p.map((x) => x.toFixed(1)).join(",")).join(" ");
  const peak = values.indexOf(Math.max(...values)), [px, py] = pts[peak], [lx, ly] = pts[n - 1];
  svg._chart = { values, pts, W, H };
  svg.replaceChildren();
  const defs = s("defs");
  const fill = s("linearGradient", { id: "lfill", x1: 0, x2: 0, y1: 0, y2: 1 });
  fill.append(s("stop", { offset: 0, "stop-color": "#35d9ff", "stop-opacity": ".45" }), s("stop", { offset: 1, "stop-color": "#b26bff", "stop-opacity": "0" }));
  const stroke = s("linearGradient", { id: "lstroke", x1: 0, x2: 1 });
  stroke.append(s("stop", { offset: 0, "stop-color": "#b26bff" }), s("stop", { offset: 1, "stop-color": "#35d9ff" }));
  defs.append(fill, stroke);
  svg.append(defs);
  [.25, .5, .75].forEach((f) => svg.append(s("line", { x1: 0, x2: W, y1: H * f, y2: H * f, stroke: "rgba(80,220,255,.1)" })));
  const body = s("g", { class: grow ? "grow-spring" : "" });
  body.append(s("polygon", { points: `0,${H} ${line} ${W},${H}`, fill: "url(#lfill)" }));
  body.append(s("polyline", { points: line, fill: "none", stroke: "url(#lstroke)", "stroke-width": 2.4, "vector-effect": "non-scaling-stroke" }));
  svg.append(body);
  if (values[peak] > 0) {
    svg.append(s("line", { x1: px, x2: px, y1: py, y2: H, stroke: "rgba(255,200,87,.5)", "stroke-dasharray": "3 3" }));
    svg.append(s("circle", { cx: px, cy: py, r: 3.5, fill: "#ffc857" }));
    svg.append(s("text", { x: Math.min(px + 6, W - 80), y: Math.max(py - 8, 12), fill: "#ffc857", "font-size": 11, "font-family": "Consolas, monospace", text: `PEAK ${values[peak]}/分` }));
  }
  svg.append(s("line", { x1: lx, x2: lx, y1: 0, y2: H, stroke: "rgba(124,255,156,.35)" }));
  const dot = s("circle", { cx: lx, cy: ly, r: 4, fill: "#7cff9c" });
  if (!Motion.reduce) dot.append(s("animate", { attributeName: "r", values: "3;7;3", dur: "1s", repeatCount: "indefinite" }));
  svg.append(dot);
  // 十字準星（滑鼠移上去才出現）
  const xh = s("g", { class: "xhair", visibility: svg._hover == null ? "hidden" : "visible" });
  xh.append(s("line", { class: "xv", x1: 0, x2: 0, y1: 0, y2: H }), s("line", { class: "xh", x1: 0, x2: W, y1: 0, y2: 0 }),
            s("circle", { class: "xd", r: 4.5, cx: 0, cy: 0 }));
  svg.append(xh);
  if (svg._hover != null) placeCrosshair(svg, svg._hover);
  if (!svg._hooked) {
    svg._hooked = true;
    svg.addEventListener("pointermove", (event) => {
      const r = svg.getBoundingClientRect(), { values: vs } = svg._chart;
      const i = Math.round(((event.clientX - r.left) / r.width) * (vs.length - 1));
      svg._hover = Math.max(0, Math.min(vs.length - 1, i));
      placeCrosshair(svg, svg._hover);
      const ago = vs.length - 1 - svg._hover;
      showTip(event, [h("div", { class: "title", text: ago ? `${ago} 分鐘前` : "這一分鐘" }),
        h("div", { class: "row" }, h("span", { class: "swatch", style: { background: "#35d9ff", color: "#35d9ff" } }), "指令數",
          h("span", { class: "num", text: `${vs[svg._hover]} 次` }))]);
    });
    svg.addEventListener("pointerleave", () => { svg._hover = null; const x = svg.querySelector(".xhair"); if (x) x.setAttribute("visibility", "hidden"); hideTip(); });
  }
}
function placeCrosshair(svg, i) {
  const xh = svg.querySelector(".xhair"), [x, y] = svg._chart.pts[i];
  xh.setAttribute("visibility", "visible");
  xh.querySelector(".xv").setAttribute("x1", x); xh.querySelector(".xv").setAttribute("x2", x);
  xh.querySelector(".xh").setAttribute("y1", y); xh.querySelector(".xh").setAttribute("y2", y);
  xh.querySelector(".xd").setAttribute("cx", x); xh.querySelector(".xd").setAttribute("cy", y);
}

async function renderOverview(view) {
  const data = await api("/api/overview");
  const { kpi, days, games, bot } = data;
  setBotInfo(bot);
  const totals = days.map((d) => SERIES.reduce((sum, ser) => sum + (d[ser.key] || 0), 0));
  const intro = [];   // 進場時要逐一出現的卡片

  // ---- 主視覺：粒子星雲背景＋核心全像
  const nebula = h("canvas", { class: "nebula", "aria-hidden": "true" });
  const holoCanvas = h("canvas", { "aria-label": "機器人核心：中間是核心，外圈每個光點是一個伺服器，有人使用指令時會射出資料封包" });
  const uptime = h("b", { text: fmtUptime(bot.uptime) });
  const gaugeBox = h("div", { class: "gauges" });
  const gLatency = Motion.gauge("延遲 ms", "var(--accent)");
  const gActive = Motion.gauge("今日活躍 %", "var(--accent-3)");
  const gCheckin = Motion.gauge("今日簽到 %", "var(--accent-2)");
  gaugeBox.append(gLatency.el, gActive.el, gCheckin.el);
  const setGauges = (b, k, delay = 0) => {
    gLatency.set((b.latency_ms ?? 0) / 500, b.latency_ms ?? "—", { delay });
    const pct = (n) => (k.users ? Math.round((n / k.users) * 100) : 0);
    gActive.set(pct(k.active_today) / 100, pct(k.active_today), { delay: delay + 120 });
    gCheckin.set(pct(k.checkins_today) / 100, pct(k.checkins_today), { delay: delay + 240 });
  };
  const sys = h("div", { class: "sys" + (bot.online ? "" : " off"), text: bot.online ? "System online" : "System offline" });
  const name = (bot.display || "BOT").toUpperCase();
  const title = h("div", { class: "glitch", "data-text": name, text: name });
  const aiName = h("b", { text: bot.ai_configured ? bot.ai_name : "未設定" }), version = h("b", { text: bot.version });
  const guildCount = h("b", { text: "0" });
  const replay = h("button", { class: "replay", type: "button", text: "↻ 重播開機動畫",
    onclick: async () => { await playBoot(); render(true); } });
  const core = h("section", { class: "card core scan" }, nebula, holoCanvas,
    h("div", { class: "core-info" }, sys, title,
      h("div", { class: "stat-lines" },
        h("span", {}, "運行　", uptime),
        h("span", {}, "伺服器 ", guildCount, "・AI ", aiName),
        h("span", {}, "版本 ", version, "・", h("b", { text: bot.ai ? "AI 正常" : "AI 暫停" }))),
      gaugeBox, replay));
  intro.push(core);

  // ---- 數字卡片
  const kpiCard = (label, value, sub, sparkValues, cls = "", color) => {
    const v = h("div", { class: "value", text: "0" });
    const card = h("div", { class: `card kpi ${cls}` }, h("div", { class: "label", text: label }), v, h("div", { class: "sub", text: sub }),
      Motion.spark(sparkValues, color));
    intro.push(card);
    return { card, v, value };
  };
  const kUsers = kpiCard("使用者", kpi.users, `USERS・今日活躍 ${fmt(kpi.active_today)}`, days.map((d) => d.active));
  const kPoints = kpiCard("流通存款", kpi.points, `保管中 ${fmt(kpi.held)}`, totals, "", "var(--accent-3)");
  const kCmds = kpiCard("今日指令", 0, "CMDS・bot 啟動後累計", [0], "good", "var(--accent-2)");
  const kErr = kpiCard("錯誤", 0, "ERRORS・近 24 小時", [0], "bad", "var(--critical)");

  // ---- 即時圖表與紀錄
  const liveSvg = s("svg", { viewBox: "0 0 400 190", preserveAspectRatio: "none", role: "img", "aria-label": "最近 60 分鐘每分鐘指令數" });
  const term = h("div", { class: "term", "aria-live": "off" });
  const fullLog = h("a", { class: "btn small", href: "#/serverlog", text: "完整紀錄" });
  const termCard = h("section", { class: "card term-card" }, h("div", { class: "card-head" },
    h("span", { class: "live-dot", title: "即時連線中" }), h("h2", { text: "伺服器紀錄・即時" }),
    h("span", { class: "spacer" }), fullLog), term);
  const liveCard = h("section", { class: "card live" }, h("div", { class: "card-head" }, h("h2", { text: "每分鐘指令數・最近 60 分鐘" })), liveSvg);
  intro.push(liveCard, termCard);
  // 中段跑馬燈：上排往左、下排往右
  const trackA = h("div", { class: "track", "data-dir": "left" }), trackB = h("div", { class: "track", "data-dir": "right" });
  const ticker = h("div", { class: "ticker", "aria-hidden": "true" }, h("div", { class: "row" }, trackA), h("div", { class: "row alt" }, trackB));

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
  const payoutCard = h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "每日發放金額・近 14 天" }), h("span", { class: "spacer" }), toggle),
    h("div", { class: "legend" }, SERIES.map((ser) =>
      h("span", {}, h("span", { class: "swatch", style: { background: cssVar(ser.color), color: cssVar(ser.color) } }), ser.label))),
    chartBox, tableBox,
    h("p", { class: "muted", text: "遊戲輸贏不計入發放（是玩家之間或與莊家的移轉），請看下方各遊戲損益。" }));
  const gamesCard = h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: "各遊戲玩家累計損益" }),
      h("span", { class: "muted", text: "正數＝玩家整體贏、莊家發出存款；負數＝玩家整體輸、存款被回收" })),
    games.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["遊戲", "場次", "玩家數", "勝率", "損益分布", "玩家累計損益"].map((t, i) =>
        h("th", { class: [1, 2, 3, 5].includes(i) ? "num" : "", text: t })))),
      h("tbody", {}, gameRows)))
      : h("p", { class: "empty", text: "還沒有遊戲紀錄。" }));
  intro.push(ticker, payoutCard, gamesCard);

  view.replaceChildren(
    core,
    h("div", { class: "kpis" }, kUsers.card, kPoints.card, kCmds.card, kErr.card),
    h("div", { class: "home-row" }, liveCard, termCard),
    ticker, payoutCard, gamesCard,
  );
  view.classList.add("home");
  onLeave(() => view.classList.remove("home"));
  stackedColumns(chartBox, days);

  // ---- 特效：流光繞邊、3D 傾斜聚光燈、磁吸按鈕
  [core, kUsers.card, kPoints.card, kCmds.card, kErr.card].forEach((el) => Motion.beam(el));
  [core, kUsers.card, kPoints.card, kCmds.card, kErr.card, liveCard, termCard, payoutCard, gamesCard].forEach((el) => Motion.tilt(el, el.classList.contains("kpi") ? 9 : 5));
  Motion.magnetic(replay, fullLog, toggle);
  const nebulaFx = new Motion.Nebula(nebula, core);
  const holo = new Motion.Holo(holoCanvas, core);
  holo.setNodes(bot.guilds);
  onLeave(() => { holo.stop(); nebulaFx.stop(); });

  // ---- 開機序列：掃描光束 → 卡片交錯浮現 → 標題解碼 → 數字滾動、儀表充能
  Motion.laser();
  Motion.stagger(intro, 60);
  Motion.decode(title, name, { delay: 250, duration: 1000 });
  Motion.decode(version, bot.version, { delay: 500, duration: 800 });
  Motion.decode(aiName, aiName.textContent, { delay: 600, duration: 800 });
  Motion.countUp(guildCount, bot.guilds, { delay: 300, duration: 1200 });
  [kUsers, kPoints].forEach((k, i) => Motion.countUp(k.v, k.value, { delay: 200 + i * 80, duration: 1600 }));
  setGauges(bot, kpi, 350);

  // ---- 即時更新：每 3 秒讀 /api/pulse
  let seq = null, lastLog = null, guildIndex = {};
  const swapSpark = (card, values, color) => card.querySelector(".spark-wrap").replaceWith(Motion.spark(values, color));
  const tickerItems = (pulse) => {
    const stats = [
      ["伺服器", `${pulse.guilds.length} 個`], ["今日簽到", `${fmt(kpi.checkins_today)} 人`], ["今日活躍", `${fmt(kpi.active_today)} 人`],
      ["流通存款", fmt(kpi.points)], ["AI", bot.ai ? "正常" : "暫停"], ["版本", bot.version],
    ];
    const guildsRow = pulse.guilds.slice(0, 16).map((g) => [g.name, g.members ? `${fmt(g.members)} 位成員` : "已連線"]);
    const fillTrack = (track, items) => {
      // 內容至少要比畫面寬，再複製一份做無縫循環
      let once = items.map(([a, b]) => h("span", {}, h("b", { text: a }), "　", h("span", { class: "up", text: b })));
      while (once.length && once.length < 10) once = once.concat(once.map((n) => n.cloneNode(true)));
      track.replaceChildren(...once, ...once.map((n) => n.cloneNode(true)));
    };
    fillTrack(trackA, stats);
    fillTrack(trackB, guildsRow.length ? guildsRow : [["伺服器", "等待連線…"]]);
  };
  const pulse = async (firstTime) => {
    const p = await api(`/api/pulse?since=${seq ?? 0}`);
    p.guilds.forEach((g, i) => { guildIndex[g.id] = i; });
    holo.setNodes(p.guilds.length || bot.guilds);
    if (!firstTime) p.events.forEach((e, i) => setTimeout(() => holo.packet(guildIndex[e.guild] ?? i), i * 150));
    seq = p.seq;
    liveChart(liveSvg, p.per_minute, firstTime);
    Motion.countUp(kCmds.v, p.today_commands, firstTime ? { delay: 360, duration: 1600 } : { duration: 700 });
    Motion.countUp(kErr.v, p.errors_24h, firstTime ? { delay: 440, duration: 1600 } : { duration: 700 });
    if (firstTime) {
      swapSpark(kCmds.card, p.per_minute, "var(--accent-2)");
      swapSpark(kErr.card, p.error_hours, "var(--critical)");
      tickerItems(p);
      onLeave(Motion.kinetic(ticker));
    }
    const key = (r) => `${r.time}|${r.text}`;
    const start = lastLog ? p.logs.findIndex((r) => key(r) === lastLog) + 1 : 0;
    const fresh = lastLog && start === 0 ? p.logs : p.logs.slice(start);
    let alarm = false;
    fresh.forEach((r, i) => {
      const bad = r.level === "ERROR" || r.level === "CRITICAL";
      if (bad && !firstTime) alarm = true;
      const row = h("div", { class: (bad ? "err " : "") + (firstTime ? "" : "new") },
        h("span", { class: "t", text: `${r.time} ` }), h("span", { class: r.level, text: `${r.level.padEnd(7)} ` }), r.text);
      row.style.setProperty("--i", i);
      term.append(row);
    });
    while (term.children.length > 12) term.firstChild.remove();
    if (!p.logs.length && !term.children.length) term.append(h("div", { class: "t", text: "還沒有紀錄（logs/bot.log）" }));
    if (p.logs.length) lastLog = key(p.logs[p.logs.length - 1]);
    if (alarm) { Motion.alarm(termCard); holo.packet(0, true); }
  };
  await pulse(true).catch(() => liveChart(liveSvg, new Array(60).fill(0), true));
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
let featureQuery = "";
const FEATURE_FOLD_KEY = "features.folded";
function foldedGroups() {
  try { return new Set(JSON.parse(localStorage.getItem(FEATURE_FOLD_KEY) || "[]")); } catch (e) { return new Set(); }
}
function saveFolded(set) {
  try { localStorage.setItem(FEATURE_FOLD_KEY, JSON.stringify([...set])); } catch (e) { /* 存不了就算了 */ }
}

async function renderFeatures(view) {
  const box = h("div");
  const folded = foldedGroups();
  const post = (guild, feature, enabled) => api("/api/features", { method: "POST", body: { guild, feature, enabled } });
  const save = async (guild, feature, enabled) => {
    try { await post(guild, feature, enabled); toast("已儲存，立即生效"); } catch (e) { toast(e.message, true); }
    await load();
  };
  // 整組一次開或關：只送狀態真的要改的功能
  const saveGroup = async (changes) => {
    if (!changes.length) { toast("這一組已經是這個狀態了"); return; }
    try {
      for (const [guild, feature, enabled] of changes) await post(guild, feature, enabled);
      toast(`已更新 ${changes.length} 個功能，立即生效`);
    } catch (e) { toast(e.message, true); }
    await load();
  };

  const search = h("input", { type: "search", class: "feature-search", placeholder: "搜尋功能…", value: featureQuery,
    "aria-label": "搜尋功能", oninput: (e) => { featureQuery = e.target.value; applyFilter(); } });
  const applyFilter = () => {
    const q = featureQuery.trim().toLowerCase();
    box.querySelectorAll("tbody.feature-group").forEach((group) => {
      let shown = 0;
      group.querySelectorAll("tr.feature-row").forEach((row) => {
        const hit = !q || row.dataset.search.includes(q);
        row.hidden = !hit || (!q && group.classList.contains("folded"));
        if (hit) shown++;
      });
      group.hidden = q && !shown;
    });
  };

  const load = async () => {
    const data = await api("/api/features");
    if (featureGuild && !data.guilds.some((g) => g.id === featureGuild)) featureGuild = "";
    const overrides = data.overrides[featureGuild] || {};
    const guildName = (data.guilds.find((g) => g.id === featureGuild) || {}).name;
    const isOn = (f) => data.global[f.key] && (!featureGuild || overrides[f.key] !== false);

    const picker = h("label", { class: "inline" }, "個別伺服器 ",
      h("select", { onchange: (e) => { featureGuild = e.target.value; load(); } },
        h("option", { value: "", text: "（不選，只看全部伺服器）" }),
        data.guilds.map((g) => h("option", { value: g.id, text: g.name, selected: g.id === featureGuild }))));

    const row = (f) => {
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
      return h("tr", { class: "feature-row" + (isOn(f) ? "" : " off"), "data-search": `${f.name} ${f.description}`.toLowerCase() },
        h("td", {}, h("div", { class: "feature-name", text: `${f.emoji} ${f.name}` }), h("div", { class: "muted", text: f.description })),
        h("td", {}, globalSwitch), guildCell);
    };

    const cols = featureGuild ? 3 : 2;
    const groups = data.categories.map((c) => {
      const items = data.features.filter((f) => f.category === c.key);
      if (!items.length) return null;
      const on = items.filter(isOn).length;
      // 整組開關：選了伺服器就作用在那個伺服器（全域關掉的略過），否則作用在所有伺服器
      const changesFor = (want) => items.filter((f) => isOn(f) !== want && (!featureGuild || data.global[f.key]))
        .map((f) => featureGuild ? [featureGuild, f.key, want ? null : false] : ["0", f.key, want]);
      const tbody = h("tbody", { class: "feature-group" + (folded.has(c.key) ? " folded" : "") });
      const toggleFold = () => {
        tbody.classList.toggle("folded");
        if (tbody.classList.contains("folded")) folded.add(c.key); else folded.delete(c.key);
        saveFolded(folded);
        head.querySelector(".fold").setAttribute("aria-expanded", String(!tbody.classList.contains("folded")));
        applyFilter();
      };
      const head = h("tr", { class: "group-head" }, h("th", { colspan: cols },
        h("div", { class: "group-bar" },
          h("button", { class: "fold", type: "button", "aria-expanded": String(!folded.has(c.key)), onclick: toggleFold },
            h("span", { class: "chev", text: "▸" }), h("span", { class: "group-name", text: `${c.emoji} ${c.name}` }),
            h("span", { class: "group-count" + (on === items.length ? " all" : on ? "" : " none"), text: `${on}／${items.length} 開啟` })),
          h("span", { class: "spacer" }),
          h("span", { class: "group-actions" },
            h("button", { class: "btn small", type: "button", text: "全部開啟", onclick: () => saveGroup(changesFor(true)) }),
            h("button", { class: "btn small ghost", type: "button", text: "全部關閉", onclick: () => saveGroup(changesFor(false)) })))));
      tbody.append(head, ...items.map(row));
      return tbody;
    });

    box.replaceChildren(
      h("div", { class: "card-head" }, h("h2", { text: "功能開關" }), h("span", { class: "spacer" }), search, picker),
      h("p", { class: "muted", text: "關掉的功能：指令會回覆「已關閉」、說明頁和大廳不再列出、自動通知（摘要、週報、提醒、天氣、地震）停止發送。" +
        "大廳、說明、清除訊息是基本功能，不能關。天氣預報和地震通報是用 Webhook 發的，只能在「所有伺服器」關閉。" }),
      h("div", { class: "table-wrap" }, h("table", { class: "features" },
        h("thead", {}, h("tr", {}, h("th", { text: "功能" }), h("th", { text: "所有伺服器" }),
          featureGuild ? h("th", { text: guildName }) : null)),
        groups)));
    applyFilter();
  };

  view.replaceChildren(h("section", { class: "card" }, box));
  await load();
}

// ---------------------------------------------------------------- 管理操作紀錄
const ACTIONS = { login: "登入後台", points: "調整存款", streak: "修改連續簽到", reset_work: "重設打工冷卻",
                  end_game: "結束遊戲", refund_hold: "退還賭注", update: "立即更新",
                  move_task: "移動待辦", feature: "功能開關", ai_profile: "AI 個性設定",
                  guild_settings: "伺服器設定", music: "音樂控制", stock_cancel: "取消股票委託", minecraft: "Minecraft" };

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

// ---------------------------------------------------------------- 共用：會變形的儲存按鈕
function morphButton(label, action) {
  const btn = h("button", { class: "btn primary morph", type: "button", text: label });
  btn.addEventListener("click", async () => {
    if (btn.classList.contains("loading")) return;
    btn.classList.add("loading");
    try {
      await action();
      btn.classList.replace("loading", "done");
      setTimeout(() => btn.classList.remove("done"), 1400);
    } catch (e) { btn.classList.remove("loading"); toast(e.message, true); }
  });
  return btn;
}

// ---------------------------------------------------------------- 伺服器設定（天氣地點、每日預報、警報、地震）
async function renderGuildSettings(view, id) {
  const { guilds } = await api("/api/guilds");
  if (!guilds.length) {
    view.replaceChildren(h("section", { class: "card" }, h("p", { class: "empty", text: "bot 目前沒有加入任何伺服器。" })));
    return;
  }
  const guildId = guilds.some((g) => g.id === id) ? id : guilds[0].id;
  const data = await api(`/api/guilds/${guildId}/settings`);

  const picker = h("div", { class: "guild-list" }, guilds.map((g) =>
    h("a", { href: `#/guilds/${g.id}`, class: "guild-pill" + (g.id === guildId ? " on" : "") },
      h("span", { class: "g-av", text: (g.name || "?").slice(0, 1) }), h("span", { class: "g-name", text: g.name }),
      g.members ? h("small", { text: `${fmt(g.members)} 人` }) : null)));

  // ---- 天氣地點（最多 3 個，可搜尋全台鄉鎮）
  let places = data.places.slice();
  let placesChanged = false;
  const chips = h("div", { class: "chips" });
  const placeLabel = h("label", { for: "place-search" });
  const drawChips = () => (placeLabel.textContent = `天氣地點（${places.length}/3）`) && chips.replaceChildren(...places.map((p, i) => h("span", { class: "place-chip" }, p.label,
    h("button", { type: "button", "aria-label": `移除 ${p.label}`, text: "×",
      onclick: () => { places.splice(i, 1); placesChanged = true; drawChips(); } }))),
    ...(places.length ? [] : [h("span", { class: "muted", text: "沒有選地點時用預設（台北、淡水）" })]));
  drawChips();
  const search = h("input", { type: "search", id: "place-search", placeholder: "搜尋鄉鎮或縣市，例如 淡水、高雄 苓雅" });
  const results = h("div", { class: "place-results" });
  let typing;
  search.addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(async () => {
      const q = search.value.trim();
      if (!q) { results.replaceChildren(); return; }
      const r = await api(`/api/places?${new URLSearchParams({ q })}`);
      results.replaceChildren(...r.items.slice(0, 8).map((p) => h("button", { type: "button", class: "chip", text: p.label,
        disabled: places.length >= 3 || places.some((x) => x.key === p.key),
        onclick: () => { places.push(p); placesChanged = true; drawChips(); search.value = ""; results.replaceChildren(); } })));
      if (!r.items.length) results.append(h("span", { class: "muted", text: "找不到這個地點" }));
    }, 250);
  });

  const channelSelect = (current, id) => h("select", { id },
    h("option", { value: "", text: "（不傳送）", selected: !current }),
    data.channels.map((c) => h("option", { value: c.id, selected: c.id === current, disabled: !!c.problem,
      text: `#${c.name}${c.problem ? `（${c.problem}）` : ""}` })));
  const weatherChannel = channelSelect(data.weather.channel, "w-channel");
  const weatherTime = h("input", { type: "time", id: "w-time", value: data.weather.time });
  const alertChannel = channelSelect(data.alerts.channel, "a-channel");
  const alertAll = h("input", { type: "checkbox", checked: data.alerts.all_types, "aria-label": "所有天氣特報都通知" });
  const quakeChannel = channelSelect(data.earthquake.channel, "e-channel");
  const quakeLevel = h("select", { id: "e-level" }, data.earthquake.levels.map((l) =>
    h("option", { value: l.value, text: l.label, selected: l.value === data.earthquake.level })));

  const save = morphButton("儲存", async () => {
    const body = { weather_channel: weatherChannel.value || null, weather_time: weatherTime.value,
                   alerts_channel: alertChannel.value || null, alerts_all: alertAll.checked,
                   earthquake_channel: quakeChannel.value || null, earthquake_level: quakeLevel.value };
    if (placesChanged) body.places = places.map((p) => p.key);
    await api(`/api/guilds/${guildId}/settings`, { method: "POST", body });
    placesChanged = false;
    toast("已儲存，立即生效");
  });
  const field = (label, control, hint) => h("div", { class: "field" }, h("label", { for: control.id, text: label }), control,
    hint ? h("span", { class: "hint", text: hint }) : null);

  view.replaceChildren(
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "伺服器" }),
      h("span", { class: "muted", text: "跟 Discord 裡的 /settings 是同一份設定" })), picker),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: `天氣・${data.guild.name}` })),
        h("div", { class: "field" }, placeLabel, chips, search, results),
        field("每天自動傳送預報的頻道", weatherChannel),
        field("傳送時間", weatherTime)),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "颱風豪雨與停班停課" })),
        field("通知頻道", alertChannel, "只通知天氣地點所在縣市的颱風、豪雨、大雨特報與停班停課"),
        h("label", { class: "switch" }, alertAll, "強風、低溫等所有天氣特報也通知")),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "地震通報" })),
        field("通報頻道", quakeChannel, "天氣地點所在縣市的震度達到門檻時通報"),
        field("震度門檻", quakeLevel),
        h("p", { class: "muted", style: { marginTop: "16px" } }, "功能開關在",
          h("a", { href: "#/features", onclick: () => { featureGuild = guildId; }, text: "「功能開關」頁" }), "，選這個伺服器即可。"))),
    h("div", { class: "save-bar" }, save),
  );
}

// ---------------------------------------------------------------- 音樂
async function renderMusic(view) {
  const box = h("div", { class: "music-list" });
  const STATE = { playing: ["播放中", "good"], paused: ["暫停", ""], loading: ["載入中", ""], idle: ["待機", ""] };
  const control = async (guild, action) => {
    try { const r = await api(`/api/music/${guild}/control`, { method: "POST", body: { action } }); toast(r.message); }
    catch (e) { toast(e.message, true); }
    load().catch(() => {});
  };
  const load = async () => {
    const data = await api("/api/music");
    if (!data.enabled) { box.replaceChildren(h("p", { class: "empty", text: "音樂功能沒有載入。" })); return; }
    if (!data.players.length) { box.replaceChildren(h("section", { class: "card" }, h("p", { class: "empty", text: "目前沒有伺服器在放歌。有人用 /play 點歌後會出現在這裡。" }))); return; }
    box.replaceChildren(...data.players.map((p) => {
      const cur = p.current;
      const [label, cls] = STATE[p.state] || STATE.idle;
      const eq = h("span", { class: "eq" + (p.state === "playing" ? " on" : ""), "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"));
      const btn = (text, action, disabled, title) => h("button", { class: "btn small", type: "button", text, disabled, title,
        onclick: () => control(p.guild, action) });
      return h("section", { class: "card music-card" + (p.state === "playing" ? " beam" : "") },
        h("div", { class: "card-head" }, h("h2", { text: p.guild_name }), h("span", { class: `badge ${cls}`, text: label }), eq,
          h("span", { class: "spacer" }), h("span", { class: "muted", text: p.channel ? `🔊 ${p.channel}・${p.listeners.length} 人在聽` : "" })),
        cur ? h("div", { class: "now" },
          cur.thumbnail ? h("img", { class: "thumb", src: cur.thumbnail, alt: "",
            onerror: (e) => e.currentTarget.replaceWith(h("div", { class: "thumb" })) }) : h("div", { class: "thumb" }),
          h("div", { class: "now-text" },
            h("a", { href: cur.url, target: "_blank", rel: "noopener", class: "now-title", text: cur.title }),
            h("div", { class: "muted", text: `${cur.duration}・${cur.requester || "?"} 點的` }),
            h("div", { class: "toolbar", style: { marginTop: "10px" } },
              btn("⏮ 上一首", "prev", !p.has_prev), p.state === "paused" ? btn("▶ 繼續", "resume") : btn("⏸ 暫停", "pause", p.state !== "playing"),
              btn("⏭ 下一首", "next", !p.has_next), h("button", { class: "btn small danger", type: "button", text: "⏹ 停止並離開",
                onclick: () => { if (confirm(`確定停止「${p.guild_name}」的音樂並離開語音頻道？`)) control(p.guild, "stop"); } }))))
          : h("p", { class: "empty", text: "沒有正在播放的歌。" }),
        p.tracks.length ? h("ol", { class: "queue" }, p.tracks.map((t) => h("li", { class: t.index === p.index ? "on" : t.index < p.index ? "past" : "" },
          h("span", { class: "q-title", text: t.title }), h("small", { text: `${t.duration}・${t.requester}` })))) : null,
        p.listeners.length ? h("p", { class: "muted", text: `在聽的人：${p.listeners.join("、")}` }) : null);
    }));
  };
  view.replaceChildren(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "音樂" }),
    h("span", { class: "muted", text: "各伺服器正在播放的歌與播放清單，每 5 秒更新" }))), box);
  await load();
  every(5000, () => load().catch(() => {}));
}

// ---------------------------------------------------------------- 股票
async function renderStocks(view) {
  const data = await api("/api/stocks");
  const sum = data.summary;
  const unrealized = sum.value - sum.cost;
  const tile = (label, value, cls = "", sub = "") => {
    const v = h("div", { class: "value", text: "0" });
    Motion.scramble(v, value, (n) => (typeof n === "number" && cls ? signed(n) : fmt(n)));
    return h("div", { class: `card kpi ${cls}` }, h("div", { class: "label", text: label }), v, sub ? h("div", { class: "sub", text: sub }) : null);
  };
  const pnlClass = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");
  const userLink = (u) => h("a", { href: `#/users/${u.id}`, text: u.name });
  const side = (o) => h("span", { class: `badge ${o.side === "buy" ? "good" : "bad"}`, text: o.side === "buy" ? "買進" : "賣出" });
  const table = (heads, rows, empty) => rows.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, heads.map(([t, num]) => h("th", { class: num ? "num" : "", text: t })))), h("tbody", {}, rows)))
    : h("p", { class: "empty", text: empty });

  const cancel = async (o) => {
    if (!confirm(`確定取消 ${o.user.name} 的委託 #${o.id}（${o.side === "buy" ? "買進" : "賣出"} ${o.symbol} ${fmt(o.shares)} 股）？\n預扣的錢或股票會退回。`)) return;
    try { await api(`/api/stocks/orders/${o.id}/cancel`, { method: "POST" }); toast("已取消並退回"); render(true); }
    catch (e) { toast(e.message, true); }
  };

  view.replaceChildren(
    data.available ? null : h("section", { class: "card error", text: "股票資料庫（twfolio）沒有連上：持股市值會用成本估算，Discord 的 /股票 也會停用。" }),
    h("div", { class: "kpis" },
      tile("交易人數", sum.traders), tile("持股市值", sum.value, "", data.quotes_live ? "證交所即時行情" : "最近收盤價"),
      tile("未實現損益", unrealized, unrealized >= 0 ? "good" : "bad"), tile("已實現損益", sum.realized, sum.realized >= 0 ? "good" : "bad"),
      tile("委託中", sum.pending)),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "損益排行（已實現＋未實現）" })),
        table([["#"], ["玩家"], ["總損益", 1], ["持股", 0]], data.ranking.map((r, i) => h("tr", {},
          h("td", { class: "rank", text: i < 3 ? ["🥇", "🥈", "🥉"][i] : String(i + 1) }), h("td", {}, userLink(r)),
          h("td", { class: `num ${pnlClass(r.total)}`, text: signed(r.total) }),
          h("td", { class: "muted", text: r.symbols.join("、") || "空手" }))), "還沒有人交易過股票。")),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "熱門持股" })),
        table([["股票"], ["人數", 1], ["股數", 1], ["現價", 1], ["漲跌", 1], ["市值", 1]], data.symbols.map((a) => h("tr", {},
          h("td", {}, `${a.symbol} `, h("span", { class: "muted", text: a.name })), h("td", { class: "num", text: fmt(a.holders) }),
          h("td", { class: "num", text: fmt(a.shares) }), h("td", { class: "num", text: a.price ?? "—" }),
          h("td", { class: `num ${pnlClass(a.change_pct ?? 0)}`, text: a.change_pct == null ? "—" : `${a.change_pct > 0 ? "+" : ""}${a.change_pct}%` }),
          h("td", { class: "num", text: fmt(a.value) }))), "目前沒有人持有股票。"))),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "委託中的單" }),
      h("span", { class: "muted", text: "盤後下的單會用下一個交易日的開盤價成交；取消會退回預扣" })),
      table([["#", 1], ["玩家"], ["買賣"], ["股票"], ["股數", 1], ["預扣", 1], ["最早成交日"], [""]], data.pending.map((o) => h("tr", {},
        h("td", { class: "num id", text: o.id }), h("td", {}, userLink(o.user)), h("td", {}, side(o)),
        h("td", {}, `${o.symbol} `, h("span", { class: "muted", text: o.name })), h("td", { class: "num", text: fmt(o.shares) }),
        h("td", { class: "num", text: o.reserved ? fmt(o.reserved) : "—" }), h("td", { text: o.not_before || "—" }),
        h("td", {}, h("button", { class: "btn small danger", type: "button", text: "取消", onclick: () => cancel(o) })))), "沒有委託中的單。")),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "最近的成交與取消" })),
      table([["時間"], ["玩家"], ["買賣"], ["股票"], ["股數", 1], ["成交價", 1], ["已實現", 1], ["備註"]], data.history.map((o) => h("tr", { class: o.status === "cancelled" ? "off" : "" },
        h("td", { text: fmtTime(o.at) }), h("td", {}, userLink(o.user)), h("td", {}, side(o)),
        h("td", {}, `${o.symbol} `, h("span", { class: "muted", text: o.name })), h("td", { class: "num", text: fmt(o.shares) }),
        h("td", { class: "num", text: o.status === "filled" ? o.price : "已取消" }),
        h("td", { class: `num ${pnlClass(o.realized ?? 0)}`, text: o.realized == null ? "—" : signed(o.realized) }),
        h("td", { class: "muted", text: o.note || "" }))), "還沒有交易紀錄。")),
  );
}

// ---------------------------------------------------------------- Minecraft
async function renderMinecraft(view) {
  const first = await api("/api/minecraft");
  if (!first.enabled) {
    view.replaceChildren(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "Minecraft" })),
      h("p", { class: "empty", text: "Minecraft 管理沒有啟用。在 .env 設定 MINECRAFT_GUILD_IDS（要出現 /mc 的伺服器）與 MINECRAFT_SCRIPT（控制腳本）後重新啟動 bot。" })));
    return;
  }
  const pill = h("span", { class: "badge" });
  const status = h("pre", { class: "console" });
  const busy = h("div", { class: "mc-busy", hidden: true }, h("span", { class: "dots" }, h("i"), h("i"), h("i")), h("span"));
  const last = h("div");
  const buttons = [["start", "▶ 開服", "開服需要幾分鐘"], ["stop", "■ 關服", "會先通知玩家、存檔、備份再關"],
                   ["restart", "↻ 重開", "關服再開服"], ["backup", "💾 備份", "立刻備份一次"]].map(([action, label, hint]) =>
    h("button", { class: "btn" + (action === "stop" ? " danger" : action === "start" ? " primary" : ""), type: "button", text: label, title: hint,
      onclick: async () => {
        if (!confirm(`確定要${label.slice(2)}嗎？\n${hint}`)) return;
        try { const r = await api("/api/minecraft/action", { method: "POST", body: { action } }); toast(r.message); load(); }
        catch (e) { toast(e.message, true); }
      } }));
  const draw = (d) => {
    pill.textContent = d.online ? "● 線上" : "● 離線";
    pill.className = `badge ${d.online ? "good" : "bad"}`;
    status.textContent = d.status || "（沒有輸出）";
    busy.hidden = !d.busy;
    busy.lastChild.textContent = d.busy ? ` 正在${d.busy}…` : "";
    buttons.forEach((b) => { b.disabled = !!d.busy; });
    last.replaceChildren(...(d.last ? [h("div", { class: "card-head" }, h("h3", { text: `上次從後台${d.last.action}` }),
      h("span", { class: `badge ${d.last.ok ? "good" : "bad"}`, text: d.last.ok ? "成功" : "有問題" }), h("span", { class: "muted", text: fmtTime(d.last.at) })),
      h("pre", { class: "console", text: d.last.output || "（沒有輸出）" })] : []));
  };
  const load = async () => draw(await api("/api/minecraft"));

  const cmd = h("input", { id: "mc-cmd", placeholder: "例如 list、say 大家好、op 玩家名稱（不用加 /）" });
  const cmdOut = h("pre", { class: "console", hidden: true });
  const runCmd = async () => {
    if (!cmd.value.trim()) return;
    try { const r = await api("/api/minecraft/cmd", { method: "POST", body: { command: cmd.value } });
      cmdOut.hidden = false; cmdOut.textContent = `> ${cmd.value}\n${r.output || "（沒有輸出）"}`; cmd.value = ""; }
    catch (e) { toast(e.message, true); }
  };
  cmd.addEventListener("keydown", (e) => { if (e.key === "Enter") runCmd(); });
  const logLines = h("select", { "aria-label": "行數" }, [30, 60, 100, 200].map((n) => h("option", { value: n, text: `${n} 行` })));
  const logBox = h("pre", { class: "console tall", text: "按「讀取」看伺服器最近的紀錄" });
  const loadLog = async () => {
    try { logBox.textContent = (await api(`/api/minecraft/log?lines=${logLines.value}`)).output || "（沒有輸出）"; logBox.scrollTop = logBox.scrollHeight; }
    catch (e) { toast(e.message, true); }
  };

  view.replaceChildren(
    h("section", { class: "card beam scan" }, h("div", { class: "card-head" }, h("h2", { text: "伺服器狀態" }), pill, busy,
      h("span", { class: "spacer" }), h("button", { class: "btn small", type: "button", text: "重新整理", onclick: () => load() })),
      status, h("div", { class: "toolbar", style: { marginTop: "12px" } }, buttons), last),
    h("div", { class: "grid-2" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "控制台指令" })),
        h("div", { class: "chat-input" }, cmd, h("button", { class: "btn primary", type: "button", text: "送出", onclick: runCmd })),
        h("p", { class: "muted", text: "關服請用上面的「關服」按鈕，才會先通知玩家並備份。" }), cmdOut),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { text: "伺服器紀錄" }), h("span", { class: "spacer" }),
        logLines, h("button", { class: "btn small", type: "button", text: "讀取", onclick: loadLog })), logBox)),
  );
  draw(first);
  every(8000, () => load().catch(() => {}));
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
