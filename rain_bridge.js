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
  // ── 分辨率归一。所有尺寸、速度、密度都以 1080 px 高的画面为基准，
  //    实际渲染尺寸不同时按 S = height/1080 换算：长度 ×S、面积密度 ×S²。
  //    不做这一步的话，同一套参数在 4K 上就是「小得多、也稀得多」。
  refHeight:          1080,
  scaleMin:           0.6,
  scaleMax:           2.2,

  // ── 雨强 0..1 两端的取值，中间按 pow 插值。
  //    注意 intensityFloor：谱里的 intensity 0 是「刚好可听」的毛毛雨，
  //    画面上不能真按 0 处理，否则玻璃是干的、隔几秒才掉一滴。
  //    视觉用的雨强先抬到地板上，最小档也是一直在下、玻璃一直是湿的。
  intensityFloor:     0.34,
  spawnIntervalCalm:  [0.018, 0.042],  // 秒（1080p 基准）；约 33 滴/秒
  spawnIntervalStorm: [0.005, 0.012],  // 约 118 滴/秒
  spawnIntervalPow:   1.4,
  spawnSizeCalm:      [14, 46],        // 参照片里在动的目标中位直径约 9 px、P90 约 30 px
  spawnSizeStorm:     [18, 74],
  spawnLimit:         1500,            // 1080p 基准，实际按 S² 放大后再封顶
  spawnLimitMax:      2600,
  // slipRate 才是「水珠动不动」的总闸，不是重力。库里默认 0＝几乎全都黏住不动。
  slipRateCalm:       0.88,
  slipRateStorm:      0.96,
  motionIntervalCalm: [0.06, 0.22],    // 多久重掷一次黏滞；小＝走走停停更频繁
  motionIntervalStorm:[0.05, 0.16],
  initialSpread:      0.62,            // 刚砸上去的那一下摊开多少，随后迅速收拢
  gravityCalm:        1900,
  gravityStorm:       2700,
  evaporateCalm:      26,              // 要够大，水珠才会有生有灭、画面一直在换
  evaporateStorm:     16,
  mistTimeCalm:       11,              // 秒，雾从透到满
  mistTimeStorm:      22,
  trailCalm:          0.16,
  trailStorm:         0.26,

  // ── 细水珠。这是「玻璃一直是湿的」的主要来源，也是斜线纹的来源，见 §斜线
  dropletsCalm:       2200,           // 1080p 基准，每秒；实际 ×S²
  dropletsStorm:      4200,
  dropletsFloor:      1800,           // 别再往下调，低于这个数会长出斜线纹
  dropletsPow:        1.2,
  dropletSize:        [5, 17],        // 参照片里静态纹理中位直径 3.4 px、P90 7.4 px
  primeSeconds:       2.5,            // 开场先快速铺满，不让人看着痕迹一条条长出来
  primeGain:          3.0,

  // ── 实时电平：让画面跟着声音本身呼吸，而不只跟着每小时一换的谱
  levelDrive:         0.30,           // 对细水珠密度的调制深度
  levelDriveSpawn:    0.18,           // 对生成率的调制深度

  // ── character：阵性。三个不互质周期叠出慢噪声，越大起伏越猛。
  //    倍率卡上限，免得阵与阵之间雨真的停了
  gustDepth:          0.35,
  gustPeriods:        [17, 41, 97],
  gustMaxScale:       1.7,

  // ── 风。raindrop-fx 的 xShifting 只有大小没有方向（它在 randomMotion 里乘了
  //    一个 (-1,1) 的随机数），所以它只能让每滴各自乱晃，做不出「被风统一吹斜」。
  //    方向性的那一份由本模块自己加，见 _sweep。
  windFloor:          0.03,           // 风层增益之和低于此值＝没风，水珠竖直
  windGain:           1.4,            // windMag × windPan → 横向分量
  windLfoDepth:       0.7,            // wind_lfo 叠加到横向分量上的深度
  windMaxShift:       0.22,           // 满风时 横向/下落 速度比，约 12°
  wanderCalm:         0.008,          // 无风时每滴自身的晃动（±0.8%，肉眼是直的）
  wanderWind:         0.06,           // 有风时才允许晃起来

  // ── 雷
  flashDecay:         3.2,            // 1/s
  flashMist:          0.40,           // 闪光把雾提亮多少
  flashOverlay:       0.30,           // DOM 覆盖层的最大不透明度

  // ── 没有音频时的静态雨（按下播放键之前）
  idle: { intensity: 0.35, character: 0.30, wind: 0 },

  // ── 收尸。见 _sweep。cullMass 会随拥挤程度自动上浮，保证名额永远有余量，
  //    否则 raindrops.length 一顶到 spawnLimit，库就彻底停止生成新滴，画面冻住。
  cullMass:           4,              // 下限：mass 低于此值一定收掉
  cullMassMax:        900,            // 上限：最拥挤时也只收掉这个质量以下的
  cullTarget:         0.86,           // 目标占用率
  cullRate:           260,            // 阈值上下浮动速度 /s
  smooth: 1.6,                        // 状态平滑速率 1/s
  dtMax:  0.05,
};

class Bridge {
  constructor(fx, audio, flashEl) {
    this.fx = fx; this.audio = audio; this.flashEl = flashEl || null;
    this.manual = false; this.running = false;
    this.t = 0; this._last = 0; this._raf = 0;
    this.flash = 0; this.wind = 0; this.culled = 0;
    this.scale = 1; this._cull = CFG.cullMass;
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
      // 一旦这里抛异常，rAF 就不会再排下一帧，画面静止且控制台之外无迹可寻。
      // 所以自己接住：报一次，停掉循环，交给上层显示。
      try {
        this.update(dt);
        if (this.manual) {
          const time = { dt, total: this.t };
          fx.simulator.update(time);
          this._sweep(dt);
          fx.renderer.render(fx.simulator.raindrops, time);
        } else {
          this._sweep(dt);          // 退回内置循环时插不进两者之间，只能近似
        }
      } catch (e) {
        this.running = false;
        this.lastError = e;
        if (this.onerror) this.onerror(e); else console.error("[rain_bridge]", e);
        return;
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
      // 这一小时没有风层在响就是没有风，水珠必须竖直落，不能自作主张地斜
      tw = 0;
      if (st.windMag >= CFG.windFloor) {
        tw = st.windMag * st.windPan * CFG.windGain;
        if (st.windLfo) {
          const ph = 2 * Math.PI * (st.windLfo.rate_hz || 0.15) * this.t;
          tw += Math.sin(ph) * st.windLfo.depth * CFG.windLfoDepth * st.windMag;
        }
      }
      tl = st.level;
    }
    // 一阶平滑：谱是每小时一跳的，直接写进去会看见台阶
    const k = 1 - Math.exp(-CFG.smooth * dt);
    this.s.i += (ti - this.s.i) * k;
    this.s.c += (tc - this.s.c) * k;
    this.s.w += (tw - this.s.w) * k;
    this.s.lv += (tl - this.s.lv) * Math.min(1, k * 4);   // 电平跟得快一些

    const raw = clamp(this.s.i, 0, 1);
    const i = CFG.intensityFloor + (1 - CFG.intensityFloor) * raw;   // 视觉雨强，恒在地板之上
    const c = clamp(this.s.c, 0, 1);
    const w = clamp(this.s.w, -1, 1);
    const lv = clamp(this.s.lv, 0, 1);

    // 阵性：慢噪声乘在生成率上。三个周期不互质，看着不会觉得在循环。
    let g = 0;
    for (const p of CFG.gustPeriods) g += Math.sin(2 * Math.PI * this.t / p);
    const gust = 1 + (g / CFG.gustPeriods.length) * CFG.gustDepth * c;

    // 实时电平：雨声一响，玻璃上的细水珠就多一层
    const drive = 1 + (lv - 0.5) * 2 * CFG.levelDrive;

    // 分辨率归一。长度与速度 ×S，单位面积上的密度 ×S²。
    const S = clamp((o.height || CFG.refHeight) / CFG.refHeight, CFG.scaleMin, CFG.scaleMax);
    const S2 = S * S;
    this.scale = S;

    const ei = Math.pow(i, CFG.spawnIntervalPow);
    const iv = lerp2(CFG.spawnIntervalCalm, CFG.spawnIntervalStorm, ei);
    // 倍率封顶：阵与阵之间可以变小，但不许真的停
    const spawnScale = clamp(1 / Math.max(0.15, gust * (1 + (lv - 0.5) * 2 * CFG.levelDriveSpawn)),
                             0.2, CFG.gustMaxScale) / S2;      // 面积大＝同样密度要更多滴
    o.spawnInterval = [iv[0] * spawnScale, iv[1] * spawnScale];
    o.spawnSize     = lerp2(CFG.spawnSizeCalm, CFG.spawnSizeStorm, i).map(x => x * S);
    o.spawnLimit    = Math.min(CFG.spawnLimitMax, Math.round(CFG.spawnLimit * S2));
    o.slipRate      = lerp(CFG.slipRateCalm, CFG.slipRateStorm, i);
    o.motionInterval = lerp2(CFG.motionIntervalCalm, CFG.motionIntervalStorm, i);
    o.initialSpread = CFG.initialSpread;
    o.dropletSize   = CFG.dropletSize.map(x => x * S);
    o.dropletsPerSeconds = this._dropletRate(i, drive, gust) * S2;
    o.gravity       = lerp(CFG.gravityCalm, CFG.gravityStorm, i) * S;
    o.evaporate     = lerp(CFG.evaporateCalm, CFG.evaporateStorm, i) * S2;
    o.mistTime      = lerp(CFG.mistTimeCalm, CFG.mistTimeStorm, i);
    o.trailDropDensity = lerp(CFG.trailCalm, CFG.trailStorm, i);

    // xShifting 在库里会被一个 (-1,1) 的随机数乘掉，只剩大小。
    // 因此这里只用它表示「每滴各自晃多少」，方向那一份在 _sweep 里加。
    o.xShifting = [0, CFG.wanderCalm + CFG.wanderWind * Math.abs(w)];
    this.wind = w;

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

  /** 细水珠的生成率。
   *
   *  为什么有地板：raindrop-fx 的细水珠位置是在 vertex shader 里按 gl_InstanceID
   *  和一个逐帧随机的 seed 算出来的——
   *      pos = gold_noise(vec2(1, id), seed+1), gold_noise(vec2(id, 1), seed+2)
   *  横纵两个坐标由同一个标量 seed 驱动，所以对固定的 id，(x, y) 随 seed 变化
   *  是在画面上描一条一维曲线，不是撒点。每帧的实例数 = dropletsPerSeconds × dt，
   *  这个数一小（比如 120/秒、60 fps 下每帧只有 2 个），可用的 id 就只有 1、2，
   *  水珠于是长年累月落在那两三条曲线上——屏幕上看见的就是那几道大斜线。
   *  把生成率抬高，id 铺开到几十上百个，才是各处均匀的湿玻璃。
   *
   *  开场再乘一个倍率，让玻璃在两三秒内就湿透，而不是让人看着痕迹一条条长出来。 */
  _dropletRate(i, drive, gust) {
    const base = lerp(CFG.dropletsCalm, CFG.dropletsStorm, Math.pow(i, CFG.dropletsPow));
    const prime = this.t < CFG.primeSeconds ? CFG.primeGain : 1;
    return Math.max(CFG.dropletsFloor, base * drive * gust) * prime;
  }

  /** 模拟之后的一次扫描，做两件库里没做的事。
   *
   *  一、清尸并留出名额。库里只在水珠滑出画面下沿或被吞并时才置 destroied；
   *  蒸发只是把 mass 一路减下去，减穿 0 之后 size = sqrt(负数) = NaN，
   *  水珠看不见了却永远留在数组里，照常占着 spawnLimit 的名额。而库的生成
   *  是这么写的：
   *      if (this.raindrops.length <= this.options.spawnLimit) { …trySpawn… }
   *  名额一满就**彻底停止生成**——不是少生成，是一滴都不再来。画面于是冻住：
   *  剩下的水珠慢慢流完，再没有新的砸上来。这正是「看着不像一直在下雨」的原因。
   *  所以这里用一个随拥挤程度自动上浮的质量阈值收尸，把占用率压在 cullTarget，
   *  永远留着余量给新滴。
   *
   *  二、方向性的风。库里没有这个概念（xShifting 的符号是随机的），只能在
   *  模拟之后把位置推一把，推力正比于该滴当前的下落速度：停着的不动，
   *  流得快的被吹得多。拖尾珠生成在母珠位置上，整条痕迹跟着斜。 */
  _sweep(dt) {
    const sim = this.fx.simulator;
    if (!sim || !sim.raindrops) return;
    const drops = sim.raindrops;
    const limit = this.fx.options.spawnLimit || 1;

    // 占用率高于目标就抬高收尸线，低于目标就放回去
    const load = drops.length / limit;
    this._cull += (load > CFG.cullTarget ? 1 : -1) * CFG.cullRate * dt;
    this._cull = clamp(this._cull, CFG.cullMass, CFG.cullMassMax * this.scale * this.scale);

    const w = this.wind || 0;
    const k = w * CFG.windMaxShift * dt;
    const blow = Math.abs(w) >= 1e-3;
    let dead = 0;
    for (const d of drops) {
      if (!d || d.destroied) continue;
      // 只收停着的：正在流的水珠是画面上「在动」的那部分，再小也留着
      const still = !d.velocity || Math.abs(d.velocity.y) < 8 * this.scale;
      if (!(d.mass > CFG.cullMass) || (still && d.mass < this._cull)) {
        d.destroied = true; dead++; continue;
      }
      if (blow && d.velocity && d.pos) d.pos.x += Math.abs(d.velocity.y) * k;
    }
    this.culled = dead;
  }

  get stats() {
    const sim = this.fx.simulator;
    const o = this.fx.options;
    return {
      i: this.s.i, c: this.s.c, w: this.s.w, level: this.s.lv, flash: this.flash,
      drops: sim ? sim.raindrops.length : -1,
      limit: o.spawnLimit, cull: this._cull, scale: this.scale,
      spawnPerSec: o.spawnInterval ? 2 / (o.spawnInterval[0] + o.spawnInterval[1]) : 0,
      droplets: o.dropletsPerSeconds || 0,
    };
  }
}

global.RainBridge = {
  CFG,
  attach(fx, audio, flashEl) { return new Bridge(fx, audio, flashEl); },
  _class: Bridge,
};

})(typeof window !== "undefined" ? window : globalThis);
