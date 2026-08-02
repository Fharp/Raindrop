#!/usr/bin/env python3
"""按名册生成页面用的字体子集。

页面上会出现的字只有三类：名册里的城市名、汉字数字与「年月日」、
以及两行里那几个固定字。全字库的思源宋体有几 MB，子集下来通常在 30 KB 上下。

以后加城市，重跑一次这个脚本就行——字表是从 cities.json 现读的，没有写死。

用法：
    pip install fonttools brotli
    python make_font_subset.py --font NotoSerifSC[wght].ttf

字体来源：https://github.com/google/fonts/tree/main/ofl/notoserifsc
许可：SIL Open Font License 1.1，且**没有**声明保留字体名，
所以子集之后仍可沿用 Noto Serif SC 这个名字。把 OFL.txt 一并放进 fonts/。
"""

import argparse
import json
import pathlib
import subprocess
import sys

# 两行文案里固定出现的字，以及标点、数字、冒号
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


def charset(roster_path: pathlib.Path) -> str:
    doc = json.loads(roster_path.read_text(encoding="utf-8"))
    chars = set(FIXED)
    for city in doc.get("cities", []):
        chars.update(city.get("name") or "")
        chars.update(city.get("slug") or "")      # 万一以后要显示 slug
    return "".join(sorted(chars))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True, help="源字体（.ttf / .otf，可变字体亦可）")
    ap.add_argument("--roster", default="web_out/index/cities.json")
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

    text = charset(roster)
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
