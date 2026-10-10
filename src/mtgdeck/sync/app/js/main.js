// Start, router and the parts every screen shares (tab bar, floating action, top bar, scroll positions),
// the service worker (offline start, "Neu laden" for a new version) and pairing links (…/koppeln#CODE).

import { chatThread, claude, unreadCount } from "./claude.js";
import { boot, onChange, state } from "./data.js";
import { applyTheme } from "./forms.js";
import { codeFrom, welcome } from "./pair.js";
import * as scry from "./scry.js";
import { deck, decks, gegner, karten, opponent, paint, tisch } from "./screens.js";
import { clearToast, sheetIsOpen, toast, topbar } from "./ui.js";
import { $, $$, esc, icon, reducedMotion } from "./util.js";

const TABS = ["tisch", "decks", "gegner", "karten", "claude"];
const scrolls = {};
let current = null;  // {key, depth}
let linkCode = null;  // code from a pairing link

function parse() {
  const [tab, id, seg] = location.hash.replace(/^#\/?/, "").split("/").map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { tab: TABS.includes(tab) ? tab : "tisch", id: id || null, seg: seg || null };
}

function screenFor(r) {
  if (state.mode === "none") return welcome(linkCode);
  if (!state.snap) return { html: `<div class="empty">${icon("cloud")}<h3>Lade deine Daten …</h3><p>Einen Moment – beim ersten Mal braucht es Netz.</p></div>`, bar: { title: "Am Tisch" } };
  if (r.tab === "decks" && r.id) return deck(r.id, r.seg || "rule0");
  if (r.tab === "gegner" && r.id) return opponent(r.id);
  if (r.tab === "claude" && r.id) return chatThread(r.id);
  return { tisch, decks, gegner, karten, claude }[r.tab]();
}

function draw({ keepScroll = false } = {}) {
  const r = parse();
  const key = `${state.mode}:${r.tab}/${r.id || ""}`;
  const s = screenFor(r);
  const main = $("#screen");
  main.innerHTML = s.html;
  const fabOn = !!s.fab && state.mode !== "none";
  main.classList.toggle("has-fab", fabOn);
  document.body.classList.toggle("has-fab", fabOn);
  document.body.classList.toggle("no-tabs", state.mode === "none");
  const fab = $("#fab");
  fab.hidden = !fabOn;
  if (fabOn) {
    fab.innerHTML = `<button class="fab" type="button">${icon("plus")}<span>${esc(s.fab.label)}</span></button>`;
    fab.firstElementChild.addEventListener("click", s.fab.run);
  }
  for (const a of $$(".tabbar a")) {
    if (a.dataset.tab === r.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const unread = state.snap ? unreadCount() : 0;  // new answers from Claude
  $(".tabbar [data-tab=claude] .tab-dot").hidden = !unread;
  $(".tabbar [data-tab=claude]").setAttribute("aria-label", unread ? `Claude, ${unread === 1 ? "eine neue Antwort" : `${unread} neue Antworten`}` : "Claude");
  s.bind?.(main);
  scry.hydrate(main);
  paint(main);
  topbar({ ...s.bar, watch: s.bar?.watch ? main.querySelector(s.bar.watch) : null });
  document.title = `${s.bar?.title && r.tab !== "tisch" && state.mode !== "none" ? `${s.bar.title} · ` : ""}Am Tisch`;
  if (!keepScroll) scrollTo(0, scrolls[key] || 0);
  current = { key, depth: r.id ? 1 : 0 };
}

function route() {
  if (current) scrolls[current.key] = scrollY;
  if (current && !sheetIsOpen()) clearToast();  // a hint belongs to the screen it was shown on
  const r = parse();
  const depth = r.id ? 1 : 0;
  const sameTab = current && current.key.split("/")[0] === `${state.mode}:${r.tab}`;
  const dir = !current || !sameTab ? "" : depth > current.depth ? "vt-push" : depth < current.depth ? "vt-pop" : "";
  if (document.startViewTransition && dir && !reducedMotion()) {
    document.documentElement.classList.add(dir);
    document.startViewTransition(() => draw()).finished.finally(() => document.documentElement.classList.remove(dir));
  } else draw();
}

// a tap on the active tab goes back to its top
for (const a of $$(".tabbar a")) a.addEventListener("click", (e) => {
  const r = parse();
  if (a.dataset.tab === r.tab && !r.id) { e.preventDefault(); scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" }); }
});

// data changed: redraw, but never under the user's fingers (typing on the screen, a sheet open)
let pendingRedraw = false;
let lastMode = null;
function redrawSoon() {
  if (state.mode !== lastMode) {  // paired, signed off, demo started/ended: always redraw
    lastMode = state.mode;
    if (state.mode !== "none") linkCode = null;
    else clearToast();  // "Verbunden" and the like no longer apply
    pendingRedraw = false;
    return draw();
  }
  const typing = document.activeElement && $("#screen").contains(document.activeElement) && document.activeElement.matches("input, textarea");
  if (typing || sheetIsOpen()) { pendingRedraw = true; return; }
  pendingRedraw = false;
  draw({ keepScroll: true });
}
document.addEventListener("focusout", () => { if (pendingRedraw) setTimeout(redrawSoon, 50); });
$("#sheet").addEventListener("close", () => { if (pendingRedraw) setTimeout(redrawSoon, 260); });

const linkHash = () => (/^#[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/.test(location.hash) ? codeFrom(location.hash) : null);
addEventListener("hashchange", () => { const code = linkHash(); if (code) takeLink(code); else route(); });
addEventListener("redraw", () => draw({ keepScroll: true }));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

/** A pairing link (…/koppeln#ABCD-EFGH → /app/#ABCD-EFGH). */
function takeLink(code) {
  history.replaceState(null, "", state.mode === "none" ? "#/koppeln" : "#/tisch");
  if (state.mode === "live") { toast("Dieses Handy ist schon verbunden", { sub: "Neu koppeln: Einstellungen → Abmelden" }); route(); return; }
  linkCode = code;
  if (state.mode === "demo") state.mode = "none";  // the preview gives way to the real connection
  lastMode = state.mode;
  draw();
}

// ---------- service worker: start without network, offer new versions ----------
function serviceWorker() {
  if (!("serviceWorker" in navigator)) return;
  // the first install takes over the page quietly (no reload under the user's fingers); a new version reloads
  const hadController = !!navigator.serviceWorker.controller;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadController && !reloading) { reloading = true; location.reload(); } });
  navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).then((reg) => {
    const offer = (worker) => {
      if (!navigator.serviceWorker.controller) return;  // first install: nothing to replace
      toast("Neue Version von „Am Tisch“", { sub: "Deine Eingaben bleiben erhalten.", ms: 15000, action: { label: "Neu laden", run: () => worker.postMessage("skip-waiting") } });
    };
    if (reg.waiting) offer(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      w?.addEventListener("statechange", () => { if (w.state === "installed") offer(w); });
    });
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") reg.update().catch(() => {}); });
  }).catch(() => { /* not available (private mode, http) – the app works without */ });
}

// Android: keep the install prompt for the hint on the start screen
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); window.installPrompt = e; });

async function start() {
  applyTheme();
  const code = linkHash();
  try {
    await boot();
  } catch (err) {
    $("#screen").innerHTML = `<div class="empty">${icon("cloud")}<h3>Keine Daten</h3><p>${esc(err.message)}</p></div>`;
    return;
  }
  lastMode = state.mode;
  onChange(() => current && redrawSoon());
  if (code) takeLink(code);
  else {
    if (!location.hash || (state.mode !== "none" && location.hash.startsWith("#/koppeln"))) history.replaceState(null, "", "#/tisch");
    route();
  }
  serviceWorker();
}
start();
