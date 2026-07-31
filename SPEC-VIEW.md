# 雨窗视觉层 · 接口约定

装在底图与观察者之间。**引擎与 EQ 不动**，本层不读渲染谱文件，
只吃三个标量，由播放器逐帧转喂。

```
底图(jpg) ─► [雾面│凝结│流淌│回雾] ─► 屏幕
                      ▲
        rg.set({intensity, character, wind})   每帧一次
```

---

## 1. 文件

| 文件 | 内容 |
|---|---|
| `rain_view.html` | 页面本体：canvas + 临时播放键，键盘 / URL / `?debug=1` 调参入口 |
| `rain_glass.js` | 效果模块，无依赖，`window.RainGlass` |

**必须走 http 打开**（目录里 `python3 -m http.server` 即可）。
file:// 协议下浏览器把本地图片当跨域纹理拦掉，画面是黑的。

---

## 2. 输入

| 键 | 域 | 来源 |
|---|---|---|
| `intensity` | 0..1 | 渲染谱 `frames[i].intensity`，原样 |
| `character` | 0..1 | 渲染谱 `frames[i].character`。0 恒定层状，1 一阵一阵 |
| `wind` | −1..1 | 大小 = 风层 stem 增益之和（0..1），符号 = 方向。方向建议取 `frames[i].wind_pan`（缺省 +1），`ui.wind_dir_deg` 也能换算 |

三个值全部经 clamp，喂多快都行；模拟内部自己按 dt 走，
帧间不需要 ramp——雨强的过渡本来就该由渲染谱的 30 s ramp 提供。

## 3. 行为对应

| 现象 | 由谁驱动 |
|---|---|
| 磨砂雾面，细节不可辨 | 常在；`frostLift` 定白度 |
| 凝结小珠积累、蒸发 | 密度 ∝ intensity^1.6 |
| 大珠沿重力下滑、走走停停 | 数量 ∝ intensity^1.7；黏滞概率 `stick` |
| 滑珠吞并小珠、越滑越大 | 自动（空间哈希碰撞） |
| 擦痕露出半清晰底图 + 尾珠 | 滑珠路径；`wipedBlur` 定残余模糊 |
| 痕迹被新雾盖回 | 指数回雾，时间常数 `refog_s`（默认 14 s） |
| 斜着流、蛇行 | wind 的符号与大小 + `meander` |
| 降雨忽大忽小 | character 调制生成率（慢噪声，周期十几秒到分钟级） |
| 暴雨整体变暗 | `dimMax * intensity` |

## 4. 调参

全部参数在 `RainGlass.CFG`，控制台改了立即生效
（`renderScale / dropTexScale / fogTexScale` 例外，改后 `rg.rebuild()`）。
最先值得动的五个：`refract`（珠内倒像强度）、`frostLift`、`refog_s`、
`droplet.rate`、`mover.rate`。

页面入口：键盘 0–9 = intensity，←/→ = wind，↑/↓ = character，F = 回雾；
URL `?i=0.7&wind=-0.4&c=0.5&img=xx.jpg`；`?debug=1` 出滑杆与帧率。

## 5. 边界

- 播放键目前只负责消失，点击时向 `window` 派发 `rainglass:play`，
  音频起播挂这个事件即可。
- 窗口尺寸变化会清场重来（珠子坐标随尺寸失效，不值得迁移）。
- 性能瓶颈是水珠层每帧整张上传 GPU；卡先降 `dropTexScale`，再降
  `renderScale`。模拟本身（≤64 滑珠 + ≤4200 静珠）可忽略。
- 视觉参数的默认值只调过量级，最终以你屏幕上的观感为准；
  模拟层的守恒关系（强度单调、风向符号、上限）已离线自检过。
