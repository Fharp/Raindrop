#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_eq_profile.py — 生成 eq_profile.json

为什么要 EQ，而不是只调音量：
雨的强弱在听感上主要是**频谱**的事，不是电平的事。
毛毛雨是离散的水滴撞击，能量集中在 2–8 kHz；暴雨是整片水，
100–600 Hz 有一层"轰"，反而把高频盖掉一部分。
库里只有四条雨，光靠交叉淡入淡出，中间地带全是同一条素材在不同音量下重复。
把频谱随 intensity 倾斜，同一条素材能撑开成一大片听感，
这是手头素材条件下性价比最高的一步。

曲线之外还算一件事：**响度补偿**。
低架抬 3.5 dB 会让暴雨比引擎设定的更响，等于偷偷改了引擎的强度标定。
所以这里把每档 intensity 下整条 EQ 的宽带增益算出来，反向补回去，
让 EQ 只改音色、不改响度。加权谱优先用你自己的素材实测，
没有素材就退回雨声的经验谱型。

    python make_eq_profile.py                    # 用经验谱型
    python make_eq_profile.py --sound sound      # 用实测谱，更准

产出 eq_profile.json，rain_eq.js 直接读它。
"""

import argparse
import json
import math
import os
import subprocess
import sys

PROFILE_VERSION = "eq/1"
STEPS = 21                       # intensity 采样点数，0.00 … 1.00


# ---------------------------------------------------------------- 曲线定义
# 每一项：(类型, 频率, Q, intensity=0 时的增益, intensity=1 时的增益)
# 类型 lowpass/highpass 的两个增益位改为「频率」，见 build() 里的处理。

RAIN_BANDS = [
    # 高通。轻雨剥掉低频，听起来薄、远；大雨放行，让"轰"出来。
    {"type": "highpass",  "q": 0.707, "f_at0": 150.0, "f_at1": 30.0, "gamma": 0.6},
    # 低架：雨越大越有身量
    {"type": "lowshelf",  "f": 180.0, "q": 0.707, "g0": -5.0,  "g1": 3.5},
    # 300–500 Hz 的箱声。几乎所有雨声录音都有，录音机总是贴着某个面。
    {"type": "peaking",   "f": 400.0, "q": 1.0,   "g0": -4.5,  "g1": -2.0},
    # 2–4 kHz 是"滴答"的位置。轻雨要这里亮，大雨要收，否则听着像白噪声。
    {"type": "peaking",   "f": 3200.0, "q": 0.8,  "g0": 3.0,   "g1": -0.5},
    # 9 kHz 以上是嘶声。这东西要连开几小时，高频过量最先让人累。
    {"type": "highshelf", "f": 9000.0, "q": 0.707, "g0": -1.5, "g1": -4.0},
]

# character 轴：0 层状连绵 → 1 阵性。阵雨的水花更碎更亮。
CHARACTER_BAND = {"type": "peaking", "f": 5000.0, "q": 1.0, "g0": 0.0, "g1": 1.5}

# 风：录音底噪的低频很重，先切掉；1.6 kHz 是风噪最刺耳的地方。
WIND_BANDS = [
    {"type": "highpass", "q": 0.707, "f_at0": 55.0, "f_at1": 55.0, "gamma": 1.0},
    {"type": "peaking",  "f": 1600.0, "q": 1.0, "g0": -3.0, "g1": -3.0},
]

# 室内感：用户侧滑杆，与数据无关。隔着窗听雨——玻璃过低频、挡高频。
INDOOR = {
    "lowpass_f": [20000.0, 500.0],     # m=0 → m=1
    "lowpass_gamma": 0.65,
    "lowshelf_f": 220.0,
    "lowshelf_g": [0.0, 4.0],
    "makeup_db": [0.0, 4.5],
}

# 雷：远近本质是低通。空气对高频的吸收随距离急剧上升，
# 所以十条"干雷"（近、脆）经低通也能当远雷用，等于把素材库撑大。
THUNDER = {
    "lowpass_f": [600.0, 8000.0],      # gain 0 → 1
    "lowshelf_f": 90.0,
    "lowshelf_g": [3.0, 0.0],
    "duck_db": [0.0, -3.0],            # 雷响时把雨压一下，雷才有分量
    "duck_attack_s": 0.04,
    "duck_release_s": 0.6,
}


# ---------------------------------------------------------------- 双二阶响应

def coeffs(kind, fs, f0, q, gain_db=0.0):
    A = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * f0 / fs
    cw, sw = math.cos(w), math.sin(w)
    al = sw / (2 * q)
    if kind == "peaking":
        b = [1 + al * A, -2 * cw, 1 - al * A]
        a = [1 + al / A, -2 * cw, 1 - al / A]
    elif kind == "lowshelf":
        s = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw),
             A * ((A + 1) - (A - 1) * cw - s)]
        a = [(A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw),
             (A + 1) + (A - 1) * cw - s]
    elif kind == "highshelf":
        s = 2 * math.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw),
             A * ((A + 1) + (A - 1) * cw - s)]
        a = [(A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw),
             (A + 1) - (A - 1) * cw - s]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    elif kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    else:
        raise ValueError(kind)
    return [x / a[0] for x in b], [1.0] + [x / a[0] for x in a[1:]]


def mag_db(b, a, f, fs):
    w = 2 * math.pi * f / fs
    cw1, sw1 = math.cos(-w), math.sin(-w)
    cw2, sw2 = math.cos(-2 * w), math.sin(-2 * w)
    nr = b[0] + b[1] * cw1 + b[2] * cw2
    ni = b[1] * sw1 + b[2] * sw2
    dr = a[0] + a[1] * cw1 + a[2] * cw2
    di = a[1] * sw1 + a[2] * sw2
    n = math.hypot(nr, ni)
    d = math.hypot(dr, di) or 1e-12
    return 20 * math.log10(max(n / d, 1e-12))


# ---------------------------------------------------------------- 参考谱

def model_spectrum(freqs):
    """雨声的经验谱型：到 1.5 kHz 大致平，之后约 -4 dB/oct 滚降，
    低端 100 Hz 以下也走低。用于加权计算宽带增益。"""
    out = []
    for f in freqs:
        if f < 100:
            db = -12 * math.log2(100.0 / max(f, 20.0))
        elif f <= 1500:
            db = 0.0
        else:
            db = -4.0 * math.log2(f / 1500.0)
        out.append(db)
    return out


def measured_spectrum(sound_dir, freqs):
    """用 ffmpeg 把四条雨各取 30 秒，算 1/3 倍频程能量，取平均。
    没有 ffmpeg 或没有素材就返回 None。"""
    files = []
    rain = os.path.join(sound_dir, "Rain")
    if not os.path.isdir(rain):
        return None
    for n in sorted(os.listdir(rain)):
        if n.lower().endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a")):
            files.append(os.path.join(rain, n))
    if not files:
        return None
    try:
        import numpy as np
    except ImportError:
        print("  没装 numpy，改用经验谱型", file=sys.stderr)
        return None

    acc = None
    for p in files:
        try:
            raw = subprocess.run(
                ["ffmpeg", "-v", "quiet", "-t", "30", "-i", p,
                 "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                capture_output=True).stdout
        except FileNotFoundError:
            print("  没有 ffmpeg，改用经验谱型", file=sys.stderr)
            return None
        x = np.frombuffer(raw, dtype="<f4")
        if x.size < 48000:
            continue
        n = 1 << 15
        segs = x[:x.size // n * n].reshape(-1, n) * np.hanning(n)
        psd = (np.abs(np.fft.rfft(segs, axis=1)) ** 2).mean(axis=0)
        fr = np.fft.rfftfreq(n, 1 / 48000.0)
        vals = []
        for f in freqs:
            lo, hi = f / 2 ** (1 / 6), f * 2 ** (1 / 6)
            sel = psd[(fr >= lo) & (fr < hi)]
            vals.append(10 * math.log10(max(float(sel.mean()) if sel.size else 1e-20, 1e-20)))
        v = np.array(vals)
        v -= v.max()
        acc = v if acc is None else acc + v
    if acc is None:
        return None
    return list(acc / len(files))


# ---------------------------------------------------------------- 组装

def third_octave(f_lo=25.0, f_hi=18000.0):
    out, f = [], f_lo
    while f <= f_hi:
        out.append(f)
        f *= 2 ** (1 / 3)
    return out


def lerp(a, b, t):
    return a + (b - a) * t


def rain_bands_at(i, c=0.0):
    """给定 intensity（与可选 character），返回该点的滤波器参数表。"""
    out = []
    for bd in RAIN_BANDS:
        if bd["type"] == "highpass":
            f = lerp(bd["f_at0"], bd["f_at1"], i ** bd["gamma"])
            out.append({"type": "highpass", "f": round(f, 1), "q": bd["q"], "gain": 0.0})
        else:
            out.append({"type": bd["type"], "f": bd["f"], "q": bd["q"],
                        "gain": round(lerp(bd["g0"], bd["g1"], i), 2)})
    cb = CHARACTER_BAND
    out.append({"type": cb["type"], "f": cb["f"], "q": cb["q"],
                "gain": round(lerp(cb["g0"], cb["g1"], c), 2)})
    return out


def k_weight_db(f, fs=48000.0):
    """ITU-R BS.1770 的 K 加权幅频响应。
    用它而不是裸能量积分：人耳对 150 Hz 以下的雨声几乎不计入响度，
    裸能量会把「切掉低频」误判成掉了 10 dB，补偿就会补过头。"""
    b1, a1 = coeffs("highshelf", fs, 1681.974450955533, 0.7071752369554196,
                    3.999843853973347)
    b2, a2 = coeffs("highpass", fs, 38.13547087602444, 0.5003270373238773)
    return mag_db(b1, a1, f, fs) + mag_db(b2, a2, f, fs)


def broadband_db(bands, freqs, ref_db, fs=48000.0):
    """整条链在「参考谱 × K 加权」下的宽带增益。"""
    num = den = 0.0
    for f, rdb in zip(freqs, ref_db):
        g = 0.0
        for bd in bands:
            b, a = coeffs(bd["type"], fs, bd["f"], bd["q"], bd["gain"])
            g += mag_db(b, a, f, fs)
        w = 10 ** ((rdb + k_weight_db(f, fs)) / 10.0)
        num += w * 10 ** (g / 10.0)
        den += w
    return 10 * math.log10(max(num / den, 1e-12))


def main():
    ap = argparse.ArgumentParser(description="生成雨声 EQ 曲线表")
    ap.add_argument("--sound", default=None, help="给出素材目录则用实测谱加权")
    ap.add_argument("--out", default="eq_profile.json")
    args = ap.parse_args()

    freqs = third_octave()
    ref = measured_spectrum(args.sound, freqs) if args.sound else None
    basis = "实测" if ref else "经验谱型"
    if ref is None:
        ref = model_spectrum(freqs)

    print("EQ 曲线表   加权谱：%s   采样 %d 档 intensity" % (basis, STEPS))
    print("-" * 78)
    print("%6s %9s %9s %9s %9s %9s %8s"
          % ("i", "HP Hz", "低架", "400Hz", "3.2k", "9k架", "补偿"))

    table = []
    for k in range(STEPS):
        i = k / (STEPS - 1)
        bands = rain_bands_at(i, 0.0)
        comp = -broadband_db(bands, freqs, ref)
        table.append({"i": round(i, 3),
                      "bands": bands[:-1],          # character 段单独给
                      "makeup_db": round(comp, 2)})
        print("%6.2f %9.0f %+9.2f %+9.2f %+9.2f %+9.2f %+8.2f"
              % (i, bands[0]["f"], bands[1]["gain"], bands[2]["gain"],
                 bands[3]["gain"], bands[4]["gain"], comp))

    # character 段自己的补偿
    char_comp = []
    for k in range(3):
        c = k / 2.0
        cb = CHARACTER_BAND
        band = [{"type": cb["type"], "f": cb["f"], "q": cb["q"],
                 "gain": round(lerp(cb["g0"], cb["g1"], c), 2)}]
        char_comp.append({"c": c, "band": band[0],
                          "makeup_db": round(-broadband_db(band, freqs, ref), 2)})

    doc = {
        "profile_version": PROFILE_VERSION,
        "weighting_basis": basis + " + BS.1770 K 加权",
        "reference_spectrum": [{"hz": round(f, 1), "db": round(d, 2)}
                               for f, d in zip(freqs, ref)],
        "rain": {"table": table, "character": char_comp},
        "wind": {"bands": [
            {"type": "highpass", "f": WIND_BANDS[0]["f_at0"], "q": WIND_BANDS[0]["q"], "gain": 0.0},
            {"type": "peaking", "f": WIND_BANDS[1]["f"], "q": WIND_BANDS[1]["q"],
             "gain": WIND_BANDS[1]["g0"]},
        ]},
        "indoor": INDOOR,
        "thunder": THUNDER,
        "note": ("makeup_db 已经把整条 EQ 的宽带增益抵消掉，"
                 "所以 EQ 只改音色不改响度，引擎的强度标定不受影响。"),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("-" * 78)
    print("补偿范围 %+.2f … %+.2f dB" % (min(t["makeup_db"] for t in table),
                                          max(t["makeup_db"] for t in table)))
    print("已写出 %s" % args.out)


if __name__ == "__main__":
    main()
