#!/usr/bin/env bash
#
# 把构建输入与垃圾文件从 git 里摘出去，并重写那唯一的一个 commit。
#
# ┌─ 这个脚本会改动什么 ────────────────────────────────────────────┐
# │                                                                  │
# │ 会：                                                             │
# │   · git rm -r --cached <路径>   —— 只从索引里移除，磁盘上的文件  │
# │                                    一个都不会少                  │
# │   · 把垃圾文件移进 _trash/       —— 是移动，不是删除              │
# │   · git commit --amend          —— 重写当前分支最顶上那个 commit │
# │   · git gc --prune=now          —— 回收已经没有引用的对象        │
# │                                                                  │
# │ 不会：                                                           │
# │   · 不会 push。最后那一步要你自己手动执行                        │
# │   · 不会删除 openmeteo_out/ 等任何数据目录                       │
# │                                                                  │
# └──────────────────────────────────────────────────────────────────┘
#
# ⚠ amend + gc 之后，被摘掉的那些 blob 在本地就找不回来了。
#   脚本会先把整个 .git 备份成 ../<仓库名>.git.backup-<时间戳>；
#   万一改坏了，把它换回来就是。
#
# ⚠ 之后需要 git push --force。远端 GitHub 上的历史会被不可逆地替换掉。
#   如果这个仓库在别处还有 clone 或 fork，它们会和新历史分叉。
#
# ⚠ push 之后 Cloudflare Pages 会自动重新部署。部署内容会变（少掉 3900 多个文件）。

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
REPO="$(basename "$PWD")"

# ── 摘出索引，但保留在磁盘上 ───────────────────────────────────
UNTRACK=(
  openmeteo_out
  openmeteo_out.bak
  __pycache__
  cities15000.txt
  cities.json
  cities.skipped.txt
  assets_profile.json
  levels.json
  "NotoSerifSC[wght].ttf"
  mnt
)

# ── 移进 _trash/ 的垃圾 ───────────────────────────────────────
TRASH=(
  "others/新建文本文档.txt"
  "others/photo.xlsx"
  "sound/新建 Microsoft Excel 工作表.xlsx"
  "sound/Wind/ronkoster2023-heavy-rain-amp-wind-1021-min-rk-177371.mp3"
  "rain_glass.js"
  "rain_audio.nocomment.js"
  "bundle/index.js.map"
)

echo "仓库：$PWD"
echo "当前 commit 数：$(git rev-list --count HEAD)"
echo
if [ "$(git rev-list --count HEAD)" -ne 1 ]; then
  echo "⚠ 不止一个 commit。--amend 只会重写最顶上那个，"
  echo "  更早的 commit 里的大文件仍然留在历史里，仓库不会真正变小。"
  echo "  那种情况请改用 git filter-repo，不要用这个脚本。"
  echo
fi

echo "将从 git 索引中移除（磁盘文件保留）："
for p in "${UNTRACK[@]}"; do
  [ -e "$p" ] && printf "  %-28s %s\n" "$p" "$(du -sh "$p" 2>/dev/null | cut -f1)"
done
echo
echo "将移入 _trash/（移动，不是删除）："
for p in "${TRASH[@]}"; do
  [ -e "$p" ] && printf "  %-60s %s\n" "$p" "$(du -sh "$p" 2>/dev/null | cut -f1)"
done
echo
echo "随后会执行 git commit --amend 与 git gc --prune=now。"
echo "这一步之后，被摘掉的对象在本地就找不回来了（.git 会先备份）。"
echo
read -r -p "确认继续请输入 PRUNE：" ans
[ "$ans" = "PRUNE" ] || { echo "已取消，什么都没做。"; exit 1; }

# ── 备份 .git ─────────────────────────────────────────────────
BK="../${REPO}.git.backup-$(date +%Y%m%d-%H%M%S)"
cp -a .git "$BK"
echo "→ .git 已备份到 $BK"

# ── 摘出索引 ──────────────────────────────────────────────────
for p in "${UNTRACK[@]}"; do
  git rm -r --cached --quiet --ignore-unmatch -- "$p" || true
done

# ── 移垃圾 ────────────────────────────────────────────────────
mkdir -p _trash
for p in "${TRASH[@]}"; do
  if [ -e "$p" ]; then
    mkdir -p "_trash/$(dirname "$p")"
    git rm --cached --quiet --ignore-unmatch -- "$p" || true
    mv -- "$p" "_trash/$p"
  fi
done
grep -qxF '_trash/' .gitignore || echo '_trash/' >> .gitignore

# ── 重写那一个 commit ─────────────────────────────────────────
git add -A
git commit --amend --no-edit --quiet
git reflog expire --expire=now --all
git gc --prune=now --quiet

echo
echo "完成。"
echo "  跟踪文件数：$(git ls-files | wc -l)"
echo "  .git 体积：  $(du -sh .git | cut -f1)"
echo "  部署体积：   $(git ls-files -z | xargs -0 du -ch 2>/dev/null | tail -1 | cut -f1)"
echo
echo "确认无误后，手动推送（会不可逆地替换远端历史）："
echo "  git push --force origin main"
