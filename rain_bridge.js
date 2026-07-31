/* rain_bridge.js — 把渲染谱与实时电平接到 raindrop-fx 上
 *
 * 只做两件事：
 *   1. 用真实 dt 驱动 raindrop-fx（它自带的循环把 dt 写死成 0.03 秒，
 *      于是在 60 Hz 屏上模拟时间跑成 1.8 倍、120 Hz 上 3.6 倍。
 *      雾的回复速率 dt/mistTime 与小雾珠密度都吃这个 dt，必须自己给）；
 *   2. 每帧把 intensity / character / wind / 实时电平 / 雷，
 *      换算成 raindrop-fx 的 options。
 *
 * 参数全在 RainBridge.CFG，控制台改了立即生效。
 */
(function (global) {
"use strict";

const clamp = (x, a, b) => x < a ? a : x > b ? b : x;
const lerp  = (a, b, t) => a + (b - a) * t;
const lerp2 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t)];

const CFG = {
  // ── 雨强 0..1 两端的取值，中间按 pow 插值
  spawnIntervalCalm:  [0.85, 1.60],   // 秒，两滴之间
  spawnIntervalStorm: [0.02, 0.06],
  spawnIntervalPow:   1.5,
  spawnSizeCalm:      [22, 42],
  spawnSizeStorm:     [55, 120],
  spawnLimit:         1400,
  dropletsCalm:       120,            // 小雾珠 / 秒
  dropletsStorm:      1700,
  dropletsPow:        1.3,
  gravityCalm:        1700,
  gravityStorm:       2600,
  evaporateCalm:      20,             // 轻雨蒸发快，痕迹留不住
  evaporateStorm:     8,
  mistTimeCalm:       7,              // 秒，雾从透到满；轻雨回雾快
  mistTimeStorm:      20,
  trailCalm:          0.14,
  trailStorm:         0.26,

  // ── 实时电平：让画面跟着声音本身呼吸，而不只跟着每小时一换的谱
  levelDrive:         0.40,           // 对小雾珠密度的调制深度
  levelDriveSpawn:    0.25,           // 对生成率的调制深度

  // ── character：阵性。三个不互质周期叠出慢噪声，越大起伏越猛
  gustDepth:          0.60,
  gustPeriods:        [17, 41, 97],

  // ── 风：windMag × windPan 得到横向分量，正面吹来时 pan≈0，玻璃上本来就不斜
  windGain:           1.8,
  windSpread:         0.05,           // 每滴自身的随机横向抖动
  windLfoDepth:       0.7,            // wind_lfo 叠加到横向分量上的深度

  // ── 雷
  flashDecay:         3.2,            // 1/s
  flashMist:          0.40,           // 闪光把雾提亮多少
  flashOverlay:       0.30,           // DOM 覆盖层的最大不透明度

  // ── 没有音频时的静态雨（按下播放键之前）
  idle: { intensity: 0.35, character: 0.30, wind: 0.12 },

  smooth: 1.6,                        // 状态平滑速率 1/s
  dtMax:  0.05,
};

class Bridge {
  constructor(fx, audio, flashEl) {
    this.fx = fx; this.audio = audio; this.flashEl = flashEl || null;
    this.manual = false; this.running = false;
    this.t = 0; this._last = 0; this._raf = 0;
    this.flash = 0;
    // 平滑后的状态
    this.s = { i: CFG.idle.intensity, c: CFG.idle.character, w: CFG.idle.wind, lv: 0 };
    this._mistBase = (fx.options.mistColor || [0.01, 0.01, 0.01, 1]).slice();
    this._diffBase = (fx.options.raindropDiffuseLight || [0.2, 0.2, 0.2]).slice();
  }

  async start() {
    const fx = this.fx;
    // 能拿到内部对象就自己驱动，拿不到就退回它自带的循环
    if (fx.simulator && fx.renderer && typeof fx.renderer.loadAssets === "function") {
      await fx.renderer.loadAssets();
      this.manual = true;
    } else {
      await fx.start();
      this.manual = false;
    }
    this.running = true;
    this._last = performance.now();
    const loop = (now) => {
      if (!this.running) return;
      const dt = clamp((now - this._last) / 1000, 0.001, CFG.dtMax);
      this._last = now; this.t += dt;
      this.update(dt);
      if (this.manual) {
        const time = { dt, total: this.t };
        fx.simulator.update(time);
        fx.renderer.render(fx.simulator.raindrops, time);
      }
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
    return this;
  }

  stop() { this.running = false; cancelAnimationFrame(this._raf); if (!this.manual) this.fx.stop(); }

  // ─────────────── 每帧换算

  update(dt) {
    const o = this.fx.options;
    const a = this.audio;
    const st = (a && a.playing) ? a.state : null;

    // 目标值
    let ti = CFG.idle.intensity, tc = CFG.idle.character, tw = CFG.idle.wind, tl = 0;
    if (st && st.ok) {
      ti = st.intensity;
      tc = st.character;
      tw = st.windMag * st.windPan * CFG.windGain;
      if (st.windLfo) {
        const ph = 2 * Math.PI * (st.windLfo.rate_hz || 0.15) * this.t;
        tw += Math.sin(ph) * st.windLfo.depth * CFG.windLfoDepth * st.windMag;
      }
      tl = st.level;
    }
    // 一阶平滑：谱是每小时一跳的，直接写进去会看见台阶
    const k = 1 - Math.exp(-CFG.smooth * dt);
    this.s.i += (ti - this.s.i) * k;
    this.s.c += (tc - this.s.c) * k;
    this.s.w += (tw - this.s.w) * k;
    this.s.lv += (tl - this.s.lv) * Math.min(1, k * 4);   // 电平跟得快一些

    const i = clamp(this.s.i, 0, 1);
    const c = clamp(this.s.c, 0, 1);
    const w = clamp(this.s.w, -1, 1);
    const lv = clamp(this.s.lv, 0, 1);

    // 阵性：慢噪声乘在生成率上。三个周期不互质，听感上不会觉得在循环。
    let g = 0;
    for (const p of CFG.gustPeriods) g += Math.sin(2 * Math.PI * this.t / p);
    const gust = 1 + (g / CFG.gustPeriods.length) * CFG.gustDepth * c;

    // 实时电平：雨声一响，玻璃上的细雾珠就多一层
    const drive = 1 + (lv - 0.5) * 2 * CFG.levelDrive;

    const ei = Math.pow(i, CFG.spawnIntervalPow);
    const iv = lerp2(CFG.spawnIntervalCalm, CFG.spawnIntervalStorm, ei);
    const spawnScale = 1 / Math.max(0.15, gust * (1 + (lv - 0.5) * 2 * CFG.levelDriveSpawn));
    o.spawnInterval = [iv[0] * spawnScale, iv[1] * spawnScale];
    o.spawnSize     = lerp2(CFG.spawnSizeCalm, CFG.spawnSizeStorm, i);
    o.spawnLimit    = CFG.spawnLimit;
    o.dropletsPerSeconds = lerp(CFG.dropletsCalm, CFG.dropletsStorm, Math.pow(i, CFG.dropletsPow)) * drive * gust;
    o.gravity       = lerp(CFG.gravityCalm, CFG.gravityStorm, i);
    o.evaporate     = lerp(CFG.evaporateCalm, CFG.evaporateStorm, i);
    o.mistTime      = lerp(CFG.mistTimeCalm, CFG.mistTimeStorm, i);
    o.trailDropDensity = lerp(CFG.trailCalm, CFG.trailStorm, i);

    // 风只改横向速度的分布中心，重力方向不动——玻璃上的水本来就是被吹斜的
    const sp = CFG.windSpread + 0.06 * Math.abs(w);
    o.xShifting = [w * 0.35 - sp, w * 0.35 + sp];

    // 雷
    const strike = a && a.takeStrikes ? a.takeStrikes() : null;
    if (strike) this.flash = Math.max(this.flash, clamp(strike.gain, 0, 1));
    if (this.flash > 0.001) {
      this.flash *= Math.exp(-CFG.flashDecay * dt);
      const f = this.flash;
      const m = this._mistBase;
      o.mistColor = [m[0] + f * CFG.flashMist, m[1] + f * CFG.flashMist, m[2] + f * CFG.flashMist * 1.05, m[3]];
      const d = this._diffBase;
      o.raindropDiffuseLight = [d[0] + f * 0.5, d[1] + f * 0.5, d[2] + f * 0.55];
      if (this.flashEl) {
        this.flashEl.style.opacity = String(f * CFG.flashOverlay);
        if (strike) {
          const x = (0.5 + (strike.pan || 0) * 0.45) * 100;
          this.flashEl.style.background =
            "radial-gradient(60% 50% at " + x.toFixed(0) + "% 20%, rgba(210,225,255,1), rgba(210,225,255,0) 70%)";
        }
      }
    } else if (this.flash) {
      this.flash = 0;
      o.mistColor = this._mistBase.slice();
      o.raindropDiffuseLight = this._diffBase.slice();
      if (this.flashEl) this.flashEl.style.opacity = "0";
    }
  }

  get stats() { return { i: this.s.i, c: this.s.c, w: this.s.w, level: this.s.lv, flash: this.flash,
                         drops: this.fx.simulator ? this.fx.simulator.raindrops.length : -1 }; }
}

global.RainBridge = {
  CFG,
  attach(fx, audio, flashEl) { return new Bridge(fx, audio, flashEl); },
  _class: Bridge,
};

})(typeof window !== "undefined" ? window : globalThis);
