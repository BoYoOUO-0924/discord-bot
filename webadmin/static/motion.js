"use strict";
// 管理後台的動畫與特效。不依賴外部函式庫；使用者開啟「減少動態效果」時全部改成靜態。

const Motion = (() => {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const nf = new Intl.NumberFormat("zh-TW");
  const dpr = () => Math.min(window.devicePixelRatio || 1, 1.5);

  // ---------------------------------------------------------------- 背景：透視網格、十六進位資料流、粒子連線
  const bg = {
    started: false, mouse: { x: -999, y: -999 }, t0: performance.now(),
    start() {
      if (this.started) return;
      this.started = true;
      this.cv = document.getElementById("bg");
      this.c = this.cv.getContext("2d");
      const cross = document.getElementById("crosshair"), tag = cross.querySelector(".tag");
      const resize = () => {
        const d = dpr();
        this.w = innerWidth; this.h = innerHeight;
        this.cv.width = this.w * d; this.cv.height = this.h * d; this.c.setTransform(d, 0, 0, d, 0, 0);
        if (reduce) this.draw(0);
      };
      resize(); addEventListener("resize", resize);
      this.ps = Array.from({ length: Math.min(80, Math.round(innerWidth * innerHeight / 22000)) }, () => ({
        x: Math.random() * innerWidth, y: Math.random() * innerHeight * .6, vx: (Math.random() - .5) * .3, vy: (Math.random() - .5) * .3 }));
      this.streams = Array.from({ length: 16 }, (_, i) => ({ x: (i + .5) / 16, y: Math.random() * innerHeight, v: .5 + Math.random() * 1.3,
        chars: Array.from({ length: 14 }, () => "0123456789ABCDEF"[Math.floor(Math.random() * 16)]) }));
      addEventListener("pointermove", (e) => {
        if (e.pointerType !== "mouse") return;
        this.mouse = { x: e.clientX, y: e.clientY };
        document.body.classList.add("pointer");
        cross.style.setProperty("--px", e.clientX + "px");
        cross.style.setProperty("--py", e.clientY + "px");
        tag.textContent = `X ${String(e.clientX).padStart(4, "0")} / Y ${String(e.clientY).padStart(4, "0")}`;
      });
      document.addEventListener("pointerleave", () => document.body.classList.remove("pointer"));
      const loop = () => { this.draw((performance.now() - this.t0) / 1000); requestAnimationFrame(loop); };
      if (reduce) this.draw(0); else requestAnimationFrame(loop);
    },
    draw(t) {
      const { c, w, h } = this, hz = h * .62, cx = w / 2;
      c.clearRect(0, 0, w, h);
      const g = c.createLinearGradient(0, hz, 0, h);
      g.addColorStop(0, "rgba(53,217,255,0)"); g.addColorStop(1, "rgba(53,217,255,.3)");
      c.strokeStyle = g; c.lineWidth = 1;
      for (let i = -14; i <= 14; i++) { c.beginPath(); c.moveTo(cx + i * 18, hz); c.lineTo(cx + i * w / 9, h); c.stroke(); }
      const off = (t * .35) % 1;
      for (let k = 0; k < 14; k++) { const p = (k + off) / 14, y = hz + (h - hz) * p * p; c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
      const sun = c.createRadialGradient(cx, hz, 0, cx, hz, w * .35);
      sun.addColorStop(0, "rgba(178,107,255,.18)"); sun.addColorStop(1, "rgba(178,107,255,0)");
      c.fillStyle = sun; c.fillRect(0, hz - w * .35, w, w * .35 + 2);
      c.font = "12px Consolas, monospace";
      for (const s of this.streams) {
        if (!reduce) { s.y += s.v; if (s.y > hz + 60) { s.y = -200; s.chars[Math.floor(Math.random() * 14)] = "0123456789ABCDEF"[Math.floor(Math.random() * 16)]; } }
        s.chars.forEach((ch, j) => { const y = s.y - j * 15; if (y < 0 || y > hz) return;
          c.fillStyle = j === 0 ? "rgba(200,255,255,.45)" : `rgba(53,217,255,${.11 - j * .007})`; c.fillText(ch, s.x * w, y); });
      }
      const ps = this.ps, m = this.mouse;
      for (const p of ps) {
        if (!reduce) { p.x += p.vx; p.y += p.vy; }
        const dx = p.x - m.x, dy = p.y - m.y, d = Math.hypot(dx, dy);
        if (d < 120 && d > 0) { p.x += dx / d * 1.8; p.y += dy / d * 1.8; }
        if (p.x < 0 || p.x > w) p.vx *= -1; if (p.y < 0 || p.y > hz) p.vy *= -1;
      }
      for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) {
        const a = ps[i], b = ps[j], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < 120) { c.strokeStyle = `rgba(53,217,255,${(1 - d / 120) * .2})`; c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke(); }
      }
      for (const p of ps) { const d = Math.hypot(p.x - m.x, p.y - m.y);
        if (d < 160) { c.strokeStyle = `rgba(124,255,156,${(1 - d / 160) * .45})`; c.beginPath(); c.moveTo(p.x, p.y); c.lineTo(m.x, m.y); c.stroke(); } }
      c.fillStyle = "rgba(124,255,156,.7)";
      for (const p of ps) { c.beginPath(); c.arc(p.x, p.y, 1.5, 0, 6.3); c.fill(); }
    },
  };

  // ---------------------------------------------------------------- 首頁核心全像：旋轉線框球、伺服器軌道、資料封包
  class Holo {
    constructor(canvas) {
      this.cv = canvas; this.c = canvas.getContext("2d");
      this.nodes = 8; this.packets = []; this.boost = 0; this.t0 = performance.now(); this.running = true; this.points = [];
      this.resize = () => { const r = canvas.getBoundingClientRect(), d = dpr();
        this.w = r.width; this.h = r.height; canvas.width = r.width * d; canvas.height = r.height * d; this.c.setTransform(d, 0, 0, d, 0, 0); };
      this.resize(); addEventListener("resize", this.resize);
      const loop = () => { if (!this.running) return; this.draw((performance.now() - this.t0) / 1000); requestAnimationFrame(loop); };
      if (reduce) setTimeout(() => this.draw(1), 0); else requestAnimationFrame(loop);
    }
    stop() { this.running = false; removeEventListener("resize", this.resize); }
    setNodes(n) { this.nodes = Math.max(3, Math.min(24, n || 3)); }
    packet(index, err = false) { if (!reduce) this.packets.push({ n: Math.abs(index) % this.nodes, p: 0, err }); }
    draw(t) {
      const { c, w, h } = this; if (!w) return;
      c.clearRect(0, 0, w, h);
      const cx = w / 2, cy = h / 2, R = Math.min(w, h) * .27, rot = t * .45, tilt = .38, f = 420;
      const proj = (x, y, z) => { const x1 = x * Math.cos(rot) - z * Math.sin(rot), z1 = x * Math.sin(rot) + z * Math.cos(rot);
        const y2 = y * Math.cos(tilt) - z1 * Math.sin(tilt), z2 = y * Math.sin(tilt) + z1 * Math.cos(tilt); const s = f / (f + z2); return [cx + x1 * s, cy + y2 * s, z2]; };
      const seg = (pts) => { for (let i = 1; i < pts.length; i++) { const [x0, y0, z0] = pts[i - 1], [x1, y1] = pts[i];
        c.strokeStyle = `rgba(53,217,255,${z0 < 0 ? .6 : .14})`; c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); } };
      c.lineWidth = 1;
      for (let lat = -60; lat <= 60; lat += 30) { const a = lat * Math.PI / 180, pts = [];
        for (let k = 0; k <= 48; k++) { const b = k / 48 * Math.PI * 2; pts.push(proj(R * Math.cos(a) * Math.cos(b), R * Math.sin(a), R * Math.cos(a) * Math.sin(b))); } seg(pts); }
      for (let lon = 0; lon < 180; lon += 30) { const b = lon * Math.PI / 180, pts = [];
        for (let k = 0; k <= 48; k++) { const a = k / 48 * Math.PI * 2; pts.push(proj(R * Math.cos(a) * Math.cos(b), R * Math.sin(a), R * Math.cos(a) * Math.sin(b))); } seg(pts); }
      this.boost *= .94;
      const glow = c.createRadialGradient(cx, cy, 0, cx, cy, R * (.75 + this.boost * .5));
      glow.addColorStop(0, "rgba(220,255,255,.85)"); glow.addColorStop(.25, `rgba(53,217,255,${.45 + this.boost * .4})`); glow.addColorStop(1, "rgba(53,217,255,0)");
      c.fillStyle = glow; c.beginPath(); c.arc(cx, cy, R * 1.3, 0, 6.3); c.fill();
      const ox = R * 1.75, oy = R * .48, tiltO = -.12;
      c.setLineDash([3, 6]); c.strokeStyle = "rgba(178,107,255,.45)"; c.beginPath(); c.ellipse(cx, cy, ox * 1.12, oy * 1.12, tiltO, 0, 6.3); c.stroke(); c.setLineDash([]);
      c.strokeStyle = "rgba(53,217,255,.35)"; c.beginPath(); c.ellipse(cx, cy, ox, oy, tiltO, 0, 6.3); c.stroke();
      this.points = Array.from({ length: this.nodes }, (_, i) => { const a = i / this.nodes * Math.PI * 2 + t * .18;
        const x = Math.cos(a) * ox, y = Math.sin(a) * oy;
        return [cx + x * Math.cos(tiltO) - y * Math.sin(tiltO), cy + x * Math.sin(tiltO) + y * Math.cos(tiltO), Math.sin(a)]; });
      this.points.forEach(([x, y, z], i) => {
        c.fillStyle = z > 0 ? "rgba(124,255,156,.95)" : "rgba(124,255,156,.4)"; c.beginPath(); c.arc(x, y, z > 0 ? 3.6 : 2.4, 0, 6.3); c.fill();
        if (z > 0) { c.strokeStyle = "rgba(124,255,156,.35)"; c.beginPath(); c.arc(x, y, 7 + Math.sin(t * 3 + i) * 2, 0, 6.3); c.stroke(); } });
      this.packets = this.packets.filter((p) => p.p < 1);
      for (const p of this.packets) {
        p.p += .018;
        const [nx, ny] = this.points[p.n] || [cx, cy], k = { x: (nx + cx) / 2, y: Math.min(ny, cy) - R * .9 };
        const at = (s) => [(1 - s) ** 2 * nx + 2 * (1 - s) * s * k.x + s * s * cx, (1 - s) ** 2 * ny + 2 * (1 - s) * s * k.y + s * s * cy];
        for (let j = 0; j < 10; j++) { const s = Math.max(0, p.p - j * .025), [x, y] = at(s);
          c.fillStyle = p.err ? `rgba(255,77,109,${.9 - j * .09})` : `rgba(200,255,255,${.9 - j * .09})`; c.beginPath(); c.arc(x, y, 2.6 - j * .2, 0, 6.3); c.fill(); }
        if (p.p >= 1) this.boost = Math.min(1, this.boost + .5);
      }
      const sr = (t * .6) % 1;
      c.strokeStyle = `rgba(53,217,255,${.5 * (1 - sr)})`; c.beginPath(); c.arc(cx, cy, R * (1 + sr * 1.2), 0, 6.3); c.stroke();
    }
  }

  // ---------------------------------------------------------------- 數字解碼效果
  function scramble(node, value, format = (v) => nf.format(v)) {
    const final = typeof value === "number" ? format(value) : String(value);
    if (reduce || node.dataset.final === final) { node.textContent = final; node.dataset.final = final; return; }
    node.dataset.final = final;
    const chars = "0123456789#$%&@";
    let f = 0; const total = 16;
    clearTimeout(node._scr);
    const step = () => {
      f++;
      node.textContent = [...final].map((ch, i) => (!/[0-9]/.test(ch) || f > total * (i + 1) / final.length) ? ch : chars[Math.floor(Math.random() * chars.length)]).join("");
      if (f < total) node._scr = setTimeout(step, 40); else node.textContent = final;
    };
    step();
  }

  // ---------------------------------------------------------------- 迷你走勢線
  const SVG = "http://www.w3.org/2000/svg";
  function spark(values, color = "var(--accent)") {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 100 34"); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("aria-hidden", "true");
    const v = values.length > 1 ? values : [0, ...values, 0];
    const mx = Math.max(...v), mn = Math.min(...v);
    const pts = v.map((n, i) => `${(i / (v.length - 1) * 100).toFixed(1)},${(31 - (n - mn) / ((mx - mn) || 1) * 27).toFixed(1)}`);
    const area = document.createElementNS(SVG, "polygon");
    area.setAttribute("points", `0,34 ${pts.join(" ")} 100,34`); area.setAttribute("fill", color); area.setAttribute("opacity", ".08");
    const line = document.createElementNS(SVG, "polyline");
    line.setAttribute("points", pts.join(" ")); line.setAttribute("fill", "none"); line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", "1.6"); line.setAttribute("vector-effect", "non-scaling-stroke"); line.setAttribute("pathLength", "1");
    line.setAttribute("class", "spark");
    svg.append(area, line);
    return svg;
  }

  // ---------------------------------------------------------------- 環形儀表
  function gauge(label, color) {
    const C = 2 * Math.PI * 34, ARC = C * .75;
    const box = document.createElement("div");
    box.className = "gauge";
    box.style.setProperty("--gc", color);
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 86 86"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label);
    const g = document.createElementNS(SVG, "g"); g.setAttribute("transform", "rotate(135 43 43)");
    const ring = (cls, offset) => { const c = document.createElementNS(SVG, "circle");
      for (const [k, v] of Object.entries({ cx: 43, cy: 43, r: 34, fill: "none", "stroke-width": 6, "stroke-dasharray": `${ARC} ${C}`, "stroke-dashoffset": offset, class: cls })) c.setAttribute(k, v);
      return c; };
    const val = ring("val", ARC);
    g.append(ring("trk", 0), val);
    const ticks = document.createElementNS(SVG, "circle");
    for (const [k, v] of Object.entries({ cx: 43, cy: 43, r: 41, fill: "none", "stroke-width": 2, "stroke-dasharray": "1 5", class: "ticks" })) ticks.setAttribute(k, v);
    const text = document.createElementNS(SVG, "text");
    text.setAttribute("x", 43); text.setAttribute("y", 48); text.setAttribute("text-anchor", "middle"); text.textContent = "—";
    svg.append(g, ticks, text);
    const small = document.createElement("small"); small.textContent = label;
    box.append(svg, small);
    return {
      el: box,
      set(fraction, shown) {
        val.setAttribute("stroke-dashoffset", (ARC * (1 - Math.max(0, Math.min(1, fraction)))).toFixed(1));
        scramble(text, shown);
      },
    };
  }

  // ---------------------------------------------------------------- 開機畫面
  function boot(lines) {
    const box = document.getElementById("boot");
    if (reduce) return Promise.resolve();
    const out = document.getElementById("boot-lines"), bar = document.getElementById("boot-bar");
    out.textContent = ""; bar.style.width = "0"; box.classList.remove("leave"); box.hidden = false;
    return new Promise((resolve) => {
      let i = 0;
      const next = () => {
        if (i >= lines.length) {
          setTimeout(() => { box.classList.add("leave"); resolve(); setTimeout(() => { box.hidden = true; }, 700); }, 250);
          return;
        }
        const row = document.createElement("div"); row.textContent = lines[i]; out.append(row);
        if (i > 0 && i < lines.length - 1) setTimeout(() => { const ok = document.createElement("span"); ok.className = "ok"; ok.textContent = "[ OK ]"; row.append(ok); }, 120);
        bar.style.width = `${(i + 1) / lines.length * 100}%`; i++; setTimeout(next, 210);
      };
      next();
    });
  }

  // ---------------------------------------------------------------- 切頁轉場、警報、打字機
  function wipe() {
    if (reduce) return;
    const w = document.createElement("div"); w.className = "wipe"; w.setAttribute("aria-hidden", "true");
    for (let k = 0; k < 8; k++) { const i = document.createElement("i"); i.style.setProperty("--k", (k * 5) % 8); w.append(i); }
    document.body.append(w); setTimeout(() => w.remove(), 800);
  }
  function restart(el, cls) { if (!el || reduce) return; el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); }
  function alarm(card) { restart(document.getElementById("alarm"), "on"); restart(card, "shake"); }
  function typewriter(el, text) {
    return new Promise((resolve) => {
      if (reduce) { el.textContent = text; return resolve(); }
      let i = 0; el.textContent = "";
      const tick = () => { el.textContent = text.slice(0, ++i); if (i < text.length) setTimeout(tick, 22); else resolve(); };
      tick();
    });
  }

  return { reduce, bg, Holo, scramble, spark, gauge, boot, wipe, alarm, typewriter };
})();
