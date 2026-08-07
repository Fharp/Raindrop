# 用法

覆盖同名文件即可，都是完整文件不是补丁。`changes.diff` 是六个改动文件的全量 diff。

## 必要的

```
rain_audio.js       五处 bug
index.html          抽屉内容（关于）；删掉不存在的 1.jpg；两处 aria
rain_menu.js        列表项补 role="option" / aria-selected
rain_place.js       只改了一行：清掉那个从不清除的 9s timer
.gitignore          原来只有一行 sound_orig/
tools/prune-repo.sh 剪仓库用，破坏性，脚本自己会解释并要求确认
```

## 可选的（不想要就整个跳过）

```
_headers                        CSP、Permissions-Policy、缓存策略
404.html                        只加了 @font-face —— 原来那里写了
                                font-family:"Noto Serif SC" 但字体根本没加载
make_font_subset.py             字表改成自动扫 HTML，不用手动维护 FIXED
```

## 字体子集是必须换的

`fonts/NotoSerifSC-rain.woff2` 已重新生成，205 字（原 160），88 KB。
抽屉里「关于」那段话的字原子集里没有，不换的话那些字会掉到系统回退字体上。
逐字复核过，205 字全覆盖，缺失 0。

## 另外，仓库根目录那个字体文件是坏的

`NotoSerifSC[wght].ttf` 是截断文件。完整的思源宋体可变字库 25,125,510 字节，
仓库里那份 1,572,864 字节 —— 正好 1.5 MiB，下载中断。fontTools 打开就
AssertionError，`make_font_subset.py` 现在根本跑不动。

```sh
git rm --cached "NotoSerifSC[wght].ttf"
rm "NotoSerifSC[wght].ttf"
```

要重生成子集时现取，不必进仓库（25 MB 也超过 Pages 单文件 25 MiB 上限）：

```sh
curl -LO "https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf"
python make_font_subset.py --font "NotoSerifSC[wght].ttf"
```

新 `.gitignore` 里已经写了 `NotoSerifSC*.ttf`。

## 剪仓库

```sh
bash tools/prune-repo.sh
```

先把 `.git` 备份到 `../Raindrop.git.backup-<时间戳>`，要求输入 `PRUNE` 确认，然后：

- `git rm -r --cached` 掉 `openmeteo_out/` 等构建输入 —— **只动索引，磁盘文件一个不少**
- 垃圾文件**移进** `_trash/`，不是删除
- `git commit --amend` 重写那唯一的 commit，再 `gc --prune=now`

4280 个文件 → 316 个，部署 672 MB → 61 MB。

脚本**不 push**。确认后自己执行 `git push --force origin main` —— 这会不可逆地
替换 GitHub 上的历史，推完 Pages 会自动重新部署。
