"use strict";

// ============================================================================================
// helpers
// ============================================================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const store = {  // per-browser conveniences only (view mode); failures are harmless
  get(k) { try { return localStorage.getItem("mtgdeck." + k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem("mtgdeck." + k, v); } catch { /* ignore */ } },
};

const fmtNum = (v, suffix = "", digits = null) =>
  v === null || v === undefined ? "–" : `${digits === null ? v : Number(v).toFixed(digits)}${suffix}`;
const fmtPower = (v) => fmtNum(v, "", 1);
const fmtPrice = (v, cur = "") => fmtNum(v, cur ? " " + cur : "", 2);
const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso.replace("T", " ").slice(0, 16) : d.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
};
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;

// non-blocking notifications (success, hints, errors)
function toast(text, kind = "info", ms = 4500) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === "error" ? "alert" : "check")}<span>${esc(text)}</span>`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? ms + 3000 : ms);
}
const fail = (err) => toast(err.message || String(err), "error");

// confirm / prompt as a proper dialog. Returns true (confirm), the entered text (prompt) or null.
function ask({ title, text = "", value = null, ok = "OK", danger = false }) {
  const dlg = $("#ask-dialog"), input = $("#ask-input");
  $("#ask-title").textContent = title;
  $("#ask-text").textContent = text;
  $("#ask-text").hidden = !text;
  input.hidden = value === null;
  input.value = value ?? "";
  $("#ask-ok").textContent = ok;
  $("#ask-ok").classList.toggle("danger-fill", danger);
  return new Promise((resolve) => {
    const done = (result) => { resolve(result); if (dlg.open) dlg.close(); };
    $("#ask-cancel").onclick = () => done(null);
    $("#ask-form").onsubmit = (e) => { e.preventDefault(); done(value === null ? true : input.value.trim() || null); };
    dlg.onclose = () => resolve(null);
    dlg.showModal();
    if (value !== null) { input.focus(); input.select(); } else $("#ask-ok").focus();
  });
}

// dialogs close on a click outside their box (Esc works natively)
for (const dlg of $$("dialog")) {
  dlg.addEventListener("click", (e) => {
    if (e.target !== dlg) return;
    const r = dlg.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dlg.close();
  });
}

// ============================================================================================
// state + routing  (#/new · #/job · #/deck/<slug>/<tab> · #/blacklist · #/settings)
// ============================================================================================
let brackets = [];
let currentDeck = null;
let deckIndex = [];
const VIEWS = ["new", "job", "deck", "collection", "glossary", "blacklist", "settings"];
const TABS = ["karten", "anleitung", "testen", "anpassen", "fragen", "verlauf", "drucken"];
let lastView = null;

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  const view = VIEWS.includes(parts[0]) ? parts[0] : "new";
  return { view, slug: parts[1] || null, tab: parts[2] || null };
}

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function route() {
  const r = parseHash();
  if (r.view === "deck" && !r.slug) return go("#/new");
  if (r.view === "deck" && (!currentDeck || currentDeck.slug !== r.slug)) {
    if (!(await openDeck(r.slug))) return;
  }
  for (const v of $$(".view")) v.hidden = v.dataset.view !== r.view;
  if (r.view === "deck") selectTab(r.tab || "karten", false);
  if (r.view === "settings") refreshDbStatus();
  if (r.view === "collection") loadCollection();
  if (r.view === "glossary") showGlossary(r.slug);
  if (r.view === "job") $("#job-empty").hidden = !!(jobInfo && jobInfo.slot === "#job-slot-main" && !jobInfo.dismissed);
  placeJobPanel();
  setNavOpen(false);
  markNav(r);
  const key = r.view + (r.slug || "");
  if (key !== lastView) {
    window.scrollTo(0, 0);
    if (lastView !== null) $("#main").focus({ preventScroll: true });  // screen readers start at the new content
    lastView = key;
  }
  document.title = (r.view === "deck" && currentDeck ? currentDeck.name
    : { new: "Neues Deck", job: "Claude arbeitet", collection: "Meine Sammlung", glossary: "Glossar", blacklist: "Blacklist", settings: "Einstellungen" }[r.view]) + " · Commander Deckbuilder";
}
window.addEventListener("hashchange", route);

function markNav(r = parseHash()) {
  for (const a of $$("#deck-list a")) {
    if (r.view === "deck" && a.dataset.slug === r.slug) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  for (const a of $$(".nav-bottom a")) {
    if (a.dataset.nav === r.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

// mobile: the sidebar becomes a drawer
function setNavOpen(open) {
  document.body.classList.toggle("nav-open", open);
  $("#scrim").hidden = !open;
  $("#nav-toggle").setAttribute("aria-expanded", String(open));
  $("#nav-toggle").setAttribute("aria-label", open ? "Menü schließen" : "Menü öffnen");
}
$("#nav-toggle").addEventListener("click", () => setNavOpen(!document.body.classList.contains("nav-open")));
$("#scrim").addEventListener("click", () => setNavOpen(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("nav-open")) setNavOpen(false); });

// ============================================================================================
// sidebar: saved decks
// ============================================================================================
async function refreshDeckList() {
  deckIndex = await api("/api/decks").catch(() => []);
  $("#deck-count").textContent = deckIndex.length || "";
  $("#deck-filter").hidden = deckIndex.length < 8;
  renderDeckList();
}

function renderDeckList() {
  const q = $("#deck-filter").value.trim().toLowerCase();
  const list = deckIndex.filter((d) => !q || `${d.name} ${d.commanders.join(" ")}`.toLowerCase().includes(q));
  $("#deck-list").innerHTML = list.length ? list.map((d) => `<li>
      <a href="#/deck/${enc(d.slug)}" data-slug="${esc(d.slug)}">
        <span class="name">${d.valid === false ? '<span class="dot bad" title="nicht legal"></span><span class="sr-only">nicht legal:</span>' : ""}<span>${esc(d.name)}</span></span>
        <span class="meta">${esc(d.commanders.join(" + "))} · ${esc(d.level || `Bracket ${d.bracket ?? "?"}`)}${d.proxy ? " · Proxy" : ""}</span>
      </a></li>`).join("")
    : `<li class="empty-inline">${deckIndex.length ? "Kein Treffer." : "Noch keine Decks – starte mit „Neues Deck“."}</li>`;
  markNav();
}
$("#deck-filter").addEventListener("input", renderDeckList);

// ============================================================================================
// new deck / commander finder
// ============================================================================================
const buildForm = $("#build-form");

async function initBrackets() {
  brackets = await api("/api/brackets");
  const options = (name, checked) => brackets.map((b) => `
    <label title="${esc(b.summary)}"><input type="radio" name="${name}" value="${b.number}" ${b.number === checked ? "checked" : ""}>
    <span><b>${b.number}</b><small>${esc(b.name)}</small></span></label>`).join("");
  $("#bracket-options").innerHTML = options("bracket", 3);
  $("#retune-brackets").innerHTML = options("rbracket", 0);
  $("#bracket-options").addEventListener("change", showBracketDesc);
  showBracketDesc();
}

function showBracketDesc() {
  const n = Number(new FormData(buildForm).get("bracket"));
  const b = brackets.find((x) => x.number === n);
  $("#bracket-desc").innerHTML = b ? `<b>${esc(b.name)}:</b> ${esc(b.summary)}` : "";
}

function setMode(mode) {
  buildForm.dataset.mode = mode;
  buildForm.elements.mode.value = mode;
  // only the fields of the active mode take part in validation and submission
  for (const el of buildForm.querySelectorAll(".mode-build :is(input, textarea, select)")) el.disabled = mode !== "build";
  for (const el of buildForm.querySelectorAll(".mode-find :is(input, textarea, select)")) el.disabled = mode !== "find";
}
buildForm.addEventListener("change", (e) => { if (e.target.name === "mode") setMode(e.target.value); });

$("#partner-toggle").addEventListener("click", () => {
  $("#partner-field").hidden = false;
  $("#partner-toggle").hidden = true;
  $("#partner").focus();
});

$("#proxy").addEventListener("change", () => {
  const on = $("#proxy").checked;
  $("#budget-row").classList.toggle("off", on);
  $("#budget").disabled = on;
  buildForm.elements.currency.disabled = on;
  $("#budget").placeholder = on ? "egal (Proxy)" : "unbegrenzt";
});

function wireAutocomplete(input, list, onPick) {
  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) return;
    try {
      const names = await api(`/api/autocomplete?q=${enc(q)}`);
      list.innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");
    } catch { /* ignore */ }
  }, 200));
  input.addEventListener("change", () => onPick && onPick(input.value));
}

async function previewCommander(name) {
  const box = $("#commander-preview");
  if (!name) { box.innerHTML = ""; return; }
  try {
    const c = await api(`/api/card?name=${enc(name)}`);
    box.innerHTML = c.image ? `<img src="${esc(c.image)}" alt="${esc(c.name)}">` : "";
    if (c.name && c.name !== name) $("#commander").value = c.name;
  } catch { box.innerHTML = ""; }
}

function buildSettings() {
  const f = Object.fromEntries(new FormData(buildForm));
  const proxy = $("#proxy").checked;
  return {
    bracket: Number(f.bracket), currency: buildForm.elements.currency.value, proxy,
    budget: !proxy && f.budget ? Number(f.budget) : null, model: f.model || null,
  };
}

buildForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (currentJob) { toast("Es läuft schon ein Auftrag – warte kurz oder brich ihn ab.", "error"); return; }
  const f = Object.fromEntries(new FormData(buildForm));
  try {
    if (buildForm.dataset.mode === "find") {
      const body = { ...buildSettings(), prompt: f.prompt, count: Number(f.count) };
      const { job } = await api("/api/find-commander", { method: "POST", body });
      $("#suggestions").hidden = true;
      startJob(job, "Claude sucht passende Commander", { kind: "finder", slot: "#finder-job-slot", route: "#/new" });
      $("#finder-job-slot").scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      const body = {
        ...buildSettings(), commander: f.commander, partner: f.partner || null,
        strategy: f.strategy || null, notes: f.notes || null, prefer_collection: !!f.prefer_collection,
        profile: readProfile($("#build-profile .profile-fields")),
      };
      const { job } = await api("/api/build", { method: "POST", body });
      startJob(job, `Claude baut ${body.commander}${body.partner ? " + " + body.partner : ""}`, { kind: "build", slot: "#job-slot-main", route: "#/job" });
      go("#/job");
    }
  } catch (err) { fail(err); }
});

const COLOR_NAMES = { W: "Weiß", U: "Blau", B: "Schwarz", R: "Rot", G: "Grün" };
let suggestions = [];

function renderSuggestions(items) {
  suggestions = items || [];
  $("#suggestions").hidden = !suggestions.length;
  const priceKey = buildForm.elements.currency.value === "usd" ? "price_usd" : "price_eur";
  $("#suggestion-list").innerHTML = suggestions.map((s, i) => {
    const colors = (s.color_identity || []).map((c) => COLOR_NAMES[c] || c).join(", ") || "Farblos";
    return `<article class="suggestion">
      ${s.image ? `<img src="${esc(s.image)}" alt="${esc(s.name)}" loading="lazy">` : ""}
      <h3>${esc(s.name)}${s.partner ? " + " + esc(s.partner) : ""}</h3>
      <p class="muted small">${esc(s.archetype || "")} · ${esc(colors)}${s[priceKey] ? " · " + esc(s[priceKey]) : ""}</p>
      <p>${esc(s.why || "")}</p>
      ${s.bracket_fit ? `<p class="muted small">${esc(s.bracket_fit)}</p>` : ""}
      <div class="actions">
        <button type="button" class="btn" data-use="${i}">Übernehmen</button>
        <button type="button" class="btn primary" data-build="${i}">Deck bauen</button>
      </div>
    </article>`;
  }).join("");
  if (suggestions.length) $("#suggestions").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#suggestion-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-use], button[data-build]");
  if (!btn) return;
  const s = suggestions[Number(btn.dataset.use ?? btn.dataset.build)];
  setMode("build");
  $("#commander").value = s.name;
  $("#partner").value = s.partner || "";
  if (s.partner) { $("#partner-field").hidden = false; $("#partner-toggle").hidden = true; }
  const strategy = buildForm.elements.strategy;
  if (s.strategy && !strategy.value) strategy.value = s.strategy;
  previewCommander(s.name);
  if (btn.dataset.build !== undefined) buildForm.requestSubmit();
  else {
    buildForm.scrollIntoView({ behavior: "smooth", block: "start" });
    toast(`${s.name} übernommen – prüf noch Bracket und Budget.`);
  }
});

// ---------- power profile (sub-tier, house rules, style) ----------
const TIER_LABELS = { low: "unteres", mid: "mittleres", high: "oberes" };
const TIER_ORDER = ["low", "mid", "high"];
const TIER_CENTER = { low: 0.17, mid: 0.5, high: 0.83 };

$$(".profile-fields").forEach((el) => el.appendChild($("#profile-template").content.cloneNode(true)));

function readProfile(root) {
  const q = (n) => root.querySelector(`[name="${n}"]`);
  const num = (n) => (q(n).value === "" ? null : Number(q(n).value));
  const p = {
    tier: root.querySelector('[name="tier"]:checked')?.value || null,
    max_game_changers: num("max_game_changers"),
    max_tutors: num("max_tutors"),
    allow_two_card_combos: q("no_combos").checked ? false : null,
    allow_extra_turns: q("no_extra_turns").checked ? false : null,
    allow_mass_land_denial: q("no_mld").checked ? false : null,
    style: q("style").value.trim(),
  };
  return Object.values(p).some((v) => v !== null && v !== "") ? p : null;
}

function setProfile(root, p = {}) {
  p = p || {};
  const q = (n) => root.querySelector(`[name="${n}"]`);
  root.querySelector(`[name="tier"][value="${p.tier || ""}"]`).checked = true;
  q("max_game_changers").value = p.max_game_changers ?? "";
  q("max_tutors").value = p.max_tutors ?? "";
  q("no_combos").checked = p.allow_two_card_combos === false;
  q("no_extra_turns").checked = p.allow_extra_turns === false;
  q("no_mld").checked = p.allow_mass_land_denial === false;
  q("style").value = p.style || "";
}

function levelText(bracket, tier) {
  return tier ? `${TIER_LABELS[tier]} Bracket ${bracket}` : `Bracket ${bracket}`;
}

// ============================================================================================
// jobs: one Claude Code run (or print/autofill runner) at a time, streamed via SSE
// ============================================================================================
let currentJob = null;   // id of the running job
let jobInfo = null;      // { id, kind, slot, route, slug, title, started, error, dismissed }
let eventSource = null;
let elapsedTimer = null;

const TOOL_LABELS = {
  Skill: "lädt die Deckbau-Anleitung", Read: "liest eine Referenz", Glob: "sieht in den Anleitungen nach",
  Grep: "sieht in den Anleitungen nach", ToolSearch: "bereitet die Werkzeuge vor",
  edhrec_recommendations: "prüft EDHREC-Empfehlungen", edhrec_average_deck: "lädt ein EDHREC-Durchschnittsdeck",
  search_cards: "sucht Karten auf Scryfall", local_card_search: "durchsucht die Kartendatenbank",
  get_cards: "liest Kartentexte", find_commanders: "sucht Commander", find_combos: "sucht Combos",
  validate_deck: "prüft das Deck (Legalität, Bracket, Budget)", save_deck: "speichert das Deck",
  load_deck: "lädt das Deck", bracket_rules: "liest die Bracket-Regeln", game_changers: "prüft Game Changer",
  get_blacklist: "liest deine Blacklist", import_deck: "importiert ein Deck", export_deck: "exportiert das Deck",
  list_deck_versions: "liest den Verlauf", compare_deck_versions: "vergleicht Versionen",
};
const toolText = (name) => `Claude ${TOOL_LABELS[name] || `nutzt ${name}`} …`;

function startJob(jobId, title, { kind = "build", slot = "#job-slot-main", route: home = "#/job", slug = null } = {}) {
  currentJob = jobId;
  jobInfo = { id: jobId, kind, slot, route: home, slug, title, started: Date.now(), error: null, dismissed: false };
  const panel = $("#job");
  panel.classList.remove("finished", "failed");
  $("#job-title").textContent = title;
  setJobStatus("Starte …");
  $("#log").innerHTML = "";
  $("#progress").hidden = true;
  $("#console-form").hidden = true;
  $("#job-details").open = false;
  $("#cancel-btn").textContent = "Abbrechen";
  resetTerminal();
  placeJobPanel();
  setBusy(true);
  tickElapsed();
  clearInterval(elapsedTimer);
  elapsedTimer = setInterval(tickElapsed, 1000);
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/jobs/${jobId}/events`);
  eventSource.onmessage = (e) => handleEvent(JSON.parse(e.data));
  eventSource.onerror = () => { /* the browser reconnects; the server resumes via Last-Event-ID */ };
}

// the job panel lives in the slot of the view that started it (build page, deck tab, finder)
function placeJobPanel() {
  const panel = $("#job");
  const visible = jobInfo && !jobInfo.dismissed && (!jobInfo.slug || (currentDeck && currentDeck.slug === jobInfo.slug));
  if (visible) { $(jobInfo.slot).appendChild(panel); panel.hidden = false; }
  else { document.body.appendChild(panel); panel.hidden = true; }
  for (const ind of [$("#job-indicator"), $("#job-indicator-top")]) {
    ind.hidden = !currentJob;
    if (currentJob) { ind.href = jobInfo.route; ind.title = jobInfo.title; }
  }
  if (currentJob) $("#job-indicator-text").textContent = jobInfo.title;
}

function setJobStatus(text) { $("#job-status").textContent = text; }

function tickElapsed() {
  if (!jobInfo) return;
  const s = Math.round((Date.now() - jobInfo.started) / 1000);
  $("#job-elapsed").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function setBusy(busy) {
  for (const sel of ["#build-btn", "#refine-form [type=submit]", "#retune-btn", "#prepare-btn", "#mpc-btn", "#upgrade-btn", "#guide-btn"]) {
    const b = $(sel);
    b.disabled = busy;
    b.title = busy ? "Es läuft gerade ein Auftrag" : "";
  }
}

function logLine(cls, text) {
  const log = $("#log");
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function handleEvent(ev) {
  switch (ev.type) {
    case "text": logLine("text", ev.text); setJobStatus(ev.text); break;
    case "tool": logLine("tool", `→ ${ev.name} ${ev.summary || ""}`); setJobStatus(toolText(ev.name)); break;
    case "status": logLine("tool", ev.text); setJobStatus(ev.text); break;
    case "error": logLine("error", "Fehler: " + ev.text); if (jobInfo) jobInfo.error = ev.text; break;
    case "result": logLine("result", ev.text); break;
    case "suggestions": renderSuggestions(ev.items); break;
    case "upgrades": renderUpgrades(ev, jobInfo?.slug); break;
    case "guide": onGuide(ev.guide, jobInfo?.slug); break;
    case "progress": {
      const bar = $("#progress");
      bar.hidden = false;
      bar.querySelector("div").style.width = `${(ev.done / Math.max(ev.total, 1)) * 100}%`;
      bar.querySelector("span").textContent = `${ev.done} / ${ev.total} · ${ev.text || ""}`;
      setJobStatus(`Bild ${ev.done} von ${ev.total}`);
      break;
    }
    case "print": onPrepared(ev.result); break;
    case "console":
      if (ev.running) { openTerminal(); setJobStatus("MPC Autofill läuft – bediene es im Terminal."); }
      else if (term) term.options.disableStdin = true;
      break;
    case "term":
      if (term) term.write(ev.data);
      else logLine("text", ev.data.replace(ANSI_RE, "").trimEnd());
      break;
    case "done": finishJob(ev); break;
  }
}

async function finishJob(ev) {
  eventSource.close();
  clearInterval(elapsedTimer);
  const info = jobInfo;
  currentJob = null;
  setBusy(false);
  $("#job").classList.add(ev.ok ? "finished" : "failed");
  $("#cancel-btn").textContent = "Schließen";
  if (!ev.ok) {
    setJobStatus(info.error || "Beendet, ohne Ergebnis. Details unten.");
    placeJobPanel();
    return;
  }
  switch (info.kind) {
    case "build":
      info.dismissed = true;
      await refreshDeckList();
      toast("Dein Deck ist fertig.");
      go(`#/deck/${enc(ev.deck)}`);
      break;
    case "deck":
      info.dismissed = true;
      await refreshDeckList();
      if (currentDeck?.slug === info.slug) currentDeck = null;  // reload on the next route
      toast("Deck aktualisiert – die Änderungen stehen im Verlauf.");
      go(`#/deck/${enc(info.slug)}/verlauf`);
      break;
    case "finder":
    case "upgrade":
    case "guide":
      info.dismissed = true;
      break;
    case "print":
      info.dismissed = true;
      toast("Druckdateien sind fertig.");
      break;
    default:  // autofill: keep the terminal output visible until closed
      setJobStatus("MPC Autofill ist beendet.");
  }
  placeJobPanel();
}

$("#cancel-btn").addEventListener("click", async () => {
  if (currentJob) {
    await api(`/api/jobs/${currentJob}/cancel`, { method: "POST" }).catch(() => {});
    return;
  }
  if (jobInfo) jobInfo.dismissed = true;
  placeJobPanel();
  if (parseHash().view === "job") $("#job-empty").hidden = false;
});

// ============================================================================================
// deck view
// ============================================================================================
let printLoadedFor = null;

async function openDeck(slug) {
  let d;
  try { d = await api(`/api/decks/${enc(slug)}`); }
  catch (err) { toast(`Deck „${slug}“ nicht gefunden.`, "error"); go("#/new"); return false; }
  if (edit && edit.slug !== d.slug) {
    if (editChanges()) toast("Ungespeicherte Kartenänderungen wurden verworfen.", "error");
    edit = null;
    $("#edit-toggle").setAttribute("aria-pressed", "false");
    $("#edit-toggle span").textContent = "Bearbeiten";
    $("#add-card-form").hidden = true;
  }
  currentDeck = d;
  if (ownership && ownership.slug !== d.slug) ownership = null;
  printLoadedFor = null;
  rule0For = null;
  renderDeckHead(d);
  renderValidation(d.validation);
  renderHealth(d);
  renderStats(d.validation?.stats, d.validation);
  renderCards(d);
  $("#retune-form").querySelector(`[name="rbracket"][value="${d.bracket || 3}"]`).checked = true;
  setProfile($("#retune-form .profile-fields"), d.power_profile);
  $("#retune-form").elements.request.value = "";
  renderPower(d);
  $("#upgrade-budget-field").hidden = !!d.proxy;
  $("#upgrade-cur").textContent = `(${(d.currency || "eur").toUpperCase()})`;
  if (upgrades?.slug !== d.slug) $("#upgrade-result").hidden = true;
  renderHistory(d);
  renderQuestions(d);
  renderGuide(d);
  loadOwnership(d);
  loadTokens(d);
  resetHand();
  renderOdds(d);
  $("#deck-menu").open = false;
  return true;
}

function renderDeckHead(d) {
  const v = d.validation || {};
  const cur = (d.currency || "eur").toUpperCase();
  $("#deck-name").textContent = d.name;
  const thumb = d.card_data?.[d.commanders[0]]?.image;
  $("#deck-thumb").hidden = !thumb;
  if (thumb) $("#deck-thumb").src = thumb;
  const pills = [
    `<span class="pill">${esc(d.commanders.join(" + "))}</span>`,
    `<span class="pill accent">${esc(levelText(d.bracket ?? "?", d.power_profile?.tier))}</span>`,
  ];
  if (v.legal === true) pills.push(`<span class="pill ok">${icon("check")}legal</span>`);
  if (v.legal === false) pills.push(`<span class="pill bad">${icon("alert")}nicht legal</span>`);
  if (d.proxy) pills.push('<span class="pill">Proxy-Deck</span>');
  else if (v.price_total != null) pills.push(`<span class="pill">${esc(fmtPrice(v.price_total, cur))}${d.budget ? ` / ${esc(d.budget)} ${cur}` : ""}</span>`);
  if (d.power_profile?.style) pills.push(`<span class="pill">Stil: ${esc(d.power_profile.style)}</span>`);
  pills.push(`<span class="muted small">${d.version ? `v${d.version} · ` : ""}${esc(fmtDate(d.updated))}</span>`);
  $("#deck-meta").innerHTML = pills.join("");
  $("#deck-desc").textContent = d.description || "";
  $("#deck-desc").hidden = !d.description;
  for (const [id, fmt] of [["#export-cod", "cockatrice"], ["#export-tts", "tts"], ["#export-txt", "text"]]) $(id).href = `/api/decks/${enc(d.slug)}/export/${fmt}`;
}

// tabs (WAI-ARIA APG pattern: arrow keys move between tabs)
function selectTab(tab, updateHash = true, focus = false) {
  if (!TABS.includes(tab)) tab = "karten";
  for (const t of $$("#deck-tabs [role=tab]")) {
    const on = t.dataset.tab === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  }
  for (const p of $$("#view-deck [role=tabpanel]")) p.hidden = p.id !== `panel-${tab}`;
  if (updateHash && currentDeck) history.replaceState(null, "", `#/deck/${enc(currentDeck.slug)}/${tab}`);
  if (tab === "anleitung" && currentDeck && rule0For !== currentDeck.slug) loadRule0();
  if (tab === "drucken" && currentDeck && printLoadedFor !== currentDeck.slug) {
    printLoadedFor = currentDeck.slug;
    loadPlan();
  }
}
$("#deck-tabs").addEventListener("click", (e) => {
  const t = e.target.closest("[role=tab]");
  if (t) selectTab(t.dataset.tab);
});
$("#deck-tabs").addEventListener("keydown", (e) => {
  const i = TABS.indexOf(document.activeElement?.dataset?.tab);
  if (i < 0) return;
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key];
  if (next === undefined) return;
  e.preventDefault();
  selectTab(TABS[(next + TABS.length) % TABS.length], true, true);
});

// overflow menu: closes on outside click, item click and Esc
document.addEventListener("click", (e) => {
  for (const m of $$("details.menu[open]")) if (!m.contains(e.target)) m.open = false;
});
$("#deck-menu").addEventListener("click", (e) => { if (e.target.closest(".menu-list button, .menu-list a")) $("#deck-menu").open = false; });
$("#deck-menu").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { $("#deck-menu").open = false; $("#deck-menu summary").focus(); }
});

function renderValidation(v) {
  const box = $("#validation");
  if (!v) { box.innerHTML = '<p class="muted">Noch nicht geprüft – „⋯ → Neu prüfen“.</p>'; return; }
  const br = v.bracket || {};
  const goal = br.target_text || `Bracket ${br.target}`;
  const status = [
    v.legal ? `<span class="pill ok">${icon("check")}legal</span>` : `<span class="pill bad">${icon("alert")}nicht legal</span>`,
    br.compliant ? `<span class="pill ok">passt zu ${esc(goal)}</span>` : `<span class="pill warn">${esc(goal)} verletzt</span>`,
  ];
  const issues = [...(v.errors || []).map((t) => ["bad", t]), ...(br.violations || []).map((t) => ["warn", t]), ...(v.warnings || []).map((t) => ["warn", t])];
  const li = ([cls, t]) => `<li class="${cls}">${esc(t)}</li>`;
  const shown = issues.slice(0, 4), rest = issues.slice(4);
  const list = (items) => (items.length ? `<ul class="issues">${items.map(li).join("")}</ul>` : "");
  const kv = (label, items) => `<dt>${label}</dt><dd>${esc(items.length ? items.join(", ") : "–")}</dd>`;
  box.innerHTML = `
    <div class="status-line">${status.join("")}</div>
    ${br.estimated ? `<p class="muted small">Commander Spellbook schätzt Bracket ${esc(br.estimated)}.</p>` : ""}
    ${list(shown)}
    ${rest.length ? `<details class="more"><summary>${rest.length} weitere Hinweise</summary>${list(rest)}</details>` : ""}
    <details class="more"><summary>Bracket-Details</summary>
      <dl class="kv">
        ${kv("Game Changer", br.game_changers || [])}
        ${kv("2-Karten-Combos", (br.two_card_combos || []).map((c) => c.cards.join(" + ")))}
        ${kv("Extra Turns", br.extra_turns || [])}
        ${kv("Mass Land Denial", br.mass_land_denial || [])}
        ${kv("Tutoren", br.tutors || [])}
      </dl>
    </details>`;
}

const ROLE_LABELS = { ramp: "Ramp", card_draw: "Kartenzug", removal: "Removal", board_wipe: "Board Wipes",
  tutor: "Tutoren", extra_turn: "Extra Turns", counterspell: "Counter", protection: "Schutz" };

function renderStats(s, v = {}) {
  const box = $("#stats");
  if (!s) { box.innerHTML = '<p class="muted">Keine Statistik.</p>'; return; }
  const curve = s.mana_curve || {};
  const max = Math.max(1, ...Object.values(curve));
  const keys = ["0", "1", "2", "3", "4", "5", "6", "7+"];
  const priceKey = Object.keys(s).find((k) => k.startsWith("total_price_"));
  const cur = priceKey ? priceKey.replace("total_price_", "").toUpperCase() : "";
  box.innerHTML = `
    <div class="curve" role="img" aria-label="Manakurve: ${keys.map((k) => `${k}: ${curve[k] || 0}`).join(", ")}">
      ${keys.map((k) => `<div><span>${curve[k] || 0}</span><div class="bar" style="height:${((curve[k] || 0) / max) * 78}%"></div><span>${k}</span></div>`).join("")}</div>
    <dl class="kv">
      <dt>Karten</dt><dd>${s.card_count}</dd>
      <dt>Ø Manawert</dt><dd>${s.avg_cmc_nonland}</dd>
      <dt>Typen</dt><dd>${esc(Object.entries(s.types || {}).map(([k, n]) => `${k} ${n}`).join(" · "))}</dd>
      <dt>Rollen</dt><dd>${esc(Object.entries(s.role_counts || {}).map(([k, n]) => `${ROLE_LABELS[k] || k} ${n}`).join(" · "))}</dd>
      <dt>Preis</dt><dd>${priceKey ? `${esc(v.price_total ?? s[priceKey])} ${cur}` : "–"}${v.proxy ? " · Proxy" : v.budget ? ` <span class="muted">/ Budget ${esc(v.budget)}</span>` : ""}</dd>
    </dl>`;
}

// ---------- card list: grouped + sorted, as text list or image grid, with an edit mode ----------
let cardView = store.get("cardview") === "grid" ? "grid" : "list";
let cardGroup = store.get("cardgroup") || "category";
let cardSort = store.get("cardsort") || "name";
document.querySelector(`[name="cardview"][value="${cardView}"]`).checked = true;
$("#card-group").value = cardGroup;
$("#card-sort").value = cardSort;
$$('[name="cardview"]').forEach((r) => r.addEventListener("change", () => {
  cardView = r.value;
  store.set("cardview", cardView);
  if (currentDeck) renderCards(currentDeck);
}));
$("#card-group").addEventListener("change", (e) => { cardGroup = e.target.value; store.set("cardgroup", cardGroup); renderCards(currentDeck); });
$("#card-sort").addEventListener("change", (e) => { cardSort = e.target.value; store.set("cardsort", cardSort); renderCards(currentDeck); });

const CATEGORY_ORDER = ["Commander", "Ramp", "Draw", "Removal", "Board Wipe", "Protection", "Synergy", "Win Condition", "Utility", "Land"];
const TYPE_ORDER = ["Commander", "Creature", "Planeswalker", "Battle", "Artifact", "Enchantment", "Instant", "Sorcery", "Land", "Sonstiges"];
const TYPE_LABELS = { Creature: "Kreaturen", Planeswalker: "Planeswalker", Battle: "Schlachten", Artifact: "Artefakte",
  Enchantment: "Verzauberungen", Instant: "Spontanzauber", Sorcery: "Hexereien", Land: "Länder", Sonstiges: "Sonstiges" };
const COLOR_GROUPS = { W: "Weiß", U: "Blau", B: "Schwarz", R: "Rot", G: "Grün" };

function typeOf(cd) {
  const t = (cd?.type_line || "").split("//")[0];
  for (const k of ["Land", "Creature", "Planeswalker", "Battle", "Artifact", "Enchantment", "Instant", "Sorcery"]) if (t.includes(k)) return k;
  return "Sonstiges";
}
const priceOf = (cd, d) => Number(cd?.[d.currency === "usd" ? "price_usd" : "price_eur"]) || 0;

function groupOf(c, cd) {
  if (c.commander) return "Commander";
  switch (cardGroup) {
    case "type": return TYPE_LABELS[typeOf(cd)];
    case "cmc": return typeOf(cd) === "Land" ? "Länder" : `Manawert ${cd?.cmc >= 7 ? "7+" : Math.round(cd?.cmc || 0)}`;
    case "color": {
      if (typeOf(cd) === "Land") return "Länder";
      const ci = cd?.color_identity || [];
      return ci.length === 0 ? "Farblos" : ci.length > 1 ? "Mehrfarbig" : COLOR_GROUPS[ci[0]];
    }
    case "owned": return ownershipLabel(c.name);
    default: return c.category || TYPE_LABELS[typeOf(cd)] || "Sonstiges";
  }
}

function groupRank(g) {
  const order = cardGroup === "type" ? TYPE_ORDER.map((t) => TYPE_LABELS[t] || t)
    : cardGroup === "color" ? ["Commander", "Weiß", "Blau", "Schwarz", "Rot", "Grün", "Mehrfarbig", "Farblos", "Länder"]
    : cardGroup === "owned" ? ["Commander", ...OWNED_ORDER]
    : CATEGORY_ORDER;
  const i = order.indexOf(g);
  if (cardGroup === "cmc" && g.startsWith("Manawert")) return 1 + (g.endsWith("7+") ? 7 : Number(g.split(" ")[1]));
  if (cardGroup === "cmc") return g === "Commander" ? 0 : 99;
  return i < 0 ? 50 : i;
}

// pending edits (saved together as one new version)
let edit = null;  // { slug, add: Map(name -> {qty, category, data}), qty: Map(name -> qty), remove: Set, cat: Map(name -> category) }
const editChanges = () => edit ? edit.add.size + edit.qty.size + edit.remove.size + edit.cat.size : 0;

function deckRows(d) {
  const rows = d.commanders.map((n) => ({ name: n, qty: 1, commander: true, cd: d.card_data?.[n] || {} }));
  for (const c of d.cards) {
    const row = { ...c, cd: d.card_data?.[c.name] || {} };
    if (edit) {
      if (edit.remove.has(c.name)) row.status = "removed";
      else if (edit.qty.has(c.name)) { row.qty = edit.qty.get(c.name); row.status = "changed"; }
      if (edit.cat.has(c.name)) { row.category = edit.cat.get(c.name); row.status ||= "changed"; }
    }
    rows.push(row);
  }
  if (edit) for (const [name, a] of edit.add) rows.push({ name, qty: a.qty, category: a.category || "", cd: a.data || {}, status: "new" });
  return rows;
}

function renderCards(d) {
  if (!d) return;
  const editing = !!edit;
  const grid = cardView === "grid" && !editing;
  const rows = deckRows(d);
  const groups = {};
  for (const r of rows) (groups[groupOf(r, r.cd)] ||= []).push(r);
  const cmp = {
    name: (a, b) => a.name.localeCompare(b.name),
    cmc: (a, b) => (a.cd.cmc || 0) - (b.cd.cmc || 0) || a.name.localeCompare(b.name),
    price: (a, b) => priceOf(b.cd, d) - priceOf(a.cd, d) || a.name.localeCompare(b.name),
  }[cardSort] || ((a, b) => a.name.localeCompare(b.name));
  const live = rows.filter((r) => r.status !== "removed");
  const total = live.reduce((a, r) => a + (r.qty || 1), 0);
  $("#card-total").textContent = total;
  $("#card-total").classList.toggle("bad", editing && total !== 100);
  $("#cards").classList.toggle("grid", grid);
  $("#cards").classList.toggle("editing", editing);
  const catOptions = (sel) => ["", ...CATEGORY_ORDER.slice(1)].map((c) => `<option value="${esc(c)}" ${c === sel ? "selected" : ""}>${esc(c || "Kategorie …")}</option>`).join("");
  $("#cards").innerHTML = Object.entries(groups).sort(([a], [b]) => groupRank(a) - groupRank(b) || a.localeCompare(b)).map(([g, items]) => {
    const html = items.sort(cmp).map((c) => {
      const cd = c.cd;
      const own = ownershipBadge(c.name, c.qty, c.commander);
      const data = `data-img="${esc(cd.image || "")}" data-img-back="${esc(cd.image_back || "")}" data-name="${esc(c.name)}"`;
      const label = `${c.qty > 1 ? c.qty + "× " : ""}${c.name}`;
      if (grid) {
        return `<div class="card" role="button" tabindex="0" ${data} aria-label="${esc(label)} – große Ansicht">
          ${cd.image ? `<img src="${esc(cd.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(c.name)}</div>`}
          ${c.qty > 1 ? `<span class="qty-badge">${c.qty}×</span>` : ""}${own ? `<span class="own-badge">${own}</span>` : ""}</div>`;
      }
      const name = `${esc(c.name)}${cd.image_back ? '<span class="dfc" title="doppelseitig">⇄</span>' : ""}${cd.game_changer ? '<span class="gc" title="Game Changer">GC</span>' : ""}`;
      const price = `<span class="price">${priceOf(cd, d) ? esc(cd[d.currency === "usd" ? "price_usd" : "price_eur"]) : ""}</span>`;
      if (!editing || c.commander) {
        return `<div class="card" role="button" tabindex="0" ${data} aria-label="${esc(label)} – große Ansicht">
          <span>${c.qty > 1 ? `<span class="qty">${c.qty}× </span>` : ""}${name}${own}</span>${price}</div>`;
      }
      if (c.status === "removed") {
        return `<div class="card removed" ${data}><span class="card-name"><s>${esc(label)}</s></span>
          <span class="edit-ctrls"><button type="button" class="mini" data-act="undo" title="Rückgängig">↶ zurück</button></span></div>`;
      }
      return `<div class="card ${c.status || ""}" ${data}>
        <button type="button" class="card-name" data-act="view">${name}${c.status === "new" ? ' <span class="tag-new">neu</span>' : ""}</button>
        <span class="edit-ctrls">
          <select class="cat" data-act="cat" aria-label="Kategorie von ${esc(c.name)}">${catOptions(c.category || "")}</select>
          <button type="button" class="mini" data-act="minus" aria-label="Eine weniger">−</button>
          <span class="q">${c.qty}</span>
          <button type="button" class="mini" data-act="plus" aria-label="Eine mehr">+</button>
          <button type="button" class="mini" data-act="similar" title="Ähnliche Karten zum Tauschen">${icon("swap")}</button>
          <button type="button" class="mini danger" data-act="remove" title="Entfernen" aria-label="${esc(c.name)} entfernen">✕</button>
        </span></div>`;
    }).join("");
    const n = items.filter((r) => r.status !== "removed").reduce((a, c) => a + (c.qty || 1), 0);
    return `<section class="group${!grid && items.length > 16 ? " long" : ""}"><h3>${esc(g)} <span class="count">${n}</span></h3>${grid ? `<div class="group-cards">${html}</div>` : html}</section>`;
  }).join("");
  updateEditBar(total);
}

// ---------- edit mode ----------
function setEditing(on) {
  if (on && !currentDeck) return;
  edit = on ? { slug: currentDeck.slug, add: new Map(), qty: new Map(), remove: new Set(), cat: new Map() } : null;
  $("#edit-toggle").setAttribute("aria-pressed", String(on));
  $("#edit-toggle span").textContent = on ? "Fertig" : "Bearbeiten";
  $("#add-card-form").hidden = !on;
  renderCards(currentDeck);
  if (on) $("#add-card-name").focus();
}
$("#edit-toggle").addEventListener("click", async () => {
  if (!edit) return setEditing(true);
  if (editChanges() && !(await ask({ title: "Änderungen verwerfen?", text: `${editChanges()} ungespeicherte Änderungen gehen verloren.`, ok: "Verwerfen", danger: true }))) return;
  setEditing(false);
});
$("#edit-discard").addEventListener("click", () => setEditing(false));

function updateEditBar(total) {
  const n = editChanges();
  $("#edit-bar").hidden = !edit || !n;
  if (!edit || !n) return;
  const parts = [];
  if (edit.add.size) parts.push(`${edit.add.size} neu`);
  if (edit.remove.size) parts.push(`${edit.remove.size} raus`);
  if (edit.qty.size + edit.cat.size) parts.push(`${edit.qty.size + edit.cat.size} geändert`);
  $("#edit-summary").innerHTML = `${parts.join(" · ")} · <b class="${total === 100 ? "ok" : "bad"}">${total} Karten</b>${total === 100 ? "" : " (Commander-Decks brauchen 100)"}`;
}

function currentQty(name) {
  if (edit.add.has(name)) return edit.add.get(name).qty;
  if (edit.qty.has(name)) return edit.qty.get(name);
  return currentDeck.cards.find((c) => c.name === name)?.qty || 0;
}
function setQty(name, qty) {
  if (edit.add.has(name)) {
    if (qty <= 0) edit.add.delete(name); else edit.add.get(name).qty = qty;
    return;
  }
  const orig = currentDeck.cards.find((c) => c.name === name)?.qty || 0;
  if (qty <= 0) { edit.qty.delete(name); edit.remove.add(name); }
  else if (qty === orig) edit.qty.delete(name);
  else edit.qty.set(name, qty);
}

async function addCard(name, qty = 1, category = "") {
  const inDeck = currentDeck.cards.find((c) => c.name.toLowerCase() === name.toLowerCase());
  if (inDeck && !edit.remove.has(inDeck.name)) { setQty(inDeck.name, currentQty(inDeck.name) + qty); return inDeck.name; }
  if (inDeck) { edit.remove.delete(inDeck.name); return inDeck.name; }
  const data = await api(`/api/card?name=${enc(name)}`);
  if (currentDeck.commanders.includes(data.name)) throw new Error(`${data.name} ist der Commander.`);
  const existing = edit.add.get(data.name);
  edit.add.set(data.name, { qty: (existing?.qty || 0) + qty, category: category || existing?.category || "", data });
  return data.name;
}

wireAutocomplete($("#add-card-name"), $("#ac-add"));
$("#add-card-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#add-card-name").value.trim();
  if (!name || !edit) return;
  try {
    const added = await addCard(name, Math.max(1, Number($("#add-card-qty").value) || 1), $("#add-card-cat").value);
    $("#add-card-name").value = "";
    $("#add-card-qty").value = 1;
    renderCards(currentDeck);
    toast(`${added} vorgemerkt – mit „Speichern & prüfen“ übernehmen.`);
  } catch (err) { fail(err); }
});

$("#cards").addEventListener("change", (e) => {
  const sel = e.target.closest("select[data-act=cat]");
  if (!sel || !edit) return;
  const name = sel.closest(".card").dataset.name;
  if (edit.add.has(name)) edit.add.get(name).category = sel.value;
  else if (sel.value === (currentDeck.cards.find((c) => c.name === name)?.category || "")) edit.cat.delete(name);
  else edit.cat.set(name, sel.value);
  renderCards(currentDeck);
});

$("#edit-save").addEventListener("click", async () => {
  if (!edit || !editChanges()) return;
  const body = {
    add: [...edit.add].map(([name, a]) => ({ name, qty: a.qty, category: a.category || null })),
    remove: [...edit.remove],
    set_qty: Object.fromEntries(edit.qty),
    set_category: Object.fromEntries([...edit.cat].filter(([n]) => !edit.remove.has(n))),
  };
  const btn = $("#edit-save");
  btn.disabled = true;
  btn.textContent = "Speichere …";
  try {
    const r = await api(`/api/decks/${enc(edit.slug)}/cards`, { method: "POST", body });
    setEditing(false);
    currentDeck = null;
    await refreshDeckList();
    await route();
    toast(`Gespeichert als v${r.version}.${r.legal ? "" : " Achtung: das Deck ist nicht legal – siehe Prüfung."}`, r.legal ? "info" : "error");
  } catch (err) { fail(err); }
  finally { btn.disabled = false; btn.textContent = "Speichern & prüfen"; }
});

// ---------- collection ownership in the deck (filled by loadOwnership) ----------
let ownership = null;  // { cards: {name: {need, real, proxy, missing, other_decks}}, ... }
const OWNED_ORDER = ["Fehlt", "Als Proxy vorhanden", "Vorhanden"];
function ownershipLabel(name) {
  const o = ownership?.cards?.[name];
  if (!o) return "Unbekannt";
  return o.missing > 0 ? "Fehlt" : o.real >= o.need ? "Vorhanden" : "Als Proxy vorhanden";
}
function ownershipBadge(name, need = 1, commander = false) {
  const o = ownership?.cards?.[name];
  if (!ownership?.collection_size || (!o && !ownership.all)) return "";
  const real = o?.real || 0, prox = o?.proxy || 0;
  if (real >= need) return '<span class="own ok" title="In deiner Sammlung">✓</span>';
  if (real + prox >= need) return '<span class="own proxy" title="Als Proxy in deiner Sammlung">P</span>';
  if (real + prox > 0) return `<span class="own part" title="Nur ${real + prox} von ${need} vorhanden">${real + prox}/${need}</span>`;
  return "";  // missing: no badge (the Sammlung panel and "nach Besitz" show them) – keeps the list calm
}

// ---------- test hand (London mulligan, first mulligan free) ----------
let hand = null;  // { lib: [card], hand: [card], draws, mull, bottom }
const isLand = (cd) => /\bLand\b/.test(cd?.type_line || "");
function libraryOf(d) {
  return d.cards.flatMap((c) => Array.from({ length: c.qty || 1 }, () => ({ name: c.name, cd: d.card_data?.[c.name] || {} })));
}
function shuffle(arr) {
  const rnd = new Uint32Array(arr.length);
  crypto.getRandomValues(rnd);
  for (let i = arr.length - 1; i > 0; i--) { const j = rnd[i] % (i + 1); [arr[i], arr[j]] = [arr[j], arr[i]]; }
  return arr;
}
function resetHand() {
  hand = null;
  $("#hand").innerHTML = "";
  $("#hand-hint").hidden = true;
  $("#hand-status").textContent = "Zieh eine Starthand – wie am Tisch.";
}
function dealHand(mull) {
  const lib = shuffle(libraryOf(currentDeck));
  hand = { lib, hand: lib.splice(0, 7), draws: 0, mull, bottom: Math.max(0, mull - 1) };
  renderHand();
}
function renderHand() {
  if (!hand) return;
  const lands = hand.hand.filter((c) => isLand(c.cd)).length;
  const onDraw = $("#on-draw").checked;
  const turn = hand.draws === 0 ? 1 : onDraw ? hand.draws : hand.draws + 1;
  $("#hand-status").textContent = `${hand.draws ? `Zug ${turn}` : "Starthand"} · ${hand.hand.length} Karten · ${lands} ${lands === 1 ? "Land" : "Länder"}`
    + (hand.mull ? ` · ${hand.mull}. Mulligan` : "") + ` · noch ${hand.lib.length} in der Bibliothek`;
  const hint = $("#hand-hint");
  hint.hidden = !hand.bottom && hand.mull !== 1;
  hint.textContent = hand.bottom
    ? `Lege noch ${hand.bottom} ${hand.bottom === 1 ? "Karte" : "Karten"} unter die Bibliothek – klick sie an.`
    : "Der erste Mulligan ist in Commander frei: du behältst alle 7 Karten.";
  $("#hand").innerHTML = hand.hand.map((c, i) => `<button type="button" class="hand-card card ${isLand(c.cd) ? "land" : ""}" data-i="${i}"
      data-img="${esc(c.cd.image || "")}" data-name="${esc(c.name)}" title="${esc(c.name)}${hand.bottom ? " – unter die Bibliothek legen" : ""}">
      ${c.cd.image ? `<img src="${esc(c.cd.image)}" alt="${esc(c.name)}">` : `<span class="noimg">${esc(c.name)}</span>`}</button>`).join("");
  $("#hand-draw").disabled = !!hand.bottom || !hand.lib.length;
}
$("#hand-new").addEventListener("click", () => currentDeck && dealHand(0));
$("#hand-mull").addEventListener("click", () => currentDeck && dealHand((hand?.mull || 0) + 1));
$("#hand-draw").addEventListener("click", () => {
  if (!hand) return dealHand(0);
  if (hand.bottom || !hand.lib.length) return;
  hand.hand.push(hand.lib.shift());
  hand.draws += 1;
  renderHand();
});
$("#on-draw").addEventListener("change", () => { renderHand(); if (currentDeck) renderOdds(currentDeck); });
$("#hand").addEventListener("click", (e) => {
  const b = e.target.closest(".hand-card");
  if (!b || !hand) return;
  if (!hand.bottom) { showCardView(b.dataset.name, hand.hand[Number(b.dataset.i)].cd); return; }
  hand.lib.push(...hand.hand.splice(Number(b.dataset.i), 1));
  hand.bottom -= 1;
  renderHand();
});

// ---------- probabilities (hypergeometric) ----------
function choose(n, k) {
  if (k < 0 || k > n) return 0;
  let r = 1;
  for (let i = 1; i <= Math.min(k, n - k); i++) r = (r * (n - Math.min(k, n - k) + i)) / i;
  return r;
}
const pExactly = (N, K, n, k) => (choose(K, k) * choose(N - K, n - k)) / choose(N, n);
function pAtLeast(N, K, n, k) {
  let p = 0;
  for (let i = k; i <= Math.min(n, K); i++) p += pExactly(N, K, n, i);
  return Math.min(1, p);
}
const pct = (p) => `${Math.round(p * 100)} %`;

function renderOdds(d) {
  const lib = libraryOf(d);
  const N = lib.length;
  if (N < 8) { $("#odds").innerHTML = '<p class="muted">Zu wenige Karten.</p>'; return; }
  const count = (fn) => lib.filter((c) => fn(c.cd)).length;
  const L = count(isLand);
  const has = (roles) => (cd) => !isLand(cd) && (cd.roles || []).some((r) => roles.includes(r));
  const R = count(has(["ramp"])), D = count(has(["card_draw"])), X = count(has(["removal", "board_wipe", "counterspell"]));
  const onDraw = $("#on-draw").checked;
  const seen = (turn) => 7 + (onDraw ? turn : turn - 1);  // cards seen by the given turn
  const dist = Array.from({ length: 8 }, (_, k) => pExactly(N, L, 7, k));
  const max = Math.max(...dist);
  const keep = dist.slice(2, 6).reduce((a, b) => a + b, 0);
  const bars = dist.map((p, k) => `<div class="dbar ${k >= 2 && k <= 5 ? "ok" : ""}" title="${k} Länder: ${pct(p)}">
      <div class="fill" style="height:${(p / max) * 100}%"></div><span class="x">${k}</span></div>`).join("");
  const row = (label, p, detail) => `<tr><th scope="row">${label}</th><td class="num">${pct(p)}</td><td class="muted small">${detail}</td></tr>`;
  $("#odds").innerHTML = `
    <div class="odds-grid">
      <figure class="dist">
        <figcaption><b>Länder in der Starthand</b> · Ø ${(7 * L / N).toFixed(1)} · 2–5 Länder: <b>${pct(keep)}</b></figcaption>
        <div class="dbars" role="img" aria-label="Länder in der Starthand: ${dist.map((p, k) => `${k}: ${pct(p)}`).join(", ")}">${bars}</div>
      </figure>
      <table class="odds-table">
        <caption class="sr-only">Wahrscheinlichkeiten ${onDraw ? "auf dem Draw" : "auf dem Play"}</caption>
        <tbody>
          ${[2, 3, 4, 5].map((t) => row(`${t}. Landdrop in Zug ${t}`, pAtLeast(N, L, seen(t), t), `mind. ${t} Länder unter ${seen(t)} Karten`)).join("")}
          ${R ? row("Ramp bis Zug 2", pAtLeast(N, R, seen(2), 1), `${R} Ramp-Karten im Deck`) : ""}
          ${D ? row("Kartenzug bis Zug 3", pAtLeast(N, D, seen(3), 1), `${D} Kartenzug-Karten`) : ""}
          ${X ? row("Interaktion bis Zug 4", pAtLeast(N, X, seen(4), 1), `${X} Removal/Wipes/Counter`) : ""}
        </tbody>
      </table>
    </div>
    <p class="muted small">${L} Länder in ${N} Karten · ${onDraw ? "auf dem Draw (Karte in Zug 1)" : "auf dem Play (keine Karte in Zug 1)"}${keep < 0.75 ? ` · <span class="warn">Nur ${pct(keep)} der Starthände haben 2–5 Länder – mehr Länder oder günstiger Ramp helfen.</span>` : ""}</p>`;
}

// ---------- tokens, emblems and markers the deck creates ----------
async function loadTokens(d) {
  $("#token-panel").hidden = true;
  let toks = [];
  try { toks = await api(`/api/decks/${enc(d.slug)}/tokens`); } catch { return; }
  if (currentDeck?.slug !== d.slug || !toks.length) return;
  $("#token-panel").hidden = false;
  $("#token-count").textContent = toks.length;
  $("#token-list").innerHTML = toks.map((t) => `<li class="card" data-img="${esc(t.image || "")}" data-name="${esc(t.name)}">
    ${t.image ? `<img src="${esc(t.image)}" alt="" loading="lazy">` : ""}
    <div><b>${esc(t.name)}</b><div class="muted small">${esc(t.type_line.replace(/^Token /, ""))} · von ${esc(t.from.slice(0, 3).join(", "))}${t.from.length > 3 ? ` +${t.from.length - 3}` : ""}</div></div></li>`).join("");
}
$("#token-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-name]");
  if (li) showCardView(li.dataset.name, { image: li.dataset.img });
});

// ---------- replacement suggestions ----------
// deck check: traffic light per area; "fix" offers role candidates (no AI) or upgrade suggestions with a focus
const STATUS_TEXT = { green: "passt", yellow: "prüfen", red: "Problem" };
const ROLE_CATEGORY = { ramp: "Ramp", card_draw: "Draw", removal: "Removal", board_wipe: "Board Wipe" };
function renderHealth(d) {
  const h = d.health;
  $("#health-panel").hidden = !h;
  if (!h) return;
  $("#health-summary").textContent = h.summary;
  $("#health-list").innerHTML = h.items.map((it) => `<li><details data-key="${esc(it.key)}">
      <summary><span class="light ${esc(it.status)}" role="img" aria-label="${STATUS_TEXT[it.status]}"></span>
        <span class="h-label">${esc(it.label)}</span><span class="h-val">${esc(fmtNum(it.value))} · Ziel ${esc(it.target)}</span></summary>
      <div class="h-body"><p>${esc(it.text)}</p><p class="why"><strong>Warum wichtig?</strong> ${esc(it.why)}</p>
        ${it.cards.length ? `<p class="why">Erkannt: ${it.cards.slice(0, 12).map(esc).join(", ")}${it.cards.length > 12 ? " …" : ""}</p>` : ""}
        ${it.fix ? `<div class="btn-group">
          ${it.fix.role ? `<button type="button" class="btn small" data-role="${esc(it.fix.role)}" data-label="${esc(it.label)}">Karten vorschlagen</button>` : ""}
          ${it.fix.focus ? `<button type="button" class="btn small ghost" data-focus="${esc(it.fix.focus)}">${icon("spark")}Upgrades mit KI</button>` : ""}
        </div>` : ""}</div></details></li>`).join("");
}
$("#health-list").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.role) return openRoleCandidates(b.dataset.role, b.dataset.label);
  if (b.dataset.focus) {
    selectTab("anpassen");
    const f = $("#upgrade-form");
    f.elements.focus.value = b.dataset.focus;
    f.scrollIntoView({ behavior: "smooth", block: "center" });
    $("#upgrade-btn").focus({ preventScroll: true });
    toast("Fokus eingetragen – mit „Vorschläge holen“ startest du die Suche.");
  }
});

let similarRole = null;
async function openRoleCandidates(role, label) {
  similarFor = null;
  similarRole = role;
  $("#similar-title").textContent = `${label} ergänzen`;
  $("#similar-hint").textContent = "Suche beliebte Karten in deinen Farben …";
  $("#similar-grid").innerHTML = "";
  $("#similar").showModal();
  try {
    const items = await api(`/api/decks/${enc(currentDeck.slug)}/role-candidates?role=${enc(role)}`);
    const cur = (currentDeck.currency || "eur") === "usd" ? "price_usd" : "price_eur";
    $("#similar-hint").textContent = items.length
      ? "Klick merkt die Karte im Bearbeiten-Modus vor. Nimm für jede neue Karte eine andere heraus, damit es 100 bleiben."
      : "Keine passenden Karten gefunden.";
    $("#similar-grid").innerHTML = items.map((c) => `<button type="button" class="similar" data-name="${esc(c.name)}">
      ${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(c.name)}</div>`}
      <span class="sim-name">${esc(c.name)}</span>
      <span class="muted small">${esc(c.type_line || "")}${c[cur] ? ` · ${esc(c[cur])}` : ""}${ownershipBadge(c.name, 1) ? " · " + ownershipBadge(c.name, 1) : ""}</span></button>`).join("");
  } catch (err) { $("#similar-hint").textContent = err.message; }
}

let similarFor = null;
async function openSimilar(name) {
  similarFor = name;
  similarRole = null;
  $("#similar-title").textContent = `Ersatz für ${name}`;
  $("#similar-hint").textContent = "Suche Karten mit gleicher Rolle in deinen Farben …";
  $("#similar-grid").innerHTML = "";
  $("#similar").showModal();
  try {
    const items = await api(`/api/decks/${enc(currentDeck.slug)}/similar?card=${enc(name)}`);
    const cur = (currentDeck.currency || "eur") === "usd" ? "price_usd" : "price_eur";
    $("#similar-hint").textContent = items.length ? "Klick tauscht die Karte (wird erst beim Speichern übernommen)." : "Keine passenden Karten gefunden.";
    $("#similar-grid").innerHTML = items.map((c, i) => `<button type="button" class="similar" data-i="${i}" data-name="${esc(c.name)}">
      ${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(c.name)}</div>`}
      <span class="sim-name">${esc(c.name)}</span>
      <span class="muted small">${esc(c.reason || "")}${c[cur] ? ` · ${esc(c[cur])}` : ""}${ownershipBadge(c.name, 1) ? " · " + ownershipBadge(c.name, 1) : ""}</span></button>`).join("");
  } catch (err) { $("#similar-hint").textContent = err.message; }
}
$("#similar-grid").addEventListener("click", async (e) => {
  const b = e.target.closest(".similar");
  if (b && similarRole) {
    if (b.classList.contains("picked")) return;
    try {
      if (!edit) setEditing(true);
      const added = await addCard(b.dataset.name, 1, ROLE_CATEGORY[similarRole] || "");
      b.classList.add("picked");
      b.querySelector(".sim-name").textContent = "✓ " + added;
      renderCards(currentDeck);
      toast(`${added} vorgemerkt – entferne dafür eine andere Karte und speichere.`);
    } catch (err) { fail(err); }
    return;
  }
  if (!b || !edit || !similarFor) return;
  const old = similarFor;
  const category = edit.add.get(old)?.category || edit.cat.get(old) || currentDeck.cards.find((c) => c.name === old)?.category || "";
  try {
    const added = await addCard(b.dataset.name, 1, category);
    setQty(old, currentQty(old) - 1);
    $("#similar").close();
    renderCards(currentDeck);
    toast(`${old} → ${added} vorgemerkt.`);
  } catch (err) { fail(err); }
});
$("#similar-close").addEventListener("click", () => $("#similar").close());

// hover preview of card images (list view and card references in answers)
const preview = $("#preview");
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest(".card[data-img]");
  if (!el || !el.dataset.img || el.closest(".cards.grid")) return;
  const [front, back] = preview.querySelectorAll("img");
  front.src = el.dataset.img;
  back.hidden = !el.dataset.imgBack;  // double-faced: both sides side by side
  if (el.dataset.imgBack) back.src = el.dataset.imgBack;
  preview.hidden = false;
});
document.addEventListener("mouseout", (e) => { if (e.target.closest(".card[data-img]")) preview.hidden = true; });
document.addEventListener("mousemove", (e) => {
  if (preview.hidden) return;
  const w = preview.querySelector("img.back:not([hidden])") ? 500 : 260;
  const x = e.clientX + w > window.innerWidth ? e.clientX - w : e.clientX + 20;
  const y = Math.min(e.clientY - 40, window.innerHeight - 350);
  preview.style.left = x + "px"; preview.style.top = Math.max(8, y) + "px";
});

// click (or Enter) on a card: large view, both faces for double-faced cards
function openCardFromEvent(e) {
  const el = e.target.closest(".card[data-name]");
  if (!el || !currentDeck) return;
  const act = e.target.closest("[data-act]")?.dataset.act;
  const name = el.dataset.name;
  if (edit && act && act !== "view") {
    if (act === "cat") return;
    if (act === "plus") setQty(name, currentQty(name) + 1);
    if (act === "minus") setQty(name, currentQty(name) - 1);
    if (act === "remove") setQty(name, 0);
    if (act === "undo") edit.remove.delete(name);
    if (act === "similar") return openSimilar(name);
    renderCards(currentDeck);
    return;
  }
  if (edit && !act) return;  // clicks on the row background in edit mode
  const cd = currentDeck.card_data?.[name] || edit?.add.get(name)?.data || {};
  showCardView(name, cd);
}
$("#cards").addEventListener("click", openCardFromEvent);
$("#cards").addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches(".card[role=button]")) { e.preventDefault(); openCardFromEvent(e); }
});

function showCardView(name, cd) {
  const faces = name.split(" // ");
  const imgs = [[cd.image, faces[0]], ...(cd.image_back ? [[cd.image_back, faces[1] || "Rückseite"]] : [])];
  $("#card-view-title").textContent = name + (cd.image_back ? " – doppelseitig" : "");
  $("#card-view-faces").innerHTML = imgs.filter(([u]) => u).map(([u, label], i) =>
    `<figure><img src="${esc(u.replace("/normal/", "/large/"))}" alt="${esc(label)}"><figcaption>${i ? "Rückseite" : "Vorderseite"}: ${esc(label)}</figcaption></figure>`).join("")
    || '<p class="muted">Kein Bild verfügbar.</p>';
  $("#card-view-link").href = cd.scryfall_uri || `https://scryfall.com/search?q=${enc('!"' + name + '"')}`;
  const inDeck = !!currentDeck && parseHash().view === "deck" && (!!currentDeck.card_data?.[name] || currentDeck.commanders.includes(name));
  $("#card-view-explain").hidden = $("#card-view-explain-hint").hidden = !inDeck;
  $("#card-view-explain").dataset.name = name;
  preview.hidden = true;
  $("#card-view").showModal();
  loadCardText(name);
}
$("#card-view-close").addEventListener("click", () => $("#card-view").close());

// rules text next to the image: German printed text or English Oracle text, glossary terms marked
const cardTexts = new Map();
let cardViewName = null;
let cardLang = store.get("cardlang") === "en" ? "en" : "de";
document.querySelector(`[name="cardlang"][value="${cardLang}"]`).checked = true;

async function loadCardText(name) {
  cardViewName = name;
  const box = $("#card-view-text");
  box.hidden = false;
  $("#card-view-rules").innerHTML = '<p class="muted small">Kartentext wird geladen …</p>';
  $("#card-view-terms").hidden = true;
  let data = cardTexts.get(name);
  if (!data) {
    try { data = await api(`/api/cards/text?name=${enc(name)}&lang=de`); cardTexts.set(name, data); }
    catch { data = null; }
  }
  if (cardViewName !== name) return;  // another card was opened meanwhile
  if (!data) { box.hidden = true; return; }
  renderCardText(data);
}

function manaHtml(sym) {
  const s = sym.slice(1, -1);
  const color = /^[WUBRG]$/.test(s) ? s.toLowerCase() : "";
  const label = { T: "↷", Q: "↶" }[s] || s.replace("/", "");
  return `<span class="mana ${color}" title="${esc(sym)}">${esc(label)}</span>`;
}

// escape a rules text, turning {G}-symbols into pills and glossary terms into buttons
function rulesHtml(text, terms) {
  const names = [];
  for (const t of terms) for (const n of [t.term, t.de]) if (n) names.push([n, t.term]);
  names.sort((a, b) => b[0].length - a[0].length);
  const byName = new Map(names.map(([n, t]) => [n.toLowerCase(), t]));
  const alt = names.map(([n]) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const re = new RegExp(`\\{[^}]+\\}` + (alt ? `|(?<![\\p{L}\\d-])(?:${alt})(?![\\p{L}\\d-])` : ""), "giu");
  let out = "", last = 0;
  for (const m of text.matchAll(re)) {
    out += esc(text.slice(last, m.index));
    const hit = m[0];
    if (hit.startsWith("{")) out += manaHtml(hit);
    else {
      const term = byName.get(hit.toLowerCase());
      const entry = terms.find((t) => t.term === term);
      out += `<button type="button" class="term" data-term="${esc(term)}" title="${esc(entry?.text || "")}">${esc(hit)}</button>`;
    }
    last = m.index + hit.length;
  }
  return out + esc(text.slice(last));
}

function renderCardText(data) {
  const german = cardLang === "de" && data.printed;
  const faces = german ? data.printed.faces : data.faces;
  $("#card-view-rules").innerHTML = faces.map((f, i) => `<div class="face">
      <div class="head"><span>${esc(f.name)}</span><span>${(data.faces[i]?.mana_cost || "").match(/\{[^}]+\}/g)?.map(manaHtml).join("") || ""}</span></div>
      <div class="type">${esc(f.type_line)}</div>
      ${f.text.split("\n").filter(Boolean).map((line) => `<p>${rulesHtml(line, data.terms)}</p>`).join("") || '<p class="muted small">Kein Regeltext.</p>'}
    </div>`).join("")
    + (data.pt && faces.length === 1 ? `<p class="pt">${esc(data.pt)}</p>` : "")
    + (cardLang === "de" && !data.printed ? '<p class="note">Keine deutsche Ausgabe gefunden – das ist der englische Originaltext.</p>' : "")
    + (german ? `<p class="note">Gedruckter Text${data.printed.set_name ? " aus " + esc(data.printed.set_name) : ""}. Maßgeblich ist der aktuelle englische Oracle-Text.</p>` : "");
  $("#card-view-terms").hidden = !data.terms.length;
  $("#card-view-terms-list").innerHTML = data.terms.map((t) =>
    `<dt data-term="${esc(t.term)}">${esc(t.de || t.term)}${t.de ? ` <span class="de">(${esc(t.term)})</span>` : ""}</dt><dd data-term="${esc(t.term)}">${esc(t.text)}</dd>`).join("");
}

$("#card-view-text").addEventListener("change", (e) => {
  if (e.target.name !== "cardlang") return;
  cardLang = e.target.value;
  store.set("cardlang", cardLang);
  const data = cardTexts.get(cardViewName);
  if (data) renderCardText(data);
});
$("#card-view-rules").addEventListener("click", (e) => {
  const b = e.target.closest(".term");
  if (!b) return;
  for (const el of $$("#card-view-text .on")) el.classList.remove("on");
  for (const el of $$(`#card-view-text [data-term="${CSS.escape(b.dataset.term)}"]`)) el.classList.add("on");
  $(`#card-view-terms-list dt[data-term="${CSS.escape(b.dataset.term)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
});
$("#card-view-explain").addEventListener("click", () => {
  const name = $("#card-view-explain").dataset.name;
  $("#card-view").close();
  selectTab("fragen");
  const box = $("#qa-form textarea");
  box.value = `Erkläre mir die Karte [[${name}]]: Was macht sie genau, wofür ist sie in diesem Deck und wann spiele ich sie am besten?`;
  if (qaRun && !qaRun.finished) { toast("Claude beantwortet gerade eine andere Frage – deine Frage steht bereit."); box.focus(); return; }
  $("#qa-form").requestSubmit();
});

// ---------- deck actions ----------
$("#copy-btn").addEventListener("click", async () => {
  if (!currentDeck) return;
  try {
    await navigator.clipboard.writeText(currentDeck.export_text);
    toast("Liste kopiert – z. B. in Moxfield oder Archidekt einfügen.");
  } catch { toast("Kopieren nicht erlaubt – der Browser blockiert die Zwischenablage.", "error"); }
});

async function copyAsDeck(version) {
  const name = await ask({
    title: "Als neues Deck kopieren", text: "Die Kopie bekommt einen eigenen Verlauf.",
    value: `${currentDeck.name} (Kopie${version ? " v" + version : ""})`, ok: "Kopieren",
  });
  if (!name) return;
  const r = await api(`/api/decks/${enc(currentDeck.slug)}/copy`, { method: "POST", body: { name, version: version || null } });
  await refreshDeckList();
  toast(`„${name}“ angelegt.`);
  go(`#/deck/${enc(r.slug)}`);
}
$("#duplicate-btn").addEventListener("click", () => currentDeck && copyAsDeck(null).catch(fail));

$("#validate-btn").addEventListener("click", async () => {
  if (!currentDeck) return;
  const slug = currentDeck.slug;
  try {
    await api(`/api/decks/${enc(slug)}/validate`, { method: "POST" });
    currentDeck = null;
    await refreshDeckList();
    await route();
    toast("Deck neu geprüft.");
  } catch (err) { fail(err); }
});

$("#delete-btn").addEventListener("click", async () => {
  if (!currentDeck) return;
  const ok = await ask({ title: `„${currentDeck.name}“ löschen?`, text: "Das Deck, alle Versionen und Fragen werden gelöscht.", ok: "Löschen", danger: true });
  if (!ok) return;
  await api(`/api/decks/${enc(currentDeck.slug)}`, { method: "DELETE" }).catch(fail);
  toast(`„${currentDeck.name}“ gelöscht.`);
  currentDeck = null;
  await refreshDeckList();
  go("#/new");
});

// ============================================================================================
// tab "Anpassen": refine in own words, re-tune power
// ============================================================================================
$("#refine-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck || currentJob) return;
  const request = new FormData(e.target).get("request");
  const { slug, name } = currentDeck;
  try {
    const { job } = await api("/api/refine", { method: "POST", body: { slug, request } });
    e.target.reset();
    startJob(job, `Claude überarbeitet ${name}`, { kind: "deck", slug, slot: "#tune-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
  } catch (err) { fail(err); }
});
$("#refine-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const input = $("#refine-form").elements.request;
  input.value = chip.dataset.q;
  input.focus();
});

// ---------- upgrade suggestions ----------
let upgrades = null;  // { slug, items, currency, budget }
$("#upgrade-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck || currentJob) return;
  const f = new FormData(e.target);
  const body = { budget: f.get("budget") ? Number(f.get("budget")) : null, focus: f.get("focus") || null, count: Number(f.get("count")) };
  const { slug, name } = currentDeck;
  try {
    const { job } = await api(`/api/decks/${enc(slug)}/upgrades`, { method: "POST", body });
    upgrades = { slug, items: [], budget: body.budget };
    $("#upgrade-result").hidden = true;
    startJob(job, `Claude sucht Upgrades für ${name}`, { kind: "upgrade", slug, slot: "#tune-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
    $("#tune-job-slot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { fail(err); }
});

function renderUpgrades(ev, slug) {
  upgrades = { ...(upgrades || {}), slug, items: ev.items, currency: (ev.currency || "eur").toUpperCase() };
  if (currentDeck?.slug !== slug) return;
  $("#upgrade-result").hidden = false;
  $("#upgrade-summary").textContent = ev.summary || "";
  $("#upgrade-list").innerHTML = ev.items.map((u, i) => `<li>
    <label class="up-check"><input type="checkbox" data-i="${i}" checked aria-label="${esc(u.add)} statt ${esc(u.remove)} übernehmen"></label>
    <span class="up-imgs">
      ${u.image_remove ? `<img class="card out" data-img="${esc(u.image_remove)}" data-name="${esc(u.remove)}" src="${esc(u.image_remove)}" alt="">` : ""}
      ${u.image ? `<img class="card in" data-img="${esc(u.image)}" data-name="${esc(u.add)}" src="${esc(u.image)}" alt="">` : ""}
    </span>
    <div class="up-text">
      <div><span class="minus">− ${esc(u.remove)}</span> <span aria-hidden="true">→</span> <b class="plus">+ ${esc(u.add)}</b>
        ${u.impact ? `<span class="tagb">${esc(u.impact)}</span>` : ""}${u.owned ? '<span class="own ok" title="Schon in deiner Sammlung">✓ Sammlung</span>' : ""}</div>
      <div class="muted small">${esc(u.reason)}</div>
    </div>
    <span class="up-price">${u.owned ? "0 (hast du)" : u.price != null ? esc(fmtPrice(u.price, upgrades.currency)) : "–"}</span>
  </li>`).join("");
  updateUpgradeTotal();
  $("#upgrade-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function selectedUpgrades() {
  return $$("#upgrade-list input[data-i]").filter((b) => b.checked).map((b) => upgrades.items[Number(b.dataset.i)]);
}
function updateUpgradeTotal() {
  const sel = selectedUpgrades();
  const total = sel.reduce((a, u) => a + (u.owned ? 0 : u.price || 0), 0);
  const over = upgrades.budget != null && total > upgrades.budget;
  $("#upgrade-total").innerHTML = `${sel.length} ausgewählt · <b class="${over ? "bad" : ""}">${esc(fmtPrice(total, upgrades.currency))}</b>`
    + (upgrades.budget != null ? ` von ${esc(fmtPrice(upgrades.budget, upgrades.currency))}` : "");
  $("#upgrade-apply").disabled = !sel.length;
}
$("#upgrade-list").addEventListener("change", updateUpgradeTotal);
$("#upgrade-apply").addEventListener("click", async () => {
  const sel = selectedUpgrades();
  if (!sel.length || !currentDeck || upgrades?.slug !== currentDeck.slug) return;
  const body = {
    add: sel.map((u) => ({ name: u.add, qty: 1 })), remove: sel.map((u) => u.remove),
    note: `Upgrades: ${sel.map((u) => `${u.remove} → ${u.add}`).join(", ")}`,
  };
  try {
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/cards`, { method: "POST", body });
    const slug = currentDeck.slug;
    upgrades = null;
    $("#upgrade-result").hidden = true;
    currentDeck = null;
    await refreshDeckList();
    go(`#/deck/${enc(slug)}/verlauf`);
    toast(`${sel.length} Upgrades übernommen (v${r.version}).${r.legal ? "" : " Achtung: Deck ist nicht legal – siehe Prüfung."}`, r.legal ? "info" : "error");
  } catch (err) { fail(err); }
});

function retuneTarget() {
  const form = $("#retune-form");
  return {
    bracket: Number(form.querySelector('[name="rbracket"]:checked')?.value || currentDeck?.bracket || 3),
    tier: form.querySelector('[name="tier"]:checked')?.value || null,
  };
}

function stepTarget(direction) {
  let { bracket, tier } = retuneTarget();
  let idx = bracket * 3 + TIER_ORDER.indexOf(tier || "mid") + direction;
  idx = Math.min(Math.max(idx, 3), 17);
  bracket = Math.floor(idx / 3);
  tier = TIER_ORDER[idx % 3];
  $("#retune-form").querySelector(`[name="rbracket"][value="${bracket}"]`).checked = true;
  $("#retune-form").querySelector(`[name="tier"][value="${tier}"]`).checked = true;
  renderPower(currentDeck);
}
$("#step-up").addEventListener("click", () => stepTarget(+1));
$("#step-down").addEventListener("click", () => stepTarget(-1));
$("#retune-form").addEventListener("change", () => renderPower(currentDeck));

function renderPower(d) {
  if (!d) return;
  const power = d.validation?.bracket?.power;
  const { bracket, tier } = retuneTarget();
  const goal = bracket + TIER_CENTER[tier || "mid"];
  const pos = (v) => `${((Math.min(Math.max(v, 1), 5.99) - 1) / 5) * 100}%`;
  $("#power-scale").innerHTML = `<div class="track"></div>
    ${[2, 3, 4, 5].map((b) => `<span class="sep" style="left:${pos(b)}"></span>`).join("")}
    ${[1, 2, 3, 4, 5].map((b) => `<span class="tick" style="left:${pos(b + 0.5)}">Bracket ${b}</span>`).join("")}
    ${power ? `<div class="mark now" style="left:${pos(power.value)}" title="Aktuell ${power.value}"></div>` : ""}
    <div class="mark goal" style="left:${pos(goal)}" title="Ziel"></div>`;
  const changed = bracket !== d.bracket || (tier || null) !== (d.power_profile?.tier || null);
  $("#power-text").innerHTML = (power
    ? `<span class="legend-bar"></span> Aktuell: <b>${esc(fmtPower(power.value))}</b> · ${esc(power.text)}
       <details class="more"><summary>Wie kommt der Wert zustande?</summary>${esc(power.components.map((c) => `${c.reason} ${c.points > 0 ? "+" : ""}${c.points}`).join(", "))}${power.floor_from_rules ? ` – durch die Regeln mindestens Bracket ${power.floor_from_rules}` : ""}</details>`
    : "Noch keine Power-Einschätzung – „⋯ → Neu prüfen“.<br>")
    + `<span class="legend-dot"></span> Ziel: <b>${esc(levelText(bracket, tier))}</b>${changed ? " – mit „Deck umbauen“ übernehmen" : ""}`;
}

$("#retune-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck || currentJob) return;
  const { bracket } = retuneTarget();
  const profile = readProfile($("#retune-form .profile-fields")) || {};
  const request = new FormData(e.target).get("request") || null;
  const { slug, name } = currentDeck;
  try {
    const { job } = await api("/api/retune", { method: "POST", body: { slug, bracket, profile, request } });
    startJob(job, `Claude stimmt ${name} ab: ${levelText(bracket, profile.tier)}`, { kind: "deck", slug, slot: "#tune-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
    $("#tune-job-slot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { fail(err); }
});

// ============================================================================================
// tab "Anleitung": rule 0 (no AI) + play guide (Claude, stored in the deck), one printable sheet
// ============================================================================================
let rule0For = null;
let rule0 = null;
const rule0Rows = (rows) => rows.map((r) => `<dt>${esc(r.label)}</dt><dd${r.flag ? ' class="flag"' : ""}>${esc(r.value)}</dd>`).join("");

async function loadRule0() {
  const slug = currentDeck.slug;
  rule0For = slug;
  try {
    const r = await api(`/api/decks/${enc(slug)}/rule0`);
    if (currentDeck?.slug !== slug) return;
    rule0 = r;
    $("#rule0-rows").innerHTML = rule0Rows(r.rows);
  } catch (err) { rule0For = null; $("#rule0-rows").innerHTML = `<p class="empty-inline">${esc(err.message)}</p>`; }
}
$("#rule0-copy").addEventListener("click", async () => {
  if (!rule0) return;
  try { await navigator.clipboard.writeText(rule0.text); toast("Rule-0-Text kopiert."); }
  catch { toast("Kopieren nicht möglich – markiere den Text von Hand.", "error"); }
});
$("#rule0-show").addEventListener("click", () => {
  if (!rule0) return;
  $("#rule0-full-title").textContent = rule0.title;
  $("#rule0-full-rows").innerHTML = rule0Rows(rule0.rows);
  $("#rule0-full").showModal();
});
$("#rule0-full-close").addEventListener("click", () => $("#rule0-full").close());
$("#sheet-print").addEventListener("click", () => window.print());

const bullets = (items, refs) => items?.length ? md(items.map((x) => "- " + x).join("\n"), refs) : '<p class="muted small">–</p>';
function renderGuide(d) {
  const g = d.guide;
  $("#sheet-title").textContent = `${d.name} · ${d.commanders.join(" + ")}`;
  $("#guide").hidden = !g;
  $("#guide-empty").hidden = !!g;
  $("#guide-btn span").textContent = g ? "Neu erstellen" : "Anleitung erstellen";
  $("#guide-btn").classList.toggle("primary", !g);
  if (!g) { $("#guide-meta").textContent = ""; return; }
  const stale = g.version && d.version && g.version !== d.version;
  $("#guide-meta").innerHTML = `Erstellt ${esc(fmtDate(g.created))} für v${esc(g.version)}`
    + (stale ? ` · <span class="guide-note">Das Deck hat sich seitdem geändert (jetzt v${esc(d.version)}).</span>` : "");
  const refs = g.cards || {};
  const section = (title, items) => `<div><h3>${esc(title)}</h3>${bullets(items, refs)}</div>`;
  $("#guide").innerHTML = `${md(g.plan, refs).replace("<p>", '<p class="plan">')}
    <div class="guide-cols">${section("Früh (Zug 1–3)", g.early)}${section("Mitte", g.mid)}${section("Spät", g.late)}</div>
    <div class="guide-cols two">${section("Starthand behalten?", g.mulligan)}${section("So gewinnst du", g.win_conditions)}</div>
    <div class="key-block"><h3>Schlüsselkarten</h3><ul class="key-cards">${(g.key_cards || []).map((k) =>
      `<li>${cardRef(k.name, { [k.name]: { image: k.image } })} – ${esc(k.why)}</li>`).join("")}</ul></div>
    <div class="guide-cols two">${section("Worauf achten", g.watch_out)}${section("Tipps", g.tips)}</div>`;
}
function onGuide(guide, slug) {
  if (currentDeck?.slug !== slug) return;
  currentDeck.guide = guide;
  renderGuide(currentDeck);
  toast("Die Anleitung ist fertig.");
}
$("#guide-btn").addEventListener("click", async () => {
  if (!currentDeck || currentJob) return;
  const { slug, name } = currentDeck;
  if (currentDeck.guide && !(await ask({ title: "Anleitung neu erstellen?", text: "Die bisherige Anleitung wird ersetzt.", ok: "Neu erstellen" }))) return;
  try {
    const { job } = await api(`/api/decks/${enc(slug)}/guide`, { method: "POST", body: {} });
    startJob(job, `Claude schreibt die Anleitung für ${name}`, { kind: "guide", slug, slot: "#guide-job-slot", route: `#/deck/${enc(slug)}/anleitung` });
  } catch (err) { fail(err); }
});
$("#guide").addEventListener("click", (e) => {
  const ref = e.target.closest(".card-ref");
  if (ref) showCardView(ref.dataset.name, { image: ref.dataset.img, image_back: ref.dataset.imgBack, scryfall_uri: ref.dataset.uri });
});

// ============================================================================================
// tab "Fragen": read-only questions about the deck
// ============================================================================================
let qaRun = null;  // { job, slug, question, source, error, finished }

function cardRef(name, refs) {
  const r = refs[name] || currentDeck?.card_data?.[name] || {};
  return `<span class="card card-ref" data-img="${esc(r.image || "")}" data-img-back="${esc(r.image_back || "")}"
    data-name="${esc(name)}" data-uri="${esc(r.scryfall_uri || "")}">${esc(name)}</span>`;
}

// Small Markdown subset for answers: headings, lists, tables, bold/italic/code, [[Card]] refs.
// Everything is escaped first; only the tags generated here end up in the HTML.
function md(text, refs = {}) {
  const names = [];
  const raw = String(text || "").replace(/\[\[([^\[\]]+)\]\]/g, (_, n) => `\u0001${names.push(n.trim()) - 1}\u0001`);
  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(„])[*_]([^*_\s][^*_]*?)[*_](?=[\s).,:;!?“]|$)/g, "$1<em>$2</em>");
  let html = "", para = [], list = null, table = [];
  const flushPara = () => { if (para.length) html += `<p>${inline(para.join(" "))}</p>`; para = []; };
  const flushList = () => { if (list) html += `</${list}>`; list = null; };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|[\s:|-]+\|$/.test(r))
      .map((r) => r.slice(1, -1).split("|").map((c) => inline(c.trim())));
    html += "<table>" + rows.map((cells, i) => {
      const tag = i === 0 && table.length > 1 && /^\|[\s:|-]+\|$/.test(table[1]) ? "th" : "td";
      return `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`;
    }).join("") + "</table>";
    table = [];
  };
  for (const line of esc(raw).split(/\r?\n/)) {
    const t = line.trim();
    if (/^\|.*\|$/.test(t)) { flushPara(); flushList(); table.push(t); continue; }
    flushTable();
    let m;
    if (!t) { flushPara(); flushList(); }
    else if ((m = t.match(/^#{1,6}\s+(.*)$/))) { flushPara(); flushList(); html += `<h4>${inline(m[1])}</h4>`; }
    else if (/^([-*_])\1{2,}$/.test(t)) { flushPara(); flushList(); html += "<hr>"; }
    else if ((m = t.match(/^(?:[-*+•]|(\d+)[.)])\s+(.*)$/))) {
      flushPara();
      const kind = m[1] ? "ol" : "ul";
      if (list !== kind) { flushList(); html += `<${kind}>`; list = kind; }
      html += `<li>${inline(m[2])}</li>`;
    }
    else if ((m = t.match(/^&gt;\s?(.*)$/))) { flushPara(); flushList(); html += `<p><em>${inline(m[1])}</em></p>`; }
    else { flushList(); para.push(t); }
  }
  flushPara(); flushList(); flushTable();
  return html.replace(/\u0001(\d+)\u0001/g, (_, i) => cardRef(names[Number(i)], refs));
}

function qaItem(q) {
  const older = q.version && currentDeck?.version && q.version !== currentDeck.version;
  const ver = q.version ? ` · v${q.version}${older ? " (ältere Version)" : ""}` : "";
  return `<article class="qa-item" data-id="${esc(q.id)}">
    <div class="qa-q"><span>${esc(q.question)}</span>
      <span class="meta">${esc(fmtDate(q.asked))}${esc(ver)}<button type="button" class="qa-del" title="Frage löschen" aria-label="Frage löschen">✕</button></span></div>
    <div class="qa-a" tabindex="0" role="region" aria-label="Antwort">${md(q.answer, q.cards || {})}</div></article>`;
}

function updateQaCount() {
  const n = $("#qa-list").children.length;
  $("#qa-count").textContent = n || "";
  $("#qa-clear").hidden = !n;
}

async function renderQuestions(d) {
  const items = await api(`/api/decks/${enc(d.slug)}/questions`).catch(() => []);
  if (currentDeck?.slug !== d.slug) return;
  $("#qa-list").innerHTML = items.map(qaItem).join("");
  updateQaCount();
  updateQaLive();
}

function updateQaLive() {
  const run = qaRun && currentDeck && qaRun.slug === currentDeck.slug ? qaRun : null;
  $("#qa-live").hidden = !run;
  $("#qa-btn").disabled = !!(qaRun && !qaRun.finished);
  if (!run) return;
  $("#qa-question").textContent = run.question;
  $("#qa-live .spinner").hidden = !!run.finished;
  $("#qa-status").textContent = run.error ? "Fehler: " + run.error : run.status || "Claude denkt nach …";
  $("#qa-status").classList.toggle("bad", !!run.error);
  $("#qa-cancel").textContent = run.finished ? "Schließen" : "Abbrechen";
}

function onQaEvent(run, ev) {
  switch (ev.type) {
    case "tool": run.status = toolText(ev.name); break;
    case "status": run.status = ev.text; break;
    case "error": run.error = ev.text; break;
    case "answer":
      if (currentDeck?.slug === run.slug) {
        $("#qa-list").insertAdjacentHTML("beforeend", qaItem(ev.entry));
        updateQaCount();
        $("#qa-list").lastElementChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } else toast(`Antwort zu „${run.question}“ ist da.`);
      break;
    case "done":
      run.source.close();
      run.finished = true;
      if (ev.ok) qaRun = null;
      else run.error ||= "Keine Antwort erhalten.";
      break;
  }
  updateQaLive();
}

$("#qa-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck || (qaRun && !qaRun.finished)) return;
  const question = e.target.elements.question.value.trim();
  if (!question) return;
  try {
    const slug = currentDeck.slug;
    const { job } = await api(`/api/decks/${enc(slug)}/ask`, { method: "POST", body: { question } });
    const run = { job, slug, question, source: new EventSource(`/api/jobs/${job}/events`) };
    run.source.onmessage = (ev) => onQaEvent(run, JSON.parse(ev.data));
    qaRun = run;
    e.target.reset();
    updateQaLive();
  } catch (err) { fail(err); }
});
$("#qa-form textarea").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#qa-form").requestSubmit(); }
});
$("#qa-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  const box = $("#qa-form textarea");
  box.value = chip.dataset.q;
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
});
$("#qa-cancel").addEventListener("click", async () => {
  if (!qaRun) return;
  if (!qaRun.finished) await api(`/api/jobs/${qaRun.job}/cancel`, { method: "POST" }).catch(() => {});
  else { qaRun = null; updateQaLive(); }
});
$("#qa-list").addEventListener("click", async (e) => {
  const ref = e.target.closest(".card-ref");
  if (ref) { showCardView(ref.dataset.name, { image: ref.dataset.img, image_back: ref.dataset.imgBack, scryfall_uri: ref.dataset.uri }); return; }
  const del = e.target.closest(".qa-del");
  if (!del || !currentDeck) return;
  const item = del.closest(".qa-item");
  await api(`/api/decks/${enc(currentDeck.slug)}/questions?id=${enc(item.dataset.id)}`, { method: "DELETE" }).catch(fail);
  item.remove();
  updateQaCount();
});
$("#qa-clear").addEventListener("click", async () => {
  if (!currentDeck) return;
  const ok = await ask({ title: "Alle Fragen löschen?", text: "Alle Fragen und Antworten zu diesem Deck werden entfernt.", ok: "Löschen", danger: true });
  if (!ok) return;
  await api(`/api/decks/${enc(currentDeck.slug)}/questions`, { method: "DELETE" }).catch(fail);
  renderQuestions(currentDeck);
});

// ============================================================================================
// tab "Verlauf": versions, diffs, restore, copy
// ============================================================================================
let versions = [];

async function renderHistory(d) {
  versions = await api(`/api/decks/${enc(d.slug)}/versions`).catch(() => []);
  if (currentDeck?.slug !== d.slug) return;
  $("#version-count").textContent = versions.length > 1 ? versions.length : "";
  $("#compare-result").hidden = true;
  const cur = (d.currency || "eur").toUpperCase();
  $("#history").innerHTML = versions.length ? versions.slice().reverse().map((h) => `<li>
    <div class="vhead">
      <span class="vnum">${h.version ? "v" + h.version : "–"}</span>
      ${h.current ? '<span class="pill ok">aktuell</span>' : ""}
      <span class="when">${esc(fmtDate(h.at))}</span>
      <span class="muted small">${h.from && h.from !== h.to ? `${esc(h.from)} → ` : ""}${esc(h.to || "")}
        · ${esc(fmtPrice(h.price, cur))} · Power ${esc(fmtPower(h.power))}</span>
    </div>
    ${h.note ? `<div>${esc(h.note)}</div>` : ""}
    ${h.added?.length ? `<div class="plus">+ ${esc(h.added.join(", "))}</div>` : ""}
    ${h.removed?.length ? `<div class="minus">− ${esc(h.removed.join(", "))}</div>` : ""}
    ${h.version ? `<div class="vactions">
      ${!h.current ? `<button type="button" class="btn small" data-act="diff" data-v="${h.version}">Mit aktuell vergleichen</button>` : ""}
      ${h.restorable && !h.current ? `<button type="button" class="btn small" data-act="restore" data-v="${h.version}">Wiederherstellen</button>` : ""}
      ${h.restorable ? `<button type="button" class="btn small ghost" data-act="copy" data-v="${h.version}">Als neues Deck</button>` : ""}
      ${h.restorable ? `<button type="button" class="btn small ghost" data-act="text" data-v="${h.version}">Liste kopieren</button>` : ""}
    </div>` : ""}
  </li>`).join("") : '<li class="empty-inline">Noch keine Versionen – jede Änderung durch Claude legt eine an.</li>';
  const opts = versions.filter((h) => h.restorable).map((h) => `<option value="${h.version}">v${h.version}${h.current ? " (aktuell)" : ""}</option>`).join("");
  const form = $("#compare-form").elements;
  form.a.innerHTML = opts;
  form.b.innerHTML = opts;
  const n = versions.filter((h) => h.restorable).length;
  if (n > 1) form.a.selectedIndex = n - 2;
  form.b.selectedIndex = n - 1;
  $("#compare-form").hidden = n < 2;
}

async function showDiff(a, b) {
  const r = await api(`/api/decks/${enc(currentDeck.slug)}/diff?a=${a}${b ? `&b=${b}` : ""}`);
  const box = $("#compare-result");
  const delta = (x, f) => (x.from === x.to || x.from == null || x.to == null ? esc(f(x.to)) : `${esc(f(x.from))} → <b>${esc(f(x.to))}</b>`);
  box.innerHTML = `<b>v${r.from_version} → v${r.to_version}</b>
    <dl class="kv">
      <dt>Stufe</dt><dd>${r.level.from === r.level.to ? esc(r.level.to) : `${esc(r.level.from)} → <b>${esc(r.level.to)}</b>`}</dd>
      <dt>Power</dt><dd>${delta(r.power, fmtPower)}</dd>
      <dt>Preis</dt><dd>${delta(r.price, (v) => fmtPrice(v, (currentDeck.currency || "eur").toUpperCase()))}</dd>
      <dt>Rein (${r.added.length})</dt><dd class="plus">${esc(r.added.join(", ") || "–")}</dd>
      <dt>Raus (${r.removed.length})</dt><dd class="minus">${esc(r.removed.join(", ") || "–")}</dd>
    </dl>`;
  box.hidden = false;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("#compare-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target.elements;
  showDiff(f.a.value, f.b.value).catch(fail);
});

$("#history").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || !currentDeck) return;
  const v = Number(btn.dataset.v);
  try {
    if (btn.dataset.act === "diff") await showDiff(v);
    if (btn.dataset.act === "copy") await copyAsDeck(v);
    if (btn.dataset.act === "text") {
      const snap = await api(`/api/decks/${enc(currentDeck.slug)}/versions/${v}`);
      await navigator.clipboard.writeText(snap.export_text);
      toast(`Liste von v${v} kopiert.`);
    }
    if (btn.dataset.act === "restore") {
      const ok = await ask({ title: `Version ${v} wiederherstellen?`, text: "Sie wird als neue Version gespeichert – nichts geht verloren.", ok: "Wiederherstellen" });
      if (!ok) return;
      await api(`/api/decks/${enc(currentDeck.slug)}/versions/${v}/restore`, { method: "POST" });
      currentDeck = null;
      await refreshDeckList();
      await route();
      toast(`Version ${v} wiederhergestellt.`);
    }
  } catch (err) { fail(err); }
});

// ============================================================================================
// tab "Drucken": print studio (MPC Autofill, PDF)
// ============================================================================================
let printPlan = null;
let prepared = { faces: {} };
const printOpts = () => {
  const f = $("#print-form").elements;
  return { source: f.source.value, stock: f.stock.value, foil: f.foil.checked, upscale: f.upscale.checked,
    only_missing: !f.only_missing.closest("[hidden]") && f.only_missing.checked,
    tokens: f.tokens.checked ? Math.min(20, Math.max(1, Number(f.token_copies.value) || 1)) : 0 };
};
$("#print-form").addEventListener("submit", (e) => { e.preventDefault(); loadPlan(); });
const planQuery = () => { const o = printOpts(); return new URLSearchParams({ source: o.source, only_missing: o.only_missing, tokens: o.tokens }).toString(); };

async function loadPlan() {
  $("#print-summary").textContent = "Lade Vorschau …";
  $("#print-grid").innerHTML = "";
  const slug = currentDeck.slug;
  try {
    [printPlan, prepared] = await Promise.all([
      api(`/api/decks/${enc(slug)}/print/plan?${planQuery()}`),
      api(`/api/decks/${enc(slug)}/print/prepared`),
    ]);
  } catch (err) { $("#print-summary").textContent = err.message; return; }
  if (currentDeck?.slug !== slug) return;
  renderPlan();
  renderPreparedInfo();
  updateDownloadLinks();
}

function renderPreparedInfo() {
  const faces = Object.values(prepared.faces || {});
  const has = faces.length > 0;
  $("#open-folder-btn").hidden = !has;
  $("#prepared-info").hidden = !has;
  if (!has) return;
  const ai = faces.filter((f) => f.upscaled).length;
  $("#prepared-info").innerHTML = `${icon("check")} ${faces.length} Druckbilder in <code>${esc(prepared.images_dir)}</code>`
    + (ai ? ` · ${ai} KI-hochskaliert (${esc(prepared.upscale_model)}${prepared.descreen && prepared.descreen !== "off" ? ", Druckraster entfernt: " + esc(prepared.descreen) : ""})` : "")
    + " · 🔍 auf einer Karte zeigt Vorher/Nachher.";
}

function originTag(img) {
  if (!img) return "";
  if (img.custom) return '<span class="tag own">eigene Wahl</span>';
  return img.origin === "mpcfill" ? '<span class="tag mpc">MPC</span>' : '<span class="tag">Scryfall</span>';
}

function renderPlan() {
  renderPlanSummary();
  $("#print-grid").innerHTML = printPlan.cards.map((c, i) => cardTile(c, i, "front")).join("");
}

function renderPlanSummary() {
  const p = printPlan;
  const imgs = p.cards.flatMap((c) => [c.front?.image, c.back?.image]).filter(Boolean);
  const mpc = imgs.filter((i) => i.origin === "mpcfill").length;
  $("#print-summary").innerHTML = `${p.quantity} Karten · MPC-Staffel ${p.mpc_bracket} · ${mpc} MPC-Autofill-Scans, ${imgs.length - mpc} Scryfall`
    + ` · ${p.cards.filter((c) => c.back).length} doppelseitig`
    + (p.server ? "" : ' · <span class="warn">kein MPC-Autofill-Server eingestellt (nur Scryfall)</span>')
    + (p.missing.length ? ` · <span class="bad">ohne Bild: ${esc(p.missing.join(", "))}</span>` : "")
    + (p.warnings.length ? `<br><span class="warn">${esc(p.warnings.join(" "))}</span>` : "");
}

function cardTile(c, i, side) {
  const f = c[side];
  const img = f?.image;
  return `<button type="button" class="pcard" data-i="${i}" data-side="${side}" title="${esc(c.name)} – Bild wählen">
    ${img ? `<img src="${esc(img.thumb)}" alt="${esc(f.face)}" loading="lazy">` : `<div class="noimg">${esc(c.name)}<br>kein Bild</div>`}
    <div class="tags">${c.qty > 1 ? `<span class="tag">${c.qty}×</span>` : ""}${c.commander ? '<span class="tag">Commander</span>' : ""}${c.token ? '<span class="tag tok">Token</span>' : ""}${c.back ? '<span class="tag dfc" title="Doppelseitige Karte – ↻ dreht sie um">DFC</span>' : ""}${originTag(img)}${f && prepared.faces?.[f.face]?.upscaled ? '<span class="tag ai">KI</span>' : ""}</div>
    ${c.back ? `<span class="flip" data-flip="${i}" title="${side === "front" ? "Rückseite zeigen" : "Vorderseite zeigen"}" aria-label="Karte umdrehen">↻</span>` : ""}
    ${f && prepared.faces?.[f.face] ? `<span class="zoom" data-compare="${esc(f.face)}" title="Vorher/Nachher vergleichen">🔍 ${prepared.faces[f.face].dpi ? esc(prepared.faces[f.face].dpi) + " DPI" : ""}</span>` : ""}
    <div class="cap">${esc(f?.face || c.name)}${c.back ? `<span class="muted"> · ${side === "front" ? "Vorderseite" : "Rückseite"}</span>` : ""}</div>
  </button>`;
}

$("#print-grid").addEventListener("click", (e) => {
  const cmp = e.target.closest("[data-compare]");
  if (cmp) { openCompare(cmp.dataset.compare); return; }
  const flip = e.target.closest("[data-flip]");
  const tile = e.target.closest(".pcard");
  if (!tile) return;
  const i = Number(tile.dataset.i);
  if (flip) {
    const side = tile.dataset.side === "front" ? "back" : "front";
    tile.outerHTML = cardTile(printPlan.cards[i], i, side);
    return;
  }
  openPicker(i, tile.dataset.side);
});

// image picker: MPC Autofill scans + every Scryfall printing, loaded page by page (175 per page)
let pickerCtx = null;
async function openPicker(i, side) {
  const c = printPlan.cards[i];
  pickerCtx = { i, card: c, side, face: c[side].face, token: !!c.token, options: [], page: 0, hasMore: true, total: 0, loading: false };
  $("#picker-title").textContent = `${c[side].face}${side === "back" ? " (Rückseite)" : ""}`;
  $("#picker-filter").value = "";
  $("#picker-hint").textContent = "Lade Bilder von MPC Autofill und die Scryfall-Drucke …";
  $("#picker-grid").innerHTML = "";
  $("#picker-more").hidden = true;
  $("#picker").showModal();
  await loadPickerPage();
}

async function loadPickerPage() {
  const ctx = pickerCtx;
  if (!ctx || ctx.loading || !ctx.hasMore) return;
  ctx.loading = true;
  $("#picker-more-btn").disabled = $("#picker-all-btn").disabled = true;
  try {
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/print/alternatives?card=${enc(ctx.card.name)}&side=${ctx.side}&token=${ctx.token}&page=${ctx.page + 1}`);
    if (pickerCtx !== ctx) return;
    ctx.options.push(...r.options);
    ctx.page = r.page;
    ctx.hasMore = r.has_more;
    ctx.total = r.scryfall_total;
    renderPicker();
  } catch (err) { $("#picker-hint").textContent = err.message; }
  finally { ctx.loading = false; $("#picker-more-btn").disabled = $("#picker-all-btn").disabled = false; }
}

function renderPicker() {
  const ctx = pickerCtx;
  const q = $("#picker-filter").value.trim().toLowerCase();
  const current = ctx.card[ctx.side].image?.id;
  const mpc = ctx.options.filter((o) => o.origin === "mpcfill").length;
  const scry = ctx.options.length - mpc;
  const shown = ctx.options.map((o, k) => [o, k]).filter(([o]) => !q || `${o.label || ""} ${o.released || ""} ${o.dpi || ""}`.toLowerCase().includes(q));
  $("#picker-hint").textContent = `${mpc ? `${mpc} MPC-Autofill-Scans (druckoptimiert) · ` : ""}${scry} von ${ctx.total} Scryfall-Drucken geladen`
    + (q ? ` · ${shown.length} passen zum Filter` : "") + (ctx.hasMore && q ? " – „Alle laden“ durchsucht alle Drucke" : "");
  $("#picker-grid").innerHTML = shown.map(([o, k]) => `<button type="button" class="pcard ${o.id === current ? "selected" : ""}" data-k="${k}">
    <img src="${esc(o.thumb)}" alt="" loading="lazy">
    <div class="tags">${o.origin === "mpcfill" ? '<span class="tag mpc">MPC</span>' : '<span class="tag">Scryfall</span>'}${o.dpi ? `<span class="tag">${esc(o.dpi)} DPI</span>` : ""}</div>
    <div class="cap">${esc(o.label || "")}${o.released ? ` · ${esc(o.released.slice(0, 4))}` : ""}</div></button>`).join("")
    || '<p class="muted">Keine passenden Bilder.</p>';
  $("#picker-more").hidden = !ctx.hasMore;
}
$("#picker-filter").addEventListener("input", debounce(() => pickerCtx && renderPicker(), 120));
$("#picker-more-btn").addEventListener("click", loadPickerPage);
$("#picker-all-btn").addEventListener("click", async () => {
  const ctx = pickerCtx;
  while (ctx && pickerCtx === ctx && ctx.hasMore && $("#picker").open) await loadPickerPage();
});

async function pick(option) {
  const { i, card, side, face } = pickerCtx;
  const slug = currentDeck.slug;
  await api(`/api/decks/${enc(slug)}/print/choose`, { method: "POST", body: { face, option } });
  $("#picker").close();
  // update just this tile: re-rendering the whole grid would make the page jump to the top
  const plan = await api(`/api/decks/${enc(slug)}/print/plan?${planQuery()}`);
  if (currentDeck?.slug !== slug) return;
  printPlan = plan;
  renderPlanSummary();
  const j = plan.cards.findIndex((c) => c.name === card.name);
  const tile = $(`#print-grid .pcard[data-i="${i}"]`);
  if (j < 0 || !tile) { renderPlan(); return; }
  tile.outerHTML = cardTile(plan.cards[j], j, side);
  $(`#print-grid .pcard[data-i="${j}"]`)?.focus({ preventScroll: true });
}
$("#picker-grid").addEventListener("click", (e) => {
  const t = e.target.closest("[data-k]");
  if (t) pick(pickerCtx.options[Number(t.dataset.k)]).catch(fail);
});
$("#picker-auto").addEventListener("click", () => pick(null).catch(fail));
$("#picker-close").addEventListener("click", () => $("#picker").close());

$("#prepare-btn").addEventListener("click", async () => {
  if (!currentDeck || currentJob) return;
  const { slug, name } = currentDeck;
  try {
    const { job } = await api(`/api/decks/${enc(slug)}/print/prepare`, { method: "POST", body: printOpts() });
    startJob(job, `Druckdateien für ${name}`, { kind: "print", slug, slot: "#print-job-slot", route: `#/deck/${enc(slug)}/drucken` });
    $("#print-job-slot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { fail(err); }
});

// ---------- before/after comparison ----------
let compareFace = null;
function imageUrl(face, kind) {
  return `/api/decks/${enc(currentDeck.slug)}/print/image?face=${enc(face)}&kind=${kind}&t=${Date.now()}`;
}
function openCompare(face) {
  compareFace = face;
  const info = prepared.faces[face];
  $("#compare-title").textContent = `${face} – Vorher / Nachher`;
  const originLabel = { scryfall: "Scryfall-Scan", mpcfill: "MPC-Autofill-Scan", local: "eigene Datei" }[info.origin] || info.origin;
  $("#compare-cap-a").textContent = `Original: ${originLabel}`;
  $("#compare-cap-b").textContent = `Druckdatei: ${info.dpi ? info.dpi + " DPI" : ""}${info.upscaled ? " · KI-hochskaliert" : info.origin === "scryfall" ? " · nicht hochskaliert" : ""}`;
  loadCompareImages();
  $("#compare").showModal();
}
function loadCompareImages() {
  const bleed = $("#compare-bleed").checked;
  const a = $("#compare-a"), b = $("#compare-b");
  a.onload = applyZoom;
  a.src = imageUrl(compareFace, "original");
  b.src = imageUrl(compareFace, bleed ? "file" : "trim");
  b.onload = () => {
    $("#compare-cap-b").title = `${b.naturalWidth} × ${b.naturalHeight} px`;
    applyZoom();
  };
}
function applyZoom() {
  const z = Number($("#compare-zoom").value);
  const pane = $("#pane-a");
  // keep the visible centre when zooming
  const cx = (pane.scrollLeft + pane.clientWidth / 2) / Math.max(pane.scrollWidth, 1);
  const cy = (pane.scrollTop + pane.clientHeight / 2) / Math.max(pane.scrollHeight, 1);
  const img = $("#compare-b").naturalWidth ? $("#compare-b") : $("#compare-a");
  const aspect = img.naturalWidth ? img.naturalWidth / img.naturalHeight : 63 / 88;
  const fit = Math.min(pane.clientWidth, pane.clientHeight * aspect);  // whole card visible at "Einpassen"
  const width = fit * z;
  for (const im of [$("#compare-a"), $("#compare-b")]) im.style.width = `${width}px`;
  for (const p of [$("#pane-a"), $("#pane-b")]) {
    p.scrollLeft = cx * p.scrollWidth - p.clientWidth / 2;
    p.scrollTop = cy * p.scrollHeight - p.clientHeight / 2;
  }
  const a = $("#compare-a");
  if (a.naturalWidth) $("#compare-cap-a").title = `${a.naturalWidth} × ${a.naturalHeight} px`;
}
let syncLock = null;  // the pane the user is scrolling; the other one follows
let syncTimer = null;
for (const [src, dst] of [["#pane-a", "#pane-b"], ["#pane-b", "#pane-a"]]) {
  $(src).addEventListener("scroll", () => {
    if (syncLock && syncLock !== src) return;
    syncLock = src;
    const s = $(src), d = $(dst);
    d.scrollLeft = s.scrollLeft * (d.scrollWidth / Math.max(s.scrollWidth, 1));
    d.scrollTop = s.scrollTop * (d.scrollHeight / Math.max(s.scrollHeight, 1));
    clearTimeout(syncTimer);
    syncTimer = setTimeout(() => { syncLock = null; }, 120);
  });
}
$("#compare-zoom").addEventListener("change", applyZoom);
$("#compare-bleed").addEventListener("change", loadCompareImages);
$("#compare-close").addEventListener("click", () => $("#compare").close());

$("#open-folder-btn").addEventListener("click", async () => {
  try {
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/print/open-folder`, { method: "POST" });
    if (!r.opened) toast(`Ordner: ${r.path}`);
  } catch (err) { fail(err); }
});

async function onPrepared(result) {
  logLine("result", `Druckbilder: ${result.images_dir}`);
  if (result.missing.length) toast(`Ohne Bild: ${result.missing.join(", ")}`, "error");
  if (currentDeck && printLoadedFor === currentDeck.slug) {
    prepared = await api(`/api/decks/${enc(currentDeck.slug)}/print/prepared`).catch(() => prepared);
    renderPlan();
    renderPreparedInfo();
    updateDownloadLinks();
  }
}

async function updateDownloadLinks() {
  const base = `/api/decks/${enc(currentDeck.slug)}/print/files`;
  for (const [id, kind] of [["#xml-link", "xml"], ["#pdf-link", "pdf"]]) {
    const ok = (await fetch(`${base}/${kind}`, { method: "HEAD" }).catch(() => null))?.ok;
    $(id).href = `${base}/${kind}`;
    $(id).hidden = !ok;
  }
}

$("#pdf-btn").addEventListener("click", async () => {
  const btn = $("#pdf-btn");
  btn.disabled = true;
  btn.textContent = "Erstelle PDF …";
  try {
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/print/pdf`, { method: "POST", body: { paper: $("#pdf-paper").value, include_backs: $("#pdf-backs").checked } });
    await updateDownloadLinks();
    window.open($("#pdf-link").href, "_blank");
    toast(`PDF erstellt: ${r.cards} Karten auf ${r.pages} Seiten.`);
  } catch (err) { fail(err); }
  finally { btn.disabled = false; btn.textContent = "PDF erstellen"; }
});

$("#mpc-btn").addEventListener("click", async () => {
  if (!currentDeck || currentJob) return;
  const { slug, name } = currentDeck;
  try {
    const r = await api(`/api/decks/${enc(slug)}/print/autofill`, { method: "POST", body: { mode: "mpc", window: $("#mpc-window").checked, ...TERM_SIZE } });
    if (r.job) {
      startJob(r.job, `MPC Autofill: ${name}`, { kind: "autofill", slug, slot: "#print-job-slot", route: `#/deck/${enc(slug)}/drucken` });
      $("#print-job-slot").scrollIntoView({ behavior: "smooth", block: "start" });
    } else toast("MPC Autofill läuft in einem eigenen Konsolenfenster – dort weiter bedienen.");
  } catch (err) { fail(err); }
});

// ---------- terminal (MPC Autofill runs in a pseudo-terminal; its menus need arrow keys) ----------
const ANSI_RE = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b\][^\x07]*\x07|\r/g;
const TERM_SIZE = { rows: 32, cols: 110 };
const KEYS = { up: "\x1b[A", down: "\x1b[B", enter: "\r" };
let term = null;
let keyQueue = Promise.resolve();

function sendKeys(data, raw = true) {
  const job = currentJob;
  if (!job) return;
  keyQueue = keyQueue.then(() => api(`/api/jobs/${job}/input`, { method: "POST", body: { text: data, raw } })
    .catch((err) => logLine("error", err.message)));
}

function resetTerminal() {
  if (term) { term.dispose(); term = null; }
  $("#terminal").innerHTML = "";
  $("#terminal-wrap").hidden = true;
}

function openTerminal() {
  if (term) return;
  if (!window.Terminal) {  // xterm.js missing -> simple line input as fallback
    $("#console-form").hidden = false;
    return;
  }
  $("#terminal-wrap").hidden = false;
  term = new window.Terminal({ ...TERM_SIZE, fontSize: 13, cursorBlink: true, convertEol: false,
    theme: { background: "#111111" }, fontFamily: "ui-monospace, Consolas, monospace" });
  term.open($("#terminal"));
  term.onData((d) => sendKeys(d));
  term.focus();
}

document.querySelector(".term-keys").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-key]");
  if (b && currentJob) { sendKeys(KEYS[b.dataset.key]); term?.focus(); }
});

$("#console-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = e.target.elements.text;
  sendKeys(input.value, false);
  input.value = "";
});

// ============================================================================================
// my collection
// ============================================================================================
let coll = { entries: [], summary: {}, decks: {} };
let collSummary = { cards: 0 };
let collShown = 150;
const COLL_PAGE = 150;
const LANG_NAMES = { en: "EN", de: "DE", fr: "FR", it: "IT", es: "ES", pt: "PT", ja: "JA", ko: "KO", ru: "RU", zhs: "ZH", zht: "ZH" };
let collView = store.get("collview") === "grid" ? "grid" : "list";
document.querySelector(`[name="collview"][value="${collView}"]`).checked = true;

function applyCollectionPresence() {
  const has = (collSummary.cards || 0) > 0;
  $("#coll-count").textContent = has ? collSummary.cards : "";
  for (const el of $$(".needs-collection")) el.hidden = !has;
  const opt = $('#card-group option[value="owned"]');
  opt.hidden = !has;
  opt.disabled = !has;
  if (!has && cardGroup === "owned") { cardGroup = "category"; $("#card-group").value = "category"; }
}

async function refreshCollectionSummary() {
  try {
    coll = await api("/api/collection");
    collSummary = coll.summary;
  } catch { /* offline: keep the old numbers */ }
  applyCollectionPresence();
}

async function loadCollection() {
  await refreshCollectionSummary();
  renderCollection();
}

function collFiltered() {
  const q = $("#coll-q").value.trim().toLowerCase();
  const filter = document.querySelector('[name="collfilter"]:checked').value;
  const sort = $("#coll-sort").value;
  const list = coll.entries.filter((e) => (filter === "all" || (filter === "proxy") === !!e.proxy)
    && (!q || `${e.name} ${e.set_name || ""} ${e.set || ""} ${e.note || ""}`.toLowerCase().includes(q)));
  const price = (e) => Number(e.price_eur) || 0;
  const cmp = { name: (a, b) => a.name.localeCompare(b.name), qty: (a, b) => b.qty - a.qty || a.name.localeCompare(b.name),
    price: (a, b) => price(b) - price(a), added: (a, b) => (b.added || "").localeCompare(a.added || ""),
    set: (a, b) => (a.set_name || "~").localeCompare(b.set_name || "~") || a.name.localeCompare(b.name) }[sort];
  return list.sort(cmp);
}

function renderCollection() {
  const s = coll.summary || {};
  const empty = !coll.entries.length;
  $("#coll-empty").hidden = !empty;
  $("#coll-stats").innerHTML = empty ? "" : [
    `<span class="pill">${s.cards} Karten</span>`, `<span class="pill">${s.unique} verschiedene</span>`,
    `<span class="pill ok">${s.real} echt</span>`, `<span class="pill accent">${s.proxy} Proxies</span>`,
    s.value_eur ? `<span class="pill">Wert ≈ ${fmtPrice(s.value_eur, "EUR")}</span>` : "",
  ].join("");
  const list = collFiltered();
  $("#coll-result").textContent = empty ? "" : `${list.length} Einträge${list.length !== coll.entries.length ? ` (von ${coll.entries.length})` : ""}`;
  const shown = list.slice(0, collShown);
  const grid = collView === "grid";
  $("#coll-list").classList.toggle("grid", grid);
  $("#coll-list").innerHTML = shown.map((e) => {
    const decks = coll.decks?.[e.name] || [];
    const printing = [e.set_name || (e.set ? e.set.toUpperCase() : "Standard-Druck"), e.collector_number ? `#${e.collector_number}` : ""].filter(Boolean).join(" · ");
    const badges = `${e.foil ? '<span class="tagb foil">Foil</span>' : ""}${e.lang && e.lang !== "en" ? `<span class="tagb">${esc(LANG_NAMES[e.lang] || e.lang)}</span>` : ""}`;
    const img = e.image ? `<img src="${esc(e.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(e.name)}</div>`;
    if (grid) {
      return `<div class="coll-tile" data-id="${esc(e.id)}">
        <button type="button" class="card tile-img" data-act="art" data-img="${esc(e.image || "")}" data-name="${esc(e.name)}" title="Artwork ändern">${img}</button>
        <span class="qty-badge">${e.qty}×</span>${e.proxy ? '<span class="own proxy tile-p" title="Proxy">P</span>' : ""}
        <span class="tile-name">${esc(e.name)} ${badges}</span></div>`;
    }
    return `<div class="coll-row" data-id="${esc(e.id)}">
      <button type="button" class="thumb card" data-act="art" data-img="${esc(e.image || "")}" data-name="${esc(e.name)}" title="Artwork ändern" aria-label="Artwork von ${esc(e.name)} ändern">${img}</button>
      <div class="coll-main">
        <div class="coll-name">${esc(e.name)} ${badges}</div>
        <div class="muted small">${esc(printing)}${e.note ? ` · ${esc(e.note)}` : ""}${decks.length ? ` · in ${decks.length === 1 ? "Deck" : "Decks"}: ${esc(decks.join(", "))}` : ""}</div>
      </div>
      <label class="check small"><input type="checkbox" data-act="proxy" ${e.proxy ? "checked" : ""}> Proxy</label>
      <label class="check small tg-foil"><input type="checkbox" data-act="foil" ${e.foil ? "checked" : ""}> Foil</label>
      <div class="stepper" aria-label="Anzahl">
        <button type="button" class="mini" data-act="minus" aria-label="Eine weniger">−</button>
        <span class="q">${e.qty}</span>
        <button type="button" class="mini" data-act="plus" aria-label="Eine mehr">+</button>
      </div>
      <span class="price">${e.price_eur && !e.proxy ? esc(fmtPrice(Number(e.price_eur) * e.qty, "€")) : ""}</span>
      <button type="button" class="mini danger" data-act="delete" title="Entfernen" aria-label="${esc(e.name)} entfernen">✕</button>
    </div>`;
  }).join("");
  $("#coll-more").hidden = list.length <= collShown;
}

$("#coll-q").addEventListener("input", debounce(() => { collShown = COLL_PAGE; renderCollection(); }, 150));
$$('[name="collfilter"]').forEach((r) => r.addEventListener("change", () => { collShown = COLL_PAGE; renderCollection(); }));
$("#coll-sort").addEventListener("change", renderCollection);
$$('[name="collview"]').forEach((r) => r.addEventListener("change", () => { collView = r.value; store.set("collview", collView); renderCollection(); }));
$("#coll-more button").addEventListener("click", () => { collShown += COLL_PAGE; renderCollection(); });

async function patchEntry(id, body) {
  try {
    await api(`/api/collection/${enc(id)}`, { method: "PATCH", body });
    await loadCollection();
  } catch (err) { fail(err); }
}

$("#coll-list").addEventListener("click", async (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  const row = e.target.closest("[data-id]");
  if (!act || !row || act === "proxy" || act === "foil") return;
  const entry = coll.entries.find((x) => x.id === row.dataset.id);
  if (!entry) return;
  if (act === "plus") patchEntry(entry.id, { qty: entry.qty + 1 });
  if (act === "minus") {
    if (entry.qty > 1) patchEntry(entry.id, { qty: entry.qty - 1 });
    else if (await ask({ title: `${entry.name} entfernen?`, ok: "Entfernen", danger: true })) patchEntry(entry.id, { qty: 0 });
  }
  if (act === "delete" && await ask({ title: `${entry.name} entfernen?`, text: `${entry.qty}× ${entry.proxy ? "Proxy" : "echt"} – aus der Sammlung löschen.`, ok: "Entfernen", danger: true })) {
    await api(`/api/collection/${enc(entry.id)}`, { method: "DELETE" }).catch(fail);
    loadCollection();
  }
  if (act === "art") {
    const p = await pickPrinting(entry.name);
    if (p) patchEntry(entry.id, { printing: p });
  }
});
$("#coll-list").addEventListener("change", (e) => {
  const box = e.target.closest("input[data-act]");
  const row = e.target.closest("[data-id]");
  if (box && row) patchEntry(row.dataset.id, { [box.dataset.act]: box.checked });
});

// artwork picker: all printings of a card, page by page; resolves with the chosen printing (or null)
let printsCtx = null;
function renderPrints() {
  const ctx = printsCtx;
  const q = $("#prints-filter").value.trim().toLowerCase();
  const shown = ctx.prints.map((p, i) => [p, i]).filter(([p]) => !q || `${p.set_name || ""} ${p.set || ""} ${p.collector_number || ""} ${p.released || ""}`.toLowerCase().includes(q));
  $("#prints-hint").textContent = ctx.prints.length
    ? `${ctx.prints.length} von ${ctx.total} Drucken geladen${q ? ` · ${shown.length} passen zum Filter` : ""} – klick wählt das Artwork.`
    : "Keine Drucke gefunden.";
  $("#prints-grid").innerHTML = shown.map(([p, i]) => `<button type="button" class="similar" data-i="${i}">
    ${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(ctx.name)}</div>`}
    <span class="sim-name">${esc(p.set_name || p.set)}</span>
    <span class="muted small">#${esc(p.collector_number)} · ${esc((p.released || "").slice(0, 4))}${p.price_eur ? ` · ${esc(p.price_eur)} €` : ""}</span></button>`).join("");
  $("#prints-more").hidden = !ctx.hasMore;
}
async function loadPrintsPage() {
  const ctx = printsCtx;
  if (!ctx || ctx.loading || !ctx.hasMore) return;
  ctx.loading = true;
  $("#prints-more-btn").disabled = $("#prints-all-btn").disabled = true;
  try {
    const r = await api(`/api/cards/prints?name=${enc(ctx.name)}&page=${ctx.page + 1}`);
    if (printsCtx !== ctx) return;
    ctx.prints.push(...r.prints);
    ctx.page = r.page;
    ctx.hasMore = r.has_more;
    ctx.total = r.total;
    renderPrints();
  } catch (err) { $("#prints-hint").textContent = err.message; }
  finally { ctx.loading = false; $("#prints-more-btn").disabled = $("#prints-all-btn").disabled = false; }
}
$("#prints-filter").addEventListener("input", debounce(() => printsCtx && renderPrints(), 120));
$("#prints-more-btn").addEventListener("click", loadPrintsPage);
$("#prints-all-btn").addEventListener("click", async () => {
  const ctx = printsCtx;
  while (ctx && printsCtx === ctx && ctx.hasMore && $("#prints-dialog").open) await loadPrintsPage();
});

function pickPrinting(name) {
  const dlg = $("#prints-dialog");
  $("#prints-title").textContent = `Artwork: ${name}`;
  $("#prints-hint").textContent = "Lade die Drucke von Scryfall …";
  $("#prints-filter").value = "";
  $("#prints-grid").innerHTML = "";
  $("#prints-more").hidden = true;
  printsCtx = { name, prints: [], page: 0, hasMore: true, total: 0, loading: false };
  dlg.showModal();
  loadPrintsPage();
  return new Promise((resolve) => {
    const ctx = printsCtx;
    const done = (p) => { resolve(p); if (dlg.open) dlg.close(); };
    dlg.onclose = () => resolve(null);
    $("#prints-close").onclick = () => done(null);
    $("#prints-grid").onclick = (e) => {
      const b = e.target.closest("[data-i]");
      if (b) done(ctx.prints[Number(b.dataset.i)]);
    };
  });
}

// add one card
let addPrinting = null;
function openCollAdd(name = "") {
  addPrinting = null;
  $("#coll-add-form").reset();
  $("#coll-add-name").value = name;
  $("#coll-add-art").hidden = true;
  $("#coll-add-art-text").textContent = "Artwork: Standard-Druck";
  $("#coll-add-dialog").showModal();
  $("#coll-add-name").focus();
}
wireAutocomplete($("#coll-add-name"), $("#ac-coll"));
$("#coll-add-btn").addEventListener("click", () => openCollAdd());
$("#coll-add-cancel").addEventListener("click", () => $("#coll-add-dialog").close());
$("#coll-add-art-btn").addEventListener("click", async () => {
  const name = $("#coll-add-name").value.trim();
  if (!name) { $("#coll-add-name").focus(); return; }
  const p = await pickPrinting(name);
  if (!p) return;
  addPrinting = p;
  $("#coll-add-name").value = p.name || name;
  $("#coll-add-art").src = p.image || "";
  $("#coll-add-art").hidden = !p.image;
  $("#coll-add-art-text").textContent = `Artwork: ${p.set_name || p.set} · #${p.collector_number}`;
});
$("#coll-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const item = {
    name: $("#coll-add-name").value.trim(), qty: Number($("#coll-add-qty").value) || 1,
    proxy: $("#coll-add-proxy").checked, foil: $("#coll-add-foil").checked, lang: $("#coll-add-lang").value,
    ...(addPrinting ? { printing: addPrinting } : {}),
  };
  try {
    const r = await api("/api/collection", { method: "POST", body: { items: [item] } });
    if (r.not_found.length) { toast(`Nicht gefunden: ${r.not_found.join(", ")}`, "error"); return; }
    $("#coll-add-dialog").close();
    toast(`${item.qty}× ${item.name} hinzugefügt.`);
    await loadCollection();
    if (currentDeck) loadOwnership(currentDeck);
  } catch (err) { fail(err); }
});

// import
$("#coll-import-btn").addEventListener("click", () => { $("#coll-import-form").reset(); $("#coll-import-dialog").showModal(); });
$("#coll-import-cancel").addEventListener("click", () => $("#coll-import-dialog").close());
$("#coll-import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (file) $("#coll-import-text").value = await file.text();
});
$("#coll-import-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#coll-import-text").value;
  if (!text.trim()) { toast("Datei wählen oder Text einfügen.", "error"); return; }
  const replace = $("#coll-import-replace").checked;
  if (replace && !(await ask({ title: "Sammlung ersetzen?", text: "Die bisherige Sammlung wird durch den Import ersetzt.", ok: "Ersetzen", danger: true }))) return;
  const btn = $("#coll-import-ok");
  btn.disabled = true;
  btn.textContent = "Importiere …";
  try {
    const r = await api("/api/collection/import", { method: "POST", body: { text, proxy: $("#coll-import-proxy").checked, replace } });
    $("#coll-import-dialog").close();
    toast(`${r.added} Karten importiert.${r.not_found.length ? ` Nicht gefunden (${r.not_found.length}): ${r.not_found.slice(0, 5).join(", ")}${r.not_found.length > 5 ? " …" : ""}` : ""}`,
      r.not_found.length ? "error" : "info", 8000);
    await loadCollection();
  } catch (err) { fail(err); }
  finally { btn.disabled = false; btn.textContent = "Importieren"; }
});
$("#coll-empty").addEventListener("click", (e) => {
  const b = e.target.closest("[data-open]");
  if (b?.dataset.open === "import") $("#coll-import-btn").click();
  if (b?.dataset.open === "add") openCollAdd();
});
$("#coll-menu").addEventListener("click", (e) => { if (e.target.closest(".menu-list button")) $("#coll-menu").open = false; });
$("#coll-clear").addEventListener("click", async () => {
  if (!(await ask({ title: "Ganze Sammlung leeren?", text: `${collSummary.cards || 0} Karten werden gelöscht. Tipp: vorher exportieren.`, ok: "Leeren", danger: true }))) return;
  await api("/api/collection?confirm=true", { method: "DELETE" }).catch(fail);
  loadCollection();
});

// ---------- the deck vs. the collection ----------
async function loadOwnership(d) {
  ownership = null;
  $("#own-panel").hidden = true;
  if (!(collSummary.cards > 0)) return;
  try { ownership = { ...(await api(`/api/decks/${enc(d.slug)}/ownership`)), slug: d.slug }; } catch { return; }
  if (currentDeck?.slug !== d.slug) return;
  renderCards(currentDeck);
  const o = ownership;
  const pct = (n) => `${(n / Math.max(o.need, 1)) * 100}%`;
  const cur = o.currency === "usd" ? "USD" : "EUR";
  $("#own-panel").hidden = false;
  $("#own-summary").innerHTML = `
    <div class="own-bar" role="img" aria-label="${o.have_real} echt, ${o.have_proxy} als Proxy, ${o.missing} fehlen">
      <span class="r" style="width:${pct(o.have_real)}"></span><span class="p" style="width:${pct(o.have_proxy)}"></span></div>
    <p class="small"><b>${o.have_real + o.have_proxy} von ${o.need}</b> vorhanden · ${o.have_real} echt · ${o.have_proxy} Proxy
      · <b class="${o.missing ? "bad" : "ok"}">${o.missing} fehlen</b>${o.missing_price ? ` (≈ ${esc(fmtPrice(o.missing_price, cur))})` : ""}</p>
    ${o.shared_shortages.length ? `<p class="small warn">In mehreren Decks, aber zu wenige Exemplare: ${esc(o.shared_shortages.slice(0, 6).join(", "))}${o.shared_shortages.length > 6 ? " …" : ""}</p>` : ""}
    ${o.missing ? `<div class="btn-group">
      <button type="button" class="btn small" id="own-copy">Einkaufsliste kopieren</button>
      <button type="button" class="btn small" id="own-print">Fehlende drucken</button></div>` : '<p class="small ok">Du hast alle Karten dieses Decks.</p>'}`;
}
$("#own-summary").addEventListener("click", async (e) => {
  if (e.target.id === "own-copy") {
    try { await navigator.clipboard.writeText(ownership.shopping_text); toast("Einkaufsliste kopiert – z. B. bei Cardmarket als Wants-Liste einfügen."); }
    catch { toast("Kopieren nicht erlaubt.", "error"); }
  }
  if (e.target.id === "own-print") {
    $("#print-form").elements.only_missing.checked = true;
    printLoadedFor = null;
    selectTab("drucken");
  }
});

$("#add-printed-btn").addEventListener("click", async () => {
  if (!currentDeck) return;
  const opts = printOpts();
  const ok = await ask({ title: "Gedruckte Karten übernehmen?", ok: "Übernehmen",
    text: `${printPlan?.quantity ?? "Alle"} Karten dieses Druckauftrags kommen als Proxy (mit dem gewählten Artwork) in deine Sammlung.` });
  if (!ok) return;
  try {
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/collection/add-printed`, { method: "POST", body: opts });
    toast(`${r.added} Proxies zur Sammlung hinzugefügt.`);
    await refreshCollectionSummary();
    loadOwnership(currentDeck);
  } catch (err) { fail(err); }
});

// ============================================================================================
// blacklist
// ============================================================================================
async function refreshBlacklist() {
  const names = await api("/api/blacklist").catch(() => []);
  $("#bl-count").textContent = names.length || "";
  $("#bl-empty").hidden = names.length > 0;
  $("#bl-list").innerHTML = names.map((n) => `<li>${esc(n)}<button type="button" title="Entfernen" aria-label="${esc(n)} entfernen" data-name="${esc(n)}">×</button></li>`).join("");
}
$("#bl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = $("#bl-input").value.trim();
  if (!raw) return;
  const add = raw.split(/[;\n]/).map((x) => x.trim()).filter(Boolean);
  try {
    const r = await api("/api/blacklist", { method: "POST", body: { add } });
    $("#bl-input").value = "";
    $("#bl-msg").textContent = r.not_found.length ? `Nicht gefunden: ${r.not_found.join(", ")}` : "";
    refreshBlacklist();
  } catch (err) { $("#bl-msg").textContent = err.message; }
});
$("#bl-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-name]");
  if (!btn) return;
  await api("/api/blacklist", { method: "POST", body: { remove: [btn.dataset.name] } }).catch(fail);
  refreshBlacklist();
});

// ============================================================================================
// settings: proxy printing, AI upscaling, local card DB
// ============================================================================================
let appSettings = {};
async function loadSettings() {
  appSettings = await api("/api/settings");
  const f = $("#settings-form").elements;
  const MODEL_HINTS = { "realesrgan-x4plus": " (empfohlen)", "realesrgan-x4plus-anime": " (für Zeichnungen, glättet stärker)" };
  f.upscale_model.innerHTML = appSettings.upscale_models.map((m) => `<option value="${esc(m)}">${esc(m + (MODEL_HINTS[m] || ""))}</option>`).join("")
    || '<option value="">– Programm nicht gefunden –</option>';
  for (const k of ["autofill_path", "mpcfill_server", "cardback_path", "browser", "site", "upscaler_path", "upscale_model", "descreen"]) if (f[k]) f[k].value = appSettings[k] ?? "";
  f.upscale.checked = !!appSettings.upscale;
  $("#upscaler-status").innerHTML = appSettings.upscaler_found
    ? `<span class="ok">✓ gefunden:</span> ${esc(appSettings.upscaler_found)}`
    : 'Nicht installiert – nur nötig, wenn du hochskalieren willst (<a href="https://github.com/xinntao/Real-ESRGAN/releases" target="_blank" rel="noopener">Download</a>).';
  $("#print-form").elements.upscale.checked = !!appSettings.upscale;
  $("#autofill-status").innerHTML = appSettings.autofill_found
    ? `<span class="ok">✓ gefunden:</span> ${esc(appSettings.autofill_found)}`
    : '<span class="warn">Nicht gefunden</span> – Pfad eintragen oder die exe in den Ordner <code>tools/</code> legen.';
  const stock = $("#print-form").elements.stock;
  stock.innerHTML = appSettings.stocks.map((s) => `<option ${s === appSettings.stock ? "selected" : ""}>${esc(s)}</option>`).join("");
  $("#print-form").elements.foil.checked = !!appSettings.foil;
  $("#pdf-paper").value = appSettings.paper || "A4";
}
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  body.upscale = e.target.elements.upscale.checked;
  try {
    await api("/api/settings", { method: "POST", body });
    await loadSettings();
    toast("Einstellungen gespeichert.");
  } catch (err) { $("#settings-msg").textContent = err.message; }
});

async function refreshDbStatus() {
  const [st, run] = await Promise.all([api("/api/carddb"), api("/api/carddb/refresh")]).catch(() => [{}, {}]);
  const el = $("#db-status");
  if (run.running) {
    el.textContent = "Lade und importiere Bulk-Daten … (All Cards ≈ 375 MB, dauert einige Minuten)";
    $("#db-btn").disabled = true;
    setTimeout(refreshDbStatus, 5000);
    return;
  }
  $("#db-btn").disabled = false;
  if (run.error) el.innerHTML = `<span class="bad">Fehler: ${esc(run.error)}</span>`;
  else if (!st.available) el.innerHTML = '<span class="warn">Nicht vorhanden</span> – ohne lokale Datenbank wird die Scryfall-API live genutzt (langsamer, nur englische Namen).';
  else el.innerHTML = `<span class="ok">✓</span> ${st.cards} Karten, ${st.tags} Tags, ${st.languages.length} Sprachen · Stand ${esc(st.cards_updated_at?.slice(0, 10) ?? "?")}`
    + (st.schema_outdated ? ' · <span class="warn">Update empfohlen:</span> die Datenbank kennt noch keine Rückseiten doppelseitiger Karten'
      : st.needs_refresh ? ' · <span class="warn">Update empfohlen</span>' : "");
  $("#db-btn").textContent = st.available ? "Jetzt aktualisieren" : "Scryfall-Bulk-Daten laden";
}
$("#db-btn").addEventListener("click", async () => {
  await api("/api/carddb/refresh", { method: "POST" }).catch(fail);
  refreshDbStatus();
});

// ============================================================================================
// glossary: keywords, actions and Commander terms
// ============================================================================================
let glossaryItems = null;
const GLOSSARY_GROUPS = { keyword: "Schlüsselwörter", action: "Aktionen, Spielsteine & Marker", concept: "Commander-Begriffe & Spielweisen" };
async function loadGlossary() {
  if (!glossaryItems) glossaryItems = await api("/api/glossary");
  return glossaryItems;
}
const glossaryId = (term) => "gl-" + term.toLowerCase().replace(/[^a-z0-9]+/g, "-");

function renderGlossary() {
  const words = $("#gl-filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = (glossaryItems || []).filter((t) => words.every((w) => `${t.term} ${t.de} ${t.text}`.toLowerCase().includes(w)));
  $("#gl-list").innerHTML = Object.entries(GLOSSARY_GROUPS).map(([kind, title]) => {
    const items = hits.filter((t) => t.kind === kind).sort((a, b) => (a.de || a.term).localeCompare(b.de || b.term, "de"));
    return items.length ? `<section class="panel gl-group"><h2>${esc(title)} <span class="count">${items.length}</span></h2><dl>${items.map((t) =>
      `<dt id="${glossaryId(t.term)}" tabindex="-1">${esc(t.de || t.term)}${t.de ? ` <span class="de">(${esc(t.term)})</span>` : ""}</dt><dd>${esc(t.text)}</dd>`).join("")}</dl></section>` : "";
  }).join("") || '<p class="empty-inline">Kein Begriff gefunden.</p>';
}

async function showGlossary(term) {
  try { await loadGlossary(); } catch (err) { return fail(err); }
  if (term) $("#gl-filter").value = "";
  renderGlossary();
  if (term) {
    const el = document.getElementById(glossaryId(term));
    if (el) { el.scrollIntoView({ block: "center" }); el.focus({ preventScroll: true }); }
  }
}
$("#gl-filter").addEventListener("input", debounce(renderGlossary, 120));
loadGlossary().catch(() => {});  // the quick search lists glossary terms too

// ============================================================================================
// quick search (Ctrl+K): decks, pages and actions
// ============================================================================================
let paletteHits = [];
let paletteSel = 0;
function paletteItems() {
  const items = [
    { label: "Neues Deck", hint: "Seite", run: () => { go("#/new"); setMode("build"); } },
    { label: "Commander vorschlagen lassen", hint: "Neues Deck", run: () => { go("#/new"); setMode("find"); } },
    { label: "Meine Sammlung", hint: "Seite", run: () => go("#/collection") },
    { label: "Karte zur Sammlung hinzufügen", hint: "Sammlung", run: () => { go("#/collection"); openCollAdd(); } },
    { label: "Sammlung importieren", hint: "Sammlung", run: () => { go("#/collection"); $("#coll-import-btn").click(); } },
    { label: "Glossar", hint: "Seite", run: () => go("#/glossary") },
    { label: "Blacklist", hint: "Seite", run: () => go("#/blacklist") },
    { label: "Einstellungen", hint: "Seite", run: () => go("#/settings") },
  ];
  if (currentDeck && parseHash().view === "deck") {
    const d = currentDeck;
    const tabNames = { karten: "Karten", anleitung: "Anleitung & Rule 0", testen: "Testhand & Wahrscheinlichkeiten", anpassen: "Anpassen & Upgrades", fragen: "Fragen zum Deck", verlauf: "Verlauf", drucken: "Drucken" };
    for (const [tab, label] of Object.entries(tabNames)) items.push({ label, hint: d.name, run: () => selectTab(tab) });
    items.push({ label: "Karten bearbeiten", hint: d.name, run: () => { selectTab("karten"); if (!edit) setEditing(true); } });
    items.push({ label: "Liste kopieren", hint: d.name, run: () => $("#copy-btn").click() });
  }
  for (const d of deckIndex) items.push({ label: d.name, hint: `Deck · ${d.commanders.join(" + ")} · ${d.level || ""}`, run: () => go(`#/deck/${enc(d.slug)}`) });
  for (const t of glossaryItems || []) items.push({ label: t.de ? `${t.de} (${t.term})` : t.term, hint: "Glossar", run: () => go(`#/glossary/${enc(t.term)}`) });
  return items;
}
function renderPalette() {
  const words = $("#palette-input").value.toLowerCase().split(/\s+/).filter(Boolean);
  paletteHits = paletteItems().filter((it) => words.every((w) => `${it.label} ${it.hint}`.toLowerCase().includes(w))).slice(0, 14);
  paletteSel = Math.min(paletteSel, Math.max(0, paletteHits.length - 1));
  $("#palette-list").innerHTML = paletteHits.map((it, i) => `<li role="option" id="pal-${i}" data-i="${i}" aria-selected="${i === paletteSel}">
    <span>${esc(it.label)}</span><span class="muted small">${esc(it.hint)}</span></li>`).join("") || '<li class="muted small">Nichts gefunden.</li>';
  $("#palette-input").setAttribute("aria-activedescendant", paletteHits.length ? `pal-${paletteSel}` : "");
  $(`#pal-${paletteSel}`)?.scrollIntoView({ block: "nearest" });
}
function openPalette() {
  if ($("#palette").open) return;
  $("#palette-input").value = "";
  paletteSel = 0;
  renderPalette();
  setNavOpen(false);
  $("#palette").showModal();
  $("#palette-input").focus();
}
function runPalette(i) {
  const it = paletteHits[i];
  if (!it) return;
  $("#palette").close();
  it.run();
}
$("#palette-btn").addEventListener("click", openPalette);
$("#palette-input").addEventListener("input", () => { paletteSel = 0; renderPalette(); });
$("#palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const n = paletteHits.length;
    if (n) paletteSel = (paletteSel + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
    renderPalette();
  } else if (e.key === "Enter") { e.preventDefault(); runPalette(paletteSel); }
});
$("#palette-list").addEventListener("click", (e) => { const li = e.target.closest("li[data-i]"); if (li) runPalette(Number(li.dataset.i)); });
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
});
if (/Mac|iPhone|iPad/.test(navigator.platform)) $("#palette-btn kbd").textContent = "⌘ K";

// ============================================================================================
// start
// ============================================================================================
wireAutocomplete($("#commander"), $("#ac-commander"), previewCommander);
wireAutocomplete($("#partner"), $("#ac-partner"));
wireAutocomplete($("#bl-input"), $("#ac-bl"));
setMode("build");
refreshBlacklist();
loadSettings().catch((err) => console.error(err));
Promise.all([initBrackets(), refreshDeckList(), refreshCollectionSummary()]).then(route, (err) => { fail(err); route(); });
