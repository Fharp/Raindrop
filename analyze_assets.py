#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_assets.py — 素材体检

解决的问题：随机取片段播放时，可能取到素材自身的淡入淡出、录音里雨势
收住的那几秒、或者编码器补白。听感上那是「雨停了」，但数据里没有这回事。
这一步预先把每条素材的响度曲线量出来，标出「可安全取用」的区段，
之后引擎与播放器都只在这些区段里取片段。

顺带把响度补偿也算了：四条雨互相之间、两条风互相之间都归一到同一响度，
否则强度交叉过渡的时候会听出台阶。

依赖 ffmpeg（解码与测量都交给它，不需要 numpy）。

    python analyze_assets.py --sound sound/
    python analyze_assets.py --sound sound/ --write-levels levels.json
    python analyze_assets.py --sound sound/ --drop-db 2.5 --min-region 10

产出 assets_profile.json，喂给 rain_engine.py --profile。
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

PROFILE_VERSION = "1.0.0"

# 分块 RMS 的块长。0.4 秒与 EBU R128 的 momentary 窗一致，
# 短到能抓住淡入淡出，长到不会被雨声本身的颗粒感干扰。
BLOCK_S = 0.4
DROP_DB = 3.0        # 比参考电平低这么多就判为「不可用」
MIN_REGION_S = 6.0   # 短于此的可用区段不要，取不出像样的片段
GUARD_S = 0.15       # 区段两端各再缩掉一点，块边界是粗的
SILENCE_DB = -120.0


def die(msg):
    sys.exit("错误：" + msg)


def need_ffmpeg():
    for exe in ("ffmpeg", "ffprobe"):
        if not shutil.which(exe):
            die("找不到 %s。Windows 上从 https://www.gyan.dev/ffmpeg/builds/ "
                "下载 essentials 包，把 bin 目录加进 PATH 即可。" % exe)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 测量

def probe(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels,duration",
             "-show_entries", "format=duration", "-of", "json", path])
    if r.returncode != 0:
        die("ffprobe 读不了 %s：%s" % (path, r.stderr.strip()[:200]))
    d = json.loads(r.stdout)
    st = (d.get("streams") or [{}])[0]
    dur = st.get("duration") or d.get("format", {}).get("duration")
    return {
        "sample_rate": int(st.get("sample_rate") or 0),
        "channels": int(st.get("channels") or 0),
        "duration_s": round(float(dur or 0.0), 3),
    }


def loudness(path):
    """整体响度与峰值。ebur128 给 LUFS，astats 给峰值。"""
    out = {"lufs_i": None, "lra": None, "peak_dbfs": None}
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    txt = r.stderr
    m = re.search(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", txt)
    if m:
        out["lufs_i"] = None if m.group(1) == "-inf" else float(m.group(1))
    m = re.search(r"LRA:\s*(-?[\d.]+)\s*LU", txt)
    if m:
        out["lra"] = float(m.group(1))

    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
             "-af", "astats=measure_perchannel=none", "-f", "null", "-"])
    m = re.search(r"Peak level dB:\s*(-?[\d.]+|-inf)", r.stderr)
    if m:
        out["peak_dbfs"] = SILENCE_DB if m.group(1) == "-inf" else float(m.group(1))
    return out


def rms_series(path, sample_rate, block_s):
    """逐块 RMS（dBFS）。返回 [(块起点秒, dB), ...]。"""
    n = max(1, int(round(sample_rate * block_s)))
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", path,
             "-af", "asetnsamples=n=%d,astats=metadata=1:reset=1,"
                    "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-" % n,
             "-f", "null", "-"])
    if r.returncode != 0:
        die("astats 失败 %s：%s" % (path, r.stderr.strip()[:200]))
    series, t = [], None
    for line in r.stdout.splitlines():
        m = re.match(r"frame:\d+\s+pts:\S+\s+pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.match(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-?inf|nan)", line)
        if m and t is not None:
            v = m.group(1)
            series.append((t, SILENCE_DB if v in ("-inf", "inf", "nan") else float(v)))
            t = None
    return series


# ---------------------------------------------------------------- 区段

def percentile(vals, p):
    if not vals:
        return SILENCE_DB
    s = sorted(vals)
    pos = (len(s) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo)


def find_regions(series, block_s, drop_db, min_region_s, guard_s, duration):
    """参考电平取中位数——对首尾淡变和中间的静段都不敏感。
    低于「参考 − drop_db」的块判为不可用，连续可用块合成区段。"""
    if not series:
        return [], SILENCE_DB, []
    levels = [v for _, v in series]
    ref = percentile(levels, 0.5)
    thr = ref - drop_db

    runs, start = [], None
    for i, (t, v) in enumerate(series):
        ok = v >= thr
        if ok and start is None:
            start = t
        elif not ok and start is not None:
            runs.append((start, t))
            start = None
    if start is not None:
        runs.append((start, duration))

    regions, dropped = [], []
    for a, b in runs:
        a2, b2 = a + guard_s, min(b, duration) - guard_s
        if b2 - a2 >= min_region_s:
            regions.append([round(a2, 3), round(b2, 3)])
        elif b - a > 0:
            dropped.append([round(a, 3), round(b, 3)])
    return regions, ref, dropped


def gaps_of(regions, duration):
    """区段之间被排除掉的部分，用来报告素材有多少内容不可用。"""
    out, cur = [], 0.0
    for a, b in regions:
        if a - cur > 0.05:
            out.append([round(cur, 3), round(a, 3)])
        cur = b
    if duration - cur > 0.05:
        out.append([round(cur, 3), round(duration, 3)])
    return out


# ---------------------------------------------------------------- 主流程

def load_assets():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from rain_engine import ASSETS
        return ASSETS
    except Exception as e:
        die("导入 rain_engine.ASSETS 失败（%s）。把本脚本和 rain_engine.py 放同一目录。" % e)


def main():
    ap = argparse.ArgumentParser(description="素材响度体检与可用区段标注")
    ap.add_argument("--sound", default="sound", help="素材根目录")
    ap.add_argument("--out", default="assets_profile.json")
    ap.add_argument("--write-levels", default=None,
                    help="顺便把算出的响度补偿写进这个文件，可直接喂给 --levels")
    ap.add_argument("--block", type=float, default=BLOCK_S)
    ap.add_argument("--drop-db", type=float, default=DROP_DB,
                    help="低于参考电平这么多 dB 判为不可用，默认 3.0")
    ap.add_argument("--min-region", type=float, default=MIN_REGION_S)
    ap.add_argument("--guard", type=float, default=GUARD_S)
    ap.add_argument("--max-trim", type=float, default=12.0, help="响度补偿上下限 dB")
    args = ap.parse_args()

    need_ffmpeg()
    assets = load_assets()
    if not os.path.isdir(args.sound):
        die("找不到素材目录 %s" % args.sound)

    print("素材体检   块长 %.2f s   下限 参考−%.1f dB   最短区段 %.1f s"
          % (args.block, args.drop_db, args.min_region))
    print("-" * 92)
    print("%-20s %8s %8s %8s %7s %6s  %s"
          % ("素材", "时长s", "LUFS", "峰值dB", "可用率", "区段", "备注"))

    out, by_kind = {}, {}
    for a in assets:
        path = os.path.join(args.sound, a["file"].replace("/", os.sep))
        if not os.path.exists(path):
            print("%-20s %s" % (a["id"], "缺文件：" + path))
            out[a["id"]] = {"file": a["file"], "missing": True}
            continue

        info = probe(path)
        loud = loudness(path)
        rec = {"file": a["file"], "kind": a["kind"], "missing": False}
        rec.update(info)
        rec.update(loud)
        warn = []

        if a.get("loop"):
            series = rms_series(path, info["sample_rate"] or 48000, args.block)
            regions, ref, small = find_regions(series, args.block, args.drop_db,
                                               args.min_region, args.guard,
                                               info["duration_s"])
            usable = sum(b - a2 for a2, b in regions)
            rec["ref_rms_db"] = round(ref, 2)
            rec["regions"] = regions
            rec["excluded"] = gaps_of(regions, info["duration_s"])
            rec["usable_s"] = round(usable, 2)
            rec["usable_ratio"] = round(usable / max(info["duration_s"], 1e-6), 4)
            rec["blocks"] = len(series)
            if not regions:
                warn.append("无可用区段，这条素材不能用于循环层")
            elif rec["usable_ratio"] < 0.6:
                warn.append("可用率偏低，素材内部电平起伏大")
            if len(regions) > 1:
                warn.append("被切成 %d 段（中间有明显变弱处，已排除）" % len(regions))
            longest = max((b - a2 for a2, b in regions), default=0.0)
            rec["longest_region_s"] = round(longest, 2)
            if longest < 12:
                warn.append("最长区段仅 %.1f s，片段会切得很碎" % longest)
        else:
            rec["regions"] = None

        rec["warnings"] = warn
        out[a["id"]] = rec
        by_kind.setdefault(a["kind"], []).append(a["id"])

        print("%-20s %8.1f %8s %8s %7s %6s  %s"
              % (a["id"], info["duration_s"],
                 "%.1f" % loud["lufs_i"] if loud["lufs_i"] is not None else "—",
                 "%.1f" % loud["peak_dbfs"] if loud["peak_dbfs"] is not None else "—",
                 ("%.0f%%" % (100 * rec["usable_ratio"])) if a.get("loop") else "—",
                 str(len(rec["regions"])) if a.get("loop") else "—",
                 "；".join(warn) if warn else ""))

    # ---- 响度补偿：组内归一 ----
    # 雨与风各自组内对齐即可，两组之间的平衡由引擎的 WIND_MASTER 管；
    # 雷是瞬态，按峰值对齐，用 LUFS 会被前后的静音拉偏。
    levels = {}
    for kind, ids in by_kind.items():
        key = "peak_dbfs" if kind == "thunder" else "lufs_i"
        vals = [out[i][key] for i in ids if out[i].get(key) is not None]
        if not vals:
            continue
        target = percentile(vals, 0.5)
        for i in ids:
            v = out[i].get(key)
            if v is None:
                continue
            trim = max(-args.max_trim, min(args.max_trim, target - v))
            out[i]["trim_db"] = round(trim, 2)
            levels[i] = round(trim, 2)   # 雷也逐条给，各条峰值能差十几 dB
        print("\n%s 组：对齐目标 %.1f %s"
              % (kind, target, "dBFS 峰值" if kind == "thunder" else "LUFS"))
        for i in ids:
            if "trim_db" in out[i]:
                print("    %-20s %+6.2f dB" % (i, out[i]["trim_db"]))
    levels.setdefault("thunder", 0.0)    # 整组雷的统一升降，留给你手调

    profile = {
        "profile_version": PROFILE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sound_root": args.sound,
        "params": {"block_s": args.block, "drop_db": args.drop_db,
                   "min_region_s": args.min_region, "guard_s": args.guard},
        "assets": out,
        "level_trim_db": levels,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=1)
    print("\n已写出 %s" % args.out)

    if args.write_levels:
        doc = {"_说明": "由 analyze_assets.py 自动算出，组内响度归一。手工微调后不会被覆盖，除非重跑本脚本。"}
        doc.update(levels)
        with open(args.write_levels, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print("已写出 %s" % args.write_levels)

    bad = [i for i, r in out.items() if r.get("warnings")]
    if bad:
        print("\n有 %d 条素材带备注，逐条看一眼：%s" % (len(bad), "、".join(bad)))


if __name__ == "__main__":
    main()
