#!/usr/bin/env python3
"""生成 pythondownload.py 要的城市表。

两种来源，二选一：

    # 从 GeoNames 的数据里筛（推荐，要什么层级筛什么层级，不靠手打）
    python make_cities.py --geonames cities5000.txt --country CN,HK,MO,TW --out cities.json

    # 从一份手写名单解析
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

GeoNames 那一路要先下一份转储（CC BY 4.0，免费）：

    https://download.geonames.org/export/dump/cities5000.zip     人口 ≥5000，全球，约 10 MB
    https://download.geonames.org/export/dump/cities15000.zip    人口 ≥15000，小一些
    https://download.geonames.org/export/dump/CN.zip             中国全部地名，最全也最大

解压出 .txt 直接喂给 --geonames。feature_code 决定筛到哪一级：

    PPLC   国家首都
    PPLA   一级行政区驻地 —— 省会、自治区首府、直辖市
    PPLA2  二级行政区驻地 —— 地级市，自治州、地区、盟的驻地也在这里
    PPLA3  三级行政区驻地 —— 县级市、县城

默认筛 PPLC,PPLA,PPLA2，对中国来说就是全部地级行政区（约 330 个）。
想连县级一起要就加上 PPLA3，会多出几千个。

中文名按 GeoNames id 精确取（/v1/get?id=…&language=zh），
不用转储里那个没有语种标记的 alternatenames 字段——
那里面中日韩混在一起，分不出来。

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

# Cloudflare Pages 免费版每站点 20000 个文件（单文件 25 MiB，没有总体积上限）。
# 下面两个数是跑过 pack_scores.py 之后实测的：57 座城打包后 web_out 是 234 个文件，
# 其中 57 个城市索引 + 151 个谱包，每城边际 3.65 个。
# 固定开销 ≈ sound 50 + 根目录 32 + 钟点索引 24 + bundle 3 + 名册清单 2 + 字体 2。
# 注意这个数随取数的日期跨度线性涨：跨度翻倍，谱包数大致翻倍，能装的城数减半。
FILES_PER_CITY = 3.65
FIXED_FILES = 120
MB_PER_CITY = 0.42
PAGES_FREE_FILE_LIMIT = 20000
CALLS_PER_CITY = 39          # 274 天 / 30 天一块 / 18 个变量，见 pythondownload 的账本

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


# GeoNames 转储的列序，见 https://download.geonames.org/export/dump/readme.txt
GN_ID, GN_NAME, GN_ASCII, GN_ALT = 0, 1, 2, 3
GN_LAT, GN_LON = 4, 5
GN_FCLASS, GN_FCODE, GN_CC = 6, 7, 8
GN_POP, GN_TZ = 14, 17


def parse_geonames(path, countries, features, min_pop):
    """转储 → [(记录, 人口)]，按人口从多到少。

    只收 feature class P（居民点）。同一座城在转储里可能有多条
    （市辖区、旧名等），按 geonameid 去重之后再交给下游的距离过滤。"""
    rows, dropped = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 18 or c[GN_FCLASS] != "P":
                continue
            if countries and c[GN_CC].upper() not in countries:
                continue
            if features and c[GN_FCODE] not in features:
                continue
            try:
                pop = int(c[GN_POP] or 0)
                lat, lon = float(c[GN_LAT]), float(c[GN_LON])
            except ValueError:
                continue
            if pop < min_pop:
                dropped.append((c[GN_ASCII] or c[GN_NAME], c[GN_CC].upper(), c[GN_FCODE], pop))
                continue
            rows.append({
                "id": int(c[GN_ID]),
                "en": c[GN_ASCII] or c[GN_NAME],
                "lat": round(lat, 4), "lon": round(lon, 4),
                "tz": c[GN_TZ] or "UTC",
                "cc": c[GN_CC].upper(), "fc": c[GN_FCODE], "pop": pop,
            })
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: -x["pop"]):
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append(r)
    dropped.sort(key=lambda x: -x[3])
    return out, dropped


def zh_name(gid, fallback, pause):
    """按 GeoNames id 取中文名。取不到就退回英文名——宁可显示 Hohhot，也不要空着。"""
    try:
        got = http_json(GET, {"id": gid, "language": "zh"})
        time.sleep(pause)
        row = got if "name" in got else (got.get("results") or [{}])[0]
        return row.get("name") or fallback
    except Exception:
        return fallback


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
    ap.add_argument("--names", help="手写名单文件，一行一座城")
    ap.add_argument("--geonames", help="GeoNames 转储 .txt，例如 cities5000.txt")
    ap.add_argument("--country", default="CN,HK,MO,TW",
                    help="只收这些国家/地区码（逗号分隔），空串表示不限。仅 --geonames 有效")
    ap.add_argument("--feature", default="PPLC,PPLA,PPLA2",
                    help="只收这些 feature_code。默认是全部地级行政区驻地；"
                         "加 PPLA3 会连县级一起收")
    ap.add_argument("--out", default="cities.json")
    ap.add_argument("--existing", default=None,
                    help="已有城市表 JSON；不填则从 pythondownload.py 的 CITIES 读")
    ap.add_argument("--near-km", type=float, default=30.0,
                    help="相距小于这个距离的两座城**不会**被丢掉，只写进复核清单让你自己看。"
                         "中国的地级市本来就有挨得近的（佛山离广州约 20 km），"
                         "拿距离当同城判据会把真城市误杀")
    ap.add_argument("--exclude", default=None,
                    help="排除清单，一行一条：slug、英文名或中文名都行，# 开头是注释。"
                         "市辖区、县、以及被误收的重复项写进去，重跑就不会再进来")
    ap.add_argument("--report", default=None,
                    help="把所有「非重复原因」被跳过的条目写到这个文件，默认是 --out 同名的 .skipped.txt")
    ap.add_argument("--same-place-km", type=float, default=12.0,
                    help="中文名和 slug 都对不上时的兜底同城判据。"
                         "呼和浩特/Hohhot 这种两边写法不同、坐标只差几公里的靠它拦")
    ap.add_argument("--min-km-unused", type=float, default=0.0,
                    help=argparse.SUPPRESS)
    ap.add_argument("--min-pop", type=int, default=0, help="人口低于此值不收")
    ap.add_argument("--same-km", type=float, default=100.0,
                    help="slug 撞车时，相距在这个距离以内就判定是同一座城、跳过；"
                         "超过才认为是真重名并改名")
    ap.add_argument("--pause", type=float, default=0.35, help="每次请求之间歇多久（秒）")
    ap.add_argument("--limit", type=int, default=0, help="最多解析多少条，0 为不限")
    args = ap.parse_args()
    if not args.names and not args.geonames:
        ap.error("--names 和 --geonames 至少要给一个")

    have = load_existing(args.existing)
    print("已有 %d 座城，原样保留（slug 是已发布的路径，不能动）" % len(have))

    by_slug = {c["slug"]: c for c in have}
    coords = [(c["lat"], c["lon"], c["slug"]) for c in have]
    out = list(have)

    thin = []
    if args.geonames:
        countries = {x.strip().upper() for x in args.country.split(",") if x.strip()}
        features = {x.strip().upper() for x in args.feature.split(",") if x.strip()}
        rows, thin = parse_geonames(args.geonames, countries, features, args.min_pop)
        print("从 %s 筛出 %d 条（国家 %s，层级 %s）"
              % (args.geonames, len(rows),
                 ",".join(sorted(countries)) or "不限", ",".join(sorted(features))))
        wanted = [(r["en"], r["cc"], None, r) for r in rows]
    else:
        wanted = [(n, cc, zh, None) for n, cc, zh in parse_names(args.names)]
    if args.limit:
        wanted = wanted[:args.limit]
    print("待处理 %d 条，开始解析\n" % len(wanted))

    drop = set()
    if args.exclude and os.path.exists(args.exclude):
        with open(args.exclude, encoding="utf-8") as f:
            for raw in f:
                x = raw.split("#", 1)[0].strip()
                if x:
                    drop.add(x.lower())
        print("排除清单 %s：%d 条" % (args.exclude, len(drop)))

    by_name = {c["name"]: c for c in have if c.get("name")}
    added, skipped, merged, nearby = 0, [], [], []
    for en, cc, fc, pop in thin:
        skipped.append(("%s, %s" % (en, cc),
                        "人口 %d 低于 --min-pop %d（%s）" % (pop, args.min_pop, fc)))
    for i, (name, cc, zh, gn) in enumerate(wanted, 1):
        label = name + (", " + cc if cc else "")
        if gn:
            # 转储里坐标、时区、层级全都有，只差一个中文名
            rec = {"slug": slugify(gn["en"]), "name": zh or zh_name(gn["id"], gn["en"], args.pause),
                   "lat": gn["lat"], "lon": gn["lon"], "tz": gn["tz"],
                   "_en": gn["en"], "_id": gn["id"], "_pop": gn["pop"],
                   "_cc": gn["cc"], "_fc": gn["fc"]}
        else:
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

        if drop & {label.lower(), (rec.get("name") or "").lower(),
                   (rec.get("slug") or "").lower(),
                   label.split(",")[0].strip().lower()}:
            merged.append((label, "在排除清单里"))
            print("  %-28s – 在排除清单里，跳过" % label)
            continue

        # 同城判定三道，任一命中就认为已经有了。距离**不再**单独作为判据——
        # 佛山离广州约 20 km、中山离珠海约 30 km，都是两座独立的地级市。
        dup = None
        if rec["name"] and rec["name"] in by_name:
            # 中文名完全相同：最可靠的一道。呼和浩特 / Hohhot 这种
            # slug 对不上、坐标也差着的，只有它拦得住
            dup = (by_name[rec["name"]]["slug"], "中文名相同")
        if not dup:
            for la, lo, sl in coords:
                d = haversine_km(rec["lat"], rec["lon"], la, lo)
                if d <= args.same_place_km:
                    dup = (sl, "相距仅 %.0f km，判定同城" % d)
                    break
        if dup:
            merged.append((label, "已有 %s：%s" % dup))
            print("  %-28s – 已有 %s（%s），跳过" % (label, dup[0], dup[1]))
            continue

        # 挨得近但不算同城的，留着，只记一笔供人工复核
        for la, lo, sl in coords:
            d = haversine_km(rec["lat"], rec["lon"], la, lo)
            if d < args.near_km:
                nearby.append((label, rec.get("name", ""), rec.get("_fc", ""),
                               rec.get("_pop", 0), sl, d))
                break

        slug = rec["slug"] or slugify(name)
        if slug in by_slug:
            # slug 撞车，先看是不是同一座城。
            # GeoNames 给的坐标常与名册里的差几十公里（取的点不一样），
            # 光靠 --min-km 拦不住：天津就差了 27 km，会被当成新城改名成
            # tianjincn 收进来，同一座城进两次，选单里出现两个天津。
            # 撞车 + 距离在 --same-km 以内 = 同一座，跳过；
            # 撞车但相隔很远 = 真的重名（英美两个 Cambridge），才改名。
            o = by_slug[slug]
            d = haversine_km(rec["lat"], rec["lon"], o["lat"], o["lon"])
            if d <= args.same_km:
                merged.append((label, "已有 %s：同名，相距 %.0f km" % (slug, d)))
                print("  %-28s – 与已有的 %s 同名同地（%.0f km），跳过" % (label, slug, d))
                continue
            alt = slug + (rec["_cc"] or "").lower()
            n = 2
            while alt in by_slug:
                alt = "%s%d" % (slug, n)
                n += 1
            print("  %-28s ! 与 %s 重名但相隔 %.0f km，改用 %s" % (label, slug, d, alt))
            slug = alt
        rec["slug"] = slug

        clean = {k: rec[k] for k in ("slug", "name", "lat", "lon", "tz")}
        out.append(clean)
        by_slug[slug] = clean
        by_name[clean["name"]] = clean
        coords.append((rec["lat"], rec["lon"], slug))
        added += 1
        print("  %-28s ✓ %-10s %-8s %8.4f,%9.4f  %s  pop %s"
              % (label, rec["name"], slug, rec["lat"], rec["lon"], rec["tz"], rec["_pop"]))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    n = len(out)
    room = int((PAGES_FREE_FILE_LIMIT - FIXED_FILES) / FILES_PER_CITY)
    print("\n" + "=" * 68)
    print("写出 %s：共 %d 座城（新增 %d，判定重复 %d，其他原因跳过 %d）"
          % (args.out, n, added, len(merged), len(skipped)))

    files = FIXED_FILES + n * FILES_PER_CITY
    print("部署文件数 ≈ %d / 20000（打包后每城 %.2f 个，固定开销 %d）"
          % (files, FILES_PER_CITY, FIXED_FILES))
    if files > PAGES_FREE_FILE_LIMIT:
        print("  ⚠ 超过 Cloudflare Pages 免费版上限，砍到 %d 座以内。" % room)
    else:
        print("  还能再加约 %d 座（前提是跑过 pack_scores.py；不打包的话每城要 121 个文件）"
              % (room - n))
    print("web_out 体积 ≈ %.0f MB（Pages 对总体积没有限制，单文件上限 25 MiB，谱包远低于它）"
          % (n * MB_PER_CITY))

    calls = n * CALLS_PER_CITY
    days = math.ceil(calls / DAILY_CALL_BUDGET)
    print("Open-Meteo ≈ %d 次加权调用（每城约 %d）" % (calls, CALLS_PER_CITY))
    if days > 1:
        print("  超过免费版每天 10000 次，分 %d 天跑：" % days)
        print("    python pythondownload.py --cities-file %s --skip-existing --max-calls 9000"
              % args.out)
        print("  每天跑一次这条，--skip-existing 会跳过已经下好的城，接着往下走。")
    print("取数耗时 ≈ %.0f 分钟（每城约 12 个请求，--sleep 1.0）" % (n * 12 / 60.0))
    print("拉完记得：rain_engine.py 重新编 → pack_scores.py 合并文件。")
    rep = args.report or (os.path.splitext(args.out)[0] + ".skipped.txt")
    lines = []
    if skipped:
        lines.append("# 非重复原因被跳过的条目——需要你自己看一眼，决定要不要手工补进 %s" % args.out)
        lines.append("# 补的办法：把名字写进一个文本文件，用 --names 那条路子跑")
        for label, why in skipped:
            lines.append("%-32s %s" % (label, why))
    if nearby:
        lines.append("")
        lines.append("# 以下这些**已经收进来了**，只是离邻居较近（< %.0f km），列出来供复核。" % args.near_km)
        lines.append("# 如果确认其中某两条其实是同一座城，从 %s 里手工删掉一条即可。" % args.out)
        lines.append("# 列的含义：英文名 / 中文名 / GeoNames 层级 / 人口 / 离谁多远")
        lines.append("# PPLA=省级驻地 PPLA2=地级驻地 PPLA3=县级驻地 PPLX=市辖区")
        for label, zh, fc, pop, sl, d in nearby:
            lines.append("%-24s %-10s %-6s %-10d 距 %s %.0f km" % (label, zh, fc, pop, sl, d))
    if lines:
        with open(rep, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n复核清单写到 %s：非重复跳过 %d 条，近邻提醒 %d 条"
              % (rep, len(skipped), len(nearby)))
        for label, why in skipped[:12]:
            print("  %-30s %s" % (label, why))
        if len(skipped) > 12:
            print("  …… 其余 %d 条见文件" % (len(skipped) - 12))
    else:
        print("\n没有需要人工复核的条目")


if __name__ == "__main__":
    main()
