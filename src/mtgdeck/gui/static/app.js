"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let brackets = [];
const ROLE_LABELS = { ramp: "Ramp", card_draw: "Kartenzug", removal: "Removal", board_wipe: "Board Wipes",
  tutor: "Tutoren", extra_turn: "Extra Turns", counterspell: "Counter", protection: "Schutz" };
let currentDeck = null;
let currentJob = null;
let eventSource = null;

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

// ---------- form: brackets, autocomplete, preview ----------
async function initBrackets() {
  brackets = await api("/api/brackets");
  const box = $("#bracket-options");
  box.innerHTML = brackets.map((b) => `
    <label title="${esc(b.name)}"><input type="radio" name="bracket" value="${b.number}" ${b.number === 3 ? "checked" : ""}>
    <span>${b.number}</span></label>`).join("");
  box.addEventListener("change", showBracketDesc);
  showBracketDesc();
  $("#retune-brackets").innerHTML = brackets.map((b) => `
    <label title="${esc(b.name)}"><input type="radio" name="rbracket" value="${b.number}"><span>${b.number}</span></label>`).join("");
}

// ---------- power profile (sub-tier, house rules, style) ----------
const TIER_LABELS = { low: "unteres", mid: "mittleres", high: "oberes" };
const TIER_ORDER = ["low", "mid", "high"];
const TIER_CENTER = { low: 0.17, mid: 0.5, high: 0.83 };

document.querySelectorAll(".profile-fields").forEach((el) => el.appendChild($("#profile-template").content.cloneNode(true)));

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
       <span class="hint">(${esc(power.components.map((c) => `${c.reason} ${c.points > 0 ? "+" : ""}${c.points}`).join(", "))}${power.floor_from_rules ? ` – durch die Regeln mindestens Bracket ${power.floor_from_rules}` : ""})</span><br>`
    : "Noch keine Power-Einschätzung – „Neu prüfen“ drücken.<br>")
    + `<span class="legend-dot"></span> Ziel: <b>${esc(levelText(bracket, tier))}</b>${changed ? " – mit „Deck umbauen“ übernehmen" : ""}`;
}

$("#retune-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck) return;
  const { bracket } = retuneTarget();
  const profile = readProfile($("#retune-form .profile-fields")) || {};
  const request = new FormData(e.target).get("request") || null;
  try {
    const { job } = await api("/api/retune", { method: "POST", body: { slug: currentDeck.slug, bracket, profile, request } });
    startJob(job, `Claude stimmt ${currentDeck.name} ab: ${levelText(bracket, profile.tier)} …`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) { alert(err.message); }
});

const fmtDate = (iso) => (iso || "").replace("T", " ").slice(0, 16);
const fmtNum = (v, suffix = "", digits = null) =>
  v === null || v === undefined ? "–" : `${digits === null ? v : Number(v).toFixed(digits)}${suffix}`;
const fmtPower = (v) => fmtNum(v, "", 1);
const fmtPrice = (v, cur = "") => fmtNum(v, cur ? " " + cur : "", 2);
let versions = [];

async function renderHistory(d) {
  versions = await api(`/api/decks/${encodeURIComponent(d.slug)}/versions`).catch(() => []);
  $("#history-panel").classList.toggle("hidden", !versions.length);
  $("#compare-result").classList.add("hidden");
  const cur = (d.currency || "eur").toUpperCase();
  $("#history").innerHTML = versions.slice().reverse().map((h) => `<li>
    <div class="vhead">
      <span class="vnum">${h.version ? "v" + h.version : "–"}</span>
      ${h.current ? '<span class="badge ok">aktuell</span>' : ""}
      <span class="when">${esc(fmtDate(h.at))}</span>
      <span class="hint">${h.from && h.from !== h.to ? `${esc(h.from)} → ` : ""}${esc(h.to || "")}
        · ${esc(fmtPrice(h.price, cur))} · Power ${esc(fmtPower(h.power))}</span>
    </div>
    ${h.note ? `<div>${esc(h.note)}</div>` : ""}
    ${h.added?.length ? `<div class="plus">+ ${esc(h.added.join(", "))}</div>` : ""}
    ${h.removed?.length ? `<div class="minus">− ${esc(h.removed.join(", "))}</div>` : ""}
    ${h.version ? `<div class="vactions">
      ${!h.current ? `<button class="secondary" data-act="diff" data-v="${h.version}">Diff zu aktuell</button>` : ""}
      ${h.restorable && !h.current ? `<button class="secondary" data-act="restore" data-v="${h.version}">Wiederherstellen</button>` : ""}
      ${h.restorable ? `<button class="secondary" data-act="copy" data-v="${h.version}">Als neues Deck</button>` : ""}
      ${h.restorable ? `<button class="secondary" data-act="text" data-v="${h.version}">Liste kopieren</button>` : ""}
    </div>` : ""}
  </li>`).join("");
  const opts = versions.filter((h) => h.restorable).map((h) => `<option value="${h.version}">v${h.version}${h.current ? " (aktuell)" : ""}</option>`).join("");
  $("#compare-form").elements.a.innerHTML = opts;
  $("#compare-form").elements.b.innerHTML = opts;
  const n = versions.filter((h) => h.restorable).length;
  if (n > 1) $("#compare-form").elements.a.selectedIndex = n - 2;
  $("#compare-form").elements.b.selectedIndex = n - 1;
  $("#compare-form").classList.toggle("hidden", n < 2);
}

async function showDiff(a, b) {
  const r = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/diff?a=${a}${b ? `&b=${b}` : ""}`);
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
  box.classList.remove("hidden");
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("#compare-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target.elements;
  showDiff(f.a.value, f.b.value).catch((err) => alert(err.message));
});

async function copyAsDeck(version) {
  const suggestion = `${currentDeck.name} (Kopie${version ? " v" + version : ""})`;
  const name = prompt("Name des neuen Decks:", suggestion);
  if (!name) return;
  const r = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/copy`, { method: "POST", body: { name, version: version || null } });
  await refreshDeckList();
  openDeck(r.slug);
}

$("#history").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || !currentDeck) return;
  const v = Number(btn.dataset.v);
  try {
    if (btn.dataset.act === "diff") await showDiff(v);
    if (btn.dataset.act === "copy") await copyAsDeck(v);
    if (btn.dataset.act === "text") {
      const snap = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/versions/${v}`);
      await navigator.clipboard.writeText(snap.export_text);
      btn.textContent = "Kopiert ✓";
    }
    if (btn.dataset.act === "restore" && confirm(`Version ${v} wiederherstellen? Sie wird als neue Version gespeichert – nichts geht verloren.`)) {
      await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/versions/${v}/restore`, { method: "POST" });
      await openDeck(currentDeck.slug);
    }
  } catch (err) { alert(err.message); }
});

$("#duplicate-btn").addEventListener("click", () => currentDeck && copyAsDeck(null).catch((err) => alert(err.message)));
function showBracketDesc() {
  const n = Number(new FormData($("#build-form")).get("bracket"));
  const b = brackets.find((x) => x.number === n);
  $("#bracket-desc").innerHTML = b ? `<b>${esc(b.name)}</b> – ${esc(b.summary)}` : "";
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function wireAutocomplete(input, list, onPick) {
  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) return;
    try {
      const names = await api(`/api/autocomplete?q=${encodeURIComponent(q)}`);
      list.innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");
    } catch { /* ignore */ }
  }, 200));
  input.addEventListener("change", () => onPick && onPick(input.value));
}

async function previewCommander(name) {
  const box = $("#commander-preview");
  if (!name) { box.innerHTML = ""; return; }
  try {
    const c = await api(`/api/card?name=${encodeURIComponent(name)}`);
    box.innerHTML = c.image ? `<img src="${esc(c.image)}" alt="${esc(c.name)}">` : "";
    if (c.name && c.name !== name) $("#commander").value = c.name;
  } catch { box.innerHTML = ""; }
}

// ---------- jobs ----------
function startJob(jobId, title) {
  currentJob = jobId;
  $("#welcome").classList.add("hidden");
  $("#job").classList.remove("hidden");
  $("#job-title").textContent = title;
  $("#log").innerHTML = "";
  $("#build-btn").disabled = true;
  $("#cancel-btn").disabled = false;
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/jobs/${jobId}/events`);
  eventSource.onmessage = (e) => handleEvent(JSON.parse(e.data));
  eventSource.onerror = () => { /* browser retries automatically; server replays from start */ };
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
    case "text": logLine("text", ev.text); break;
    case "tool": logLine("tool", `→ ${ev.name} ${ev.summary || ""}`); break;
    case "status": logLine("tool", ev.text); break;
    case "error": logLine("error", "Fehler: " + ev.text); break;
    case "result": logLine("result", ev.text); break;
    case "suggestions": renderSuggestions(ev.items); break;
    case "done":
      eventSource.close();
      $("#build-btn").disabled = false;
      $("#finder-btn").disabled = false;
      $("#cancel-btn").disabled = true;
      $("#job-title").textContent = ev.ok ? "Fertig" : "Beendet";
      refreshDeckList().then(() => { if (ev.deck) openDeck(ev.deck); });
      break;
  }
}

function buildSettings() {
  const f = Object.fromEntries(new FormData($("#build-form")));
  const proxy = $("#proxy").checked;
  return {
    bracket: Number(f.bracket), currency: f.currency, proxy,
    budget: !proxy && f.budget ? Number(f.budget) : null, model: f.model || null,
  };
}

$("#proxy").addEventListener("change", () => {
  const on = $("#proxy").checked;
  $("#budget").disabled = on;
  $("#budget").placeholder = on ? "egal (Proxy)" : "unbegrenzt";
});

$("#build-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const body = {
    ...buildSettings(), commander: f.commander, partner: f.partner || null,
    strategy: f.strategy || null, notes: f.notes || null,
    profile: readProfile($("#build-profile .profile-fields")),
  };
  try {
    const { job } = await api("/api/build", { method: "POST", body });
    startJob(job, `Claude baut ${body.commander} (Bracket ${body.bracket}) …`);
  } catch (err) { alert(err.message); }
});

// ---------- commander finder ----------
$("#finder-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const body = { ...buildSettings(), prompt: f.prompt, count: Number(f.count) };
  try {
    const { job } = await api("/api/find-commander", { method: "POST", body });
    $("#finder-btn").disabled = true;
    $("#suggestions").classList.add("hidden");
    startJob(job, "Claude sucht passende Commander …");
  } catch (err) { alert(err.message); }
});

const COLOR_NAMES = { W: "Weiß", U: "Blau", B: "Schwarz", R: "Rot", G: "Grün" };
let suggestions = [];

function renderSuggestions(items) {
  suggestions = items || [];
  $("#welcome").classList.add("hidden");
  $("#deck-view").classList.add("hidden");
  $("#suggestions").classList.remove("hidden");
  const priceKey = new FormData($("#build-form")).get("currency") === "usd" ? "price_usd" : "price_eur";
  $("#suggestion-list").innerHTML = suggestions.map((s, i) => {
    const colors = (s.color_identity || []).map((c) => COLOR_NAMES[c] || c).join(", ") || "Farblos";
    return `<div class="suggestion">
      ${s.image ? `<img src="${esc(s.image)}" alt="${esc(s.name)}" loading="lazy">` : ""}
      <h4>${esc(s.name)}${s.partner ? " + " + esc(s.partner) : ""}</h4>
      <p class="hint">${esc(s.archetype || "")} · ${esc(colors)}${s[priceKey] ? " · " + esc(s[priceKey]) : ""}</p>
      <p>${esc(s.why || "")}</p>
      ${s.bracket_fit ? `<p class="hint">${esc(s.bracket_fit)}</p>` : ""}
      <div class="actions">
        <button class="secondary" data-use="${i}">Übernehmen</button>
        <button data-build="${i}">Deck bauen</button>
      </div>
    </div>`;
  }).join("");
}

$("#suggestion-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-use], button[data-build]");
  if (!btn) return;
  const s = suggestions[Number(btn.dataset.use ?? btn.dataset.build)];
  $("#commander").value = s.name;
  $("#partner").value = s.partner || "";
  const strategy = $("#build-form").elements.strategy;
  if (s.strategy && !strategy.value) strategy.value = s.strategy;
  previewCommander(s.name);
  if (btn.dataset.build !== undefined) $("#build-form").requestSubmit();
  else $("#commander").scrollIntoView({ behavior: "smooth", block: "center" });
});

// ---------- blacklist ----------
async function refreshBlacklist() {
  const names = await api("/api/blacklist");
  $("#bl-count").textContent = names.length ? `(${names.length})` : "";
  $("#bl-list").innerHTML = names.map((n) => `<li>${esc(n)}<button title="Entfernen" data-name="${esc(n)}">×</button></li>`).join("");
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
  await api("/api/blacklist", { method: "POST", body: { remove: [btn.dataset.name] } });
  refreshBlacklist();
});

$("#refine-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentDeck) return;
  const request = new FormData(e.target).get("request");
  try {
    const { job } = await api("/api/refine", { method: "POST", body: { slug: currentDeck.slug, request } });
    e.target.reset();
    startJob(job, `Claude überarbeitet ${currentDeck.name} …`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) { alert(err.message); }
});

$("#cancel-btn").addEventListener("click", async () => {
  if (currentJob) await api(`/api/jobs/${currentJob}/cancel`, { method: "POST" }).catch(() => {});
});

// ---------- decks ----------
async function refreshDeckList() {
  const decks = await api("/api/decks");
  $("#deck-list").innerHTML = decks.length ? decks.map((d) => `
    <li data-slug="${esc(d.slug)}" class="${currentDeck && currentDeck.slug === d.slug ? "active" : ""}">
      <div>${esc(d.name)}</div>
      <div class="meta">${esc(d.commanders.join(" + "))} · ${esc(d.level || `Bracket ${d.bracket ?? "?"}`)}${d.proxy ? " · Proxy" : ""}
        ${d.valid === true ? '<span class="ok">✓</span>' : d.valid === false ? '<span class="bad">✗</span>' : ""}</div>
    </li>`).join("") : '<li class="hint">Noch keine Decks.</li>';
}
$("#deck-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-slug]");
  if (li) openDeck(li.dataset.slug);
});

async function openDeck(slug) {
  const d = await api(`/api/decks/${encodeURIComponent(slug)}`);
  currentDeck = d;
  $("#welcome").classList.add("hidden");
  $("#deck-view").classList.remove("hidden");
  $("#deck-name").textContent = d.name;
  $("#suggestions").classList.add("hidden");
  const money = d.proxy ? " · Proxy-Deck" : d.budget ? ` · Budget ${d.budget} ${(d.currency || "eur").toUpperCase()}` : "";
  const level = levelText(d.bracket ?? "?", d.power_profile?.tier);
  const style = d.power_profile?.style ? ` · Stil: ${d.power_profile.style}` : "";
  $("#deck-meta").textContent = `${d.commanders.join(" + ")} · ${level}${style}${money} · aktualisiert ${d.updated ?? ""}`;
  $("#deck-desc").textContent = d.description || "";
  renderValidation(d.validation);
  renderStats(d.validation?.stats, d.validation);
  renderCards(d);
  $("#retune-form").querySelector(`[name="rbracket"][value="${d.bracket || 3}"]`).checked = true;
  setProfile($("#retune-form .profile-fields"), d.power_profile);
  $("#retune-form").elements.request.value = "";
  renderPower(d);
  renderHistory(d);
  refreshDeckList();
}

function renderValidation(v) {
  const box = $("#validation");
  if (!v) { box.innerHTML = '<p class="hint">Noch nicht geprüft.</p>'; return; }
  const status = v.legal ? '<span class="badge ok">legal</span>' : '<span class="badge bad">nicht legal</span>';
  const br = v.bracket || {};
  const goal = br.target_text || `Bracket ${br.target}`;
  const brStatus = br.compliant ? `<span class="badge ok">passt zu ${esc(goal)}</span>`
    : `<span class="badge warn">${esc(goal)} verletzt</span>`;
  const list = (items, cls) => items && items.length ? `<ul class="issues ${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "";
  box.innerHTML = `
    <p>${status} ${brStatus} ${br.estimated ? `<span class="hint">geschätzt: Bracket ${esc(br.estimated)}</span>` : ""}</p>
    ${list(v.errors, "bad")}${list(br.violations, "warn")}${list(v.warnings, "warn")}
    <dl class="kv">
      <dt>Game Changers</dt><dd>${esc((br.game_changers || []).join(", ") || "–")}</dd>
      <dt>2-Karten-Combos</dt><dd>${esc((br.two_card_combos || []).map((c) => c.cards.join(" + ")).join("; ") || "–")}</dd>
      <dt>Extra Turns</dt><dd>${esc((br.extra_turns || []).join(", ") || "–")}</dd>
      <dt>Mass Land Denial</dt><dd>${esc((br.mass_land_denial || []).join(", ") || "–")}</dd>
      <dt>Tutoren</dt><dd>${esc((br.tutors || []).join(", ") || "–")}</dd>
    </dl>`;
}

function renderStats(s, v = {}) {
  const box = $("#stats");
  if (!s) { box.innerHTML = ""; return; }
  const curve = s.mana_curve || {};
  const max = Math.max(1, ...Object.values(curve));
  const keys = ["0", "1", "2", "3", "4", "5", "6", "7+"];
  const priceKey = Object.keys(s).find((k) => k.startsWith("total_price_"));
  const cur = priceKey ? priceKey.replace("total_price_", "").toUpperCase() : "";
  box.innerHTML = `
    <div class="curve">${keys.map((k) => `<div><span>${curve[k] || 0}</span><div class="bar" style="height:${((curve[k] || 0) / max) * 80}%"></div><span>${k}</span></div>`).join("")}</div>
    <dl class="kv">
      <dt>Karten</dt><dd>${s.card_count}</dd>
      <dt>Ø Manawert</dt><dd>${s.avg_cmc_nonland}</dd>
      <dt>Typen</dt><dd>${esc(Object.entries(s.types || {}).map(([k, v]) => `${k} ${v}`).join(" · "))}</dd>
      <dt>Rollen</dt><dd>${esc(Object.entries(s.role_counts || {}).map(([k, v]) => `${ROLE_LABELS[k] || k} ${v}`).join(" · "))}</dd>
      <dt>Preis</dt><dd>${priceKey ? `${v.price_total ?? s[priceKey]} ${cur}` : "–"}${v.proxy ? ' <span class="badge proxy">Proxy</span>' : v.budget ? ` <span class="hint">/ Budget ${v.budget}</span>` : ""}</dd>
    </dl>`;
}

function renderCards(d) {
  const data = d.card_data || {};
  const groups = {};
  const typeOf = (name) => {
    const t = (data[name]?.type_line || "").split("//")[0];
    for (const k of ["Land", "Creature", "Planeswalker", "Battle", "Artifact", "Enchantment", "Instant", "Sorcery"]) if (t.includes(k)) return k;
    return "Sonstiges";
  };
  groups["Commander"] = d.commanders.map((n) => ({ name: n, qty: 1 }));
  for (const c of d.cards) {
    const g = c.category || typeOf(c.name);
    (groups[g] ||= []).push(c);
  }
  const priceKey = d.currency === "usd" ? "price_usd" : "price_eur";
  $("#cards").innerHTML = Object.entries(groups).map(([g, cards]) => `
    <div class="group"><h4>${esc(g)} <span class="count">(${cards.reduce((a, c) => a + (c.qty || 1), 0)})</span></h4>
    ${cards.sort((a, b) => a.name.localeCompare(b.name)).map((c) => {
      const cd = data[c.name] || {};
      return `<div class="card" data-img="${esc(cd.image || "")}">
        <span>${c.qty > 1 ? c.qty + "× " : ""}${esc(c.name)}${cd.game_changer ? '<span class="gc">GC</span>' : ""}</span>
        <span class="price">${cd[priceKey] ? cd[priceKey] : ""}</span></div>`;
    }).join("")}</div>`).join("");
}

// hover preview of card images
const preview = $("#preview");
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest(".card[data-img]");
  if (el && el.dataset.img) { preview.querySelector("img").src = el.dataset.img; preview.classList.remove("hidden"); }
});
document.addEventListener("mouseout", (e) => { if (e.target.closest(".card[data-img]")) preview.classList.add("hidden"); });
document.addEventListener("mousemove", (e) => {
  const x = e.clientX + 260 > window.innerWidth ? e.clientX - 260 : e.clientX + 20;
  const y = Math.min(e.clientY - 40, window.innerHeight - 350);
  preview.style.left = x + "px"; preview.style.top = Math.max(8, y) + "px";
});

$("#copy-btn").addEventListener("click", async () => {
  if (!currentDeck) return;
  await navigator.clipboard.writeText(currentDeck.export_text);
  $("#copy-btn").textContent = "Kopiert ✓";
  setTimeout(() => ($("#copy-btn").textContent = "Liste kopieren"), 1500);
});
$("#validate-btn").addEventListener("click", async () => {
  if (!currentDeck) return;
  $("#validate-btn").disabled = true;
  try { await api(`/api/decks/${currentDeck.slug}/validate`, { method: "POST" }); await openDeck(currentDeck.slug); }
  catch (err) { alert(err.message); }
  finally { $("#validate-btn").disabled = false; }
});
$("#delete-btn").addEventListener("click", async () => {
  if (!currentDeck || !confirm(`Deck „${currentDeck.name}“ löschen?`)) return;
  await api(`/api/decks/${currentDeck.slug}`, { method: "DELETE" });
  currentDeck = null;
  $("#deck-view").classList.add("hidden");
  $("#welcome").classList.remove("hidden");
  refreshDeckList();
});

// ---------- local card DB (Scryfall bulk data) ----------
async function refreshDbStatus() {
  const [st, run] = await Promise.all([api("/api/carddb"), api("/api/carddb/refresh")]);
  const el = $("#db-status");
  if (run.running) {
    el.textContent = "Lade und importiere Bulk-Daten … (All Cards ≈ 375 MB, dauert einige Minuten)";
    $("#db-btn").disabled = true;
    setTimeout(refreshDbStatus, 5000);
    return;
  }
  $("#db-btn").disabled = false;
  if (run.error) el.innerHTML = `<span class="bad">Fehler: ${esc(run.error)}</span>`;
  else if (!st.available) el.textContent = "Nicht vorhanden – ohne lokale DB wird die Scryfall-API live genutzt (langsamer, nur englische Namen).";
  else el.textContent = `${st.cards} Karten, ${st.tags} Tags, ${st.languages.length} Sprachen · Stand ${st.cards_updated_at?.slice(0, 10) ?? "?"}${st.needs_refresh ? " · Update empfohlen" : ""}`;
}
$("#db-btn").addEventListener("click", async () => {
  await api("/api/carddb/refresh", { method: "POST" });
  refreshDbStatus();
});

wireAutocomplete($("#commander"), $("#ac-commander"), previewCommander);
wireAutocomplete($("#partner"), $("#ac-partner"));
wireAutocomplete($("#bl-input"), $("#ac-bl"));
refreshBlacklist();
initBrackets();
refreshDeckList();
refreshDbStatus();
