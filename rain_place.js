
(function (global) {
"use strict";

const CFG = {
  geoTimeoutMs:  6000,
  geoMaxAgeMs:   1800000,
  live:          true,
  deadlineMs:    9000,
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

function position() {
  return new Promise((ok, no) => {
    const nav = global.navigator;
    if (!nav || !nav.geolocation) { no(new Error("没有 geolocation")); return; }

    const ask = () => nav.geolocation.getCurrentPosition(
      p  => ok({ lat: p.coords.latitude, lon: p.coords.longitude }),
      e  => no(new Error("定位失败 code=" + (e && e.code))),
      { enableHighAccuracy: false, timeout: CFG.geoTimeoutMs, maximumAge: CFG.geoMaxAgeMs });

    if (nav.permissions && nav.permissions.query) {
      nav.permissions.query({ name: "geolocation" })
        .then(s => { s.state === "denied" ? no(new Error("定位权限已拒绝")) : ask(); })
        .catch(ask);
    } else ask();
  });
}

function playableNow(roster) {
  const h = String(new Date().getUTCHours());
  const ok = new Set(((roster && roster.cities) || [])
    .filter(c => (c.hour_of_day_count || {})[h] > 0)
    .map(c => c.slug));
  return list(roster).filter(c => ok.has(c.slug));
}

function resolve(roster) {
  const chain = (async () => {
    try {
      const p = await position();
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

  // 唯一的改动：chain 先赢时把这个 timer 清掉，原来它从不清除
  return Promise.race([chain, fallback]).finally(() => clearTimeout(id));
}

global.RainPlace = { CFG, resolve, nearest, distanceKm, list };

})(typeof window !== "undefined" ? window : globalThis);
