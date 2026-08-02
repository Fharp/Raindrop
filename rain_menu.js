/* rain_menu.js — 两个界面外壳
 *
 *   1. 左上角城市名 → 下拉，列出名册里全部城市。
 *      此刻没在下雨的城市照样可选（选中之后画面进干燥态，见 rain_bridge）。
 *      在下雨的城市名字左边点一个小点，纯装饰，取不到天气就一个点都不画。
 *   2. 右上角三条横线 → 右侧抽屉。内容留空，等以后往 #drawerBody 里写。
 *
 * 只管开合、排版与事件，不判断天气、不选片、不碰音频。
 * 结构在 index.html 里，这里只负责填内容与接事件。
 */
(function (global) {
"use strict";

const doc = global.document;

/** 当前有没有任何浮层开着。快捷键要据此让路。 */
let openCount = 0;
const anyOpen = () => openCount > 0;

// ───────────────────────────────── 城市下拉

/**
 * @param {object} o {button, panel, list, roster, onPick}
 *        button 左上角那个城市名；panel 浮层；list 浮层里放城市的容器
 *        roster web_out/index/cities.json 的内容
 *        onPick(cityEntry) 选中回调，参数是名册里那一项（含 slug/name/coordinates）
 */
function attachCityPicker(o) {
  const btn = o.button, panel = o.panel, list = o.list;
  let open = false, built = false, current = null;

  /** 名册 → 有场次可放的城市。没有场次的不列，免得选中了放不出声。 */
  function cities() {
    return ((o.roster && o.roster.cities) || []).filter(c => c && (c.events == null || c.events > 0));
  }

  function build() {
    if (built) return;
    built = true;
    const frag = doc.createDocumentFragment();
    for (const c of cities()) {
      const it = doc.createElement("button");
      it.type = "button";
      it.className = "city";
      it.dataset.slug = c.slug;
      const dot = doc.createElement("i");          // 在下雨的那个小点
      const nm  = doc.createElement("span");
      nm.textContent = c.name;
      it.appendChild(dot);
      it.appendChild(nm);
      it.addEventListener("click", () => {
        hide();
        if (o.onPick) o.onPick(c);
      });
      frag.appendChild(it);
    }
    list.appendChild(frag);
    mark();
  }

  /** 按天气给每一项标记。取不到就什么都不标——不写状态字，也不猜。 */
  function mark() {
    const wet = (global.RainWeather && global.RainWeather.batchCached()) || null;
    for (const it of list.children) {
      const w = wet && wet.get(it.dataset.slug);
      it.classList.toggle("wet", !!(w && w.raining));
      it.classList.toggle("dry", !!(wet && (!w || !w.raining)));
      it.classList.toggle("now", it.dataset.slug === current);
    }
  }

  function place() {
    // 用 #place 的实际位置定位：字号是 clamp() 出来的，写死偏移会在某些宽度上错开
    const r = btn.getBoundingClientRect();
    panel.style.left = Math.round(r.left) + "px";
    panel.style.top  = Math.round(r.bottom + 14) + "px";
  }

  function show() {
    if (open) return;
    build();
    mark();
    place();
    open = true; openCount++;
    panel.classList.add("on");
    btn.setAttribute("aria-expanded", "true");
    const now = list.querySelector(".city.now");
    if (now && now.scrollIntoView) now.scrollIntoView({ block: "nearest" });
  }

  function hide() {
    if (!open) return;
    open = false; openCount = Math.max(0, openCount - 1);
    panel.classList.remove("on");
    btn.setAttribute("aria-expanded", "false");
  }

  btn.addEventListener("click", e => { e.stopPropagation(); open ? hide() : show(); });
  btn.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); open ? hide() : show(); }
  });
  panel.addEventListener("click", e => e.stopPropagation());
  doc.addEventListener("click", hide);
  global.addEventListener("resize", () => { if (open) place(); });
  global.addEventListener("scroll", () => { if (open) place(); }, { passive: true });

  return {
    /** 天气刚问回来时叫一下，让小点跟上 */
    refresh() { if (built) mark(); return this; },
    /** 当前是哪座城，用来在列表里描出来 */
    setCurrent(slug) { current = slug || null; if (built) mark(); return this; },
    hide, get open() { return open; },
  };
}

// ───────────────────────────────── 右侧抽屉

/**
 * @param {object} o {button, panel, scrim}
 */
function attachDrawer(o) {
  const btn = o.button, panel = o.panel, scrim = o.scrim;
  let open = false;

  function show() {
    if (open) return;
    open = true; openCount++;
    panel.classList.add("on");
    if (scrim) scrim.classList.add("on");
    btn.classList.add("on");
    btn.setAttribute("aria-expanded", "true");
  }
  function hide() {
    if (!open) return;
    open = false; openCount = Math.max(0, openCount - 1);
    panel.classList.remove("on");
    if (scrim) scrim.classList.remove("on");
    btn.classList.remove("on");
    btn.setAttribute("aria-expanded", "false");
  }

  btn.addEventListener("click", e => { e.stopPropagation(); open ? hide() : show(); });
  if (scrim) scrim.addEventListener("click", hide);
  panel.addEventListener("click", e => e.stopPropagation());

  return { show, hide, toggle() { open ? hide() : show(); }, get open() { return open; } };
}

global.RainMenu = { attachCityPicker, attachDrawer, anyOpen };

})(typeof window !== "undefined" ? window : globalThis);
