// Small helpers shared by all modules: escaping, icons, German dates, local storage.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export const icon = (name, cls = "") => `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

export const uid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(16).slice(2) + Date.now().toString(16)).slice(0, 12);

export const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");

// "gerade eben", "vor 5 Min.", "vor 2 Std.", "gestern", "vor 3 Tagen", "12. Okt."
export function ago(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (isNaN(t)) return "";
  const s = (Date.now() - t.getTime()) / 1000;
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  if (t >= today) return `vor ${Math.max(1, Math.round(s / 3600))} Std.`;
  const days = Math.ceil((today - t) / 86400000);
  if (days <= 1) return "gestern";
  if (days < 7) return `vor ${days} Tagen`;
  return t.toLocaleDateString("de-DE", { day: "numeric", month: "short", year: t.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}
export const dateLong = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
export function save(key, value) {
  try { if (value === undefined || value === null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch { /* full or blocked */ }
}

export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// colour identity "BG" / ["B","G"] -> mana pips
export function pips(colors) {
  const list = (Array.isArray(colors) ? colors : String(colors || "").split("")).filter((c) => "WUBRG".includes(c));
  const shown = list.length ? list : ["C"];
  const names = { W: "Weiß", U: "Blau", B: "Schwarz", R: "Rot", G: "Grün", C: "farblos" };
  return `<span class="pips" role="img" aria-label="${shown.map((c) => names[c]).join(", ")}">${shown.map((c) => `<span class="pip ${c}"></span>`).join("")}</span>`;
}

// a soft background in the deck's colours while its artwork loads
const MANA_RGB = { W: "232 220 180", U: "47 125 196", B: "70 60 58", R: "216 69 47", G: "45 140 78", C: "160 155 145" };
export function artFallback(colors) {
  const list = String(Array.isArray(colors) ? colors.join("") : colors || "C").split("").filter((c) => MANA_RGB[c]);
  const a = MANA_RGB[list[0] || "C"], b = MANA_RGB[list[1] || list[0] || "C"];
  return `linear-gradient(135deg, rgb(${a}) 0%, rgb(${b}) 100%)`;
}

export const RESULT_SHORT = { win: "S", loss: "N", draw: "R" };
export const RESULT_TEXT = { win: "Sieg", loss: "Niederlage", draw: "Remis" };

export function recordText(r, { long = false } = {}) {
  if (!r || !r.games) return long ? "noch keine Partie" : "–";
  const parts = [`${r.wins} S`, `${r.losses} N`];
  if (r.draws) parts.push(`${r.draws} R`);
  return parts.join(" · ");
}

export function vibrate(ms = 12) {
  try { if (navigator.vibrate && !reducedMotion()) navigator.vibrate(ms); } catch { /* not supported (iOS) */ }
}

// German genitive of a name: "Tims", "Jonas’", "Max’"
export const genitive = (name) => (/[sßxz]$/i.test(name) ? `${name}’` : `${name}s`);

// "{2}{B}{G}" -> small mana symbols
const MANA_NAMES = { W: "Weiß", U: "Blau", B: "Schwarz", R: "Rot", G: "Grün", C: "farblos", X: "X", T: "tappen" };
export function mana(cost) {
  if (!cost) return "";
  return String(cost).split(" // ").map((part) => part.replace(/\{([^}]+)\}/g, (_, sym) => {
    const parts = sym.split("/");
    const colors = parts.filter((x) => "WUBRGC".includes(x));
    const cls = colors.length === 2 ? `ms hy ${colors[0]}${colors[1]}` : colors.length ? `ms ${colors[0]}` : "ms n";
    const label = parts.map((x) => MANA_NAMES[x] || x).join("/");
    return `<span class="${cls}" title="${label}"><span class="sr">${label}</span><span aria-hidden="true">${colors.length ? "" : esc(sym === "T" ? "↷" : sym)}</span></span>`;
  })).join(" // ");
}
