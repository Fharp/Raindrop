#!/usr/bin/env python3
"""按名册与页面文案生成字体子集。

字表有三个来源，都是现读的，没有写死：
  1. cities.json 里的城市名与 slug
  2. index.html / 404.html 里真正会被渲染的文字（script、style、注释已剥掉）
  3. 本文件里的 FIXED —— 只放由 JS 拼出来、不出现在 HTML 里的那些字

所以加城市、改抽屉文案之后，重跑一次这个脚本就行。

用法：
    pip install fonttools brotli
    python make_font_subset.py --font "NotoSerifSC[wght].ttf"

字体来源：https://github.com/google/fonts/tree/main/ofl/notoserifsc
许可：SIL Open Font License 1.1，且**没有**声明保留字体名，
所以子集之后仍可沿用 Noto Serif SC 这个名字。把 OFL.txt 一并放进 fonts/。
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

# 两行文案里固定出现的字，以及标点、数字
FIXED = (
    "零〇一二三四五六七八九十"      # 汉字数字（〇 备用，想换年份写法时不必重新生成）
    "年月日"
    "此刻在下雨"
    "地球某处"                      # 定位取不到时左上角写的那四个字
    "的下一场雨将在小时后开始"      # 那座城没在下雨时第二行的句式
    "聆听静音"
    "，。、·"
    "0123456789:"
)

# 从 HTML 里抽出真正会被渲染的文字：先剥掉注释、script、style，再剥标签。
_STRIP = re.compile(
    r"<!--.*?-->|<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>",
    re.S | re.I,
)
_TAG = re.compile(r"<[^>]+>")


def html_text(path: pathlib.Path) -> str:
    """页面上会被排版渲染的文字。script 里那些只出现在 #err 的报错字串用的是
    等宽字体，不该进子集，所以整段剥掉。"""
    raw = path.read_text(encoding="utf-8")
    return _TAG.sub(" ", _STRIP.sub(" ", raw))


def charset(roster_path: pathlib.Path, html_paths) -> str:
    doc = json.loads(roster_path.read_text(encoding="utf-8"))
    chars = set(FIXED)
    for city in doc.get("cities", []):
        chars.update(city.get("name") or "")
        chars.update(city.get("slug") or "")      # 万一以后要显示 slug
    for h in html_paths:
        h = pathlib.Path(h)
        if not h.exists():
            print(f"跳过（不存在）：{h}", file=sys.stderr)
            continue
        for ch in html_text(h):
            # 跳过控制字符与空白，其余一律收进来
            if ch.isprintable() and not ch.isspace():
                chars.add(ch)
    return "".join(sorted(chars))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True, help="源字体（.ttf / .otf，可变字体亦可）")
    ap.add_argument("--roster", default="web_out/index/cities.json")
    ap.add_argument("--html", nargs="*", default=["index.html", "404.html"],
                    help="要扫的页面。页面上出现的字会自动并进字表，"
                         "改了抽屉文案重跑一次即可，不必手动维护 FIXED")
    ap.add_argument("--out", default="fonts/NotoSerifSC-rain.woff2")
    ap.add_argument("--weight", type=int, default=None,
                    help="把可变字体固定到某一档字重（如 300）。体积能再小一半，"
                         "代价是 CSS 里改 font-weight 不再生效")
    ap.add_argument("--print-only", action="store_true", help="只打印字表，不调 pyftsubset")
    args = ap.parse_args()

    roster = pathlib.Path(args.roster)
    if not roster.exists():
        print(f"找不到名册：{roster}", file=sys.stderr)
        return 1

    text = charset(roster, args.html)
    print(f"{len(text)} 个字符：{text}")
    if args.print_only:
        return 0

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    src = args.font
    if args.weight is not None:
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer

        font = TTFont(src)
        instancer.instantiateVariableFont(font, {"wght": args.weight}, inplace=True)
        src = str(out.with_suffix(".pinned.ttf"))
        font.save(src)

    cmd = [
        sys.executable, "-m", "fontTools.subset", src,
        f"--text={text}",
        f"--output-file={out}",
        "--flavor=woff2",
        "--layout-features=*",   # 可变字体的 wght 轴与标点定位规则都留着
        "--no-hinting",
        "--desubroutinize",
    ]
    proc = subprocess.run(cmd)
    if args.weight is not None:
        pathlib.Path(src).unlink(missing_ok=True)
    if proc.returncode == 0:
        print(f"{out} · {out.stat().st_size / 1024:.1f} KB")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
