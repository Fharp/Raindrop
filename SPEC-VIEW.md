# 雨窗视觉层 · 接口约定

```
web_out/scores/*.json ─► rain_audio.js ─┬─► Web Audio ─► 扬声器
                                        │
                             逐帧 intensity / character / wind
                             实时电平 / 雷的精确时刻
                                        │
                                        ▼
                                 rain_bridge.js
                                        │  改写 options
                                        ▼
                     raindrop-fx ─► canvas ─► 屏幕
```

自绘的 `rain_glass.js` 作废，可以从仓库删掉。渲染改用 **raindrop-fx**
（MIT，WebGL2，<https://github.com/SardineFish/raindrop-fx>），
它是 Codrops 的 RainEffect 那一脉的优化实现，做的正是这件事，
而且做得比重写一遍好。

---

## 1. 放置

raindrop-fx 不进 npm 也能用，取它仓库里的 `bundle/index.js` 即可：

```
Raindrop/
├── bundle/index.js        ← 从 SardineFish/raindrop-fx 下载，唯一的外部依赖
├── rain_view.html
├── rain_audio.js
├── rain_bridge.js
├── rain_eq.js             （已有）
├── eq_profile.json        （已有）
├── 1.jpg                  （底图，可用 ?img= 换）
├── web_out/…              （已有）
└── sound/…                （已有）
```

**必须走 http 打开**（`python3 -m http.server` 即可）。file:// 下底图会被当
跨域纹理拦掉，且 `fetch` 取不到本地 JSON。

---

## 2. 三层各自的职责

| 文件 | 做什么 | 不做什么 |
|---|---|---|
| `bundle/index.js` | 水珠的物理与合成、雾、折射、光照 | 不知道有音频 |
| `rain_audio.js` | 拉谱、解码素材、排片、放雷、按 SPEC 走 UTC 对齐 | 不碰画面 |
| `rain_bridge.js` | 每帧把声音的状态换算成 raindrop-fx 的 options | 不发声、不画 |

`rain_audio.js` 与 `audition.html` 是同一套排片逻辑（等功率交叠、区段随机
切入、前瞻泵），只把文件夹选择器换成 `fetch`，并多开两个出口：
`audio.state`（逐帧物理量）与 `audio.takeStrikes()`（已排进时间线的雷）。

---

## 3. 映射

| 谱里的量 | 画面上的表现 | 落到哪个 option |
|---|---|---|
| `intensity` | 雨点密度、大小、下落速度 | `spawnInterval` `spawnSize` `gravity` |
| `intensity` | 细雾珠密度 | `dropletsPerSeconds` |
| `intensity` | 擦痕留多久 | `evaporate` `mistTime` |
| `intensity` | 拖尾长短 | `trailDropDensity` |
| `character` | 一阵一阵：三个不互质周期叠成的慢噪声乘在生成率上 | `spawnInterval` `dropletsPerSeconds` |
| `wind_*` 增益 × `wind_pan` | 水珠斜着流 | `xShifting` |
| `wind_lfo` | 斜度随阵风起伏 | 同上，叠加正弦 |
| **实时电平** | 雨声一响，玻璃上细雾珠就密一层 | `dropletsPerSeconds` `spawnInterval` |
| `thunder[]` | 雾被照亮 + 整屏一闪，亮点位置跟 `pan` | `mistColor` `raindropDiffuseLight` + `#flash` |

`wind_pan` 是风向换算出的声像。正面吹来时 `pan≈0`，玻璃上的水本来就不斜——
所以横向分量取 `windMag × windPan` 是对的，不是偷懒。

**实时电平**是这一版和上一版的实质差别。谱一小时才换一帧，光跟谱的话画面
是每小时跳一次的阶梯；接上 `AnalyserNode` 之后，素材本身的起伏、风的 LFO、
交叠处的呼吸都会进到画面里。

---

## 4. 为什么自己驱动帧

raindrop-fx 自带的 `start()` 循环把 `dt` 写死成 `0.03`：

```js
const time = <Time>{ dt: 0.03, total: delay / 1000 };
```

于是模拟时间跟着刷新率跑——60 Hz 上是 1.8 倍速，120 Hz 上 3.6 倍。
雾的回复速率是 `dt / mistTime`、细雾珠数是 `dropletsPerSeconds × dt`，
全都吃这个 `dt`。`rain_bridge` 因此不调 `start()`，改成自己拿真实 `dt`
喂 `simulator.update()` 与 `renderer.render()`；拿不到内部对象时退回
`start()`，功能不缺，只是速度会随屏幕变。

---

## 5. 调参

全部在 `RainBridge.CFG`，控制台改立即生效：

```js
RainBridge.CFG.mistTimeCalm = 4      // 轻雨时擦痕多快被雾盖回
RainBridge.CFG.levelDrive   = 0.6    // 画面跟声音起伏的深度
RainBridge.CFG.windGain     = 1.2    // 风把水珠吹多斜
RainBridge.CFG.gustDepth    = 0.8    // 阵性
```

raindrop-fx 自身的观感参数在 `fx.options`，也是随改随生效：
`refractBase` / `refractScale`（珠内倒像的强度）、`mistColor`、
`backgroundBlurSteps`、`raindropLightBump`、`smoothRaindrop`。

页面入口：空格播放／停止，`D` 换一场雨，`0–9` 与 `←/→` 调空转时的雨强与风，
`?debug=1` 出滑杆与读数，`?sec=30` 把一小时压成 30 秒看全程，
`?img=xx.jpg` 换底图，`?vol=0.5` 定音量。

---

## 6. 边界

- **首次点播放要等**。六条循环层加十二条雷约 120 MB，全部 `decodeAudioData`
  之后才起播，进度写在按钮上。上线前把素材转成 opus 能小一个量级，
  顺带绕开 SPEC §6 里那个 mp3 补白问题。
- **底图越锐利越容易穿帮**。参照片里那种观感有一半来自背景本身就是散景，
  眼睛没有可对照的几何，就不会去核对折射对不对。这张天际线是清晰照片，
  `backgroundBlurSteps` 已经开到 4，还嫌假就再加一档，或直接换一张虚焦底图。
  （rainymood 的画面是录像还是实时生成，我没有查到可确认的说法，不下判断。）
- **性能**。1400 颗封顶，作者给的数据是 2000 颗约 6 ms/帧。卡就先降
  `spawnLimit`，再降 `dpr`（`rain_view.html` 里现在压在 1.5）。
- **未在本机验证**。容器里没有 WebGL 也没有音频设备，我只做了语法检查和
  桥接层的离线自检（全输入区间无 NaN、`spawnInterval` 恒正且有序、
  风向符号正确、闪光衰减归零并复位雾色、无音频时空转仍出雨）。
  raindrop-fx 与 Web Audio 的实际表现要你在浏览器里看。
