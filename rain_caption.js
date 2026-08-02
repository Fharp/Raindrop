/* rain_caption.js — 左上角那两行字
 *
 *   第一行  城市 18:11        此地此刻。城市由 rain_place 决定，取不到写「地球某处」。
 *   第二行  二零二六年八月一日，上海此刻在下雨。
 *                              正在播放的那一场雨：它自己的日期、它自己的城市。
 *           那座城此刻没在下雨时，整行换成
 *           天津的下一场雨将在六小时后开始。
 *
 * 第二行在「在下雨」时完全从 audio.score / audio.progress 读，所以换场、换城、
 * 拖进度条、跨过零点，全都会自动跟上，不需要谁来通知它；
 * 在「没下雨」时由 setDry() 给定，谁判定的、怎么判定的，这里一概不管。
 *
 * 换城要用 setExpect() 先报一声要换到哪。谱是后到的，不报的话，从点下去到
 * 新谱落地这段时间里，第一行已经写着纽约、第二行还在说昆明此刻在下雨——
 * 那不是「还没加载完」，那是一句错话。报了之后这段时间第二行留空。
 *
 * 只写这两行内容，不写任何状态字（不出现「定位中」「加载中」之类）。
 * 拿不到小时数就把第二行留空——宁可不说，也不说错。
 */
(function (global) {
"use strict";

// 年份逐位念。想用出版惯例的「二〇二六」，把下面这行的第一个字换成 〇 即可。
const YEAR_DIGIT = "零一二三四五六七八九";
const NUM_DIGIT  = "零一二三四五六七八九";

const UNKNOWN_PLACE = "地球某处";

function cnYear(y) {
  let s = "";
  for (const ch of String(y)) s += YEAR_DIGIT[+ch];
  return s;
}

/** 1–99 的读法：一 / 十 / 十一 / 二十 / 二十一 / 七十二。月、日、小时数共用。 */
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

/** 没在下雨时的那一行。小时数超出念法范围（>99）或没有，就返回空串。 */
function sentenceDry(name, hours) {
  if (!name || !(hours > 0) || hours > 99) return "";
  return name + "的下一场雨将在" + cnSmall(hours) + "小时后开始。";
}

/**
 * @param {object} o  { root, city, clock, line, audio }
 *                    audio 可以是播放器本身，也可以是个返回播放器的函数
 *                    （页面里播放器是后建的，用函数拿最省事）
 */
function attach(o) {
  const root  = o.root, cityEl = o.city, clockEl = o.clock, lineEl = o.line;
  const grab  = typeof o.audio === "function" ? o.audio : () => o.audio;

  let shown = "", target = "", fading = false;
  let clockTimer = 0, lineTimer = 0, swapTimer = 0, live = false;
  let dry = null;                 // null＝在下雨；否则 {name, hours}
  let expect = null;              // 正在等哪座城的谱（slug）。到位之前第二行留空

  function sentence() {
    if (dry) return sentenceDry(dry.name, dry.hours);
    const a = grab();
    // 谱还是上一座城的：这句话此刻是错的，不许显示
    if (expect && a && a.score && a.score.city !== expect) return "";
    return sentenceOf(a);
  }

  function paintClock() {
    clockEl.textContent = clockNow();
    // 对齐到下一个整分，而不是每秒空转
    const d = new Date();
    const ms = (60 - d.getSeconds()) * 1000 - d.getMilliseconds();
    clearTimeout(clockTimer);
    clockTimer = setTimeout(paintClock, Math.max(500, ms + 50));
  }

  /** 真正把字写进 DOM。淡出结束时调一次，用的一定是当时最新的 target。 */
  function commit() {
    fading = false;
    lineEl.textContent = target;
    shown = target;
    lineEl.style.opacity = target ? "" : "0";
  }

  function paintLine() {
    const s = sentence();
    // 还没有谱、又不是干燥态、也没在等哪座城：什么都不写，别把已有的字擦掉
    if (!s && !dry && !expect) return;
    if (s === target) return;
    target = s;
    if (!shown) { commit(); return; }          // 屏上本来就没字，直接写，靠 CSS 淡入
    // 换场／换城／进出干燥态时软换字：先落下去，再换，再起来。
    // 已经在淡出中就不重开一轮——换城时「清空」与「新谱到位」往往前后脚发生，
    // 重开会把两次淡出叠在一起，看着像闪了一下。
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

    /** 左上角写哪座城。时刻始终是本机时间，与这座城的时区无关。
     *  给空值＝定位没拿到，写「地球某处」。 */
    setPlace(name) {
      cityEl.textContent = name || UNKNOWN_PLACE;
      return api;
    },

    /** 第二行的口径。
     *  @param {null|{name:string,hours:number}} info
     *         null 表示在下雨，第二行回到从谱里读；
     *         给对象表示没在下雨，写「xx 的下一场雨将在 n 小时后开始。」 */
    setDry(info) {
      dry = info || null;
      if (live) paintLine();
      return api;
    },

    get dry() { return dry; },

    /** 要换到哪座城（slug）。在新谱落地之前，第二行留空而不是继续说上一座城。
     *  给 null＝不挑，任何谱都认（?follow=0 的全局随机就是这种）。 */
    setExpect(slug) {
      expect = slug || null;
      if (live) paintLine();
      return api;
    },

    /** 立刻重画一次。谱刚换完时叫一下，不必干等下一次 2 秒轮询。 */
    refresh() {
      if (live) { paintClock(); paintLine(); }
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

global.RainCaption = {
  attach, cnYear, cnSmall, clockNow, sentenceOf, sentenceDry, UNKNOWN_PLACE,
};

})(typeof window !== "undefined" ? window : globalThis);
