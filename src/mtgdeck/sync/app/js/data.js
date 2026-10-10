// App state: the read model from the sync server (here: demo data) plus the local operations on top of it.
// Every change is an operation with its own id ("game.add", "opponent.add", …). It is applied locally at once
// (optimistic) and sent later; the server applies the same operation, so both arrive at the same documents.

import { load, nowIso, save, uid } from "./util.js";

const KEY_OPS = "amtisch.ops";
const KEY_PREFS = "amtisch.prefs";
const SEND_DELAY_DEMO = 1500;

export const state = {
  base: null,      // last read model from the server
  snap: null,      // base + local operations
  ops: load(KEY_OPS, []),
  prefs: load(KEY_PREFS, {}),
  demo: true,
};
const listeners = new Set();
export const onChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const emit = () => listeners.forEach((fn) => fn(state.snap));

export function setPref(key, value) {
  state.prefs = { ...state.prefs, [key]: value };
  save(KEY_PREFS, state.prefs);
}

export async function boot() {
  const res = await fetch("/app/demo.json", { cache: "no-cache" });
  if (!res.ok) throw new Error("Demodaten fehlen");
  state.base = shiftDemoDates(await res.json());
  rebuild();
  for (const op of state.ops.filter((o) => o.status === "pending")) send(op);
}

// demo data were written at a fixed time: move everything so the last game was "just now"
const DATE_KEYS = new Set(["played", "updated", "created", "at", "last"]);
function shiftDemoDates(base) {
  const ref = Date.parse(base.demo?.now || "");
  if (!ref) return base;
  const delta = Date.now() - ref;
  const walk = (v) => {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) {
      if (DATE_KEYS.has(k) && typeof x === "string" && !isNaN(Date.parse(x))) v[k] = new Date(Date.parse(x) + delta).toISOString();
      else walk(x);
    }
  };
  walk(base);
  return base;
}

function rebuild() {
  const snap = structuredClone(state.base);
  for (const op of state.ops) apply(snap, op);
  recompute(snap);
  state.snap = snap;
  emit();
}

export const pendingCount = () => state.ops.filter((o) => o.status === "pending").length;

export function op(type, payload) {
  const entry = { id: uid(), type, payload, at: nowIso(), status: "pending" };
  state.ops.push(entry);
  save(KEY_OPS, state.ops);
  rebuild();
  send(entry);
  return entry;
}

// demo: the "server" accepts after a moment; the real outbox comes in step 3c
function send(entry) {
  setTimeout(() => {
    const o = state.ops.find((x) => x.id === entry.id);
    if (!o || o.status !== "pending" || !navigator.onLine) return;
    o.status = "sent";
    save(KEY_OPS, state.ops);
    rebuild();
  }, SEND_DELAY_DEMO);
}

export function undo(opId) {
  state.ops = state.ops.filter((o) => o.id !== opId);
  save(KEY_OPS, state.ops);
  rebuild();
}

export function resetDemo() {
  state.ops = [];
  save(KEY_OPS, state.ops);
  rebuild();
}

// ---------- lookups ----------------------------------------------------------------------------

export const deckBy = (slug) => state.snap?.decks.find((d) => d.slug === slug) || null;
export const oppBy = (id) => state.snap?.opponents.find((o) => o.id === id) || null;
export const shortName = (name) => String(name || "?").split(/,| \/\/ /)[0].trim();
export function oppName(o) {
  if (!o) return "?";
  if (o.label) return o.label;
  const c = o.commanders || [];
  return c.length > 1 ? `${shortName(c[0])} & ${shortName(c[1])}` : shortName(c[0]);
}
export const oppSub = (o) => [o.player, (o.commanders || []).join(" + ")].filter(Boolean).join(" · ");
export function currentDeck() {
  const s = state.snap;
  if (!s?.decks.length) return null;
  return deckBy(state.prefs.deck) || deckBy(s.games[0]?.deck) || s.decks[0];
}
export const gamesOf = (slug) => state.snap.games.filter((g) => g.deck === slug);
export const isPending = (gameId) => state.ops.some((o) => o.status === "pending" && o.payload?.game?.id === gameId);

// ---------- applying operations (mirrors what the server does) ---------------------------------

function findOpponent(snap, slot) {
  if (slot.id) {
    const o = snap.opponents.find((x) => x.id === slot.id);
    if (o) return o;
  }
  const name = (slot.commander || "").trim().toLowerCase();
  if (!name) return null;
  const hits = snap.opponents.filter((x) => (x.commanders || []).some((c) => c.toLowerCase() === name));
  return hits.sort((a, b) => String(b.updated).localeCompare(String(a.updated)))[0] || null;
}

function newOpponent(fields, at) {
  return { id: fields.id || uid().slice(0, 8), commanders: fields.commanders || [], colors: fields.colors || "", label: fields.label || "",
    player: fields.player || "", bracket: null, tags: fields.tags || [], notes: [], created: at, updated: at,
    record: { games: 0, wins: 0, losses: 0, draws: 0, last: null, per_deck: [] } };
}

function apply(snap, op) {
  const p = op.payload || {};
  if (op.type === "game.add") {
    const deck = snap.decks.find((d) => d.slug === p.game.deck);
    const ids = [], names = [];
    for (const slot of p.opponents || []) {
      let o = findOpponent(snap, slot);
      if (!o) {
        o = newOpponent({ id: slot.new_id, commanders: [slot.commander], colors: slot.colors }, op.at);
        snap.opponents.push(o);
      }
      if ((slot.note || "").trim()) o.notes = [{ id: `${op.id}-${o.id}`, text: slot.note.trim(), at: op.at, game_id: p.game.id, deck_slug: p.game.deck }, ...(o.notes || [])];
      o.updated = op.at;
      ids.push(o.id);
      names.push(slot.commander || o.commanders[0]);
    }
    snap.games.unshift({ ...p.game, played: p.game.played || op.at, opponents: names, opponent_ids: ids, version: deck?.version ?? null });
  } else if (op.type === "game.delete") {
    snap.games = snap.games.filter((g) => !(g.id === p.id && g.deck === p.deck));
  } else if (op.type === "opponent.add") {
    if (!snap.opponents.some((o) => o.id === p.opponent.id)) {
      const o = newOpponent(p.opponent, op.at);
      if ((p.note || "").trim()) o.notes = [{ id: `${op.id}-n`, text: p.note.trim(), at: op.at }];
      snap.opponents.push(o);
    }
  } else if (op.type === "opponent.update") {
    const o = snap.opponents.find((x) => x.id === p.id);
    if (o) {
      for (const k of ["tags", "player", "label"]) if (k in p) o[k] = p[k];
      o.updated = op.at;
    }
  } else if (op.type === "opponent.note") {
    const o = snap.opponents.find((x) => x.id === p.id);
    if (o) o.notes = [{ id: p.note_id, text: p.text, at: op.at }, ...(o.notes || [])];
  }
}

function recordOf(games) {
  const r = { games: games.length, wins: 0, losses: 0, draws: 0, last: null };
  for (const g of games) {
    r.wins += g.result === "win"; r.losses += g.result === "loss"; r.draws += g.result === "draw";
    if (!r.last || (g.played || "") > r.last) r.last = g.played;
  }
  return r;
}

function faced(o, g) {
  if ((g.opponent_ids || []).includes(o.id)) return true;
  const linked = (g.opponent_ids || []).filter(Boolean);
  const names = new Set((o.commanders || []).map((c) => c.toLowerCase()));
  return !linked.length && (g.opponents || []).some((n) => names.has(String(n).toLowerCase()));
}

function recompute(snap) {
  snap.games.sort((a, b) => String(b.played).localeCompare(String(a.played)));
  for (const d of snap.decks) d.record = recordOf(snap.games.filter((g) => g.deck === d.slug));
  const names = Object.fromEntries(snap.decks.map((d) => [d.slug, d.name]));
  for (const o of snap.opponents) {
    const mine = snap.games.filter((g) => faced(o, g));
    const per = {};
    for (const g of mine) {
      const p = (per[g.deck] ||= { slug: g.deck, name: names[g.deck] || g.deck, games: 0, wins: 0, losses: 0 });
      p.games++; p.wins += g.result === "win"; p.losses += g.result === "loss";
    }
    o.record = { ...recordOf(mine), per_deck: Object.values(per).sort((a, b) => b.games - a.games) };
  }
  snap.opponents.sort((a, b) => String(b.record.last || b.updated || "").localeCompare(String(a.record.last || a.updated || "")));
  snap.players = [...new Set(snap.opponents.map((o) => o.player).filter(Boolean))].sort((a, b) => a.localeCompare(b, "de"));
}
