/*
 * rain_glass.js — 雨窗渲染层（无依赖，直接 <script> 引入）
 *
 * 做什么：在底图上盖一层「起雾的玻璃」。四个平面：
 *   1. 雾面      底图强模糊 + 向白提亮，细节不可辨；
 *   2. 凝结      静止小水珠随时间在玻璃上积累、蒸发；
 *   3. 流淌      水珠过重（自发提拔，或被新雨点砸中）就沿重力下滑，
 *                吞并路径上的小珠、在雾上擦出一条透明痕迹、身后撒下细碎尾珠；
 *   4. 回雾      擦痕在 refog_s 秒内被新雾盖回去，一切回到磨砂。
 *
 * 与音频引擎的关系：本层只读三个标量，接口刻意与渲染谱对齐：
 *   intensity  0..1   雨强，直接用 frames[i].intensity；
 *   character  0..1   阵性，frames[i].character。控制降雨的忽大忽小：
 *                     0 恒定层状，1 一阵一阵（慢噪声调制生成率）；
 *   wind      -1..1   风。大小控制滑珠的斜度与蛇行，符号是水平方向。
 *                     渲染谱只有风层增益（0..1），方向在 ui.wind_dir_deg /
 *                     wind_pan 里，怎么拼由调用方定（见 rain_view.html 末尾示意）。
 *
 * 结构：模拟在 CPU（几十颗滑珠 + 几千颗静珠，量级很小），每帧把水珠
 * 画进一张法线编码的 canvas、雾画进另一张低分辨率 canvas，WebGL 只做
 * 一次全屏合成：按雾的浓度混合「磨砂 / 擦净」两版底图，在水珠处用
 * 法线反向取样清晰底图——珠子里是缩小的倒像，这是真实水珠的透镜行为。
 * 单 draw call，单程序，无 framebuffer。
 *
 * 用法：
 *   const rg = await RainGlass.mount({ canvas, image: "bg.jpg" });
 *   rg.set({ intensity: 0.6, wind: -0.3, character: 0.4 });  // 随渲染谱逐帧喂
 *   rg.refog();                 // 一键回到全雾
 *   rg.stats.fps                // 平滑后的帧率
 *   RainGlass.CFG               // 全部视觉参数，控制台改了立即生效
 *                               //（*Scale 类参数除外，改后需 rg.rebuild()）
 *
 * 打开方式：必须走 http（目录里 python3 -m http.server 即可）。
 * file:// 协议下浏览器会把本地图片当跨域纹理拦掉，画面是黑的。
 */
(function (global) {
  "use strict";

  // ------------------------------------------------------------ 可调参数
  // 视觉参数没有「实测」一说，量级以 1280×720、intensity 0.5 为基准
  // 调过一轮，最终要靠眼睛在你机器上定。最值得先动的五个：
  // refract（珠内倒像的强度）、frostLift（雾有多白）、refog_s（回雾快慢）、
  // droplet.rate（凝结密度）、mover.rate（滑珠多少）。
  const CFG = {
    // ── 渲染 ──
    renderScale: Math.min(global.devicePixelRatio || 1, 1.25), // 主画布 DPR 上限。
                         // 效果本身是软的，1.25 与 2.0 肉眼难分，带宽省一半
    dropTexScale: 0.5,   // 水珠层相对主画布的分辨率。每帧要整张上传 GPU，
                         // 这是本模块唯一的带宽大头，卡就先降它
    fogTexScale: 0.25,   // 雾层。雾本来就是低频的东西
    refract: 0.062,      // 折射位移（uv 单位）。负向取样，珠内是倒像
    frostLift: 0.13,     // 雾面向白提亮的比例。越亮越像结露
    wipedBlur: 0.35,     // 擦痕处残余模糊：0 全清晰，1 与雾面同糊。
                         // 湿玻璃擦出的道子并不真正透明，留一点糊才像
    dimMax: 0.12,        // intensity=1 时整体压暗的比例——暴雨天光线弱
    grain: 0.035,        // 雾面颗粒噪声幅度，去掉纯色渐变的塑料感
    refog_s: 14,         // 擦痕被新雾盖回去的时间常数（「很快」的定义在这）
    light: [-0.35, -0.55, 0.75], // 镜面高光方向：屏幕左上偏观察者

    // ── 静珠（凝结层）──
    droplet: {
      base: 4,          // intensity=0 时每秒新珠数（按 1280×720 面积折算）
      rate: 70,         // 强度带来的增量
      gamma: 1.6,       // 强度→数量的弯曲，低强度区留得稀一点
      r: [0.8, 2.6],    // 半径范围 px
      life: [22, 70],   // 存活秒数，到点蒸发
      trailLife: [8, 20], // 尾珠命更短：擦痕先于回雾干净，层次才对
      cap: 4200,        // 总数上限，防长时间挂机堆积
    },

    // ── 滑珠 ──
    mover: {
      base: 0.02,        // intensity=0 时每秒生成数（近乎没有）
      rate: 3.2,
      gamma: 1.7,
      rMaxK: [6, 8],     // 半径上限 = 6 + 8*intensity
      rStop: 2.6,        // 缩到这个半径就停下，变回一颗静珠
      cap: 64,
      g: 520,            // 重力加速度 px/s²，按（半径−rStop）比例施加
      vTerm: 26,         // 末速系数：v_max ≈ vTerm*(r−rStop)+18
      stick: 0.55,       // 迟滞：每秒有此概率突然黏滞减速——走走停停
                         // 是玻璃上水珠最认得出来的特征
      windAx: 110,       // wind=±1 时的水平加速度
      meander: 46,       // 随机蛇行加速度
      trailSpacing: 1.4, // 每走 r*此系数 的路程撒一颗尾珠
      trailShrink: 0.988,// 撒一颗尾珠后的半径衰减（质量流失）
      absorb: 0.6,       // 吞并静珠时的面积转化率
      promote: 0.35,     // 生成滑珠时有此比例改为「就地提拔一颗大静珠」，
                         // 凝结→过重→开始流，这条路径就是它
      promoteRMin: 1.9,
    },
  };

  // ------------------------------------------------------------ 小工具
  const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
  const lerp = (a, b, t) => a + (b - a) * t;
  const rand = Math.random;
  const rr = (a, b) => a + rand() * (b - a);

  // character 用的慢噪声：三条不可公度的正弦叠加，取值大致 0..1。
  // 周期落在十几秒到一两分钟之间——阵雨的「一阵」就是这个时间尺度。
  function makeSlowNoise() {
    const p = [rand() * 6.28, rand() * 6.28, rand() * 6.28];
    return (t) => 0.5 + (Math.sin(t * 0.11 + p[0]) * 0.5 +
                         Math.sin(t * 0.043 + p[1]) * 0.3 +
                         Math.sin(t * 0.27 + p[2]) * 0.2) * 0.5;
  }

  // 水珠精灵：单位球面法线编码进 RG（0.5 为零点），高度进 B，覆盖进 A。
  // 一张图所有珠子共用，滑珠画的时候按速度方向旋转拉伸。
  function makeDropSprite(S) {
    const c = document.createElement("canvas");
    c.width = c.height = S;
    const ctx = c.getContext("2d");
    const im = ctx.createImageData(S, S), d = im.data;
    for (let y = 0; y < S; y++) {
      for (let x = 0; x < S; x++) {
        const px = ((x + 0.5) / S) * 2 - 1;
        const py = ((y + 0.5) / S) * 2 - 1;
        const r = Math.hypot(px, py);
        const i = (y * S + x) * 4;
        const a = clamp((1 - r) * S / 6, 0, 1);          // 约 3 px 软边
        const z = Math.sqrt(Math.max(0, 1 - px * px - py * py));
        d[i] = Math.round((px * 0.5 + 0.5) * 255);
        d[i + 1] = Math.round((py * 0.5 + 0.5) * 255);
        d[i + 2] = Math.round(z * 255);
        d[i + 3] = Math.round(a * 255);
      }
    }
    ctx.putImageData(im, 0, 0);
    return c;
  }

  // 雾的橡皮擦：软边圆盘，destination-out 用
  function makeSoftDisc(S) {
    const c = document.createElement("canvas");
    c.width = c.height = S;
    const ctx = c.getContext("2d");
    const g = ctx.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.65, "rgba(255,255,255,0.85)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, S, S);
    return c;
  }

  // 静珠的空间哈希。静珠基本不动，每帧整表重建（几千次插入，可忽略）
  class Hash {
    constructor(cell) { this.cell = cell; this.m = new Map(); }
    clear() { this.m.clear(); }
    add(o) {
      const k = Math.floor(o.x / this.cell) * 100000 + Math.floor(o.y / this.cell);
      let a = this.m.get(k);
      if (!a) { a = []; this.m.set(k, a); }
      a.push(o);
    }
    near(x, y, rad, fn) {
      const c = this.cell;
      const x0 = Math.floor((x - rad) / c), x1 = Math.floor((x + rad) / c);
      const y0 = Math.floor((y - rad) / c), y1 = Math.floor((y + rad) / c);
      for (let gy = y0; gy <= y1; gy++) {
        for (let gx = x0; gx <= x1; gx++) {
          const a = this.m.get(gx * 100000 + gy);
          if (a) for (let i = 0; i < a.length; i++) fn(a[i]);
        }
      }
    }
  }

  // ------------------------------------------------------------ 模拟
  // 坐标一律 CSS px。渲染分辨率的换算在 renderer 一侧做。
  class Sim {
    constructor(W, H) {
      this.droplets = [];
      this.movers = [];
      this.accD = 0;
      this.accM = 0;
      this.t = 0;
      this.noise = makeSlowNoise();
      this.hash = new Hash(28);
      this.state = { intensity: 0.5, wind: 0, character: 0 };
      this.setArea(W, H);
    }

    setArea(W, H) {
      this.W = W; this.H = H;
      this.area = (W * H) / (1280 * 720);   // 生成率按面积折算
    }

    step(dt, R) {
      this.t += dt;
      const S = this.state;

      // 阵性：character=0 时恒为 1；=1 时在 ~0.12..2.4 之间起伏
      const burst = lerp(1, clamp(this.noise(this.t) * 2.2 - 0.35, 0.12, 2.4),
                         S.character);

      // 静珠生成（分数累加器，速率低时也能均匀出珠）
      const cd = CFG.droplet;
      this.accD += (cd.base + cd.rate * Math.pow(S.intensity, cd.gamma)) *
                   this.area * burst * dt;
      while (this.accD >= 1) {
        this.accD--;
        if (this.droplets.length < cd.cap) this.spawnDroplet(R);
      }

      // 滑珠生成
      const cm = CFG.mover;
      this.accM += (cm.base + cm.rate * Math.pow(S.intensity, cm.gamma)) *
                   this.area * burst * dt;
      while (this.accM >= 1) {
        this.accM--;
        if (this.movers.length < cm.cap) this.spawnMover(R);
      }

      // 静珠蒸发 / 被吞并的清理
      const ds = this.droplets;
      for (let i = ds.length - 1; i >= 0; i--) {
        const o = ds[i];
        if (o.absorbed) {                       // 已被滑珠擦掉，直接除名
          ds[i] = ds[ds.length - 1]; ds.pop();
        } else if (this.t >= o.die) {
          R.eraseDroplet(o);                    // 蒸发：从画布上擦掉
          ds[i] = ds[ds.length - 1]; ds.pop();
        }
      }

      // 哈希重建
      this.hash.clear();
      for (let i = 0; i < ds.length; i++) this.hash.add(ds[i]);

      // 滑珠
      const ms = this.movers;
      for (let i = ms.length - 1; i >= 0; i--) {
        if (!this.stepMover(ms[i], dt, R)) {
          ms[i] = ms[ms.length - 1]; ms.pop();
        }
      }
    }

    spawnDroplet(R, x, y, r, life) {
      const c = CFG.droplet;
      const o = {
        x: x !== undefined ? x : rr(-6, this.W + 6),
        y: y !== undefined ? y : rr(-6, this.H * 0.98),
        r: r !== undefined ? r : rr(c.r[0], c.r[1]),
        die: this.t + (life !== undefined ? life : rr(c.life[0], c.life[1])),
      };
      this.droplets.push(o);
      R.drawDroplet(o);
      return o;
    }

    spawnMover(R) {
      const M = CFG.mover, S = this.state;
      const rMax = M.rMaxK[0] + M.rMaxK[1] * S.intensity;
      let x, y;

      // 一部分滑珠不是「新雨点砸上来」，而是把一颗已经够大的静珠
      // 就地提拔——凝结到过重、开始流，就是这条路径。
      if (rand() < M.promote && this.droplets.length) {
        let best = null;
        for (let k = 0; k < 24; k++) {
          const o = this.droplets[(rand() * this.droplets.length) | 0];
          if (o.r >= M.promoteRMin && !o.absorbed && (!best || o.r > best.r)) best = o;
        }
        if (best) {
          x = best.x; y = best.y;
          R.eraseDroplet(best);
          best.absorbed = true;                 // 下一帧清理流程除名
        }
      }
      if (x === undefined) { x = rr(0, this.W); y = rr(0, this.H * 0.85); }

      const r = clamp(M.rStop + 1.5 + rand() * 2.0 + 3.0 * S.intensity,
                      M.rStop + 1.2, rMax);
      this.movers.push({ x, y, r, rMax, vx: 0, vy: rr(6, 26),
                         trailAcc: 0, pause: 0 });
    }

    stepMover(m, dt, R) {
      const M = CFG.mover, S = this.state;
      const ox = m.x, oy = m.y;

      if (m.pause > 0) {
        m.pause -= dt;                          // 黏滞中：原地不动
      } else {
        if (rand() < M.stick * dt) {            // 随机进入黏滞
          m.pause = rr(0.06, 0.5);
          m.vx *= 0.2; m.vy *= 0.2;
        }
        m.vy += M.g * ((m.r - M.rStop) / 10) * dt;
        const vT = M.vTerm * (m.r - M.rStop) + 18;
        if (m.vy > vT) m.vy = vT;
        m.vx += (S.wind * M.windAx + (rand() - 0.5) * 2 * M.meander) * dt;
        m.vx *= Math.pow(0.35, dt);             // 阻尼：蛇行不至于跑飞
        const vxMax = 0.7 * m.vy + 14;          // 斜得再厉害也是往下流
        m.vx = clamp(m.vx, -vxMax, vxMax);
        m.x += m.vx * dt;
        m.y += m.vy * dt;
      }

      // 擦雾 + 清除路径上的静珠 + 吞并
      R.fogErase(m.x, m.y, m.r * 1.5);
      R.eraseAt(m.x, m.y, m.r * 0.95);
      this.hash.near(m.x, m.y, m.r + 3, (o) => {
        if (o.absorbed) return;
        const dx = o.x - m.x, dy = o.y - m.y;
        const rad = m.r * 0.9 + o.r;
        if (dx * dx + dy * dy <= rad * rad) {
          o.absorbed = true;
          m.r = Math.min(m.rMax * 1.15,
                         Math.sqrt(m.r * m.r + o.r * o.r * M.absorb));
        }
      });

      // 拖尾：按实际位移计程（黏滞时不撒，免得原地堆珠）
      m.trailAcc += Math.hypot(m.x - ox, m.y - oy);
      const spacing = Math.max(4, m.r * M.trailSpacing);
      while (m.trailAcc >= spacing) {
        m.trailAcc -= spacing;
        const sp = Math.max(Math.hypot(m.vx, m.vy), 1e-3);
        // 撒在身后 1.6r 处：出了吞并半径，不然自己会把尾珠吃回去
        const bx = m.x - (m.vx / sp) * m.r * 1.6 + (rand() - 0.5) * m.r * 0.8;
        const by = m.y - (m.vy / sp) * m.r * 1.6;
        this.spawnDroplet(R, bx, by, m.r * rr(0.16, 0.30),
                          rr(CFG.droplet.trailLife[0], CFG.droplet.trailLife[1]));
        m.r *= M.trailShrink;
      }

      if (m.y - m.r > this.H + 8 || m.x < -40 || m.x > this.W + 40) return false;
      if (m.r <= M.rStop) {                     // 流不动了，凝成一颗静珠
        this.spawnDroplet(R, m.x, m.y, m.r * 0.9, rr(10, 26));
        return false;
      }
      return true;
    }
  }

  // ------------------------------------------------------------ WebGL
  const VS =
    "attribute vec2 aPos;varying vec2 vUv;" +
    "void main(){vUv=vec2(aPos.x*0.5+0.5,(1.0-aPos.y)*0.5);" +
    "gl_Position=vec4(aPos,0.0,1.0);}";

  const FS =
    "precision mediump float;\n" +
    "varying vec2 vUv;\n" +
    "uniform sampler2D uSharp,uBlur,uDrops,uFog;\n" +
    "uniform float uRefract,uFrostLift,uWipedBlur,uDim,uGrain;\n" +
    "uniform vec3 uLight;\n" +
    "void main(){\n" +
    "  vec4 d=texture2D(uDrops,vUv);\n" +
    "  float fog=texture2D(uFog,vUv).a;\n" +
    "  vec3 sharp=texture2D(uSharp,vUv).rgb;\n" +
    "  vec3 blur =texture2D(uBlur ,vUv).rgb;\n" +
    // 雾面：强模糊 + 提亮 + 一点静态颗粒
    "  vec3 frost=mix(blur,vec3(1.0),uFrostLift);\n" +
    "  float g=fract(sin(dot(vUv,vec2(12.9898,78.233)))*43758.5453);\n" +
    "  frost+=vec3((g-0.5)*uGrain);\n" +
    // 擦痕：半清晰
    "  vec3 wiped=mix(sharp,blur,uWipedBlur);\n" +
    "  vec3 base=mix(wiped,frost,fog);\n" +
    // 水珠：法线反向取样清晰图——珠内是缩小倒像；加一点提亮与高光
    "  vec2 n=d.rg*2.0-1.0;\n" +
    "  float a=d.a;\n" +
    "  vec3 nrm=normalize(vec3(n,sqrt(max(1.0-dot(n,n),0.04))));\n" +
    "  vec3 dropCol=texture2D(uSharp,vUv-n*uRefract).rgb*1.10;\n" +
    "  dropCol+=vec3(pow(max(dot(nrm,uLight),0.0),60.0)*0.85);\n" +
    "  float m=smoothstep(0.10,0.45,a);\n" +
    "  vec3 col=mix(base,dropCol,m);\n" +
    // 珠缘一圈轻微压暗，立体感
    "  float rim=smoothstep(0.10,0.28,a)*(1.0-smoothstep(0.28,0.75,a));\n" +
    "  col*=1.0-0.22*rim;\n" +
    "  col*=uDim;\n" +
    "  gl_FragColor=vec4(col,1.0);\n" +
    "}";

  function buildProgram(gl, vsrc, fsrc) {
    function sh(type, src) {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        throw new Error("shader: " + gl.getShaderInfoLog(s));
      return s;
    }
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vsrc));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fsrc));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS))
      throw new Error("link: " + gl.getProgramInfoLog(p));
    return p;
  }

  function newTex(gl) {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return t;
  }

  // 绑定到固定纹理单元再上传。init=true 分配尺寸，之后走 texSubImage2D
  function upload(gl, unit, tex, src, init) {
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    if (init) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
    else gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, src);
  }

  function layer(w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    return { c, ctx: c.getContext("2d") };
  }

  // 底图按 cover 裁进 W×H。blurred=1 时做强模糊：连续缩到 1/16 再放大，
  // 双线性往返约等于十几像素的高斯，不依赖 ctx.filter；filter 可用时
  // 再叠一道把残余方块感磨掉。
  function coverCanvas(img, W, H, blurred) {
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    const cover = (cx, cw, ch) => {
      const s = Math.max(cw / img.naturalWidth, ch / img.naturalHeight);
      const w = img.naturalWidth * s, h = img.naturalHeight * s;
      cx.imageSmoothingEnabled = true;
      cx.imageSmoothingQuality = "high";
      cx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
    };
    if (!blurred) { cover(ctx, W, H); return c; }

    const t1 = document.createElement("canvas");
    t1.width = Math.max(2, W >> 2); t1.height = Math.max(2, H >> 2);
    const t2 = document.createElement("canvas");
    t2.width = Math.max(2, W >> 4); t2.height = Math.max(2, H >> 4);
    cover(t1.getContext("2d"), t1.width, t1.height);
    const x2 = t2.getContext("2d");
    x2.imageSmoothingEnabled = true;
    x2.drawImage(t1, 0, 0, t2.width, t2.height);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    if ("filter" in ctx) ctx.filter = "blur(" + Math.max(3, Math.round(W / 280)) + "px)";
    ctx.drawImage(t2, 0, 0, W, H);
    ctx.filter = "none";
    return c;
  }

  function loadImage(src) {
    if (src && src.nodeName === "IMG") {
      if (src.complete && src.naturalWidth) return Promise.resolve(src);
      return new Promise((res, rej) => {
        src.onload = () => res(src);
        src.onerror = () => rej(new Error("底图加载失败"));
      });
    }
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error(
        "底图加载失败：" + src + "（file:// 协议下会被拦，请走 http）"));
      im.src = src;
    });
  }

  // ------------------------------------------------------------ 主类
  class RainGlass {
    constructor(opts) {
      if (!opts || !opts.canvas || !opts.image)
        throw new Error("RainGlass: 需要 { canvas, image }");
      this.canvas = opts.canvas;
      this.imageSrc = opts.image;
      this.sim = new Sim(1280, 720);
      this.set(opts);
      this.stats = { fps: 0 };
      this._running = false;
      this._loop = (now) => {
        if (!this._running) return;
        const dt = clamp((now - this._last) / 1000, 0, 0.05); // 掉帧不炸物理
        this._last = now;
        this._frame(dt);
        this.stats.fps = this.stats.fps * 0.95 + 0.05 / Math.max(dt, 1e-3);
        requestAnimationFrame(this._loop);
      };
    }

    /** 三个标量都可选，都会被 clamp 进合法区间。返回当前状态。 */
    set(s) {
      const st = this.sim.state;
      if (s.intensity !== undefined) st.intensity = clamp(+s.intensity || 0, 0, 1);
      if (s.wind !== undefined) st.wind = clamp(+s.wind || 0, -1, 1);
      if (s.character !== undefined) st.character = clamp(+s.character || 0, 0, 1);
      return this.state;
    }

    get state() { return Object.assign({}, this.sim.state); }

    /** 全雾重来（调参时看回雾节奏很方便） */
    refog() {
      if (!this.F) return;
      const f = this.F.ctx;
      f.setTransform(1, 0, 0, 1, 0, 0);
      f.globalCompositeOperation = "source-over";
      f.fillStyle = "rgba(255,255,255,1)";
      f.fillRect(0, 0, this.F.c.width, this.F.c.height);
    }

    /** 改了 *Scale 类参数后重建各层（会清场） */
    rebuild() { this._resize(); }

    pause() { this._running = false; }
    resume() {
      if (this._running) return;
      this._running = true;
      this._last = performance.now();
      requestAnimationFrame(this._loop);
    }
    destroy() {
      this._running = false;
      global.removeEventListener("resize", this._onResize);
    }

    async _init() {
      this.img = await loadImage(this.imageSrc);
      this.sprite = makeDropSprite(96);
      this.disc = makeSoftDisc(64);
      this._initGL();
      this._resize();
      this._onResize = () => {
        clearTimeout(this._rt);
        this._rt = setTimeout(() => this._resize(), 150);
      };
      global.addEventListener("resize", this._onResize);
      this._running = true;
      this._last = performance.now();
      requestAnimationFrame(this._loop);
      return this;
    }

    _initGL() {
      const gl = this.canvas.getContext("webgl",
        { alpha: false, antialias: false, depth: false, stencil: false });
      if (!gl) throw new Error("WebGL 不可用");
      this.gl = gl;
      const prog = buildProgram(gl, VS, FS);
      gl.useProgram(prog);
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, "aPos");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

      this.u = {};
      ["uRefract", "uFrostLift", "uWipedBlur", "uDim", "uGrain", "uLight"]
        .forEach((n) => { this.u[n] = gl.getUniformLocation(prog, n); });
      [["uSharp", 0], ["uBlur", 1], ["uDrops", 2], ["uFog", 3]]
        .forEach(([n, i]) => { gl.uniform1i(gl.getUniformLocation(prog, n), i); });

      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      this.tex = [newTex(gl), newTex(gl), newTex(gl), newTex(gl)];
    }

    _resize() {
      const gl = this.gl;
      const cw = this.canvas.clientWidth || global.innerWidth;
      const ch = this.canvas.clientHeight || global.innerHeight;
      const W = Math.max(2, Math.round(cw * CFG.renderScale));
      const H = Math.max(2, Math.round(ch * CFG.renderScale));
      this.canvas.width = W;
      this.canvas.height = H;
      gl.viewport(0, 0, W, H);
      this.sim.setArea(cw, ch);                 // 模拟用 CSS px

      // 底图两版
      upload(gl, 0, this.tex[0], coverCanvas(this.img, W, H, 0), true);
      upload(gl, 1, this.tex[1], coverCanvas(this.img, W, H, 1), true);

      // 水珠层：D 静珠累积，C 每帧合成（D 打底 + 滑珠）
      const dw = Math.max(2, Math.round(W * CFG.dropTexScale));
      const dh = Math.max(2, Math.round(H * CFG.dropTexScale));
      this.D = layer(dw, dh);
      this.C = layer(dw, dh);
      this.dropScale = dw / cw;

      // 雾层
      const fw = Math.max(2, Math.round(W * CFG.fogTexScale));
      const fh = Math.max(2, Math.round(H * CFG.fogTexScale));
      this.F = layer(fw, fh);
      this.fogScale = fw / cw;
      this.refog();

      upload(gl, 2, this.tex[2], this.C.c, true);
      upload(gl, 3, this.tex[3], this.F.c, true);

      // 尺寸变了，旧坐标不再成立；简单起见清场重来
      this.sim.droplets.length = 0;
      this.sim.movers.length = 0;
      this.D.ctx.clearRect(0, 0, dw, dh);
    }

    // 模拟层对画布的全部操作走这个门面，坐标换算集中在这里
    _R() {
      if (this.__R) return this.__R;
      const self = this;
      return (this.__R = {
        drawDroplet(o) {
          const s = self.dropScale, ctx = self.D.ctx;
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.globalCompositeOperation = "source-over";
          const r = Math.max(o.r * s, 0.5);
          ctx.drawImage(self.sprite, o.x * s - r, o.y * s - r, r * 2, r * 2);
        },
        eraseDroplet(o) { this.eraseAt(o.x, o.y, o.r * 1.25); },
        eraseAt(x, y, r) {
          const s = self.dropScale, ctx = self.D.ctx;
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.globalCompositeOperation = "destination-out";
          ctx.beginPath();
          ctx.arc(x * s, y * s, Math.max(r * s, 0.6), 0, 6.2832);
          ctx.fill();
          ctx.globalCompositeOperation = "source-over";
        },
        fogErase(x, y, r) {
          const s = self.fogScale, ctx = self.F.ctx;
          ctx.setTransform(1, 0, 0, 1, 0, 0);
          ctx.globalCompositeOperation = "destination-out";
          const rp = Math.max(r * s, 1.2);
          ctx.drawImage(self.disc, x * s - rp, y * s - rp, rp * 2, rp * 2);
          ctx.globalCompositeOperation = "source-over";
        },
      });
    }

    _frame(dt) {
      const gl = this.gl;

      // 回雾：整面盖一层极薄的白，a' = a + ε(1−a)，指数逼近全雾
      const f = this.F.ctx;
      f.setTransform(1, 0, 0, 1, 0, 0);
      f.globalCompositeOperation = "source-over";
      f.fillStyle = "rgba(255,255,255," +
        Math.min(1, dt / CFG.refog_s).toFixed(5) + ")";
      f.fillRect(0, 0, this.F.c.width, this.F.c.height);

      // 模拟
      this.sim.step(dt, this._R());

      // 合成水珠层：静珠打底，滑珠按速度方向旋转拉伸画上去
      const cc = this.C.ctx, s = this.dropScale;
      cc.setTransform(1, 0, 0, 1, 0, 0);
      cc.clearRect(0, 0, this.C.c.width, this.C.c.height);
      cc.drawImage(this.D.c, 0, 0);
      const ms = this.sim.movers;
      for (let i = 0; i < ms.length; i++) {
        const m = ms[i];
        const sp = Math.hypot(m.vx, m.vy);
        const sy = 1 + Math.min(1.1, sp / 240);   // 越快越拉长
        const sx = 1 / Math.sqrt(sy);             // 体积守恒式压扁
        cc.setTransform(1, 0, 0, 1, 0, 0);
        cc.translate(m.x * s, m.y * s);
        cc.rotate(-Math.atan2(m.vx, m.vy));
        cc.scale(sx, sy);
        const r = m.r * s;
        cc.drawImage(this.sprite, -r, -r, r * 2, r * 2);
      }
      cc.setTransform(1, 0, 0, 1, 0, 0);

      // 上传两张动态纹理，设好本帧 look 参数，一次画完
      upload(gl, 2, this.tex[2], this.C.c, false);
      upload(gl, 3, this.tex[3], this.F.c, false);
      gl.uniform1f(this.u.uRefract, CFG.refract);
      gl.uniform1f(this.u.uFrostLift, CFG.frostLift);
      gl.uniform1f(this.u.uWipedBlur, CFG.wipedBlur);
      gl.uniform1f(this.u.uGrain, CFG.grain);
      gl.uniform1f(this.u.uDim, 1 - CFG.dimMax * this.sim.state.intensity);
      const L = CFG.light, len = Math.hypot(L[0], L[1], L[2]) || 1;
      gl.uniform3f(this.u.uLight, L[0] / len, L[1] / len, L[2] / len);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
  }

  RainGlass.mount = function (opts) {
    return new RainGlass(opts)._init();
  };
  RainGlass.CFG = CFG;
  RainGlass._internals = { Sim, Hash, CFG };   // 仅供离线自检

  global.RainGlass = RainGlass;
})(typeof window !== "undefined" ? window : this);
