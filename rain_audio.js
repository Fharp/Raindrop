
(function (global) {
"use strict";

const RAIN_IDS = ["rain_light_steady", "rain_light_gusty", "rain_mid", "rain_heavy"];
const WIND_IDS = ["wind_breeze", "wind_strong"];
const LOOP_IDS = [...RAIN_IDS, ...WIND_IDS];
const TRIM = 0.05, XFADE = 0.5, MIN_RUN = 4, LOOKAHEAD = 3.0, PUMP_MS = 200;

const FADE_PTS = 129;
const FADE_IN = new Float32Array(FADE_PTS), FADE_OUT = new Float32Array(FADE_PTS);
for (let i = 0; i < FADE_PTS; i++) {
  const x = i / (FADE_PTS - 1);
  FADE_IN[i]  = Math.sin(x * Math.PI / 2);
  FADE_OUT[i] = Math.cos(x * Math.PI / 2);
}

const clamp = (x, a, b) => x < a ? a : x > b ? b : x;

class RainAudio {
  constructor(opts) {
    this.ver = "";
    this._packs = new Map();
    this.root        = opts.root || "./";
    this.secPerFrame = opts.secPerFrame || 3600;
    this.volume      = opts.volume != null ? opts.volume : 0.7;
    this.useEQ       = opts.eq !== false;

    this.preferOpus  = opts.preferOpus !== false;

    this.manifest = null; this.roster = null;
    this.cityDoc = null;  this.score = null;
    this.buffers = new Map(); this.assetById = new Map();
    this.stems = new Map();
    this.ctx = null; this.master = null; this.analyser = null; this.eq = null;

    this.playing = false; this.cursor = 0;
    this._t0 = 0; this._scheduled = -1; this._pumpId = 0; this._rafId = 0;
    this._flashes = [];
    this._thunder = [];
    this._levelBuf = null; this._level = 0;
    this._pending = new Map();
    this._fmt = null;
    this._err = null;
  }

  async _json(path) {

    const v = this.ver;
    const url = this.root + path + (v ? (path.indexOf("?") < 0 ? "?v=" : "&v=") + v : "");
    const r = await fetch(url, { cache: v ? "force-cache" : "no-cache" });
    if (!r.ok) throw new Error("取不到 " + path + "（HTTP " + r.status + "）");
    const txt = await r.text();
    if (txt.charCodeAt(0) === 0x3C) {
      throw new Error("取 " + path + " 拿回的是网页而不是 JSON，" +
        "这个文件多半没被部署，且站点缺少顶层 404.html。");
    }
    return JSON.parse(txt);
  }

  async _fetchAudio(path) {

    const opt = { cache: "force-cache" };

    // 每个素材各探各的。旧版把探测结果写进 this._fmt 当全局闩：
    // 只要有一个素材没有 .opus 兄弟文件，之后所有素材都会退回 mp3/flac。
    if (this.preferOpus) {
      const alt = path.replace(/\.[^./]+$/, ".opus");
      if (alt !== path) {
        try {
          const r = await fetch(this.root + alt, opt);
          if (r.ok) {
            const buf = await r.arrayBuffer();
            if (RainAudio._sniff(buf) === "ogg") { this._fmt = "opus"; return buf; }
          }
        } catch (e) {  }
      }
    }

    const r = await fetch(this.root + path, opt);
    if (!r.ok) throw new Error("取不到素材 " + path + "（HTTP " + r.status + "）");
    const buf = await r.arrayBuffer();
    if (RainAudio._sniff(buf) === "html") {
      throw new Error("素材 " + path + " 返回的是网页而不是音频。" +
        "多半是这个文件没被部署（Cloudflare Pages 单文件上限 25 MiB），" +
        "而站点缺少顶层 404.html，于是回落成了首页。");
    }
    if (this._fmt !== "opus") this._fmt = "orig";
    return buf;
  }

  static _sniff(buf) {
    if (!buf || buf.byteLength < 4) return "?";
    const b = new Uint8Array(buf, 0, 4);
    if (b[0] === 0x4F && b[1] === 0x67 && b[2] === 0x67 && b[3] === 0x53) return "ogg";
    if (b[0] === 0x66 && b[1] === 0x4C && b[2] === 0x61 && b[3] === 0x43) return "flac";
    if (b[0] === 0x49 && b[1] === 0x44 && b[2] === 0x33) return "mp3";
    if (b[0] === 0xFF && (b[1] & 0xE0) === 0xE0) return "mp3";
    if (b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46) return "riff";
    if (b[0] === 0x3C) return "html";
    return "?";
  }

  _load(id) {
    if (this._pending.has(id)) return this._pending.get(id);
    const a = this.assetById.get(id);
    if (!a) return Promise.reject(new Error("manifest 里没有 " + id));
    const root = this.manifest.audio_root || "sound/";
    const p = this._fetchAudio(root + a.file)
      .then(buf => this.ctx.decodeAudioData(buf))
      .then(dec => {
        this.buffers.set(id, dec);
        const st = this.stems.get(id);
        if (st) st.buffer = dec;
        return dec;
      })
      // 失败的 promise 不能留在 _pending 里，否则这个素材整场会话都无法重试
      .catch(e => { this._pending.delete(id); throw e; });
    this._pending.set(id, p);
    return p;
  }

  async boot() {
    this.manifest = await this._json("web_out/manifest.json");
    this.ver = String(this.manifest.built_at_utc || this.manifest.engine_version || "")
      .replace(/[^0-9A-Za-z]/g, "");
    this.roster   = await this._json("web_out/index/cities.json");
    for (const a of this.manifest.assets) this.assetById.set(a.id, a);

    const AC = global.AudioContext || global.webkitAudioContext;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.volume * 0.7;
    this.master.connect(this.ctx.destination);

    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.2;
    this.master.connect(this.analyser);
    this._levelBuf = new Float32Array(this.analyser.fftSize);

    if (this.useEQ) await this._setupEQ();

    for (const id of LOOP_IDS) {
      const a = this.assetById.get(id);
      if (!a) continue;
      const g = this.ctx.createGain();
      g.gain.value = 0;
      g.connect(this.eq ? (a.kind === "wind" ? this.eq.windIn : this.eq.rainIn) : this.master);
      this.stems.set(id, {
        gain: g, buffer: null, next: 0, lfo: null, lfoGain: null,
        regions: a.regions || null, running: false, sources: [], region: null, target: 0,
      });
    }

    await this.pickRandom();

    this.readyFirst = this.readyFor();

    this.ready = this.readyFirst.catch(() => {}).then(() => {

      // 只预热当前这份谱子真正用到的素材。旧版无条件解码全部 18 条，
      // 解码后的 float32 PCM 常驻约 286 MB，移动端有被系统回收的风险。
      this._warm();
      return Promise.all(RAIN_IDS.map(id => this._load(id).catch(e => { this._err = e.message; })));
    });

    return this;
  }

  readyFor() {
    if (!this.score) return Promise.resolve();
    const fr = this.score.frames[Math.min(
      Math.floor(this.cursor), this.score.frames.length - 1)];
    if (!fr) return Promise.resolve();
    const need = LOOP_IDS.filter(id => (fr.stems[id] || 0) > 0.004);
    const loops = (need.length ? need : [RAIN_IDS[0]]).map(id => this._load(id));

    // 雷声按需取。取不到不该拖垮起播，所以单独 catch 掉。
    const th = [...new Set((fr.thunder || []).map(s => s.asset).filter(Boolean))]
      .map(id => this._load(id).catch(() => {}));
    return Promise.all(loops.concat(th));
  }

  // 当前谱子从头到尾会用到的素材集合
  _needs() {
    const out = new Set();
    if (!this.score) return out;
    for (const fr of this.score.frames) {
      for (const id of LOOP_IDS) if ((fr.stems[id] || 0) > 0.004) out.add(id);
      for (const s of (fr.thunder || [])) if (s.asset) out.add(s.asset);
    }
    return out;
  }

  _warm() {
    for (const id of this._needs()) {
      if (!this.buffers.has(id)) this._load(id).catch(() => {});
    }
  }

  async _setupEQ() {
    if (typeof global.RainEQ === "undefined") return;
    let prof = null;
    for (const p of ["eq_profile.json", "web_out/eq_profile.json"]) {
      try { prof = await this._json(p); break; } catch (e) {  }
    }
    if (!prof) return;
    this.eq = await global.RainEQ.create(this.ctx, prof);
    this.eq.out.connect(this.master);
    this.eq.setIndoor(0);
  }

  async pickRandom() {
    const now = new Date();
    const hh = String(now.getUTCHours()).padStart(2, "0");
    let pick = null;
    try {
      const idx = await this._json("web_out/index/hour/" + hh + ".json");
      const table = idx.cities || idx;
      const slugs = Object.keys(table).filter(s => (table[s] || []).length);
      if (slugs.length) {
        const slug = slugs[(Math.random() * slugs.length) | 0];
        const cand = table[slug];
        const [ev, fi] = cand[(Math.random() * cand.length) | 0];
        pick = { slug, event: ev, frame: fi };
      }
    } catch (e) {  }

    if (!pick) {
      const cs = this.roster.cities.filter(c => c.events > 0);
      const c = cs[(Math.random() * cs.length) | 0];
      pick = { slug: c.slug, event: null, frame: 0 };
    }

    await this.loadCity(pick.slug);
    const evs = this.cityDoc.events;
    const ev = pick.event != null
      ? (evs.find(e => e.event_id === pick.event) || evs[0])
      : evs[(Math.random() * evs.length) | 0];
    await this.loadEvent(ev.event_id);

    const off = (now.getUTCMinutes() * 60 + now.getUTCSeconds()) / 3600;
    this.cursor = clamp(pick.frame + off, 0, Math.max(0, this.score.frames.length - 1e-6));
    return this.info;
  }

  async loadCity(slug) {
    this.cityDoc = await this._json("web_out/index/city/" + slug + ".json");
    return this.cityDoc;
  }

  async loadEvent(id) {
    const was = this.playing;
    this.stop();
    const ev = this.cityDoc.events.find(e => e.event_id === id) || this.cityDoc.events[0];
    this.score = await this._score(ev);
    this.cursor = 0; this._scheduled = -1;
    this._warm();
    if (was) await this.play();
    return this.score;
  }

  async _score(ev) {
    if (!this.cityDoc || !this.cityDoc.pack_version) {
      return this._json("web_out/" + ev.score);
    }
    let pack = this._packs.get(ev.score);
    if (!pack) {
      pack = await this._json("web_out/" + ev.score);
      this._packs.set(ev.score, pack);
      while (this._packs.size > 2) this._packs.delete(this._packs.keys().next().value);
    }
    const s = pack[String(ev.event_id)];
    if (!s) throw new Error("包 " + ev.score + " 里没有场次 " + ev.event_id);
    return s;
  }

  async seek(x) {
    if (!this.score) return;
    const was = this.playing;
    this.stop();
    this.cursor = clamp(x, 0, this.score.frames.length - 1e-6);
    this._scheduled = -1;
    if (was) await this.play();
  }

  async play() {
    if (!this.score) throw new Error("还没有选谱");
    await this.readyFor();

    await this.ctx.resume();

    // 连着两次 play() 会叠出第二条 _tick 循环，_applyFrame 双触发。
    // stop() 里两个都清了，play() 原来只清了 interval。
    cancelAnimationFrame(this._rafId);
    this.playing = true;
    this._t0 = this.ctx.currentTime - this.cursor * this.secPerFrame;
    this._scheduled = -1;
    for (const st of this.stems.values()) st.running = false;
    clearInterval(this._pumpId);
    this._pumpId = setInterval(() => this._pump(), PUMP_MS);
    this._tick();
  }

  stop() {
    this.playing = false;
    cancelAnimationFrame(this._rafId);
    clearInterval(this._pumpId);
    this._flashes.length = 0;
    if (!this.ctx) return;
    const now = this.ctx.currentTime;

    // 雷声是在进入每一帧时就把整小时排完的，源节点原来没有被记录，
    // 于是按下「静音」之后，之前排下去的雷还会继续响一个小时。
    for (const src of this._thunder) { try { src.stop(); } catch (e) {} }
    this._thunder.length = 0;

    for (const st of this.stems.values()) {
      st.gain.gain.cancelScheduledValues(now);
      st.gain.gain.setTargetAtTime(0, now, 0.05);
      st.running = false; st.region = null; st.target = 0;
      for (const src of st.sources) { try { src.stop(); } catch (e) {} }
      st.sources = [];
      if (st.lfoGain) st.lfoGain.gain.setTargetAtTime(0, now, 0.05);
    }
  }

  setVolume(v) {
    this.volume = clamp(v, 0, 1);
    if (this.master) this.master.gain.setTargetAtTime(this.volume * 0.7, this.ctx.currentTime, 0.05);
  }

  _tick() {
    if (!this.playing) return;
    const total = this.score.frames.length;
    this.cursor = (this.ctx.currentTime - this._t0) / this.secPerFrame;
    if (this.cursor >= total) { this.cursor = total; this.stop(); if (this.onend) this.onend(); return; }
    const k = Math.floor(this.cursor);
    if (k !== this._scheduled) {
      this._scheduled = k;
      this._applyFrame(k, this.ctx.currentTime, this._t0 + k * this.secPerFrame);
      if (this.onframe) this.onframe(k, this.score.frames[k]);
    }
    this._rafId = requestAnimationFrame(() => this._tick());
  }

  _applyFrame(k, when, frameStart) {
    const fr = this.score.frames[k];
    if (!fr) return;
    const ramp = Math.max(0.08, this.manifest.playback.ramp_seconds * this.secPerFrame / 3600);
    if (this.eq) this.eq.setFrame(fr.intensity, fr.character, when, ramp);
    for (const [id, st] of this.stems) {
      st.target = fr.stems[id] || 0;
      st.gain.gain.setTargetAtTime(st.target, when, ramp / 3);
      if (st.target > 0.004 && st.buffer && !st.running) this._startLoop(st);
    }
    this._setWindLfo(fr, when);

    let strikes = fr.thunder || [];
    const compress = 3600 / this.secPerFrame;
    if (compress > 1 && strikes.length > 1) {
      const keep = Math.max(1, Math.round(strikes.length / Math.sqrt(compress)));
      strikes = [...strikes].sort((a, b) => b.gain - a.gain).slice(0, keep).sort((a, b) => a.at - b.at);
    }
    for (const s of strikes) this._fireThunder(s, frameStart + s.at * this.secPerFrame / 3600);
  }

  _fireThunder(s, when) {
    const buf = this.buffers.get(s.asset);
    if (!buf || when < this.ctx.currentTime) return;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.playbackRate.value = s.rate || 1;
    const g = this.ctx.createGain();
    g.gain.value = s.gain;
    const pan = this.ctx.createStereoPanner ? this.ctx.createStereoPanner() : null;
    const tail = pan ? (pan.pan.value = s.pan || 0, pan) : g;
    if (pan) src.connect(g).connect(pan); else src.connect(g);
    if (this.eq && this.eq.enabled) {
      const th = this.eq.thunderNode(s.gain);
      tail.connect(th.input);
      th.output.connect(this.master);
      this.eq.duck(s.gain, when);
    } else {
      tail.connect(this.master);
    }
    src.start(when);
    src.onended = () => {
      const at = this._thunder.indexOf(src);
      if (at >= 0) this._thunder.splice(at, 1);
    };
    this._thunder.push(src);
    this._flashes.push({ when, gain: s.gain, pan: s.pan || 0 });
    // takeStrikes() 没被调用时（bridge 未接上）这里会一直堆积，封个顶
    while (this._flashes.length > 256) this._flashes.shift();
  }

  _setWindLfo(fr, when) {
    for (const id of WIND_IDS) {
      const st = this.stems.get(id); if (!st) continue;
      const lfo = fr.wind_lfo;
      if (!lfo || !(fr.stems[id] > 0)) {
        if (st.lfoGain) st.lfoGain.gain.setTargetAtTime(0, when, 0.3);
        continue;
      }
      if (!st.lfo) {
        st.lfo = this.ctx.createOscillator();
        st.lfoGain = this.ctx.createGain();
        st.lfo.connect(st.lfoGain).connect(st.gain.gain);
        st.lfo.start();
      }
      st.lfo.frequency.setTargetAtTime(lfo.rate_hz * 3600 / this.secPerFrame, when, 0.2);
      st.lfoGain.gain.setTargetAtTime(lfo.depth * (fr.stems[id] || 0), when, 0.3);
    }
  }

  _startLoop(st) {
    if (st.running || !st.buffer) return;
    st.running = true; st.sources = [];
    st.next = this.ctx.currentTime + 0.05;
    this._arm(st, true);
    this._pump();
  }

  _pump() {
    if (!this.ctx || !this.playing) return;
    const now = this.ctx.currentTime;
    for (const st of this.stems.values()) {

      if (!st.running && st.buffer && st.target > 0.004) this._startLoop(st);
      if (!st.running) continue;
      let guard = 0;
      try {
        while (st.next < now + LOOKAHEAD && guard++ < 4) this._scheduleRun(st);
      } catch (e) { st.running = false; this._err = "排片失败：" + e.message; }
    }
  }

  _regionsOf(st) {
    const dur = st.buffer.duration;
    const raw = (st.regions && st.regions.length) ? st.regions : [[TRIM, Math.max(TRIM, dur - TRIM)]];
    const ok = raw.filter(r => Math.min(r[1], dur) - r[0] >= MIN_RUN);
    return ok.length ? ok : raw;
  }

  _arm(st, firstTime) {
    const regs = this._regionsOf(st);
    st.region = regs[(Math.random() * regs.length) | 0];
    const a = st.region[0], b = Math.min(st.region[1], st.buffer.duration);
    st.pos = firstTime ? a + Math.random() * Math.max(0, (b - a) - MIN_RUN) : a;
    if (firstTime) st.fadeIn = true;
  }

  _scheduleRun(st) {
    if (!st.region) this._arm(st, true);
    const rb = Math.min(st.region[1], st.buffer.duration);
    const len = rb - st.pos;
    if (len < MIN_RUN) { this._arm(st, false); return; }

    const src = this.ctx.createBufferSource();
    src.buffer = st.buffer;
    const g = this.ctx.createGain();
    src.connect(g).connect(st.gain);

    const t = Math.max(st.next, this.ctx.currentTime + 0.05);
    if (st.fadeIn) { g.gain.setValueCurveAtTime(FADE_IN, t, XFADE); st.fadeIn = false; }
    else g.gain.setValueAtTime(1, t);
    g.gain.setValueCurveAtTime(FADE_OUT, t + len - XFADE, XFADE);

    src.start(t, st.pos, len + TRIM);
    src.stop(t + len + TRIM);
    st.sources.push(src);
    if (st.sources.length > 6) st.sources.shift();

    st.next = t + len - XFADE;
    st.fadeIn = true;
    this._arm(st, false);
  }

  get state() {
    const fr = this.score && this.score.frames[Math.min(
      Math.floor(this.cursor), this.score.frames.length - 1)];
    if (!fr) return { ok: false, intensity: 0, character: 0, windMag: 0, windPan: 0, level: 0 };
    const windMag = clamp((fr.stems.wind_breeze || 0) + (fr.stems.wind_strong || 0), 0, 1);
    return {
      ok: true,
      intensity: fr.intensity || 0,
      character: fr.character || 0,
      windMag,
      windPan: fr.wind_pan != null ? fr.wind_pan : 0,
      windLfo: fr.wind_lfo || null,
      level: this.level,
      frame: fr,
      cursor: this.cursor,
    };
  }

  get level() {
    if (!this.analyser || !this.playing) return 0;
    this.analyser.getFloatTimeDomainData(this._levelBuf);
    let s = 0;
    for (let i = 0; i < this._levelBuf.length; i++) s += this._levelBuf[i] * this._levelBuf[i];
    const rms = Math.sqrt(s / this._levelBuf.length);
    const v = clamp(Math.pow(rms * 6, 0.6), 0, 1);
    this._level += (v - this._level) * 0.25;
    return this._level;
  }

  takeStrikes() {
    if (!this.ctx || !this._flashes.length) return null;
    const now = this.ctx.currentTime;
    let out = null;
    while (this._flashes.length && this._flashes[0].when <= now) {
      const f = this._flashes.shift();
      if (now - f.when < 0.5) out = (!out || f.gain > out.gain) ? f : out;
    }
    return out;
  }

  get info() {
    if (!this.score) return null;
    const s = this.score;
    return {
      city: s.city, cityName: s.city_name, timezone: s.timezone,
      eventId: s.event_id, hours: s.frames.length, summary: s.summary,
    };
  }

  get progress() {
    if (!this.score) return null;
    const total = this.score.frames.length;
    const k = Math.min(Math.floor(this.cursor), total - 1);
    return {
      cursor: this.cursor, frames: total, frame: k,
      local: this.score.frames[k].local, utc: this.score.frames[k].utc,
      seconds: this.cursor * this.secPerFrame,
      totalSeconds: total * this.secPerFrame,
    };
  }

  get loaded() {
    const total = this.manifest ? this.manifest.assets.length : 0;
    return { done: this.buffers.size, total, format: this._fmt || "?" };
  }

  get error() { return this._err; }
}

global.RainAudio = {
  LOOP_IDS, RAIN_IDS, WIND_IDS,
  async create(opts) { return new RainAudio(opts || {}).boot(); },
  _class: RainAudio,
};

})(typeof window !== "undefined" ? window : globalThis);
