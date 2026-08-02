/* rain_weather.js — 「那座城此刻在不在下雨」与「下一场雨还有多久」
 *
 * 唯一数据源是 Open-Meteo 的 /v1/forecast（免费、无 key、带 CORS，
 * 与 pythondownload.py 取历史数据用的是同一家，口径一致）：
 *
 *   批量档 batch()   一次请求带上名册里全部坐标，只取 current 的 rain / showers。
 *                    给城市下拉菜单标记谁在下雨，也给 rain_place 的第二档用。
 *   单城档 city()    当前这座城另要一份 hourly，用来算「下一场雨在几小时后」。
 *
 * 判定阈值与 manifest.thresholds.rain_floor_mm_h 一致（0.3 mm/h）：
 * 低于它，rain_engine 当初根本不会把这一小时编成场次，
 * 前端也就没有理由说它「在下雨」。
 *
 * 两档各自带缓存。取不到时返回 ok:false —— 调用方必须按「无法判断」处理，
 * 不要当成没下雨。断网时把窗户擦干是最糟的表现。
 *
 * 不碰 DOM，不产生任何可显示的文本。
 */
(function (global) {
"use strict";

const CFG = {
  api:         "https://api.open-meteo.com/v1/forecast",
  floorMmH:    0.3,        // 与 manifest.thresholds.rain_floor_mm_h 相同
  horizonDays: 3,          // 预报只看 72 小时。再远的数字念出来也没有意义
  batchTtlMs:  600000,     // 全城「此刻」缓存 10 分钟
  cityTtlMs:   900000,     // 单城预报缓存 15 分钟
  timeoutMs:   6000,
};

async function get(url) {
  const ctl  = typeof AbortController === "function" ? new AbortController() : null;
  const kill = setTimeout(() => ctl && ctl.abort(), CFG.timeoutMs);
  let res;
  try { res = await fetch(url, ctl ? { signal: ctl.signal } : undefined); }
  finally { clearTimeout(kill); }
  if (!res.ok) throw new Error("Open-Meteo HTTP " + res.status);
  return res.json();
}

/** current 块换算成 mm/h。
 *  current 的 interval 是回看窗口的秒数（15 分钟模型下是 900），
 *  不折算的话小雨会被当成没下。降雪不算——SPEC 里降雪本来就不发声。 */
function currentMmH(cur) {
  if (!cur) return null;
  const iv = cur.interval > 0 ? cur.interval : 3600;
  const mm = (cur.rain != null || cur.showers != null)
    ? (cur.rain || 0) + (cur.showers || 0)
    : (cur.precipitation || 0);
  return mm * 3600 / iv;
}

/** hourly 里第一个越过阈值的未来钟点，距现在几小时。找不到返回 null。
 *  Open-Meteo 的逐时降水是「该时刻之前一小时的累计」，所以这个数
 *  最多差一小时；对一句话来说够了。 */
function nextRain(hr, now) {
  if (!hr || !Array.isArray(hr.time)) return null;
  const t = hr.time, rain = hr.rain, sh = hr.showers, pr = hr.precipitation;
  for (let i = 0; i < t.length; i++) {
    const ts = typeof t[i] === "number" ? t[i] : Date.parse(t[i] + "Z") / 1000;
    if (!Number.isFinite(ts) || ts <= now) continue;
    const mm = (rain || sh)
      ? ((rain && rain[i]) || 0) + ((sh && sh[i]) || 0)
      : ((pr && pr[i]) || 0);
    if (mm >= CFG.floorMmH) return Math.max(1, Math.round((ts - now) / 3600));
  }
  return null;
}

const pick = (b, k) => (Array.isArray(b) ? (b[0] && b[0][k]) : (b && b[k])) || null;

// ───────────────────────────────── 批量档

let _batch = null;

/**
 * @param {Array<{slug,lat,lon}>} cities
 * @returns {Promise<Map<string,{mmH:number,raining:boolean}>>}
 */
async function batch(cities) {
  const cs  = (cities || []).filter(c => c && Number.isFinite(c.lat) && Number.isFinite(c.lon));
  if (!cs.length) return new Map();
  const key = cs.map(c => c.slug).join(",");
  if (_batch && _batch.key === key && Date.now() - _batch.at < CFG.batchTtlMs) return _batch.map;

  const body = await get(CFG.api +
    "?latitude="  + cs.map(c => c.lat.toFixed(4)).join(",") +
    "&longitude=" + cs.map(c => c.lon.toFixed(4)).join(",") +
    "&current=rain,showers,precipitation");

  const rows = Array.isArray(body) ? body : [body];
  // 多坐标按请求顺序返回。条数对不上就不猜，直接当这一次失败。
  if (rows.length !== cs.length) throw new Error("返回条数与请求不符");

  const map = new Map();
  for (let i = 0; i < cs.length; i++) {
    const mmH = currentMmH(rows[i] && rows[i].current);
    if (mmH == null) continue;
    map.set(cs[i].slug, { mmH, raining: mmH >= CFG.floorMmH });
  }
  _batch = { key, at: Date.now(), map };
  return map;
}

/** 已经取回来的那一份，没有就返回 null。菜单要立刻画出来时用，不触发请求。 */
function batchCached() {
  return (_batch && Date.now() - _batch.at < CFG.batchTtlMs) ? _batch.map : null;
}

// ───────────────────────────────── 单城档

const _city = new Map();

/**
 * @param {{slug,lat,lon}} c
 * @returns {Promise<{ok,slug,mmH,raining,nextHours}>}
 *          ok 为 false 表示这次没问出结果，调用方应当维持原状。
 */
async function city(c) {
  if (!c || !Number.isFinite(c.lat) || !Number.isFinite(c.lon)) {
    return { ok: false, slug: c && c.slug, mmH: 0, raining: false, nextHours: null };
  }
  const hit = _city.get(c.slug);
  if (hit && Date.now() - hit.at < CFG.cityTtlMs) return hit.val;

  let val;
  try {
    const b = await get(CFG.api +
      "?latitude="  + c.lat.toFixed(4) +
      "&longitude=" + c.lon.toFixed(4) +
      "&current=rain,showers,precipitation" +
      "&hourly=rain,showers,precipitation" +
      "&forecast_days=" + CFG.horizonDays +
      "&timeformat=unixtime");

    const cur = pick(b, "current"), hr = pick(b, "hourly");
    const mmH = currentMmH(cur);
    // 以服务端给的时刻为准，本机时钟偏了也不会把小时数算歪
    const now = (cur && typeof cur.time === "number") ? cur.time : Math.floor(Date.now() / 1000);
    val = {
      ok:        mmH != null,
      slug:      c.slug,
      mmH:       mmH || 0,
      raining:   mmH != null && mmH >= CFG.floorMmH,
      nextHours: nextRain(hr, now),
    };
  } catch (e) {
    return { ok: false, slug: c.slug, mmH: 0, raining: false, nextHours: null, error: e };
  }

  _city.set(c.slug, { at: Date.now(), val });
  return val;
}

/** 丢掉缓存，下一次调用重新问。切城或手动刷新时用。 */
function forget(slug) {
  if (slug) _city.delete(slug); else { _city.clear(); _batch = null; }
}

global.RainWeather = { CFG, batch, batchCached, city, forget, currentMmH, nextRain };

})(typeof window !== "undefined" ? window : globalThis);
