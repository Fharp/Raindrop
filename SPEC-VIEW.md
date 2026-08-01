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
│                            拿到后先跑一次 patch_droplet_hash.py，见 §3
├── patch_droplet_hash.py
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
| `wind_*` 增益 × `wind_pan` | 水珠被吹斜 | 桥接层的 `_applyWind` |
| `wind_lfo` | 斜度随阵风起伏 | 同上，叠加正弦 |
| 风的大小 | 每滴自身晃动的幅度 | `xShifting` |
| **实时电平** | 雨声一响，玻璃上细雾珠就密一层 | `dropletsPerSeconds` `spawnInterval` |
| `thunder[]` | 雾被照亮 + 整屏一闪，亮点位置跟 `pan` | `mistColor` `raindropDiffuseLight` + `#flash` |

**风要单独说**。raindrop-fx 的 `xShifting` 只有大小没有方向——它在
`randomMotion()` 里是这么用的：

```js
this.shifting = random() * randomRange(...options.xShifting);   // random() ∈ (-1, 1)
```

那个 `random()` 把符号也随机掉了，所以无论把区间挪到哪儿，结果都是每滴各自
乱斜、左右各半，做不出「被风统一吹斜」。这里因此把两件事拆开：`xShifting`
只留下「每滴晃多少」，方向那一份由桥接层在模拟之后自己推：

```js
d.pos.x += Math.abs(d.velocity.y) * w * windMaxShift * dt;
```

正比于该滴当前的下落速度，形式与库内部的 `vx = |vy| × shifting` 一致，只是
符号由风定。于是停住不动的珠子不会被平移，流得快的被吹得多；拖尾珠生成在
母珠位置上，整条痕迹跟着斜。

**没有风声就不斜**。`wind_breeze + wind_strong` 低于 `CFG.windFloor`（0.03）
时横向分量强制归零，水珠竖直落。风向分量取 `windMag × windPan`：`wind_pan`
是风向换算出的声像，正面吹来时 `pan≈0`，玻璃上的水本来就不斜。轻风
（如 `wind_breeze 0.19`、`pan −0.21`）算出来约 1°，肉眼近乎竖直；只有强风层
起来了才会明显斜，满风约 12°。

**为什么画面会「冻住」**。库里只在水珠滑出画面下沿或被吞并时才置
`destroied`；蒸发只是把 `mass` 一路减下去，减穿 0 之后 `size = sqrt(负数) = NaN`，
水珠看不见了却永远留在 `raindrops` 里，照常占着 `spawnLimit` 的名额。挂久了
名额被占满，`simulator.update` 里那句 `if (raindrops.length <= spawnLimit)`
就不再生成新滴——雨看着停了。桥接层每帧扫一遍按 `mass` 收尸。

**真正的运动总闸是 `slipRate`，不是 `gravity`**。水珠动不动取决于
`gravity × mass > resistance`，而

```js
const maxResistance = lerp(...spawnSize, 1 - slipRate) ** 2 * 4;
this.resistance = randomRange(0, 1) * gravity * maxResistance;
```

`slipRate` 默认 0，于是 `maxResistance` 按最大粒径算，绝大多数水珠的
`mass` 根本压不过随机出来的 `resistance`，全程黏在原地。把 `slipRate`
抬到 0.9 上下，`maxResistance` 按接近最小粒径算，水珠才普遍会走。
`gravity` 是可以改的（`fx.options.gravity`），但改它只影响已经在动的那些
滑得多快，改不出「大家都在动」。

**参照片测出来的量**。960×540 下逐帧统计：任一时刻同时在动的独立目标中位
12 个（四分位 7–20，阵性峰值到 130），运动像素只占 1.7%，目标等效直径中位
约 9 px、P95 约 30 px（折到 1080p）；但把画面切成 12×8 格，两秒之内每一格
都发生过运动。所以要的不是「一大片一起往下流」，是**又小又密、遍布全屏、
一直在发生**。参数据此重标：粒径降到 12–68，生成率提到 28–100 滴/秒，
蒸发率提到 16–26 让水珠有生有灭，`motionInterval` 压到 0.05–0.22 秒让
走走停停更频繁。

**基础雨量**。谱里的 `intensity = 0` 是「刚好越过 0.3 mm/h 可听下限」的毛毛雨，
画面上不能真按 0 处理，否则玻璃是干的、隔几秒才掉一滴。所以视觉用的雨强先抬到
`CFG.intensityFloor`（0.32）之上，最小档也是约 8 滴/秒、玻璃始终挂着水。
阵性的倍率同时卡了上限（`gustMaxScale`），阵与阵之间可以变小，但不许真的停。

**那几道大斜线**。raindrop-fx 的细水珠位置是在 vertex shader 里算的：

```glsl
vec2 pos = uSpawnRect.xy + uSpawnRect.zw * vec2(
    gold_noise(vec2(1, id), uSeed + 1.0),
    gold_noise(vec2(id, 1), uSeed + 2.0));
```

横纵两个坐标由同一个标量 `uSeed` 驱动，`uSeed` 每帧重掷一次。于是对固定的
`id`，`(x, y)` 随 seed 变化是在画面上**描一条一维曲线，而不是撒点**。
每帧的实例数 = `dropletsPerSeconds × dt`，这个数一小——120/秒、60 fps 下每帧
只有 2 个——可用的 `id` 就只有 1 和 2，水珠于是长期落在那两三条曲线上，
积成屏幕上那几道大斜线。细水珠图层只被经过的水珠擦掉，不会自己淡出，
所以痕迹是永久的。

把生成率抬高（`dropletsFloor` 1800/秒起）只是缓解：id 铺开到几十个，
曲线多了、叠在一起才像纹理。**要根治必须换掉那个哈希**，跑一次就好：

```bash
python3 patch_droplet_hash.py bundle/index.js
```

它把上面那行整段换成

```glsl
fract(sin(dot(vec3(xy, seed), vec3(12.9898, 78.233, 37.719))) * 43758.5453)
```

——x、y 各自混进 id 与 seed，从此互不相关，撒出来是真正的二维散点。
会先备份 `.bak`，重复执行安全，只影响细水珠的分布。打完之后
`dropletsFloor` 就可以往回调，省一点填充率。

**「画面像是冻住了」的另一半原因**。库的生成条件是这么写的：

```js
if (this.raindrops.length <= this.options.spawnLimit) { …trySpawn… }
```

名额一满就**彻底停止生成**——不是少生成，是一滴都不再来。而水珠只在滑出
画面下沿或被吞并时才置 `destroied`；蒸发只把 `mass` 一路减下去，减穿 0 之后
`size = sqrt(负数) = NaN`，水珠看不见了却永远留在数组里占着名额。跑几分钟，
名额被一群看不见的尸体占满，新雨滴再也不来，剩下的慢慢流完，屏幕就静止了。
`_sweep` 因此按质量收尸，并用一个随拥挤程度自动上浮的阈值把占用率压在
`cullTarget`（0.86），永远给新滴留余量。正在流动的水珠不收——那是画面上
「在动」的那部分。

**分辨率归一**。所有尺寸、速度、密度都以 1080 px 高为基准，实际渲染尺寸不同时
按 `S = height/1080` 换算：长度与速度 ×S，单位面积上的密度 ×S²。不做这一步，
同一套参数在 4K 上就是「小得多、也稀得多」，正是「填不满屏幕」的来源。
页面默认把 `devicePixelRatio` 压到 1（`?dpr=1.5` 可提高）：画面本来蒙着一层雾，
锐度没用，省下的填充率换成更多水珠更划算。

**参照片的量化标定**（取自你给的第二段录像，1920×1080）：静态细纹理约 1000 个
斑块、等效直径中位 3.4 px、P90 7.4 px、覆盖率 13.6%；83 ms 内位置有变化的斑块
约 34 个、面积中位 62 px。`dropletSize` 与 `spawnSize` 就是照这个定的。

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

## 5. 调试面板与快捷键

`?debug=1` 打开右上角面板：城市与场次两个下拉、一条可拖的进度条（按小时，
带小数）、当前帧的本地时刻与 `mm:ss / 总长`、空转时的 `i / c / w` 三个滑杆，
以及帧率、水珠数与上限、每秒生成滴数、细水珠率、分辨率系数 S、收尸线、
实时电平、素材已解码几条以及用的是 opus 还是原始文件。

快捷键（正式页面也有，不需要 `?debug`）：

| 键 | 作用 |
|---|---|
| 空格 | 播放 / 停止 |
| `D` | 换一场雨（按 UTC 钟点重新随机） |
| `[` `]` | 同城上一场 / 下一场 |
| `←` `→` | 快退 / 快进 6 分钟（0.1 帧） |
| `0`–`9` | 空转（未播放）时的雨强 |

URL：`?sec=30` 把一小时压成 30 秒看全程，`?dpr=1.5` 提高分辨率，
`?img=` 换底图，`?vol=` 定音量。

---

## 6. 调参

全部在 `RainBridge.CFG`，控制台改立即生效：

```js
RainBridge.CFG.slipRateCalm   = 0.95  // 再往上＝更多水珠在流，1 是全都动
RainBridge.CFG.intensityFloor = 0.45  // 抬高＝最小档的雨也更密
RainBridge.CFG.dropletsFloor  = 2600  // 玻璃更湿；没打哈希补丁前别低于 1800
RainBridge.CFG.mistTimeCalm   = 8     // 擦痕多快被雾盖回
RainBridge.CFG.levelDrive     = 0.5   // 画面跟声音起伏的深度
RainBridge.CFG.gustDepth      = 0.5   // 阵性；越大阵与阵之间落差越明显
RainBridge.CFG.windMaxShift   = 0.30  // 满风时斜多少（0.22 ≈ 12°）
RainBridge.CFG.windGain       = 2.0   // 多小的风就算满风；嫌暴雨里斜得不够就调它
RainBridge.CFG.windFloor      = 0.10  // 抬高它＝更多小时被判成无风
RainBridge.CFG.spawnLimitMax  = 3200  // 卡帧率就往下调，这是总水珠数的硬顶
RainBridge.CFG.cullTarget     = 0.80  // 调低＝留更多名额给新滴，新雨来得更勤
```

raindrop-fx 自身的观感参数在 `fx.options`，也是随改随生效：
`refractBase` / `refractScale`（珠内倒像的强度）、`mistColor`、
`backgroundBlurSteps`、`raindropLightBump`、`smoothRaindrop`。

---

## 7. 边界

- **起播延迟**。页面一载入就开始准备，不等点击：`AudioContext` 允许在手势
  之前建（建出来是 suspended），`decodeAudioData` 也不需要手势。四条雨层
  一上来全开，`play()` 只 `await` **当前这一帧真正有增益的那几条**；风层和
  十二条雷在后台继续，解完一条 `_pump` 就按当前帧把它淡进来。点击时只剩
  `ctx.resume()`。按钮在准备好之前是禁用状态并缓慢呼吸，不写字。

  真要再快就得从体积下手，这一步得你自己做，收益最大——120 MB 的 mp3 转
  opus 之后约 10 MB，解码时间同比缩短：

  ```bash
  cd sound
  find . \( -name '*.mp3' -o -name '*.flac' \) | while read -r f; do
    ffmpeg -v error -i "$f" -c:a libopus -b:a 96k "${f%.*}.opus"
  done
  ```

  `rain_audio.js` 会自动优先找同名 `.opus`，找不到才退回 manifest 里写的原始
  文件，转完不用改任何配置。`?debug=1` 面板末行会显示当前用的是哪一种。
  这同时绕开了 SPEC §6 那个 mp3 编码器补白问题。
- **城市、时间、峰值那一条已经去掉**，播放器仍然把它们放在 `audio.info` 里，
  以后要做界面直接取。
- **底图越锐利越容易穿帮**。参照片里那种观感有一半来自背景本身就是散景，
  眼睛没有可对照的几何，就不会去核对折射对不对。这张天际线是清晰照片，
  `backgroundBlurSteps` 已经开到 4，还嫌假就再加一档，或直接换一张虚焦底图。
  （rainymood 的画面是录像还是实时生成，我没有查到可确认的说法，不下判断。）
- **性能**。总水珠数 = `spawnLimit × S²`，封顶 `spawnLimitMax`（2600）。
  作者给的数据是 2000 颗约 6 ms/帧。卡就先降 `spawnLimitMax`，再确认
  `dpr` 没被 `?dpr=` 调高——默认已经压在 1。
- **未在本机验证**。容器里没有 WebGL 也没有音频设备，我只做了语法检查和
  桥接层的离线自检（全输入区间无 NaN、`spawnInterval` 恒正且有序、
  风向符号正确、闪光衰减归零并复位雾色、无音频时空转仍出雨、收尸只收停着的、
  以及在 1080p／1252p／1878p 三档下模拟三分钟堆积，**没有任何一帧因名额顶满
  而停止生成**）。raindrop-fx 与 Web Audio 的实际表现要你在浏览器里看。

---

## 8. 一个必须知道的坑：库的加载路径会静默挂死

zogra-renderer（raindrop-fx 的渲染底座）里，底图是这样取的：

```js
async url(url) {
    const buffer = await fetch(url).then(r => r.arrayBuffer());   // 没查 r.ok
    return await this.buffer(buffer);
}
```

拿到字节之后：

```js
const img = new Image();
img.src = URL.createObjectURL(new Blob([buffer]));
if (img.complete) complete(); else img.onload = complete;        // 没有 onerror
```

两处叠加的后果是：底图 404 时，服务器返回的错误页正文被当成图片字节，
`<img>` 解不出来，而 **`onerror` 根本没挂**，于是那个 Promise 永远不会 settle。
`loadAssets()` 挂死 → `start()` 不返回 → 后面的代码一行都不跑。屏幕全黑、
按钮没反应、控制台干干净净——没有任何东西告诉你出了什么事。

所以底图改成页面自己 `fetch`、自己查状态码、自己解码，再把解好的位图交给库
（`background` 接受 `TextureData`，不一定要是 URL 字符串）。同时：

- 所有 `await` 都套了超时，最长 30 秒就变成屏幕左下角的一行红字，
  写明卡在哪一步、试过哪些文件、各自是什么状态码；
- 渲染循环体套了 try/catch。rAF 回调里抛异常不会冒泡到任何地方，只会让
  循环停止排下一帧，表现同样是「画面静止但毫无线索」；现在会报出来；
- 音频预载与画面完全解耦，画面挂了照样能听。
