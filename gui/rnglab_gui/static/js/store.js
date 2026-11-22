// RunStore: the state of one run as reconstructed from its telemetry messages.
// The same store serves a live run (server-sent events) and a replay (archived
// telemetry fed by the Player), so every view draws recorded and live runs alike.

export class RunStore {
  constructor(runId) { this.runId = runId; this.reset(); }
  reset() {
    this.hello = null; this.meta = {}; this.series = {}; this.state = {}; this.scalars = {}; this.streams = {};
    this.append = {}; this.matrix = {}; this.table = {}; this.hist = {}; this.events = []; this.statusLog = [];
    this.result = null; this.integrity = null; this.done = null; this.error = null; this.sim = null; this.progress = null;
    this.wall = 0; this.controller = "running"; this.lastSeq = 0; this.version = 0; this.closed = null; this.frames = 0;
  }
  ingest(m) {
    if (m.seq) { if (m.seq <= this.lastSeq && m.t !== "closed") return; this.lastSeq = m.seq; }
    if (typeof m.wall === "number") this.wall = m.wall;
    switch (m.t) {
      case "hello": this.hello = m; break;
      case "meta": this.meta = { ...this.meta, ...m.data }; break;
      case "frame": this.frames += 1; this.sim = m.sim; this.progress = m.progress; this.merge(m.data || {}); break;
      case "event": this.events.push(m); break;
      case "status": this.controller = m.state || this.controller; this.statusLog.push(m); break;
      case "result": this.result = m.data; break;
      case "integrity": this.integrity = m.checks; break;
      case "error": this.error = m; break;
      case "done": this.done = m; this.controller = m.status; break;
      case "closed": this.closed = m; break;
      default: break;
    }
    this.version += 1;
  }
  merge(d) {
    for (const [k, v] of Object.entries(d.series || {})) {
      if (Array.isArray(v)) { (this.series[k] ||= []).push(...v); continue; }
      if (v.replace || !this.series[k]) { this.series[k] = { x: [...(v.x || [])], y: [...(v.y || [])] }; continue; }
      const s = this.series[k]; for (let i = 0; i < v.x.length; i++) { s.x.push(v.x[i]); s.y.push(v.y[i]); }
    }
    Object.assign(this.state, d.state || {});
    Object.assign(this.scalars, d.scalars || {});
    Object.assign(this.matrix, d.matrix || {});
    Object.assign(this.table, d.table || {});
    Object.assign(this.hist, d.hist || {});
    if (d.streams) this.streams = d.streams;
    for (const [k, v] of Object.entries(d.append || {})) {
      const arr = (this.append[k] ||= []); arr.push(...v);
      const cap = this.meta.append_cap?.[k] ?? 200000; if (arr.length > cap) arr.splice(0, arr.length - cap);
    }
  }
}

// Replays archived telemetry through a store: play, pause, step, scrub, speed.
export class Player {
  constructor(messages, store, onChange) {
    this.messages = messages; this.store = store; this.onChange = onChange; this.pos = 0; this.timer = null; this.fps = 12;
    this.frameIdx = []; messages.forEach((m, i) => { if (m.t === "frame") this.frameIdx.push(i); });
    // constants (hello/meta) are always applied first so panels can lay out
    this.apply(this.firstFrame());
  }
  firstFrame() { return this.frameIdx.length ? this.frameIdx[0] : this.messages.length; }
  get frames() { return this.frameIdx.length; }
  get frameAt() { let k = 0; for (const i of this.frameIdx) { if (i < this.pos) k += 1; else break; } return k; }
  apply(upto) {  // apply messages [pos, upto)
    upto = Math.max(0, Math.min(this.messages.length, upto));
    if (upto < this.pos) { this.store.reset(); this.pos = 0; }
    for (; this.pos < upto; this.pos++) this.store.ingest(this.messages[this.pos]);
    this.onChange?.();
  }
  seekFrame(k) {  // show frames 0..k-1 applied (k = 0: constants only)
    k = Math.max(0, Math.min(this.frames, k));
    const upto = k === 0 ? this.firstFrame() : (k >= this.frames ? this.messages.length : this.frameIdx[k]);
    this.apply(upto);
  }
  step(n = 1) { this.seekFrame(this.frameAt + n); }
  end() { this.apply(this.messages.length); }
  play() { if (this.timer) return; this.timer = setInterval(() => { if (this.frameAt >= this.frames) { this.end(); this.pause(); return; } this.step(1); }, 1000 / this.fps); this.onChange?.(); }
  pause() { clearInterval(this.timer); this.timer = null; this.onChange?.(); }
  setFps(f) { this.fps = f; if (this.timer) { this.pause(); this.play(); } }
  get playing() { return !!this.timer; }
}
