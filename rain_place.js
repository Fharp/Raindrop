
(function (global) {
"use strict";

const CFG = {
  geoTimeoutMs:  6000,
  geoMaxAgeMs:   1800000,
  live:          true,
  deadlineMs:    9000,

  // 页面刚打开时不主动弹定位授权框。
  // 没有用户手势的权限请求，Chrome 会降级成静默 UI，多数人根本看不到；
  // 对第一次来的访客也是一个没有任何上下文的打扰。
  //
  // 已经授权过的人（permissions 状态是 granted）照旧直接用，不会弹框。
  // 没授权过的人，在城市下拉里点「此处」时才问——那是一次真正的用户手势。
  //
  // 想恢复旧行为，把这个改成 true。
  askOnLoad: false,
};

const RADIUS_KM = 6371.0088;
const rad = d => d * Math.PI / 180;

function distanceKm(lat1, lon1, lat2, lon2) {
  const dLat = rad(lat2 - lat1), dLon = rad(lon2 - lon1);
  const h = Math.sin(dLat / 2) ** 2 +
            Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

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

function nearest(roster, lat, lon) {
  let best = null, bestD = Infinity;
  for (const c of list(roster)) {
    const d = distanceKm(lat, lon, c.lat, c.lon);
    if (d < bestD) { bestD = d; best = c; }
  }
  return best ? Object.assign({}, best, { distanceKm: bestD }) : null;
}

function pick(arr) { return arr.length ? arr[(Math.random() * arr.length) | 0] : null; }

// "granted" / "denied" / "prompt" / "unknown"
// Safari 一度不支持在 permissions.query 里查 geolocation，那里会落到 unknown。
async function permissionState() {
  const nav = global.navigator;
  if (!nav || !nav.permissions || !nav.permissions.query) return "unknown";
  try {
    const s = await nav.permissions.query({ name: "geolocation" });
    return s.state;
  } catch (e) { return "unknown"; }
}

// force 为真时允许弹授权框；为假时只在已经授权过的情况下取坐标。
async function position(force) {
  const nav = global.navigator;
  if (!nav || !nav.geolocation) throw new Error("没有 geolocation");

  const st = await permissionState();
  if (st === "denied") throw new Error("定位权限已拒绝");
  if (st !== "granted" && !force) throw new Error("未授权，此刻不该弹框");

  return new Promise((ok, no) => {
    nav.geolocation.getCurrentPosition(
      p => ok({ lat: p.coords.latitude, lon: p.coords.longitude }),
      e => no(new Error("定位失败 code=" + (e && e.code))),
      { enableHighAccuracy: false, timeout: CFG.geoTimeoutMs, maximumAge: CFG.geoMaxAgeMs });
  });
}

function playableNow(roster) {
  const h = String(new Date().getUTCHours());
  const ok = new Set(((roster && roster.cities) || [])
    .filter(c => (c.hour_of_day_count || {})[h] > 0)
    .map(c => c.slug));
  return list(roster).filter(c => ok.has(c.slug));
}

// opts.force：由用户手势触发，允许弹授权框
function resolve(roster, opts) {
  const force = !!(opts && opts.force);

  const chain = (async () => {
    try {
      const p = await position(force || CFG.askOnLoad);
      const c = nearest(roster, p.lat, p.lon);
      if (c) return Object.assign(c, { source: "geolocation" });
    } catch (e) {  }

    if (CFG.live) {
      const c = pick(playableNow(roster));
      if (c) return Object.assign({}, c, { distanceKm: 0, source: "raining" });
    }

    const c = pick(list(roster));
    return c ? Object.assign({}, c, { distanceKm: 0, source: "random" }) : null;
  })();

  let id = 0;
  const fallback = new Promise(ok => {
    id = setTimeout(() => {
      const c = pick(list(roster));
      ok(c ? Object.assign({}, c, { distanceKm: 0, source: "random" }) : null);
    }, CFG.deadlineMs);
  });

  // 这个 timer 原来从不清除
  return Promise.race([chain, fallback]).finally(() => clearTimeout(id));
}

// 用户手势路径：允许弹授权框。取不到就返回 null，不做随机兜底——
// 用户明确点了「此处」，塞一个随机城市给他是答非所问。
async function locate(roster) {
  try {
    const p = await position(true);
    const c = nearest(roster, p.lat, p.lon);
    return c ? Object.assign(c, { source: "geolocation" }) : null;
  } catch (e) { return null; }
}

global.RainPlace = { CFG, resolve, locate, nearest, distanceKm, list, permissionState };

})(typeof window !== "undefined" ? window : globalThis);
