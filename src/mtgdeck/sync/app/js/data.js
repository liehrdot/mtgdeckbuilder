// App state: the read model from the sync server plus the local operations on top of it (the outbox).
// Every change is an operation with its own id ("game.add", "opponent.add", …). It is applied locally at once
// (optimistic), kept in the outbox until the server confirms it, and the server applies the same operation to
// the synced documents (sync/appops.py) – sending one twice changes nothing there.
//
// Modes: "none" (not paired – the welcome screen), "demo" (example data, nothing leaves the phone), "live".

import { load, nowIso, save, uid } from "./util.js";

const KEY_DEVICE = "amtisch.device";
const KEY_BASE = "amtisch.base";
const KEY_OPS = "amtisch.ops";
const KEY_DEMO_OPS = "amtisch.demo.ops";
const KEY_MODE = "amtisch.mode";
const KEY_PREFS = "amtisch.prefs";
const SEND_DELAY_DEMO = 1500;
const TIMEOUT_MS = 12000;
const FLUSH_EVERY = 30000;
const STATUS_EVERY = 60000;
const STATUS_FAST = 3000;  // while a question for Claude is open
const TITLE_LEN = 70;      // like chat.py

export const state = {
  mode: "none", device: null, base: null, snap: null, etag: null, ops: [], prefs: load(KEY_PREFS, {}),
  online: true, revoked: false, lastSync: null, pcs: [], syncing: false, jobs: [],
};
const listeners = new Set();
export const onChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const emit = () => listeners.forEach((fn) => fn(state.snap));
export const isDemo = () => state.mode === "demo";

export function setPref(key, value) {
  state.prefs = { ...state.prefs, [key]: value };
  save(KEY_PREFS, state.prefs);
}

const opsKey = () => (state.mode === "demo" ? KEY_DEMO_OPS : KEY_OPS);
const saveOps = () => save(opsKey(), state.ops);

// ---------- start ------------------------------------------------------------------------------

export async function boot() {
  state.device = load(KEY_DEVICE, null);
  if (state.device) { bootLive(); return; }  // show the stored data at once; news come in the background
  if (load(KEY_MODE, null) === "demo") return bootDemo();
  state.mode = "none";
  emit();
}

function bootLive() {
  state.mode = "live";
  state.ops = load(KEY_OPS, []);
  const cached = load(KEY_BASE, null);
  state.base = cached?.data || null;
  state.etag = cached?.etag || null;
  rebuild();
  startTimers();
  return refresh().then(() => flush());
}

async function bootDemo() {
  state.mode = "demo";
  const res = await fetch("/app/demo.json", { cache: "no-cache" });
  if (!res.ok) throw new Error("Beispieldaten fehlen");
  state.base = shiftDemoDates(await res.json());
  state.ops = load(KEY_DEMO_OPS, []);
  rebuild();
  for (const o of state.ops.filter((x) => x.status === "pending")) demoSend(o);
}

export async function startDemo() { save(KEY_MODE, "demo"); await bootDemo(); }
export function endDemo() { save(KEY_MODE, null); Object.assign(state, { mode: "none", snap: null, base: null, ops: [], jobs: [] }); emit(); }

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
  if (!state.base) { state.snap = null; emit(); return; }
  const snap = structuredClone(state.base);
  for (const op of state.ops) if (op.status !== "failed") apply(snap, op);
  recompute(snap);
  state.snap = snap;
  emit();
}

// ---------- talking to the sync server ---------------------------------------------------------

async function api(path, init = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const headers = { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers || {}) };
    if (state.device?.token) headers.Authorization = `Bearer ${state.device.token}`;
    const res = await fetch(path, { ...init, headers, signal: ctrl.signal, cache: "no-store" });
    if (res.status === 401 && state.mode === "live") revoked();
    return res;
  } finally { clearTimeout(timer); }
}
const detail = async (res) => { try { return (await res.json()).detail || `Fehler ${res.status}`; } catch { return `Fehler ${res.status}`; } };

/** Pair this phone with a code from the PC; then load the data. */
export async function connect(code, name) {
  let res;
  try {
    res = await api("/api/pair/claim", { method: "POST", body: JSON.stringify({ code, name: (name || "Handy").slice(0, 60), kind: "phone" }) });
  } catch {
    throw new Error("Der Server ist gerade nicht erreichbar – Internetverbindung prüfen.");
  }
  if (!res.ok) throw new Error(await detail(res));
  const got = await res.json();
  state.device = { server: location.origin, token: got.token, device_id: got.device_id, name: got.name, paired_at: nowIso() };
  save(KEY_DEVICE, state.device);
  save(KEY_MODE, null);
  state.revoked = false;
  if (state.mode === "demo") { state.base = null; state.etag = null; }
  await bootLive();
  try { await navigator.storage?.persist?.(); } catch { /* not granted – the data is still kept while the app is used */ }
}

/** Sign this phone off on the server and forget everything that belongs to it here. */
export async function disconnect() {
  try { await api(`/api/devices/${encodeURIComponent(state.device?.device_id || "")}`, { method: "DELETE" }); } catch { /* offline: forget locally */ }
  stopTimers();
  for (const k of [KEY_DEVICE, KEY_BASE, KEY_OPS]) save(k, null);
  Object.assign(state, { mode: "none", device: null, base: null, snap: null, etag: null, ops: [], revoked: false, pcs: [], jobs: [] });
  emit();
}

function revoked() {
  stopTimers();
  state.revoked = true;
  state.device = null;
  save(KEY_DEVICE, null);  // the outbox stays: after pairing again it is sent
  state.mode = "none";
  emit();
}

const seqOf = (etag) => Number(String(etag || "").replace(/"/g, "").split("-").pop()) || 0;
let refreshing = null;

/** Load the read model (only when it changed – ETag). */
export function refresh() {
  if (state.mode !== "live") return Promise.resolve();
  if (refreshing) return refreshing;
  state.syncing = true;
  refreshing = (async () => {
    try {
      const res = await api("/api/app/data", { headers: state.etag && state.base ? { "If-None-Match": state.etag } : {} });
      if (res.status === 200) {
        state.base = await res.json();
        state.etag = res.headers.get("etag");
        save(KEY_BASE, { data: state.base, etag: state.etag });
      } else if (res.status !== 304) throw new Error(await detail(res));
      const have = seqOf(state.etag);
      state.ops = state.ops.filter((o) => !(o.status === "sent" && seqOf(o.ack) <= have));  // part of the read model now
      saveOps();
      state.online = true;
      state.lastSync = Date.now();
    } catch {
      state.online = false;
    } finally {
      state.syncing = false;
      refreshing = null;
      rebuild();
    }
  })();
  return refreshing;
}

let flushing = false, backoff = 0, nextTry = 0;
/** Send what waits in the outbox (in order). Confirmed operations stay applied until the next read. */
export async function flush(force = false) {
  if (state.mode !== "live" || flushing) return;
  const pending = state.ops.filter((o) => o.status === "pending");
  if (!pending.length || (!force && Date.now() < nextTry)) return;
  flushing = true;
  let more = false, ok = false;
  try {
    const batch = pending.slice(0, 50);
    const res = await api("/api/app/ops", { method: "POST", body: JSON.stringify({ ops: batch.map(({ id, type, payload, at }) => ({ id, type, payload, at })) }) });
    if (!res.ok) throw new Error(await detail(res));
    const out = await res.json();
    for (const r of out.results) {
      const o = state.ops.find((x) => x.id === r.id);
      if (!o) continue;
      if (r.ok) { o.status = "sent"; o.ack = out.etag; }
      else if (!r.retry) { o.status = "failed"; o.error = r.error; }
    }
    saveOps();
    backoff = 0; nextTry = 0;
    state.online = true;
    more = pending.length > batch.length;
    ok = true;
  } catch {
    if (state.mode === "live") {
      state.online = false;
      backoff = Math.min(backoff ? backoff * 2 : 5000, 300000);
      nextTry = Date.now() + backoff;
    }
  } finally {
    flushing = false;
  }
  if (ok) await refresh(); else rebuild();
  if (more) setTimeout(() => flush(true), 50);
}

async function pollStatus() {
  if (state.mode !== "live") return;
  try {
    const res = await api("/api/app/status");
    if (!res.ok) return;
    const st = await res.json();
    const before = JSON.stringify([state.pcs, state.jobs, state.online]);
    state.pcs = st.pcs || [];
    state.jobs = st.jobs || [];
    state.online = true;
    if (st.etag !== state.etag) await refresh();
    else if (JSON.stringify([state.pcs, state.jobs, state.online]) !== before) emit();  // redraw only when something changed
  } catch { state.online = false; emit(); }
}

let timers = [];
function startTimers() {
  stopTimers();
  const visible = () => document.visibilityState === "visible";
  timers.push(setInterval(() => { if (visible()) flush(); }, FLUSH_EVERY));
  timers.push(setInterval(() => { if (visible()) pollStatus(); }, STATUS_EVERY));
  timers.push(setInterval(() => { if (visible() && questionsOpen()) pollStatus(); }, STATUS_FAST));  // answers come quickly
  pollStatus();
}
function stopTimers() { timers.forEach(clearInterval); timers = []; }
/** Look for news now (app shown again, network back, "Jetzt aktualisieren"). */
export function wake() { if (state.mode === "live") { flush(true); pollStatus(); } }
addEventListener("online", wake);
addEventListener("offline", () => { state.online = false; emit(); });
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") wake(); });  // iOS: no background sync
addEventListener("pageshow", (e) => { if (e.persisted) wake(); });

// ---------- operations -------------------------------------------------------------------------

export const pendingCount = () => state.ops.filter((o) => o.status === "pending").length;
/** The server's state of a question for Claude (``waiting`` / ``running`` with ``progress`` / ``done`` / ``failed``). */
export const jobOf = (messageId) => state.jobs.find((j) => j.id === messageId) || null;
export const questionsOpen = () => state.jobs.some((j) => j.status === "waiting" || j.status === "running")
  || state.ops.some((o) => o.type === "chat.ask" && o.status !== "failed" && !(state.snap?.chats || []).some((c) => c.messages.some((m) => m.id === o.payload.message_id && m.answer)));
export const failedOps = () => state.ops.filter((o) => o.status === "failed");

export function op(type, payload) {
  const entry = { id: uid(), type, payload, at: nowIso(), status: "pending" };
  state.ops.push(entry);
  saveOps();
  rebuild();
  if (state.mode === "demo") demoSend(entry); else flush(true).then(() => { if (type.startsWith("chat.")) pollStatus(); });
  return entry;
}

/** A question for Claude (new conversation without ``chatId``); returns ``{chat_id, message_id}``. */
export function ask(question, { chatId = null, deep = false } = {}) {
  const payload = { chat_id: chatId || uid(), message_id: uid().slice(0, 10), question: question.trim(), deep: !!deep };
  op("chat.ask", payload);
  return payload;
}

/** Take back an open question: not sent yet → it disappears; on the server → ``chat.cancel``. */
export function cancelQuestion(chatId, messageId) {
  const pending = state.ops.find((o) => o.type === "chat.ask" && o.payload.message_id === messageId && o.status === "pending");
  if (pending) { dropOp(pending.id); return; }
  state.jobs = state.jobs.filter((j) => j.id !== messageId);
  op("chat.cancel", { chat_id: chatId, message_id: messageId });
}

function demoSend(entry) {  // the demo "server" accepts after a moment
  setTimeout(() => {
    const o = state.ops.find((x) => x.id === entry.id);
    if (!o || o.status !== "pending" || !navigator.onLine || state.mode !== "demo") return;
    o.status = "sent";
    saveOps();
    rebuild();
    if (o.type === "chat.ask") demoAnswer(o.payload);
  }, SEND_DELAY_DEMO);
}

// the demo "PC": shows what Claude is doing, then answers honestly that this is the preview
const DEMO_STEPS = ["verschafft sich einen Überblick", "sieht sich „Meren Aristocrats“ an", "liest Kartentexte", "schreibt die Antwort"];
function demoAnswer(p) {
  const id = p.message_id;
  state.jobs = [...state.jobs.filter((j) => j.id !== id), { id, chat_id: p.chat_id, status: "running", progress: DEMO_STEPS[0] }];
  emit();
  DEMO_STEPS.forEach((step, i) => setTimeout(() => {
    const j = state.jobs.find((x) => x.id === id);
    if (!j || state.mode !== "demo") return;
    j.progress = step;
    emit();
  }, 1100 * i));
  setTimeout(() => {
    if (state.mode !== "demo" || !state.jobs.some((j) => j.id === id)) return;
    state.jobs = state.jobs.map((j) => (j.id === id ? { ...j, status: "done", progress: null } : j));
    state.ops.push({ id: uid(), type: "chat.demo_answer", at: nowIso(), status: "sent", payload: { chat_id: p.chat_id, message_id: id,
      answer: "**Das ist die Vorschau** – hier antwortet noch kein echter PC.\n\nMit deinem PC verbunden liest Claude für deine Frage "
        + "deine Decks, Partien und Gegnerdecks und antwortet meist nach einer halben bis anderthalb Minuten. Zum Beispiel so:\n\n"
        + "- Gegen schnelle Combo-Decks hält {{meren-aristocrats}} [[Grave Pact]] lieber zurück, bis die Combo-Teile liegen.\n"
        + "- Karten und Decks in der Antwort kannst du antippen.\n\nVerbinden: **Einstellungen → Mit meinem PC verbinden**." } });
    saveOps();
    rebuild();
  }, 1100 * DEMO_STEPS.length);
}

const INVERSE = {
  "game.add": (p) => ({ type: "game.delete", payload: { deck: p.game.deck, id: p.game.id } }),
  "game.delete": (p) => (p.restore ? { type: "game.add", payload: p.restore } : null),
  "opponent.add": (p) => ({ type: "opponent.delete", payload: { id: p.opponent.id } }),
  "opponent.note": (p) => ({ type: "opponent.note_delete", payload: { id: p.id, note_id: p.note_id } }),
  "opponent.update": (p) => (p.prev ? { type: "opponent.update", payload: { id: p.id, ...p.prev } } : null),
};

/** Take an operation back: not sent yet → it disappears; already sent (or already in the read model) → its
 *  opposite is sent. ``entry`` is the operation as ``op()`` returned it. */
export function undo(entry) {
  const o = state.ops.find((x) => x.id === entry.id);
  if (o && (o.status !== "sent" || state.mode === "demo")) {
    state.ops = state.ops.filter((x) => x.id !== entry.id);
    saveOps();
    rebuild();
    return;
  }
  if (!o && state.mode === "demo") return;
  const inv = INVERSE[entry.type]?.(entry.payload);
  if (inv) op(inv.type, inv.payload);
}
export const inverseOf = (type, payload) => INVERSE[type]?.(payload) || null;

export function dropOp(opId) {
  state.ops = state.ops.filter((x) => x.id !== opId);
  saveOps();
  rebuild();
}

export function resetDemo() {
  state.ops = [];
  state.jobs = [];
  saveOps();
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
export const isPending = (gameId) => state.ops.some((o) => o.status === "pending" && o.type === "game.add" && o.payload?.game?.id === gameId);

// ---------- applying operations (mirrors sync/appops.py) ---------------------------------------

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
    if (snap.games.some((g) => g.id === p.game.id && g.deck === p.game.deck)) return;
    const deck = snap.decks.find((d) => d.slug === p.game.deck);
    const ids = [], names = [];
    for (const slot of p.opponents || []) {
      let o = findOpponent(snap, slot);
      if (!o) {
        if (!slot.commander) continue;
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
      if ((p.note || "").trim()) o.notes = [{ id: p.note_id || `${op.id}-n`, text: p.note.trim(), at: op.at }];
      snap.opponents.push(o);
    }
  } else if (op.type === "opponent.delete") {
    snap.opponents = snap.opponents.filter((o) => o.id !== p.id);
  } else if (op.type.startsWith("chat.")) {
    applyChat(snap, op, p);
  } else {
    const o = snap.opponents.find((x) => x.id === p.id);
    if (!o) return;
    if (op.type === "opponent.update") {
      for (const k of ["tags", "player", "label"]) if (k in p) o[k] = p[k];
      o.updated = op.at;
    } else if (op.type === "opponent.note") {
      if (!(o.notes || []).some((n) => n.id === p.note_id)) o.notes = [{ id: p.note_id, text: p.text, at: op.at }, ...(o.notes || [])];
    } else if (op.type === "opponent.note_delete") {
      o.notes = (o.notes || []).filter((n) => n.id !== p.note_id);
    }
  }
}

const chatTitle = (q) => { const t = q.split(/\s+/).join(" ").trim(); return t.length <= TITLE_LEN ? t : `${t.slice(0, TITLE_LEN - 2).trimEnd()} …`; };
function applyChat(snap, op, p) {
  const chats = (snap.chats ||= []);
  let c = chats.find((x) => x.id === p.chat_id);
  if (op.type === "chat.ask") {
    if (!c) { c = { id: p.chat_id, title: chatTitle(p.question), created: op.at, updated: op.at, messages: [] }; chats.push(c); }
    if (!c.messages.some((m) => m.id === p.message_id)) {
      c.messages.push({ id: p.message_id, asked: op.at, question: p.question, answer: null, status: "waiting", deep: !!p.deep, source: "phone",
        pending: op.status === "pending" });
    }
    c.updated = op.at;
  } else if (c && op.type === "chat.cancel") {
    c.messages = c.messages.filter((m) => !(m.id === p.message_id && !m.answer));
    if (!c.messages.length) snap.chats = chats.filter((x) => x !== c);
  } else if (c && op.type === "chat.demo_answer") {
    const m = c.messages.find((x) => x.id === p.message_id);
    if (m && !m.answer) { m.answer = p.answer; delete m.status; }
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
  (snap.chats ||= []).sort((a, b) => String(b.updated || "").localeCompare(String(a.updated || "")));
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
