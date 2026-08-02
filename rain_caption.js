
(function (global) {
"use strict";

const YEAR_DIGIT = "零一二三四五六七八九";
const NUM_DIGIT  = "零一二三四五六七八九";

const UNKNOWN_PLACE = "地球某处";

function cnYear(y) {
  let s = "";
  for (const ch of String(y)) s += YEAR_DIGIT[+ch];
  return s;
}

function cnSmall(n) {
  if (n < 10) return NUM_DIGIT[n];
  if (n === 10) return "十";
  if (n < 20) return "十" + NUM_DIGIT[n % 10];
  const t = (n / 10) | 0, o = n % 10;
  return NUM_DIGIT[t] + "十" + (o ? NUM_DIGIT[o] : "");
}

const pad = n => (n < 10 ? "0" : "") + n;

function clockNow() {
  const d = new Date();
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
}

function sentenceOf(audio) {
  if (!audio || !audio.score) return "";
  const p = audio.progress;
  const stamp = (p && p.local) ||
                (audio.score.summary && audio.score.summary.start_local) || "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(stamp);
  if (!m) return "";
  const city = audio.score.city_name || audio.score.city || "";
  if (!city) return "";
  return cnYear(+m[1]) + "年" + cnSmall(+m[2]) + "月" + cnSmall(+m[3]) + "日，" +
         city + "此刻在下雨。";
}

function sentenceDry(name, hours) {
  if (!name || !(hours > 0) || hours > 99) return "";
  return name + "的下一场雨将在" + cnSmall(hours) + "小时后开始。";
}

function attach(o) {
  const root  = o.root, cityEl = o.city, clockEl = o.clock, lineEl = o.line;
  const grab  = typeof o.audio === "function" ? o.audio : () => o.audio;

  let shown = "", target = "", fading = false;
  let clockTimer = 0, lineTimer = 0, swapTimer = 0, live = false;
  let dry = null;
  let expect = null;

  function sentence() {
    if (dry) return sentenceDry(dry.name, dry.hours);
    const a = grab();

    if (expect && a && a.score && a.score.city !== expect) return "";
    return sentenceOf(a);
  }

  function paintClock() {
    clockEl.textContent = clockNow();

    const d = new Date();
    const ms = (60 - d.getSeconds()) * 1000 - d.getMilliseconds();
    clearTimeout(clockTimer);
    clockTimer = setTimeout(paintClock, Math.max(500, ms + 50));
  }

  function commit() {
    fading = false;
    lineEl.textContent = target;
    shown = target;
    lineEl.style.opacity = target ? "" : "0";
  }

  function paintLine() {
    const s = sentence();

    if (!s && !dry && !expect) return;
    if (s === target) return;
    target = s;
    if (!shown) { commit(); return; }

    if (fading) return;
    fading = true;
    lineEl.style.opacity = "0";
    clearTimeout(swapTimer);
    swapTimer = setTimeout(commit, 420);
  }

  function wake() {
    if (!live || (global.document && document.hidden)) return;
    paintClock(); paintLine();
  }

  const api = {

    start() {
      if (live) return api;
      live = true;
      paintClock();
      paintLine();
      lineTimer = setInterval(paintLine, 2000);
      if (global.document) document.addEventListener("visibilitychange", wake);
      return api;
    },

    setPlace(name) {
      cityEl.textContent = name || UNKNOWN_PLACE;
      return api;
    },

    setDry(info) {
      dry = info || null;
      if (live) paintLine();
      return api;
    },

    get dry() { return dry; },

    setExpect(slug) {
      expect = slug || null;
      if (live) paintLine();
      return api;
    },

    refresh() {
      if (live) { paintClock(); paintLine(); }
      return api;
    },

    reveal() {
      if (live) { paintClock(); paintLine(); }
      let done = false;
      const go = () => { if (!done) { done = true; root.classList.add("on"); } };
      if (global.document && document.fonts && document.fonts.ready) {
        document.fonts.ready.then(go, go);
        setTimeout(go, 3000);
      } else go();
      return api;
    },

    stop() {
      live = false;
      clearTimeout(clockTimer); clearTimeout(swapTimer); clearInterval(lineTimer);
      if (global.document) document.removeEventListener("visibilitychange", wake);
      return api;
    },
  };
  return api;
}

global.RainCaption = {
  attach, cnYear, cnSmall, clockNow, sentenceOf, sentenceDry, UNKNOWN_PLACE,
};

})(typeof window !== "undefined" ? window : globalThis);
