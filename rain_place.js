
(function (global) {
"use strict";

const CFG = {
  geoTimeoutMs:  6000,
  geoMaxAgeMs:   1800000,
  live:          true,
  liveTimeoutMs: 5000,
  deadlineMs:    9000,
  api: "https://api.open-meteo.com/v1/forecast",
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

async function rainingNow(roster) {
  const cs = list(roster);
  if (!cs.length) return [];

  if (global.RainWeather && typeof global.RainWeather.batch === "function") {
    const map = await global.RainWeather.batch(cs);
    return cs.filter(c => { const w = map.get(c.slug); return !!(w && w.raining); });
  }

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

  if (rows.length !== cs.length) throw new Error("返回条数与请求不符");

  const wet = [];
  for (let i = 0; i < cs.length; i++) {
    const cur = rows[i] && rows[i].current;
    if (!cur) continue;

    const mm = (cur.rain != null || cur.showers != null)
      ? (cur.rain || 0) + (cur.showers || 0)
      : (cur.precipitation || 0);
    if (mm > 0) wet.push(cs[i]);
  }
  return wet;
}

function resolve(roster) {
  const chain = (async () => {
    try {
      const p = await position();
      const c = nearest(roster, p.lat, p.lon);
      if (c) return Object.assign(c, { source: "geolocation" });
    } catch (e) {  }

    if (CFG.live) {
      try {
        const c = pick(await rainingNow(roster));
        if (c) return Object.assign({}, c, { distanceKm: 0, source: "raining" });
      } catch (e) {  }
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
