# 怎么用这一包

## 1. 直接覆盖（文件是完整的，不是补丁）

```
index.html                      → 覆盖仓库根目录同名文件
404.html
rain_audio.js
rain_menu.js
rain_place.js
make_font_subset.py
.gitignore                      ← 原来只有一行 sound_orig/
_headers                        ← 新增，Cloudflare Pages 读它
README.md                       ← 新增
fonts/NotoSerifSC-rain.woff2    ← 重新生成，320 字（原 160 字，覆盖不全）
tools/prune-repo.sh             ← 新增，剪仓库用
```

`changes.diff` 是这六个改动文件的完整 diff，想逐条核对就看它。

`rain_caption.js` 不在这一包里 —— 时钟走本地时间是有意为之，那个改动已经撤回，
该文件与仓库里现有的完全一致，不用动。

## 2. 删掉那个坏掉的字体文件

仓库根目录的 `NotoSerifSC[wght].ttf` 是**截断文件**：完整的思源宋体可变字库是
25,125,510 字节，仓库里那份只有 1,572,864 字节（正好 1.5 MiB，典型的下载中断）。
fontTools 打开就报 AssertionError，`make_font_subset.py` 根本跑不动。

```sh
git rm --cached "NotoSerifSC[wght].ttf"
rm "NotoSerifSC[wght].ttf"
```

要重新生成子集时从 google/fonts 现取，不必进仓库（25 MB 也超过 Pages 单文件上限）：

```sh
curl -LO "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf"
python make_font_subset.py --font "NotoSerifSC[wght].ttf"
```

新的 `.gitignore` 里已经写了 `NotoSerifSC*.ttf`。

## 3. 本地跑一遍确认没坏

```sh
python -m http.server 8000 -b 127.0.0.1
```

要看的三件事：

- 开页面**不再弹定位授权框**（除非你以前给这个域名授过权）
- 点城市名 → 下拉最上面多了一条「此处」，点它才会问定位
- 右上角汉堡打开的抽屉里现在有内容了
- 换城市之后字都能正常显示（子集从 160 字扩到 320 字）

## 4. 剪仓库（破坏性，看清楚再跑）

```sh
bash tools/prune-repo.sh
```

脚本自己会解释要做什么，并要求输入 `PRUNE` 确认。它会先把 `.git` 整个备份到
`../Raindrop.git.backup-<时间戳>`，然后：

- `git rm -r --cached` 掉 `openmeteo_out/`、`openmeteo_out.bak/`、`cities15000.txt`
  等构建输入 —— **只动索引，磁盘上一个文件都不少**
- 把垃圾文件**移进** `_trash/`（不是删除）
- `git commit --amend` 重写那唯一的 commit，然后 `gc --prune=now`

跑完 4280 个文件会降到 316 个，部署体积 672 MB → 61 MB。

脚本**不会 push**。确认结果没问题之后自己执行：

```sh
git push --force origin main
```

`--force` 会不可逆地替换 GitHub 上的历史。推完 Cloudflare Pages 会自动重新部署。

## 5. 还需要你自己做的两件事

- **核对 Freesound 授权**：`freesound.org/s/572443`（trp）与 `freesound.org/s/796529`
  （fran_marenco）。CC0 / CC BY / CC BY-NC 三种都可能，逐条不同。查清楚以后补进
  `index.html` 抽屉、`README.md` 的来源表、`web_out/manifest.json` 的 `assets[]`。
  若其中有 CC BY-NC，商用会受限。
- **补 raindrop-fx 的许可全文**：MIT 要求随分发保留。把上游
  `github.com/SardineFish/raindrop-fx` 的 `LICENSE` 拷进 `bundle/`。
