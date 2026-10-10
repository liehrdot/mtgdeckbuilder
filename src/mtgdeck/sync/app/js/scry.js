// Card data straight from Scryfall (CORS is allowed): names → images and texts, batched via /cards/collection
// (75 per request), cached in local storage; commander and card search incl. German names; German printed text.
// Scryfall asks for 50–100 ms between API requests – one queue keeps that. Images (cards.scryfall.io) are a CDN.

import { load, save } from "./util.js";

const API = "https://api.scryfall.com";
const KEY = "amtisch.cards.v1";
const KEY_DE = "amtisch.cards.de.v1";
const cache = load(KEY, {});
const cacheDe = load(KEY_DE, {});
let saveTimer = null;
const persist = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => { save(KEY, cache); save(KEY_DE, cacheDe); }, 400); };

let chain = Promise.resolve();
function api(path, init) {  // one request at a time, ~100 ms apart
  const run = chain.then(() => fetch(API + path, { ...init, headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) } }));
  chain = run.then(() => new Promise((r) => setTimeout(r, 100)), () => new Promise((r) => setTimeout(r, 100)));
  return run.then((res) => (res.ok ? res.json() : res.status === 404 ? null : Promise.reject(new Error(`Scryfall ${res.status}`))));
}

function pick(card) {
  const faces = card.card_faces || [];
  const uris = card.image_uris || faces[0]?.image_uris || {};
  const text = card.oracle_text ?? faces.map((f) => f.oracle_text).filter(Boolean).join("\n—\n");
  return {
    name: card.name, type: card.type_line || faces.map((f) => f.type_line).join(" // "),
    cost: card.mana_cost ?? faces.map((f) => f.mana_cost).filter(Boolean).join(" // "),
    text, colors: (card.color_identity || []).join(""), art: uris.art_crop, image: uris.normal, small: uris.small,
    back: faces[1]?.image_uris?.normal || null, pt: card.power != null ? `${card.power}/${card.toughness}` : null,
    uri: card.scryfall_uri,
  };
}
const remember = (card, asked) => {
  const info = pick(card);
  cache[info.name] = info;
  if (asked && asked !== info.name) cache[asked] = info;
  const front = info.name.split(" // ")[0];
  if (front !== info.name) cache[front] = info;
  persist();
  return info;
};

export const cached = (name) => cache[name] || null;

// ---- batched lookups by name ----
const waiting = new Map();  // name -> [resolve]
let batchTimer = null;
export function info(name) {
  if (!name) return Promise.resolve(null);
  if (cache[name]) return Promise.resolve(cache[name]);
  return new Promise((resolve) => {
    if (!waiting.has(name)) waiting.set(name, []);
    waiting.get(name).push(resolve);
    clearTimeout(batchTimer);
    batchTimer = setTimeout(flush, 30);
  });
}
async function flush() {
  const names = [...waiting.keys()];
  while (names.length) {
    const part = names.splice(0, 75);
    let data = null;
    try { data = await api("/cards/collection", { method: "POST", body: JSON.stringify({ identifiers: part.map((n) => ({ name: n })) }) }); } catch { /* offline */ }
    const byName = {};
    for (const card of data?.data || []) {
      const info = pick(card);
      byName[info.name.toLowerCase()] = card;
      byName[info.name.split(" // ")[0].toLowerCase()] = card;
    }
    for (const n of part) {
      const card = byName[n.toLowerCase()];
      const result = card ? remember(card, n) : null;
      for (const r of waiting.get(n) || []) r(result);
      waiting.delete(n);
    }
  }
}

// fill <img data-card="Name" data-kind="art|small|image"> once the data is there
export function hydrate(root) {
  for (const img of root.querySelectorAll("img[data-card]:not([src])")) {
    const kind = img.dataset.kind || "art";
    const set = (i) => { if (i?.[kind]) { img.src = i[kind]; img.addEventListener("load", () => img.classList.add("loaded"), { once: true }); } };
    const c = cached(img.dataset.card);
    if (c) set(c); else info(img.dataset.card).then(set);
  }
}

// ---- search ----
const ok = (d) => (d?.data || []);
export async function searchCommanders(q) {
  const data = await api(`/cards/search?q=${encodeURIComponent(`${q} is:commander`)}&include_multilingual=true&unique=cards&order=edhrec`);
  return ok(data).slice(0, 8).map((c) => remember(c));
}
export async function searchCards(q) {
  const [auto, multi] = await Promise.all([
    api(`/cards/autocomplete?q=${encodeURIComponent(q)}`).catch(() => null),
    api(`/cards/search?q=${encodeURIComponent(q)}&include_multilingual=true&unique=cards&order=edhrec`).catch(() => null),
  ]);
  const out = new Map();
  for (const c of ok(multi).slice(0, 10)) { const i = remember(c); out.set(i.name, { name: i.name, de: c.printed_name && c.lang === "de" ? c.printed_name : null, type: i.type }); }
  for (const n of auto?.data || []) if (!out.has(n)) out.set(n, { name: n, type: cache[n]?.type || "" });
  return [...out.values()].slice(0, 15);
}

// German printed name/type/text of a card (null when there is no German printing)
export async function german(name) {
  if (name in cacheDe) return cacheDe[name];
  let de = null;
  try {
    const d = await api(`/cards/search?q=${encodeURIComponent(`!"${name}" lang:de`)}&unique=prints&order=released&dir=desc&include_multilingual=true`);
    const c = ok(d)[0];
    if (c) {
      const f = c.card_faces || [];
      de = { name: c.printed_name || f.map((x) => x.printed_name).filter(Boolean).join(" // "),
             type: c.printed_type_line || f.map((x) => x.printed_type_line).filter(Boolean).join(" // "),
             text: c.printed_text ?? f.map((x) => x.printed_text).filter(Boolean).join("\n—\n") };
    }
  } catch { return null; }  // offline: ask again next time
  cacheDe[name] = de;
  persist();
  return de;
}
