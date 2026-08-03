
(function (global) {
"use strict";

const doc = global.document;

let openCount = 0;
const anyOpen = () => openCount > 0;

function attachCityPicker(o) {
  const btn = o.button, panel = o.panel, list = o.list;
  let open = false, built = false, current = null;

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
      const dot = doc.createElement("i");
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

  function mark() {
    for (const it of list.children) {
      const w = o.wetOf ? o.wetOf(it.dataset.slug) : null;
      it.classList.toggle("wet", w === true);
      it.classList.toggle("dry", w === false);
      it.classList.toggle("now", it.dataset.slug === current);
    }
  }

  function place() {

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

    refresh() { if (built) mark(); return this; },

    setCurrent(slug) { current = slug || null; if (built) mark(); return this; },
    hide, get open() { return open; },
  };
}

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
