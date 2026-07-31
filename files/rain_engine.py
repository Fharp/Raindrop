#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rain_engine.py — 把真实降水事件编译成网页可直接播放的渲染谱

输入（pythondownload.py 的产物）
    openmeteo_out/{slug}_index.csv
    openmeteo_out/{slug}_meta.json

输出
    web_out/manifest.json               素材表、全局播放参数、覆盖区间
    web_out/index/cities.json           城市名册
    web_out/index/city/{slug}.json      单城事件摘要 + 钟点可用性 + 本地日期表
    web_out/index/hour/{HH}.json        UTC 钟点 -> 候选（城市, 事件, 帧号）
    web_out/scores/{slug}/{event}.json  逐小时渲染谱

约定
    1 个数据小时 = 1 小时实时播放。帧长 3600 秒，帧间线性渐变 ramp_seconds。
    stems 里的增益是最终绝对值，前端只需把每条 stem 接一个 GainNode。
    降雪不出声：可听判定只看液态降水，雪只进 ui 字段。
    只用标准库。
"""

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python < 3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

ENGINE_VERSION = "1.0.0"

# ---------------------------------------------------------------- 素材表
# id 是前端用的稳定键；file 是相对 audio_root 的路径。
ASSETS = [
    # --- 雨：两条梯子，轻雨层再按「持续/阵性」分成两个样本 ---
    {"id": "rain_light_steady", "kind": "rain", "rung": 0, "character": "steady",
     "file": "Rain/soundsforyou-soft-rain-ambient-111154.mp3",
     "source": "https://pixabay.com/sound-effects/soft-rain-ambient-111154/",
     "label": "轻雨（持续）", "loop": True},
    {"id": "rain_light_gusty", "kind": "rain", "rung": 0, "character": "convective",
     "file": "Rain/soundsforyou-calm-rain-ambient-sound-15-min-147850.mp3",
     "source": "https://pixabay.com/sound-effects/nature-calm-rain-ambient-sound-15-min-147850/",
     "label": "轻雨（急骤携风）", "loop": True},
    {"id": "rain_mid", "kind": "rain", "rung": 1, "character": None,
     "file": "Rain/u_dui2p5vt45-rain-heavy-loud-191411.mp3",
     "source": "https://pixabay.com/sound-effects/nature-rain-heavy-loud-191411/",
     "label": "中雨量", "loop": True},
    {"id": "rain_heavy", "kind": "rain", "rung": 2, "character": None,
     "file": ("Rain/adsabbhelp-heavy-raining-rain-on-tree-natural-clear-sound-"
              "abbas-ali-quiet-life-relaxing-music-11938.mp3"),
     "source": ("https://pixabay.com/sound-effects/nature-heavy-raining-rain-on-tree-natural-"
                "clear-sound-abbas-ali-quiet-life-relaxing-music-11938/"),
     "label": "大雨量", "loop": True},

    # --- 风：两级 ---
    {"id": "wind_breeze", "kind": "wind", "rung": 0,
     "file": "Wind/freesound_community-bushes-medium-heavy-wind-in-dry-vegetation-19537.mp3",
     "source": "https://pixabay.com/sound-effects/nature-bushes-medium-heavy-wind-in-dry-vegetation-19537/",
     "label": "微风", "loop": True},
    {"id": "wind_strong", "kind": "wind", "rung": 1,
     "file": "Wind/restfuldreamingtunes-sounds-of-nature-wind-in-the-desert-262406.mp3",
     "source": "https://pixabay.com/sound-effects/nature-sounds-of-nature-wind-in-the-desert-262406/",
     "label": "大风", "loop": True},

    # --- 雷：远雷（滚雷，录音自带雨底）与近雷（干雷，爆裂） ---
    {"id": "thunder_far_01", "kind": "thunder", "proximity": "far", "has_rain_bed": True,
     "file": "Thunder/572443__trp__180806-thunder-distant-rolling-boomy-rain-drops-toronto_01.flac",
     "source": "https://freesound.org/people/TRP/sounds/572443/", "loop": False},
    {"id": "thunder_far_02", "kind": "thunder", "proximity": "far", "has_rain_bed": True,
     "file": "Thunder/572443__trp__180806-thunder-distant-rolling-boomy-rain-drops-toronto_02.flac",
     "source": "https://freesound.org/people/TRP/sounds/572443/", "loop": False},
] + [
    {"id": "thunder_near_%02d" % i, "kind": "thunder", "proximity": "near", "has_rain_bed": False,
     "file": "Thunder/796529__fran_marenco__dry-storm-thunders_%02d.flac" % i,
     "source": "https://freesound.org/people/fran_marenco/sounds/796529/", "loop": False}
    for i in range(1, 11)
]

THUNDER_FAR = [a["id"] for a in ASSETS if a["kind"] == "thunder" and a["proximity"] == "far"]
THUNDER_NEAR = [a["id"] for a in ASSETS if a["kind"] == "thunder" and a["proximity"] == "near"]

# ---------------------------------------------------------------- 触发下限与映射常数
# 这些是我替你定的下限，全部可在命令行覆盖。

RAIN_FLOOR_MM = 0.30        # 液态降水低于此值判为不可听，与抓取脚本一致
MIN_EVENT_HOURS = 1         # 事件最短可听小时数
MIN_EVENT_PEAK_MM = 0.30    # 整场峰值下限；默认等于可听下限，即不额外过滤
# 默认取「不额外过滤」，理由：这样每座城的事件数与抓取脚本自己数出来的一致，
# 整条链路可对账；而且「真实下过的雨都能听到」本来就是这个项目的前提。
# 若线上觉得 1 小时的毛毛雨太薄，用 --min-hours 2 --min-peak 0.5 收紧即可。

# 绝对强度锚点 mm/h -> 0..1。15 mm/h 及以上一律封顶为暴雨。
ABS_ANCHORS = [(0.30, 0.00), (1.00, 0.25), (2.50, 0.50), (7.50, 0.80), (15.00, 1.00)]
# 分城锚点用该城可听小时的分位数，位置固定在下面这几档
CITY_LEVELS = [0.35, 0.65, 0.88, 1.00]   # 对应 P50 / P85 / P97 / max

MASTER_DB_FLOOR = -12.0     # intensity=0 时的雨床增益
MASTER_DB_LULL = -19.0      # 事件内被跨接的那个无雨小时
MASTER_CURVE = 1.15         # 增益曲线弯度

WIND_FLOOR_MS = 3.4         # 蒲福 3 级下限；以下判为无风，不渲染
WIND_BREEZE_FULL_MS = 7.0   # 微风层达到满增益
WIND_STRONG_FROM_MS = 8.0   # 开始向大风层交叉
WIND_STRONG_FULL_MS = 15.0  # 完全落在大风层
WIND_GUST_MIX = 0.35        # w_eff = (1-x)*wind + x*gust
WIND_MASTER = 0.55          # 风相对雨的整体压低

# 素材间响度补偿 dB。四条雨、两条风的原始电平不一定齐，用耳朵对齐后填这里；
# 也可以用 --levels levels.json 从外部文件覆盖，不必改代码。
LEVEL_TRIM_DB = {
    "rain_light_steady": 0.0,
    "rain_light_gusty": 0.0,
    "rain_mid": 0.0,
    "rain_heavy": 0.0,
    "wind_breeze": 0.0,
    "wind_strong": 0.0,
    "thunder": 0.0,          # 整组雷统一升降
}

THUNDER_MIN_SCORE = 0.05    # thunder_score 低于此值不打雷
THUNDER_CODE_FLOOR = 0.15   # weather_code 判为雷暴时的分数下限
THUNDER_LAMBDA_BASE = 1.0   # 每小时雷击次数 = BASE + SPAN * score^GAMMA
THUNDER_LAMBDA_SPAN = 17.0
THUNDER_GAMMA = 1.3
THUNDER_LAMBDA_CAP = 12.0   # 每小时上限。气象上可以更密，但库里只有 12 条素材，
                            # 再密就会听出重复。这是素材约束，不是物理约束。
THUNDER_MIN_GAP_S = 4.0     # 两次雷击最小间隔
THUNDER_NO_REPEAT = 4       # 最近这么多次雷击内不重用同一条素材
THUNDER_NEAR_CUT = 0.45     # 亲近度高于此值用干雷，否则用滚雷
THUNDER_MASTER = 0.85

FRAME_SECONDS = 3600
RAMP_SECONDS = 30           # 帧边界上所有 stem 增益的线性渐变时长
CITY_SWITCH_GAP_MS = 1200   # 换城时有意留出的静音

FALLBACK_UTC_OFFSET = {     # zoneinfo 无 tzdata 时的退路，不含夏令时
    "Asia/Shanghai": 8, "Asia/Hong_Kong": 8, "Asia/Macau": 8, "Asia/Taipei": 8,
    "Asia/Tokyo": 9, "Asia/Singapore": 8,
    "Europe/London": 0, "Europe/Paris": 1,
    "America/New_York": -5, "America/Chicago": -6, "America/Los_Angeles": -8,
}
_TZ_WARNED = set()


# ---------------------------------------------------------------- 小工具

def ffloat(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else (hi if x > hi else x)


def db_to_lin(db):
    return 10.0 ** (db / 20.0)


def piecewise(x, anchors):
    """anchors 为按 x 升序的 (x, y)，区间外取端点值。"""
    if x <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x <= x1:
            if x1 <= x0:
                return y1
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return anchors[-1][1]


def quantile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo)


def parse_utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def get_tz(name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    off = FALLBACK_UTC_OFFSET.get(name)
    if off is None:
        return timezone.utc
    if name not in _TZ_WARNED:
        _TZ_WARNED.add(name)
        print("  警告：找不到时区数据 %s，退回固定偏移 UTC%+d（不含夏令时）。"
              "装 tzdata 可修正：pip install tzdata" % (name, off), file=sys.stderr)
    return timezone(timedelta(hours=off))


def to_local(dt_utc, tz):
    return dt_utc.astimezone(tz)


def stable_rng(*parts):
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam < 30:
        limit = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
            if k > 200:
                return k
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


# ---------------------------------------------------------------- 读取

INDEX_FIELDS = ("time_utc", "liquid_mm", "showers_frac", "snowfall_cm", "snow_depth_m",
                "temperature_c", "wind_ms", "gust_ms", "wind_dir_deg", "cloud_cover",
                "visibility_m", "is_day", "cape", "thunder_score", "weather_code")


def read_index(path):
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        missing = [c for c in INDEX_FIELDS if c not in (rd.fieldnames or [])]
        if missing:
            raise RuntimeError("%s 缺少列：%s" % (path, ", ".join(missing)))
        for d in rd:
            rows.append({
                "utc": d["time_utc"],
                "liq": ffloat(d["liquid_mm"]),
                "showers": clamp(ffloat(d["showers_frac"])),
                "snow_cm": ffloat(d["snowfall_cm"]),
                "snow_depth": ffloat(d["snow_depth_m"]),
                "temp": ffloat(d["temperature_c"]),
                "wind": ffloat(d["wind_ms"]),
                "gust": ffloat(d["gust_ms"]),
                "wdir": ffloat(d["wind_dir_deg"]),
                "cloud": ffloat(d["cloud_cover"]),
                "vis": ffloat(d["visibility_m"]),
                "is_day": int(ffloat(d["is_day"])),
                "cape": ffloat(d["cape"]),
                "thunder": ffloat(d["thunder_score"]),
                "wcode": int(ffloat(d["weather_code"])),
            })
    return rows


# ---------------------------------------------------------------- 事件切分
# 与抓取脚本同一规则：连续可听小时成一场，允许跨接 1 小时空档。
# 差别只有一处：这里的「可听」只认液态降水，降雪不出声。

def find_events(flags):
    events, i, n = [], 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        start = end = i
        j = i + 1
        while j < n:
            if flags[j]:
                end = j
                j += 1
            elif j + 1 < n and flags[j + 1]:
                j += 2
                end = j - 1
            else:
                break
        events.append((start, end))
        i = end + 1
    return events


# ---------------------------------------------------------------- 强度标定

def calibrate(rows, cfg):
    aud = [r["liq"] for r in rows if r["liq"] >= cfg.rain_floor]
    if not aud:
        return None
    pts = [(cfg.rain_floor, 0.0)]
    for q, level in zip((0.50, 0.85, 0.97), CITY_LEVELS[:3]):
        pts.append((quantile(aud, q), level))
    pts.append((max(aud), CITY_LEVELS[3]))
    # 去掉不严格递增的锚点，避免分段函数退化
    clean = [pts[0]]
    for x, y in pts[1:]:
        if x > clean[-1][0] + 1e-9:
            clean.append((x, y))
    if len(clean) == 1:
        clean.append((clean[0][0] + 1.0, 1.0))
    return clean


def intensity_of(liq, city_anchors, mode):
    a = piecewise(liq, ABS_ANCHORS)
    if mode == "absolute" or city_anchors is None:
        return clamp(a)
    c = piecewise(liq, city_anchors)
    if mode == "city":
        return clamp(c)
    return clamp(max(a, c))          # mode == "max"，默认


# ---------------------------------------------------------------- 渲染一帧

def rain_stems(inten, character):
    """强度 0..1 映射到 3 级梯子上的等功率交叉；轻雨层再按 character 分裂成两个样本。"""
    p = 2.0 * clamp(inten)                       # 0=轻 1=中 2=大
    if p <= 1.0:
        th = p * math.pi / 2
        light, mid, heavy = math.cos(th), math.sin(th), 0.0
    else:
        th = (p - 1.0) * math.pi / 2
        light, mid, heavy = 0.0, math.cos(th), math.sin(th)
    ph = clamp(character) * math.pi / 2
    return {
        "rain_light_steady": light * math.cos(ph),
        "rain_light_gusty": light * math.sin(ph),
        "rain_mid": mid,
        "rain_heavy": heavy,
    }


def wind_stems(wind, gust, cfg):
    w = (1.0 - WIND_GUST_MIX) * wind + WIND_GUST_MIX * gust
    if w < cfg.wind_floor:
        return {}, w, None
    stage = clamp((w - cfg.wind_floor) / max(WIND_BREEZE_FULL_MS - cfg.wind_floor, 1e-6))
    t = clamp((w - WIND_STRONG_FROM_MS) / (WIND_STRONG_FULL_MS - WIND_STRONG_FROM_MS))
    th = t * math.pi / 2
    stems = {
        "wind_breeze": stage * math.cos(th) * WIND_MASTER,
        "wind_strong": stage * math.sin(th) * WIND_MASTER,
    }
    ratio = gust / max(wind, 0.5)
    lfo = {"rate_hz": round(0.05 + 0.10 * clamp((ratio - 1.1) / 1.4), 3),
           "depth": round(clamp((ratio - 1.15) / 1.35) * 0.6, 3)}
    return stems, w, lfo


def thunder_strikes(row, rng, cfg):
    s = row["thunder"]
    if row["wcode"] in (95, 96, 99):
        s = max(s, THUNDER_CODE_FLOOR)
    if s < cfg.thunder_min:
        return []
    lam = min(THUNDER_LAMBDA_BASE + THUNDER_LAMBDA_SPAN * (s ** THUNDER_GAMMA),
              THUNDER_LAMBDA_CAP)
    n = poisson(rng, lam)
    if n <= 0:
        return []
    out, last, recent = [], -1e9, []
    for t in sorted(rng.uniform(0.0, FRAME_SECONDS) for _ in range(n)):
        if t - last < THUNDER_MIN_GAP_S:
            continue
        last = t
        rho = clamp(s * rng.uniform(0.5, 1.35))
        near = rho >= THUNDER_NEAR_CUT
        pool = THUNDER_NEAR if near else THUNDER_FAR
        asset = pick_unrepeated(rng, pool, recent)
        recent.append(asset)
        if len(recent) > THUNDER_NO_REPEAT:
            recent.pop(0)
        gain = (0.45 + 0.55 * rho) if near else (0.28 + 0.32 * rho)
        out.append({"at": round(t, 2), "asset": asset,
                    "gain": round(gain * THUNDER_MASTER, 3),
                    "pan": round(rng.uniform(-0.7, 0.7), 2),
                    # 轻微变速。雷是宽带瞬态，±8% 听起来像远近大小不同，
                    # 不会像变调；这是掩盖素材重复最省事的一招。
                    "rate": round(rng.uniform(0.92, 1.08), 3)})
    return out


def pick_unrepeated(rng, pool, recent):
    """优先从最近没用过的素材里取。远雷只有 2 条，池子必然不够，
    这时至少保证不与上一次相同——否则会退化成 50% 概率连播同一条。"""
    if len(pool) == 1:
        return pool[0]
    fresh = [a for a in pool if a not in recent]
    if not fresh:
        last = recent[-1] if recent else None
        fresh = [a for a in pool if a != last] or list(pool)
    return rng.choice(fresh)


def render_frame(idx, row, ev_burst, city_anchors, cfg, tz, slug):
    lull = row["liq"] < cfg.rain_floor
    if lull:
        inten = 0.0
        master_db = MASTER_DB_LULL
    else:
        inten = intensity_of(row["liq"], city_anchors, cfg.calibration)
        master_db = MASTER_DB_FLOOR * ((1.0 - inten) ** MASTER_CURVE)
    master = db_to_lin(master_db)

    character = clamp(0.6 * row["showers"] + 0.4 * clamp(ev_burst, 0.0, 1.5) / 1.5)
    trim = cfg.levels
    stems = {k: round(v * master * trim.get(k, 1.0), 4)
             for k, v in rain_stems(inten, character).items()}

    w_stems, w_eff, lfo = wind_stems(row["wind"], row["gust"], cfg)
    for k, v in w_stems.items():
        stems[k] = round(v * trim.get(k, 1.0), 4)
    stems = {k: v for k, v in stems.items() if v >= 0.005}

    rng = stable_rng(cfg.seed, slug, row["utc"])
    strikes = thunder_strikes(row, rng, cfg)
    grp = cfg.levels.get("thunder", 1.0)
    for st in strikes:
        # 逐条素材的补偿 × 整组补偿。十几条雷的峰值可以差十几 dB，
        # 不逐条对齐的话「远雷/近雷」的距离感会被素材本身的电平盖掉。
        f = cfg.levels.get(st["asset"], 1.0) * grp
        if f != 1.0:
            st["gain"] = round(min(1.5, st["gain"] * f), 3)

    dt = parse_utc(row["utc"])
    frame = {
        "i": idx,
        "utc": row["utc"],
        "hour_utc": dt.hour,
        "local": to_local(dt, tz).strftime("%Y-%m-%dT%H:%M"),
        "intensity": round(inten, 3),
        "character": round(character, 3),
        "master_db": round(master_db, 2),
        "stems": stems,
    }
    if lfo and ("wind_breeze" in stems or "wind_strong" in stems):
        frame["wind_lfo"] = lfo
        frame["wind_pan"] = round(0.5 * math.sin(math.radians(row["wdir"])), 2)
    if strikes:
        frame["thunder"] = strikes
    if not cfg.slim:
        frame["ui"] = {
            "liquid_mm": round(row["liq"], 2),
            "temp_c": round(row["temp"], 1),
            "wind_ms": round(row["wind"], 1),
            "gust_ms": round(row["gust"], 1),
            "wind_dir_deg": round(row["wdir"]),
            "wind_eff_ms": round(w_eff, 1),
            "cloud_pct": round(row["cloud"]),
            "visibility_m": round(row["vis"]),
            "is_day": row["is_day"],
            "weather_code": row["wcode"],
            "snowfall_cm": round(row["snow_cm"], 2),
            "snow_depth_m": round(row["snow_depth"], 3),
            "cape": round(row["cape"]),
            "thunder_score": round(row["thunder"], 4),
        }
    return frame


# ---------------------------------------------------------------- 编译单城

def compile_city(slug, cfg):
    ipath = os.path.join(cfg.indir, "%s_index.csv" % slug)
    mpath = os.path.join(cfg.indir, "%s_meta.json" % slug)
    if not (os.path.exists(ipath) and os.path.exists(mpath)):
        return None
    with open(mpath, "r", encoding="utf-8") as f:
        meta = json.load(f)
    rows = read_index(ipath)
    if not rows:
        return None

    city = meta.get("city", {})
    tzname = city.get("timezone", "UTC")
    tz = get_tz(tzname)
    anchors = calibrate(rows, cfg)

    flags = [r["liq"] >= cfg.rain_floor for r in rows]
    spans = find_events(flags)

    kept, dropped = [], 0
    for s, e in spans:
        aud = sum(1 for k in range(s, e + 1) if flags[k])
        peak = max(rows[k]["liq"] for k in range(s, e + 1))
        if aud < cfg.min_hours or peak < cfg.min_peak:
            dropped += 1
            continue
        kept.append((s, e))

    score_dir = os.path.join(cfg.outdir, "scores", slug)
    os.makedirs(score_dir, exist_ok=True)

    summaries, hour_map = [], {h: [] for h in range(24)}
    cov_end = parse_utc(rows[-1]["utc"])

    for eid, (s, e) in enumerate(kept):
        liqs = [rows[k]["liq"] for k in range(s, e + 1)]
        mean = sum(liqs) / len(liqs)
        if mean > 0 and len(liqs) > 1:
            var = sum((x - mean) ** 2 for x in liqs) / (len(liqs) - 1)
            burst = math.sqrt(var) / mean
        else:
            burst = 0.0

        frames = [render_frame(k - s, rows[k], burst, anchors, cfg, tz, slug)
                  for k in range(s, e + 1)]

        nxt = kept[eid + 1][0] if eid + 1 < len(kept) else None
        gap = (nxt - e - 1) if nxt is not None else None
        start_dt, end_dt = parse_utc(rows[s]["utc"]), parse_utc(rows[e]["utc"])

        thunder_hours = sum(1 for fr in frames if "thunder" in fr)
        peak_inten = max(fr["intensity"] for fr in frames)

        summary = {
            "event_id": eid,
            "start_utc": rows[s]["utc"],
            "end_utc": rows[e]["utc"],
            "hours": e - s + 1,
            "audible_hours": sum(1 for k in range(s, e + 1) if flags[k]),
            "start_local": to_local(start_dt, tz).strftime("%Y-%m-%dT%H:%M"),
            "end_local": to_local(end_dt, tz).strftime("%Y-%m-%dT%H:%M"),
            "date_local": to_local(start_dt, tz).strftime("%Y-%m-%d"),
            "date_utc": start_dt.strftime("%Y-%m-%d"),
            "liquid_mean_mm": round(mean, 3),
            "liquid_peak_mm": round(max(liqs), 2),
            "liquid_sum_mm": round(sum(liqs), 2),
            "burstiness": round(burst, 3),
            "intensity_peak": round(peak_inten, 3),
            "thunder_hours": thunder_hours,
            "thunder_peak": round(max((rows[k]["thunder"] for k in range(s, e + 1)), default=0.0), 4),
            "wind_peak_ms": round(max(rows[k]["wind"] for k in range(s, e + 1)), 1),
            "gust_peak_ms": round(max(rows[k]["gust"] for k in range(s, e + 1)), 1),
            "has_snow": any(rows[k]["snow_cm"] > 0 for k in range(s, e + 1)),
            "gap_to_next_hours": gap,
            "hours_to_coverage_end": int((cov_end - end_dt).total_seconds() // 3600),
            "score": "scores/%s/%d.json" % (slug, eid),
        }
        summaries.append(summary)

        for fr in frames:
            hour_map[fr["hour_utc"]].append([eid, fr["i"]])

        score = {
            "engine_version": ENGINE_VERSION,
            "city": slug,
            "city_name": city.get("name", slug),
            "timezone": tzname,
            "event_id": eid,
            "frame_seconds": FRAME_SECONDS,
            "ramp_seconds": cfg.ramp,
            "summary": summary,
            "frames": frames,
        }
        with open(os.path.join(score_dir, "%d.json" % eid), "w", encoding="utf-8") as f:
            json.dump(score, f, ensure_ascii=False, separators=(",", ":"))

    dates_local, dates_utc = {}, {}
    for sm in summaries:
        dates_local.setdefault(sm["date_local"], []).append(sm["event_id"])
        dates_utc.setdefault(sm["date_utc"], []).append(sm["event_id"])

    cov_start = parse_utc(rows[0]["utc"])
    cov_hours = int((cov_end - cov_start).total_seconds() // 3600) + 1
    city_doc = {
        "engine_version": ENGINE_VERSION,
        "slug": slug,
        "name": city.get("name", slug),
        "timezone": tzname,
        "coordinates": meta.get("requested_coordinates", {}),
        "grid_cell": meta.get("grid_cell", {}),
        "coverage_utc": {"start": rows[0]["utc"], "end": rows[-1]["utc"],
                         "hours": cov_hours, "days": round(cov_hours / 24.0, 1)},
        "calibration": {"mode": cfg.calibration,
                        "anchors_mm": [[round(x, 3), y] for x, y in (anchors or [])]},
        "events": summaries,
        "events_total": len(summaries),
        "events_dropped": dropped,
        "playable_hours": sum(sm["hours"] for sm in summaries),
        "hour_of_day": {str(h): hour_map[h] for h in range(24)},
        "hour_of_day_count": {str(h): len(hour_map[h]) for h in range(24)},
        "dates_local": dates_local,
        "dates_utc": dates_utc,
    }
    with open(os.path.join(cfg.outdir, "index", "city", "%s.json" % slug),
              "w", encoding="utf-8") as f:
        json.dump(city_doc, f, ensure_ascii=False, separators=(",", ":"))

    return city_doc


# ---------------------------------------------------------------- 主流程

def discover_slugs(indir):
    out = []
    for name in sorted(os.listdir(indir)):
        if name.endswith("_index.csv"):
            out.append(name[:-len("_index.csv")])
    return out


def main():
    ap = argparse.ArgumentParser(description="降水事件 → 网页渲染谱编译器")
    ap.add_argument("--indir", default="openmeteo_out")
    ap.add_argument("--outdir", default="web_out")
    ap.add_argument("--city", default=None, help="只编译某个 slug")
    ap.add_argument("--audio-root", default="sound/", help="前端取素材的根路径")
    ap.add_argument("--calibration", choices=("max", "city", "absolute"), default="max",
                    help="max=分城分位数与绝对刻度取大（默认）")
    ap.add_argument("--rain-floor", type=float, default=RAIN_FLOOR_MM)
    ap.add_argument("--min-hours", type=int, default=MIN_EVENT_HOURS)
    ap.add_argument("--min-peak", type=float, default=MIN_EVENT_PEAK_MM)
    ap.add_argument("--wind-floor", type=float, default=WIND_FLOOR_MS)
    ap.add_argument("--thunder-min", type=float, default=THUNDER_MIN_SCORE)
    ap.add_argument("--ramp", type=int, default=RAMP_SECONDS)
    ap.add_argument("--profile", default=None,
                    help="analyze_assets.py 产出的 assets_profile.json；"
                         "提供后可用区段会写进 manifest，播放器只在区段内取片段")
    ap.add_argument("--levels", default=None,
                    help="素材响度补偿表 json，形如 {\"rain_heavy\": -2.5}，单位 dB")
    ap.add_argument("--seed", default="rain-engine-1")
    ap.add_argument("--slim", action="store_true", help="不写 ui 字段，输出体积约减半")
    args = ap.parse_args()

    class Cfg:
        pass
    cfg = Cfg()
    cfg.indir, cfg.outdir = args.indir, args.outdir
    cfg.calibration = args.calibration
    cfg.rain_floor, cfg.min_hours, cfg.min_peak = args.rain_floor, args.min_hours, args.min_peak
    cfg.wind_floor, cfg.thunder_min = args.wind_floor, args.thunder_min
    cfg.ramp, cfg.seed, cfg.slim = args.ramp, args.seed, args.slim

    assets = [dict(a) for a in ASSETS]
    profile = None
    trims = dict(LEVEL_TRIM_DB)
    if args.profile:
        with open(args.profile, "r", encoding="utf-8") as f:
            profile = json.load(f)
        trims.update({k: v for k, v in profile.get("level_trim_db", {}).items()
                      if not k.startswith("_")})
        pa = profile.get("assets", {})
        missing, noregion = [], []
        for a in assets:
            r = pa.get(a["id"])
            if not r or r.get("missing"):
                missing.append(a["id"])
                continue
            a["duration_s"] = r.get("duration_s")
            if a.get("loop"):
                a["regions"] = r.get("regions") or []
                a["usable_ratio"] = r.get("usable_ratio")
                if not a["regions"]:
                    noregion.append(a["id"])
        if missing:
            print("警告：体检报告里缺这些素材，将按整条可用处理：%s" % ", ".join(missing))
        if noregion:
            print("警告：这些循环层没有可用区段，播放会退回整条取样：%s" % ", ".join(noregion))
    if args.levels:
        with open(args.levels, "r", encoding="utf-8") as f:
            trims.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    cfg.levels = {k: db_to_lin(v) for k, v in trims.items()}
    cfg.levels_db = trims

    if not os.path.isdir(cfg.indir):
        sys.exit("找不到输入目录 %s" % cfg.indir)
    for sub in ("index/city", "index/hour", "scores"):
        os.makedirs(os.path.join(cfg.outdir, sub), exist_ok=True)

    slugs = [args.city] if args.city else discover_slugs(cfg.indir)
    if not slugs:
        sys.exit("%s 里没有 *_index.csv" % cfg.indir)

    print("编译器 %s   输入 %s   城市 %d 座   标定 %s"
          % (ENGINE_VERSION, cfg.indir, len(slugs), cfg.calibration))
    print("下限：液态 %.2f mm/h ・ 每场 ≥%d 可听小时且峰值 ≥%.2f mm/h ・ 风 %.1f m/s ・ 雷 %.2f"
          % (cfg.rain_floor, cfg.min_hours, cfg.min_peak, cfg.wind_floor, cfg.thunder_min))
    print("-" * 78)
    print("%-14s %6s %6s %8s %8s %8s" % ("城市", "事件", "丢弃", "可播小时", "雷小时", "峰值mm/h"))

    roster, hour_pool = [], {h: {} for h in range(24)}
    for slug in slugs:
        doc = compile_city(slug, cfg)
        if doc is None:
            print("%-14s 跳过（缺文件或无数据）" % slug)
            continue
        for h in range(24):
            lst = doc["hour_of_day"][str(h)]
            if lst:
                hour_pool[h][slug] = lst
        peak = max((e["liquid_peak_mm"] for e in doc["events"]), default=0.0)
        th = sum(e["thunder_hours"] for e in doc["events"])
        print("%-14s %6d %6d %8d %8d %8.1f"
              % (doc["name"], doc["events_total"], doc["events_dropped"],
                 doc["playable_hours"], th, peak))
        roster.append({
            "slug": slug, "name": doc["name"], "timezone": doc["timezone"],
            "coordinates": doc["coordinates"],
            "events": doc["events_total"], "playable_hours": doc["playable_hours"],
            "coverage_days": doc["coverage_utc"]["days"],
            "hour_of_day_count": doc["hour_of_day_count"],
            "index": "index/city/%s.json" % slug,
        })

    for h in range(24):
        total = sum(len(v) for v in hour_pool[h].values())
        with open(os.path.join(cfg.outdir, "index", "hour", "%02d.json" % h),
                  "w", encoding="utf-8") as f:
            json.dump({"hour_utc": h, "total": total, "cities": hour_pool[h]},
                      f, ensure_ascii=False, separators=(",", ":"))

    starts = [d["slug"] for d in roster]
    with open(os.path.join(cfg.outdir, "index", "cities.json"), "w", encoding="utf-8") as f:
        json.dump({"engine_version": ENGINE_VERSION, "count": len(roster), "cities": roster},
                  f, ensure_ascii=False, separators=(",", ":"))

    cov_starts, cov_ends = [], []
    for slug in starts:
        p = os.path.join(cfg.outdir, "index", "city", "%s.json" % slug)
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        cov_starts.append(d["coverage_utc"]["start"])
        cov_ends.append(d["coverage_utc"]["end"])

    manifest = {
        "engine_version": ENGINE_VERSION,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audio_root": args.audio_root,
        "assets": assets,
        "playback": {
            "frame_seconds": FRAME_SECONDS,
            "ramp_seconds": cfg.ramp,
            "city_switch_gap_ms": CITY_SWITCH_GAP_MS,
            "clock_alignment": "utc_hour_of_day",
            "note": "1 个数据小时 = 1 小时实时播放；stems 增益为最终绝对值",
        },
        "thresholds": {
            "rain_floor_mm_h": cfg.rain_floor,
            "min_event_audible_hours": cfg.min_hours,
            "min_event_peak_mm_h": cfg.min_peak,
            "wind_floor_ms": cfg.wind_floor,
            "wind_strong_full_ms": WIND_STRONG_FULL_MS,
            "thunder_min_score": cfg.thunder_min,
            "downpour_cap_mm_h": ABS_ANCHORS[-1][0],
        },
        "calibration": {"mode": cfg.calibration,
                        "absolute_anchors_mm_h": ABS_ANCHORS,
                        "city_levels": CITY_LEVELS},
        "level_trim_db": cfg.levels_db,
        "asset_profile": ({"profile_version": profile.get("profile_version"),
                           "generated_at_utc": profile.get("generated_at_utc"),
                           "params": profile.get("params")} if profile else None),
        "coverage_utc": {"start": max(cov_starts) if cov_starts else None,
                         "end": min(cov_ends) if cov_ends else None},
        "cities": len(roster),
        "snow_policy": "降雪不参与发声，只写入 ui 字段",
        "attribution": ("Weather data by Open-Meteo.com (CC BY 4.0), based on ECMWF IFS HRES. "
                        "音频素材来自 Pixabay 与 Freesound，逐条许可见 assets[].source。"),
    }
    with open(os.path.join(cfg.outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    total_ev = sum(d["events"] for d in roster)
    total_hr = sum(d["playable_hours"] for d in roster)
    print("-" * 78)
    print("合计 %d 座城 ・ %d 场雨 ・ %d 可播小时（约 %.0f 天连续音频）"
          % (len(roster), total_ev, total_hr, total_hr / 24.0))
    print("公共覆盖区间 %s … %s" % (manifest["coverage_utc"]["start"],
                                    manifest["coverage_utc"]["end"]))
    print("已写出 %s/" % cfg.outdir)


if __name__ == "__main__":
    main()
