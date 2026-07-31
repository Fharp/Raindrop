/*
 * rain_eq.js — 雨声均衡模块（无依赖，直接 <script> 引入）
 *
 * 装在渲染谱与扬声器之间，只改音色不改响度：
 * eq_profile.json 里的 makeup_db 已经把整条链的宽带增益抵消掉，
 * 所以引擎的强度标定原样成立，音量该多大还是多大。
 *
 * 信号图
 *     雨的六条 stem ──► rainIn ──► [高通 低架 400 3.2k 9k架 character] ──► makeup ──► duck ──┐
 *                                                                                            ├──► [室内低通 低架 补偿] ──► out
 *     风的两条 stem ──► windIn ──► [高通 1.6k]                                       ────────┘
 *
 *     每次雷 ──► thunderNode(gain) ──► [低通 低架] ──► out   同时触发 duck
 *
 * 用法
 *     const eq = await RainEQ.create(ctx, profileJson);
 *     stemGain.connect(eq.rainIn);            // 雨层
 *     windGain.connect(eq.windIn);            // 风层
 *     eq.out.connect(masterGain);
 *
 *     eq.setFrame(frame.intensity, frame.character);   // 每帧调一次
 *     eq.setIndoor(0.35);                              // 用户滑杆 0..1
 *
 *     const th = eq.thunderNode(strike.gain);          // 每次雷
 *     src.connect(th.input); th.output.connect(...);
 *     eq.duck(strike.gain, when);
 */
(function (global) {
  "use strict";

  const DEFAULT_RAMP = 0.25;   // 滤波器参数的平滑时间常数，避免扫频听出「唰」

  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function dbToLin(db) { return Math.pow(10, db / 20); }

  // 在 21 档表里插值出该 intensity 的滤波器参数
  function sampleTable(table, i) {
    i = clamp(i, 0, 1);
    let k = 0;
    while (k < table.length - 2 && table[k + 1].i < i) k++;
    const a = table[k], b = table[k + 1] || table[k];
    const span = b.i - a.i;
    const t = span > 1e-9 ? (i - a.i) / span : 0;
    const bands = a.bands.map((bd, n) => ({
      type: bd.type,
      f: lerp(bd.f, b.bands[n].f, t),
      q: bd.q,
      gain: lerp(bd.gain, b.bands[n].gain, t),
    }));
    return { bands: bands, makeup_db: lerp(a.makeup_db, b.makeup_db, t) };
  }

  function makeFilter(ctx, spec) {
    const f = ctx.createBiquadFilter();
    f.type = spec.type;
    f.frequency.value = spec.f;
    f.Q.value = spec.q;
    if (spec.type !== "highpass" && spec.type !== "lowpass") f.gain.value = spec.gain || 0;
    return f;
  }

  function chain(ctx, specs) {
    const nodes = specs.map(s => makeFilter(ctx, s));
    for (let i = 0; i < nodes.length - 1; i++) nodes[i].connect(nodes[i + 1]);
    return { nodes: nodes, input: nodes[0], output: nodes[nodes.length - 1] };
  }

  function setParam(p, v, when, ramp) {
    if (ramp <= 0) { p.value = v; return; }
    p.setTargetAtTime(v, when, ramp / 3);
  }

  class RainEQ {
    constructor(ctx, profile) {
      this.ctx = ctx;
      this.profile = profile;
      this.enabled = true;
      this._indoor = 0;
      this._last = null;

      const p0 = sampleTable(profile.rain.table, 0);
      const charSpec = profile.rain.character[0].band;

      // ── 雨链 ──
      this.rainIn = ctx.createGain();
      this._rain = chain(ctx, p0.bands.concat([{
        type: charSpec.type, f: charSpec.f, q: charSpec.q, gain: 0,
      }]));
      this._rainMakeup = ctx.createGain();
      this._rainMakeup.gain.value = dbToLin(p0.makeup_db);
      this._duck = ctx.createGain();
      this._duck.gain.value = 1;

      // ── 风链 ──
      this.windIn = ctx.createGain();
      this._wind = chain(ctx, profile.wind.bands);

      // ── 室内段（雨风共用）──
      const ind = profile.indoor;
      this._indoorLP = makeFilter(ctx, {
        type: "lowpass", f: ind.lowpass_f[0], q: 0.707,
      });
      this._indoorShelf = makeFilter(ctx, {
        type: "lowshelf", f: ind.lowshelf_f, q: 0.707, gain: ind.lowshelf_g[0],
      });
      this._indoorMakeup = ctx.createGain();
      this._indoorMakeup.gain.value = 1;

      this.out = ctx.createGain();

      // ── 旁路用的直通 ──
      this._bypassBus = ctx.createGain();
      this._bypassBus.gain.value = 0;
      this._bypassBus.connect(this.out);

      this._wire(true);
    }

    _wire(on) {
      const { rainIn, windIn } = this;
      try { rainIn.disconnect(); } catch (e) {}
      try { windIn.disconnect(); } catch (e) {}
      try { this._duck.disconnect(); } catch (e) {}
      try { this._wind.output.disconnect(); } catch (e) {}
      try { this._indoorMakeup.disconnect(); } catch (e) {}

      if (on) {
        rainIn.connect(this._rain.input);
        this._rain.output.connect(this._rainMakeup).connect(this._duck);
        windIn.connect(this._wind.input);
        this._duck.connect(this._indoorLP);
        this._wind.output.connect(this._indoorLP);
        this._indoorLP.connect(this._indoorShelf)
                      .connect(this._indoorMakeup)
                      .connect(this.out);
      } else {
        rainIn.connect(this.out);
        windIn.connect(this.out);
      }
      this.enabled = on;
    }

    /** 逐帧调用。intensity、character 直接来自渲染谱。 */
    setFrame(intensity, character, when, ramp) {
      const ctx = this.ctx;
      when = when === undefined ? ctx.currentTime : when;
      ramp = ramp === undefined ? DEFAULT_RAMP : ramp;

      const s = sampleTable(this.profile.rain.table, intensity);
      s.bands.forEach((bd, n) => {
        const f = this._rain.nodes[n];
        setParam(f.frequency, bd.f, when, ramp);
        if (f.type !== "highpass" && f.type !== "lowpass") {
          setParam(f.gain, bd.gain, when, ramp);
        }
      });

      // character 段：末尾那一个
      const cb = this.profile.rain.character;
      const c = clamp(character || 0, 0, 1);
      const cg = lerp(cb[0].band.gain, cb[cb.length - 1].band.gain, c);
      const cmk = lerp(cb[0].makeup_db, cb[cb.length - 1].makeup_db, c);
      const last = this._rain.nodes[this._rain.nodes.length - 1];
      setParam(last.gain, cg, when, ramp);

      setParam(this._rainMakeup.gain, dbToLin(s.makeup_db + cmk), when, ramp);
      this._last = { intensity: intensity, character: c, bands: s.bands,
                     makeup_db: s.makeup_db + cmk, character_db: cg };
      return this._last;
    }

    /** 室内感 0（户外）… 1（隔窗）。用户侧滑杆，与气象数据无关。 */
    setIndoor(m, when, ramp) {
      const ctx = this.ctx;
      when = when === undefined ? ctx.currentTime : when;
      ramp = ramp === undefined ? 0.15 : ramp;
      m = clamp(m, 0, 1);
      this._indoor = m;
      const ind = this.profile.indoor;
      const f = lerp(ind.lowpass_f[0], ind.lowpass_f[1], Math.pow(m, ind.lowpass_gamma));
      setParam(this._indoorLP.frequency, f, when, ramp);
      setParam(this._indoorShelf.gain, lerp(ind.lowshelf_g[0], ind.lowshelf_g[1], m), when, ramp);
      setParam(this._indoorMakeup.gain,
               dbToLin(lerp(ind.makeup_db[0], ind.makeup_db[1], m)), when, ramp);
      return { lowpass_hz: f, indoor: m };
    }

    /**
     * 每次雷建一条短链。远近本质是低通——空气对高频的吸收随距离急升，
     * 所以十条「干雷」经低通也能当远雷用，等于把素材库撑大一倍。
     */
    thunderNode(strikeGain) {
      const ctx = this.ctx, th = this.profile.thunder;
      const g = clamp(strikeGain, 0, 1);
      const lp = makeFilter(ctx, {
        type: "lowpass", q: 0.707,
        f: lerp(th.lowpass_f[0], th.lowpass_f[1], g),
      });
      const sh = makeFilter(ctx, {
        type: "lowshelf", f: th.lowshelf_f, q: 0.707,
        gain: lerp(th.lowshelf_g[0], th.lowshelf_g[1], g),
      });
      lp.connect(sh);
      return { input: lp, output: sh, lowpass_hz: lp.frequency.value };
    }

    /** 雷响时把雨压一下。雷不用调更响，听感上自然就出来了。 */
    duck(strikeGain, when) {
      if (!this.enabled) return;
      const th = this.profile.thunder;
      const ctx = this.ctx;
      when = when === undefined ? ctx.currentTime : when;
      const g = clamp(strikeGain, 0, 1);
      const depth = dbToLin(lerp(th.duck_db[0], th.duck_db[1], g));
      if (depth >= 0.999) return;
      const p = this._duck.gain;
      p.setTargetAtTime(depth, when, th.duck_attack_s / 3);
      p.setTargetAtTime(1, when + th.duck_attack_s * 3, th.duck_release_s / 3);
    }

    setEnabled(on) {
      if (on === this.enabled) return;
      this._wire(!!on);
    }

    get state() {
      return Object.assign({
        indoor: this._indoor,
        enabled: this.enabled,
        indoor_lowpass_hz: this._indoorLP.frequency.value,
      }, this._last || {});
    }
  }

  RainEQ.create = function (ctx, profile) {
    return Promise.resolve(new RainEQ(ctx, profile));
  };

  global.RainEQ = RainEQ;
})(typeof window !== "undefined" ? window : this);
