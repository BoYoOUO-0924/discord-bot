"use strict";
// 管理後台的動畫與特效。不依賴外部函式庫。
// 動畫偏好：自己在右上角選過就照選的；沒選過就跟系統的「減少動態效果」（html.rm＝簡化動畫，在 index.html 一開始就決定）。
// 效能原則：只動 transform／opacity；Canvas 畫面外或分頁切走時暫停；能預先畫好的東西（光點、漸層）只畫一次。

const Motion = (() => {
  const root = document.documentElement;
  const reduce = root.classList.contains("rm");
  const fine = matchMedia("(pointer: fine)").matches;
  const nf = new Intl.NumberFormat("zh-TW");
  const dpr = () => Math.min(window.devicePixelRatio || 1, 1.5);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const easeOutExpo = (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x));
  const easeOutCubic = (x) => 1 - Math.pow(1 - x, 3);

  // ---------------------------------------------------------------- 動畫迴圈：畫面外、分頁切走時暫停；fps 可以限制每秒最多畫幾次（背景不需要 60 張）
  function animate(el, draw, fps = 0) {
    let visible = true, running = false, alive = true, io = null, last = 0;
    const gap = fps ? 1000 / fps - 2 : 0;
    const frame = (now) => {
      if (!alive || !visible || document.hidden) { running = false; return; }
      if (!gap || now - last >= gap) { last = now; draw(now); }
      requestAnimationFrame(frame);
    };
    const wake = () => { if (alive && !running && visible && !document.hidden) { running = true; requestAnimationFrame(frame); } };
    if ("IntersectionObserver" in window) { io = new IntersectionObserver((es) => { visible = es[0].isIntersecting; wake(); }); io.observe(el); }
    document.addEventListener("visibilitychange", wake);
    if (reduce) requestAnimationFrame((t) => draw(t)); else wake();
    return () => { alive = false; if (io) io.disconnect(); document.removeEventListener("visibilitychange", wake); };
  }

  // 預先畫好的發光圓點（之後只要 drawImage，比每格做漸層快很多）
  const glowCache = {};
  function glowSprite(color) {
    if (glowCache[color]) return glowCache[color];
    const cv = document.createElement("canvas"); cv.width = cv.height = 64;
    const c = cv.getContext("2d"), g = c.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(.18, color); g.addColorStop(1, "rgba(0,0,0,0)");
    c.fillStyle = g; c.fillRect(0, 0, 64, 64);
    return (glowCache[color] = cv);
  }

  // ---------------------------------------------------------------- 背景：透視網格、十六進位資料流、粒子連線、游標照亮的網格
  const bg = {
    started: false, mouse: { x: -999, y: -999 }, t0: performance.now(),
    start() {
      if (this.started) return;
      this.started = true;
      this.cv = document.getElementById("bg");
      this.c = this.cv.getContext("2d");
      const cross = document.getElementById("crosshair"), tag = cross.querySelector(".tag");
      const spot = document.getElementById("spot-grid"), spotGrid = spot && spot.firstElementChild;
      // 全螢幕背景很大又在卡片後面：用 1 倍解析度；不會動的透視直線與光暈預先畫在另一張畫布，每格只要貼上
      this.still = document.createElement("canvas");
      const resize = () => {
        this.w = innerWidth; this.h = innerHeight;
        this.cv.width = this.w; this.cv.height = this.h;
        const hz = this.h * .62, cx = this.w / 2;
        this.lineGrad = this.c.createLinearGradient(0, hz, 0, this.h);
        this.lineGrad.addColorStop(0, "rgba(53,217,255,0)"); this.lineGrad.addColorStop(1, "rgba(53,217,255,.3)");
        this.still.width = this.w; this.still.height = this.h;
        const s = this.still.getContext("2d");
        s.strokeStyle = this.lineGrad; s.lineWidth = 1; s.beginPath();
        for (let i = -14; i <= 14; i++) { s.moveTo(cx + i * 18, hz); s.lineTo(cx + i * this.w / 9, this.h); }
        s.stroke();
        const sun = s.createRadialGradient(cx, hz, 0, cx, hz, this.w * .35);
        sun.addColorStop(0, "rgba(178,107,255,.18)"); sun.addColorStop(1, "rgba(178,107,255,0)");
        s.fillStyle = sun; s.fillRect(0, hz - this.w * .35, this.w, this.w * .35 + 2);
        if (reduce && this.ps) this.draw(0);
      };
      resize(); addEventListener("resize", resize);
      this.ps = Array.from({ length: Math.min(70, Math.round(innerWidth * innerHeight / 24000)) }, () => ({
        x: Math.random() * innerWidth, y: Math.random() * innerHeight * .6, vx: (Math.random() - .5) * .3, vy: (Math.random() - .5) * .3 }));
      this.streams = Array.from({ length: 16 }, (_, i) => ({ x: (i + .5) / 16, y: Math.random() * innerHeight, v: .5 + Math.random() * 1.3,
        chars: Array.from({ length: 14 }, () => "0123456789ABCDEF"[Math.floor(Math.random() * 16)]) }));
      let pending = false, px = 0, py = 0;
      const apply = () => {
        pending = false;
        cross.style.setProperty("--px", px + "px"); cross.style.setProperty("--py", py + "px");
        tag.textContent = `X ${String(px).padStart(4, "0")} / Y ${String(py).padStart(4, "0")}`;
        // 游標照亮的網格：一塊圓形的網格跟著游標移動，網格本身對齊整個畫面
        if (spot) {
          const x = px - 280, y = py - 280, ox = ((x % 40) + 40) % 40, oy = ((y % 40) + 40) % 40;
          spot.style.transform = `translate3d(${x}px, ${y}px, 0)`;
          if (spotGrid) spotGrid.style.transform = `translate3d(${-ox}px, ${-oy}px, 0)`;
        }
      };
      addEventListener("pointermove", (e) => {
        if (e.pointerType !== "mouse") return;
        this.mouse = { x: e.clientX, y: e.clientY }; px = Math.round(e.clientX); py = Math.round(e.clientY);
        document.body.classList.add("pointer");
        if (!pending) { pending = true; requestAnimationFrame(apply); }
      }, { passive: true });
      document.addEventListener("pointerleave", () => document.body.classList.remove("pointer"));
      animate(this.cv, (now) => this.draw((now - this.t0) / 1000), 30);
    },
    draw(t) {
      const { c, w, h } = this, hz = h * .62, cx = w / 2;
      c.clearRect(0, 0, w, h);
      c.drawImage(this.still, 0, 0);
      c.strokeStyle = this.lineGrad; c.lineWidth = 1; c.beginPath();
      const off = (t * .35) % 1;
      for (let k = 0; k < 14; k++) { const p = (k + off) / 14, y = hz + (h - hz) * p * p; c.moveTo(0, y); c.lineTo(w, y); }
      c.stroke();
      c.font = "12px Consolas, monospace";
      for (const s of this.streams) {
        if (!reduce) { s.y += s.v * 2; if (s.y > hz + 60) { s.y = -200; s.chars[Math.floor(Math.random() * 14)] = "0123456789ABCDEF"[Math.floor(Math.random() * 16)]; } }
        s.chars.forEach((ch, j) => { const y = s.y - j * 15; if (y < 0 || y > hz) return;
          c.fillStyle = j === 0 ? "rgba(200,255,255,.45)" : `rgba(53,217,255,${.11 - j * .007})`; c.fillText(ch, s.x * w, y); });
      }
      const ps = this.ps, m = this.mouse;
      for (const p of ps) {
        if (!reduce) { p.x += p.vx * 2; p.y += p.vy * 2; }
        const dx = p.x - m.x, dy = p.y - m.y, d = Math.hypot(dx, dy);
        if (d < 120 && d > 0) { p.x += dx / d * 3.6; p.y += dy / d * 3.6; }
        if (p.x < 0 || p.x > w) p.vx *= -1; if (p.y < 0 || p.y > hz) p.vy *= -1;
      }
      // 連線依透明度分 4 組，一組一次畫完
      const buckets = [new Path2D(), new Path2D(), new Path2D(), new Path2D()];
      for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) {
        const a = ps[i], b = ps[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < 14400) { const k = Math.min(3, Math.floor(Math.sqrt(d2) / 30)); buckets[k].moveTo(a.x, a.y); buckets[k].lineTo(b.x, b.y); }
      }
      buckets.forEach((path, k) => { c.strokeStyle = `rgba(53,217,255,${(.2 * (1 - (k + .5) / 4)).toFixed(3)})`; c.stroke(path); });
      c.strokeStyle = "rgba(124,255,156,.25)"; c.beginPath();
      for (const p of ps) { if (Math.hypot(p.x - m.x, p.y - m.y) < 160) { c.moveTo(p.x, p.y); c.lineTo(m.x, m.y); } }
      c.stroke();
      c.fillStyle = "rgba(124,255,156,.7)"; c.beginPath();
      for (const p of ps) c.rect(p.x - 1.2, p.y - 1.2, 2.4, 2.4);
      c.fill();
    },
  };

  // ---------------------------------------------------------------- 首頁主卡背景：粒子星雲（游標排斥，按住滑鼠變吸引；鄰近粒子自動連線）
  class Nebula {
    constructor(canvas, host) {
      this.cv = canvas; this.c = canvas.getContext("2d"); this.host = host;
      this.mouse = { x: -999, y: -999, down: false };
      this.resize = () => {
        const r = host.getBoundingClientRect(), d = dpr();
        this.w = r.width; this.h = r.height; canvas.width = r.width * d; canvas.height = r.height * d; this.c.setTransform(d, 0, 0, d, 0, 0);
        const n = clamp(Math.round(this.w * this.h / 6000), 28, 64);
        while ((this.ps || (this.ps = [])).length < n) this.ps.push(this.spawn());
        this.ps.length = n;
      };
      this.resize();
      this.ro = new ResizeObserver(this.resize); this.ro.observe(host);
      this.onMove = (e) => { const r = host.getBoundingClientRect(); this.mouse.x = e.clientX - r.left; this.mouse.y = e.clientY - r.top; };
      this.onLeave = () => { this.mouse.x = this.mouse.y = -999; this.mouse.down = false; };
      this.onDown = () => { this.mouse.down = true; }; this.onUp = () => { this.mouse.down = false; };
      host.addEventListener("pointermove", this.onMove, { passive: true });
      host.addEventListener("pointerleave", this.onLeave);
      host.addEventListener("pointerdown", this.onDown); addEventListener("pointerup", this.onUp);
      this.sprites = [glowSprite("rgba(53,217,255,.9)"), glowSprite("rgba(178,107,255,.9)")];
      this.stopLoop = animate(canvas, () => this.draw());
    }
    spawn() {
      return { x: Math.random() * (this.w || 800), y: Math.random() * (this.h || 300), vx: (Math.random() - .5) * .25, vy: (Math.random() - .5) * .25,
               r: .6 + Math.random() * 1.8, k: Math.random() < .62 ? 0 : 1, tw: Math.random() * 6 };
    }
    stop() {
      this.stopLoop(); this.ro.disconnect(); removeEventListener("pointerup", this.onUp);
      this.host.removeEventListener("pointermove", this.onMove); this.host.removeEventListener("pointerleave", this.onLeave);
      this.host.removeEventListener("pointerdown", this.onDown);
    }
    draw() {
      const { c, w, h, ps, mouse } = this;
      c.clearRect(0, 0, w, h);
      for (const p of ps) {
        const dx = p.x - mouse.x, dy = p.y - mouse.y, d2 = dx * dx + dy * dy;
        if (d2 < 22500 && d2 > 1) {
          const d = Math.sqrt(d2), f = (1 - d / 150) * (mouse.down ? -.55 : .35);
          p.vx += dx / d * f; p.vy += dy / d * f;
        }
        p.vx *= .965; p.vy *= .965;
        p.vx += (Math.random() - .5) * .02; p.vy += (Math.random() - .5) * .02;
        if (!reduce) { p.x += p.vx; p.y += p.vy; }
        if (p.x < -10) p.x = w + 10; else if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10; else if (p.y > h + 10) p.y = -10;
      }
      const lines = [new Path2D(), new Path2D(), new Path2D()];
      for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) {
        const a = ps[i], b = ps[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < 8100) { const k = Math.min(2, Math.floor(Math.sqrt(d2) / 30)); lines[k].moveTo(a.x, a.y); lines[k].lineTo(b.x, b.y); }
      }
      c.lineWidth = 1;
      lines.forEach((path, k) => { c.strokeStyle = `rgba(120,200,255,${[.32, .18, .08][k]})`; c.stroke(path); });
      const t = performance.now() / 1000;
      for (const p of ps) {
        const s = p.r * 7 * (.75 + .25 * Math.sin(t * 2 + p.tw));
        c.globalAlpha = .55 + .35 * Math.sin(t * 1.4 + p.tw);
        c.drawImage(this.sprites[p.k], p.x - s / 2, p.y - s / 2, s, s);
      }
      c.globalAlpha = 1;
    }
  }

  // ---------------------------------------------------------------- 首頁核心全像：線框地球、兩道反向能量軌道、聲納脈衝、伺服器軌道、資料封包
  class Holo {
    constructor(canvas, host) {
      this.cv = canvas; this.c = canvas.getContext("2d"); this.host = host || canvas;
      this.nodes = 8; this.packets = []; this.boost = 0; this.t0 = performance.now(); this.points = [];
      this.ripples = []; this.lastRipple = -10; this.tx = 0; this.ty = 0; this.px = 0; this.py = 0;
      this.resize = () => { const r = canvas.getBoundingClientRect(), d = dpr();
        this.w = r.width; this.h = r.height; canvas.width = r.width * d; canvas.height = r.height * d; this.c.setTransform(d, 0, 0, d, 0, 0);
        this.cx = this.w / 2; this.cy = this.h / 2; this.R = Math.min(this.w, this.h) * .27; };
      this.resize(); addEventListener("resize", this.resize);
      // 視差：游標在主卡片上移動時，地球跟著傾斜
      this.onMove = (e) => { const r = this.host.getBoundingClientRect(); this.tx = ((e.clientX - r.left) / r.width - .5) * 2; this.ty = ((e.clientY - r.top) / r.height - .5) * 2; };
      this.onLeave = () => { this.tx = this.ty = 0; };
      this.host.addEventListener("pointermove", this.onMove, { passive: true });
      this.host.addEventListener("pointerleave", this.onLeave);
      this.sprites = { cyan: glowSprite("rgba(53,217,255,.95)"), violet: glowSprite("rgba(178,107,255,.95)"), green: glowSprite("rgba(124,255,156,.95)") };
      this.stopLoop = animate(canvas, (now) => this.draw((now - this.t0) / 1000));
    }
    stop() {
      this.stopLoop(); removeEventListener("resize", this.resize);
      this.host.removeEventListener("pointermove", this.onMove); this.host.removeEventListener("pointerleave", this.onLeave);
    }
    setNodes(n) { this.nodes = Math.max(3, Math.min(24, n || 3)); }
    packet(index, err = false) { if (!reduce) this.packets.push({ n: Math.abs(index) % this.nodes, p: 0, err }); }
    draw(t) {
      const { c, w, h, cx, cy, R } = this; if (!w) return;
      c.clearRect(0, 0, w, h);
      this.px += (this.tx - this.px) * .06; this.py += (this.ty - this.py) * .06;
      const rot = t * .45 + this.px * .7, tilt = .38 + this.py * .32, f = 420;
      const cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(tilt), st = Math.sin(tilt);
      const proj = (x, y, z) => { const x1 = x * cr - z * sr, z1 = x * sr + z * cr, y2 = y * ct - z1 * st, z2 = y * st + z1 * ct, s = f / (f + z2);
        return [cx + x1 * s, cy + y2 * s, z2]; };
      // 地球經緯線：前後各一條路徑
      const front = new Path2D(), back = new Path2D();
      const line = (pts) => { for (let i = 1; i < pts.length; i++) { const a = pts[i - 1], b = pts[i], p = a[2] < 0 ? front : back; p.moveTo(a[0], a[1]); p.lineTo(b[0], b[1]); } };
      for (let lat = -60; lat <= 60; lat += 30) { const a = lat * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a), pts = [];
        for (let k = 0; k <= 48; k++) { const b = k / 48 * Math.PI * 2; pts.push(proj(R * ca * Math.cos(b), R * sa, R * ca * Math.sin(b))); } line(pts); }
      for (let lon = 0; lon < 180; lon += 30) { const b = lon * Math.PI / 180, cb = Math.cos(b), sb = Math.sin(b), pts = [];
        for (let k = 0; k <= 48; k++) { const a = k / 48 * Math.PI * 2; pts.push(proj(R * Math.cos(a) * cb, R * Math.sin(a), R * Math.cos(a) * sb)); } line(pts); }
      c.lineWidth = 1;
      c.strokeStyle = "rgba(53,217,255,.14)"; c.stroke(back);
      c.strokeStyle = "rgba(53,217,255,.6)"; c.stroke(front);
      // 核心光暈
      this.boost *= .94;
      const glow = c.createRadialGradient(cx, cy, 0, cx, cy, R * (.75 + this.boost * .5));
      glow.addColorStop(0, "rgba(220,255,255,.85)"); glow.addColorStop(.25, `rgba(53,217,255,${.45 + this.boost * .4})`); glow.addColorStop(1, "rgba(53,217,255,0)");
      c.fillStyle = glow; c.beginPath(); c.arc(cx, cy, R * 1.3, 0, 6.3); c.fill();
      // 兩道不同傾角、反向旋轉的能量軌道，各有三顆拖尾光點
      const ring = (radius, incl, asc, spin, sprite, color) => {
        const ci = Math.cos(incl), si = Math.sin(incl), ca = Math.cos(asc), sa = Math.sin(asc);
        const at = (a) => { const x = radius * Math.cos(a), z0 = radius * Math.sin(a), y = -z0 * si, z = z0 * ci;
          return proj(x * ca - z * sa, y, x * sa + z * ca); };
        const fr = new Path2D(), bk = new Path2D(); let prev = at(0);
        for (let k = 1; k <= 72; k++) { const p = at(k / 72 * Math.PI * 2), path = prev[2] < 0 ? fr : bk; path.moveTo(prev[0], prev[1]); path.lineTo(p[0], p[1]); prev = p; }
        c.lineWidth = 1.4; c.strokeStyle = color.replace("A", ".12"); c.stroke(bk);
        c.strokeStyle = color.replace("A", ".55"); c.stroke(fr);
        for (let m = 0; m < 3; m++) {
          const base = t * spin + m * Math.PI * 2 / 3;
          for (let j = 0; j < 9; j++) {
            const p = at(base - Math.sign(spin) * j * .045), s = (j ? 14 : 22) * (1 - j / 10) * (p[2] < 0 ? 1 : .6);
            c.globalAlpha = (1 - j / 9) * (p[2] < 0 ? 1 : .45); c.drawImage(sprite, p[0] - s / 2, p[1] - s / 2, s, s);
          }
        }
        c.globalAlpha = 1;
      };
      ring(R * 1.38, 1.15, .45, .9, this.sprites.cyan, "rgba(53,217,255,A)");
      ring(R * 1.58, -.85, -.55, -.65, this.sprites.violet, "rgba(178,107,255,A)");
      // 聲納脈衝：每 3 秒從核心擴散一圈
      if (!reduce && t - this.lastRipple > 3) { this.lastRipple = t; this.ripples.push(t); }
      this.ripples = this.ripples.filter((r0) => t - r0 < 2.6);
      for (const r0 of this.ripples) {
        const k = (t - r0) / 2.6, rr = R * (.3 + k * 2.3);
        c.lineWidth = 2.2 * (1 - k) + .4; c.strokeStyle = `rgba(53,217,255,${(.55 * (1 - k)).toFixed(3)})`;
        c.beginPath(); c.ellipse(cx, cy, rr, rr * (.92 - this.py * .1), 0, 0, 6.3); c.stroke();
        c.lineWidth = 8 * (1 - k); c.strokeStyle = `rgba(53,217,255,${(.08 * (1 - k)).toFixed(3)})`; c.stroke();
      }
      // 伺服器軌道與光點
      const ox = R * 1.75, oy = R * .48, tiltO = -.12 + this.px * .06;
      c.lineWidth = 1;
      c.setLineDash([3, 6]); c.strokeStyle = "rgba(178,107,255,.45)"; c.beginPath(); c.ellipse(cx, cy, ox * 1.12, oy * 1.12, tiltO, 0, 6.3); c.stroke(); c.setLineDash([]);
      c.strokeStyle = "rgba(53,217,255,.35)"; c.beginPath(); c.ellipse(cx, cy, ox, oy, tiltO, 0, 6.3); c.stroke();
      this.points = Array.from({ length: this.nodes }, (_, i) => { const a = i / this.nodes * Math.PI * 2 + t * .18;
        const x = Math.cos(a) * ox, y = Math.sin(a) * oy;
        return [cx + x * Math.cos(tiltO) - y * Math.sin(tiltO), cy + x * Math.sin(tiltO) + y * Math.cos(tiltO), Math.sin(a)]; });
      this.points.forEach(([x, y, z], i) => {
        const s = z > 0 ? 20 : 12; c.globalAlpha = z > 0 ? 1 : .45; c.drawImage(this.sprites.green, x - s / 2, y - s / 2, s, s);
        if (z > 0) { c.globalAlpha = 1; c.strokeStyle = "rgba(124,255,156,.35)"; c.beginPath(); c.arc(x, y, 7 + Math.sin(t * 3 + i) * 2, 0, 6.3); c.stroke(); } });
      c.globalAlpha = 1;
      // 資料封包：從伺服器飛向核心
      this.packets = this.packets.filter((p) => p.p < 1);
      for (const p of this.packets) {
        p.p += .018;
        const [nx, ny] = this.points[p.n] || [cx, cy], k = { x: (nx + cx) / 2, y: Math.min(ny, cy) - R * .9 };
        const at = (s) => [(1 - s) ** 2 * nx + 2 * (1 - s) * s * k.x + s * s * cx, (1 - s) ** 2 * ny + 2 * (1 - s) * s * k.y + s * s * cy];
        for (let j = 0; j < 10; j++) { const s = Math.max(0, p.p - j * .025), [x, y] = at(s);
          c.fillStyle = p.err ? `rgba(255,77,109,${.9 - j * .09})` : `rgba(200,255,255,${.9 - j * .09})`; c.beginPath(); c.arc(x, y, 2.6 - j * .2, 0, 6.3); c.fill(); }
        if (p.p >= 1) this.boost = Math.min(1, this.boost + .5);
      }
    }
  }

  // ---------------------------------------------------------------- 文字：數字解碼、整段亂碼解碼、數字滾動
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

  // 每個字都先亂碼，再由左到右定格（標題、版本號用）
  const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&@$<>/\\|=+*▓▒░";
  function decode(node, text, { duration = 900, delay = 0 } = {}) {
    const final = String(text);
    if (reduce) { node.textContent = final; return; }
    cancelAnimationFrame(node._dec);
    const chars = [...final], start = performance.now() + delay;
    // 每個字的定格時間：大致由左到右，帶一點隨機
    const lock = chars.map((_, i) => (i / Math.max(1, chars.length)) * .7 + Math.random() * .3);
    const glyph = () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
    const step = (now) => {
      const k = clamp((now - start) / duration, 0, 1);
      node.textContent = chars.map((ch, i) => (ch === " " || k >= lock[i] ? ch : glyph())).join("");
      if (k < 1) node._dec = requestAnimationFrame(step); else node.textContent = final;
    };
    node._dec = requestAnimationFrame(step);
  }

  // 數字從目前值（第一次從 0）滾動到新值
  function countUp(node, value, { duration = 1400, delay = 0, format = (v) => nf.format(Math.round(v)), from } = {}) {
    if (typeof value !== "number" || !isFinite(value)) { node.textContent = String(value); node._v = undefined; return; }
    const start = from ?? node._v ?? 0;
    node._v = value;
    if (reduce || start === value) { node.textContent = format(value); return; }
    cancelAnimationFrame(node._cu);
    const t0 = performance.now() + delay;
    const step = (now) => {
      const k = clamp((now - t0) / duration, 0, 1);
      node.textContent = format(start + (value - start) * easeOutExpo(k));
      if (k < 1) node._cu = requestAnimationFrame(step);
    };
    node.textContent = format(start);
    node._cu = requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- 迷你走勢線：由左往右畫出，最新一點有心跳光點
  const SVG = "http://www.w3.org/2000/svg";
  function spark(values, color = "var(--accent)") {
    const wrap = document.createElement("div");
    wrap.className = "spark-wrap";
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 100 34"); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("aria-hidden", "true");
    const v = values.length > 1 ? values : [0, ...values, 0];
    const mx = Math.max(...v), mn = Math.min(...v);
    const ys = v.map((n) => 31 - (n - mn) / ((mx - mn) || 1) * 27);
    const pts = ys.map((y, i) => `${(i / (v.length - 1) * 100).toFixed(1)},${y.toFixed(1)}`);
    const area = document.createElementNS(SVG, "polygon");
    area.setAttribute("points", `0,34 ${pts.join(" ")} 100,34`); area.setAttribute("fill", color); area.setAttribute("class", "spark-area");
    const line = document.createElementNS(SVG, "polyline");
    line.setAttribute("points", pts.join(" ")); line.setAttribute("fill", "none"); line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", "1.6"); line.setAttribute("vector-effect", "non-scaling-stroke"); line.setAttribute("pathLength", "1");
    line.setAttribute("class", "spark");
    svg.append(area, line);
    // 心跳光點用 HTML 疊在最後一點上（SVG 被拉伸，圓會變形）
    const beacon = document.createElement("span");
    beacon.className = "beacon"; beacon.style.top = `${(ys[ys.length - 1] / 34 * 100).toFixed(1)}%`; beacon.style.setProperty("--bc", color);
    wrap.append(svg, beacon);
    return wrap;
  }

  // ---------------------------------------------------------------- 環形儀表：從 0 充能到目標，末端有發光光點，平常呼吸脈動
  function gauge(label, color) {
    const C = 2 * Math.PI * 34, ARC = C * .75;
    const box = document.createElement("div");
    box.className = "gauge";
    box.style.setProperty("--gc", color);
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 86 86"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label);
    const g = document.createElementNS(SVG, "g"); g.setAttribute("transform", "rotate(135 43 43)");
    const ring = (cls, offset, width = 6) => { const c = document.createElementNS(SVG, "circle");
      for (const [k, v] of Object.entries({ cx: 43, cy: 43, r: 34, fill: "none", "stroke-width": width, "stroke-dasharray": `${ARC} ${C}`, "stroke-dashoffset": offset, class: cls })) c.setAttribute(k, v);
      return c; };
    const halo = ring("halo", ARC, 12), val = ring("val", ARC);
    g.append(ring("trk", 0), halo, val);
    const ticks = document.createElementNS(SVG, "circle"), tickSvg = document.createElementNS(SVG, "svg");
    for (const [k, v] of Object.entries({ cx: 43, cy: 43, r: 41, fill: "none", "stroke-width": 2, "stroke-dasharray": "1 5", class: "ticks" })) ticks.setAttribute(k, v);
    tickSvg.setAttribute("viewBox", "0 0 86 86"); tickSvg.setAttribute("aria-hidden", "true"); tickSvg.setAttribute("class", "gauge-ticks"); tickSvg.append(ticks);
    const tipHalo = document.createElementNS(SVG, "circle"), tip = document.createElementNS(SVG, "circle");
    for (const [el, r, cls] of [[tipHalo, 7, "tip-halo"], [tip, 3.2, "tip"]]) { el.setAttribute("r", r); el.setAttribute("class", cls); el.setAttribute("cx", 43); el.setAttribute("cy", 43); }
    const text = document.createElementNS(SVG, "text");
    text.setAttribute("x", 43); text.setAttribute("y", 48); text.setAttribute("text-anchor", "middle"); text.textContent = "—";
    svg.append(g, tipHalo, tip, text);
    const small = document.createElement("small"); small.textContent = label;
    box.append(svg, tickSvg, small);
    let current = 0, raf = 0;
    const paint = (f) => {
      const off = (ARC * (1 - f)).toFixed(2);
      val.setAttribute("stroke-dashoffset", off); halo.setAttribute("stroke-dashoffset", off);
      const a = (135 + 270 * f) * Math.PI / 180, x = (43 + 34 * Math.cos(a)).toFixed(2), y = (43 + 34 * Math.sin(a)).toFixed(2);
      tip.setAttribute("cx", x); tip.setAttribute("cy", y); tipHalo.setAttribute("cx", x); tipHalo.setAttribute("cy", y);
      box.classList.toggle("empty", f <= .001);
    };
    paint(0);
    return {
      el: box,
      set(fraction, shown, { delay = 0 } = {}) {
        const target = clamp(fraction || 0, 0, 1), from = current;
        current = target;
        if (typeof shown === "number") countUp(text, shown, { duration: 1500, delay }); else text.textContent = String(shown);
        if (reduce) { paint(target); return; }
        cancelAnimationFrame(raf);
        const t0 = performance.now() + delay;
        const step = (now) => { const k = clamp((now - t0) / 1500, 0, 1); paint(from + (target - from) * easeOutCubic(k)); if (k < 1) raf = requestAnimationFrame(step); };
        raf = requestAnimationFrame(step);
      },
    };
  }

  // ---------------------------------------------------------------- 互動：3D 傾斜＋聚光燈、磁吸按鈕、點擊衝擊波、流光繞邊
  function tilt(el, max = 7) {
    if (!el) return;
    const spot = document.createElement("span"); spot.className = "spot"; spot.setAttribute("aria-hidden", "true");
    el.classList.add("tilt"); el.append(spot);
    if (reduce || !fine) return;
    let raf = 0, x = .5, y = .5, k = max;
    const apply = () => {
      raf = 0;
      el.style.setProperty("--mx", (x * 100).toFixed(1) + "%"); el.style.setProperty("--my", (y * 100).toFixed(1) + "%");
      el.style.transform = `perspective(1000px) rotateX(${((.5 - y) * k).toFixed(2)}deg) rotateY(${((x - .5) * k).toFixed(2)}deg) translateZ(0)`;
    };
    el.addEventListener("pointermove", (e) => {
      if (el.classList.contains("fx-in")) return;
      const r = el.getBoundingClientRect(); x = (e.clientX - r.left) / r.width; y = (e.clientY - r.top) / r.height;
      k = max * Math.min(1, 380 / r.width);          // 大卡片傾斜角度小一點，不會暈
      if (!raf) raf = requestAnimationFrame(apply);
    }, { passive: true });
    el.addEventListener("pointerleave", () => { cancelAnimationFrame(raf); raf = 0; el.style.transform = ""; });
  }

  // 磁吸：游標靠近按鈕時，按鈕往游標偏一點（全頁共用一個監聽）
  const magnets = new Set();
  let magnetPending = false, mx = -999, my = -999;
  const magnetFrame = () => {
    magnetPending = false;
    const rects = [];
    for (const el of magnets) { if (!el.isConnected) magnets.delete(el); else rects.push([el, el.getBoundingClientRect()]); }
    for (const [el, r] of rects) {
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2, dx = mx - cx, dy = my - cy;
      const reach = Math.max(r.width, r.height) / 2 + 46, d = Math.hypot(dx, dy);
      if (d < reach) { const p = 1 - d / reach; el.style.transform = `translate(${(dx * .32 * p).toFixed(1)}px, ${(dy * .45 * p).toFixed(1)}px)`; el.classList.add("magnet-on"); }
      else if (el.classList.contains("magnet-on")) { el.style.transform = ""; el.classList.remove("magnet-on"); }
    }
  };
  if (!reduce && fine) addEventListener("pointermove", (e) => { mx = e.clientX; my = e.clientY; if (magnets.size && !magnetPending) { magnetPending = true; requestAnimationFrame(magnetFrame); } }, { passive: true });
  function magnetic(...els) { for (const el of els) { if (!el) continue; el.classList.add("fx-btn"); if (!reduce && fine) magnets.add(el); } }
  // 點擊衝擊波：任何 .fx-btn 按下去都會從點擊位置擴散一圈能量波
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest && e.target.closest(".fx-btn");
    if (!btn || reduce) return;
    const r = btn.getBoundingClientRect(), size = Math.max(r.width, r.height) * 2.4;
    const wave = document.createElement("span"); wave.className = "ripple"; wave.setAttribute("aria-hidden", "true");
    wave.style.width = wave.style.height = size + "px";
    wave.style.left = (e.clientX - r.left - size / 2) + "px"; wave.style.top = (e.clientY - r.top - size / 2) + "px";
    btn.append(wave); setTimeout(() => wave.remove(), 750);
  });

  // 流光繞邊：一道青紫光束沿著卡片邊框繞圈（旋轉的是內層元素，交給 GPU）
  function beam(el) {
    if (!el || el.querySelector(":scope > .beam-ring")) return el;
    const ring = document.createElement("span"); ring.className = "beam-ring"; ring.setAttribute("aria-hidden", "true");
    ring.append(document.createElement("i"));
    el.append(ring);
    return el;
  }

  // ---------------------------------------------------------------- 進場：全息掃描光束＋卡片交錯模糊浮現
  function laser() {
    if (reduce) return;
    const el = document.createElement("div"); el.className = "laser"; el.setAttribute("aria-hidden", "true");
    document.body.append(el); setTimeout(() => el.remove(), 1300);
  }
  function stagger(els, step = 60) {
    if (reduce) return;
    els.forEach((el, i) => {
      el.classList.remove("fx-in"); void el.offsetWidth;
      el.style.setProperty("--d", `${i * step}ms`); el.classList.add("fx-in");
      el.addEventListener("animationend", function done(e) { if (e.target !== el) return; el.classList.remove("fx-in"); el.removeEventListener("animationend", done); });
    });
  }

  // ---------------------------------------------------------------- 雙向錯位慣性跑馬燈：滑鼠移入減速停住，捲動頁面會加速滑行
  function kinetic(el, speed = 46) {
    const tracks = [...el.querySelectorAll(".track")].map((t, i) => ({ el: t, dir: t.dataset.dir === "right" ? 1 : -1, x: 0, half: 0, mul: 1 + i * .25 }));
    const measure = () => tracks.forEach((t) => { t.half = t.el.scrollWidth / 2; if (t.dir > 0 && t.x === 0) t.x = -t.half; });
    measure();
    const ro = new ResizeObserver(measure); tracks.forEach((t) => ro.observe(t.el));
    let hover = false, ease = 1, boost = 0, last = 0;
    el.addEventListener("pointerenter", () => { hover = true; });
    el.addEventListener("pointerleave", () => { hover = false; });
    const scrolls = new WeakMap();
    const onScroll = (e) => {
      const target = e.target === document ? document.scrollingElement : e.target;
      if (!target || typeof target.scrollTop !== "number") return;
      const prev = scrolls.get(target) ?? target.scrollTop; scrolls.set(target, target.scrollTop);
      boost = clamp(boost + (target.scrollTop - prev) * 9, -1800, 1800);
    };
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    const stopLoop = animate(el, (now) => {
      const dt = last ? Math.min(.05, (now - last) / 1000) : 0; last = now;
      ease += ((hover ? 0 : 1) - ease) * (1 - Math.pow(.02, dt));       // 優雅減速／恢復
      boost *= Math.pow(.08, dt);                                       // 慣性慢慢消失
      for (const t of tracks) {
        if (!t.half) continue;
        const v = (speed * ease + Math.abs(boost)) * t.mul * t.dir * (boost < 0 ? -1 : 1);
        t.x += v * dt;
        if (t.x <= -t.half) t.x += t.half; else if (t.x > 0) t.x -= t.half;
        t.el.style.transform = `translate3d(${t.x.toFixed(2)}px,0,0)`;
      }
    });
    return () => { stopLoop(); ro.disconnect(); document.removeEventListener("scroll", onScroll, { capture: true }); };
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

  // ---------------------------------------------------------------- 動畫開關（記在瀏覽器，重新載入套用）
  function setPreference(value) { try { localStorage.setItem("motion", value); } catch (e) { /* 存不了就只這次 */ } location.reload(); }

  return { reduce, animate, bg, Nebula, Holo, scramble, decode, countUp, spark, gauge, tilt, magnetic, beam, laser, stagger, kinetic,
           boot, wipe, alarm, typewriter, setPreference };
})();
