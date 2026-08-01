#!/usr/bin/env python3
"""把循环素材裁短，并为每条素材生成同名 .opus。

为什么要做这件事
----------------
1. Cloudflare Pages 单个文件上限 25 MiB，超出的文件不会被部署，浏览器取到 404。
2. 起播前要解码的音频有 46.6 分钟，解出来是约 1 GB 的 Float32 PCM，
   这就是每次打开都要等一下的原因。
排片器只从 regions 里取 6 秒以上的片段做等功率交叠，而雨是宽带噪声，
120 秒和 15 分钟听不出区别，解码量却差五到八倍。

三种用法
--------
    python make_loops.py --check          # 只清点，不动任何文件
    python make_loops.py --dry-run        # 打印打算做什么，不动任何文件
    python make_loops.py --seconds 120    # 真做

会改写 sound/ 里的原文件
------------------------
真做的那次会先把整个 sound/ 复制到 sound_orig/；若 sound_orig/ 已存在则中止，
免得第二次运行拿裁过的文件覆盖备份。确实要在已有备份的情况下补做，
用 --only 指定那几条，并显式加 --skip-backup。

三道保护，防的是「拿旧 profile 去裁已经裁过的文件」这件事：
  1. 裁之前用 ffprobe 读磁盘上的真实时长，与 profile 里的 duration_s 不符就跳过；
  2. 裁之后校验产物时长，明显不对就丢弃产物、保留原文件；
  3. --only 只处理点名的那几条。

裁完必须重建，因为 regions 是相对原文件的偏移量：
    python analyze_assets.py --sound sound --out assets_profile.json
    python rain_engine.py --indir openmeteo_out --outdir web_out --profile assets_profile.json
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

GUARD_S = 0.5        # 从区段边缘往里让开一点，避开判定边界上的渐弱
DUR_TOL = 0.02       # 磁盘时长与 profile 时长允许的相对误差
LOOP_KINDS = ("rain", "wind")


def probe_duration(path):
    """读真实时长，秒。读不出返回 None。"""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return None


def run(cmd, dry, label):
    if dry:
        print(f"    [dry] {label}")
        return True
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print("    ffmpeg 失败：" + p.stderr.strip()[-400:], file=sys.stderr)
    return p.returncode == 0


def cut_window(rec, want):
    """在最长的可用区段里挑一段 want 秒的窗口，返回 (起点, 长度)。"""
    regions = rec.get("regions") or []
    if not regions:
        return None
    a, b = max(regions, key=lambda r: r[1] - r[0])
    a, b = a + GUARD_S, b - GUARD_S
    span = b - a
    if span <= 0:
        return None
    if span <= want:
        return a, span
    return a + (span - want) / 2, want      # 从区段中段取，前后都留一点


def inventory(assets, root):
    """清点：每条素材期望的路径、是否在、磁盘时长与 profile 是否对得上。"""
    print(f"{'id':20s} {'kind':8s} {'磁盘':>9s} {'profile':>9s} {'体积':>9s}  状态")
    missing, stale, ok = [], [], []
    for aid, rec in assets.items():
        src = root / rec["file"]
        want = rec.get("duration_s")
        if not src.exists():
            print(f"{aid:20s} {rec.get('kind',''):8s} {'—':>9s} {want or 0:8.1f}s {'—':>9s}  文件不在：{rec['file']}")
            missing.append(aid)
            continue
        got = probe_duration(src)
        size = src.stat().st_size / 1048576
        opus = src.with_suffix(".opus")
        mark = " +opus" if opus.exists() else ""
        if got is None:
            state = "读不出时长"
            stale.append(aid)
        elif want and abs(got - want) / want > DUR_TOL:
            state = "与 profile 不符（多半已裁过，profile 是旧的）"
            stale.append(aid)
        else:
            state = "一致"
            ok.append(aid)
        print(f"{aid:20s} {rec.get('kind',''):8s} {got or 0:8.1f}s {want or 0:8.1f}s {size:8.2f}M  {state}{mark}")
    print(f"\n一致 {len(ok)} 条 · 对不上 {len(stale)} 条 · 文件不在 {len(missing)} 条")
    if missing:
        print("文件不在的：" + ", ".join(missing))
    return missing, stale, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sound", default="sound")
    ap.add_argument("--profile", default="assets_profile.json")
    ap.add_argument("--backup", default="sound_orig")
    ap.add_argument("--seconds", type=float, default=120.0, help="每条循环保留多少秒")
    ap.add_argument("--bitrate", default="96k", help="opus 码率")
    ap.add_argument("--only", default=None, help="只处理这几条，逗号分隔的 asset id")
    ap.add_argument("--skip-backup", action="store_true",
                    help="备份目录已存在时照样继续。只在用 --only 补做时使用")
    ap.add_argument("--no-opus", action="store_true", help="只裁剪，不转码")
    ap.add_argument("--check", action="store_true", help="只清点，不动任何文件")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    prof, root = pathlib.Path(args.profile), pathlib.Path(args.sound)
    if not prof.exists():
        print(f"找不到 {prof}", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"找不到 {root}/", file=sys.stderr)
        return 1

    assets = json.loads(prof.read_text(encoding="utf-8"))["assets"]
    if args.only:
        want = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in want if s not in assets]
        if unknown:
            print("profile 里没有这些 id：" + ", ".join(unknown), file=sys.stderr)
            return 1
        assets = {k: v for k, v in assets.items() if k in want}

    if args.check:
        inventory(assets, root)
        return 0

    backup = pathlib.Path(args.backup)
    if backup.exists() and not args.skip_backup:
        print(f"{backup}/ 已存在。这说明素材可能已经裁过一轮，再跑整轮会拿裁过的文件覆盖备份。\n"
              f"先跑 --check 看清楚；只补做某几条就用 --only ID1,ID2 --skip-backup。", file=sys.stderr)
        return 1
    if not backup.exists():
        print(f"备份 {root}/ → {backup}/")
        if not args.dry_run:
            shutil.copytree(root, backup)

    print(f"\n裁剪（每条保留 {args.seconds:.0f} 秒）")
    for aid, rec in assets.items():
        if rec.get("kind") not in LOOP_KINDS or rec.get("missing"):
            continue
        src = root / rec["file"]
        if not src.exists():
            print(f"  {aid:20s} 文件不在：{rec['file']}")
            continue

        # 保护一：磁盘上的真实时长必须和 profile 对得上，否则 regions 是旧的
        want_dur = rec.get("duration_s")
        got = probe_duration(src)
        if got is None:
            print(f"  {aid:20s} 读不出时长，跳过")
            continue
        if want_dur and abs(got - want_dur) / want_dur > DUR_TOL:
            print(f"  {aid:20s} 磁盘 {got:.1f}s 与 profile {want_dur:.1f}s 不符，"
                  f"regions 已失效，跳过。要重裁请先重建 profile")
            continue

        win = cut_window(rec, args.seconds)
        if not win:
            print(f"  {aid:20s} 没有可用区段，跳过")
            continue
        start, length = win
        tmp = src.with_name("_trim_tmp" + src.suffix)
        before = src.stat().st_size
        print(f"  {aid:20s} {got:7.1f}s → {length:5.1f}s  （从 {start:.1f}s 起）")
        ok = run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
                  "-i", str(src), "-c", "copy", str(tmp)],
                 args.dry_run, f"裁 {src.name}")
        if args.dry_run:
            continue
        # 保护二：产物时长明显不对就丢掉，原文件不动
        out = probe_duration(tmp) if ok and tmp.exists() else None
        if not ok or out is None or out < length * 0.5:
            print(f"    产物不对（{out}），已丢弃，原文件保持不动")
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(src)
        print(f"    {before/1048576:.2f} → {src.stat().st_size/1048576:.2f} MiB")

    if args.no_opus:
        return 0

    print(f"\n转 opus（{args.bitrate}）")
    print("  播放器的格式探测是一次性的，务必让每一条素材都有 .opus。")
    total = missing = 0
    for aid, rec in assets.items():
        if rec.get("missing"):
            continue
        src = root / rec["file"]
        if not src.exists():
            print(f"  {aid:20s} 文件不在，没有生成 opus")
            missing += 1
            continue
        dst = src.with_suffix(".opus")
        ok = run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-map_metadata", "-1",
                  "-c:a", "libopus", "-b:a", args.bitrate, str(dst)],
                 args.dry_run, f"转 {dst.name}")
        if ok and not args.dry_run and dst.exists():
            total += dst.stat().st_size
            print(f"  {aid:20s} → {dst.name}  {dst.stat().st_size/1048576:.2f} MiB")
    if not args.dry_run:
        print(f"\nopus 合计 {total/1048576:.1f} MiB" +
              (f"；还有 {missing} 条没有 opus，补齐前不要部署" if missing else ""))

    print("\n接下来必须重建（regions 是相对原文件的偏移量，不重算就会指向不存在的位置）：")
    print("  python analyze_assets.py --sound sound --out assets_profile.json")
    print("  python rain_engine.py --indir openmeteo_out --outdir web_out --profile assets_profile.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
