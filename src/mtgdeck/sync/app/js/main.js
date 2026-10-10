// Start, router and the parts every screen shares (tab bar, floating action, top bar, scroll positions).

import { boot, onChange, state } from "./data.js";
import { applyTheme } from "./forms.js";
import * as scry from "./scry.js";
import { deck, decks, gegner, karten, opponent, paint, tisch } from "./screens.js";
import { clearToast, sheetIsOpen, topbar } from "./ui.js";
import { $, $$, esc, icon, reducedMotion } from "./util.js";

const TABS = ["tisch", "decks", "gegner", "karten"];
const scrolls = {};
let current = null;  // {key, depth}

function parse() {
  const [tab, id, seg] = location.hash.replace(/^#\/?/, "").split("/").map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  return { tab: TABS.includes(tab) ? tab : "tisch", id: id || null, seg: seg || null };
}

function screenFor(r) {
  if (r.tab === "decks" && r.id) return deck(r.id, r.seg || "rule0");
  if (r.tab === "gegner" && r.id) return opponent(r.id);
  return { tisch, decks, gegner, karten }[r.tab]();
}

function draw({ keepScroll = false } = {}) {
  const r = parse();
  const key = `${r.tab}/${r.id || ""}`;
  const s = screenFor(r);
  const main = $("#screen");
  main.innerHTML = s.html;
  main.classList.toggle("has-fab", !!s.fab);
  document.body.classList.toggle("has-fab", !!s.fab);
  const fab = $("#fab");
  fab.hidden = !s.fab;
  if (s.fab) {
    fab.innerHTML = `<button class="fab" type="button">${icon("plus")}<span>${esc(s.fab.label)}</span></button>`;
    fab.firstElementChild.addEventListener("click", s.fab.run);
  }
  for (const a of $$(".tabbar a")) {
    if (a.dataset.tab === r.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  s.bind?.(main);
  scry.hydrate(main);
  paint(main);
  topbar({ ...s.bar, watch: s.bar?.watch ? main.querySelector(s.bar.watch) : null });
  document.title = `${s.bar?.title && r.tab !== "tisch" ? `${s.bar.title} · ` : ""}Am Tisch`;
  if (!keepScroll) scrollTo(0, scrolls[key] || 0);
  current = { key, depth: r.id ? 1 : 0 };
}

function route() {
  if (current) scrolls[current.key] = scrollY;
  if (current && !sheetIsOpen()) clearToast();  // a hint belongs to the screen it was shown on
  const r = parse();
  const depth = r.id ? 1 : 0;
  const sameTab = current && current.key.split("/")[0] === r.tab;
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
function redrawSoon() {
  const typing = document.activeElement && $("#screen").contains(document.activeElement) && document.activeElement.matches("input, textarea");
  if (typing || sheetIsOpen()) { pendingRedraw = true; return; }
  pendingRedraw = false;
  draw({ keepScroll: true });
}
document.addEventListener("focusout", () => { if (pendingRedraw) setTimeout(redrawSoon, 50); });
$("#sheet").addEventListener("close", () => { if (pendingRedraw) setTimeout(redrawSoon, 260); });

addEventListener("hashchange", route);
addEventListener("redraw", () => draw({ keepScroll: true }));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

async function start() {
  applyTheme();
  try {
    await boot();
  } catch (err) {
    $("#screen").innerHTML = `<div class="empty">${icon("cloud")}<h3>Keine Daten</h3><p>${esc(err.message)}</p></div>`;
    return;
  }
  onChange(() => current && redrawSoon());
  if (!location.hash) history.replaceState(null, "", "#/tisch");
  route();
}
start();
