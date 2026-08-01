/* rain_caption.js — 左上角那两行字
 *
 *   第一行  城市 18:11        此地此刻。城市由 rain_place 决定，时刻走该城时区。
 *   第二行  二零二六年八月一日，上海在下雨。
 *                              正在播放的那一场雨：它自己的日期、它自己的城市。
 *
 * 第二行完全从 audio.score / audio.progress 读，所以换场、换城、拖进度条、
 * 跨过零点，全都会自动跟上，不需要谁来通知它。
 *
 * 只写这两行内容，不写任何状态字（不出现「定位中」「加载中」之类）。
 */
(function (global) {
"use strict";

// 年份逐位念。想用出版惯例的「二〇二六」，把下面这行的第一个字换成 〇 即可。
const YEAR_DIGIT = "零一二三四五六七八九";
const NUM_DIGIT  = "零一二三四五六七八九";

function cnYear(y) {
  let s = "";
  for (const ch of String(y)) s += YEAR_DIGIT[+ch];
  return s;
}

/** 1–31 的月日读法：一 / 十 / 十一 / 二十 / 二十一 / 三十一。 */
function cnSmall(n) {
  if (n < 10) return NUM_DIGIT[n];
  if (n === 10) return "十";
  if (n < 20) return "十" + NUM_DIGIT[n % 10];
  const t = (n / 10) | 0, o = n % 10;
  return NUM_DIGIT[t] + "十" + (o ? NUM_DIGIT[o] : "");
}

const pad = n => (n < 10 ? "0" : "") + n;

/** 本机此刻，24 小时制。
 *  显示的一律是「你自己的表」，不是那座城的当地时间——
 *  选片本来就按 UTC 钟点对齐，放的是此刻那座城正在下的那场雨，
 *  所以「巴黎 02:57」读作：你的 02:57，巴黎正在下雨。 */
function clockNow() {
  const d = new Date();
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
}

/** 正在放的这一场雨，此刻停在哪一天、哪座城。 */
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

/**
 * @param {object} o  { root, city, clock, line, audio }
 *                    audio 可以是播放器本身，也可以是个返回播放器的函数
 *                    （页面里播放器是后建的，用函数拿最省事）
 */
function attach(o) {
  const root  = o.root, cityEl = o.city, clockEl = o.clock, lineEl = o.line;
  const grab  = typeof o.audio === "function" ? o.audio : () => o.audio;

  let shown = "", clockTimer = 0, lineTimer = 0, swapTimer = 0, live = false;

  function paintClock() {
    clockEl.textContent = clockNow();
    // 对齐到下一个整分，而不是每秒空转
    const d = new Date();
    const ms = (60 - d.getSeconds()) * 1000 - d.getMilliseconds();
    clearTimeout(clockTimer);
    clockTimer = setTimeout(paintClock, Math.max(500, ms + 50));
  }

  function paintLine() {
    const s = sentenceOf(grab());
    if (!s || s === shown) return;
    if (!shown) { lineEl.textContent = s; shown = s; return; }
    // 换场时软换字：先落下去，再换，再起来
    lineEl.style.opacity = "0";
    clearTimeout(swapTimer);
    swapTimer = setTimeout(() => {
      lineEl.textContent = s; shown = s; lineEl.style.opacity = "";
    }, 420);
  }

  function wake() {
    if (!live || (global.document && document.hidden)) return;
    paintClock(); paintLine();
  }

  const api = {
    /** 时钟与第二行开始跟着走。此时整块仍是透明的，reveal() 之前看不见。 */
    start() {
      if (live) return api;
      live = true;
      paintClock();
      paintLine();
      lineTimer = setInterval(paintLine, 2000);
      if (global.document) document.addEventListener("visibilitychange", wake);
      return api;
    },

    /** 左上角写哪座城；时钟随之改用该城时区。 */
    /** 左上角写哪座城。时刻始终是本机时间，与这座城的时区无关。 */
    setPlace(name) {
      cityEl.textContent = name || "";
      return api;
    },

    /** 整块淡入。等字体到位再露面，免得先闪一眼系统衬线。 */
    reveal() {
      if (live) { paintClock(); paintLine(); }   // 露面的那一刻内容必须已经是对的
      let done = false;
      const go = () => { if (!done) { done = true; root.classList.add("on"); } };
      if (global.document && document.fonts && document.fonts.ready) {
        document.fonts.ready.then(go, go);
        setTimeout(go, 3000);           // 字体取不到也不能一直不露面
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

global.RainCaption = { attach, cnYear, cnSmall, clockNow, sentenceOf };

})(typeof window !== "undefined" ? window : globalThis);
