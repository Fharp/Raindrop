#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成假的 openmeteo_out 用于验证编译器 —— 不是交付物。

与上一版的差别：分位数、事件数、最长事件、雷小时数全部照抄你那次真实
运行的控制台统计，用逆 CDF 采样反推逐小时序列。这样跑出来的层级分布、
雷击次数才有参考价值。
"""
import csv, json, math, os, random
from datetime import datetime, timedelta, timezone

OUT = "openmeteo_out"
os.makedirs(OUT, exist_ok=True)

# slug, 名称, lat, lon, tz, 可听h, P50,P85,P97,max, 事件数, 最长h,
# 雷非零h, thP90, thP99, thMax, wcode雷h, 雪
REAL = [
    ("guangzhou","广州",23.1291,113.2644,"Asia/Shanghai",
     627,0.80,3.60,7.99,24.90,185,37,243,0.692,1.000,1.000,184,False),
    ("singapore","新加坡",1.3521,103.8198,"Asia/Singapore",
     962,0.90,3.20,7.50,24.20,343,25,429,0.720,1.000,1.000,133,False),
    ("taipei","台北",25.0330,121.5654,"Asia/Taipei",
     927,0.70,2.60,6.72,27.10,189,90,296,0.232,0.618,1.000,60,False),
    ("london","伦敦",51.5074,-0.1278,"Europe/London",
     516,0.60,1.50,2.66,6.70,167,15,89,0.017,0.268,0.404,6,False),
    ("harbin","哈尔滨",45.8038,126.5349,"Asia/Shanghai",
     184,0.55,1.70,4.01,7.10,70,21,47,0.220,0.475,0.515,16,True),
    ("lhasa","拉萨",29.6520,91.1721,"Asia/Shanghai",
     102,0.50,0.98,1.60,2.80,49,10,9,0.007,0.007,0.007,3,True),
    ("yinchuan","银川",38.4872,106.2309,"Asia/Shanghai",
     58,0.50,1.70,2.53,4.30,21,9,16,0.015,0.057,0.064,0,True),
]

FIELDS = ["time_utc","liquid_mm","showers_frac","snowfall_cm","snow_depth_m",
          "temperature_c","wind_ms","gust_ms","wind_dir_deg","cloud_cover",
          "visibility_m","is_day","soil_moisture","cape","pbl_m","cin_missing",
          "thunder_score","weather_code","audible","rain_field_only",
          "event_id","run_elapsed","run_remaining","gap_to_next","burstiness","character"]

START = datetime(2025,10,8,12,tzinfo=timezone.utc)
N = 6396                                  # 与真实公共区间等长

def inv_cdf(u, pts):
    """pts 为按分位升序的 (q, value)，线性插值。"""
    if u <= pts[0][0]: return pts[0][1]
    for (q0,v0),(q1,v1) in zip(pts, pts[1:]):
        if u <= q1:
            return v0 if q1<=q0 else v0 + (u-q0)/(q1-q0)*(v1-v0)
    return pts[-1][1]

def quant(vals, p):
    if not vals: return 0.0
    s = sorted(vals); pos = (len(s)-1)*p
    lo = int(pos); hi = min(lo+1, len(s)-1)
    return s[lo]*(1-(pos-lo)) + s[hi]*(pos-lo)

for (slug,name,lat,lon,tz,aud_h,p50,p85,p97,pmax,
     n_ev,max_h,th_h,thp90,thp99,thmax,wc_h,snowy) in REAL:
    rng = random.Random(slug)

    # ---- 事件长度：几何分布截到 max_h，总和拉到 aud_h ----
    lens = []
    for _ in range(n_ev):
        L = min(max_h, 1 + int(rng.expovariate(1.0 / max(1.6, aud_h/n_ev - 1))))
        lens.append(max(1, L))
    scale = aud_h / max(1, sum(lens))
    lens = [max(1, min(max_h, int(round(L*scale)))) for L in lens]
    lens[0] = max_h                                   # 保证最长事件出现一次

    # ---- 事件起点：均匀撒在区间内，保证互不相接（至少隔 2 小时）----
    starts, cursor = [], 0
    room = N - sum(lens) - 2*n_ev
    for L in lens:
        cursor += 2 + int(rng.random() * max(1, room//n_ev) * 2)
        if cursor + L >= N: break
        starts.append(cursor); cursor += L
    lens = lens[:len(starts)]

    liq = [0.0]*N
    for st, L in zip(starts, lens):
        # 事件内先按逆 CDF 取值，再排成「涨—峰—落」的弧形
        vals = sorted(inv_cdf(rng.random(), [(0,0.3),(0.5,p50),(0.85,p85),(0.97,p97),(1.0,pmax)])
                      for _ in range(L))
        peak = rng.randint(0, L-1)
        arc = [0.0]*L
        up, down = vals[-1:0:-2][::-1], vals[::2]
        seq = sorted(vals)
        for k in range(L):
            d = abs(k-peak) / max(1, max(peak, L-1-peak))
            arc[k] = seq[max(0, min(L-1, int((1-d)*(L-1))))] * rng.uniform(0.85,1.15)
        for k in range(L):
            liq[st+k] = round(min(pmax, max(0.3, arc[k])), 2)

    # ---- 雷：给液态最大的 th_h 个小时派分数 ----
    audible_idx = [i for i in range(N) if liq[i] >= 0.3]
    audible_idx.sort(key=lambda i: -liq[i])
    thunder = [0.0]*N
    for rank, i in enumerate(audible_idx[:th_h]):
        u = 1.0 - rank/max(1, th_h)
        thunder[i] = round(inv_cdf(u, [(0,0.001),(0.90,thp90),(0.99,thp99),(1.0,thmax)]), 4)
    wc_idx = sorted(range(N), key=lambda i: -thunder[i])[:wc_h]
    wc_set = set(wc_idx) if wc_h else set()

    rows, t = [], START
    for i in range(N):
        L = liq[i]
        snow = 0.0
        temp = round(rng.gauss(14 if not snowy else 4, 9), 1)
        if snowy and temp < -2 and L > 0 and rng.random() < 0.6:
            snow, L = round(L*0.9, 2), 0.0
        wind = abs(rng.gauss(3.6, 2.7))
        cape = rng.uniform(0, 2600) if thunder[i] > 0 else rng.uniform(0, 500)
        if L >= 0.3:      wcode = 95 if i in wc_set else (63 if L >= 2 else 61)
        elif snow >= 0.5: wcode = 73
        else:             wcode = 3
        rows.append({
            "time_utc": t.strftime("%Y-%m-%dT%H:%M"),
            "liquid_mm": round(L,2),
            "showers_frac": round(rng.betavariate(2,2) if L>0 else 0.0, 3),
            "snowfall_cm": snow, "snow_depth_m": 0.0,
            "temperature_c": temp,
            "wind_ms": round(wind,2), "gust_ms": round(wind*rng.uniform(1.1,2.4),2),
            "wind_dir_deg": round(rng.uniform(0,360),1),
            "cloud_cover": round(rng.uniform(40,100) if L>0 else rng.uniform(0,90)),
            "visibility_m": round(rng.uniform(1500,24000)),
            "is_day": 1 if 0 <= t.hour < 12 else 0,
            "soil_moisture": 0.3, "cape": round(cape,1),
            "pbl_m": round(rng.uniform(100,1800)),
            "cin_missing": 1 if rng.random() < 0.2 else 0,
            "thunder_score": thunder[i],
            "weather_code": wcode,
            "audible": 1 if (L>=0.3 or snow>=0.5) else 0,
            "rain_field_only": round(L,2),
            "event_id": -1,"run_elapsed": -1,"run_remaining": -1,
            "gap_to_next": -1,"burstiness": 0.0,"character": 0.0,
        })
        t += timedelta(hours=1)

    with open(os.path.join(OUT, "%s_index.csv"%slug),"w",encoding="utf-8",newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)

    a = [r["liquid_mm"] for r in rows if r["liquid_mm"] >= 0.3]
    meta = {
        "city": {"slug":slug,"name":name,"timezone":tz},
        "requested_coordinates": {"latitude":lat,"longitude":lon},
        "grid_cell": {"latitude":lat,"longitude":lon,"elevation_m":30},
        "model": "ecmwf_ifs",
        "index_range_utc": {"start":rows[0]["time_utc"],"end":rows[-1]["time_utc"]},
        "statistics": {"liquid_p50":quant(a,.5),"liquid_p85":quant(a,.85),
                       "liquid_p97":quant(a,.97),"liquid_max":max(a) if a else 0.0,
                       "audible_hours":len(a)},
    }
    with open(os.path.join(OUT, "%s_meta.json"%slug),"w",encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("%-11s 可听 %4d h（目标 %4d）  P85 %.2f/%.2f  雷 %3d h"
          % (slug, len(a), aud_h, quant(a,.85), p85, sum(1 for x in thunder if x>0)))
