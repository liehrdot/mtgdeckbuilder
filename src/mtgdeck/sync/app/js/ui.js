// Interface building blocks: one sheet at a time (drag to dismiss, pages inside it, stays above the keyboard),
// one toast at a time (with an action such as "Rückgängig"), the full-screen Rule 0 view, and the back button
// (Android/browser back closes the sheet or the full screen instead of leaving the page).

import { $, esc, icon, reducedMotion } from "./util.js";

// ---------- back button closes overlays ----------
const overlays = [];
function openOverlay(close) {
  overlays.push(close);
  history.pushState({ overlay: overlays.length }, "");
}
function closeOverlay() {
  if (overlays.length && history.state?.overlay) history.back();  // popstate does the closing
  else overlays.pop()?.();
}
addEventListener("popstate", () => { overlays.pop()?.(); });

// ---------- the visible area: sheets sit on its bottom edge, above the keyboard ----------
// iOS can make the layout viewport taller than the screen (above all in a Home Screen app), and the keyboard only
// shrinks the visual viewport – so sheets are placed from visualViewport (--vvt, --vvh), never with bottom: 0.
const vv = window.visualViewport;
let fullH = 0, fullW = 0;
function onViewport() {
  if (!vv) return;
  const root = document.documentElement.style;
  root.setProperty("--vvt", `${vv.offsetTop}px`);
  root.setProperty("--vvh", `${vv.height}px`);
  if (vv.width !== fullW) { fullW = vv.width; fullH = 0; }  // turned: measure again
  fullH = Math.max(fullH, vv.height);
  document.body.classList.toggle("kb-open", fullH - vv.height > 120);
}
vv?.addEventListener("resize", onViewport);
vv?.addEventListener("scroll", onViewport);
onViewport();

// content runs under the status bar of an installed app: once scrolled, a quiet backdrop keeps the clock readable
const onScroll = () => document.body.classList.toggle("scrolled", scrollY > 2);
addEventListener("scroll", onScroll, { passive: true });

// ---------- sheet ----------
const dlg = () => $("#sheet");
let sheetClose = null;  // callback of the open sheet
let pages = [];         // stack of render functions inside the sheet

export const sheetIsOpen = () => dlg().open;

/** Open a sheet. ``render(api)`` returns ``{title, left, right, body, foot, bind(el)}``. */
export function openSheet(render, { full = false, onClose = null } = {}) {
  const d = dlg();
  if (d.open) { finishClose(true); }
  pages = [render];
  sheetClose = onClose;
  d.className = `sheet${full ? " full" : ""} entering`;
  d.removeAttribute("style");
  draw("");
  d.showModal();
  document.body.classList.add("sheet-open");
  document.documentElement.style.overflow = "hidden";
  const enter = () => d.classList.remove("entering");
  requestAnimationFrame(() => requestAnimationFrame(enter));
  setTimeout(enter, 120);  // never left hidden below the screen, even without animation frames
  openOverlay(() => finishClose());
}

export function closeSheet() { if (dlg().open) closeOverlay(); }

function finishClose(immediate = false) {
  const d = dlg();
  if (!d.open) return;
  const done = () => {
    d.close();
    d.classList.remove("closing");
    d.innerHTML = "";
    document.body.classList.remove("sheet-open");
    document.documentElement.style.overflow = "";
    const cb = sheetClose;
    sheetClose = null;
    cb?.();
  };
  if (immediate || reducedMotion()) return done();
  d.style.translate = "";
  d.classList.add("closing");
  setTimeout(done, 240);
}

/** Show another page inside the open sheet (``back`` = the slide direction). */
export function pushPage(render) { pages.push(render); draw("page"); }
export function popPage() { if (pages.length > 1) { pages.pop(); draw("page back"); } }
export function redraw() { draw(""); }

function draw(anim) {
  const d = dlg();
  const p = pages[pages.length - 1]({ close: closeSheet, push: pushPage, pop: popPage, redraw });
  const scrollKeep = anim ? 0 : d.querySelector(".sheet-body")?.scrollTop || 0;
  d.innerHTML = `
    <div class="sheet-head">
      <button class="grabber" type="button" aria-label="Schließen"></button>
      <div class="sheet-bar">
        <div>${p.left ?? `<button class="btn plain" type="button" data-sheet-close>Abbrechen</button>`}</div>
        <h2 class="title" id="sheet-title">${esc(p.title || "")}</h2>
        <div>${p.right ?? ""}</div>
      </div>
    </div>
    <div class="sheet-body ${anim}">${p.body || ""}</div>
    ${p.foot ? `<div class="sheet-foot">${p.foot}</div>` : ""}`;
  const body = d.querySelector(".sheet-body");
  body.scrollTop = scrollKeep;
  if (anim) body.classList.add(...anim.split(" "));
  d.querySelector("[data-sheet-close]")?.addEventListener("click", closeSheet);
  d.querySelector(".grabber").addEventListener("click", closeSheet);
  d.querySelector("[data-sheet-back]")?.addEventListener("click", popPage);
  dragToClose(d, d.querySelector(".sheet-head"));
  p.bind?.(d);
}

function dragToClose(d, handle) {
  let startY = null, lastY = 0, lastT = 0, v = 0;
  handle.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button:not(.grabber), a, input")) return;
    startY = e.clientY; lastY = e.clientY; lastT = e.timeStamp; v = 0;
    handle.setPointerCapture(e.pointerId);
    d.style.transition = "none";
  });
  handle.addEventListener("pointermove", (e) => {
    if (startY === null) return;
    const dy = Math.max(0, e.clientY - startY);
    v = (e.clientY - lastY) / Math.max(1, e.timeStamp - lastT);
    lastY = e.clientY; lastT = e.timeStamp;
    d.style.translate = `0 ${dy < 0 ? 0 : dy}px`;
  });
  const end = (e) => {
    if (startY === null) return;
    const dy = e.clientY - startY;
    startY = null;
    d.style.transition = "";
    if (dy > 110 || v > 0.7) closeSheet();
    else d.style.translate = "";
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
}

// Esc and taps on the dimmed area close too
addEventListener("DOMContentLoaded", () => {
  const d = dlg();
  d.addEventListener("cancel", (e) => { e.preventDefault(); closeSheet(); });
  d.addEventListener("click", (e) => {
    if (e.target !== d) return;
    const r = d.getBoundingClientRect();
    if (e.clientY < r.top) closeSheet();
  });
});

// ---------- toast ----------
let toastTimer = null;
export function clearToast() { clearTimeout(toastTimer); $("#toasts").innerHTML = ""; }
export function toast(msg, { sub = "", action = null, ms = 4500, ok = false } = {}) {
  const box = $("#toasts");
  clearTimeout(toastTimer);
  box.innerHTML = `<div class="toast">${ok ? `<span class="ok">${icon("check", "xs")}</span>` : ""}
    <div class="msg">${esc(msg)}${sub ? `<small>${esc(sub)}</small>` : ""}</div>
    ${action ? `<button class="btn" type="button">${esc(action.label)}</button>` : ""}</div>`;
  const el = box.firstElementChild;
  const hide = () => { el.classList.add("out"); setTimeout(() => el.remove(), 200); };
  el.querySelector("button")?.addEventListener("click", () => { hide(); action.run(); });
  toastTimer = setTimeout(hide, action ? Math.max(ms, 8000) : ms);
}

// ---------- full screen (Rule 0 to hand across the table) ----------
let wake = null;
export function present(html, { actions = "", bind = null, onClose = null } = {}) {
  const el = $("#present");
  el.innerHTML = `<div class="present" role="dialog" aria-modal="true" aria-label="Rule 0"><div class="inner">${html}</div></div>
    <div class="present-actions">${actions}<button class="btn primary" type="button" data-present-close>Schließen</button></div>`;
  el.hidden = false;
  document.documentElement.style.overflow = "hidden";
  el.querySelector("[data-present-close]").addEventListener("click", closeOverlay);
  bind?.(el);
  navigator.wakeLock?.request("screen").then((w) => { wake = w; }, () => {});
  openOverlay(() => {
    el.hidden = true;
    el.innerHTML = "";
    document.documentElement.style.overflow = "";
    wake?.release?.();
    wake = null;
    onClose?.();
  });
}

// ---------- compact top bar ----------
let observer = null;
/** ``title`` for the bar; ``watch`` = element whose disappearance shows the bar; ``back`` = {href, label}. */
export function topbar({ title = "", watch = null, back = null, always = false } = {}) {
  const bar = $("#topbar");
  observer?.disconnect();
  bar.innerHTML = `${back ? `<a class="back-btn" href="${back.href}">${icon("chev-l")}<span>${esc(back.label)}</span></a>` : `<span class="spacer"></span>`}
    <span class="title">${esc(title)}</span><span class="spacer"></span>`;
  const show = (on) => { bar.classList.toggle("show", on); bar.setAttribute("aria-hidden", on ? "false" : "true"); };
  show(always);
  onScroll();
  if (watch && !always) {
    // shown while the watched title is above the bar (not below the screen); the bar's height includes the status bar
    observer = new IntersectionObserver(([e]) => show(!e.isIntersecting && e.boundingClientRect.top < (e.rootBounds?.top ?? 0)),
      { rootMargin: `-${bar.offsetHeight || 52}px 0px 0px 0px` });
    observer.observe(watch);
  }
}
