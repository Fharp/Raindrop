
(function (global) {
"use strict";

const clamp = (x, a, b) => x < a ? a : x > b ? b : x;
const lerp  = (a, b, t) => a + (b - a) * t;
const lerp2 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t)];

const CFG = {

  refHeight:          1080,
  scaleMin:           0.6,
  scaleMax:           2.2,

  intensityFloor:     0.34,
  spawnIntervalCalm:  [0.018, 0.042],
  spawnIntervalStorm: [0.005, 0.012],
  spawnIntervalPow:   1.4,
  spawnSizeCalm:      [14, 46],
  spawnSizeStorm:     [18, 74],
  spawnLimit:         1500,
  spawnLimitMax:      2600,

  slipRateCalm:       0.88,
  slipRateStorm:      0.96,
  motionIntervalCalm: [0.06, 0.22],
  motionIntervalStorm:[0.05, 0.16],
  initialSpread:      0.62,
  gravityCalm:        1900,
  gravityStorm:       2700,
  evaporateCalm:      26,
  evaporateStorm:     16,
  mistTimeCalm:       11,
  mistTimeStorm:      22,
  trailCalm:          0.16,
  trailStorm:         0.26,

  dropletsCalm:       2200,
  dropletsStorm:      4200,
  dropletsFloor:      1800,
  dropletsPow:        1.2,
  dropletSize:        [5, 17],
  primeSeconds:       2.5,
  primeGain:          3.0,

  levelDrive:         0.30,
  levelDriveSpawn:    0.18,

  gustDepth:          0.35,
  gustPeriods:        [17, 41, 97],
  gustMaxScale:       1.7,

  windFloor:          0.03,
  windGain:           1.4,
  windLfoDepth:       0.7,
  windMaxShift:       0.22,
  wanderCalm:         0.008,
  wanderWind:         0.06,

  flashDecay:         3.2,
  flashMist:          0.40,
  flashOverlay:       0.30,

  idle: { intensity: 0.35, character: 0.30, wind: 0 },

  dryIn:            3.0,
  dryOut:           1.2,
  dryEvaporate:     2.4,
  dryMistTime:      26,
  dryMistColor:     [0.060, 0.070, 0.084, 1],
  dryGlassDecay:    0.12,

  cullMass:           4,
  cullMassMax:        900,
  cullTarget:         0.86,
  cullRate:           260,
  smooth: 1.6,
  dtMax:  0.05,
};

class Bridge {
  constructor(fx, audio, flashEl) {
    this.fx = fx; this.audio = audio; this.flashEl = flashEl || null;
    this.manual = false; this.running = false;
    this.t = 0; this._last = 0; this._raf = 0;
    this.flash = 0; this.wind = 0; this.culled = 0;
    this.scale = 1; this._cull = CFG.cullMass;

    this.dry = 0; this._dryWant = 0; this._primeT0 = 0; this._noFade = false;

    this.s = { i: CFG.idle.intensity, c: CFG.idle.character, w: CFG.idle.wind, lv: 0 };
    this._mistBase = (fx.options.mistColor || [0.01, 0.01, 0.01, 1]).slice();
    this._mistNow  = this._mistBase.slice();
    this._diffBase = (fx.options.raindropDiffuseLight || [0.2, 0.2, 0.2]).slice();
  }

  setDry(on) {
    const want = on ? 1 : 0;
    if (want === this._dryWant) return this;
    this._dryWant = want;

    if (!want) this._primeT0 = this.t;
    return this;
  }

  async start() {
    const fx = this.fx;

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

      try {
        this.update(dt);
        if (this.manual) {
          const time = { dt, total: this.t };
          fx.simulator.update(time);
          this._sweep(dt);
          fx.renderer.render(fx.simulator.raindrops, time);
          this._dryGlass(dt);
        } else {
          this._sweep(dt);
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

  update(dt) {
    const o = this.fx.options;
    const a = this.audio;
    const st = (a && a.playing) ? a.state : null;

    let ti = CFG.idle.intensity, tc = CFG.idle.character, tw = CFG.idle.wind, tl = 0;
    if (st && st.ok) {
      ti = st.intensity;
      tc = st.character;

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

    const k = 1 - Math.exp(-CFG.smooth * dt);
    this.s.i += (ti - this.s.i) * k;
    this.s.c += (tc - this.s.c) * k;
    this.s.w += (tw - this.s.w) * k;
    this.s.lv += (tl - this.s.lv) * Math.min(1, k * 4);

    const raw = clamp(this.s.i, 0, 1);
    const i = CFG.intensityFloor + (1 - CFG.intensityFloor) * raw;
    const c = clamp(this.s.c, 0, 1);
    const w = clamp(this.s.w, -1, 1);
    const lv = clamp(this.s.lv, 0, 1);

    let g = 0;
    for (const p of CFG.gustPeriods) g += Math.sin(2 * Math.PI * this.t / p);
    const gust = 1 + (g / CFG.gustPeriods.length) * CFG.gustDepth * c;

    const drive = 1 + (lv - 0.5) * 2 * CFG.levelDrive;

    const S = clamp((o.height || CFG.refHeight) / CFG.refHeight, CFG.scaleMin, CFG.scaleMax);
    const S2 = S * S;
    this.scale = S;

    const dTau = this._dryWant ? CFG.dryIn : CFG.dryOut;
    this.dry += (this._dryWant - this.dry) * (1 - Math.exp(-dt / Math.max(0.05, dTau)));
    if (Math.abs(this._dryWant - this.dry) < 3e-3) this.dry = this._dryWant;
    const d = clamp(this.dry, 0, 1);

    const ei = Math.pow(i, CFG.spawnIntervalPow);
    const iv = lerp2(CFG.spawnIntervalCalm, CFG.spawnIntervalStorm, ei);

    const spawnScale = clamp(1 / Math.max(0.15, gust * (1 + (lv - 0.5) * 2 * CFG.levelDriveSpawn)),
                             0.2, CFG.gustMaxScale) / S2;
    o.spawnInterval = [iv[0] * spawnScale, iv[1] * spawnScale];
    o.spawnSize     = lerp2(CFG.spawnSizeCalm, CFG.spawnSizeStorm, i).map(x => x * S);

    o.spawnLimit    = this._dryWant
      ? -1
      : Math.min(CFG.spawnLimitMax, Math.round(CFG.spawnLimit * S2));
    o.slipRate      = lerp(CFG.slipRateCalm, CFG.slipRateStorm, i);
    o.motionInterval = lerp2(CFG.motionIntervalCalm, CFG.motionIntervalStorm, i);
    o.initialSpread = CFG.initialSpread;
    o.dropletSize   = CFG.dropletSize.map(x => x * S);

    o.dropletsPerSeconds = this._dryWant ? 0 : this._dropletRate(i, drive, gust) * S2;
    o.gravity       = lerp(CFG.gravityCalm, CFG.gravityStorm, i) * S;

    o.evaporate     = lerp(CFG.evaporateCalm, CFG.evaporateStorm, i) * S2 * lerp(1, CFG.dryEvaporate, d);
    o.mistTime      = lerp(lerp(CFG.mistTimeCalm, CFG.mistTimeStorm, i), CFG.dryMistTime, d);
    o.trailDropDensity = lerp(CFG.trailCalm, CFG.trailStorm, i);

    o.xShifting = [0, CFG.wanderCalm + CFG.wanderWind * Math.abs(w)];
    this.wind = w;

    const mb = this._mistBase, md = CFG.dryMistColor;
    for (let n = 0; n < 4; n++) this._mistNow[n] = lerp(mb[n], md[n] != null ? md[n] : mb[n], d);
    o.mistColor = this._mistNow.slice();

    const strike = a && a.takeStrikes ? a.takeStrikes() : null;
    if (strike) this.flash = Math.max(this.flash, clamp(strike.gain, 0, 1));
    if (this.flash > 0.001) {
      this.flash *= Math.exp(-CFG.flashDecay * dt);
      const f = this.flash;
      const m = this._mistNow;
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
      o.mistColor = this._mistNow.slice();
      o.raindropDiffuseLight = this._diffBase.slice();
      if (this.flashEl) this.flashEl.style.opacity = "0";
    }
  }

  _dropletRate(i, drive, gust) {
    const base = lerp(CFG.dropletsCalm, CFG.dropletsStorm, Math.pow(i, CFG.dropletsPow));

    const prime = (this.t - this._primeT0) < CFG.primeSeconds ? CFG.primeGain : 1;
    return Math.max(CFG.dropletsFloor, base * drive * gust) * prime;
  }

  _sweep(dt) {
    const sim = this.fx.simulator;
    if (!sim || !sim.raindrops) return;
    const drops = sim.raindrops;
    const limit = this.fx.options.spawnLimit || 1;

    const load = drops.length / limit;
    this._cull += (load > CFG.cullTarget ? 1 : -1) * CFG.cullRate * dt;
    this._cull = clamp(this._cull, CFG.cullMass, CFG.cullMassMax * this.scale * this.scale);

    const w = this.wind || 0;
    const k = w * CFG.windMaxShift * dt;
    const blow = Math.abs(w) >= 1e-3;
    let dead = 0;
    for (const d of drops) {
      if (!d || d.destroied) continue;

      const still = !d.velocity || Math.abs(d.velocity.y) < 8 * this.scale;
      if (!(d.mass > CFG.cullMass) || (still && d.mass < this._cull)) {
        d.destroied = true; dead++; continue;
      }
      if (blow && d.velocity && d.pos) d.pos.x += Math.abs(d.velocity.y) * k;
    }
    this.culled = dead;
  }

  _dryGlass(dt) {
    if (this._noFade || this.dry < 0.02) return;
    const R  = this.fx.renderer;
    const zr = R && R.renderer;
    const white = zr && zr.assets && zr.assets.textures && zr.assets.textures.default;
    if (!R || !zr || !R.dropletTexture || !R.matrlErase || !white || typeof zr.blit !== "function") {
      this._noFade = true; return;
    }
    const f = 1 - Math.exp(-CFG.dryGlassDecay * this.dry * dt);
    if (!(f > 1e-5)) return;
    const t = 0.5 - Math.sin(Math.asin(clamp(1 - 2 * f, -1, 1)) / 3);
    if (!(t > 1e-6) || !Number.isFinite(t)) return;
    try {
      const es = R.matrlErase.eraserSize;
      if (es && typeof es.x === "number") { es.x = 0; es.y = 1 / t; }
      else R.matrlErase.eraserSize = [0, 1 / t];
      zr.blit(white, R.dropletTexture, R.matrlErase);
    } catch (e) {
      this._noFade = true;
      this.fadeError = e;
    }
  }

  get stats() {
    const sim = this.fx.simulator;
    const o = this.fx.options;
    return {
      i: this.s.i, c: this.s.c, w: this.s.w, level: this.s.lv, flash: this.flash,
      dry: this.dry,
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
