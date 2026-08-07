# 雨窗

用真实天气数据驱动的雨声与雨窗渲染。逐小时的降水、风与雷电被转成一份「谱」，
浏览器按谱把雨声、风声、雷声实时混出来，同时用 WebGL 画玻璃上的水痕。
钟点对齐到世界时的当日时刻，所以听到的是那座城市此刻的雨。

## 结构

```
index.html          页面与主控
rain_audio.js       取谱、解码、按谱调度 Web Audio
rain_eq.js          室内外均衡与雷声闪避
rain_bridge.js      把音频状态喂给 raindrop-fx
rain_place.js       从坐标找最近的城市
rain_caption.js     左上角那两行字与时钟
rain_menu.js        城市下拉与右侧抽屉
bundle/index.js     raindrop-fx 预打包版（MIT，SardineFish）
fonts/              思源宋体子集（OFL 1.1）
web_out/            发布用的谱子与索引 —— 由管线生成
sound/              音频素材
audition.html       素材试听页（开发用）
```

管线（Python，都不参与运行时）：

```
pythondownload.py     从 Open-Meteo 抓原始数据 → openmeteo_out/
rain_engine.py        原始数据 → 谱
rain_select.py        场次筛选
pack_scores.py        谱打包 → web_out/scores/
analyze_assets.py     素材分析 → assets_profile.json
make_eq_profile.py    → eq_profile.json
make_loops.py         循环区间标注
make_cities.py        GeoNames → 城市名册
make_font_subset.py   按名册与页面文案生成字体子集
```

## 重新生成字体子集

字表是现读的：名册里的城市名、`index.html` / `404.html` 上会渲染的文字、
以及脚本里那点由 JS 拼出来的固定字。加城市或改了抽屉文案，重跑一次就行。

全字库不进仓库（25 MB，超过 Cloudflare Pages 单文件上限），从
[google/fonts](https://github.com/google/fonts/tree/main/ofl/notoserifsc) 取：

```sh
pip install fonttools brotli
python make_font_subset.py --font "NotoSerifSC[wght].ttf"
```

页面上所有字号都用 300 字重。加 `--weight 300` 把可变轴固定下来，
体积从 142 KB 降到 76 KB，代价是以后改 `font-weight` 不再生效。

## 本地起服务

```sh
python -m http.server 8000 -b 127.0.0.1
```

## 部署

Cloudflare Pages，仓库根目录即发布目录，没有构建步骤。
`_headers` 里带了 CSP 与缓存策略。免费版限制：单站点 20,000 个文件、单文件 25 MiB。

## URL 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `debug` | 关 | 右下角调试面板 |
| `follow` | `1` | 是否跟随城市的天气状态；`0` 关掉 |
| `sec` | `3600` | 一帧（一个数据小时）折合多少秒实时播放 |
| `vol` | `0.7` | 音量 |
| `dpr` | `1` | 渲染分辨率倍率上限 |
| `img` | — | 指定底图 |
| `watch` | `300` | 轮询间隔（秒），下限 60 |

## 来源与许可

| | |
|---|---|
| 天气数据 | [Open-Meteo](https://open-meteo.com/)，CC BY 4.0，基于 ECMWF IFS HRES |
| 城市坐标 | [GeoNames](https://www.geonames.org/)，CC BY 4.0 |
| 雨与风 | Pixabay：soundsforyou、u_dui2p5vt45、adsabbhelp、freesound_community、restfuldreamingtunes |
| 雷 | Freesound：trp（572443）、fran_marenco（796529） |
| 底图 | Pixabay：sakmei（7715286） |
| 字体 | Noto Serif SC，SIL Open Font License 1.1（`fonts/OFL.txt`） |
| 渲染 | [raindrop-fx](https://github.com/SardineFish/raindrop-fx)，MIT，SardineFish |

**待办**：Freesound 上每条声音的授权是逐条不同的（CC0 / CC BY / CC BY-NC）。
到 `freesound.org/s/572443` 与 `freesound.org/s/796529` 核对之后，把具体许可补进
上表、`web_out/manifest.json` 的 `assets[]`、以及页面抽屉里。
其中若有 CC BY-NC，商用会受限。

MIT 要求随分发保留许可全文：把 raindrop-fx 上游的 `LICENSE` 拷进 `bundle/`。

本仓库自身尚未选定许可证。
