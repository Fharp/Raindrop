#!/usr/bin/env python3
"""把一份城市名单解析成 pythondownload.py 要的城市表。

    python make_cities.py --names cities_seed.txt --out cities.json
    python pythondownload.py --cities-file cities.json

坐标一律来自 Open-Meteo 的地理编码接口（geocoding-api.open-meteo.com，
数据源是 GeoNames，免费、无 key、CC BY 4.0），不手写、不猜。
流程是两步：

    1. /v1/search?name=…&language=en   按名字搜，挑出最像「城市」的那一条，拿 GeoNames id
    2. /v1/get?id=…&language=zh        按 id 精确取中文名

第二步用 id 而不是再搜一次中文名，是因为按名字搜是模糊匹配：
搜 "Cologne" 和搜 "科隆" 未必落在同一个 GeoNames 条目上。

已经在跑的那 57 座城原样保留：slug 是 web_out/scores/<slug>/ 的路径，
改了就等于把已经发布出去的数据全部作废。这个脚本只往后加，绝不改动已有条目。

名单文件格式，一行一座城：

    Mumbai                  # 就按这个名字搜
    Cologne, DE             # 逗号后面跟 ISO 国家码，用来消歧
    Kochi, IN = 科钦         # 等号后面手工指定中文名，不再问接口
    # 井号开头是注释，空行忽略

输出 JSON：[{"slug","name","lat","lon","tz"}]，字段名与 CITIES 完全一致。
"""

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

SEARCH = "https://geocoding-api.open-meteo.com/v1/search"
GET = "https://geocoding-api.open-meteo.com/v1/get"
UA = "rain-archive-cities/1.0 (personal, non-commercial)"

# 只认「有人住的地方」。PPL* 是 GeoNames 的居民点系列，
# PPLC 首都、PPLA 一级行政中心……PPLX 只是城市的一个区，要排掉。
GOOD_FEATURES = ("PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPL", "PPLG")
BAD_FEATURES = ("PPLX", "PPLL", "PPLH", "PPLQ", "PPLW", "PPLF", "PPLR", "PPLS")

# 这是 Cloudflare Pages 免费版每站点 20000 个文件的换算。
# 当前布局是每场雨一个 score 文件，实测每城约 121 个。
FILES_PER_CITY = 121
PAGES_FREE_FILE_LIMIT = 20000

# Open-Meteo 免费额度：每天 10000 次，加权 weight = 地点数 × (天数/14) × (变量数/10)
DAILY_CALL_BUDGET = 10000


def http_json(url, params, timeout=30, retries=4):
    q = url + "?" + urllib.parse.urlencode(params)
    delay = 1.0
    for attempt in range(retries):
        req = urllib.request.Request(q, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 400 是参数错，重试没意义；429/5xx 才退避
            if e.code not in (429,) and e.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("取不到：" + q)


def slugify(s):
    """英文名 → slug。与现有那 57 个的写法保持一致：全小写、只留字母数字。"""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "", s)
    return s.lower()


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def parse_names(path):
    """名单文件 → [(name, country_code|None, zh_override|None)]"""
    out = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            zh = None
            if "=" in line:
                line, zh = line.split("=", 1)
                line, zh = line.strip(), zh.strip() or None
            cc = None
            if "," in line:
                line, cc = line.rsplit(",", 1)
                line, cc = line.strip(), cc.strip().upper() or None
            if line:
                out.append((line, cc, zh))
    return out


def score_candidate(c, want_cc):
    """给搜索结果打分，挑最像「那座城」的一条。人口是主要依据。"""
    fc = c.get("feature_code") or ""
    if fc in BAD_FEATURES:
        return -1
    if not fc.startswith("PPL"):
        return -1
    if want_cc and (c.get("country_code") or "").upper() != want_cc:
        return -1
    pop = c.get("population") or 0
    s = math.log10(pop + 10)
    if fc == "PPLC":
        s += 2.0          # 首都优先
    elif fc.startswith("PPLA"):
        s += 1.0          # 行政中心其次
    return s


def resolve(name, cc, zh_override, pause):
    """一个名字 → 一条完整记录，解析不出来返回 None。"""
    body = http_json(SEARCH, {"name": name, "count": 10, "language": "en", "format": "json"})
    time.sleep(pause)
    results = body.get("results") or []
    if not results:
        return None, "搜不到"

    best, best_s = None, -1
    for c in results:
        s = score_candidate(c, cc)
        if s > best_s:
            best, best_s = c, s
    if best is None or best_s < 0:
        return None, "有结果但没有一条像城市" + ("（国家码 %s 也对不上）" % cc if cc else "")

    zh = zh_override
    if not zh:
        try:
            got = http_json(GET, {"id": best["id"], "language": "zh"})
            time.sleep(pause)
            # /v1/get 有时直接返回对象，有时包在 results 里
            row = got if "name" in got else (got.get("results") or [{}])[0]
            zh = row.get("name") or ""
        except Exception:
            zh = ""
        # 中文名没拿到就退回英文名——宁可显示 Mumbai，也不要空着
        if not zh or zh == best.get("name"):
            zh = zh or best.get("name") or name

    return {
        "slug": slugify(best.get("name") or name),
        "name": zh,
        "lat": round(float(best["latitude"]), 4),
        "lon": round(float(best["longitude"]), 4),
        "tz": best.get("timezone") or "UTC",
        "_en": best.get("name"),
        "_id": best.get("id"),
        "_pop": best.get("population") or 0,
        "_cc": best.get("country_code"),
        "_fc": best.get("feature_code"),
    }, None


def load_existing(path):
    """已有的城市表。优先读 JSON，读不到就从 pythondownload.py 里把 CITIES 抠出来。"""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    src = "pythondownload.py"
    if not os.path.exists(src):
        return []
    text = open(src, encoding="utf-8").read()
    m = re.search(r"^CITIES\s*=\s*(\[.*?^\])", text, re.S | re.M)
    if not m:
        return []
    ns = {}
    exec(compile("CITIES = " + m.group(1), "<CITIES>", "exec"), ns)
    return ns["CITIES"]


def main():
    ap = argparse.ArgumentParser(description="把城市名单解析成 pythondownload.py 的城市表")
    ap.add_argument("--names", required=True, help="名单文件，一行一座城")
    ap.add_argument("--out", default="cities.json")
    ap.add_argument("--existing", default=None,
                    help="已有城市表 JSON；不填则从 pythondownload.py 的 CITIES 读")
    ap.add_argument("--min-km", type=float, default=25.0,
                    help="两座城近于这个距离就跳过后来的。rain_place 按最近城市选片，"
                         "挨得太近会让「你在哪」变得没有意义")
    ap.add_argument("--min-pop", type=int, default=0, help="人口低于此值不收")
    ap.add_argument("--pause", type=float, default=0.35, help="每次请求之间歇多久（秒）")
    ap.add_argument("--limit", type=int, default=0, help="最多解析多少条，0 为不限")
    args = ap.parse_args()

    have = load_existing(args.existing)
    print("已有 %d 座城，原样保留（slug 是已发布的路径，不能动）" % len(have))

    by_slug = {c["slug"]: c for c in have}
    coords = [(c["lat"], c["lon"], c["slug"]) for c in have]
    out = list(have)

    wanted = parse_names(args.names)
    if args.limit:
        wanted = wanted[:args.limit]
    print("名单 %d 条，开始解析\n" % len(wanted))

    added, skipped = 0, []
    for i, (name, cc, zh) in enumerate(wanted, 1):
        label = name + (", " + cc if cc else "")
        try:
            rec, why = resolve(name, cc, zh, args.pause)
        except Exception as e:
            skipped.append((label, "请求失败：%s" % e))
            print("  %-28s ✗ 请求失败 %s" % (label, e))
            continue
        if rec is None:
            skipped.append((label, why))
            print("  %-28s ✗ %s" % (label, why))
            continue

        if rec["_pop"] < args.min_pop:
            skipped.append((label, "人口 %d 低于下限" % rec["_pop"]))
            print("  %-28s – 人口 %d，跳过" % (label, rec["_pop"]))
            continue

        near = None
        for la, lo, sl in coords:
            d = haversine_km(rec["lat"], rec["lon"], la, lo)
            if d < args.min_km:
                near = (sl, d)
                break
        if near:
            skipped.append((label, "距 %s 仅 %.0f km" % near))
            print("  %-28s – 距 %s 仅 %.0f km，跳过" % (label, near[0], near[1]))
            continue

        slug = rec["slug"] or slugify(name)
        if slug in by_slug:
            # slug 撞车：加国家码，再撞就加序号
            alt = slug + (rec["_cc"] or "").lower()
            n = 2
            while alt in by_slug:
                alt = "%s%d" % (slug, n)
                n += 1
            print("  %-28s ! slug %s 已被占用，改用 %s" % (label, slug, alt))
            slug = alt
        rec["slug"] = slug

        clean = {k: rec[k] for k in ("slug", "name", "lat", "lon", "tz")}
        out.append(clean)
        by_slug[slug] = clean
        coords.append((rec["lat"], rec["lon"], slug))
        added += 1
        print("  %-28s ✓ %-10s %-8s %8.4f,%9.4f  %s  pop %s"
              % (label, rec["name"], slug, rec["lat"], rec["lon"], rec["tz"], rec["_pop"]))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    n = len(out)
    print("\n" + "=" * 68)
    print("写出 %s：共 %d 座城（新增 %d，跳过 %d）" % (args.out, n, added, len(skipped)))
    files = n * FILES_PER_CITY
    print("预计部署文件数 ≈ %d（每城约 %d 个 score）" % (files, FILES_PER_CITY))
    if files > PAGES_FREE_FILE_LIMIT:
        over = n - PAGES_FREE_FILE_LIMIT // FILES_PER_CITY
        print("  ⚠ 超过 Cloudflare Pages 免费版 20000 个文件的上限，超出约 %d 座城。" % over)
        print("    要么砍到 %d 座以内，要么把每城的 score 合并成若干个包"
              % (PAGES_FREE_FILE_LIMIT // FILES_PER_CITY))
        print("    （需要同时改 rain_engine.py 的产出与 rain_audio.js 的 loadEvent）。")
    calls = n * 39
    print("Open-Meteo 预计消耗 ≈ %d 次加权调用（每城约 39 次）" % calls)
    if calls > DAILY_CALL_BUDGET:
        print("  ⚠ 超过免费版每天 10000 次，需要分 %d 天跑。"
              % math.ceil(calls / DAILY_CALL_BUDGET))
        print("    pythondownload.py 加了 --skip-existing，中断后重跑会接着走。")
    if skipped:
        print("\n没收进来的 %d 条：" % len(skipped))
        for label, why in skipped:
            print("  %-28s %s" % (label, why))


if __name__ == "__main__":
    main()
