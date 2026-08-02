
(function (global) {
"use strict";

const CFG = {
  api:         "https://api.open-meteo.com/v1/forecast",
  floorMmH:    0.3,
  horizonDays: 3,
  batchTtlMs:  600000,
  cityTtlMs:   900000,
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

function currentMmH(cur) {
  if (!cur) return null;
  const iv = cur.interval > 0 ? cur.interval : 3600;
  const mm = (cur.rain != null || cur.showers != null)
    ? (cur.rain || 0) + (cur.showers || 0)
    : (cur.precipitation || 0);
  return mm * 3600 / iv;
}

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

let _batch = null;

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

function batchCached() {
  return (_batch && Date.now() - _batch.at < CFG.batchTtlMs) ? _batch.map : null;
}

const _city = new Map();

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

function forget(slug) {
  if (slug) _city.delete(slug); else { _city.clear(); _batch = null; }
}

global.RainWeather = { CFG, batch, batchCached, city, forget, currentMmH, nextRain };

})(typeof window !== "undefined" ? window : globalThis);
