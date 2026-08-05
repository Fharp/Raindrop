import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

API_HOST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
DEFAULT_MODEL = "ecmwf_ifs"          # IFS HRES 9 km，不是 ecmwf_ifs025 / ecmwf_ifs04
DEFAULT_START = "2025-10-01"
DEFAULT_END = "2026-07-01"

CITIES = [
    {"slug": "beijing", "name": "北京", "lat": 39.9042, "lon": 116.4074, "tz": "Asia/Shanghai"},
    {"slug": "shanghai", "name": "上海", "lat": 31.2304, "lon": 121.4737, "tz": "Asia/Shanghai"},
    {"slug": "tianjin", "name": "天津", "lat": 39.3434, "lon": 117.3616, "tz": "Asia/Shanghai"},
    {"slug": "chongqing", "name": "重庆", "lat": 29.5630, "lon": 106.5516, "tz": "Asia/Shanghai"},
    {"slug": "guangzhou", "name": "广州", "lat": 23.1291, "lon": 113.2644, "tz": "Asia/Shanghai"},
    {"slug": "shenzhen", "name": "深圳", "lat": 22.5431, "lon": 114.0579, "tz": "Asia/Shanghai"},
    {"slug": "hangzhou", "name": "杭州", "lat": 30.2741, "lon": 120.1551, "tz": "Asia/Shanghai"},
    {"slug": "nanjing", "name": "南京", "lat": 32.0603, "lon": 118.7969, "tz": "Asia/Shanghai"},
    {"slug": "chengdu", "name": "成都", "lat": 30.5728, "lon": 104.0668, "tz": "Asia/Shanghai"},
    {"slug": "wuhan", "name": "武汉", "lat": 30.5928, "lon": 114.3055, "tz": "Asia/Shanghai"},
    {"slug": "xian", "name": "西安", "lat": 34.3416, "lon": 108.9398, "tz": "Asia/Shanghai"},
    {"slug": "zhengzhou", "name": "郑州", "lat": 34.7466, "lon": 113.6254, "tz": "Asia/Shanghai"},
    {"slug": "changsha", "name": "长沙", "lat": 28.2282, "lon": 112.9388, "tz": "Asia/Shanghai"},
    {"slug": "jinan", "name": "济南", "lat": 36.6512, "lon": 117.1201, "tz": "Asia/Shanghai"},
    {"slug": "hefei", "name": "合肥", "lat": 31.8206, "lon": 117.2272, "tz": "Asia/Shanghai"},
    {"slug": "fuzhou", "name": "福州", "lat": 26.0745, "lon": 119.2965, "tz": "Asia/Shanghai"},
    {"slug": "nanchang", "name": "南昌", "lat": 28.6820, "lon": 115.8579, "tz": "Asia/Shanghai"},
    {"slug": "shenyang", "name": "沈阳", "lat": 41.8057, "lon": 123.4315, "tz": "Asia/Shanghai"},
    {"slug": "changchun", "name": "长春", "lat": 43.8171, "lon": 125.3235, "tz": "Asia/Shanghai"},
    {"slug": "harbin", "name": "哈尔滨", "lat": 45.8038, "lon": 126.5349, "tz": "Asia/Shanghai"},
    {"slug": "shijiazhuang", "name": "石家庄", "lat": 38.0428, "lon": 114.5149, "tz": "Asia/Shanghai"},
    {"slug": "taiyuan", "name": "太原", "lat": 37.8706, "lon": 112.5489, "tz": "Asia/Shanghai"},
    {"slug": "hohhot", "name": "呼和浩特", "lat": 40.8426, "lon": 111.7492, "tz": "Asia/Shanghai"},
    {"slug": "nanning", "name": "南宁", "lat": 22.8170, "lon": 108.3669, "tz": "Asia/Shanghai"},
    {"slug": "haikou", "name": "海口", "lat": 20.0440, "lon": 110.1999, "tz": "Asia/Shanghai"},
    {"slug": "guiyang", "name": "贵阳", "lat": 26.6470, "lon": 106.6302, "tz": "Asia/Shanghai"},
    {"slug": "kunming", "name": "昆明", "lat": 25.0389, "lon": 102.7183, "tz": "Asia/Shanghai"},
    {"slug": "lhasa", "name": "拉萨", "lat": 29.6520, "lon": 91.1721, "tz": "Asia/Shanghai"},
    {"slug": "lanzhou", "name": "兰州", "lat": 36.0611, "lon": 103.8343, "tz": "Asia/Shanghai"},
    {"slug": "xining", "name": "西宁", "lat": 36.6171, "lon": 101.7782, "tz": "Asia/Shanghai"},
    {"slug": "yinchuan", "name": "银川", "lat": 38.4872, "lon": 106.2309, "tz": "Asia/Shanghai"},
    {"slug": "urumqi", "name": "乌鲁木齐", "lat": 43.8256, "lon": 87.6168, "tz": "Asia/Shanghai"},
    {"slug": "hongkong", "name": "香港", "lat": 22.3193, "lon": 114.1694, "tz": "Asia/Hong_Kong"},
    {"slug": "macau", "name": "澳门", "lat": 22.1987, "lon": 113.5439, "tz": "Asia/Macau"},
    {"slug": "taipei", "name": "台北", "lat": 25.0330, "lon": 121.5654, "tz": "Asia/Taipei"},
    {"slug": "suzhou", "name": "苏州", "lat": 31.2989, "lon": 120.5853, "tz": "Asia/Shanghai"},
    {"slug": "ningbo", "name": "宁波", "lat": 29.8683, "lon": 121.5440, "tz": "Asia/Shanghai"},
    {"slug": "qingdao", "name": "青岛", "lat": 36.0671, "lon": 120.3826, "tz": "Asia/Shanghai"},
    {"slug": "dalian", "name": "大连", "lat": 38.9140, "lon": 121.6147, "tz": "Asia/Shanghai"},
    {"slug": "xiamen", "name": "厦门", "lat": 24.4798, "lon": 118.0894, "tz": "Asia/Shanghai"},
    {"slug": "foshan", "name": "佛山", "lat": 23.0215, "lon": 113.1214, "tz": "Asia/Shanghai"},
    {"slug": "dongguan", "name": "东莞", "lat": 23.0205, "lon": 113.7518, "tz": "Asia/Shanghai"},
    {"slug": "wuxi", "name": "无锡", "lat": 31.4912, "lon": 120.3119, "tz": "Asia/Shanghai"},
    {"slug": "wenzhou", "name": "温州", "lat": 27.9949, "lon": 120.6994, "tz": "Asia/Shanghai"},
    {"slug": "changzhou", "name": "常州", "lat": 31.8106, "lon": 119.9741, "tz": "Asia/Shanghai"},
    {"slug": "nantong", "name": "南通", "lat": 31.9802, "lon": 120.8943, "tz": "Asia/Shanghai"},
    {"slug": "quanzhou", "name": "泉州", "lat": 24.8741, "lon": 118.6757, "tz": "Asia/Shanghai"},
    {"slug": "yantai", "name": "烟台", "lat": 37.4638, "lon": 121.4479, "tz": "Asia/Shanghai"},
    {"slug": "xuzhou", "name": "徐州", "lat": 34.2044, "lon": 117.2841, "tz": "Asia/Shanghai"},
    {"slug": "zhuhai", "name": "珠海", "lat": 22.2710, "lon": 113.5767, "tz": "Asia/Shanghai"},
    {"slug": "newyork", "name": "纽约", "lat": 40.7128, "lon": -74.0060, "tz": "America/New_York"},
    {"slug": "london", "name": "伦敦", "lat": 51.5074, "lon": -0.1278, "tz": "Europe/London"},
    {"slug": "tokyo", "name": "东京", "lat": 35.6762, "lon": 139.6503, "tz": "Asia/Tokyo"},
    {"slug": "paris", "name": "巴黎", "lat": 48.8566, "lon": 2.3522, "tz": "Europe/Paris"},
    {"slug": "singapore", "name": "新加坡", "lat": 1.3521, "lon": 103.8198, "tz": "Asia/Singapore"},
    {"slug": "losangeles", "name": "洛杉矶", "lat": 34.0522, "lon": -118.2437, "tz": "America/Los_Angeles"},
    {"slug": "chicago", "name": "芝加哥", "lat": 41.8781, "lon": -87.6298, "tz": "America/Chicago"},
]

VARIABLES = [
    # 降水
    "precipitation",
    "rain",                       
    "showers",
    "snowfall",                   # cm
    "snowfall_water_equivalent",  # mm
    "snow_depth",                 
    "weather_code",
    # 对流
    "cape",
    "convective_inhibition",      
    "boundary_layer_height",
    # 温度
    "temperature_2m",
    # 风
    "wind_speed_10m",            
    "wind_gusts_10m",
    "wind_direction_10m",
    # 视觉 / 旁证
    "cloud_cover",
    "visibility",
    "is_day",
    # 地面
    "soil_moisture_0_to_7cm",
]


GAP_TOLERANT = {"convective_inhibition": "空值视为无抑制，cin_term = 1.0"}

CHUNK_DAYS = 30
PROBE_BATCH = 16
MAX_RETRIES = 5
USER_AGENT = "rain-archive-fetch/4.0 (personal, non-commercial)"

AUDIBLE_RAIN_MM = 0.3
AUDIBLE_SNOW_CM = 0.5
AUDIBLE_SNOW_TEMP_C = -2.0

CIN_SUPPRESS_THRESHOLD = -50.0
CIN_SUPPRESS_FACTOR = 0.3


# ---------------------------------------------------------------- HTTP

def http_get_json(params, timeout=180):
    url = API_HOST + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8")), url
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"error": True, "reason": body[:500]}
        return e.code, payload, url


def http_get_json_retry(params):
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status, payload, url = http_get_json(params)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == MAX_RETRIES:
                raise
            print("    网络错误 %s，%d 秒后重试 (%d/%d)" % (e, delay, attempt, MAX_RETRIES))
            time.sleep(delay)
            delay *= 2
            continue
        if status == 200:
            return payload, url
        if status == 429 or status >= 500:
            if attempt == MAX_RETRIES:
                raise RuntimeError("HTTP %d，重试已用尽：%s" % (status, payload.get("reason")))
            print("    HTTP %d，%d 秒后重试 (%d/%d)" % (status, delay, attempt, MAX_RETRIES))
            time.sleep(delay)
            delay *= 2
            continue
        return payload, url
    raise RuntimeError("不可达")


def base_params(model, lat, lon, variables, start, end):
    return {
        "latitude": "%.4f" % lat,
        "longitude": "%.4f" % lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(variables),
        "models": model,
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
        "temperature_unit": "celsius",
        "timeformat": "iso8601",
        "cell_selection": "land",
       
    }


# ---------------------------------------------------------------- 抓取

def probe(model, lat, lon, variables, day, ok, dropped):
    payload, _ = http_get_json_retry(base_params(model, lat, lon, variables, day, day))
    time.sleep(0.2)
    if not payload.get("error"):
        ok.extend(variables)
        return
    if len(variables) == 1:
        reason = str(payload.get("reason", ""))[:200]
        dropped[variables[0]] = reason
        print("    剔除 %-28s %s" % (variables[0], reason))
        return
    mid = len(variables) // 2
    probe(model, lat, lon, variables[:mid], day, ok, dropped)
    probe(model, lat, lon, variables[mid:], day, ok, dropped)


def probe_variables(model, lat, lon, variables, day):
    print("  探测变量可用性：候选 %d 项" % len(variables))
    ok, dropped = [], {}
    for i in range(0, len(variables), PROBE_BATCH):
        probe(model, lat, lon, variables[i:i + PROBE_BATCH], day, ok, dropped)
    okset = set(ok)
    ok = [v for v in variables if v in okset]
    print("  可用 %d 项，剔除 %d 项" % (len(ok), len(dropped)))
    return ok, dropped


CHUNK_EPOCH = date(2000, 1, 1)


def date_chunks(start, end, days):
    """把 [start, end] 切成块，块的边界钉在一条固定网格上
    （从 CHUNK_EPOCH 起每 days 天一格），不跟着 start 走。

    这一点是滚动窗口能不能跑起来的关键：窗口每天往前挪一天，
    如果块从 start 开始切，所有块的起止日期都会跟着挪一天，
    缓存键全部落空，等于每天把一整年重下一遍。
    钉在网格上的话，中间那些块的键一天都不会变，
    每天只有末尾那个不完整的块需要重取。

    第三个返回值 full 表示这一块被完整覆盖。不完整的块不写缓存——
    否则明天会拿到今天那份缺了尾巴的数据，而且永远不会再更新。"""
    k0 = (start - CHUNK_EPOCH).days // days
    k1 = (end - CHUNK_EPOCH).days // days
    for k in range(k0, k1 + 1):
        a = CHUNK_EPOCH + timedelta(days=k * days)
        b = a + timedelta(days=days - 1)
        c0, c1 = max(a, start), min(b, end)
        if c0 <= c1:
            yield c0, c1, (c0 == a and c1 == b)


def cache_path(cache_dir, model, lat, lon, variables, start, end):
    key = "|".join([model, "%.4f" % lat, "%.4f" % lon,
                    ",".join(variables), start.isoformat(), end.isoformat()])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return os.path.join(cache_dir, "%s_%s_%s.json" % (start.isoformat(), end.isoformat(), digest))


def fetch_all(model, lat, lon, variables, start, end, cache_dir, sleep_s):
    os.makedirs(cache_dir, exist_ok=True)
    chunks = list(date_chunks(start, end, CHUNK_DAYS))
    payloads = []
    for i, (c0, c1, full) in enumerate(chunks, 1):
        path = cache_path(cache_dir, model, lat, lon, variables, c0, c1) if full else None
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                payloads.append(json.load(f))
            print("  [%2d/%2d] %s → %s  (缓存)" % (i, len(chunks), c0, c1))
            continue
        print("  [%2d/%2d] %s → %s  请求中…" % (i, len(chunks), c0, c1))
        payload, url = http_get_json_retry(base_params(model, lat, lon, variables, c0, c1))
        if payload.get("error"):
            raise RuntimeError("请求失败：%s\nURL: %s" % (payload.get("reason"), url))
        payload["_request_url"] = url
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        payloads.append(payload)
        time.sleep(sleep_s)
    return payloads


def merge(payloads, variables):
    table = {}
    for p in payloads:
        h = p["hourly"]
        times = h["time"]
        blank = [None] * len(times)
        for idx, t in enumerate(times):
            row = table.setdefault(t, {})
            for v in variables:
                row[v] = h.get(v, blank)[idx]
    return sorted(table.keys()), table


# ---------------------------------------------------------------- 覆盖检查与裁剪

def num(x):
    return x if isinstance(x, (int, float)) else None


def coverage(times, table, variables):
    """返回 {var: (first_idx, last_idx, internal_gap_count)}；全空则 (None, None, n)。"""
    cov = {}
    for v in variables:
        idx = [i for i, t in enumerate(times) if table[t].get(v) is not None]
        if not idx:
            cov[v] = (None, None, len(times))
            continue
        internal = sum(1 for a, b in zip(idx, idx[1:]) if b - a > 1)
        cov[v] = (idx[0], idx[-1], internal)
    return cov


def common_span(cov, variables, n):
    considered = [v for v in variables if v not in GAP_TOLERANT]
    firsts = [cov[v][0] for v in considered if cov[v][0] is not None]
    lasts  = [cov[v][1] for v in considered if cov[v][1] is not None]
    if not firsts:
        return None, None
    return max(firsts), min(lasts)


def validate(times, table, variables, lo, hi):
    """公共区间内除 GAP_TOLERANT 外不允许有空值。返回问题字段清单。"""
    bad = {}
    for v in variables:
        if v in GAP_TOLERANT:
            continue
        miss = sum(1 for i in range(lo, hi + 1) if table[times[i]].get(v) is None)
        if miss:
            bad[v] = miss
    return bad


# ---------------------------------------------------------------- 派生量

def liquid_mm(row):
    p = num(row.get("precipitation"))
    if p is None:
        return None
    s = num(row.get("snowfall_water_equivalent")) or 0.0
    return max(p - s, 0.0)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def is_audible(row):
    liq = liquid_mm(row)
    if liq is not None and liq >= AUDIBLE_RAIN_MM:
        return True
    sf = num(row.get("snowfall"))
    t = num(row.get("temperature_2m"))
    return bool(sf is not None and t is not None
                and sf >= AUDIBLE_SNOW_CM and t <= AUDIBLE_SNOW_TEMP_C)


def thunder_score(row):
    """文档 5.3.5，去掉 li_term（本模式无 lifted_index）。
    CIN 为空按无抑制处理，同时返回是否发生了这种填充。"""
    cape = num(row.get("cape")) or 0.0
    cin = num(row.get("convective_inhibition"))
    liq = liquid_mm(row) or 0.0
    cape_term = clamp(cape / 2000.0, 0.0, 1.0)
    cin_filled = cin is None
    cin_term = 1.0 if (cin_filled or cin > CIN_SUPPRESS_THRESHOLD) else CIN_SUPPRESS_FACTOR
    rain_term = clamp((liq - 1.0) / 4.0, 0.0, 1.0)
    return cape_term * cin_term * rain_term, cin_filled


def find_events(flags):
    """连续可听小时构成一个事件，允许 1 小时间断被合并（文档 6.3.1）。"""
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


def quantile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo)


def compile_index(times, table, variables, lo, hi):
    """产出逐小时索引行与事件行，索引行保证无空值。"""
    span = list(range(lo, hi + 1))
    rows = []
    for i in span:
        r = table[times[i]]
        liq = liquid_mm(r) or 0.0
        p = num(r.get("precipitation")) or 0.0
        sh = num(r.get("showers")) or 0.0
        ts, cin_filled = thunder_score(r)
        rows.append({
            "time_utc": times[i],
            "liquid_mm": round(liq, 3),
            "showers_frac": round(clamp(sh / p, 0.0, 1.0), 3) if p > 0 else 0.0,
            "snowfall_cm": num(r.get("snowfall")) or 0.0,
            "snow_depth_m": num(r.get("snow_depth")) or 0.0,
            "temperature_c": num(r.get("temperature_2m")),
            "wind_ms": num(r.get("wind_speed_10m")),
            "gust_ms": num(r.get("wind_gusts_10m")),
            "wind_dir_deg": num(r.get("wind_direction_10m")),
            "cloud_cover": num(r.get("cloud_cover")),
            "visibility_m": num(r.get("visibility")),
            "is_day": int(num(r.get("is_day")) or 0),
            "soil_moisture": num(r.get("soil_moisture_0_to_7cm")),
            "cape": num(r.get("cape")) or 0.0,
            "pbl_m": num(r.get("boundary_layer_height")) or 0.0,
            "cin_missing": int(cin_filled),
            "thunder_score": round(ts, 4),
            "weather_code": int(num(r.get("weather_code")) or 0),
            "audible": int(is_audible(r)),
            "rain_field_only": num(r.get("rain")) or 0.0,   # 对照用，勿作音量驱动
        })

    flags = [bool(x["audible"]) for x in rows]
    events = find_events(flags)

    for x in rows:
        x.update({"event_id": -1, "run_elapsed": -1, "run_remaining": -1,
                  "gap_to_next": -1, "burstiness": 0.0, "character": 0.0})

    event_rows = []
    for eid, (s, e) in enumerate(events):
        liqs = [rows[k]["liquid_mm"] for k in range(s, e + 1)]
        mean = sum(liqs) / len(liqs)
        if mean > 0 and len(liqs) > 1:
            var = sum((x - mean) ** 2 for x in liqs) / (len(liqs) - 1)
            burst = math.sqrt(var) / mean
        else:
            burst = 0.0
        gap = (events[eid + 1][0] - e - 1) if eid + 1 < len(events) else -1
        for k in range(s, e + 1):
            conv = rows[k]["showers_frac"]
            rows[k].update({
                "event_id": eid,
                "run_elapsed": k - s,
                "run_remaining": e - k + 1,
                "gap_to_next": gap,
                "burstiness": round(burst, 3),
                "character": round(clamp(0.6 * conv + 0.4 * clamp(burst, 0, 1.5) / 1.5, 0, 1), 3),
            })
        event_rows.append({
            "event_id": eid,
            "start_utc": rows[s]["time_utc"],
            "end_utc": rows[e]["time_utc"],
            "hours": e - s + 1,
            "audible_hours": sum(1 for k in range(s, e + 1) if rows[k]["audible"]),
            "liquid_mean_mm": round(mean, 3),
            "liquid_max_mm": round(max(liqs), 3),
            "liquid_sum_mm": round(sum(liqs), 2),
            "burstiness": round(burst, 3),
            "showers_frac_mean": round(sum(rows[k]["showers_frac"] for k in range(s, e + 1)) / (e - s + 1), 3),
            "thunder_score_max": round(max(rows[k]["thunder_score"] for k in range(s, e + 1)), 4),
            "gap_to_next": gap,
        })
    return rows, event_rows


# ---------------------------------------------------------------- 写出

def write_csv(path, rows, fieldnames):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_raw_csv(path, times, table, variables):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc"] + variables)
        for t in times:
            w.writerow([t] + [table[t].get(v) for v in variables])


# ---------------------------------------------------------------- 主流程

def run_city(city, args):
    print("\n" + "=" * 68)
    print("%s（%s）  %.4f, %.4f" % (city["name"], city["slug"], city["lat"], city["lon"]))
    print("=" * 68)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    cache_dir = os.path.join(args.outdir, "cache")

    variables, rejected = probe_variables(args.model, city["lat"], city["lon"], VARIABLES, start)
    if not variables:
        print("  没有任何变量可用，跳过")
        return None

    payloads = fetch_all(args.model, city["lat"], city["lon"], variables,
                         start, end, cache_dir, args.sleep)
    times, table = merge(payloads, variables)
    n = len(times)

    expected = int((datetime.combine(end, datetime.min.time())
                    - datetime.combine(start, datetime.min.time())).total_seconds() // 3600) + 24
    print("\n  返回 %d 小时，期望 %d" % (n, expected))

    cov = coverage(times, table, variables)
    ragged = {v: cov[v] for v in variables if cov[v][0] not in (0, None) or cov[v][1] != n - 1}
    if ragged:
        print("  首末不齐的字段：")
        for v, (a, b, g) in ragged.items():
            print("    %-26s %s → %s" % (v, times[a] if a is not None else "全空",
                                         times[b] if b is not None else "全空"))
    gappy = {v: cov[v][2] for v in variables if cov[v][2] and v not in GAP_TOLERANT}

    lo, hi = common_span(cov, variables, n)
    if lo is None or lo > hi:
        print("  没有任何公共可用区间，跳过")
        return None
    print("  公共可用区间：%s … %s（%d 小时，裁掉首尾 %d 小时）"
          % (times[lo], times[hi], hi - lo + 1, n - (hi - lo + 1)))

    bad = validate(times, table, variables, lo, hi)
    if bad:
        print("\n  区间内仍有意外空值，不生成索引文件：")
        for v, c in bad.items():
            print("    %-26s 缺 %d 小时" % (v, c))
        print("  处理方式：把该字段从 VARIABLES 删掉，或加进 GAP_TOLERANT 并写明填充规则。")
        return None

    rows, events = compile_index(times, table, variables, lo, hi)

    # ---- 统计 ----
    liq_aud = [r["liquid_mm"] for r in rows if r["liquid_mm"] >= AUDIBLE_RAIN_MM]
    aud = sum(1 for r in rows if r["audible"])
    cin_filled = sum(r["cin_missing"] for r in rows)
    codes = {}
    for r in rows:
        codes[r["weather_code"]] = codes.get(r["weather_code"], 0) + 1
    thunder_codes = sum(codes.get(c, 0) for c in (95, 96, 99))
    lens = [e["hours"] for e in events]
    gaps = [e["gap_to_next"] for e in events if e["gap_to_next"] >= 0]
    tscores = sorted(r["thunder_score"] for r in rows if r["thunder_score"] > 0)

    stats = {
        "hours": len(rows),
        "audible_hours": aud,
        "audible_ratio": round(aud / len(rows), 4),
        "liquid_p50": quantile(liq_aud, .50),
        "liquid_p85": quantile(liq_aud, .85),
        "liquid_p97": quantile(liq_aud, .97),
        "liquid_max": max(liq_aud) if liq_aud else 0.0,
        "events": len(events),
        "event_hours_max": max(lens) if lens else 0,
        "event_hours_median": quantile(lens, .5),
        "gap_max": max(gaps) if gaps else None,
        "cin_missing_hours": cin_filled,
        "weather_code_thunder_hours": thunder_codes,
        "thunder_score_p90": quantile(tscores, .90),
        "thunder_score_p99": quantile(tscores, .99),
        "thunder_score_max": tscores[-1] if tscores else 0.0,
    }

    print("\n  ---- 索引区间统计 ----")
    print("  可听小时 %d，占 %.1f%%" % (aud, 100.0 * aud / len(rows)))
    if liq_aud:
        print("  液态降水分档标定 mm/h：P50 %.2f   P85 %.2f   P97 %.2f   max %.2f"
              % (stats["liquid_p50"], stats["liquid_p85"], stats["liquid_p97"], stats["liquid_max"]))
    print("  降水事件 %d 场；最长 %d 小时 → run_elapsed / run_remaining 需要 %d bit"
          % (len(events), max(lens) if lens else 0,
             max(1, (max(lens) if lens else 1) - 1).bit_length()))
    if gaps:
        print("  最长间隔 %d 小时 → gap_to_next 需要 %d bit" % (max(gaps), max(gaps).bit_length()))
    print("  CIN 空值填充 %d 小时（占 %.1f%%），已记入 cin_missing 列"
          % (cin_filled, 100.0 * cin_filled / len(rows)))
    if tscores:
        print("  thunder_score 非零 %d 小时：P90 %.3f  P99 %.3f  max %.3f"
              % (len(tscores), stats["thunder_score_p90"],
                 stats["thunder_score_p99"], stats["thunder_score_max"]))
    print("  对照：weather_code 判为雷暴（95/96/99）的有 %d 小时" % thunder_codes)

    # ---- 写出 ----
    base = os.path.join(args.outdir, city["slug"])
    write_raw_csv(base + "_raw.csv", times, table, variables)
    idx_fields = list(rows[0].keys())
    write_csv(base + "_index.csv", rows, idx_fields)
    if events:
        write_csv(base + "_events.csv", events, list(events[0].keys()))

    p0 = payloads[0]
    meta = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "city": {"slug": city["slug"], "name": city["name"], "timezone": city["tz"]},
        "requested_coordinates": {"latitude": city["lat"], "longitude": city["lon"]},
        "grid_cell": {"latitude": p0.get("latitude"), "longitude": p0.get("longitude"),
                      "elevation_m": p0.get("elevation")},
        "model": args.model,
        "api_host": API_HOST,
        "requested_range_utc": {"start": args.start, "end": args.end},
        "index_range_utc": {"start": times[lo], "end": times[hi]},
        "variables_fetched": variables,
        "variables_rejected": rejected,
        "gap_tolerant_fields": GAP_TOLERANT,
        "units_raw": p0.get("hourly_units", {}),
        "derived_units": {
            "liquid_mm": "mm/h，= precipitation − snowfall_water_equivalent",
            "wind_ms": "m/s", "gust_ms": "m/s", "visibility_m": "m",
            "snow_depth_m": "m", "pbl_m": "m",
            "thunder_score": "无量纲，0–1，已移除 li_term，λ 系数需重新标定",
            "gap_to_next": "小时；-1 表示本区间内此后不再降水",
        },
        "statistics": stats,
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0), based on ECMWF IFS HRES.",
    }
    with open(base + "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n  已写出 %s_{raw,index,events}.csv 与 %s_meta.json" % (base, base))
    if gappy:
        print("  注意：以下字段在完整区间内有内部空洞（已在裁剪区间外或被容许）：%s" % ", ".join(gappy))
    return {"slug": city["slug"], "index_start": times[lo], "index_end": times[hi], "stats": stats}


def local_today(tz_name):
    """按指定时区取「今天」。
    Windows 上 zoneinfo 常常没有时区库（要装 tzdata），
    所以 Asia/Shanghai 留一条固定 +08:00 的退路——1991 年之后中国没有夏令时。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        if tz_name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Hong_Kong", "Asia/Macau",
                       "Asia/Taipei", "PRC"):
            return datetime.now(timezone(timedelta(hours=8))).date()
        return datetime.now(timezone.utc).date()


def months_back(d, n):
    """往前推 n 个整月，日不够就退到当月最后一天。"""
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    day = d.day
    while True:
        try:
            return date(y, m, day)
        except ValueError:
            day -= 1


def has_data(model, lat, lon, day):
    """这一天这个模式到底有没有数据。
    超出归档范围时接口要么报 400，要么整列返回 null，两种都算没有。"""
    try:
        # http_get_json_retry 返回的是 (payload, url) 两元组，不是 payload
        payload, _ = http_get_json_retry(base_params(model, lat, lon, ["precipitation"], day, day))
    except Exception:
        return False
    # 400 之类不重试的状态码也会正常返回，body 里带 error/reason
    if not isinstance(payload, dict) or payload.get("error"):
        return False
    vals = (payload.get("hourly") or {}).get("precipitation") or []
    return any(v is not None for v in vals)


ARCHIVE_HOST = "https://archive-api.open-meteo.com/v1/archive"

# 探测用的日期梯子。挑的都是文档里提到的分界点附近：
# 历史预报档号称 2021/2022 起、IFS HRES 单次运行档 2024-03 起、
# ECMWF 转 open-data 是 2025-10-01；历史天气档的 IFS 分析场是 2017 起、ERA5 是 1940 起。
PROBE_DATES = ["2017-01-15", "2019-01-15", "2021-01-15", "2022-01-15",
               "2023-01-15", "2024-04-15", "2025-06-15", "2025-10-15"]


def probe_earliest(args):
    """横向对照两个档口在若干日期上有没有数据。

    不做二分。二分只能回答「有没有数」，回答不了「这是谁的数」——
    历史预报档在归档范围之外不会报错，会拿别的数据集顶上，
    于是二分会一路探到 2020 年甚至更早，给出一个假的起点。
    列成表格，哪一档从哪天开始才是它自己的数据，一眼就看得出来。"""
    global API_HOST
    city = CITIES[0]
    if args.city:
        city = next((c for c in CITIES if c["slug"] == args.city), city)
    print("在 %s（%.4f, %.4f）上探测 %s\n" % (city["name"], city["lat"], city["lon"], args.model))

    hosts = [("历史预报档", args.host), ("历史天气档", ARCHIVE_HOST)]
    print("%-14s %s" % ("", "  ".join("%-12s" % d for d in PROBE_DATES)))
    for label, host in hosts:
        API_HOST = host
        cells = []
        for d in PROBE_DATES:
            cells.append("有" if has_data(args.model, city["lat"], city["lon"],
                                          date.fromisoformat(d)) else "—")
            time.sleep(0.3)
        print("%-14s %s" % (label, "  ".join("%-12s" % c for c in cells)))
    API_HOST = args.host

    print("""
读法：
  历史预报档（historical-forecast-api）存的是模式当年跑出来的预报。
  官方说法是「视模式与归档情况，从 2021 或 2022 年起」。
  这一行如果在 2021 之前也显示「有」，那不是 IFS 的归档预报，
  是接口拿别的数据集顶上了——数据源不一致，不要拿它当长历史用。

  历史天气档（archive-api，--host %s）是再分析：
  ECMWF IFS 分析场 9 km 从 2017 年，ERA5 从 1940 年。
  要拉多年历史，用这一档。代价是变量集与预报档不同，
  probe_variables 会自动丢掉取不到的；CAPE / CIN 一旦被丢掉，雷的评分会退化。

  换档之前先拿一座城试跑，看它丢了哪些变量：
    python pythondownload.py --host %s --city beijing --months 1
""" % (ARCHIVE_HOST, ARCHIVE_HOST))


def main():
    # global 必须写在本函数首次用到 API_HOST 之前（下面 --host 的 default 就用了它），
    # 否则是 SyntaxError。ast.parse 查不出来，compile() 才会报。
    global API_HOST
    ap = argparse.ArgumentParser(description="Open-Meteo IFS HRES 抓取与索引编译")
    ap.add_argument("--city", default=None, help="只跑某个 slug，不填则跑全表")
    ap.add_argument("--cities-file", default=None,
                    help="城市表 JSON（make_cities.py 的产物）。不填则用本文件里的 CITIES")
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过 outdir 里已经有产物的城。跨天分批跑、或中断后重跑时用")
    ap.add_argument("--max-calls", type=float, default=0,
                    help="本次最多消耗多少次加权调用，到了就停（免费版每天 10000）。0 为不限")
    ap.add_argument("--months", type=int, default=0,
                    help="滚动窗口：只取最近 N 个月，end 取 --tz 时区的昨天。"
                         "给了它就忽略 --start/--end。每天跑一次就能一直保持最近 N 个月")
    ap.add_argument("--tz", default="Asia/Shanghai",
                    help="--months 和 today 按哪个时区算日期")
    ap.add_argument("--floor", default=DEFAULT_START,
                    help="数据最早可用日期，滚动窗口不会越过它。用 --find-start 探出来")
    ap.add_argument("--start", default=DEFAULT_START, help="也可以写 today")
    ap.add_argument("--end", default=DEFAULT_END, help="含当日 00–23 时；也可以写 today")
    ap.add_argument("--host", default=API_HOST,
                    help="改数据源。历史预报档（默认）与再分析档 "
                         "https://archive-api.open-meteo.com/v1/archive 的变量集不同，"
                         "换之前先用 --find-start 探一探")
    ap.add_argument("--find-start", action="store_true",
                    help="二分探测这个模式最早有数据的日期，然后退出。"
                         "每次探测只要 1 天 1 个变量，几乎不耗额度")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--outdir", default="openmeteo_out")
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    API_HOST = args.host

    today = local_today(args.tz)
    if args.start == "today":
        args.start = today.isoformat()
    if args.end == "today":
        # 历史预报档要等模式跑完并归档，当天的数据通常还不全，往回退一天
        args.end = (today - timedelta(days=1)).isoformat()

    if args.months:
        end_d = today - timedelta(days=1)
        start_d = months_back(end_d, args.months) + timedelta(days=1)
        # 起点也钉到网格上（往前取整）。差这一下，窗口头上那块就从「每天重取」
        # 变成「一次下完永久命中」，日常额度直接减半。
        # 代价是实际跨度会比 N 个月多出不到 30 天——对一个雨的档案来说只多不少。
        start_d = CHUNK_EPOCH + timedelta(
            days=((start_d - CHUNK_EPOCH).days // CHUNK_DAYS) * CHUNK_DAYS)
        floor_d = date.fromisoformat(args.floor)
        if start_d < floor_d:
            start_d = floor_d
            print("窗口起点被 --floor 顶住：%s 之前没有数据，本次实际跨度 %d 天"
                  % (args.floor, (end_d - start_d).days + 1))
        args.start, args.end = start_d.isoformat(), end_d.isoformat()
        print("滚动窗口（%s）：%s → %s" % (args.tz, args.start, args.end))

    if args.find_start:
        probe_earliest(args)
        return

    if date.fromisoformat(args.end) < date.fromisoformat(args.start):
        sys.exit("end 早于 start")

    cities = CITIES
    if args.cities_file:
        with open(args.cities_file, encoding="utf-8") as f:
            cities = json.load(f)
        need = {"slug", "name", "lat", "lon", "tz"}
        for c in cities:
            missing = need - set(c)
            if missing:
                sys.exit("城市表里有条目缺字段 %s：%r" % (sorted(missing), c))
        seen = set()
        dup = [c["slug"] for c in cities if c["slug"] in seen or seen.add(c["slug"])]
        if dup:
            sys.exit("城市表里 slug 重复：%s。slug 是 web_out/scores/<slug>/ 的路径，"
                     "撞车会互相覆盖" % ", ".join(sorted(set(dup))))
    if args.city:
        cities = [c for c in cities if c["slug"] == args.city]
        if not cities:
            sys.exit("城市表里没有 slug=%s" % args.city)

    if args.skip_existing:
        before = len(cities)
        cities = [c for c in cities
                  if not os.path.exists(os.path.join(args.outdir, c["slug"], "index.json"))]
        if before != len(cities):
            print("已有产物的 %d 座城跳过" % (before - len(cities)))

    # Open-Meteo 的计费是加权的：weight = 地点数 × (天数/14) × (变量数/10)。
    # 免费版每天 10000 次、每小时 5000、每分钟 600。这里只算日额度，
    # 分钟额度由 --sleep 兜着（一块 30 天的请求 ≈ 3.9 次，1 秒一发远够不着 600/min）。
    s_d, e_d = date.fromisoformat(args.start), date.fromisoformat(args.end)
    blocks = list(date_chunks(s_d, e_d, CHUNK_DAYS))
    unit = (CHUNK_DAYS / 14.0) * (len(VARIABLES) / 10.0)
    per_city = len(blocks) * unit
    per_city += math.ceil(len(VARIABLES) / PROBE_BATCH) * (1 / 14.0) * (PROBE_BATCH / 10.0)
    partial = sum(1 for _, _, full in blocks if not full)
    print("跨度 %d 天，切成 %d 块（其中 %d 块不完整，每次都要重取）"
          % ((e_d - s_d).days + 1, len(blocks), partial))
    print("首次全量：每城约 %.1f 次加权调用，%d 座城合计约 %.0f 次"
          % (per_city, len(cities), per_city * len(cities)))
    print("之后每天：缓存命中完整的块，每城只重取 %d 块 ≈ %.1f 次，合计约 %.0f 次"
          % (partial, partial * unit, partial * unit * len(cities)))
    if args.max_calls and per_city * len(cities) > args.max_calls:
        room = int(args.max_calls // per_city)
        print("  超过 --max-calls %.0f，本次只跑前 %d 座；"
              "剩下的明天加 --skip-existing 接着跑" % (args.max_calls, room))
        cities = cities[:room]

    os.makedirs(args.outdir, exist_ok=True)
    print("模式 %s   请求区间 %s … %s   城市 %d 座   变量 %d 项"
          % (args.model, args.start, args.end, len(cities), len(VARIABLES)))

    done = []
    for c in cities:
        r = run_city(c, args)
        if r:
            done.append(r)

    if len(done) > 1:
        starts = max(d["index_start"] for d in done)
        ends = min(d["index_end"] for d in done)
        print("\n" + "=" * 68)
        print("全部城市的公共索引区间：%s … %s" % (starts, ends))


if __name__ == "__main__":
    main()