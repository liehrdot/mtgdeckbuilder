"use strict";

// ============================================================================================
// helpers
// ============================================================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new Error("Keine Verbindung zur App – läuft mtg-gui noch? Bitte neu starten und die Seite neu laden.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(res, data));
  return data;
}

// a readable German message for a failed request
function errorText(res, data) {
  const d = data?.detail;
  if (typeof d === "string" && d) return d;
  if (Array.isArray(d) && d.length) {  // pydantic: [{loc, msg}]
    return "Ungültige Eingabe: " + d.map((e) => `${(e.loc || []).filter((x) => x !== "body").join(".")} ${e.msg || ""}`.trim()).join("; ");
  }
  if (res.status >= 500) return `Interner Fehler (${res.status}) – Details stehen im Fenster, in dem mtg-gui läuft.`;
  if (res.status === 404) return "Nicht gefunden – vielleicht wurde es inzwischen gelöscht.";
  return `Anfrage fehlgeschlagen (${res.status})`;
}

// follow a job's events; onLost(text) when the job is gone (app restarted) – null while reconnecting
function jobStream(jobId, onEvent, onLost) {
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.onmessage = (e) => onEvent(JSON.parse(e.data));
  es.onopen = () => onLost(false);
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      onLost("Die Verbindung zum Auftrag ist weg – vermutlich wurde die App neu gestartet. Bitte den Auftrag noch einmal starten.");
    } else onLost(null);  // the browser reconnects; the server resumes via Last-Event-ID
  };
  return es;
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const store = {  // per-browser conveniences only (view mode); failures are harmless
  get(k) { try { return localStorage.getItem("mtgdeck." + k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem("mtgdeck." + k, v); } catch { /* ignore */ } },
};

// German number format everywhere: 32,00 € · 3,8 · 1.234
const fmtNum = (v, suffix = "", digits = null) => {
  if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return v === null || v === undefined || v === "" ? "–" : `${v}${suffix}`;
  const opts = digits === null ? { maximumFractionDigits: 2 } : { minimumFractionDigits: digits, maximumFractionDigits: digits };
  return `${Number(v).toLocaleString("de-DE", opts)}${suffix}`;
};
const fmtPower = (v) => fmtNum(v, "", 1);
const CURRENCY_SIGNS = { eur: "€", usd: "$", "€": "€", "$": "$" };
const fmtPrice = (v, cur = "") => fmtNum(v, cur ? " " + (CURRENCY_SIGNS[String(cur).toLowerCase()] || cur) : "", 2);
const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso.replace("T", " ").slice(0, 16) : d.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
};
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;

// non-blocking notifications (success, hints, errors)
function toast(text, kind = "info", ms = 4500, action = null) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === "error" ? "alert" : "check")}<span>${esc(text)}</span>`;
  if (action) {  // e.g. { label: "Rückgängig", run: () => … }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "toast-action";
    b.textContent = action.label;
    b.addEventListener("click", () => { el.remove(); action.run(); });
    el.appendChild(b);
  }
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
const VIEWS = ["new", "job", "chat", "deck", "collection", "orders", "deskmat", "glossary", "opponents", "tables", "blacklist", "settings"];
const TABS = ["karten", "anleitung", "testen", "anpassen", "fragen", "partien", "verlauf", "drucken"];
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
  if (r.view === "settings") { refreshDbStatus(); refreshBackups(); refreshTrash(); refreshSync({ quiet: true }); }
  if (r.view === "collection") loadCollection();
  if (r.view === "glossary") showGlossary(r.slug);
  if (r.view === "deskmat") showDeskmat(r.slug);
  if (r.view === "orders") showOrders(r.slug);
  if (r.view === "tables") showTables(r.slug);
  if (r.view === "opponents") showOpponents(r.slug);
  if (r.view === "chat") showChat(r.slug);
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
    : { new: "Neues Deck", job: "Claude arbeitet", chat: "Frag Claude", collection: "Meine Sammlung", orders: "Sammelbestellungen", deskmat: "Deskmat-Studio", glossary: "Glossar", opponents: "Gegnerdecks", tables: "Tischregeln", blacklist: "Blacklist", settings: "Einstellungen" }[r.view]) + " · Commander Deckbuilder";
}
window.addEventListener("hashchange", route);

function markNav(r = parseHash()) {
  for (const a of $$("#deck-list a")) {
    if (r.view === "deck" && a.dataset.slug === r.slug) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  for (const a of $$(".nav-bottom a, .nav-top a")) {
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
  for (const el of buildForm.querySelectorAll(":is(.mode-build, .mode-find, .mode-meta) :is(input, textarea, select)")) {
    el.disabled = !el.closest(".mode-build, .mode-find, .mode-meta").classList.contains(`mode-${mode}`);
  }
  buildForm.dataset.metaAction = buildForm.elements.meta_action.value || "suggest";
  $("#meta-suggestions").hidden = mode !== "meta" || !metaResult;
  if (mode === "meta") renderMetaBox();
  if (mode === "import" && $("#import-box").dataset.imode === "precon" && !preconItems) searchPrecons();
}
buildForm.addEventListener("change", (e) => {
  if (e.target.name === "mode") setMode(e.target.value);
  if (e.target.name === "meta_action") buildForm.dataset.metaAction = e.target.value;
});

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
  if (buildForm.dataset.mode === "import") {  // Enter in the link or search field
    const im = $("#import-box").dataset.imode;
    if (im === "link") loadImport({ url: $("#import-url").value });
    else if (im === "precon") searchPrecons();
    return;
  }
  if (currentJob) { toast("Es läuft schon ein Auftrag – warte kurz oder brich ihn ab.", "error"); return; }
  const f = Object.fromEntries(new FormData(buildForm));
  try {
    if (buildForm.dataset.mode === "meta") {
      const fd = new FormData(buildForm);
      const ids = fd.getAll("meta_opps");
      if (!oppIndex.length) { toast("Noch keine Gegnerdecks – lege sie unter „Gegnerdecks“ an.", "error"); return; }
      if (!ids.length) { toast("Wähle mindestens ein Gegnerdeck aus.", "error"); return; }
      const body = {
        ...buildSettings(), commander: f.meta_commander || null, opponent_ids: ids.length === oppIndex.length ? [] : ids,
        strategy: f.strategy || null, notes: f.notes || null, prefer_collection: !!f.prefer_collection,
        profile: readProfile($("#build-profile .profile-fields")), table_rule: f.table_rule || null,
      };
      if (f.meta_action === "suggest") {
        const { job } = await api("/api/build-meta/suggest", { method: "POST", body: { ...body, commander: null,
          count: Number(f.meta_count), research: !!f.meta_research } });
        startJob(job, "Claude analysiert deine Runde (Opus 5.5 · extra hoch)", { kind: "meta", slot: "#finder-job-slot", route: "#/new" });
        $("#finder-job-slot").scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
      if (metaPick && body.commander === metaPick.name) body.partner = metaPick.partner || null;
      const { job } = await api("/api/build-meta", { method: "POST", body });
      startJob(job, body.commander ? `Claude baut ${body.commander} gegen deine Runde` : "Claude baut das stärkste Deck gegen deine Runde",
        { kind: "build", slot: "#job-slot-main", route: "#/job" });
      go("#/job");
    } else if (buildForm.dataset.mode === "find") {
      const fd = new FormData(buildForm);
      const body = { ...buildSettings(), prompt: f.prompt || "", count: Number(f.count), feel: fd.getAll("feel"),
        colors: fd.getAll("colors"), themes: fd.getAll("themes"), experience: f.experience || null };
      if (!body.prompt.trim() && !body.feel.length && !body.colors.length && !body.themes.length) {
        toast("Wähl mindestens aus, was dir Spaß macht – oder beschreibe deinen Wunsch.", "error");
        buildForm.querySelector(".quiz input").focus();
        return;
      }
      const { job } = await api("/api/find-commander", { method: "POST", body });
      $("#suggestions").hidden = true;
      startJob(job, "Claude sucht passende Commander", { kind: "finder", slot: "#finder-job-slot", route: "#/new" });
      $("#finder-job-slot").scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      const body = {
        ...buildSettings(), commander: f.commander, partner: f.partner || null,
        strategy: f.strategy || null, notes: f.notes || null, prefer_collection: !!f.prefer_collection,
        profile: readProfile($("#build-profile .profile-fields")), table_rule: f.table_rule || null,
      };
      const { job } = await api("/api/build", { method: "POST", body });
      startJob(job, `Claude baut ${body.commander}${body.partner ? " + " + body.partner : ""}`, { kind: "build", slot: "#job-slot-main", route: "#/job" });
      go("#/job");
    }
  } catch (err) { fail(err); }
});

// ---------- import an existing deck: link, pasted list or starter deck ----------
let importData = null;
function setImportMode(mode) {
  $("#import-box").dataset.imode = mode;
  $(`#import-box [name=imode][value="${mode}"]`).checked = true;
  $("#import-msg").textContent = "";
  if (mode === "precon" && !preconItems) searchPrecons();
}
$("#import-box").addEventListener("change", (e) => { if (e.target.name === "imode") setImportMode(e.target.value); });
$("#import-url-btn").addEventListener("click", () => loadImport({ url: $("#import-url").value }));
wireAutocomplete($("#manual-cmd"), $("#ac-manual"));
wireAutocomplete($("#manual-partner"), $("#ac-manual-p"));
$("#manual-btn").addEventListener("click", async () => {
  const commander = $("#manual-cmd").value.trim();
  if (commander.length < 2) { toast("Gib zuerst den Commander ein.", "error"); $("#manual-cmd").focus(); return; }
  const b = $("#manual-btn");
  b.disabled = true;
  b.textContent = "Lege an …";
  try {
    const r = await api("/api/decks/new", { method: "POST", body: { commander, partner: $("#manual-partner").value.trim() || null,
      name: $("#manual-name").value.trim(), bracket: Number($("#manual-bracket").value), currency: buildForm.elements.currency.value || "eur" } });
    await refreshDeckList();
    for (const id of ["#manual-cmd", "#manual-partner", "#manual-name"]) $(id).value = "";
    go(`#/deck/${enc(r.slug)}/karten`);
    for (let k = 0; k < 50 && currentDeck?.slug !== r.slug; k++) await new Promise((res) => setTimeout(res, 100));  // deck view loads
    if (currentDeck?.slug === r.slug && !edit) setEditing(true);
    toast("Deck angelegt – füge jetzt Karten hinzu.", "info", 6000);
  } catch (err) { fail(err); }
  finally { b.disabled = false; b.textContent = "Leeres Deck anlegen"; }
});
$("#import-text-btn").addEventListener("click", () => loadImport({ text: $("#import-text").value }));

async function loadImport(body) {
  if (body.url !== undefined && !body.url.trim()) { toast("Füge zuerst einen Link ein.", "error"); $("#import-url").focus(); return; }
  const msg = $("#import-msg");
  msg.className = "small muted";
  msg.textContent = body.url ? "Lade das Deck …" : "Prüfe die Liste …";
  $("#import-preview").hidden = true;
  try {
    importData = await api("/api/import/preview", { method: "POST", body });
    msg.textContent = "";
    renderImportPreview(importData);
  } catch (err) {
    msg.className = "small bad";
    msg.textContent = err.message;
  }
}

function renderImportPreview(d) {
  const box = $("#import-preview");
  const options = (sel) => d.commander_candidates.map((c) => `<option${c.name === sel ? " selected" : ""}>${esc(c.name)}</option>`).join("");
  const known = d.commanders.length > 0;
  const img = (known ? d.commander_images?.[d.commanders[0]] : null)
    || d.commander_candidates.find((c) => c.name === d.suggested[0])?.image;
  const count = d.card_count === 100 ? "100 Karten" : `<span class="warn">${d.card_count} Karten (Commander-Decks haben 100)</span>`;
  let cmd;
  if (known) cmd = `<p>Commander: <b>${d.commanders.map(esc).join(" + ")}</b></p>`;
  else if (d.commander_candidates.length) {
    cmd = `<div class="cmd-row"><label class="field"><span>Commander</span><select id="imp-cmd1">${d.suggested.length ? "" : "<option value=''>– bitte wählen –</option>"}${options(d.suggested[0])}</select></label>
      <label class="field"><span>Partner <span class="muted">(optional)</span></span><select id="imp-cmd2"><option value="">–</option>${options(d.suggested[1])}</select></label></div>`;
  } else {
    cmd = `<label class="field"><span>Commander <span class="muted">(die Seite markiert ihn nicht)</span></span>
      <input id="imp-cmd-text" list="ac-imp-cmd" placeholder="Name des Commanders"><datalist id="ac-imp-cmd"></datalist></label>`;
  }
  box.innerHTML = `${img ? `<img src="${esc(img)}" alt="">` : ""}
    <div class="grow">
      <label class="field"><span>Name</span><input id="imp-name" value="${esc(d.name || (d.commanders[0] || d.suggested[0] || "Importiertes Deck"))}" maxlength="120"></label>
      <p class="muted small">${esc(d.site)}${d.source ? ` · <a href="${esc(d.source)}" target="_blank" rel="noopener">Quelle</a>` : ""} · ${count}
        ${d.unresolved?.length ? `<br><span class="warn">Nicht erkannt: ${d.unresolved.slice(0, 8).map(esc).join(", ")}${d.unresolved.length > 8 ? " …" : ""}</span>` : ""}</p>
      ${cmd}
      <label class="field"><span>Bracket</span><select id="imp-bracket"><option value="">automatisch schätzen</option>
        ${[1, 2, 3, 4, 5].map((n) => `<option value="${n}">${n} – ${esc(brackets.find((b) => b.number === n)?.name || "")}</option>`).join("")}</select></label>
      <div><button type="button" class="btn primary" id="imp-save">Importieren &amp; prüfen</button></div>
    </div>`;
  box.hidden = false;
  if ($("#imp-cmd-text")) wireAutocomplete($("#imp-cmd-text"), $("#ac-imp-cmd"));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("#import-preview").addEventListener("click", async (e) => {
  const b = e.target.closest("#imp-save");
  if (!b || !importData) return;
  const d = importData;
  const commanders = d.commanders.length ? d.commanders
    : $("#imp-cmd-text") ? [$("#imp-cmd-text").value.trim()].filter(Boolean)
    : [$("#imp-cmd1").value, $("#imp-cmd2").value].filter(Boolean);
  if (!commanders.length) { toast("Wähle den Commander des Decks.", "error"); return; }
  const bracket = $("#imp-bracket").value;
  const body = { name: $("#imp-name").value.trim() || "Importiertes Deck", commanders, cards: d.cards, categories: d.categories,
    bracket: bracket ? Number(bracket) : null, currency: buildForm.elements.currency.value || "eur", site: d.site, source: d.source || "" };
  b.disabled = true;
  b.textContent = "Importiere und prüfe …";
  try {
    const r = await api("/api/import", { method: "POST", body });
    await refreshDeckList();
    go(`#/deck/${enc(r.slug)}/karten`);
    toast(`Deck importiert (Bracket ${r.bracket}${bracket ? "" : ", geschätzt"}).${r.legal ? "" : " Die Prüfung hat Probleme gefunden – siehe rechts."}`, r.legal ? "info" : "error", 7000);
    importData = null;
    $("#import-preview").hidden = true;
    $("#import-url").value = "";
    $("#import-text").value = "";
  } catch (err) { b.disabled = false; b.textContent = "Importieren & prüfen"; fail(err); }
});

// ---------- starter deck (precon) import ----------
let preconItems = null;
async function searchPrecons() {
  const q = $("#precon-q").value.trim();
  $("#precon-msg").textContent = "Suche …";
  try {
    const items = await api(`/api/precons?q=${enc(q)}`);
    if ($("#precon-q").value.trim() !== q) return;
    preconItems = items;
    $("#precon-msg").textContent = items.length ? (q ? `${items.length} Treffer` : "Die neuesten Commander-Decks – oder oben suchen.") : "Kein Starterdeck gefunden.";
    $("#precon-list").innerHTML = items.map((p, i) => `<li><button type="button" data-i="${i}">
      <span>${esc(p.name)}</span><span class="muted small">${esc(p.code)} · ${esc(p.released.slice(0, 4))}</span></button></li>`).join("");
  } catch (err) { $("#precon-msg").textContent = err.message; $("#precon-list").innerHTML = ""; }
}
$("#precon-q").addEventListener("input", debounce(searchPrecons, 300));
$("#precon-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-i]");
  if (!b) return;
  const item = preconItems[Number(b.dataset.i)];
  const box = $("#precon-pick");
  box.hidden = false;
  box.innerHTML = `<p class="muted small">Lade ${esc(item.name)} …</p>`;
  try {
    const p = await api(`/api/precons/${enc(item.file)}`);
    const img = p.commander_images?.[p.commanders[0]];
    box.innerHTML = `${img ? `<img src="${esc(img)}" alt="${esc(p.commanders[0])}">` : ""}
      <div><h3>${esc(p.name)}</h3>
        <p class="muted small">${esc(p.code)} · ${esc(p.released)} · ${p.card_count} Karten</p>
        <p>Commander: <b>${p.commanders.map(esc).join(" + ")}</b></p>
        <button type="button" class="btn primary" id="precon-import" data-file="${esc(p.file)}">Importieren &amp; prüfen</button></div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { box.innerHTML = `<p class="bad small">${esc(err.message)}</p>`; }
});
$("#precon-pick").addEventListener("click", async (e) => {
  const b = e.target.closest("#precon-import");
  if (!b) return;
  b.disabled = true;
  b.textContent = "Importiere und prüfe …";
  try {
    const r = await api("/api/precons/import", { method: "POST", body: { file: b.dataset.file, currency: buildForm.elements.currency.value || "eur" } });
    await refreshDeckList();
    go(`#/deck/${enc(r.slug)}/karten`);
    toast("Starterdeck importiert. Tipp: Unter „Anpassen“ erstellt Claude dir einen Upgrade-Plan in Stufen.", "info", 8000);
  } catch (err) { b.disabled = false; b.textContent = "Importieren & prüfen"; fail(err); }
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
      ${s.difficulty ? `<span class="difficulty ${esc(s.difficulty)}" title="${esc(s.difficulty_note || "")}">${esc(s.difficulty[0].toUpperCase() + s.difficulty.slice(1))} zu spielen</span>` : ""}
      <p><span class="why-label">Warum passt der zu dir?</span> ${esc(s.why || "")}</p>
      ${s.difficulty_note ? `<p class="muted small">${esc(s.difficulty_note)}</p>` : ""}
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
  get_blacklist: "liest deine Blacklist", table_rules: "liest deine Tischregeln", opponent_decks: "liest deine Gegnerdecks", update_opponent_deck: "notiert ein Gegnerdeck", update_table_rule: "ändert eine Tischregel", deck_games: "liest deine Partien", import_deck: "importiert ein Deck", export_deck: "exportiert das Deck",
  list_deck_versions: "liest den Verlauf", compare_deck_versions: "vergleicht Versionen",
  app_overview: "verschafft sich einen Überblick über deine Decks", list_decks: "liest deine Decks",
  collection_status: "prüft deine Sammlung", collection_search: "durchsucht deine Sammlung", print_orders: "liest deine Sammelbestellungen",
};
const toolText = (name) => `Claude ${TOOL_LABELS[name] || `nutzt ${name}`} …`;

function startJob(jobId, title, { kind = "build", slot = "#job-slot-main", route: home = "#/job", slug = null, since = null } = {}) {
  currentJob = jobId;
  jobInfo = { id: jobId, kind, slot, route: home, slug, since, title, started: Date.now(), error: null, dismissed: false };
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
  eventSource = jobStream(jobId, handleEvent, (lost) => {
    if (currentJob !== jobId) return;
    if (lost) { handleEvent({ type: "error", text: lost }); handleEvent({ type: "done", ok: false }); }
    else if (lost === null) setJobStatus("Verbindung unterbrochen – verbinde neu …");
  });
}

// the job panel lives in the slot of the view that started it (build page, deck tab, finder)
function placeJobPanel() {
  const panel = $("#job");
  const visible = jobInfo && !jobInfo.dismissed && (!jobInfo.slug || (currentDeck && currentDeck.slug === jobInfo.slug)
    || (parseHash().view === "orders" && currentOrder?.slug === jobInfo.slug));
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
  for (const sel of ["#build-btn", "#refine-form [type=submit]", "#retune-btn", "#prepare-btn", "#mpc-btn", "#upgrade-btn", "#guide-btn", "#plan-btn", "#dm-gen-btn", "#dm-render-btn"]) {
    const b = $(sel);
    const noAi = AI_ONLY.includes(sel) && !aiState.available;
    b.disabled = busy || noAi;
    b.title = busy ? "Es läuft gerade ein Auftrag" : noAi ? "Braucht Claude (KI) – gerade nicht verfügbar" : "";
  }
}

// ---------- without Claude: AI features are switched off with a note, everything else works ----------
let aiState = { enabled: true, available: true, reason: null };
const AI_ONLY = ["#build-btn", "#refine-form [type=submit]", "#retune-btn", "#upgrade-btn", "#guide-btn", "#plan-btn"];
const AI_OFF_TEXT = "Ohne KI geht weiter: Deck importieren oder selbst zusammenstellen, bearbeiten, drucken, Sammlung, Partien, Gegnerdecks.";
async function refreshAi(first = false) {
  aiState = await api("/api/ai").catch(() => aiState);
  applyAi();
  // without AI a new deck starts with what works: put it together yourself
  if (first && !aiState.available && buildForm.dataset.mode === "build") { setMode("import"); setImportMode("manual"); }
}
function applyAi() {
  const off = !aiState.available;
  document.body.classList.toggle("no-ai", off);
  for (const el of $$("[data-ai-note]")) {
    el.hidden = !off;
    el.textContent = el.dataset.aiNote === "deskmat"
      ? "Ohne KI geht deine Beschreibung direkt als Bild-Prompt an den Generator – beschreib das Motiv also möglichst genau (gern auf Englisch)."
      : `${aiState.reason || "Claude ist gerade nicht verfügbar."} ${AI_OFF_TEXT}`;
  }
  setBusy(!!currentJob);
  updateQaLive();
  updateChatLive();
  $("#ai-enabled").checked = !!aiState.enabled;
  $("#ai-status").innerHTML = aiState.available
    ? '<span class="ok">✓ Claude ist bereit.</span>' + (aiState.last_error ? ` <span class="warn">Der letzte Lauf scheiterte: ${esc(aiState.last_error)}</span>` : "")
    : `<span class="warn">${esc(aiState.reason || "nicht verfügbar")}</span>`;
}
$("#ai-enabled").addEventListener("change", async (e) => {
  try {
    await api("/api/settings", { method: "POST", body: { ai_enabled: e.target.checked } });
    await refreshAi();
    toast(e.target.checked ? "KI-Funktionen sind an." : "KI-Funktionen sind aus – alles andere funktioniert wie gewohnt.");
  } catch (err) { fail(err); }
});

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
    case "meta_suggestions": renderMetaSuggestions(ev.result); break;
    case "upgrades": renderUpgrades(ev, jobInfo?.slug); break;
    case "guide": onGuide(ev.guide, jobInfo?.slug); break;
    case "plan": onUpgradePlan(ev.plan, jobInfo?.slug); break;
    case "deskmat": onDeskmat(ev.project, ev.what); break;
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
      offerOrderAfterRebuild(info.slug, info.since);
      break;
    case "finder":
    case "meta":
    case "upgrade":
    case "guide":
    case "plan":
    case "deskmat":
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
  renderDeckTable(d);
  renderValidation(d.validation);
  renderHealth(d);
  renderStats(d.validation?.stats, d.validation);
  renderCards(d);
  $("#retune-form").querySelector(`[name="rbracket"][value="${d.bracket || 3}"]`).checked = true;
  setProfile($("#retune-form .profile-fields"), d.power_profile);
  $("#retune-form").elements.request.value = "";
  renderPower(d);
  $("#upgrade-budget-field").hidden = !!d.proxy;
  $("#upgrade-cur").textContent = `(${CURRENCY_SIGNS[d.currency || "eur"]})`;
  if (upgrades?.slug !== d.slug) $("#upgrade-result").hidden = true;
  renderHistory(d);
  renderQuestions(d);
  renderGuide(d);
  renderUpgradePlan(d);
  loadGames(d);
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
  else if (v.price_total != null) pills.push(`<span class="pill">${esc(fmtPrice(v.price_total, cur))}${d.budget ? ` / ${esc(fmtPrice(d.budget, cur))}` : ""}</span>`);
  if (d.power_profile?.style) pills.push(`<span class="pill">Stil: ${esc(d.power_profile.style)}</span>`);
  if (d.table_rule_info) {
    const t = v.table_rule || {};
    pills.push(`<a class="pill ${t.compliant === false ? "bad" : ""}" href="#/deck/${enc(d.slug)}/anpassen" title="Tischregel">${icon("table")}${esc(d.table_rule_info.name)}</a>`);
  }
  pills.push(`<span class="muted small">${d.version ? `v${d.version} · ` : ""}${esc(fmtDate(d.updated))}</span>`);
  $("#deck-meta").innerHTML = pills.join("");
  $("#deck-desc").textContent = d.description || "";
  $("#deck-desc").hidden = !d.description;
  const against = d.built_against_info || [];
  $("#deck-against").hidden = !against.length;
  $("#deck-against").innerHTML = against.length ? `${icon("swords")} Gebaut gegen ${against.map((o) => `<a href="#/opponents/${enc(o.id)}">${esc(o.title)}</a>`).join(", ")}` : "";
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
  if (tab === "drucken") mountPrintStudio("#panel-drucken");
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
        ${kv("Extra-Züge", br.extra_turns || [])}
        ${kv("Massen-Landzerstörung", br.mass_land_denial || [])}
        ${kv("Tutoren", br.tutors || [])}
      </dl>
    </details>`;
}

const ROLE_LABELS = { ramp: "Ramp", card_draw: "Kartenzug", removal: "Removal", board_wipe: "Board Wipes",
  tutor: "Tutoren", extra_turn: "Extra-Züge", counterspell: "Counter", protection: "Schutz" };

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
// stored category values stay English (skill contract, references/deck-template.md); the GUI shows German names
const CATEGORY_LABELS = { Draw: "Kartenzug", "Board Wipe": "Board Wipes", Protection: "Schutz", Synergy: "Synergie",
  "Win Condition": "Siegbedingung", Utility: "Sonstiges", Land: "Länder", Tutor: "Tutoren", Counterspell: "Counterspells" };
const catLabel = (c) => CATEGORY_LABELS[c] || c;
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
    default: return c.category ? catLabel(c.category) : TYPE_LABELS[typeOf(cd)] || "Sonstiges";
  }
}

function groupRank(g) {
  const order = cardGroup === "type" ? TYPE_ORDER.map((t) => TYPE_LABELS[t] || t)
    : cardGroup === "color" ? ["Commander", "Weiß", "Blau", "Schwarz", "Rot", "Grün", "Mehrfarbig", "Farblos", "Länder"]
    : cardGroup === "owned" ? ["Commander", ...OWNED_ORDER]
    : CATEGORY_ORDER.map(catLabel);
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
  const catOptions = (sel) => ["", ...CATEGORY_ORDER.slice(1)].map((c) => `<option value="${esc(c)}" ${c === sel ? "selected" : ""}>${esc(c ? catLabel(c) : "Kategorie …")}</option>`).join("");
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
      const price = `<span class="price">${priceOf(cd, d) ? esc(fmtNum(priceOf(cd, d), "", 2)) : ""}</span>`;
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
  const slug = edit.slug, since = currentDeck?.version;
  try {
    const r = await api(`/api/decks/${enc(slug)}/cards`, { method: "POST", body });
    setEditing(false);
    currentDeck = null;
    await refreshDeckList();
    await route();
    toast(`Gespeichert als v${r.version}.${r.legal ? "" : " Achtung: das Deck ist nicht legal – siehe Prüfung."}`, r.legal ? "info" : "error");
    offerOrderAfterRebuild(slug, since);
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
        <figcaption><b>Länder in der Starthand</b> · Ø ${fmtNum(7 * L / N, "", 1)} · 2–5 Länder: <b>${pct(keep)}</b></figcaption>
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
  $("#token-list").innerHTML = toks.map((t) => `<li class="card" data-img="${esc(t.image || "")}" data-name="${esc(t.name)}" data-type="${esc(t.type_line)}" data-id="${esc(t.id || "")}">
    ${t.image ? `<img src="${esc(t.image)}" alt="" loading="lazy">` : ""}
    <div><b>${esc(t.name)}</b><div class="muted small">${esc(t.type_line.replace(/^Token /, ""))} · von ${esc(t.from.slice(0, 3).join(", "))}${t.from.length > 3 ? ` +${t.from.length - 3}` : ""}</div></div></li>`).join("");
}
$("#token-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-name]");
  if (li) showCardView(li.dataset.name, { image: li.dataset.img, token: true, type_line: li.dataset.type, id: li.dataset.id });
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
// "Anpassen": one tool at a time (own words · upgrade suggestions · staged plan · change strength)
const TUNES = ["refine", "upgrades", "plan", "power"];
function showTune(key, scroll = false, remember = true) {
  if (!TUNES.includes(key)) key = "refine";
  for (const p of $$("#panel-anpassen [data-tune]")) p.hidden = p.dataset.tune !== key;
  const radio = $(`#panel-anpassen input[name=tune][value="${key}"]`);
  if (radio) radio.checked = true;
  if (remember) store.set("tune", key);
  if (scroll) $(`#panel-anpassen [data-tune="${key}"]`).scrollIntoView({ behavior: "smooth", block: "start" });
}
$("#panel-anpassen .tune-options").addEventListener("change", (e) => { if (e.target.name === "tune") showTune(e.target.value); });
showTune(store.get("tune") || "refine");

$("#health-list").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.role) return openRoleCandidates(b.dataset.role, b.dataset.label);
  if (b.dataset.focus) {
    selectTab("anpassen");
    showTune("upgrades");
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

let cardViewCard = null;  // { name, cd } of the open card view
function showCardView(name, cd) {
  cardViewCard = { name, cd: cd || {} };
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
$("#card-view-order").addEventListener("click", () => {
  if (!cardViewCard) return;
  const { name, cd } = cardViewCard;
  const deck = parseHash().view === "deck" && currentDeck ? currentDeck : null;
  const token = !!cd.token;
  const item = { kind: token ? "token" : "card", name, qty: 1, source: deck ? deck.name : (token ? "Tokens" : "Einzelkarten"),
    ...(deck ? { source_slug: deck.slug } : {}),
    ...(token ? { type_line: cd.type_line || "Token", token_id: /^[0-9a-f-]{36}$/.test(cd.id || "") ? cd.id : null, image: cd.image || null } : {}) };
  orderDialog({ title: token ? "Token drucken" : "Karte drucken", text: `„${name}“ in eine Sammelbestellung packen.`,
    body: { items: [item] }, qtyField: true });
});

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
  const ok = await ask({ title: `„${currentDeck.name}“ löschen?`, ok: "In den Papierkorb", danger: true,
    text: "Das Deck kommt mit allen Versionen, Fragen und Partien in den Papierkorb (Einstellungen → Papierkorb). Bis du ihn leerst, kannst du es zurückholen." });
  if (!ok) return;
  const name = currentDeck.name;
  let r;
  try { r = await api(`/api/decks/${enc(currentDeck.slug)}`, { method: "DELETE" }); } catch (err) { fail(err); return; }
  currentDeck = null;
  await refreshDeckList();
  go("#/new");
  toast(`„${name}“ liegt im Papierkorb.`, "info", 8000, { label: "Rückgängig", run: () => restoreFromTrash(r.trash_id) });
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
    startJob(job, `Claude überarbeitet ${name}`, { kind: "deck", slug, since: currentDeck?.version, slot: "#tune-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
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
  const body = { budget: f.get("budget") ? Number(f.get("budget")) : null, focus: f.get("focus") || null, count: Number(f.get("count")),
    opponent_id: f.get("opponent_id") || null };
  const { slug, name } = currentDeck;
  try {
    const { job } = await api(`/api/decks/${enc(slug)}/upgrades`, { method: "POST", body });
    upgrades = { slug, items: [], budget: body.budget };
    $("#upgrade-result").hidden = true;
    startJob(job, `Claude sucht Upgrades für ${name}`, { kind: "upgrade", slug, slot: "#tune-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
    $("#tune-job-slot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { fail(err); }
});

// one swap (− out → + in) with images, reason and price; ``i`` adds the selection checkbox
function upgradeRow(u, currency, i = null) {
  return `<li>
    ${i !== null ? `<label class="up-check"><input type="checkbox" data-i="${i}" checked aria-label="${esc(u.add)} statt ${esc(u.remove)} übernehmen"></label>` : ""}
    <span class="up-imgs">
      ${u.image_remove ? `<img class="card out" data-img="${esc(u.image_remove)}" data-name="${esc(u.remove)}" src="${esc(u.image_remove)}" alt="">` : ""}
      ${u.image ? `<img class="card in" data-img="${esc(u.image)}" data-name="${esc(u.add)}" src="${esc(u.image)}" alt="">` : ""}
    </span>
    <div class="up-text">
      <div><span class="minus">− ${esc(u.remove)}</span> <span aria-hidden="true">→</span> <b class="plus">+ ${esc(u.add)}</b>
        ${u.impact ? `<span class="tagb">${esc(u.impact)}</span>` : ""}${u.owned ? '<span class="own ok" title="Schon in deiner Sammlung">✓ Sammlung</span>' : ""}</div>
      <div class="muted small">${esc(u.reason)}</div>
    </div>
    <span class="up-price">${u.owned ? "0 (hast du)" : u.price != null ? esc(fmtPrice(u.price, currency)) : "–"}</span>
  </li>`;
}

function renderUpgrades(ev, slug) {
  upgrades = { ...(upgrades || {}), slug, items: ev.items, currency: (ev.currency || "eur").toUpperCase() };
  if (currentDeck?.slug !== slug) return;
  $("#upgrade-result").hidden = false;
  $("#upgrade-summary").textContent = ev.summary || "";
  $("#upgrade-list").innerHTML = ev.items.map((u, i) => upgradeRow(u, upgrades.currency, i)).join("");
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
    const since = currentDeck.version;
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/cards`, { method: "POST", body });
    const slug = currentDeck.slug;
    upgrades = null;
    $("#upgrade-result").hidden = true;
    currentDeck = null;
    await refreshDeckList();
    go(`#/deck/${enc(slug)}/verlauf`);
    toast(`${sel.length} Upgrades übernommen (v${r.version}).${r.legal ? "" : " Achtung: Deck ist nicht legal – siehe Prüfung."}`, r.legal ? "info" : "error");
    offerOrderAfterRebuild(slug, since);
  } catch (err) { fail(err); }
});

// staged upgrade plan: one AI run, every stage applied on its own (stored in the deck)
function planStageApplied(stage, deck) {
  const names = new Set(deck.cards.map((c) => c.name));
  return stage.upgrades.every((u) => names.has(u.add) && !names.has(u.remove));
}
let tuneDeck = null;  // deck the „Anpassen“ choice was last set up for
function renderUpgradePlan(d) {
  const plan = d.upgrade_plan;
  const cur = (d.currency || "eur").toUpperCase();
  $$(".plan-cur").forEach((el) => { el.textContent = CURRENCY_SIGNS[d.currency || "eur"]; });
  $("#plan-budgets").hidden = !!d.proxy;
  $("#plan-btn span").textContent = plan ? "Neuen Plan erstellen" : "Plan erstellen";
  $("#tune-plan-badge").hidden = !plan;
  $("#plan-result").hidden = !plan;
  if (tuneDeck !== d.slug) {  // a deck with an open upgrade plan opens „Anpassen“ on it
    tuneDeck = d.slug;
    const open = plan?.stages?.some((st) => !planStageApplied(st, d));
    showTune(open ? "plan" : store.get("tune") || "refine", false, false);
  }
  if (!plan) return;
  $("#plan-meta").textContent = `Erstellt ${fmtDate(plan.created)} für v${plan.version}`;
  $("#plan-summary").textContent = plan.summary || "";
  let blocked = false;  // later stages build on earlier ones
  $("#plan-stages").innerHTML = plan.stages.map((st, i) => {
    const applied = planStageApplied(st, d);
    const canApply = !applied && !blocked;
    if (!applied) blocked = true;
    const budget = st.budget != null ? ` / ${fmtPrice(st.budget, cur)}` : "";
    return `<li class="${applied ? "applied" : ""}">
      <div class="plan-head"><h3>Stufe ${i + 1}: ${esc(st.title)}<span class="cost">${esc(fmtPrice(st.cost, cur))}${esc(budget)}</span></h3>
        ${applied ? '<span class="done">✓ übernommen</span>'
          : `<span class="btn-group"><button type="button" class="btn small ghost" data-stage-order="${i}" title="Die neuen Karten dieser Stufe drucken">Zur Sammelbestellung</button>
             <button type="button" class="btn small${canApply ? " primary" : ""}" data-stage="${i}"${canApply ? "" : ' disabled title="Erst die vorige Stufe übernehmen"'}>Stufe übernehmen</button></span>`}</div>
      ${st.goal ? `<p class="muted small">${esc(st.goal)}</p>` : ""}
      <ul class="upgrade-list">${st.upgrades.map((u) => upgradeRow(u, cur)).join("")}</ul></li>`;
  }).join("");
}
$("#plan-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck || currentJob) return;
  const f = new FormData(e.target);
  const { slug, name } = currentDeck;
  if (currentDeck.upgrade_plan && !(await ask({ title: "Neuen Plan erstellen?", text: "Der bisherige Plan wird ersetzt.", ok: "Neu erstellen" }))) return;
  const stages = currentDeck.proxy ? [1, 2, 3] : ["s1", "s2", "s3"].map((k) => Number(f.get(k))).filter((v) => v > 0);
  if (!stages.length) { toast("Gib mindestens für eine Stufe ein Budget an.", "error"); return; }
  try {
    const { job } = await api(`/api/decks/${enc(slug)}/upgrade-plan`, { method: "POST", body: { stages, focus: f.get("focus") || null } });
    startJob(job, `Claude plant Upgrades für ${name}`, { kind: "plan", slug, slot: "#plan-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
  } catch (err) { fail(err); }
});
function onUpgradePlan(plan, slug) {
  if (currentDeck?.slug !== slug) return;
  currentDeck.upgrade_plan = plan;
  renderUpgradePlan(currentDeck);
  toast("Der Upgrade-Plan ist fertig.");
  $("#plan-result").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("#plan-stages").addEventListener("click", async (e) => {
  const o = e.target.closest("button[data-stage-order]");
  if (o && currentDeck?.upgrade_plan) {
    const i = Number(o.dataset.stageOrder), st = currentDeck.upgrade_plan.stages[i];
    orderDialog({ title: `Stufe ${i + 1} drucken`, text: `${st.upgrades.length} neue Karten aus „${st.title}“ – ohne sie schon ins Deck zu übernehmen.`,
      body: { items: st.upgrades.map((u) => ({ kind: "card", name: u.add, qty: 1, source: `Upgrade-Plan ${currentDeck.name}`, source_slug: currentDeck.slug })) } });
    return;
  }
  const b = e.target.closest("button[data-stage]");
  if (!b || !currentDeck?.upgrade_plan) return;
  const i = Number(b.dataset.stage);
  const st = currentDeck.upgrade_plan.stages[i];
  const body = {
    add: st.upgrades.map((u) => ({ name: u.add, qty: 1 })), remove: st.upgrades.map((u) => u.remove),
    note: `Upgrade-Plan Stufe ${i + 1} (${st.title}): ${st.upgrades.map((u) => `${u.remove} → ${u.add}`).join(", ")}`,
  };
  b.disabled = true;
  try {
    const since = currentDeck.version;
    const r = await api(`/api/decks/${enc(currentDeck.slug)}/cards`, { method: "POST", body });
    const slug = currentDeck.slug;
    currentDeck = null;
    await refreshDeckList();
    go(`#/deck/${enc(slug)}/anpassen`);
    offerOrderAfterRebuild(slug, since);
    toast(`Stufe ${i + 1} übernommen (v${r.version}).${r.legal ? "" : " Achtung: Deck ist nicht legal – siehe Prüfung."}`, r.legal ? "info" : "error");
  } catch (err) { b.disabled = false; fail(err); }
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
    startJob(job, `Claude stimmt ${name} ab: ${levelText(bracket, profile.tier)}`, { kind: "deck", slug, since: currentDeck?.version, slot: "#tune-job-slot", route: `#/deck/${enc(slug)}/anpassen` });
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
// tab "Partien": game log, record and "learn from your games"
// ============================================================================================
let gameData = null;  // { slug, games, stats, learn_focus, issue_labels, result_labels }

async function loadGames(d) {
  renderKnownOpps();
  $("#game-deck-cards").innerHTML = [...d.commanders, ...d.cards.map((c) => c.name)].map((n) => `<option value="${esc(n)}">`).join("");
  try {
    const data = await api(`/api/decks/${enc(d.slug)}/games`);
    if (currentDeck?.slug !== d.slug) return;
    renderGames(d.slug, data);
  } catch { /* the tab shows the empty state */ }
}

function renderGames(slug, data) {
  const first = !gameData;
  gameData = { slug, ...data };
  if (first || !$("#game-issues").children.length) {
    $("#game-issues").innerHTML = Object.entries(data.issue_labels).map(([k, label]) =>
      `<label><input type="checkbox" name="issues" value="${esc(k)}"><span>${esc(label)}</span></label>`).join("");
  }
  const st = data.stats;
  $("#game-count").textContent = st.games || "";
  $("#game-learn").hidden = !data.learn_focus;
  const pct = st.win_rate === null ? "–" : `${Math.round(st.win_rate * 100)} %`;
  const maxIssue = Math.max(1, ...st.issues.map((i) => i.count));
  $("#game-stats").innerHTML = !st.games ? '<p class="empty-inline">Die Bilanz erscheint nach der ersten Partie.</p>' : `
    <div class="kpis"><div><b>${st.games}</b><span>Partien</span></div><div><b>${st.wins}–${st.losses}${st.draws ? "–" + st.draws : ""}</b><span>Siege–Niederlagen</span></div>
      <div><b>${pct}</b><span>Siegquote</span></div><div><b>${st.avg_turn ?? "–"}</b><span>Ø Zug am Ende</span></div></div>
    ${st.issues.length ? `<h3 class="small muted">Häufigste Probleme</h3><ul class="issue-bars">${st.issues.slice(0, 6).map((i) =>
      `<li><span>${esc(i.label)}</span><span class="bar"><i style="width:${(i.count / maxIssue) * 100}%"></i></span><span>${i.count}×</span></li>`).join("")}</ul>` : ""}
    ${st.per_version.length > 1 ? `<p class="game-stats-more">Pro Version: ${st.per_version.map((v) => `v${v.version} ${v.wins}/${v.games}`).join(" · ")}</p>` : ""}
    ${st.mvps.length ? `<p class="game-stats-more">Beste Karten: ${st.mvps.map((m) => `${esc(m.name)}${m.count > 1 ? ` (${m.count}×)` : ""}`).join(", ")}</p>` : ""}
    ${st.opponents.length ? `<p class="game-stats-more">Häufigste Gegner: ${st.opponents.map((o) => esc(o.name)).join(", ")}</p>` : ""}`;
  $("#game-empty").hidden = !!data.games.length;
  $("#game-list").innerHTML = data.games.slice().reverse().map((g) => `<li data-id="${esc(g.id)}">
      <div class="when"><span class="res ${esc(g.result)}">${esc(data.result_labels[g.result] || g.result)}</span><br><span class="meta">${esc(fmtDate(g.played))}${g.version ? ` · v${esc(g.version)}` : ""}</span></div>
      <div>${g.opponents.length ? `gegen ${g.opponents.map((o, i) => g.opponent_ids?.[i] ? `<a href="#/opponents/${enc(g.opponent_ids[i])}">${esc(o)}</a>` : esc(o)).join(", ")}` : '<span class="meta">Gegner nicht notiert</span>'}${g.turn ? ` · Zug ${esc(g.turn)}` : ""}
        ${g.mvp ? `<br>Beste Karte: <strong>${esc(g.mvp)}</strong>` : ""}${g.note ? `<br><span class="meta">${esc(g.note)}</span>` : ""}
        ${g.issues.length ? `<div class="issues">${g.issues.map((i) => `<span>${esc(data.issue_labels[i] || i)}</span>`).join("")}</div>` : ""}</div>
      <button type="button" class="icon-btn game-del" aria-label="Partie löschen" title="Partie löschen">${icon("x")}</button></li>`).join("");
}

for (const n of [1, 2, 3]) wireAutocomplete($(`#game-form [name=opp${n}]`), $(`#ac-opp${n}`));
$("#game-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck) return;
  const f = new FormData(e.target);
  const slots = [1, 2, 3].map((n) => ({ name: (f.get(`opp${n}`) || "").trim(), note: (f.get(`oppnote${n}`) || "").trim(),
    id: e.target.elements[`opp${n}`].dataset.oid || null })).filter((s) => s.name);
  const body = {
    result: f.get("result"), turn: f.get("turn") ? Number(f.get("turn")) : null, mvp: f.get("mvp") || null,
    opponents: slots.map((s) => s.name), opponent_ids: slots.map((s) => s.id), opponent_notes: slots.map((s) => s.note),
    remember_opponents: !!f.get("remember_opponents"), issues: f.getAll("issues"), note: f.get("note") || "",
  };
  const slug = currentDeck.slug;
  try {
    const data = await api(`/api/decks/${enc(slug)}/games`, { method: "POST", body });
    e.target.reset();
    for (const n of [1, 2, 3]) delete e.target.elements[`opp${n}`].dataset.oid;
    if (currentDeck?.slug === slug) renderGames(slug, data);
    if (body.opponents.length) refreshOpponents().then(renderKnownOpps);
    toast(body.result === "win" ? "Sieg gespeichert – Glückwunsch!" : "Partie gespeichert.");
  } catch (err) { fail(err); }
});
$("#game-list").addEventListener("click", async (e) => {
  const b = e.target.closest(".game-del");
  if (!b || !currentDeck) return;
  if (!(await ask({ title: "Partie löschen?", ok: "Löschen", danger: true }))) return;
  const slug = currentDeck.slug;
  try { renderGames(slug, await api(`/api/decks/${enc(slug)}/games/${enc(b.closest("li").dataset.id)}`, { method: "DELETE" })); }
  catch (err) { fail(err); }
});
$("#game-learn").addEventListener("click", () => {
  if (!gameData?.learn_focus) return;
  selectTab("anpassen");
  showTune("upgrades");
  const f = $("#upgrade-form");
  f.elements.focus.value = gameData.learn_focus;
  f.scrollIntoView({ behavior: "smooth", block: "center" });
  $("#upgrade-btn").focus({ preventScroll: true });
  toast("Fokus aus deinen Partien eingetragen – „Vorschläge holen“ startet die Suche.");
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
function md(text, refs = {}, decks = {}) {
  const names = [], slugs = [];
  const raw = String(text || "").replace(/\[\[([^\[\]]+)\]\]/g, (_, n) => `\u0001${names.push(n.trim()) - 1}\u0001`)
    .replace(/\{\{([a-z0-9][a-z0-9-]*)\}\}/g, (_, sl) => `\u0002${slugs.push(sl) - 1}\u0002`);
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
  return html.replace(/\u0001(\d+)\u0001/g, (_, i) => cardRef(names[Number(i)], refs))
    .replace(/\u0002(\d+)\u0002/g, (_, i) => deckRef(slugs[Number(i)], decks));
}

// {{slug}} in answers: a link to the saved deck (its name), plain text when the deck is gone
function deckRef(slug, decks = {}) {
  const d = decks[slug] || deckIndex.find((x) => x.slug === slug);
  return d ? `<a class="deck-ref" href="#/deck/${enc(slug)}">${esc(d.name)}</a>` : esc(slug);
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
  $("#qa-btn").disabled = !!(qaRun && !qaRun.finished) || !aiState.available;
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
    const run = { job, slug, question };
    run.source = jobStream(job, (ev) => onQaEvent(run, ev), (lost) => {
      if (lost && !run.finished) { onQaEvent(run, { type: "error", text: lost }); onQaEvent(run, { type: "done", ok: false }); }
    });
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
let currentOrder = null;  // the open collective order (page #/orders/<id>)
// what the print studio works on: the open deck or a collective order (same print routes, slug sammel-<id>)
function pctx() {
  if (parseHash().view === "orders" && currentOrder) {
    return { slug: currentOrder.slug, name: currentOrder.name, route: `#/orders/${enc(currentOrder.id)}`, order: true };
  }
  return currentDeck ? { slug: currentDeck.slug, name: currentDeck.name, route: `#/deck/${enc(currentDeck.slug)}/drucken`, order: false } : null;
}
// the print studio is one block that moves into the deck tab or the order page
function mountPrintStudio(slot) {
  const studio = $("#print-studio");
  if (studio.parentElement !== $(slot)) $(slot).appendChild(studio);
  $("#print-form .token-opt").hidden = !!pctx()?.order;  // orders list their tokens explicitly
}
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
  $("#print-tokens").hidden = true;  // the old list may belong to another deck/order
  $("#print-token-list").innerHTML = "";
  const slug = pctx().slug;
  try {
    [printPlan, prepared] = await Promise.all([
      api(`/api/decks/${enc(slug)}/print/plan?${planQuery()}`),
      api(`/api/decks/${enc(slug)}/print/prepared`),
    ]);
  } catch (err) { $("#print-summary").textContent = err.message; return; }
  if (pctx()?.slug !== slug) return;
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
  if (img.origin === "local") return '<span class="tag own">eigenes Bild</span>';
  if (img.custom) return '<span class="tag own">eigene Wahl</span>';
  return img.origin === "mpcfill" ? '<span class="tag mpc">MPC</span>' : '<span class="tag">Scryfall</span>';
}

function renderPlan() {
  renderPlanSummary();
  renderTokenList();
  $("#print-grid").innerHTML = printPlan.cards.map((c, i) => cardTile(c, i, "front")).join("");
}

// tokens: artwork + copies per token (stored per deck; collective orders keep theirs in the order items)
function tokenRow(c, i) {
  const img = c.front?.image;
  const from = (c.from || []).slice(0, 3).join(", ") + ((c.from || []).length > 3 ? " …" : "");
  const editable = !pctx()?.order;
  return `<li data-i="${i}" class="${c.qty === 0 ? "off" : ""}">
    <button type="button" class="tok-art" data-pick="${i}" title="Artwork für ${esc(c.name)} wählen" aria-label="Artwork für ${esc(c.name)} wählen">
      ${img ? `<img src="${esc(img.thumb)}" alt="" loading="lazy">` : '<span class="noimg">kein Bild</span>'}</button>
    <div class="tok-info"><b>${esc(c.name)}</b>
      <span class="muted small">${esc(tokenKind(c.type_line))}${from ? ` · von ${esc(from)}` : ""}</span></div>
    <div class="tok-controls">${originTag(img)}
    ${editable ? `<div class="stepper" role="group" aria-label="Anzahl ${esc(c.name)}">
        <button type="button" class="btn small" data-step="-1" aria-label="Eins weniger"${c.qty <= 0 ? " disabled" : ""}>−</button>
        <input type="number" min="0" max="99" value="${c.qty}" inputmode="numeric" aria-label="Anzahl ${esc(c.name)}">
        <button type="button" class="btn small" data-step="1" aria-label="Eins mehr"${c.qty >= 99 ? " disabled" : ""}>+</button></div>
      <button type="button" class="link-btn tok-reset" data-reset${c.qty_custom ? "" : " hidden"}>↺ Standard (${c.default_qty})</button>`
    : `<span class="muted small">${c.qty}× laut Bestellung</span>`}</div>
  </li>`;
}
// "Token Creature — Human" stays, emblems too; markers like The Monarch (type "Card") are called Marker
const tokenKind = (tl) => (/^(Token|Emblem)/.test(tl || "Token") ? tl || "Token" : "Marker");
function renderTokenList() {
  const toks = printPlan.cards.map((c, i) => [c, i]).filter(([c]) => c.token);
  $("#print-tokens").hidden = !toks.length;
  $("#print-token-list").innerHTML = toks.map(([c, i]) => tokenRow(c, i)).join("");
  updateTokenCount();
}
function updateTokenCount() {
  const toks = printPlan.cards.filter((c) => c.token);
  const n = toks.reduce((sum, c) => sum + c.qty, 0);
  $("#print-tokens-count").textContent = `· ${toks.length} verschiedene, ${n} ${n === 1 ? "Karte" : "Karten"}`;
}
const tokenSaveTimers = {};
function setTokenQty(li, qty, delay = 450) {
  const i = Number(li.dataset.i);
  const v = Math.max(0, Math.min(99, Math.round(Number(qty))));
  if (!Number.isFinite(v)) return;
  const input = li.querySelector("input");
  if (Number(input.value) !== v) input.value = v;
  printPlan.cards[i].qty = v;  // show it right away, save a moment later
  li.classList.toggle("off", v === 0);
  li.querySelector('[data-step="-1"]').disabled = v <= 0;
  li.querySelector('[data-step="1"]').disabled = v >= 99;
  updateTokenCount();
  clearTimeout(tokenSaveTimers[i]);
  tokenSaveTimers[i] = setTimeout(() => saveTokenQty(i, v), delay);
}
async function saveTokenQty(i, qty) {
  const slug = pctx().slug, face = printPlan.cards[i].front.face;
  try {
    await api(`/api/decks/${enc(slug)}/print/token-qty`, { method: "POST", body: { counts: { [face]: qty } } });
    const plan = await api(`/api/decks/${enc(slug)}/print/plan?${planQuery()}`);
    if (pctx()?.slug !== slug) return;
    printPlan = plan;
    renderPlanSummary();
    updateTokenCount();
    const j = plan.cards.findIndex((c) => c.token && c.front.face === face);
    const li = $(`#print-token-list li[data-i="${i}"]`);
    if (j < 0 || !li) { renderPlan(); return; }
    const c = plan.cards[j];
    const input = li.querySelector("input");
    if (document.activeElement !== input) input.value = c.qty;  // never overwrite what is being typed
    li.classList.toggle("off", c.qty === 0);
    const reset = li.querySelector("[data-reset]");
    reset.hidden = !c.qty_custom;
    reset.textContent = `↺ Standard (${c.default_qty})`;
    const tile = $(`#print-grid .pcard[data-i="${j}"]`);
    if (tile) tile.outerHTML = cardTile(c, j, "front");
    if (Object.keys(prepared.faces || {}).length && !$("#prepared-info .stale")) {
      $("#prepared-info").insertAdjacentHTML("beforeend", ' <span class="warn stale">· Token-Anzahl geändert – „Vorbereiten“ erneut ausführen.</span>');
    }
  } catch (err) { fail(err); }
}
$("#print-token-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-i]");
  if (!li) return;
  if (e.target.closest("[data-pick]")) { openPicker(Number(li.dataset.i), "front"); return; }
  const step = e.target.closest("[data-step]");
  if (step) { setTokenQty(li, Number(li.querySelector("input").value) + Number(step.dataset.step)); return; }
  if (e.target.closest("[data-reset]")) {
    clearTimeout(tokenSaveTimers[li.dataset.i]);
    const c = printPlan.cards[Number(li.dataset.i)];
    li.querySelector("input").value = c.default_qty;
    saveTokenQty(Number(li.dataset.i), null);
  }
});
$("#print-token-list").addEventListener("input", (e) => {
  const li = e.target.closest("li[data-i]");
  if (li && e.target.matches("input") && e.target.value !== "") setTokenQty(li, e.target.value, 700);
});
$("#print-token-list").addEventListener("change", (e) => {
  const li = e.target.closest("li[data-i]");
  if (li && e.target.matches("input")) setTokenQty(li, e.target.value === "" ? 0 : e.target.value, 0);
});
$("#print-form").elements.tokens.addEventListener("change", () => loadPlan());
$("#print-form").elements.token_copies.addEventListener("change", () => { if ($("#print-form").elements.tokens.checked) loadPlan(); });

function renderPlanSummary() {
  const p = printPlan;
  const imgs = p.cards.flatMap((c) => [c.front?.image, c.back?.image]).filter(Boolean);
  const mpc = imgs.filter((i) => i.origin === "mpcfill").length;
  $("#print-summary").innerHTML = `${p.quantity} Karten · MPC-Staffel ${p.mpc_bracket} · ${mpc} MPC-Autofill-Scans, ${imgs.length - mpc} Scryfall`
    + ` · ${p.cards.filter((c) => c.back).length} doppelseitig`
    + (p.server ? "" : ' · <span class="warn">kein MPC-Autofill-Server eingestellt (nur Scryfall)</span>')
    + (p.missing.length ? ` · <span class="bad">ohne Bild: ${esc(p.missing.join(", "))}</span>` : "")
    + (p.warnings.length ? `<br><span class="warn">${esc(p.warnings.join(" "))}</span>` : "");
  const tiles = imgs.length + p.missing.length;
  $("#print-grid-count").textContent = `· ${tiles} Bilder` + (p.missing.length ? ` · ${p.missing.length} ohne Bild` : "");
  if (p.missing.length) $("#print-grid-box").open = true;  // something needs attention: show the images
}

function cardTile(c, i, side) {
  const f = c[side];
  const img = f?.image;
  return `<button type="button" class="pcard${c.token && c.qty === 0 ? " off" : ""}" data-i="${i}" data-side="${side}" title="${esc(c.name)} – Bild wählen">
    ${img ? `<img src="${esc(img.thumb)}" alt="${esc(f.face)}" loading="lazy">` : `<div class="noimg">${esc(c.name)}<br>kein Bild</div>`}
    <div class="tags">${c.token && c.qty === 0 ? '<span class="tag off">nicht drucken</span>' : ""}${c.qty > 1 ? `<span class="tag">${c.qty}×</span>` : ""}${c.commander ? '<span class="tag">Commander</span>' : ""}${c.token ? '<span class="tag tok">Token</span>' : ""}${c.back ? '<span class="tag dfc" title="Doppelseitige Karte – ↻ dreht sie um">DFC</span>' : ""}${originTag(img)}${f && prepared.faces?.[f.face]?.upscaled ? '<span class="tag ai">KI</span>' : ""}</div>
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
    const r = await api(`/api/decks/${enc(pctx().slug)}/print/alternatives?card=${enc(ctx.card.name)}&side=${ctx.side}&token=${ctx.token}&page=${ctx.page + 1}`);
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
  const own = ctx.options.filter((o) => o.origin === "local").length;
  const scry = ctx.options.length - mpc - own;
  const shown = ctx.options.map((o, k) => [o, k]).filter(([o]) => !q || `${o.label || ""} ${o.released || ""} ${o.dpi || ""}`.toLowerCase().includes(q));
  $("#picker-hint").textContent = `${own ? `${own} eigene${own === 1 ? "s Bild" : " Bilder"} · ` : ""}${mpc ? `${mpc} MPC-Autofill-Scans (druckoptimiert) · ` : ""}${scry} von ${ctx.total} Scryfall-Drucken geladen`
    + (q ? ` · ${shown.length} passen zum Filter` : "") + (ctx.hasMore && q ? " – „Alle laden“ durchsucht alle Drucke" : "");
  $("#picker-grid").innerHTML = shown.map(([o, k]) => `<button type="button" class="pcard ${o.id === current ? "selected" : ""}" data-k="${k}">
    <img src="${esc(o.thumb)}" alt="" loading="lazy">
    ${o.upload ? `<span class="del" data-del="${esc(o.upload)}" title="Eigenes Bild löschen" aria-label="Eigenes Bild löschen">✕</span>` : ""}
    <div class="tags">${o.origin === "local" ? '<span class="tag own">eigenes Bild</span>' : o.origin === "mpcfill" ? '<span class="tag mpc">MPC</span>' : '<span class="tag">Scryfall</span>'}${o.dpi ? `<span class="tag">${esc(o.dpi)} DPI</span>` : ""}</div>
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
  const ctx = pickerCtx;
  await api(`/api/decks/${enc(pctx().slug)}/print/choose`, { method: "POST", body: { face: ctx.face, option } });
  $("#picker").close();
  await refreshAfterPick(ctx);
}

// own image: upload it for the picker's card face (it is chosen right away)
async function uploadPickerImage(file) {
  const ctx = pickerCtx;
  if (!ctx || !file) return;
  if (!/^image\//.test(file.type) && !/\.(jpe?g|png|webp|tiff?|bmp)$/i.test(file.name)) { toast("Das ist keine Bilddatei.", "error"); return; }
  const slug = pctx().slug;
  const box = $("#picker-upload");
  box.classList.add("busy");
  $("#picker-hint").textContent = `Lade „${file.name}“ hoch und mache es druckfertig …`;
  try {
    const q = new URLSearchParams({ face: ctx.face, filename: file.name, bleed: $("#picker-bleed").value });
    let res;
    try { res = await fetch(`/api/decks/${enc(slug)}/print/upload?${q}`, { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } }); }
    catch { throw new Error("Keine Verbindung zur App – läuft mtg-gui noch?"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorText(res, data));
    $("#picker").close();
    toast(`Eigenes Bild für ${ctx.face} gewählt. ${(data.notes || []).join(" ")}`, data.notes?.some((n) => n.startsWith("Niedrige")) ? "error" : "info", 8000);
    await refreshAfterPick(ctx);
  } catch (err) { $("#picker-hint").textContent = err.message; fail(err); }
  finally { box.classList.remove("busy"); $("#picker-file").value = ""; }
}
$("#picker-upload-btn").addEventListener("click", () => $("#picker-file").click());
$("#picker-file").addEventListener("change", (e) => uploadPickerImage(e.target.files[0]));
$("#picker").addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); $("#picker").classList.add("dragging"); } });
$("#picker").addEventListener("dragleave", (e) => { if (e.target === $("#picker")) $("#picker").classList.remove("dragging"); });
$("#picker").addEventListener("drop", (e) => {
  if (!e.dataTransfer?.files?.length) return;
  e.preventDefault();
  $("#picker").classList.remove("dragging");
  uploadPickerImage(e.dataTransfer.files[0]);
});

// after a pick/upload/delete: update just this tile – re-rendering the whole grid would make the page jump
async function refreshAfterPick({ i, card, side }) {
  const slug = pctx().slug;
  const plan = await api(`/api/decks/${enc(slug)}/print/plan?${planQuery()}`);
  if (pctx()?.slug !== slug) return;
  printPlan = plan;
  renderPlanSummary();
  const j = plan.cards.findIndex((c) => c.name === card.name);
  const tile = $(`#print-grid .pcard[data-i="${i}"]`);
  if (j < 0 || !tile) { renderPlan(); return; }
  tile.outerHTML = cardTile(plan.cards[j], j, side);
  if (card.token) renderTokenList();
  $(`#print-grid .pcard[data-i="${j}"]`)?.focus({ preventScroll: true });
}
$("#picker-grid").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    const ok = await ask({ title: "Eigenes Bild löschen?", text: "Das hochgeladene Bild wird entfernt. Nutzt die Karte es gerade, wählt die App wieder automatisch.", ok: "Löschen", danger: true });
    if (!ok) return;
    try {
      const slug = pctx().slug, ctx = pickerCtx;
      await api(`/api/decks/${enc(slug)}/print/uploads/${enc(del.dataset.del)}`, { method: "DELETE" });
      ctx.options = ctx.options.filter((o) => o.upload !== del.dataset.del);
      renderPicker();
      await refreshAfterPick(ctx);
      toast("Bild gelöscht.");
    } catch (err) { fail(err); }
    return;
  }
  const t = e.target.closest("[data-k]");
  if (t) pick(pickerCtx.options[Number(t.dataset.k)]).catch(fail);
});
$("#picker-auto").addEventListener("click", () => pick(null).catch(fail));
$("#picker-close").addEventListener("click", () => $("#picker").close());

$("#prepare-btn").addEventListener("click", async () => {
  const ctx0 = pctx();
  if (!ctx0 || currentJob) return;
  const { slug, name, route: back } = ctx0;
  try {
    const { job } = await api(`/api/decks/${enc(slug)}/print/prepare`, { method: "POST", body: printOpts() });
    startJob(job, `Druckdateien für ${name}`, { kind: "print", slug, slot: "#print-job-slot", route: back });
    $("#print-job-slot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { fail(err); }
});

// ---------- before/after comparison ----------
let compareFace = null;
function imageUrl(face, kind) {
  return `/api/decks/${enc(pctx().slug)}/print/image?face=${enc(face)}&kind=${kind}&t=${Date.now()}`;
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
    const r = await api(`/api/decks/${enc(pctx().slug)}/print/open-folder`, { method: "POST" });
    if (!r.opened) toast(`Ordner: ${r.path}`);
  } catch (err) { fail(err); }
});

async function onPrepared(result) {
  logLine("result", `Druckbilder: ${result.images_dir}`);
  if (result.missing.length) toast(`Ohne Bild: ${result.missing.join(", ")}`, "error");
  if (pctx() && printLoadedFor === pctx().slug) {
    prepared = await api(`/api/decks/${enc(pctx().slug)}/print/prepared`).catch(() => prepared);
    renderPlan();
    renderPreparedInfo();
    updateDownloadLinks();
  }
}

async function updateDownloadLinks() {
  const base = `/api/decks/${enc(pctx().slug)}/print/files`;
  const have = await api(base).catch(() => ({}));  // which files exist – no 404 noise in the console
  for (const [id, kind] of [["#xml-link", "xml"], ["#pdf-link", "pdf"]]) {
    $(id).href = `${base}/${kind}`;
    $(id).hidden = !have[kind];
  }
}

$("#pdf-btn").addEventListener("click", async () => {
  const btn = $("#pdf-btn");
  btn.disabled = true;
  btn.textContent = "Erstelle PDF …";
  try {
    const r = await api(`/api/decks/${enc(pctx().slug)}/print/pdf`, { method: "POST", body: { paper: $("#pdf-paper").value, include_backs: $("#pdf-backs").checked } });
    await updateDownloadLinks();
    window.open($("#pdf-link").href, "_blank");
    toast(`PDF erstellt: ${r.cards} Karten auf ${r.pages} Seiten.`);
  } catch (err) { fail(err); }
  finally { btn.disabled = false; btn.textContent = "PDF erstellen"; }
});

$("#mpc-btn").addEventListener("click", async () => {
  const ctx0 = pctx();
  if (!ctx0 || currentJob) return;
  const { slug, name, route: back } = ctx0;
  try {
    const r = await api(`/api/decks/${enc(slug)}/print/autofill`, { method: "POST", body: { mode: "mpc", window: $("#mpc-window").checked, ...TERM_SIZE } });
    if (r.job) {
      startJob(r.job, `MPC Autofill: ${name}`, { kind: "autofill", slug, slot: "#print-job-slot", route: back });
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
  if (!pctx()) return;
  const opts = printOpts();
  const ok = await ask({ title: "Gedruckte Karten übernehmen?", ok: "Übernehmen",
    text: `${printPlan?.quantity ?? "Alle"} Karten dieses Druckauftrags kommen als Proxy (mit dem gewählten Artwork) in deine Sammlung.` });
  if (!ok) return;
  try {
    const r = await api(`/api/decks/${enc(pctx().slug)}/collection/add-printed`, { method: "POST", body: opts });
    toast(`${r.added} Proxies zur Sammlung hinzugefügt.`);
    await refreshCollectionSummary();
    if (currentDeck && !pctx().order) loadOwnership(currentDeck);
  } catch (err) { fail(err); }
});

// ============================================================================================
// blacklist
// ============================================================================================
async function refreshBlacklist() {
  const bl = await api("/api/blacklist").catch(() => ({ cards: [], rules: [], catalog: [] }));
  const active = new Set(bl.rules.map((r) => r.rule));
  $("#bl-count").textContent = bl.cards.length + bl.rules.length || "";
  $("#bl-empty").hidden = bl.cards.length > 0;
  $("#bl-list").innerHTML = bl.cards.map((n) => `<li>${esc(n)}<button type="button" title="Entfernen" aria-label="${esc(n)} entfernen" data-name="${esc(n)}">×</button></li>`).join("");
  $("#bl-rules-empty").hidden = bl.rules.length > 0;
  $("#bl-rules").innerHTML = bl.rules.map((r) => `<li>
      <div class="bl-rule-main">
        <strong>${esc(r.label)}</strong>
        <span class="pill ${r.checked ? "ok" : "accent"}">${r.checked ? "wird geprüft" : "Hinweis für Claude"}</span>
        <p class="muted small">${esc(r.description)}</p>
        ${r.cards ? `<details class="more"><summary>${r.cards.length} Karten</summary><p class="small">${r.cards.map(esc).join(" · ")}</p></details>` : ""}
      </div>
      <button type="button" class="btn ghost small" data-name="${esc(r.rule)}" aria-label="${esc(r.label)} entfernen">Entfernen</button>
    </li>`).join("");
  const hasPrice = bl.rules.some((r) => r.key === "price");
  $("#bl-quick").innerHTML = bl.catalog.filter((c) => !active.has(c.rule))
    .map((c) => `<button type="button" class="chip" data-rule="${esc(c.rule)}" title="${esc(c.description)}">${esc(c.label)}</button>`).join("")
    + (hasPrice ? "" : `<button type="button" class="chip" data-price="1" title="Karten über einem Preis sperren">Teurer als … €</button>`);
}
async function addToBlacklist(add) {
  const r = await api("/api/blacklist", { method: "POST", body: { add } });
  const parts = [];
  if (r.added.length) parts.push(`Karten: ${r.added.join(", ")}`);
  if (r.added_rules.length) parts.push(`Regeln: ${r.added_rules.map((x) => x.label).join(", ")}`);
  $("#bl-msg").textContent = parts.length ? `Hinzugefügt – ${parts.join(" · ")}` : "";
  for (const term of r.not_found) {
    const ok = await ask({ title: `„${term}“ nicht gefunden`, ok: "Als Begriff speichern",
      text: "Das ist weder eine Karte noch ein bekannter Begriff. Als freien Begriff speichern? Claude beachtet ihn beim Bauen, automatisch geprüft wird er nicht." });
    if (ok) await api("/api/blacklist", { method: "POST", body: { add: [`@${term}`] } }).catch(fail);
  }
  await refreshBlacklist();
}
$("#bl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = $("#bl-input").value.trim();
  if (!raw) return;
  const add = raw.split(/[;\n]/).map((x) => x.trim()).filter(Boolean);
  try {
    $("#bl-input").value = "";
    await addToBlacklist(add);
  } catch (err) { $("#bl-msg").textContent = err.message; }
});
$("#bl-quick").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  let rule = btn.dataset.rule;
  if (btn.dataset.price) {
    const v = await ask({ title: "Karten über welchem Preis sperren?", text: "Betrag in Euro (günstigster Druck). Proxy-Decks prüfen ihn genauso.", value: "20", ok: "Sperren" });
    const n = parseFloat(String(v || "").replace(",", "."));
    if (!(n > 0)) return;
    rule = `@price>${n}`;
  }
  await addToBlacklist([rule]).catch(fail);
});
for (const list of ["#bl-list", "#bl-rules"]) {
  $(list).addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-name]");
    if (!btn) return;
    await api("/api/blacklist", { method: "POST", body: { remove: [btn.dataset.name] } }).catch(fail);
    $("#bl-msg").textContent = "";
    refreshBlacklist();
  });
}

// ============================================================================================
// opponent decks ("Gegnerdecks"): commander + what stood out, linked from the game log
// ============================================================================================
let oppIndex = [];
let oppTags = {};
let currentOpp = null;

async function refreshOpponents() {
  const data = await api("/api/opponents").catch(() => ({ opponents: [], tags: {} }));
  oppIndex = data.opponents;
  oppTags = data.tags;
  $("#opp-count").textContent = oppIndex.length || "";
  const sel = $("#upgrade-opp");
  const keep = sel.value;
  sel.innerHTML = '<option value="">alle meine Gegner berücksichtigen</option>'
    + oppIndex.map((o) => `<option value="${esc(o.id)}">${esc(o.title)}</option>`).join("");
  sel.value = oppIndex.some((o) => o.id === keep) ? keep : "";
  $(".upgrade-opp").hidden = !oppIndex.length;
}

function oppMeta(o) {
  const r = o.record;
  return [o.player && `spielt ${o.player}`, o.bracket && `etwa Bracket ${o.bracket}`,
    r.games ? `deine Bilanz ${r.wins}–${r.losses}${r.draws ? "–" + r.draws : ""} in ${r.games} Partie${r.games === 1 ? "" : "n"}` : "noch keine Partie",
    ...o.tag_labels].filter(Boolean).join(" · ");
}

// "Neues Deck" → "Gegen meine Runde bauen": which opponent decks the build targets
let metaResult = null;  // last meta suggestions {created, analysis, suggestions, sources, research, …}
let metaPick = null;    // the suggestion chosen for building
let metaLoaded = false;

function renderMetaSuggestions(result) {
  metaResult = result && result.suggestions?.length ? result : null;
  $("#meta-suggestions").hidden = !metaResult || buildForm.dataset.mode !== "meta";
  if (!metaResult) return;
  const r = metaResult;
  $("#meta-meta").textContent = `vom ${fmtDate(r.created)} · Opus 5.5, extra hoher Denkaufwand · ${r.research ? "mit" : "ohne"} Web-Recherche`;
  $("#meta-analysis").textContent = r.analysis || "";
  $("#meta-analysis").hidden = !r.analysis;
  $("#meta-list").innerHTML = r.suggestions.map((s, i) => {
    const colors = (s.color_identity || []).map((c) => COLOR_NAMES[c] || c).join(", ") || "Farblos";
    return `<article class="suggestion">
      ${s.image ? `<img src="${esc(s.image)}" alt="${esc(s.name)}" loading="lazy">` : ""}
      <h3>${i + 1}. ${esc(s.name)}${s.partner ? " + " + esc(s.partner) : ""}</h3>
      <p class="muted small">${esc(s.archetype || "")} · ${esc(colors)}</p>
      ${s.difficulty ? `<span class="difficulty ${esc(s.difficulty)}" title="${esc(s.difficulty_note || "")}">${esc(s.difficulty[0].toUpperCase() + s.difficulty.slice(1))} zu spielen</span>` : ""}
      <p><span class="why-label">Warum gegen deine Runde?</span> ${esc(s.why || "")}</p>
      ${s.win_plan ? `<p><span class="label">So gewinnt es:</span> ${esc(s.win_plan)}</p>` : ""}
      ${s.matchups?.length ? `<details class="more"><summary>Gegen deine Gegner</summary><ul>${s.matchups.map((m) =>
        `<li><strong>${esc(m.opponent)}:</strong> ${esc(m.plan)}</li>`).join("")}</ul></details>` : ""}
      ${s.key_cards?.length ? `<div class="key-cards" aria-label="Schlüsselkarten">${s.key_cards.map((c) => `<span>${esc(c)}</span>`).join("")}</div>` : ""}
      ${s.risks ? `<p class="muted small">Schwäche: ${esc(s.risks)}</p>` : ""}
      ${s.bracket_fit ? `<p class="muted small">${esc(s.bracket_fit)}</p>` : ""}
      <div class="actions">
        <button type="button" class="btn" data-meta-use="${i}">Übernehmen</button>
        <button type="button" class="btn primary" data-meta-build="${i}">Dieses Deck bauen</button>
      </div>
    </article>`;
  }).join("");
  const src = r.sources || [];
  $("#meta-sources-box").hidden = !src.length;
  $("#meta-sources").innerHTML = src.map((u) => /^https?:\/\//.test(u)
    ? `<li><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></li>` : `<li>${esc(u)}</li>`).join("");
  if (!$("#meta-suggestions").hidden && jobInfo?.kind === "meta") $("#meta-suggestions").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("#meta-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-meta-use], button[data-meta-build]");
  if (!btn || !metaResult) return;
  const s = metaResult.suggestions[Number(btn.dataset.metaUse ?? btn.dataset.metaBuild)];
  metaPick = s;
  buildForm.elements.meta_action.value = "build";
  buildForm.dataset.metaAction = "build";
  buildForm.elements.meta_commander.value = s.name;
  if (s.strategy) buildForm.elements.strategy.value = s.strategy;
  if (btn.dataset.metaBuild !== undefined) buildForm.requestSubmit();
  else {
    buildForm.scrollIntoView({ behavior: "smooth", block: "start" });
    toast(`${s.name} übernommen – prüf noch Bracket, Tischregel und Budget, dann „Stärkstes Deck bauen lassen“.`);
  }
});

async function renderMetaBox() {
  if (!metaLoaded) {
    metaLoaded = true;
    api("/api/build-meta/suggestions").then((r) => { if (!metaResult) renderMetaSuggestions(r); }).catch(() => {});
  }
  await refreshOpponents();
  const keep = new Set($$("#meta-opps input").filter((c) => !c.checked).map((c) => c.value));
  $("#meta-empty").hidden = !!oppIndex.length;
  $("#meta-opps-head").hidden = !oppIndex.length;
  $("#meta-opps").innerHTML = oppIndex.map((o) => `<label title="${esc(oppMeta(o))}"><input type="checkbox" name="meta_opps" value="${esc(o.id)}"${keep.has(o.id) ? "" : " checked"}><span>${esc(o.title)}</span></label>`).join("");
  const games = oppIndex.reduce((n, o) => n + o.record.games, 0);
  $("#meta-summary").textContent = oppIndex.length
    ? `${deckIndex.length} eigene${deckIndex.length === 1 ? "s Deck" : " Decks"} · ${oppIndex.length} Gegnerdeck${oppIndex.length === 1 ? "" : "s"} · ${games} festgehaltene Partie${games === 1 ? "" : "n"} gegen sie`
    : "";
}
$("#meta-all").addEventListener("click", () => { for (const cb of $$("#meta-opps input")) cb.checked = true; });

// game form: quick picks of known opponents fill the next empty slot
function renderKnownOpps() {
  const box = $("#game-known-opps");
  const top = oppIndex.slice(0, 12);
  box.hidden = !top.length;
  box.innerHTML = top.length ? '<em class="opp-known-label">Deine Gegner:</em>' + top.map((o) =>
    `<button type="button" class="chip" data-oid="${esc(o.id)}" title="${esc(oppMeta(o))}">${esc(o.title)}</button>`).join("") : "";
}
$("#game-known-opps").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-oid]");
  if (!b) return;
  const o = oppIndex.find((x) => x.id === b.dataset.oid);
  const els = $("#game-form").elements;
  const slot = [1, 2, 3].map((n) => els[`opp${n}`]).find((el) => !el.value.trim() || el.dataset.oid === o.id);
  if (!slot) { toast("Alle drei Gegner-Felder sind belegt.", "error"); return; }
  slot.value = o.commanders.join(" + ").split(" + ")[0];
  slot.dataset.oid = o.id;
  els[slot.name.replace("opp", "oppnote")].focus();
});
for (const n of [1, 2, 3]) {
  $(`#game-form [name=opp${n}]`).addEventListener("input", (e) => { delete e.target.dataset.oid; });
}

async function showOpponents(id) {
  $("#opp-list-view").hidden = !!id;
  $("#opp-view").hidden = !id;
  await refreshOpponents();
  if (!id) {
    currentOpp = null;
    $("#opp-empty").hidden = !!oppIndex.length;
    $("#opp-list").innerHTML = oppIndex.map((o) => `<a class="panel order-row opp-row" href="#/opponents/${enc(o.id)}">
        ${o.image ? `<img src="${esc(o.image)}" alt="" loading="lazy">` : ""}
        <div><h3>${esc(o.title)}</h3><p class="muted small">${esc(oppMeta(o))}</p>
          ${o.notes.length ? `<p class="small opp-last">„${esc(o.notes[o.notes.length - 1].text)}“</p>` : ""}</div>
        <span class="muted small">${o.record.last ? esc(fmtDate(o.record.last)) : ""}</span></a>`).join("");
    return;
  }
  try { currentOpp = await api(`/api/opponents/${enc(id)}`); } catch (err) { fail(err); go("#/opponents"); return; }
  renderOpponent();
}

function renderOpponent() {
  const o = currentOpp;
  $("#opp-name").textContent = o.title;
  $("#opp-meta").textContent = oppMeta(o);
  $("#opp-thumb").hidden = !o.image;
  if (o.image) $("#opp-thumb").src = o.image;
  $("#opp-edhrec").href = o.edhrec_url;
  const f = $("#opp-form").elements;
  f.commander.value = o.commanders[0] || "";
  f.partner.value = o.commanders[1] || "";
  f.label.value = o.label || "";
  f.player.value = o.player || "";
  f.bracket.value = o.bracket || "";
  $("#opp-table").innerHTML = '<option value="">–</option>' + tableSets.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  $("#opp-table").value = o.table_rule && tableById(o.table_rule) ? o.table_rule : "";
  $("#opp-tags").innerHTML = Object.entries(oppTags).map(([k, label]) =>
    `<label><input type="checkbox" name="tags" value="${esc(k)}"${o.tags.includes(k) ? " checked" : ""}><span>${esc(label)}</span></label>`).join("");
  $("#opp-notes-empty").hidden = !!o.notes.length;
  const deckName = (slug) => deckIndex.find((d) => d.slug === slug)?.name || slug;
  $("#opp-notes").innerHTML = o.notes.slice().reverse().map((n) => `<li>
      <div><p>${esc(n.text)}</p><span class="muted small">${esc(fmtDate(n.at))}${n.deck_slug ? ` · Partie mit <a href="#/deck/${enc(n.deck_slug)}/partien">${esc(deckName(n.deck_slug))}</a>` : ""}</span></div>
      <button type="button" class="icon-btn" data-note="${esc(n.id)}" aria-label="Notiz löschen" title="Notiz löschen">${icon("x")}</button></li>`).join("");
  const r = o.record;
  $("#opp-record").innerHTML = !r.games ? '<p class="empty-inline">Noch keine Partie gegen dieses Deck festgehalten.</p>'
    : `<div class="kpis"><div><b>${r.games}</b><span>Partien</span></div><div><b>${r.wins}–${r.losses}${r.draws ? "–" + r.draws : ""}</b><span>Siege–Niederlagen</span></div></div>
       ${r.per_deck.length > 1 ? `<p class="game-stats-more">${r.per_deck.map((p) => `${esc(p.name)} ${p.wins}–${p.losses}`).join(" · ")}</p>` : ""}`;
  const labels = { win: "Sieg", loss: "Niederlage", draw: "Unentschieden" };
  $("#opp-games").innerHTML = r.history.map((g) => `<li>
      <div class="when"><span class="res ${esc(g.result)}">${esc(labels[g.result] || g.result)}</span><br><span class="meta">${esc(fmtDate(g.played))}</span></div>
      <div>mit <a href="#/deck/${enc(g.deck_slug)}/partien">${esc(g.deck)}</a>${g.turn ? ` · Zug ${esc(g.turn)}` : ""}${g.note ? `<br><span class="meta">${esc(g.note)}</span>` : ""}</div><span></span></li>`).join("");
  const mine = $("#opp-my-deck");
  const faced = r.per_deck[0]?.slug;
  mine.innerHTML = deckIndex.map((d) => `<option value="${esc(d.slug)}">${esc(d.name)}</option>`).join("") || '<option value="">– noch keine Decks –</option>';
  if (faced && deckIndex.some((d) => d.slug === faced)) mine.value = faced;
  $("#opp-ask").disabled = $("#opp-upgrade").disabled = !deckIndex.length;
}

async function patchOpp(body, msg = "") {
  currentOpp = await api(`/api/opponents/${enc(currentOpp.id)}`, { method: "PATCH", body });
  renderOpponent();
  refreshOpponents();
  if (msg) toast(msg);
}
$("#opp-new").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  try {
    const o = await api("/api/opponents", { method: "POST", body: { commanders: [f.commander.value], label: f.label.value, note: f.note.value } });
    e.target.reset();
    toast(`„${o.title}“ gemerkt.`);
    go(`#/opponents/${enc(o.id)}`);
  } catch (err) { fail(err); }
});
$("#opp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const fd = new FormData(e.target);
  try {
    await patchOpp({ commanders: [f.commander.value, f.partner.value].filter((x) => x.trim()), label: f.label.value, player: f.player.value,
      bracket: f.bracket.value ? Number(f.bracket.value) : null, table_rule: f.table_rule.value || null, tags: fd.getAll("tags") }, "Gespeichert.");
  } catch (err) { fail(err); }
});
$("#opp-note-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const note = e.target.elements.note.value.trim();
  if (!note) return;
  try { await patchOpp({ note }); e.target.reset(); } catch (err) { fail(err); }
});
$("#opp-notes").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-note]");
  if (b) await patchOpp({ remove_note: b.dataset.note }).catch(fail);
});
$("#opp-delete").addEventListener("click", async () => {
  $("#opp-menu").open = false;
  const o = currentOpp;
  if (!(await ask({ title: `„${o.title}“ löschen?`, danger: true, ok: "Löschen",
    text: "Notizen und Steckbrief gehen verloren. Deine Partien bleiben erhalten, nur ohne Verknüpfung." }))) return;
  try {
    await api(`/api/opponents/${enc(o.id)}`, { method: "DELETE" });
    toast(`„${o.title}“ gelöscht.`);
    go("#/opponents");
  } catch (err) { fail(err); }
});
$("#opp-ask").addEventListener("click", async () => {
  const slug = $("#opp-my-deck").value;
  if (!slug) return;
  const o = currentOpp;
  go(`#/deck/${enc(slug)}/fragen`);
  await new Promise((r) => setTimeout(r, 50));
  if (currentDeck?.slug !== slug && !(await openDeck(slug))) return;
  const q = $("#qa-form").elements.question;
  q.value = `Wie spiele ich mit diesem Deck gegen ${o.title}? Worauf muss ich achten, welche Karten halte ich zurück, was ist ihre Schwachstelle?`
    + (o.notes.length ? ` Mir ist aufgefallen: ${o.notes.slice(-3).map((n) => n.text).join("; ")}.` : "");
  q.focus();
  toast("Frage vorbereitet – „Fragen“ schickt sie an Claude.");
});
$("#opp-upgrade").addEventListener("click", async () => {
  const slug = $("#opp-my-deck").value;
  if (!slug) return;
  const id = currentOpp.id;
  go(`#/deck/${enc(slug)}/anpassen`);
  await new Promise((r) => setTimeout(r, 50));
  if (currentDeck?.slug !== slug && !(await openDeck(slug))) return;
  showTune("upgrades");
  $("#upgrade-opp").value = id;
  const f = $("#upgrade-form");
  f.scrollIntoView({ behavior: "smooth", block: "center" });
  $("#upgrade-btn").focus({ preventScroll: true });
  toast("Gegner eingetragen – „Vorschläge holen“ startet die Suche.");
});

// ============================================================================================
// table rules ("Tischregeln"): rule sets per playgroup, chosen per deck
// ============================================================================================
let tableSets = [];
let tableCatalog = [];
let currentTable = null;

async function refreshTableRules() {
  const data = await api("/api/tablerules").catch(() => ({ sets: [], catalog: [] }));
  tableSets = data.sets;
  tableCatalog = data.catalog;
  $("#tr-count").textContent = tableSets.length || "";
  const opts = (sel) => '<option value="">Keine Tischregel</option>'
    + tableSets.map((t) => `<option value="${esc(t.id)}"${t.id === sel ? " selected" : ""}>${esc(t.name)}</option>`).join("");
  const build = $("#build-table");
  build.innerHTML = opts(build.value);
  updateBuildTableHint();
  if (currentDeck) renderDeckTable(currentDeck);
}

function tableById(id) { return tableSets.find((t) => t.id === id) || null; }

function updateBuildTableHint() {
  const t = tableById($("#build-table").value);
  const hint = $("#build-table-hint");
  hint.hidden = !t;
  if (!t) return;
  const notes = [];
  const bracket = Number(new FormData(buildForm).get("bracket"));
  if (t.max_bracket && bracket > t.max_bracket) notes.push(`<strong>Höchstens Bracket ${t.max_bracket}</strong> – stell das Bracket oben niedriger.`);
  if (t.no_proxies && $("#proxy").checked) notes.push("<strong>Keine Proxies</strong> an diesem Tisch – schalte „Proxy-Deck“ aus.");
  hint.innerHTML = (notes.length ? `<span class="warn-text">${notes.join(" ")}</span><br>` : "")
    + (t.summary.length ? esc(t.summary.join(" · ")) : "Diese Tischregel ist noch leer.");
}
$("#build-table").addEventListener("change", updateBuildTableHint);
$("#build-table").addEventListener("change", () => {  // meta build: the opponents of that table, if any are assigned
  if (buildForm.dataset.mode !== "meta") return;
  const id = $("#build-table").value;
  const atTable = oppIndex.filter((o) => id && o.table_rule === id).map((o) => o.id);
  if (!atTable.length) return;
  for (const cb of $$("#meta-opps input")) cb.checked = atTable.includes(cb.value);
  toast(`Gegner der Runde „${tableById(id).name}“ ausgewählt.`);
});
$("#bracket-options").addEventListener("change", updateBuildTableHint);
$("#proxy").addEventListener("change", updateBuildTableHint);

function renderDeckTable(d) {
  const sel = $("#deck-table");
  const id = d.table_rule || "";
  sel.innerHTML = '<option value="">Keine Tischregel</option>'
    + tableSets.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  sel.value = tableById(id) ? id : "";
  const box = $("#deck-table-status");
  const t = d.validation?.table_rule;
  if (!id || !t?.name) {
    box.innerHTML = tableSets.length ? "" : `<p class="muted small">Noch keine Tischregeln – <a href="#/tables">jetzt eine anlegen</a>.</p>`;
    return;
  }
  const bad = t.violations || [];
  box.innerHTML = `<p class="${bad.length ? "bad-text" : "ok-text"}">${icon(bad.length ? "alert" : "check")}
      ${bad.length ? `${bad.length} ${bad.length === 1 ? "Verstoß" : "Verstöße"} gegen „${esc(t.name)}“` : `Passt zu „${esc(t.name)}“`}</p>
    ${bad.length ? `<ul class="tr-violations">${bad.map((v) => `<li>${esc(v.split(": ").slice(1).join(": ") || v)}</li>`).join("")}</ul>` : ""}
    ${(t.warnings || []).map((w) => `<p class="muted small">${esc(w)}</p>`).join("")}
    <p class="muted small">${esc((t.summary || []).join(" · "))}</p>
    <div class="btn-row">
      ${bad.length ? `<button type="button" class="btn" id="deck-table-fix">${icon("spark")}Claude soll das Deck anpassen</button>` : ""}
      <a class="link small" href="#/tables/${enc(id)}">Tischregel bearbeiten</a>
    </div>`;
}
$("#deck-table").addEventListener("change", async (e) => {
  const d = currentDeck;
  const id = e.target.value || null;
  try {
    await api(`/api/decks/${enc(d.slug)}/table-rule`, { method: "PUT", body: { table_rule: id } });
    await openDeck(d.slug);
    refreshDeckList();
    const t = currentDeck.validation?.table_rule;
    toast(!id ? "Tischregel entfernt." : t?.compliant ? `Passt zu „${t.name}“.` : `Tischregel gesetzt – ${t?.violations?.length || 0} Verstöße.`);
  } catch (err) { fail(err); renderDeckTable(d); }
});
$("#deck-table-status").addEventListener("click", (e) => {
  if (!e.target.closest("#deck-table-fix")) return;
  const t = currentDeck.validation?.table_rule;
  showTune("refine");
  const form = $("#refine-form");
  form.elements.request.value = `Halte die Tischregel „${t.name}“ ein: ${(t.violations || []).map((v) => v.split(": ").slice(1).join(": ")).join("; ")}`;
  form.requestSubmit();
});

async function showTables(id) {
  $("#tr-list-view").hidden = !!id;
  $("#tr-view").hidden = !id;
  await refreshTableRules();
  if (!id) {
    currentTable = null;
    $("#tr-empty").hidden = !!tableSets.length;
    $("#tr-list").innerHTML = tableSets.map((t) => `<a class="panel order-row" href="#/tables/${enc(t.id)}">
        <div><h3>${esc(t.name)}</h3><p class="muted small">${esc(t.summary.join(" · ") || "noch leer")}</p></div>
        <span class="muted small">${t.decks ? `${t.decks} Deck${t.decks === 1 ? "" : "s"}` : ""}</span></a>`).join("");
    return;
  }
  currentTable = tableById(id);
  if (!currentTable) { toast("Diese Tischregel gibt es nicht mehr.", "error"); go("#/tables"); return; }
  $("#tr-decks").innerHTML = "";
  renderTable();
}

function renderTable() {
  const t = currentTable;
  $("#tr-name").textContent = t.name;
  $("#tr-meta").textContent = t.decks ? `Gilt für ${t.decks} Deck${t.decks === 1 ? "" : "s"}.` : "Noch von keinem Deck gewählt.";
  const f = $("#tr-form").elements;
  f.name.value = t.name;
  f.description.value = t.description || "";
  f.max_bracket.value = t.max_bracket || "";
  f.max_game_changers.value = t.max_game_changers ?? "";
  f.max_tutors.value = t.max_tutors ?? "";
  f.deck_budget.value = t.deck_budget ?? "";
  f.no_proxies.checked = !!t.no_proxies;
  const active = new Set(t.rules);
  $("#tr-rules").innerHTML = t.rule_info.map((r) => `<li>
      <div class="bl-rule-main">
        <strong>${esc(r.label)}</strong>
        <span class="pill ${r.checked ? "ok" : "accent"}">${r.checked ? "wird geprüft" : "Absprache"}</span>
        <p class="muted small">${esc(r.description)}</p>
      </div>
      <button type="button" class="btn ghost small" data-remove="${esc(r.rule)}" aria-label="${esc(r.label)} entfernen">Entfernen</button>
    </li>`).join("");
  $("#tr-cards").innerHTML = t.cards.map((n) => `<li>${esc(n)}<button type="button" title="Entfernen" aria-label="${esc(n)} entfernen" data-remove="${esc(n)}">×</button></li>`).join("");
  $("#tr-rules-empty").hidden = !!(t.rules.length || t.cards.length);
  $("#tr-quick").innerHTML = tableCatalog.filter((c) => !active.has(c.rule))
    .map((c) => `<button type="button" class="chip" data-rule="${esc(c.rule)}" title="${esc(c.description)}">${esc(c.label)}</button>`).join("")
    + (t.rules.some((r) => r.startsWith("@price>")) ? "" : `<button type="button" class="chip" data-price="1" title="Karten über einem Preis verbieten">Karten teurer als … €</button>`);
}

async function patchTable(body, msg = "") {
  const r = await api(`/api/tablerules/${enc(currentTable.id)}`, { method: "PATCH", body });
  await refreshTableRules();
  currentTable = tableById(currentTable.id);
  renderTable();
  if (r.revalidated?.length) toast(`${r.revalidated.length} Deck${r.revalidated.length === 1 ? "" : "s"} neu geprüft.`);
  else if (msg) toast(msg);
  return r;
}

$("#tr-new").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const t = await api("/api/tablerules", { method: "POST", body: { name: e.target.elements.name.value } });
    e.target.reset();
    go(`#/tables/${enc(t.id)}`);
  } catch (err) { fail(err); }
});
$("#tr-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const num = (v) => (v === "" ? null : Number(v));
  try {
    await patchTable({
      name: f.name.value, description: f.description.value, max_bracket: num(f.max_bracket.value),
      max_game_changers: num(f.max_game_changers.value), max_tutors: num(f.max_tutors.value),
      deck_budget: num(f.deck_budget.value), no_proxies: f.no_proxies.checked,
    }, "Gespeichert.");
  } catch (err) { fail(err); }
});
async function addToTable(add) {
  const r = await patchTable({ add });
  const parts = [];
  if (r.added?.length) parts.push(`Karten: ${r.added.join(", ")}`);
  if (r.added_rules?.length) parts.push(`Regeln: ${r.added_rules.map((x) => x.label).join(", ")}`);
  $("#tr-msg").textContent = parts.length ? `Hinzugefügt – ${parts.join(" · ")}` : "";
  for (const term of r.not_found || []) {
    const ok = await ask({ title: `„${term}“ nicht gefunden`, ok: "Als Absprache speichern",
      text: "Das ist weder eine Karte noch ein bekannter Begriff. Als freie Absprache speichern? Claude hält sich beim Bauen daran, automatisch geprüft wird sie nicht." });
    if (ok) await patchTable({ add: [`@${term}`] }).catch(fail);
  }
}
$("#tr-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const add = $("#tr-input").value.split(/[;\n]/).map((x) => x.trim()).filter(Boolean);
  if (!add.length) return;
  $("#tr-input").value = "";
  await addToTable(add).catch(fail);
});
$("#tr-quick").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  let rule = btn.dataset.rule;
  if (btn.dataset.price) {
    const v = await ask({ title: "Karten über welchem Preis verbieten?", text: "Betrag in Euro (günstigster Druck).", value: "10", ok: "Verbieten" });
    const n = parseFloat(String(v || "").replace(",", "."));
    if (!(n > 0)) return;
    rule = `@price>${n}`;
  }
  await addToTable([rule]).catch(fail);
});
for (const list of ["#tr-rules", "#tr-cards"]) {
  $(list).addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-remove]");
    if (!btn) return;
    $("#tr-msg").textContent = "";
    await patchTable({ remove: [btn.dataset.remove] }).catch(fail);
  });
}
$("#tr-delete").addEventListener("click", async () => {
  $("#tr-menu").open = false;
  const t = currentTable;
  const ok = await ask({ title: `Tischregel „${t.name}“ löschen?`, danger: true, ok: "Löschen",
    text: t.decks ? `${t.decks} Deck${t.decks === 1 ? "" : "s"} verlieren damit ihre Tischregel (als neue Version gespeichert).` : "" });
  if (!ok) return;
  try {
    await api(`/api/tablerules/${enc(t.id)}`, { method: "DELETE" });
    toast(`„${t.name}“ gelöscht.`);
    if (currentDeck?.table_rule === t.id) await openDeck(currentDeck.slug);
    go("#/tables");
  } catch (err) { fail(err); }
});
$("#tr-check").addEventListener("click", async () => {
  const btn = $("#tr-check");
  btn.disabled = true;
  $("#tr-decks").innerHTML = '<li class="muted small">Prüfe …</li>';
  try {
    const rows = await api(`/api/tablerules/${enc(currentTable.id)}/decks`);
    $("#tr-decks").innerHTML = rows.length ? rows.map((r) => `<li>
        <span class="pill ${r.ok ? "ok" : "bad"}">${icon(r.ok ? "check" : "alert")}${r.ok ? "passt" : `${r.errors.length} ${r.errors.length === 1 ? "Verstoß" : "Verstöße"}`}</span>
        <div><a href="#/deck/${enc(r.slug)}/anpassen">${esc(r.name)}</a>${r.uses ? ' <span class="muted small">· nutzt diese Tischregel</span>' : ""}
          <span class="muted small">· ${esc(r.commanders.join(" + "))} · Bracket ${esc(r.bracket ?? "?")}</span>
          ${r.errors.length ? `<ul class="tr-violations">${r.errors.map((v) => `<li>${esc(v.split(": ").slice(1).join(": ") || v)}</li>`).join("")}</ul>` : ""}
          ${r.warnings.map((w) => `<p class="muted small">${esc(w.split(": ").slice(1).join(": ") || w)}</p>`).join("")}
        </div></li>`).join("") : '<li class="muted small">Noch keine Decks gespeichert.</li>';
  } catch (err) { fail(err); $("#tr-decks").innerHTML = ""; }
  btn.disabled = false;
});

// ============================================================================================
// settings: backups (export / import / automatic) and the deck trash
// ============================================================================================
const fmtSize = (b) => (b >= 1e6 ? `${(b / 1e6).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

async function refreshBackups() {
  let data;
  try { data = await api("/api/backups"); } catch (err) { fail(err); return; }
  const autos = data.backups.filter((b) => b.kind === "auto");
  $("#backup-auto").textContent = `Automatisch: einmal am Tag beim Start der App, die letzten ${data.keep_auto} bleiben erhalten`
    + (autos.length ? ` (zuletzt ${fmtDate(autos[0].created)}).` : " (noch keine).") + ` Ordner: ${data.dir}`;
  $("#backup-list").innerHTML = data.backups.map((b) => `<li>
      <div class="grow"><strong>${esc(fmtDate(b.created) || b.name)}</strong> <span class="muted small">· ${esc(b.kind_label)}
        · ${b.decks ?? "?"} Decks · ${esc(fmtSize(b.size))}</span></div>
      <div class="actions">
        <a class="btn small" href="/api/backups/${enc(b.name)}" download>Herunterladen</a>
        <button type="button" class="btn small" data-restore="${esc(b.name)}">Wiederherstellen …</button>
      </div></li>`).join("") || '<li class="muted small">Noch keine Sicherung.</li>';
}

$("#backup-create").addEventListener("click", async () => {
  try {
    const b = await api("/api/backups", { method: "POST" });
    await refreshBackups();
    toast(`Gesichert: ${b.decks} Decks, ${b.files} Dateien.`);
  } catch (err) { fail(err); }
});
$("#backup-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const res = await fetch("/api/backups/upload", { method: "POST", body: file, headers: { "Content-Type": "application/zip" } });
    const b = await res.json();
    if (!res.ok) throw new Error(b.detail || res.statusText);
    await refreshBackups();
    await restoreBackup(b.name, b);
  } catch (err) { fail(err); }
});
$("#backup-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-restore]");
  if (btn) restoreBackup(btn.dataset.restore);
});
async function restoreBackup(name, info = null) {
  const ok = await ask({ title: "Sicherung wiederherstellen?", ok: "Wiederherstellen", danger: true,
    text: `${info?.created ? `Stand vom ${fmtDate(info.created)}${info.decks != null ? `, ${info.decks} Decks` : ""}. ` : ""}`
      + "Deine aktuellen Daten werden durch die Sicherung ersetzt. Vorher wird der jetzige Stand automatisch gesichert – du kannst also zurück." });
  if (!ok) return;
  try {
    const r = await api(`/api/backups/${enc(name)}/restore`, { method: "POST" });
    currentDeck = null;
    await Promise.all([refreshDeckList(), refreshTableRules(), refreshOpponents(), refreshBlacklist(), refreshCollectionSummary()]);
    await refreshBackups();
    await refreshTrash();
    toast(`Wiederhergestellt: ${r.decks} Decks. Der vorherige Stand liegt als Sicherung bereit.`);
  } catch (err) { fail(err); }
}

async function refreshTrash() {
  let items;
  try { items = await api("/api/trash"); } catch (err) { fail(err); return; }
  $("#trash-none").hidden = items.length > 0;
  $("#trash-empty").hidden = !items.length;
  $("#trash-list").innerHTML = items.map((t) => `<li>
      <div class="grow"><strong>${esc(t.name)}</strong> <span class="muted small">· ${esc((t.commanders || []).join(" + "))}
        · gelöscht ${esc(fmtDate(t.deleted))}</span></div>
      <div class="actions">
        <button type="button" class="btn small" data-trash-restore="${esc(t.id)}">Zurückholen</button>
        <button type="button" class="btn ghost small" data-trash-purge="${esc(t.id)}" aria-label="${esc(t.name)} endgültig löschen">Endgültig löschen …</button>
      </div></li>`).join("");
}
async function restoreFromTrash(id) {
  try {
    const r = await api(`/api/trash/${enc(id)}/restore`, { method: "POST" });
    await refreshDeckList();
    if (parseHash().view === "settings") refreshTrash();
    toast(`„${r.name}“ ist wieder da.`);
    go(`#/deck/${enc(r.slug)}`);
  } catch (err) { fail(err); }
}
$("#trash-list").addEventListener("click", async (e) => {
  const back = e.target.closest("button[data-trash-restore]");
  if (back) { restoreFromTrash(back.dataset.trashRestore); return; }
  const purge = e.target.closest("button[data-trash-purge]");
  if (!purge) return;
  if (!(await ask({ title: "Endgültig löschen?", text: "Das Deck mit Versionen, Fragen und Partien ist danach weg (außer in einer Sicherung).", ok: "Endgültig löschen", danger: true }))) return;
  try { await api(`/api/trash/${enc(purge.dataset.trashPurge)}`, { method: "DELETE" }); refreshTrash(); } catch (err) { fail(err); }
});
$("#trash-empty").addEventListener("click", async () => {
  if (!(await ask({ title: "Papierkorb leeren?", text: "Alle Decks im Papierkorb werden endgültig gelöscht (außer in einer Sicherung).", ok: "Leeren", danger: true }))) return;
  try { await api("/api/trash", { method: "DELETE" }); refreshTrash(); } catch (err) { fail(err); }
});

// ============================================================================================
// settings: sync with the own sync server (status, connect, pair another device, devices, conflicts)
// ============================================================================================
let syncInfo = null;
let syncSeenAt = null;  // the last run whose news were shown (no toast for runs before this page load)
let syncPairTimer = null;
const SYNC_POLL_MS = 30000;

const fmtAgo = (ts) => {
  if (!ts) return "nie";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  const d = Math.round(s / 86400);
  return d === 1 ? "gestern" : `vor ${d} Tagen`;
};
const fmtClock = (ts) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const syncHost = (url) => { try { return new URL(url).host; } catch { return url || ""; } };

function syncSummary(run) {
  if (!run) return "Noch nicht abgeglichen.";
  if (run.error) return `<span class="warn">Letzter Abgleich ${esc(fmtAgo(run.at))} hat nicht geklappt: ${esc(run.error)}</span>`;
  const parts = [];
  const more = (n) => (n ? ` und ${n} weitere` : "");
  if (run.incoming?.length) parts.push(`geholt: ${esc(run.incoming.join(", "))}${more(run.incoming_more)}`);
  if (run.outgoing?.length) parts.push(`gesendet: ${esc(run.outgoing.join(", "))}${more(run.outgoing_more)}`);
  if (run.conflicts) parts.push(`<a href="#" data-sync-conflicts>${run.conflicts} ${run.conflicts === 1 ? "Konflikt" : "Konflikte"}</a>`);
  return `Zuletzt abgeglichen ${esc(fmtAgo(run.at))} (${fmtClock(run.at)}) – ${parts.length ? parts.join(" · ") : "alles war schon aktuell"}.`;
}

function renderSyncDot() {
  const dot = $("#sync-dot");
  const on = syncInfo?.connected;
  dot.hidden = !on;
  if (!on) return;
  const bad = syncInfo.revoked || (syncInfo.last && !syncInfo.last.ok);
  dot.className = "sync-dot" + (syncInfo.running ? " busy" : bad ? " bad" : "");
  dot.title = syncInfo.running ? "Sync läuft …" : bad ? "Sync: Problem – siehe Einstellungen" : `Sync: abgeglichen ${fmtAgo(syncInfo.last_ok)}`;
  dot.setAttribute("aria-label", dot.title);
  dot.setAttribute("role", "img");
}

function renderSync() {
  renderSyncDot();
  const st = syncInfo;
  if (!st) return;
  const on = st.connected && !st.revoked;
  $("#sync-off").hidden = on;
  $("#sync-on").hidden = !on;
  $("#sync-revoked").hidden = !st.revoked;
  const f = $("#sync-connect").elements;
  if (!f.name.value) f.name.value = st.device?.name || st.default_name || "";
  if (!on) return;
  const d = st.device;
  const ok = st.last ? st.last.ok : null;
  $("#sync-line").innerHTML = `<span class="dot ${ok === false ? "bad" : ok ? "ok" : ""}" aria-hidden="true"></span>`
    + `<span>Verbunden mit ${esc(syncHost(d.server))} als „${esc(d.name)}“</span>`
    + (st.running ? '<span class="spinner" aria-label="Abgleich läuft"></span>' : "");
  $("#sync-result").innerHTML = syncSummary(st.last)
    + (st.pending_revalidation?.length ? ` <span class="muted">${st.pending_revalidation.length === 1 ? "Ein zusammengeführtes Deck wird" : `${st.pending_revalidation.length} zusammengeführte Decks werden`} noch geprüft (braucht Internet).</span>` : "");
  $("#sync-interval").value = String(st.interval);
  if (![...$("#sync-interval").options].some((o) => o.selected)) $("#sync-interval").value = "5";
  $("#sync-conflict-count").textContent = st.conflicts || "";
  $("#sync-details").textContent = `Server: ${d.server} · Gerät: ${d.name} (${d.device_id}) · verbunden seit ${fmtDate(new Date(d.paired_at * 1000).toISOString())}`;
}

async function refreshSync({ quiet = false } = {}) {
  try { syncInfo = await api("/api/sync"); } catch (err) { if (!quiet) fail(err); return; }
  if (syncSeenAt === null) syncSeenAt = syncInfo.last?.at || 0;
  renderSync();
  syncNews(syncInfo.last);
}

// news from the other devices: one toast per run that brought something in
function syncNews(run) {
  if (!run || !run.at || run.at <= syncSeenAt) return;
  syncSeenAt = run.at;
  if (!run.ok || !run.incoming?.length) return;
  const rest = run.incoming.length - 3 + (run.incoming_more || 0);
  const items = run.incoming.slice(0, 3).join(", ") + (rest > 0 ? ` und ${rest} weitere` : "");
  refreshDeckList();
  toast(`Neu von deinen anderen Geräten: ${items}`, "info", 9000, { label: "Anzeigen", run: reloadAfterSync });
}

async function reloadAfterSync() {
  await Promise.all([refreshDeckList(), refreshTableRules(), refreshOpponents(), refreshBlacklist(), refreshCollectionSummary(), refreshOrders()]);
  if (edit) { toast("Du bearbeitest gerade dieses Deck – speichere oder verwirf zuerst, dann siehst du den neuen Stand."); return; }
  currentDeck = null;
  route();
}

async function syncRun() {
  const btn = $("#sync-run");
  btn.disabled = true;
  if (syncInfo) { syncInfo.running = true; renderSync(); }
  try {
    const r = await api("/api/sync/run", { method: "POST" });
    syncInfo = r;
    renderSync();
    if (r.run.error) toast(r.run.error, "error", 7000);
    else if (!r.run.incoming?.length) toast(r.run.outgoing?.length ? "Abgeglichen – deine Änderungen sind auf dem Server." : "Abgeglichen – alles war schon aktuell.");
    syncNews(r.run);
    if (r.run.conflicts) refreshSyncConflicts();
  } catch (err) { fail(err); refreshSync({ quiet: true }); } finally { btn.disabled = false; }
}
$("#sync-run").addEventListener("click", syncRun);

$("#sync-connect").elements.link.addEventListener("input", (e) => {
  const v = e.target.value.trim();
  $("#sync-url-row").hidden = !/^[A-Za-z0-9]{4}-?[A-Za-z0-9]{4}$/.test(v);  // a bare code needs the address
});
$("#sync-connect").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const btn = $("#sync-connect-btn");
  btn.disabled = true;
  btn.textContent = "Verbinde und gleiche ab …";
  try {
    const r = await api("/api/sync/connect", { method: "POST", body: { link: f.link.value.trim(), url: f.url.value.trim(), name: f.name.value.trim() } });
    syncInfo = r;
    syncSeenAt = r.run?.at || syncSeenAt;
    f.link.value = "";
    renderSync();
    if (r.run?.error) toast(`Verbunden, aber der erste Abgleich hat nicht geklappt: ${r.run.error}`, "error", 8000);
    else toast(r.run?.incoming?.length ? "Verbunden – die Daten der anderen Geräte sind jetzt auch hier." : "Verbunden – deine Daten sind auf dem Server.");
    if (r.run?.incoming?.length) reloadAfterSync();
  } catch (err) { fail(err); } finally { btn.disabled = false; btn.textContent = "Verbinden"; }
});

$("#sync-interval").addEventListener("change", async (e) => {
  try {
    await api("/api/settings", { method: "POST", body: { sync_interval: Number(e.target.value) } });
    toast(Number(e.target.value) ? "Gespeichert – abgeglichen wird beim Start, nach Änderungen und regelmäßig." : "Gespeichert – abgeglichen wird nur noch per Knopf.");
    refreshSync({ quiet: true });
  } catch (err) { fail(err); }
});

$("#sync-pair").addEventListener("click", async () => {
  try {
    const p = await api("/api/sync/pair", { method: "POST" });
    $("#sync-qr").innerHTML = p.qr || "";
    $("#sync-qr").hidden = !p.qr;
    $("#sync-code").textContent = p.code;
    $("#sync-copy").dataset.link = p.link;
    $("#sync-pairing").hidden = false;
    clearInterval(syncPairTimer);
    const tick = () => {
      const left = Math.round(p.expires - Date.now() / 1000);
      $("#sync-code-hint").textContent = left > 0
        ? `Gültig noch ${Math.ceil(left / 60)} Min. (bis ${fmtClock(p.expires)}), nur einmal verwendbar.`
        : "Abgelaufen – bitte einen neuen Code erzeugen.";
      if (left <= 0) clearInterval(syncPairTimer);
    };
    tick();
    syncPairTimer = setInterval(tick, 15000);
  } catch (err) { fail(err); }
});
$("#sync-pair-close").addEventListener("click", () => { $("#sync-pairing").hidden = true; clearInterval(syncPairTimer); refreshSyncDevices(); });
$("#sync-copy").addEventListener("click", async (e) => {
  try { await navigator.clipboard.writeText(e.target.dataset.link); toast("Link kopiert."); }
  catch { await ask({ title: "Kopplungslink", text: "Zum Kopieren markieren:", value: e.target.dataset.link, ok: "Schließen" }); }
});

async function refreshSyncDevices() {
  let data;
  try { data = await api("/api/sync/devices"); } catch (err) { $("#sync-devices").innerHTML = `<li class="muted small">${esc(err.message)}</li>`; return; }
  const kinds = { pc: "PC", phone: "Handy", other: "Gerät" };
  $("#sync-device-count").textContent = data.devices.length || "";
  $("#sync-devices").innerHTML = data.devices.map((d) => {
    const ai = d.info?.ai;
    const state = d.online ? '<span class="ok">online</span>' : `zuletzt ${esc(fmtAgo(d.last_seen))}`;
    return `<li>
      <div class="grow"><strong>${esc(d.name)}</strong> <span class="muted small">· ${kinds[d.kind] || "Gerät"}${d.this ? " · dieses Gerät" : ""}
        · ${state}${ai && d.online ? ` · Claude ${ai.ready ? "bereit" : "nicht bereit"}` : ""}</span></div>
      ${d.this ? "" : `<div class="actions"><button type="button" class="btn ghost small" data-sync-revoke="${esc(d.id)}" data-name="${esc(d.name)}">Abmelden …</button></div>`}
    </li>`;
  }).join("") || '<li class="muted small">Keine Geräte.</li>';
}
$("#sync-devices-box").addEventListener("toggle", (e) => { if (e.target.open) refreshSyncDevices(); });
$("#sync-devices").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-sync-revoke]");
  if (!btn) return;
  if (!(await ask({ title: `„${btn.dataset.name}“ abmelden?`, text: "Das Gerät kann danach nicht mehr abgleichen, bis du es neu koppelst. Seine eigenen Daten bleiben dort erhalten.", ok: "Abmelden", danger: true }))) return;
  try { await api(`/api/sync/devices/${enc(btn.dataset.syncRevoke)}`, { method: "DELETE" }); toast(`„${btn.dataset.name}“ ist abgemeldet.`); refreshSyncDevices(); } catch (err) { fail(err); }
});

const conflictVal = (v) => {
  if (v === null || v === undefined) return "–";
  const t = typeof v === "string" ? v : JSON.stringify(v);
  return t.length > 140 ? t.slice(0, 140) + " …" : t;
};
async function refreshSyncConflicts() {
  let items;
  try { items = await api("/api/sync/conflicts"); } catch (err) { fail(err); return; }
  $("#sync-conflict-count").textContent = items.length || "";
  $("#sync-conflicts-none").hidden = items.length > 0;
  $("#sync-conflicts-clear").hidden = !items.length;
  $("#sync-conflicts").innerHTML = items.slice(0, 100).map((c) => `<li>
      <div class="grow"><strong>${esc(c.label)}</strong> <span class="muted small">· ${esc(fmtDate(c.at))} · ${esc(c.note)}</span>
        <div class="small">Feld <code>${esc(c.where)}</code> · gilt jetzt: <span class="conflict-val">${esc(conflictVal(c.kept))}</span>
          · hier war: <span class="conflict-val">${esc(conflictVal(c.local))}</span></div></div></li>`).join("");
}
$("#sync-conflicts-box").addEventListener("toggle", (e) => { if (e.target.open) refreshSyncConflicts(); });
$("#sync-result").addEventListener("click", (e) => {
  if (!e.target.closest("[data-sync-conflicts]")) return;
  e.preventDefault();
  $("#sync-conflicts-box").open = true;
  $("#sync-conflicts-box").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("#sync-conflicts-clear").addEventListener("click", async () => {
  try { await api("/api/sync/conflicts", { method: "DELETE" }); refreshSyncConflicts(); refreshSync({ quiet: true }); } catch (err) { fail(err); }
});

$("#sync-disconnect").addEventListener("click", async () => {
  if (!(await ask({ title: "Dieses Gerät trennen?", text: "Es meldet sich am Server ab und gleicht nicht mehr ab. Alle Daten hier bleiben erhalten; verbindest du es später wieder, geht es dort weiter.", ok: "Trennen", danger: true }))) return;
  try { syncInfo = await api("/api/sync/disconnect", { method: "POST" }); renderSync(); toast("Getrennt – die Daten auf diesem Gerät bleiben erhalten."); } catch (err) { fail(err); }
});

// keep the status fresh while the app is visible; coming back to the window syncs if it has been a while
setInterval(() => { if (document.visibilityState === "visible" && syncInfo?.connected) refreshSync({ quiet: true }); }, SYNC_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !syncInfo?.connected || syncInfo.revoked || !syncInfo.interval) return;
  if (!syncInfo.running && Date.now() / 1000 - (syncInfo.last?.at || 0) > 60) {
    api("/api/sync/run", { method: "POST" }).then((r) => { syncInfo = r; renderSync(); syncNews(r.run); }, () => {});
  }
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
  for (const k of ["autofill_path", "mpcfill_server", "cardback_path", "browser", "site", "upscaler_path", "upscale_model", "descreen", "image_generator_url"]) if (f[k]) f[k].value = appSettings[k] ?? "";
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
    dm.opts = null;  // the deskmat studio re-reads generator and upscaler
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
// collective print orders (Sammelbestellung): cards from several decks + tokens, printed like a deck
// ============================================================================================
let orderIndex = [];
async function refreshOrders() {
  orderIndex = await api("/api/orders").catch(() => orderIndex);
  $("#order-count").textContent = orderIndex.length || "";
  return orderIndex;
}
const orderMeta = (c) => `${c.cards} Karten · ${c.tokens} Tokens · ${c.slots} Druckplätze` + (c.slots ? ` · MPC-Staffel ${c.mpc_bracket}` : "");

async function showOrders(id) {
  $("#orders-list-view").hidden = !!id;
  $("#order-view").hidden = !id;
  if (!id) {
    currentOrder = null;
    await refreshOrders();
    $("#orders-empty").hidden = !!orderIndex.length;
    $("#orders-list").innerHTML = orderIndex.map((o) => `<a class="panel order-row" href="#/orders/${enc(o.id)}">
        <div><h3>${esc(o.name)}</h3><p class="muted small">${esc(orderMeta(o.counts))}${o.counts.sources.length ? " · " + esc(o.counts.sources.join(", ")) : ""}</p></div>
        <span class="muted small">${esc(fmtDate(o.updated))}</span></a>`).join("");
    return;
  }
  try { currentOrder = await api(`/api/orders/${enc(id)}`); } catch (err) { fail(err); go("#/orders"); return; }
  const decks = deckIndex.map((d) => `<option value="${esc(d.slug)}">${esc(d.name)}</option>`).join("");
  $("#oadd-deck").elements.deck.innerHTML = decks || '<option value="">– noch keine Decks –</option>';
  $("#oadd-token-deck").innerHTML = '<option value="">… oder Tokens eines Decks</option>' + decks;
  renderOrder();
  mountPrintStudio("#order-print-slot");
  if (printLoadedFor !== currentOrder.slug) { printLoadedFor = currentOrder.slug; refreshOrderPlan(true); }
}

function renderOrder() {
  const o = currentOrder;
  $("#order-name").textContent = o.name;
  $("#order-meta").textContent = orderMeta(o.counts);
  $("#order-total").textContent = o.counts.slots || "";
  $("#order-items-empty").hidden = !!o.items.length;
  $("#order-export").hidden = !o.items.some((i) => i.kind === "card");
  $("#order-export-file").href = `/api/orders/${enc(o.id)}/export?download=1`;
  const groups = new Map();
  for (const i of o.items) {
    if (!groups.has(i.source)) groups.set(i.source, []);
    groups.get(i.source).push(i);
  }
  $("#order-items").innerHTML = [...groups].map(([source, items]) => `<section class="order-group">
      <div class="order-group-head"><h3>${esc(source)} <span class="count">${items.reduce((n, i) => n + i.qty, 0)}</span></h3>
        <button type="button" class="link-btn" data-source="${esc(source)}">Gruppe entfernen</button></div>
      <ul class="order-items">${items.map((i) => `<li data-item="${esc(i.id)}">
        <input type="number" min="0" max="500" value="${i.qty}" aria-label="Anzahl ${esc(i.name)}">
        <span>${esc(i.name)}${i.kind === "token" ? ` <span class="muted">${esc(i.type_line || "Token")}</span>` : ""}</span>
        <button type="button" class="icon-btn" data-remove="${esc(i.id)}" aria-label="${esc(i.name)} entfernen" title="Entfernen">${icon("x")}</button></li>`).join("")}</ul></section>`).join("");
}

// the order's cards as a Moxfield list (also Archidekt, ManaBox …); tokens are left out
async function copyOrderList() {
  $("#order-menu").open = false;
  if (!currentOrder) return;
  try {
    const res = await fetch(`/api/orders/${enc(currentOrder.id)}/export`);
    if (!res.ok) throw new Error(errorText(res, await res.json().catch(() => ({}))));
    const text = await res.text();
    if (!text.trim()) { toast("Die Bestellung enthält noch keine Karten.", "error"); return; }
    const tokens = Number(res.headers.get("X-Tokens-Left-Out") || 0);
    await navigator.clipboard.writeText(text);
    toast(`Liste kopiert (${text.trim().split("\n").length} Zeilen) – in Moxfield unter „Import“ einfügen.`
      + (tokens ? ` ${tokens} ${tokens === 1 ? "Token-Position ist" : "Token-Positionen sind"} nicht dabei (Moxfield importiert keine Tokens).` : ""), "info", 7000);
  } catch (err) {
    fail(err.name === "NotAllowedError" ? new Error("Kopieren nicht möglich – lade die Liste als .txt herunter.") : err);
  }
}
$("#order-export-copy").addEventListener("click", copyOrderList);
$("#order-export-menu").addEventListener("click", copyOrderList);

// the print preview follows the order (MPC searches are cached, so this is cheap)
const refreshOrderPlan = (() => {
  const run = () => {
    if (!currentOrder || parseHash().view !== "orders") return;
    if (currentOrder.counts.slots) loadPlan();
    else { printPlan = null; $("#print-grid").innerHTML = ""; $("#print-summary").textContent = "Noch nichts zu drucken – füge oben Karten oder Tokens hinzu."; }
  };
  const later = debounce(run, 700);
  return (now = false) => (now ? run() : later());
})();

function orderChanged(o, msg) {
  currentOrder = o;
  renderOrder();
  refreshOrderPlan();
  refreshOrders();
  if (msg) toast(msg);
}

async function addToOrder(body, msg) {
  const r = await api(`/api/orders/${enc(currentOrder.id)}/items`, { method: "POST", body });
  orderChanged(r, msg ? msg(r) : `${r.added} hinzugefügt.`);
  return r;
}

$("#order-new").addEventListener("click", async () => {
  const name = await ask({ title: "Neue Sammelbestellung", text: "Wie soll sie heißen?", value: `Bestellung ${new Date().toLocaleDateString("de-DE")}`, ok: "Anlegen" });
  if (!name) return;
  try {
    const o = await api("/api/orders", { method: "POST", body: { name } });
    await refreshOrders();
    go(`#/orders/${enc(o.id)}`);
  } catch (err) { fail(err); }
});
$("#order-rename").addEventListener("click", async () => {
  $("#order-menu").open = false;
  const name = await ask({ title: "Umbenennen", value: currentOrder.name, ok: "Speichern" });
  if (!name) return;
  try { orderChanged(await api(`/api/orders/${enc(currentOrder.id)}`, { method: "PATCH", body: { name } })); } catch (err) { fail(err); }
});
$("#order-delete").addEventListener("click", async () => {
  $("#order-menu").open = false;
  if (!(await ask({ title: "Sammelbestellung löschen?", text: `„${currentOrder.name}“ und ihre Druckdateien werden gelöscht.`, ok: "Löschen", danger: true }))) return;
  try {
    await api(`/api/orders/${enc(currentOrder.id)}`, { method: "DELETE" });
    currentOrder = null;
    printLoadedFor = null;
    mountPrintStudio("#panel-drucken");
    go("#/orders");
  } catch (err) { fail(err); }
});

$("#order-add-kind").addEventListener("change", (e) => {
  for (const f of $$("#order-view .order-add")) f.hidden = f.dataset.kind !== e.target.value;
});
wireAutocomplete($("#oadd-card").elements.name, $("#ac-oadd"));
$("#oadd-card").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  try {
    await addToOrder({ items: [{ kind: "card", name: f.name.value.trim(), qty: Number(f.qty.value) || 1, source: "Einzelkarten" }] });
    e.target.reset();
    f.name.focus();
  } catch (err) { fail(err); }
});
$("#oadd-text").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements, url = f.url.value.trim(), text = f.text.value.trim();
  if (!url && !text) { toast("Füge eine Liste oder einen Deck-Link ein.", "error"); f.url.focus(); return; }
  const btn = e.target.querySelector("[type=submit]");
  btn.disabled = true;
  try {
    await addToOrder({ url: url || null, text: text || null }, (r) => `${r.added} ${r.added === 1 ? "Karte" : "Karten"} hinzugefügt.`);
    e.target.reset();
  } catch (err) { fail(err); }
  finally { btn.disabled = false; }
});
async function renderDeckPick() {
  const f = $("#oadd-deck").elements;
  const pick = f.which.value === "pick";
  $("#oadd-pick").hidden = !pick;
  if (!pick || !f.deck.value) return;
  try {
    const d = await api(`/api/decks/${enc(f.deck.value)}`);
    const names = [...d.commanders, ...d.cards.map((c) => c.name)];
    $("#oadd-pick").innerHTML = names.map((n) => `<label><input type="checkbox" value="${esc(n)}"> ${esc(n)}</label>`).join("");
  } catch (err) { fail(err); }
}
$("#oadd-deck").addEventListener("change", (e) => { if (e.target.name === "which" || e.target.name === "deck") renderDeckPick(); });
$("#oadd-deck").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target.elements;
  const body = { deck: f.deck.value };
  if (f.which.value === "missing") body.only_missing = true;
  if (f.which.value === "pick") {
    body.names = $$("#oadd-pick input:checked").map((c) => c.value);
    if (!body.names.length) { toast("Wähle mindestens eine Karte aus.", "error"); return; }
  }
  try { await addToOrder(body, (r) => `${r.added} Karten aus „${f.deck.selectedOptions[0].textContent}“ hinzugefügt.`); } catch (err) { fail(err); }
});

// tokens: Scryfall search or the tokens of a deck; one click adds "je N"
let tokenHits = [];
function renderTokenHits(items, msg) {
  tokenHits = items;
  $("#oadd-token-msg").textContent = msg;
  $("#oadd-token-grid").innerHTML = items.map((t, i) => `<button type="button" class="similar" data-t="${i}" title="${esc(t.type_line)}">
      ${t.image ? `<img src="${esc(t.image)}" alt="" loading="lazy">` : `<div class="noimg">${esc(t.name)}</div>`}
      <span class="sim-name">${esc(t.name)}</span><span class="muted">${esc(t.type_line || "")}${t.set_name ? " · " + esc(t.set_name) : ""}</span></button>`).join("");
}
$("#oadd-token-q").addEventListener("input", debounce(async (e) => {
  const q = e.target.value.trim();
  if (q.length < 2) return renderTokenHits([], "");
  $("#oadd-token-deck").value = "";
  try {
    const items = await api(`/api/tokens/search?q=${enc(q)}`);
    renderTokenHits(items, items.length ? "Klick fügt das Token in der gewählten Anzahl hinzu." : "Kein Token gefunden.");
  } catch (err) { renderTokenHits([], err.message); }
}, 300));
$("#oadd-token-deck").addEventListener("change", async (e) => {
  const slug = e.target.value;
  if (!slug) return renderTokenHits([], "");
  try {
    const items = await api(`/api/decks/${enc(slug)}/tokens`);
    const deckName = e.target.selectedOptions[0].textContent;
    renderTokenHits(items.map((t) => ({ ...t, source: `Tokens ${deckName}` })), items.length ? `Tokens, Embleme und Marker von „${deckName}“.` : "Dieses Deck erzeugt keine Tokens.");
  } catch (err) { renderTokenHits([], err.message); }
});
$("#oadd-token-grid").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-t]");
  if (!b) return;
  const t = tokenHits[Number(b.dataset.t)];
  const qty = Math.max(1, Number($("#oadd-token-qty").value) || 1);
  try {
    await addToOrder({ items: [{ kind: "token", name: t.name, qty, type_line: t.type_line, token_id: t.id || null, image: t.image || null, source: t.source || "Tokens" }] },
      () => `${qty}× ${t.name} hinzugefügt.`);
  } catch (err) { fail(err); }
});

$("#order-items").addEventListener("change", async (e) => {
  const li = e.target.closest("li[data-item]");
  if (!li || e.target.type !== "number") return;
  try { orderChanged(await api(`/api/orders/${enc(currentOrder.id)}/items/${enc(li.dataset.item)}`, { method: "PATCH", body: { qty: Number(e.target.value) || 0 } })); }
  catch (err) { fail(err); }
});
$("#order-items").addEventListener("click", async (e) => {
  const rm = e.target.closest("[data-remove]"), grp = e.target.closest("[data-source]");
  try {
    if (rm) orderChanged(await api(`/api/orders/${enc(currentOrder.id)}/items?item=${enc(rm.dataset.remove)}`, { method: "DELETE" }));
    if (grp && (await ask({ title: "Gruppe entfernen?", text: `Alle Positionen aus „${grp.dataset.source}“ entfernen.`, ok: "Entfernen", danger: true }))) {
      orderChanged(await api(`/api/orders/${enc(currentOrder.id)}/items?source=${enc(grp.dataset.source)}`, { method: "DELETE" }));
    }
  } catch (err) { fail(err); }
});

// "In eine Sammelbestellung packen?" – after a rebuild, from upgrades, a plan stage or the deck menu
function orderDialog({ title, text, body, missingToggle = false, qtyField = false }) {
  return refreshOrders().then(() => new Promise((resolve) => {
    const dlg = $("#order-dialog"), sel = $("#order-dialog-target");
    $("#order-dialog-title").textContent = title;
    $("#order-dialog-text").textContent = text;
    sel.innerHTML = orderIndex.map((o) => `<option value="${esc(o.id)}">${esc(o.name)} (${o.counts.slots} Druckplätze)</option>`).join("")
      + '<option value="">+ Neue Sammelbestellung …</option>';
    sel.value = orderIndex[0]?.id || "";
    $("#order-dialog-name").value = `Bestellung ${new Date().toLocaleDateString("de-DE")}`;
    const syncName = () => { $("#order-dialog-name-field").hidden = !!sel.value; };
    sel.onchange = syncName;
    syncName();
    $("#order-dialog-missing-row").hidden = !missingToggle;
    $("#order-dialog-qty-row").hidden = !qtyField;
    $("#order-dialog-qty").value = 1;
    $("#order-dialog-missing").checked = missingToggle && !!collSummary?.entries;
    const done = (r) => { dlg.onclose = null; if (dlg.open) dlg.close(); resolve(r); };
    $("#order-dialog-cancel").onclick = () => done(null);
    dlg.onclose = () => resolve(null);
    $("#order-dialog-form").onsubmit = async (e) => {
      e.preventDefault();
      try {
        let id = sel.value;
        if (!id) id = (await api("/api/orders", { method: "POST", body: { name: $("#order-dialog-name").value } })).id;
        const payload = { ...body, ...(missingToggle ? { only_missing: $("#order-dialog-missing").checked } : {}) };
        if (qtyField) {
          const qty = Math.max(1, Math.min(99, Math.round(Number($("#order-dialog-qty").value) || 1)));
          payload.items = payload.items.map((i) => ({ ...i, qty }));
        }
        const r = await api(`/api/orders/${enc(id)}/items`, { method: "POST", body: payload });
        refreshOrders();
        toast(`${r.added} ${r.added === 1 ? "Karte" : "Karten"} in „${r.name}“ – zu finden unter „Sammelbestellungen“.`);
        done(r);
      } catch (err) { fail(err); }
    };
    dlg.showModal();
  }));
}

// ---------- compare with another deck: what a rebuild needs beyond the old deck -> collective order ----------
let dcData = null;
async function openDeckCompare(other = null) {
  if (!currentDeck) return;
  $("#deck-menu").open = false;
  $("#dc-name").textContent = currentDeck.name;
  $("#dc-summary").textContent = "Vergleiche …";
  $("#dc-body").hidden = true;
  if (!$("#deck-compare").open) $("#deck-compare").showModal();
  try {
    const slug = currentDeck.slug;
    const r = await api(`/api/decks/${enc(slug)}/compare${other ? `?other=${enc(other)}` : ""}`);
    if (currentDeck?.slug !== slug) return;
    if (!r.other) { $("#dc-other").innerHTML = ""; $("#dc-summary").textContent = "Es gibt noch kein anderes Deck zum Vergleichen."; return; }
    dcData = r;
    $("#dc-other").innerHTML = r.candidates.map((c) => `<option value="${esc(c.slug)}" ${c.slug === r.other.slug ? "selected" : ""}>${esc(c.name)}`
      + ` – ${c.copied_from ? "Vorlage dieses Decks, " : ""}${c.common} gemeinsame Karten</option>`).join("");
    const hasColl = !!collSummary?.entries;
    $(`#dc-mode [value="${hasColl && r.totals.missing < r.totals.added ? "missing" : "all"}"]`).checked = true;
    $("#dc-mode").hidden = !hasColl;
    renderDeckCompare();
  } catch (err) { $("#dc-summary").textContent = err.message; }
}
const dcMode = () => ($("#dc-mode").hidden ? "all" : $("#dc-mode input:checked")?.value || "all");
const dcQty = (i) => (dcMode() === "missing" ? i.missing : i.qty);
function renderDeckCompare() {
  const r = dcData, cur = currentDeck?.currency || "eur";
  const n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  $("#dc-body").hidden = false;
  $("#dc-summary").innerHTML = `<b>${n(r.totals.added, "Karte", "Karten")}</b> kommen neu dazu`
    + (collSummary?.entries ? `, davon <b>${r.totals.missing}</b> nicht in deiner Sammlung` : "")
    + ` · ${n(r.totals.removed, "Karte wird", "Karten werden")} frei · ${r.totals.common} gemeinsam`;
  $("#dc-added-title").textContent = `Kommt neu dazu (${r.totals.added})`;
  $("#dc-added-hint").textContent = `Karten in „${r.deck.name}“, die „${r.other.name}“ nicht hat.`
    + (dcMode() === "missing" ? " Grau = hast du schon in deiner Sammlung." : "");
  $("#dc-added").innerHTML = r.added.map((i, k) => {
    const q = dcQty(i);
    const status = i.basic ? '<span class="pill">Standardland</span>'
      : i.missing === 0 ? `<span class="pill ok">in Sammlung${i.owned ? ` (${i.owned})` : ""}</span>`
      : collSummary?.entries ? '<span class="pill bad">fehlt</span>' : "";
    return `<li class="${q ? "" : "have"}"><input type="checkbox" data-k="${k}" ${q && !(i.basic && dcMode() === "all") ? "checked" : ""} ${q ? "" : "disabled"} aria-label="${esc(i.name)} bestellen">
      <span class="qty">${q || i.qty}×</span><button type="button" class="name" data-name="${esc(i.name)}" data-k="${k}">${esc(i.name)}${i.commander ? " (Commander)" : ""}</button>
      ${status}<span class="price">${i.price ? esc(fmtPrice(i.price, cur)) : ""}</span></li>`;
  }).join("") || '<li class="muted small">Keine – alle Karten stecken auch im anderen Deck.</li>';
  $("#dc-removed-title").textContent = `Wird frei (${r.totals.removed})`;
  $("#dc-removed-hint").textContent = `Karten aus „${r.other.name}“, die „${r.deck.name}“ nicht braucht.`;
  $("#dc-removed").className = "dc-list plain";
  $("#dc-removed").innerHTML = r.removed.map((i) => `<li><span class="qty">${i.qty}×</span><button type="button" class="name" data-name="${esc(i.name)}">${esc(i.name)}</button>
    <span class="price">${i.price ? esc(fmtPrice(i.price, cur)) : ""}</span></li>`).join("") || '<li class="muted small">Keine.</li>';
  $("#dc-common-title").textContent = `Gemeinsam (${r.totals.common})`;
  $("#dc-common").innerHTML = r.common.map((i) => esc(`${i.qty > 1 ? i.qty + "× " : ""}${i.name}`)).join("<br>");
  updateDcSelection();
}
function dcSelected() {
  return $$("#dc-added input[type=checkbox]:checked").map((b) => dcData.added[Number(b.dataset.k)]).map((i) => ({ ...i, order: dcQty(i) })).filter((i) => i.order > 0);
}
function updateDcSelection() {
  const sel = dcSelected();
  const copies = sel.reduce((a, i) => a + i.order, 0);
  $("#dc-order span").textContent = copies ? `${copies} ${copies === 1 ? "Karte" : "Karten"} zur Sammelbestellung …` : "Zur Sammelbestellung …";
  $("#dc-order").disabled = $("#dc-copy").disabled = !copies;
}
$("#dc-other").addEventListener("change", (e) => openDeckCompare(e.target.value));
$("#dc-mode").addEventListener("change", renderDeckCompare);
$("#dc-added").addEventListener("change", updateDcSelection);
$("#deck-compare").addEventListener("click", (e) => {
  const b = e.target.closest("button.name");
  if (!b) return;
  const i = [...(dcData?.added || []), ...(dcData?.removed || [])].find((x) => x.name === b.dataset.name) || {};
  showCardView(b.dataset.name, { image: i.image, image_back: i.image_back });
});
$("#dc-close").addEventListener("click", () => $("#deck-compare").close());
$("#dc-copy").addEventListener("click", async () => {
  const text = dcSelected().map((i) => `${i.order} ${i.name}`).join("\n");
  try { await navigator.clipboard.writeText(text); toast("Liste kopiert – z. B. für Moxfield, Cardmarket oder einen Laden."); }
  catch { toast("Kopieren nicht möglich – der Browser erlaubt keinen Zugriff auf die Zwischenablage.", "error"); }
});
$("#dc-order").addEventListener("click", async () => {
  const sel = dcSelected();
  if (!sel.length) return;
  const r = dcData;
  $("#deck-compare").close();
  const copies = sel.reduce((a, i) => a + i.order, 0);
  await orderDialog({ title: "Umbau bestellen",
    text: `${copies} ${copies === 1 ? "Karte" : "Karten"} aus „${r.deck.name}“, die „${r.other.name}“ nicht hat: `
      + sel.slice(0, 6).map((i) => (i.order > 1 ? `${i.order}× ${i.name}` : i.name)).join(", ") + (sel.length > 6 ? ` und ${sel.length - 6} weitere` : "") + ".",
    body: { items: sel.map((i) => ({ kind: "card", name: i.name, qty: i.order, source: `${r.deck.name} (statt ${r.other.name})`, source_slug: r.deck.slug })) } });
});
$("#compare-deck-btn").addEventListener("click", () => openDeckCompare());
$("#dc-open").addEventListener("click", () => openDeckCompare());

async function offerOrderAfterRebuild(slug, since) {
  if (!slug || !since) return;
  try {
    const r = await api(`/api/decks/${enc(slug)}/added?since=${since}`);
    if (!r.cards || r.version === since) return;
    const names = r.items.map((i) => (i.qty > 1 ? `${i.qty}× ${i.name}` : i.name));
    await orderDialog({
      title: "Neue Karten bestellen?",
      text: `Der Umbau (v${since} → v${r.version}) bringt ${r.cards} neue ${r.cards === 1 ? "Karte" : "Karten"}: `
        + names.slice(0, 8).join(", ") + (names.length > 8 ? ` und ${names.length - 8} weitere` : "") + ". In eine Sammelbestellung packen?",
      body: { deck: slug, since_version: since }, missingToggle: true,
    });
  } catch { /* the rebuild itself worked – the offer is optional */ }
}

$("#order-from-deck").addEventListener("click", () => {
  if (!currentDeck) return;
  $("#deck-menu").open = false;
  const total = currentDeck.cards.reduce((n, c) => n + (c.qty || 1), 0) + currentDeck.commanders.length;
  orderDialog({ title: "Deck drucken", text: `Karten aus „${currentDeck.name}“ (${total} insgesamt) in eine Sammelbestellung packen.`,
    body: { deck: currentDeck.slug }, missingToggle: true });
});
$("#upgrade-order").addEventListener("click", () => {
  const sel = selectedUpgrades();
  if (!sel.length || !currentDeck) return;
  orderDialog({ title: "Upgrades drucken", text: `${sel.length} neue Karten (${sel.map((u) => u.add).join(", ")}) drucken – ohne sie schon ins Deck zu übernehmen.`,
    body: { items: sel.map((u) => ({ kind: "card", name: u.add, qty: 1, source: `Upgrades ${currentDeck.name}`, source_slug: currentDeck.slug })) } });
});
refreshOrders();

// ============================================================================================
// deskmat studio: motif (card art, generated, upload) -> crop to the mat format -> print file at 300 or 600 DPI
// ============================================================================================
const dm = { opts: null, project: null, crop: { cx: 0.5, cy: 0.5, zoom: 1 }, printing: null, prefill: null, point: [0.5, 0.5] };
const dmPasses = () => Number($("#view-deskmat [name=dmpasses]:checked").value);

async function deskmatOptions() {
  if (dm.opts) return dm.opts;
  const o = await api("/api/deskmat/options");
  dm.opts = o;
  $("#dm-format").innerHTML = o.formats.map((f) => `<option value="${esc(f.key)}">${esc(f.label)}</option>`).join("");
  $("#dm-style").innerHTML = o.styles.map((st) => `<option value="${esc(st.key)}">${esc(st.label)}</option>`).join("");
  $("#dm-gen-host").textContent = o.generator;
  $("#dm-mpc-btn").hidden = !o.mpc;
  $("#dm-upscale").checked = o.upscaler;
  $("#dm-upscale").disabled = !o.upscaler;
  $("#dm-upscale-hint").hidden = o.upscaler;
  return o;
}

function setDmKind(kind) {
  $(`#dm-kind [value="${kind}"]`).checked = true;
  for (const b of $$("#view-deskmat .dm-block")) b.hidden = b.dataset.kind !== kind;
}
$("#dm-kind").addEventListener("change", (e) => setDmKind(e.target.value));

async function showDeskmat(id) {
  try { await deskmatOptions(); } catch (err) { return fail(err); }
  $("#dm-deck").innerHTML = '<option value="">–</option>' + deckIndex.map((d) => `<option value="${esc(d.slug)}">${esc(d.name)}</option>`).join("");
  if (dm.prefill) {
    setDmKind("card");
    $("#dm-card").value = dm.prefill.card || "";
    $("#dm-deck").value = dm.prefill.deck || "";
    dm.printing = null;
    $("#dm-art-choice").textContent = "Artwork: Standard-Druck";
    dm.prefill = null;
  }
  loadDeskmatList();
  if (!id) { dm.project = null; renderDeskmat(); return; }
  if (dm.project?.id !== id) {
    try { dm.project = await api(`/api/deskmat/${enc(id)}`); dm.crop = null; }
    catch (err) { fail(err); go("#/deskmat"); return; }
  }
  renderDeskmat();
}

function dmFormat() { return dm.opts.formats.find((f) => f.key === $("#dm-format").value) || dm.opts.formats[0]; }
const dmDpi = () => Number($("#view-deskmat [name=dmdpi]:checked").value);
const dmBleed = () => Number($("#dm-bleed").value);
// same maths as deskmat.target_size: the mat plus bleed on every side at the chosen DPI
function dmPrintMm() { const [w, h] = dmFormat().mm, b = dmBleed(); return [w + 2 * b, h + 2 * b]; }
function dmTarget() { return dmPrintMm().map((mm) => Math.round(mm / 25.4 * dmDpi())); }
const dmAspect = () => { const [w, h] = dmPrintMm(); return w / h; };
const dmFit = () => $("#view-deskmat [name=dmfit]:checked").value;

// same maths as deskmat.crop_box on the server
function dmCropBox(w, h, aspect, { cx, cy, zoom }) {
  let [ww, wh] = w / h > aspect ? [h * aspect, h] : [w, w / aspect];
  zoom = Math.max(1, Math.min(zoom, 8));
  ww /= zoom; wh /= zoom;
  const x0 = Math.min(Math.max(cx * w - ww / 2, 0), w - ww);
  const y0 = Math.min(Math.max(cy * h - wh / 2, 0), h - wh);
  return { x0, y0, ww, wh };
}
function dmDefaultCrop(p, aspect) {
  const box = p.source.art_box;
  if (!box) return { cx: 0.5, cy: 0.5, zoom: 1 };
  const [w, h] = p.source.size;
  const full = dmCropBox(w, h, aspect, { cx: 0.5, cy: 0.5, zoom: 1 });
  const winW = Math.min((box[2] - box[0]) * w, (box[3] - box[1]) * h * aspect);
  return { cx: (box[0] + box[2]) / 2, cy: (box[1] + box[3]) / 2, zoom: full.ww / winW };
}

function renderDeskmat() {
  const p = dm.project;
  const cands = p?.candidates || [];
  $("#dm-candidates").hidden = !cands.length;
  $("#dm-cand-grid").innerHTML = cands.map((c, i) => `<button type="button" data-n="${i}" aria-pressed="${p.source.chosen === i}"
      title="Variante ${i + 1}"><img src="/api/deskmat/${enc(p.id)}/image?kind=candidate&n=${i}" alt="Variante ${i + 1}" loading="lazy"></button>`).join("");
  if (cands.length) setDmKind("gen");
  const ready = !!p?.source?.file;
  $("#dm-edit-panel").hidden = !ready;
  $("#dm-result-panel").hidden = !p?.result;
  if (!ready) return;
  $("#dm-title").textContent = p.title + (p.source.card && !p.source.card.startsWith(p.title) ? ` · ${p.source.card}` : "")
    + (p.source.kind === "mpc" ? " · MPC-Scan" : p.source.kind === "card" ? " · Scryfall-Artwork" : "");
  const r = p.render;
  if (r && !dm.crop) {
    $("#dm-format").value = r.format;
    if (r.dpi) $(`#view-deskmat [name=dmdpi][value="${r.dpi}"]`).checked = true;
    $("#dm-bleed").value = String(r.bleed_mm ?? 0);
    $("#dm-filetype").value = r.filetype || "png";
    $(`#view-deskmat [name=dmpasses][value="${r.passes || 1}"]`).checked = true;
    $(`#view-deskmat [name=dmfit][value="${r.fit}"]`).checked = true;
    $("#dm-upscale").checked = r.upscale && dm.opts.upscaler;
  } else if (!r && p.format) $("#dm-format").value = p.format;
  if (!dm.crop) dm.crop = r?.crop && r.format === $("#dm-format").value ? { ...r.crop } : dmDefaultCrop(p, dmAspect());
  const src = `/api/deskmat/${enc(p.id)}/image?kind=source&v=${enc(`${p.source.file}-${p.source.chosen ?? ""}-${p.source.size}`)}`;
  if ($("#dm-img").dataset.src !== src) { $("#dm-img").src = src; $("#dm-bg").src = src; $("#dm-img").dataset.src = src; }
  layoutDeskmat();
  if (p.result) {
    const res = p.result;
    $("#dm-result").src = `/api/deskmat/${enc(p.id)}/image?kind=preview&v=${enc(res.created)}`;
    $("#dm-download").href = `/api/deskmat/${enc(p.id)}/image?kind=result&download=true`;
    const ext = res.file.split(".").pop().toUpperCase();
    $("#dm-result-info").textContent = `${res.size[0]} × ${res.size[1]} px · ${res.dpi} DPI · ${res.format_label}`
      + (res.bleed_mm ? ` + ${res.bleed_mm} mm Beschnitt (${res.print_mm[0] / 10} × ${res.print_mm[1] / 10} cm)` : "")
      + ` · ${fmtNum(res.bytes / 1048576, "", 1)} MB ${ext} · `
      + (res.ai_passes ? `${res.ai_passes}× mit Real-ESRGAN hochskaliert` : res.factor > 1.15 ? "ohne KI vergrößert" : "ohne Vergrößerung");
    $("#dm-result-warn").innerHTML = res.warnings.map((w) => `<li>${esc(w)}</li>`).join("");
    if (p.compare?.point && dm.point.join() === "0.5,0.5") dm.point = [...p.compare.point];
    placeDmMark();
    renderDmCompare();
  }
}

function layoutDeskmat() {
  const p = dm.project;
  if (!p?.source?.file || $("#dm-edit-panel").hidden) return;
  const f = dmFormat();
  const [pw, ph] = dmPrintMm();
  const aspect = pw / ph;
  const frame = $("#dm-frame");
  frame.style.aspectRatio = `${pw} / ${ph}`;
  const F = frame.clientWidth, H = F / aspect;
  const [w, h] = p.source.size;
  const fit = dmFit() === "fit";
  frame.classList.toggle("fit", fit);
  $("#dm-zoom").disabled = fit;
  const img = $("#dm-img");
  let s, left, top, region;
  if (fit) {
    s = Math.min(F / w, H / h);
    left = (F - w * s) / 2; top = (H - h * s) / 2;
    region = [w, h];
  } else {
    const b = dmCropBox(w, h, aspect, dm.crop);
    dm.crop.cx = (b.x0 + b.ww / 2) / w; dm.crop.cy = (b.y0 + b.wh / 2) / h;  // keep the centre inside
    s = F / b.ww; left = -b.x0 * s; top = -b.y0 * s;
    region = [Math.round(b.ww), Math.round(b.wh)];
  }
  Object.assign(img.style, { width: `${w * s}px`, height: `${h * s}px`, left: `${left}px`, top: `${top}px` });
  const bleed = dmBleed();
  const trim = $("#dm-trim");
  trim.hidden = !bleed;
  if (bleed) Object.assign(trim.style, { left: `${bleed / pw * 100}%`, right: `${bleed / pw * 100}%`, top: `${bleed / ph * 100}%`, bottom: `${bleed / ph * 100}%` });
  $("#dm-zoom").value = dm.crop.zoom;
  const [tw, th] = dmTarget();
  const mp = tw * th / 1e6;
  const tooBig = mp > dm.opts.max_megapixels;
  const factor = fit ? Math.min(tw / w, th / h) : tw / region[0];
  const aiOn = $("#dm-upscale").checked && dm.opts.upscaler;
  const passes = !aiOn || factor <= 1.15 ? 0 : factor > 4.5 && dmPasses() === 2 ? 2 : 1;
  $("#dm-passes").hidden = !dm.opts.upscaler;
  const soft = factor > [2.5, 6, 24][passes];
  const how = factor <= 1.15 ? "" : passes ? ` (KI ×4${passes === 2 ? " zweimal" : ""}${factor > (passes === 2 ? 16 : 4) ? ", Rest per Lanczos" : ""})` : " (ohne KI)";
  $("#dm-info").innerHTML = `Druckdatei <b>${tw} × ${th} px</b> (${fmtNum(mp, "", 0)} MP) · <b>${dmDpi()} DPI</b> auf ${fmtNum(pw / 10)} × ${fmtNum(ph / 10)} cm`
    + (bleed ? ` inkl. ${bleed} mm Beschnitt` : "") + ` · Ausschnitt ${region[0]} × ${region[1]} px → Faktor ${fmtNum(factor, "", 1)}${how}`
    + (tooBig ? ` · <span class="bad">zu groß (max. ${dm.opts.max_megapixels} MP) – 300 DPI oder kleineres Format</span>`
      : soft ? ' · <span class="warn">wird weich – größeres Motiv oder weniger Zoom</span>' : "")
    + (mp > 60 && $("#dm-filetype").value === "png" ? ' · <span class="muted">Tipp: JPEG spart hier viel Platz</span>' : "");
  $("#dm-render-btn").disabled = tooBig || !!currentJob;
}
window.addEventListener("resize", debounce(layoutDeskmat, 100));
$("#dm-format").addEventListener("change", () => { if (dm.project) dm.crop = dmDefaultCrop(dm.project, dmAspect()); layoutDeskmat(); });
$("#dm-bleed").addEventListener("change", () => { if (dm.project && !dm.project.render) dm.crop = dmDefaultCrop(dm.project, dmAspect()); layoutDeskmat(); });
$("#dm-filetype").addEventListener("change", layoutDeskmat);
$("#dm-upscale").addEventListener("change", layoutDeskmat);
$("#view-deskmat").addEventListener("change", (e) => { if (["dmfit", "dmdpi", "dmpasses"].includes(e.target.name)) layoutDeskmat(); });
$("#dm-zoom").addEventListener("input", (e) => { dm.crop.zoom = Number(e.target.value); layoutDeskmat(); });
$("#dm-reset").addEventListener("click", () => { dm.crop = dmDefaultCrop(dm.project, dmAspect()); layoutDeskmat(); });

// drag (mouse, touch, pen) and keyboard to move the crop window
let dmDrag = null;
$("#dm-frame").addEventListener("pointerdown", (e) => {
  if (dmFit() === "fit" || !dm.project) return;
  dmDrag = { x: e.clientX, y: e.clientY, crop: { ...dm.crop } };
  $("#dm-frame").setPointerCapture(e.pointerId);
  $("#dm-frame").classList.add("dragging");
});
$("#dm-frame").addEventListener("pointermove", (e) => {
  if (!dmDrag) return;
  const [w, h] = dm.project.source.size;
  const s = parseFloat($("#dm-img").style.width) / w;
  dm.crop.cx = dmDrag.crop.cx - (e.clientX - dmDrag.x) / (w * s);
  dm.crop.cy = dmDrag.crop.cy - (e.clientY - dmDrag.y) / (h * s);
  layoutDeskmat();
});
const dmDragEnd = () => { dmDrag = null; $("#dm-frame").classList.remove("dragging"); };
$("#dm-frame").addEventListener("pointerup", dmDragEnd);
$("#dm-frame").addEventListener("pointercancel", dmDragEnd);
$("#dm-frame").addEventListener("wheel", (e) => {
  if (dmFit() === "fit" || !dm.project) return;
  e.preventDefault();
  dm.crop.zoom = Math.max(1, Math.min(4, dm.crop.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08)));
  layoutDeskmat();
}, { passive: false });
$("#dm-frame").addEventListener("keydown", (e) => {
  if (dmFit() === "fit" || !dm.project) return;
  const step = 0.02 / dm.crop.zoom;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (moves[e.key]) { dm.crop.cx += moves[e.key][0]; dm.crop.cy += moves[e.key][1]; }
  else if (e.key === "+" || e.key === "=") dm.crop.zoom = Math.min(4, dm.crop.zoom * 1.1);
  else if (e.key === "-") dm.crop.zoom = Math.max(1, dm.crop.zoom / 1.1);
  else return;
  e.preventDefault();
  layoutDeskmat();
});

function openDeskmat(p) {
  dm.project = p;
  dm.crop = null;
  if (location.hash === `#/deskmat/${p.id}`) renderDeskmat();
  else go(`#/deskmat/${enc(p.id)}`);
  loadDeskmatList();
  setTimeout(() => $(p.source?.file ? "#dm-edit-panel" : "#dm-candidates").scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}
function onDeskmat(p, what) {
  if (what === "compare") {
    dm.project = p;
    renderDmCompare();
    $("#dm-compare").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (p.result) {
    dm.project = p;
    if (location.hash !== `#/deskmat/${p.id}`) go(`#/deskmat/${enc(p.id)}`); else renderDeskmat();
    loadDeskmatList();
    toast("Deine Deskmat ist fertig.");
    $("#dm-result-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  } else openDeskmat(p);
}

// 1a card artwork
wireAutocomplete($("#dm-card"), $("#ac-dm-card"));
$("#dm-card").addEventListener("input", () => { dm.printing = null; $("#dm-art-choice").textContent = "Artwork: Standard-Druck"; $("#dm-mpc-grid").hidden = true; });
$("#dm-art-btn").addEventListener("click", async () => {
  const name = $("#dm-card").value.trim();
  if (!name) { toast("Gib zuerst eine Karte ein.", "error"); $("#dm-card").focus(); return; }
  const p = await pickPrinting(name);
  if (!p) return;
  dm.printing = p;
  $("#dm-art-choice").textContent = `Artwork: ${p.set_name || p.set || ""}${p.collector_number ? " #" + p.collector_number : ""}`;
});
$("#dm-card-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { name: $("#dm-card").value.trim(), face: $("#dm-face").value, scryfall_id: dm.printing?.scryfall_id || null };
  const btn = e.submitter || $("#dm-card-form [type=submit]");
  btn.disabled = true;
  try { openDeskmat(await api("/api/deskmat/card", { method: "POST", body })); }
  catch (err) { fail(err); }
  finally { btn.disabled = false; }
});
$("#dm-mpc-btn").addEventListener("click", async () => {
  const name = $("#dm-card").value.trim();
  if (!name) { toast("Gib zuerst eine Karte ein.", "error"); return; }
  const grid = $("#dm-mpc-grid");
  grid.hidden = false;
  grid.innerHTML = '<p class="muted small">Suche Scans …</p>';
  try {
    const items = await api(`/api/deskmat/mpc?name=${enc(name)}`);
    grid.innerHTML = items.map((o) => `<button type="button" class="similar" data-mpc="${esc(o.id)}"><img src="${esc(o.thumb)}" alt="" loading="lazy">
      <span class="muted small">${esc(o.source || "")}${o.dpi ? ` · ${o.dpi} DPI` : ""}</span></button>`).join("") || '<p class="muted small">Keine Scans gefunden.</p>';
  } catch (err) { grid.innerHTML = `<p class="bad small">${esc(err.message)}</p>`; }
});
$("#dm-mpc-grid").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-mpc]");
  if (!b) return;
  try { openDeskmat(await api("/api/deskmat/card", { method: "POST", body: { name: $("#dm-card").value.trim(), mpc_id: b.dataset.mpc } })); }
  catch (err) { fail(err); }
});

// 1b generated from a described setting (Claude writes the prompt, a free generator paints)
$("#dm-gen-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (currentJob) { toast("Es läuft schon ein Auftrag – warte kurz oder brich ihn ab.", "error"); return; }
  const f = new FormData(e.target);
  const body = { setting: f.get("setting"), style: f.get("style"), deck: f.get("deck") || null, variants: Number(f.get("variants")), format: $("#dm-format").value || "playmat" };
  try {
    const { job } = await api("/api/deskmat/generate", { method: "POST", body });
    startJob(job, "Claude entwirft dein Motiv", { kind: "deskmat", slot: "#dm-job-slot", route: "#/deskmat" });
  } catch (err) { fail(err); }
});
$("#dm-cand-grid").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-n]");
  if (!b || !dm.project) return;
  try { openDeskmat(await api(`/api/deskmat/${enc(dm.project.id)}/choose`, { method: "POST", body: { n: Number(b.dataset.n) } })); }
  catch (err) { fail(err); }
});

// 1c upload
$("#dm-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const res = await fetch(`/api/deskmat/upload?filename=${enc(file.name)}`, { method: "POST", body: file });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    openDeskmat(data);
  } catch (err) { fail(err); }
  e.target.value = "";
});

// 3 render
$("#dm-render-btn").addEventListener("click", async () => {
  if (!dm.project || currentJob) return;
  const body = { format: $("#dm-format").value, dpi: dmDpi(), bleed_mm: dmBleed(), fit: dmFit(), crop: dm.crop,
    upscale: $("#dm-upscale").checked, filetype: $("#dm-filetype").value, passes: dmPasses() };
  try {
    const { job } = await api(`/api/deskmat/${enc(dm.project.id)}/render`, { method: "POST", body });
    startJob(job, `Deskmat „${dm.project.title}“ wird erstellt`, { kind: "deskmat", slot: "#dm-render-slot", route: `#/deskmat/${enc(dm.project.id)}` });
  } catch (err) { fail(err); }
});
$("#dm-open").addEventListener("click", async () => {
  try {
    const r = await api(`/api/deskmat/${enc(dm.project.id)}/open-folder`, { method: "POST" });
    toast(r.opened ? "Ordner geöffnet." : `Ordner: ${r.path}`);
  } catch (err) { fail(err); }
});

// checking before ordering: pick a spot, compare without / 1× / 2× AI, test print at real size
function placeDmMark() {
  const res = dm.project?.result;
  if (!res) return;
  const paperMm = (appSettings?.paper || "A4") === "Letter" ? [259, 190] : [270, 190];
  const [pw, ph] = res.print_mm;
  const fw = Math.min(1, paperMm[0] / pw), fh = Math.min(1, paperMm[1] / ph);
  const cx = Math.min(Math.max(dm.point[0], fw / 2), 1 - fw / 2), cy = Math.min(Math.max(dm.point[1], fh / 2), 1 - fh / 2);
  Object.assign($("#dm-mark").style, { left: `${(cx - fw / 2) * 100}%`, top: `${(cy - fh / 2) * 100}%`, width: `${fw * 100}%`, height: `${fh * 100}%` });
  $("#dm-testprint").href = `/api/deskmat/${enc(dm.project.id)}/testprint?x=${dm.point[0].toFixed(4)}&y=${dm.point[1].toFixed(4)}`;
}
$("#dm-result-wrap").addEventListener("click", (e) => {
  const r = $("#dm-result").getBoundingClientRect();
  dm.point = [Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1)];
  placeDmMark();
});
$("#dm-compare-btn").addEventListener("click", async () => {
  if (!dm.project || currentJob) return;
  try {
    const { job } = await api(`/api/deskmat/${enc(dm.project.id)}/compare`, { method: "POST", body: { x: dm.point[0], y: dm.point[1] } });
    startJob(job, "Vergleiche die Hochskalierung", { kind: "deskmat", slot: "#dm-compare-slot", route: `#/deskmat/${enc(dm.project.id)}` });
  } catch (err) { fail(err); }
});
function renderDmCompare() {
  const c = dm.project?.compare;
  $("#dm-compare").hidden = !c?.tiles?.length;
  if (!c?.tiles?.length) return;
  const labels = { 0: "Ohne KI (nur Lanczos)", 1: "1 KI-Durchgang", 2: "2 KI-Durchgänge" };
  const current = dm.project.render?.passes || 1;
  $("#dm-compare-info").textContent = `Ausschnitt ${c.tile_mm} × ${c.tile_mm} mm in Druckauflösung (Faktor ${String(c.factor).replace(".", ",")}).`
    + (c.tiles.some((t) => t.passes === 2) ? "" : " Ein zweiter Durchgang lohnt sich bei diesem Faktor nicht.");
  const v = enc(c.created);
  $("#dm-compare-grid").innerHTML = c.tiles.map((t) => `<figure>
      <div class="tile"><img src="/api/deskmat/${enc(dm.project.id)}/compare/${t.passes}?v=${v}" alt="${esc(labels[t.passes])}"></div>
      <figcaption><span>${esc(labels[t.passes])}${t.passes && t.passes === current && dm.project.result?.ai_passes ? " · aktuell" : ""}</span>
        ${t.passes ? `<button type="button" class="btn small" data-passes="${t.passes}">Damit erstellen</button>` : ""}</figcaption></figure>`).join("");
}
$("#dm-compare-real").addEventListener("change", (e) => $("#dm-compare-grid").classList.toggle("real", e.target.checked));
$("#dm-compare-grid").addEventListener("click", (e) => {
  const b = e.target.closest("[data-passes]");
  if (!b) return;
  $(`#view-deskmat [name=dmpasses][value="${b.dataset.passes}"]`).checked = true;
  $("#dm-upscale").checked = true;
  layoutDeskmat();
  $("#dm-render-btn").click();
});

async function loadDeskmatList() {
  let items = [];
  try { items = await api("/api/deskmats"); } catch { /* empty state */ }
  $("#dm-count").textContent = items.length || "";
  $("#dm-empty").hidden = !!items.length;
  $("#dm-list").innerHTML = items.map((p) => {
    const kind = p.result ? "preview" : p.source?.file ? "source" : p.candidates?.length ? "candidate&n=0" : "";
    const v = enc(p.updated || "");
    return `<div class="dm-item" data-id="${esc(p.id)}">
      <a class="thumb" href="#/deskmat/${enc(p.id)}">${kind ? `<img src="/api/deskmat/${enc(p.id)}/image?kind=${kind}&v=${v}" alt="" loading="lazy">` : ""}</a>
      <div class="meta"><span title="${esc(p.title)}">${esc(p.title)}<br><span class="muted">${p.result ? `${p.result.size[0]} × ${p.result.size[1]}` : "noch nicht erstellt"}</span></span>
        <button type="button" class="icon-btn dm-del" aria-label="Deskmat löschen" title="Löschen">${icon("x")}</button></div></div>`;
  }).join("");
}
$("#dm-list").addEventListener("click", async (e) => {
  const b = e.target.closest(".dm-del");
  if (!b) return;
  const id = b.closest(".dm-item").dataset.id;
  if (!(await ask({ title: "Deskmat löschen?", text: "Motiv und Druckdatei werden gelöscht.", ok: "Löschen", danger: true }))) return;
  try {
    await api(`/api/deskmat/${enc(id)}`, { method: "DELETE" });
    if (dm.project?.id === id) { dm.project = null; go("#/deskmat"); }
    loadDeskmatList();
  } catch (err) { fail(err); }
});
$("#deskmat-from-deck").addEventListener("click", () => {
  if (!currentDeck) return;
  $("#deck-menu").open = false;
  dm.prefill = { card: currentDeck.commanders[0], deck: currentDeck.slug };
  dm.project = null;
  go("#/deskmat");
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
// ============================================================================================
// „Frag Claude“: chat with the whole app (read-only Claude runs, conversations in decks/.chats)
// ============================================================================================
let chatIndex = [];
let currentChat = null;  // { id, title, messages } – null = a new, not yet started conversation
let chatRun = null;      // { job, chatId, question, source, status, error, answered, finished }

async function refreshChats() {
  chatIndex = await api("/api/chats").catch(() => chatIndex);
  renderChatList();
}

function renderChatList() {
  const cur = currentChat?.id;
  $("#chat-list").innerHTML = chatIndex.map((c) => `<li><a href="#/chat/${enc(c.id)}"${c.id === cur ? ' aria-current="page"' : ""}>
    ${esc(c.title || "Gespräch")}<span class="muted">${esc(fmtDate(c.updated))} · ${c.count} ${c.count === 1 ? "Frage" : "Fragen"}</span></a></li>`).join("");
  $("#chat-list-empty").hidden = chatIndex.length > 0;
}

const chatQuestion = (q, id = "") => `<div class="chat-msg user"${id ? ` id="${id}"` : ""}>${esc(q)}</div>`;
function chatAnswer(m) {
  const decks = Object.entries(m.decks || {});
  return `<article class="chat-msg ai" data-id="${esc(m.id)}">
    <div class="qa-a" tabindex="0" role="region" aria-label="Antwort von Claude">${md(m.answer, m.cards || {}, m.decks || {})}</div>
    <div class="chat-foot">${decks.map(([slug, d]) => `<a class="btn small ghost" href="#/deck/${enc(slug)}">${esc(d.name)} öffnen</a>`).join("")}
      <span class="meta">${esc(fmtDate(m.asked))}${m.deep ? " · gründlich" : ""}</span></div></article>`;
}

function renderChat() {
  const c = currentChat;
  $("#chat-title").textContent = c ? c.title : "Neues Gespräch";
  $("#chat-menu").hidden = !c?.messages?.length;
  $("#chat-log").innerHTML = (c?.messages || []).map((m) => chatQuestion(m.question) + chatAnswer(m)).join("");
  renderChatList();
  updateChatLive();
}

function updateChatLive() {
  const run = chatRun;
  const here = !!(run && currentChat && currentChat.id === run.chatId);
  $("#chat-pending")?.remove();
  if (here && !run.answered) $("#chat-log").insertAdjacentHTML("beforeend", chatQuestion(run.question, "chat-pending"));
  $("#chat-live").hidden = !here;
  $("#chat-live .spinner").hidden = !!run?.finished;
  $("#chat-status").textContent = run?.error ? "Fehler: " + run.error : run?.status || "Claude sieht sich deine Decks an …";
  $("#chat-cancel").textContent = run?.finished ? "Schließen" : "Abbrechen";
  const busy = !!(run && !run.finished);
  $("#chat-btn").disabled = busy || !aiState.available;
  $("#chat-btn").title = busy ? "Claude antwortet noch" : "";
  $("#chat-running").hidden = !busy;
  $("#chat-start").hidden = !!(currentChat?.messages?.length || here);
  $("#chat-nodecks").hidden = deckIndex.length > 0;
}

function onChatEvent(run, ev) {
  switch (ev.type) {
    case "tool": run.status = toolText(ev.name); break;
    case "status": run.status = ev.text; break;
    case "error": run.error = ev.text; break;
    case "answer": {
      run.answered = true;
      const here = currentChat?.id === ev.chat_id;
      const seen = here && parseHash().view === "chat";
      if (here) {  // keep the open conversation current, even while another page is shown
        currentChat.messages.push(ev.entry);
        $("#chat-pending")?.remove();
        $("#chat-log").insertAdjacentHTML("beforeend", chatQuestion(ev.entry.question) + chatAnswer(ev.entry));
        $("#chat-menu").hidden = false;
        if (seen) $("#chat-log").lastElementChild.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      if (!seen) toast(`Claude hat geantwortet: „${ev.entry.question.slice(0, 60)}“`, "info", 8000, { label: "Ansehen", run: () => go(`#/chat/${enc(ev.chat_id)}`) });
      refreshChats();
      break;
    }
    case "done":
      run.source.close();
      run.finished = true;
      if (ev.ok) chatRun = null;
      else {
        run.error ||= "Keine Antwort erhalten.";
        if (currentChat?.id === run.chatId && !currentChat.messages.length) {  // the empty new conversation was dropped
          currentChat = null;
          run.chatId = null;
          history.replaceState(null, "", "#/chat");
          const box = $("#chat-form textarea");
          if (!box.value) box.value = run.question;
          toast(run.error, "error");
          chatRun = null;
          renderChat();
        }
        refreshChats();
      }
      break;
  }
  updateChatLive();
}

async function showChat(id) {
  if (!id) currentChat = null;
  else if (currentChat?.id !== id) {
    try { currentChat = await api(`/api/chats/${enc(id)}`); }
    catch (err) { fail(err); currentChat = null; history.replaceState(null, "", "#/chat"); }
  }
  renderChat();
  refreshChats();
  if (matchMedia("(min-width: 901px)").matches && !(chatRun && !chatRun.finished)) $("#chat-form textarea").focus({ preventScroll: true });
}

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (chatRun && !chatRun.finished) return;
  const box = e.target.elements.question;
  const question = box.value.trim();
  if (question.length < 2) return;
  try {
    const r = await api("/api/chat", { method: "POST", body: { question, chat_id: currentChat?.id || null, deep: $("#chat-deep").checked } });
    if (!currentChat) {
      currentChat = { id: r.chat_id, title: r.title, messages: [] };
      history.replaceState(null, "", `#/chat/${enc(r.chat_id)}`);
      lastView = "chat" + r.chat_id;
    }
    const run = { job: r.job, chatId: r.chat_id, question };
    run.source = jobStream(r.job, (ev) => onChatEvent(run, ev), (lost) => {
      if (lost && !run.finished) { onChatEvent(run, { type: "error", text: lost }); onChatEvent(run, { type: "done", ok: false }); }
    });
    chatRun = run;
    box.value = "";
    renderChat();
    $("#chat-pending")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    refreshChats();
  } catch (err) { fail(err); }
});
$("#chat-form textarea").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#chat-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip || (chatRun && !chatRun.finished)) return;
  $("#chat-form textarea").value = chip.dataset.q;
  $("#chat-form").requestSubmit();
});
$("#chat-cancel").addEventListener("click", async () => {
  if (!chatRun) return;
  if (!chatRun.finished) await api(`/api/jobs/${chatRun.job}/cancel`, { method: "POST" }).catch(() => {});
  else { chatRun = null; updateChatLive(); }
});
$("#chat-new").addEventListener("click", () => {
  if (location.hash === "#/chat") { currentChat = null; renderChat(); } else go("#/chat");
  $("#chat-form textarea").focus();
});
$("#chat-log").addEventListener("click", (e) => {
  const ref = e.target.closest(".card-ref");
  if (ref) showCardView(ref.dataset.name, { image: ref.dataset.img, image_back: ref.dataset.imgBack, scryfall_uri: ref.dataset.uri });
});
$("#chat-rename").addEventListener("click", async () => {
  $("#chat-menu").open = false;
  if (!currentChat) return;
  const title = await ask({ title: "Gespräch umbenennen", value: currentChat.title, ok: "Speichern" });
  if (!title) return;
  try {
    const r = await api(`/api/chats/${enc(currentChat.id)}`, { method: "PUT", body: { title } });
    currentChat.title = r.title;
    $("#chat-title").textContent = r.title;
    refreshChats();
  } catch (err) { fail(err); }
});
$("#chat-copy").addEventListener("click", async () => {
  $("#chat-menu").open = false;
  if (!currentChat) return;
  const text = currentChat.messages.map((m) => `Frage: ${m.question}\n\n${m.answer.replace(/\{\{([a-z0-9-]+)\}\}/g, (_, sl) => (m.decks?.[sl]?.name || sl)).replace(/\[\[([^\]]+)\]\]/g, "$1")}`).join("\n\n---\n\n");
  try { await navigator.clipboard.writeText(text); toast("Gespräch kopiert."); }
  catch { toast("Kopieren nicht möglich – der Browser erlaubt keinen Zugriff auf die Zwischenablage.", "error"); }
});
$("#chat-delete").addEventListener("click", async () => {
  $("#chat-menu").open = false;
  if (!currentChat) return;
  if (!(await ask({ title: "Gespräch löschen?", text: `„${currentChat.title}“ mit allen Fragen und Antworten wird gelöscht.`, ok: "Löschen", danger: true }))) return;
  try {
    await api(`/api/chats/${enc(currentChat.id)}`, { method: "DELETE" });
    currentChat = null;
    toast("Gespräch gelöscht.");
    go("#/chat");
    refreshChats();
  } catch (err) { fail(err); }
});
refreshChats();

function paletteItems() {
  const items = [
    { label: "Neues Deck", hint: "Seite", run: () => { go("#/new"); setMode("build"); } },
    { label: "Frag Claude", hint: "Chat mit der ganzen App", run: () => go("#/chat") },
    { label: "Neues Gespräch mit Claude", hint: "Frag Claude", run: () => { go("#/chat"); $("#chat-new").click(); } },
    ...chatIndex.map((c) => ({ label: c.title, hint: "Gespräch", run: () => go(`#/chat/${enc(c.id)}`) })),
    { label: "Commander vorschlagen lassen", hint: "Neues Deck", run: () => { go("#/new"); setMode("find"); } },
    { label: "Stärkstes Deck gegen meine Runde bauen", hint: "Neues Deck", run: () => { go("#/new"); setMode("meta"); } },
    { label: "Deck per Link importieren", hint: "Moxfield, Archidekt, …", run: () => { go("#/new"); setMode("import"); setImportMode("link"); $("#import-url").focus(); } },
    { label: "Deckliste einfügen", hint: "Neues Deck", run: () => { go("#/new"); setMode("import"); setImportMode("text"); $("#import-text").focus(); } },
    { label: "Starterdeck (Precon) importieren", hint: "Neues Deck", run: () => { go("#/new"); setMode("import"); setImportMode("precon"); } },
    { label: "Meine Sammlung", hint: "Seite", run: () => go("#/collection") },
    { label: "Karte zur Sammlung hinzufügen", hint: "Sammlung", run: () => { go("#/collection"); openCollAdd(); } },
    { label: "Sammlung importieren", hint: "Sammlung", run: () => { go("#/collection"); $("#coll-import-btn").click(); } },
    { label: "Sammelbestellungen", hint: "Seite", run: () => go("#/orders") },
    { label: "Neue Sammelbestellung", hint: "Sammelbestellungen", run: () => { go("#/orders"); $("#order-new").click(); } },
    { label: "Deskmat-Studio", hint: "Seite", run: () => go("#/deskmat") },
    { label: "Glossar", hint: "Seite", run: () => go("#/glossary") },
    { label: "Gegnerdecks", hint: "Seite", run: () => go("#/opponents") },
    ...oppIndex.map((o) => ({ label: o.title, hint: "Gegnerdeck", run: () => go(`#/opponents/${enc(o.id)}`) })),
    { label: "Tischregeln", hint: "Seite", run: () => go("#/tables") },
    ...tableSets.map((t) => ({ label: t.name, hint: "Tischregel", run: () => go(`#/tables/${enc(t.id)}`) })),
    { label: "Blacklist", hint: "Seite", run: () => go("#/blacklist") },
    { label: "Einstellungen", hint: "Seite", run: () => go("#/settings") },
  ];
  if (currentDeck && parseHash().view === "deck") {
    const d = currentDeck;
    const tabNames = { karten: "Karten", anleitung: "Anleitung & Rule 0", testen: "Testhand & Wahrscheinlichkeiten", anpassen: "Anpassen & Upgrades", fragen: "Fragen zum Deck", partien: "Partien & Bilanz", verlauf: "Verlauf", drucken: "Drucken" };
    for (const [tab, label] of Object.entries(tabNames)) items.push({ label, hint: d.name, run: () => selectTab(tab) });
    items.push({ label: "Karten bearbeiten", hint: d.name, run: () => { selectTab("karten"); if (!edit) setEditing(true); } });
    items.push({ label: "Liste kopieren", hint: d.name, run: () => $("#copy-btn").click() });
    items.push({ label: "Mit anderem Deck vergleichen", hint: d.name, run: () => openDeckCompare() });
    items.push({ label: "Deskmat aus diesem Deck", hint: d.name, run: () => $("#deskmat-from-deck").click() });
    items.push({ label: "Partie festhalten", hint: d.name, run: () => { selectTab("partien"); $("#game-form input[name=result]").focus(); } });
  }
  for (const d of deckIndex) items.push({ label: d.name, hint: `Deck · ${d.commanders.join(" + ")} · ${d.level || ""}`, run: () => go(`#/deck/${enc(d.slug)}`) });
  for (const o of orderIndex) items.push({ label: o.name, hint: `Sammelbestellung · ${o.counts.slots} Druckplätze`, run: () => go(`#/orders/${enc(o.id)}`) });
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
wireAutocomplete($("#tr-input"), $("#ac-tr"));
wireAutocomplete($("#opp-new [name=commander]"), $("#ac-opp-new"));
wireAutocomplete(buildForm.elements.meta_commander, $("#ac-meta-commander"));
wireAutocomplete($("#opp-form [name=commander]"), $("#ac-opp-edit"));
wireAutocomplete($("#opp-form [name=partner]"), $("#ac-opp-partner"));
setMode("build");
refreshBlacklist();
loadSettings().catch((err) => console.error(err));
Promise.all([initBrackets(), refreshDeckList(), refreshCollectionSummary(), refreshTableRules(), refreshOpponents(), refreshAi(true)]).then(route, (err) => { fail(err); route(); });
refreshSync({ quiet: true });
