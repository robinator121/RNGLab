// Canvas charts for the laboratory: no dependencies, no random numbers.
// Plot: line / step / points / band / error-bar series with reference lines, event markers,
// wheel zoom, drag pan, shift-drag box zoom, double-click reset, hover read-out and a
// cursor shared by every plot of the same sync group.

export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || name;
export const color = (c) => (c && c.startsWith("var(")) ? css(c.slice(4, -1)) : (c || css("--s1"));
export const PALETTE = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"].map((v) => `var(${v})`);

export function fmt(v, digits = 4) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (typeof v !== "number") return String(v);
  if (!Number.isFinite(v)) return v > 0 ? "∞" : "−∞";
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e6 || a < 1e-4)) return v.toExponential(Math.max(1, digits - 2)).replace("e", "e");
  if (Number.isInteger(v) && a < 1e6) return String(v);
  return Number(v.toPrecision(digits)).toString();
}

function niceStep(span, target) {
  const raw = span / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const r = raw / mag;
  return (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * mag;
}
export function ticks(min, max, target = 6, log = false) {
  if (!(max > min)) return [min];
  if (log) {
    const out = [];
    const lo = Math.floor(Math.log10(min)), hi = Math.ceil(Math.log10(max));
    const every = Math.max(1, Math.ceil((hi - lo) / target));
    for (let e = lo; e <= hi; e += every) { const v = Math.pow(10, e); if (v >= min * 0.999 && v <= max * 1.001) out.push(v); }
    return out.length ? out : [min, max];
  }
  const step = niceStep(max - min, target);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

class Bus {  // shared hover cursor per sync group
  constructor() { this.groups = new Map(); }
  join(group, plot) { if (!group) return; if (!this.groups.has(group)) this.groups.set(group, new Set()); this.groups.get(group).add(plot); }
  leave(group, plot) { this.groups.get(group)?.delete(plot); }
  emit(group, from, x) { if (!group) return; for (const p of this.groups.get(group) || []) if (p !== from) { p.cursorX = x; p.requestDraw(); } }
}
export const bus = new Bus();

export class CanvasBox {
  constructor(container, cls = "chart") {
    this.el = document.createElement("div");
    this.el.className = cls;
    this.canvas = document.createElement("canvas");
    this.tip = document.createElement("div");
    this.tip.className = "tooltip";
    this.el.append(this.canvas, this.tip);
    container.append(this.el);
    this.ctx = this.canvas.getContext("2d");
    this._pending = false;
    this.ro = new ResizeObserver(() => this.requestDraw());
    this.ro.observe(this.el);
  }
  size() {
    const r = this.el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(10, Math.round(r.width)), h = Math.max(10, Math.round(r.height));
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
    }
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }
  requestDraw() {
    if (this._pending) return;
    this._pending = true;
    requestAnimationFrame(() => { this._pending = false; if (this.el.isConnected) this.draw(); });
  }
  showTip(html, x, y) {
    this.tip.innerHTML = html; this.tip.style.display = "block";
    const r = this.el.getBoundingClientRect(); const tw = this.tip.offsetWidth, th = this.tip.offsetHeight;
    this.tip.style.left = `${Math.min(Math.max(4, x + 12), r.width - tw - 4)}px`;
    this.tip.style.top = `${Math.min(Math.max(4, y - th - 8), r.height - th - 4)}px`;
  }
  hideTip() { this.tip.style.display = "none"; }
  destroy() { this.ro.disconnect(); this.el.remove(); }
  toPNG(name) { const a = document.createElement("a"); a.href = this.canvas.toDataURL("image/png"); a.download = `${name}.png`; a.click(); }
  draw() {}
}

export class Plot extends CanvasBox {
  constructor(container, opts = {}) {
    super(container, `chart ${opts.size || ""}`);
    this.opts = opts;
    this.series = []; this.refs = []; this.vlines = []; this.marks = []; this.bands = [];
    this.view = null; this.cursorX = null; this.hover = null;
    this.margin = { l: 54, r: 12, t: 8, b: 28 };
    bus.join(opts.sync, this);
    this._wire();
  }
  destroy() { bus.leave(this.opts.sync, this); super.destroy(); }
  setData({ series, refs, vlines, marks } = {}) {
    if (series) this.series = series;
    if (refs) this.refs = refs;
    if (vlines) this.vlines = vlines;
    if (marks) this.marks = marks;
    this.requestDraw();
  }
  reset() { this.view = null; this.requestDraw(); }

  dataRange() {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const lx = this.opts.logx, ly = this.opts.logy;
    for (const s of this.series) {
      if (s.hidden) continue;
      const xs = s.x, n = xs.length;
      for (let i = 0; i < n; i++) {
        const x = xs[i]; if (x === null || !Number.isFinite(x) || (lx && x <= 0)) continue;
        const ys = [s.y?.[i], s.y2?.[i], s.lo?.[i], s.hi?.[i]];
        let any = false;
        for (const y of ys) { if (y === null || y === undefined || !Number.isFinite(y) || (ly && y <= 0)) continue; any = true; if (y < y0) y0 = y; if (y > y1) y1 = y; }
        if (any) { if (x < x0) x0 = x; if (x > x1) x1 = x; }
      }
    }
    for (const r of this.refs) if (Number.isFinite(r.y) && !(ly && r.y <= 0) && !r.noRange) { y0 = Math.min(y0, r.y); y1 = Math.max(y1, r.y); }
    if (this.opts.xlim) [x0, x1] = this.opts.xlim;
    if (this.opts.ylim) [y0, y1] = this.opts.ylim;
    if (!Number.isFinite(x0)) { x0 = 0; x1 = 1; }
    if (!Number.isFinite(y0)) { y0 = 0; y1 = 1; }
    if (x0 === x1) { x0 -= 0.5; x1 += 0.5; }
    if (y0 === y1) { const d = Math.abs(y0) * 0.05 || 0.5; y0 -= d; y1 += d; }
    if (!this.opts.ylim) {
      if (ly) { y0 /= 1.25; y1 *= 1.25; } else { const pad = (y1 - y0) * 0.06; y0 -= pad; y1 += pad; }
    }
    return { x0, x1, y0, y1 };
  }
  range() { return this.view || this.dataRange(); }
  tx(x, r, w) { const { l } = this.margin; const W = w - l - this.margin.r;
    return this.opts.logx ? l + (Math.log10(x) - Math.log10(r.x0)) / (Math.log10(r.x1) - Math.log10(r.x0)) * W : l + (x - r.x0) / (r.x1 - r.x0) * W; }
  ty(y, r, h) { const { t, b } = this.margin; const H = h - t - b;
    return this.opts.logy ? t + H - (Math.log10(y) - Math.log10(r.y0)) / (Math.log10(r.y1) - Math.log10(r.y0)) * H : t + H - (y - r.y0) / (r.y1 - r.y0) * H; }
  ix(px, r, w) { const { l } = this.margin; const W = w - l - this.margin.r; const f = (px - l) / W;
    return this.opts.logx ? Math.pow(10, Math.log10(r.x0) + f * (Math.log10(r.x1) - Math.log10(r.x0))) : r.x0 + f * (r.x1 - r.x0); }
  iy(py, r, h) { const { t, b } = this.margin; const H = h - t - b; const f = (t + H - py) / H;
    return this.opts.logy ? Math.pow(10, Math.log10(r.y0) + f * (Math.log10(r.y1) - Math.log10(r.y0))) : r.y0 + f * (r.y1 - r.y0); }

  draw() {
    const { w, h } = this.size();
    const ctx = this.ctx, r = this.range(), m = this.margin;
    ctx.clearRect(0, 0, w, h);
    ctx.font = `10px ${css("--mono")}`;
    // grid + ticks
    ctx.strokeStyle = css("--grid"); ctx.fillStyle = css("--muted"); ctx.lineWidth = 1;
    const xt = ticks(r.x0, r.x1, Math.max(2, Math.floor((w - m.l) / 90)), this.opts.logx);
    const yt = ticks(r.y0, r.y1, Math.max(2, Math.floor((h - m.t - m.b) / 38)), this.opts.logy);
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (const v of xt) { const X = Math.round(this.tx(v, r, w)) + 0.5; ctx.beginPath(); ctx.moveTo(X, m.t); ctx.lineTo(X, h - m.b); ctx.stroke(); ctx.fillText((this.opts.xfmt || fmt)(v), X, h - m.b + 5); }
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const v of yt) { const Y = Math.round(this.ty(v, r, h)) + 0.5; ctx.beginPath(); ctx.moveTo(m.l, Y); ctx.lineTo(w - m.r, Y); ctx.stroke(); ctx.fillText((this.opts.yfmt || fmt)(v), m.l - 5, Y); }
    ctx.strokeStyle = css("--axis"); ctx.beginPath(); ctx.moveTo(m.l + 0.5, m.t); ctx.lineTo(m.l + 0.5, h - m.b + 0.5); ctx.lineTo(w - m.r, h - m.b + 0.5); ctx.stroke();
    if (this.opts.xlabel) { ctx.textAlign = "right"; ctx.textBaseline = "bottom"; ctx.fillStyle = css("--faint"); ctx.fillText(this.opts.xlabel, w - m.r, h - m.b - 3); }
    if (this.opts.ylabel) { ctx.save(); ctx.translate(11, m.t + 4); ctx.rotate(-Math.PI / 2); ctx.textAlign = "right"; ctx.textBaseline = "top"; ctx.fillStyle = css("--faint"); ctx.fillText(this.opts.ylabel, 0, 0); ctx.restore(); }
    // clip plotting area
    ctx.save(); ctx.beginPath(); ctx.rect(m.l + 1, m.t, w - m.l - m.r - 1, h - m.t - m.b); ctx.clip();
    for (const s of this.series) if (!s.hidden && s.kind === "band") this._band(s, r, w, h);
    for (const ref of this.refs) {
      if (!Number.isFinite(ref.y) || (this.opts.logy && ref.y <= 0)) continue;
      const Y = this.ty(ref.y, r, h); ctx.strokeStyle = color(ref.color || "var(--reference)"); ctx.setLineDash(ref.dash || [5, 4]); ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(m.l, Y); ctx.lineTo(w - m.r, Y); ctx.stroke(); ctx.setLineDash([]);
      if (ref.label) { ctx.fillStyle = color(ref.color || "var(--reference)"); ctx.textAlign = "left"; ctx.textBaseline = "bottom"; ctx.fillText(ref.label, m.l + 4, Y - 2); }
    }
    for (const vl of this.vlines) {
      if (!Number.isFinite(vl.x)) continue;
      const X = this.tx(vl.x, r, w); ctx.strokeStyle = color(vl.color || "var(--warn)"); ctx.setLineDash(vl.dash || [3, 3]); ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(X, m.t); ctx.lineTo(X, h - m.b); ctx.stroke(); ctx.setLineDash([]);
      if (vl.label) { ctx.fillStyle = color(vl.color || "var(--warn)"); ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.fillText(vl.label, X + 3, m.t + 2); }
    }
    for (const s of this.series) if (!s.hidden && s.kind !== "band") this._series(s, r, w, h);
    for (const mk of this.marks) {
      if (!Number.isFinite(mk.x)) continue;
      const X = this.tx(mk.x, r, w), Y = Number.isFinite(mk.y) ? this.ty(mk.y, r, h) : m.t + 8;
      ctx.fillStyle = color(mk.color || "var(--bad)"); ctx.beginPath(); ctx.moveTo(X, Y - 6); ctx.lineTo(X + 5, Y + 3); ctx.lineTo(X - 5, Y + 3); ctx.closePath(); ctx.fill();
    }
    // cursor
    const cx = this.hover ? this.hover.x : this.cursorX;
    if (cx !== null && cx !== undefined && Number.isFinite(cx)) {
      const X = this.tx(cx, r, w); ctx.strokeStyle = css("--muted"); ctx.globalAlpha = 0.6; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(X, m.t); ctx.lineTo(X, h - m.b); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
    if (this.box) { ctx.fillStyle = "rgba(76,194,255,0.12)"; ctx.strokeStyle = css("--accent");
      const { a, b } = this.box; ctx.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y)); }
    ctx.restore();
    if (this.view) { ctx.fillStyle = css("--faint"); ctx.textAlign = "right"; ctx.textBaseline = "top"; ctx.fillText("zoomed · dbl-click resets", w - m.r - 2, m.t + 2); }
  }
  _band(s, r, w, h) {
    const ctx = this.ctx; const n = s.x.length; if (!n) return;
    ctx.fillStyle = color(s.color); ctx.globalAlpha = s.alpha ?? 0.18; ctx.beginPath();
    let started = false;
    for (let i = 0; i < n; i++) { if (!Number.isFinite(s.y[i]) || !Number.isFinite(s.x[i])) continue; const X = this.tx(s.x[i], r, w), Y = this.ty(s.y[i], r, h); if (!started) { ctx.moveTo(X, Y); started = true; } else ctx.lineTo(X, Y); }
    for (let i = n - 1; i >= 0; i--) { if (!Number.isFinite(s.y2[i]) || !Number.isFinite(s.x[i])) continue; ctx.lineTo(this.tx(s.x[i], r, w), this.ty(s.y2[i], r, h)); }
    ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
  }
  _series(s, r, w, h) {
    const ctx = this.ctx, xs = s.x, ys = s.y, n = xs.length; if (!n) return;
    const c = color(s.color); ctx.strokeStyle = c; ctx.fillStyle = c; ctx.lineWidth = s.width || 1.3; ctx.setLineDash(s.dash || []);
    ctx.globalAlpha = s.alpha ?? 1;
    const lx = this.opts.logx, ly = this.opts.logy;
    const ok = (x, y) => Number.isFinite(x) && Number.isFinite(y) && !(lx && x <= 0) && !(ly && y <= 0);
    if (s.kind === "points" || s.kind === "errbar") {
      const rad = s.radius || 2.2;
      for (let i = 0; i < n; i++) {
        if (!ok(xs[i], ys[i])) continue; const X = this.tx(xs[i], r, w), Y = this.ty(ys[i], r, h);
        if (s.kind === "errbar" && Number.isFinite(s.lo?.[i]) && Number.isFinite(s.hi?.[i])) { ctx.beginPath(); ctx.moveTo(X, this.ty(s.lo[i], r, h)); ctx.lineTo(X, this.ty(s.hi[i], r, h)); ctx.stroke(); }
        ctx.beginPath(); ctx.arc(X, Y, rad, 0, 2 * Math.PI); ctx.fill();
      }
    } else {
      const W = w - this.margin.l - this.margin.r;
      ctx.beginPath(); let pen = false;
      if (n > W * 3 && s.kind !== "step") {  // min/max per pixel column
        let col = null, mn = 0, mx = 0, first = 0, last = 0;
        const flush = () => { if (col === null) return; const yA = this.ty(first, r, h), yB = this.ty(mn, r, h), yC = this.ty(mx, r, h), yD = this.ty(last, r, h);
          if (!pen) { ctx.moveTo(col, yA); pen = true; } else ctx.lineTo(col, yA); ctx.lineTo(col, yB); ctx.lineTo(col, yC); ctx.lineTo(col, yD); };
        for (let i = 0; i < n; i++) {
          if (!ok(xs[i], ys[i])) { flush(); col = null; pen = false; continue; }
          const X = Math.round(this.tx(xs[i], r, w)); const y = ys[i];
          if (X !== col) { flush(); col = X; mn = mx = first = last = y; } else { if (y < mn) mn = y; if (y > mx) mx = y; last = y; }
        }
        flush();
      } else {
        let prevY = null;
        for (let i = 0; i < n; i++) {
          if (!ok(xs[i], ys[i])) { pen = false; continue; }
          const X = this.tx(xs[i], r, w), Y = this.ty(ys[i], r, h);
          if (!pen) { ctx.moveTo(X, Y); pen = true; } else { if (s.kind === "step" && prevY !== null) ctx.lineTo(X, prevY); ctx.lineTo(X, Y); }
          prevY = Y;
        }
      }
      ctx.stroke();
    }
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }
  nearest(s, x) {
    const xs = s.x; let lo = 0, hi = xs.length - 1; if (hi < 0) return -1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < x) lo = mid; else hi = mid; }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
  }
  _wire() {
    const c = this.canvas; let drag = null;
    const pos = (e) => { const b = c.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; };
    c.addEventListener("wheel", (e) => {
      e.preventDefault(); const { w, h } = this.size(); const r = { ...this.range() }; const p = pos(e);
      const k = e.deltaY > 0 ? 1.18 : 1 / 1.18;
      if (e.shiftKey) { const y = this.iy(p.y, r, h); if (this.opts.logy) { r.y0 = Math.pow(10, Math.log10(y) - (Math.log10(y) - Math.log10(r.y0)) * k); r.y1 = Math.pow(10, Math.log10(y) + (Math.log10(r.y1) - Math.log10(y)) * k); } else { r.y0 = y - (y - r.y0) * k; r.y1 = y + (r.y1 - y) * k; } }
      else { const x = this.ix(p.x, r, w); if (this.opts.logx) { r.x0 = Math.pow(10, Math.log10(x) - (Math.log10(x) - Math.log10(r.x0)) * k); r.x1 = Math.pow(10, Math.log10(x) + (Math.log10(r.x1) - Math.log10(x)) * k); } else { r.x0 = x - (x - r.x0) * k; r.x1 = x + (r.x1 - x) * k; } }
      this.view = r; this.requestDraw();
    }, { passive: false });
    c.addEventListener("mousedown", (e) => { drag = { start: pos(e), r: { ...this.range() }, box: e.shiftKey }; });
    window.addEventListener("mouseup", () => {
      if (drag?.box && this.box) { const { w, h } = this.size(); const r = drag.r; const { a, b } = this.box;
        if (Math.abs(b.x - a.x) > 4 && Math.abs(b.y - a.y) > 4) this.view = { x0: this.ix(Math.min(a.x, b.x), r, w), x1: this.ix(Math.max(a.x, b.x), r, w), y0: this.iy(Math.max(a.y, b.y), r, h), y1: this.iy(Math.min(a.y, b.y), r, h) }; }
      drag = null; this.box = null; this.requestDraw();
    });
    c.addEventListener("mousemove", (e) => {
      const p = pos(e); const { w, h } = this.size(); const r = this.range();
      if (drag) {
        if (drag.box) { this.box = { a: drag.start, b: p }; this.requestDraw(); return; }
        const dx = p.x - drag.start.x, dy = p.y - drag.start.y; if (Math.abs(dx) + Math.abs(dy) < 2) return;
        const r0 = drag.r; const sx = this.ix(drag.start.x, r0, w) - this.ix(p.x, r0, w), sy = this.iy(drag.start.y, r0, h) - this.iy(p.y, r0, h);
        this.view = this.opts.logx || this.opts.logy ? { ...r0 } : { x0: r0.x0 + sx, x1: r0.x1 + sx, y0: r0.y0 + sy, y1: r0.y1 + sy };
        this.requestDraw(); return;
      }
      const x = this.ix(p.x, r, w); this.hover = { x, px: p.x, py: p.y }; bus.emit(this.opts.sync, this, x);
      const rows = [`<b>${this.opts.xname || "x"}</b> ${fmt(x, 6)}`];
      for (const s of this.series) {
        if (s.hidden || !s.x.length || s.noTip) continue; const i = this.nearest(s, x); if (i < 0) continue;
        let v = `${fmt(s.y[i], 6)}`; if (s.kind === "band") v = `[${fmt(s.y[i], 5)}, ${fmt(s.y2[i], 5)}]`;
        if (s.kind === "errbar" && s.lo) v += ` [${fmt(s.lo[i], 5)}, ${fmt(s.hi[i], 5)}]`;
        rows.push(`<span style="color:${color(s.color)}">■</span> ${s.name}: ${v} <span style="opacity:.6">@${fmt(s.x[i], 6)}</span>`);
      }
      this.showTip(rows.join("<br>"), p.x, p.y); this.requestDraw();
    });
    c.addEventListener("mouseleave", () => { this.hover = null; this.hideTip(); bus.emit(this.opts.sync, this, null); this.requestDraw(); });
    c.addEventListener("dblclick", () => this.reset());
  }
}

// Sequential colour map (perceptually ordered, dark to bright) and a diverging one.
const SEQ = [[13, 8, 135], [84, 2, 163], [139, 10, 165], [185, 50, 137], [219, 92, 104], [244, 136, 73], [254, 188, 43], [240, 249, 33]];
export function seqColor(t) {
  t = Math.min(1, Math.max(0, t)); const f = t * (SEQ.length - 1); const i = Math.min(SEQ.length - 2, Math.floor(f)); const u = f - i;
  const a = SEQ[i], b = SEQ[i + 1]; return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * u)).join(",")})`;
}
export function divColor(t) {  // t in [-1, 1]
  t = Math.max(-1, Math.min(1, t)); const lo = [64, 128, 255], mid = [36, 44, 54], hi = [255, 96, 80];
  const a = t < 0 ? mid : mid, b = t < 0 ? lo : hi, u = Math.abs(t);
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * u)).join(",")})`;
}

// Matrix heat map with row/column labels and a read-out on hover.
export class Heat extends CanvasBox {
  constructor(container, opts = {}) { super(container, `chart ${opts.size || ""}`); this.opts = opts; this.m = null; this._wire(); }
  setData(m) { this.m = m; this.requestDraw(); }   // {rows, cols, values[r][c], text?[r][c], vmin, vmax, diverging, fmt}
  layout(w, h) {
    const m = this.m; const left = Math.min(150, 8 + 6.4 * Math.max(...m.rows.map((s) => String(s).length), 1)); const top = 8, bottom = 30;
    return { left, top, cw: (w - left - 8) / m.cols.length, ch: (h - top - bottom) / m.rows.length, bottom };
  }
  draw() {
    const { w, h } = this.size(); const ctx = this.ctx; ctx.clearRect(0, 0, w, h); const m = this.m;
    if (!m || !m.rows.length || !m.cols.length) { ctx.fillStyle = css("--faint"); ctx.font = `12px ${css("--sans")}`; ctx.fillText("no data yet", 12, 22); return; }
    const L = this.layout(w, h); ctx.font = `10px ${css("--mono")}`;
    let vmin = m.vmin, vmax = m.vmax;
    if (vmin === undefined || vmax === undefined) { const flat = m.values.flat().filter(Number.isFinite); vmin = Math.min(...flat); vmax = Math.max(...flat); if (!(vmax > vmin)) vmax = vmin + 1; }
    for (let r = 0; r < m.rows.length; r++) for (let c = 0; c < m.cols.length; c++) {
      const v = m.values[r][c]; const x = L.left + c * L.cw, y = L.top + r * L.ch;
      ctx.fillStyle = !Number.isFinite(v) ? css("--bg2") : m.diverging ? divColor(v / Math.max(Math.abs(vmin), Math.abs(vmax))) : seqColor((v - vmin) / (vmax - vmin));
      ctx.fillRect(x + 0.5, y + 0.5, Math.max(0, L.cw - 1), Math.max(0, L.ch - 1));
      const t = m.text?.[r]?.[c]; if (t !== undefined && L.cw > 26 && L.ch > 13) { ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(t, x + L.cw / 2, y + L.ch / 2); }
    }
    ctx.fillStyle = css("--muted"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
    if (L.ch >= 9) m.rows.forEach((s, r) => ctx.fillText(String(s), L.left - 5, L.top + (r + 0.5) * L.ch));
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const every = Math.max(1, Math.ceil(m.cols.length / Math.max(1, (w - L.left) / 52)));
    m.cols.forEach((s, c) => { if (c % every === 0) ctx.fillText(String(s), L.left + (c + 0.5) * L.cw, h - L.bottom + 6); });
  }
  _wire() {
    this.canvas.addEventListener("mousemove", (e) => {
      if (!this.m) return; const b = this.canvas.getBoundingClientRect(); const x = e.clientX - b.left, y = e.clientY - b.top;
      const { w, h } = this.size(); const L = this.layout(w, h); const c = Math.floor((x - L.left) / L.cw), r = Math.floor((y - L.top) / L.ch);
      if (r < 0 || c < 0 || r >= this.m.rows.length || c >= this.m.cols.length) return this.hideTip();
      const v = this.m.values[r][c]; this.showTip(`${this.m.rows[r]} · ${this.m.cols[c]}<br><b>${(this.m.fmt || fmt)(v)}</b>${this.m.hint ? `<br>${this.m.hint(r, c)}` : ""}`, x, y);
    });
    this.canvas.addEventListener("mouseleave", () => this.hideTip());
  }
}

// Free-form canvas with a draw callback (ring polymer, lattice, scatter, raster, bars).
export class Sketch extends CanvasBox {
  constructor(container, painter, opts = {}) { super(container, `chart ${opts.size || ""}`); this.painter = painter; this.data = null; this.opts = opts;
    if (opts.hover) { this.canvas.addEventListener("mousemove", (e) => { const b = this.canvas.getBoundingClientRect(); const html = opts.hover(this.data, e.clientX - b.left, e.clientY - b.top, this.size()); if (html) this.showTip(html, e.clientX - b.left, e.clientY - b.top); else this.hideTip(); });
      this.canvas.addEventListener("mouseleave", () => this.hideTip()); } }
  setData(d) { this.data = d; this.requestDraw(); }
  draw() { const { w, h } = this.size(); this.ctx.clearRect(0, 0, w, h); if (this.data) this.painter(this.ctx, w, h, this.data); else { this.ctx.fillStyle = css("--faint"); this.ctx.font = `12px ${css("--sans")}`; this.ctx.fillText("waiting for data", 12, 22); } }
}
