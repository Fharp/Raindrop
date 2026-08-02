/* rain_place.js — 决定左上角那个城市是谁
 *
 * 三档，逐档降级，永远不抛错（最差也会给出名册里的某一座）：
 *
 *   1. 浏览器定位。拿到坐标后，在名册里找**大圆距离最近**的一座。
 *      名册就是 web_out/index/cities.json，加城市只要重新生成它，
 *      这里一行都不用改——判断完全由数据驱动，没有任何写死的城市表。
 *   2. 定位被拒或超时。向 Open-Meteo 问一次「此刻这些城市里哪些在下雨」，
 *      在下雨的里面随机取一座。候选池只能是名册本身：任何更大的池子都要
 *      先有一张全球城市表，那就又写死了。
 *   3. 连天气也取不到。名册里随机一座。
 *
 * 不产生任何可显示的文本，也不碰 DOM。
 */
(function (global) {
"use strict";

const CFG = {
  geoTimeoutMs:  6000,     // 定位等多久。超时按「拒绝」处理，走第 2 档
  geoMaxAgeMs:   1800000,  // 半小时内的缓存位置直接用，不重新定位
  live:          true,     // 第 2 档要不要联网问天气。关掉就直接跳到第 3 档
  liveTimeoutMs: 5000,
  deadlineMs:    9000,     // 全流程硬上限。到点还没结果就用第 3 档，界面不会一直空着
  api: "https://api.open-meteo.com/v1/forecast",
};

const RADIUS_KM = 6371.0088;
const rad = d => d * Math.PI / 180;

/** 两点大圆距离，公里。 */
function distanceKm(lat1, lon1, lat2, lon2) {
  const dLat = rad(lat2 - lat1), dLon = rad(lon2 - lon1);
  const h = Math.sin(dLat / 2) ** 2 +
            Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** 名册 → 归一化的候选表。没有场次的城市直接排除，免得选中了放不出声。 */
function list(roster) {
  const cs = (roster && roster.cities) || [];
  const out = [];
  for (const c of cs) {
    if (!c || !c.coordinates) continue;
    if (c.events != null && c.events <= 0) continue;
    out.push({
      slug: c.slug, name: c.name, timezone: c.timezone,
      lat: c.coordinates.latitude, lon: c.coordinates.longitude,
      events: c.events || 0,
    });
  }
  return out;
}

/** 给定坐标，返回名册里最近的一座。名册为空时返回 null。 */
function nearest(roster, lat, lon) {
  let best = null, bestD = Infinity;
  for (const c of list(roster)) {
    const d = distanceKm(lat, lon, c.lat, c.lon);
    if (d < bestD) { bestD = d; best = c; }
  }
  return best ? Object.assign({}, best, { distanceKm: bestD }) : null;
}

function pick(arr) { return arr.length ? arr[(Math.random() * arr.length) | 0] : null; }

// ───────────────────────────────── 第 1 档：浏览器定位

function position() {
  return new Promise((ok, no) => {
    const nav = global.navigator;
    if (!nav || !nav.geolocation) { no(new Error("没有 geolocation")); return; }
    // 注意：非 HTTPS 且非 localhost 时，geolocation 直接不可用（secure context 限制）。
    const ask = () => nav.geolocation.getCurrentPosition(
      p  => ok({ lat: p.coords.latitude, lon: p.coords.longitude }),
      e  => no(new Error("定位失败 code=" + (e && e.code))),
      { enableHighAccuracy: false, timeout: CFG.geoTimeoutMs, maximumAge: CFG.geoMaxAgeMs });
    // 已经拒绝过就别再弹一次窗，直接降级
    if (nav.permissions && nav.permissions.query) {
      nav.permissions.query({ name: "geolocation" })
        .then(s => { s.state === "denied" ? no(new Error("定位权限已拒绝")) : ask(); })
        .catch(ask);
    } else ask();
  });
}

// ───────────────────────────────── 第 2 档：此刻在下雨的城市

async function rainingNow(roster) {
  const cs = list(roster);
  if (!cs.length) return [];

  // rain_weather 在的话就走它：判定阈值与页面别处一致（0.3 mm/h，同 manifest），
  // 而且和城市下拉菜单共用同一份缓存，一次会话只打一发批量请求。
  if (global.RainWeather && typeof global.RainWeather.batch === "function") {
    const map = await global.RainWeather.batch(cs);
    return cs.filter(c => { const w = map.get(c.slug); return !!(w && w.raining); });
  }

  // 单独部署 rain_place 时的退路：自己问一次，口径用 mm > 0
  const url = CFG.api +
    "?latitude="  + cs.map(c => c.lat.toFixed(4)).join(",") +
    "&longitude=" + cs.map(c => c.lon.toFixed(4)).join(",") +
    "&current=rain,showers,precipitation";

  const ctl = typeof AbortController === "function" ? new AbortController() : null;
  const kill = setTimeout(() => ctl && ctl.abort(), CFG.liveTimeoutMs);
  let res;
  try {
    res = await fetch(url, ctl ? { signal: ctl.signal } : undefined);
  } finally { clearTimeout(kill); }
  if (!res.ok) throw new Error("Open-Meteo HTTP " + res.status);

  const body = await res.json();
  const rows = Array.isArray(body) ? body : [body];
  // 多坐标查询按请求顺序返回。条数对不上就不猜，直接当这一档失败。
  if (rows.length !== cs.length) throw new Error("返回条数与请求不符");

  const wet = [];
  for (let i = 0; i < cs.length; i++) {
    const cur = rows[i] && rows[i].current;
    if (!cur) continue;
    // rain + showers 才算「在下雨」；precipitation 含降雪水当量，只在没有前两项时兜底
    const mm = (cur.rain != null || cur.showers != null)
      ? (cur.rain || 0) + (cur.showers || 0)
      : (cur.precipitation || 0);
    if (mm > 0) wet.push(cs[i]);
  }
  return wet;
}

// ───────────────────────────────── 对外

/**
 * @param {object} roster  web_out/index/cities.json 的内容
 * @returns {Promise<{slug,name,timezone,lat,lon,distanceKm,source}|null>}
 *          source: "geolocation" | "raining" | "random"
 */
function resolve(roster) {
  const chain = (async () => {
    try {
      const p = await position();
      const c = nearest(roster, p.lat, p.lon);
      if (c) return Object.assign(c, { source: "geolocation" });
    } catch (e) { /* 落到第 2 档 */ }

    if (CFG.live) {
      try {
        const c = pick(await rainingNow(roster));
        if (c) return Object.assign({}, c, { distanceKm: 0, source: "raining" });
      } catch (e) { /* 落到第 3 档 */ }
    }

    const c = pick(list(roster));
    return c ? Object.assign({}, c, { distanceKm: 0, source: "random" }) : null;
  })();

  const fallback = new Promise(ok => setTimeout(() => {
    const c = pick(list(roster));
    ok(c ? Object.assign({}, c, { distanceKm: 0, source: "random" }) : null);
  }, CFG.deadlineMs));

  return Promise.race([chain, fallback]);
}

global.RainPlace = { CFG, resolve, nearest, distanceKm, list };

})(typeof window !== "undefined" ? window : globalThis);
