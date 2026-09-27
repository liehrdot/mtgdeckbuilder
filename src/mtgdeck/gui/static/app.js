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

// ---------- settings (proxy printing) ----------
let appSettings = {};
async function loadSettings() {
  appSettings = await api("/api/settings");
  const f = $("#settings-form").elements;
  const MODEL_HINTS = { "realesrgan-x4plus": " (empfohlen)", "realesrgan-x4plus-anime": " (für Zeichnungen, glättet stärker)" };
  f.upscale_model.innerHTML = appSettings.upscale_models.map((m) => `<option value="${esc(m)}">${esc(m + (MODEL_HINTS[m] || ""))}</option>`).join("");
  for (const k of ["autofill_path", "mpcfill_server", "cardback_path", "browser", "site", "upscaler_path", "upscale_model", "descreen"]) if (f[k]) f[k].value = appSettings[k] ?? "";
  f.upscale.checked = !!appSettings.upscale;
  $("#upscaler-status").innerHTML = appSettings.upscaler_found
    ? `<span class="ok">✓ gefunden:</span> ${esc(appSettings.upscaler_found)}`
    : 'nicht installiert – nur nötig, wenn du hochskalieren willst (<a href="https://github.com/xinntao/Real-ESRGAN/releases" target="_blank" rel="noopener">Download</a>, benötigt eine Vulkan-fähige Grafikkarte).';
  $("#print-form").elements.upscale.checked = !!appSettings.upscale;
  $("#autofill-status").innerHTML = appSettings.autofill_found
    ? `<span class="ok">✓ gefunden:</span> ${esc(appSettings.autofill_found)}`
    : '<span class="warn">nicht gefunden</span> – Pfad eintragen oder die exe in den Ordner <code>tools/</code> legen.';
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
    $("#settings-msg").textContent = "Gespeichert ✓";
    setTimeout(() => ($("#settings-msg").textContent = ""), 1500);
  } catch (err) { $("#settings-msg").textContent = err.message; }
});

// ---------- print studio ----------
let printPlan = null;
const printOpts = () => {
  const f = $("#print-form").elements;
  return { source: f.source.value, stock: f.stock.value, foil: f.foil.checked, upscale: f.upscale.checked };
};

async function openPrintStudio() {
  $("#print-panel").classList.remove("hidden");
  $("#print-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  await loadPlan();
}
$("#print-btn").addEventListener("click", () => currentDeck && openPrintStudio());
$("#print-close").addEventListener("click", () => $("#print-panel").classList.add("hidden"));
$("#print-form").addEventListener("submit", (e) => { e.preventDefault(); loadPlan(); });

let prepared = { faces: {} };

async function loadPlan() {
  $("#print-summary").textContent = "Lade Vorschau …";
  $("#print-grid").innerHTML = "";
  try {
    [printPlan, prepared] = await Promise.all([
      api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/plan?source=${printOpts().source}`),
      api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/prepared`),
    ]);
  } catch (err) { $("#print-summary").textContent = err.message; return; }
  renderPlan();
  renderPreparedInfo();
  updateDownloadLinks();
}

function renderPreparedInfo() {
  const faces = Object.values(prepared.faces || {});
  const has = faces.length > 0;
  $("#open-folder-btn").classList.toggle("hidden", !has);
  $("#prepared-info").classList.toggle("hidden", !has);
  if (!has) return;
  const ai = faces.filter((f) => f.upscaled).length;
  $("#prepared-info").innerHTML = `Druckbilder: <code>${esc(prepared.images_dir)}</code> · ${faces.length} Bilder`
    + (ai ? ` · ${ai} KI-hochskaliert (${esc(prepared.upscale_model)}${prepared.descreen && prepared.descreen !== "off" ? ", Druckraster entfernt: " + esc(prepared.descreen) : ""})` : "")
    + " · 🔍 auf einer Karte zeigt Vorher/Nachher.";
}

function originTag(img) {
  if (!img) return "";
  if (img.custom) return '<span class="tag own">eigene Wahl</span>';
  return img.origin === "mpcfill" ? '<span class="tag mpc">MPC</span>' : '<span class="tag">Scryfall</span>';
}

function renderPlan() {
  const p = printPlan;
  const imgs = p.cards.flatMap((c) => [c.front?.image, c.back?.image]).filter(Boolean);
  const mpc = imgs.filter((i) => i.origin === "mpcfill").length;
  $("#print-summary").innerHTML = `${p.quantity} Karten · MPC-Staffel ${p.mpc_bracket} · ${mpc} MPC-Autofill-Scans, ${imgs.length - mpc} Scryfall`
    + ` · ${p.cards.filter((c) => c.back).length} doppelseitig`
    + (p.server ? "" : ' · <span class="warn">kein MPC-Autofill-Server eingestellt (nur Scryfall)</span>')
    + (p.missing.length ? ` · <span class="bad">ohne Bild: ${esc(p.missing.join(", "))}</span>` : "")
    + (p.warnings.length ? `<br><span class="warn">${esc(p.warnings.join(" "))}</span>` : "")
    + "<br>Klick auf eine Karte, um ein anderes Bild zu wählen.";
  $("#print-grid").innerHTML = p.cards.map((c, i) => cardTile(c, i, "front")).join("");
}

function cardTile(c, i, side) {
  const f = c[side];
  const img = f?.image;
  return `<button type="button" class="pcard" data-i="${i}" data-side="${side}" title="${esc(c.name)} – Bild wählen">
    ${img ? `<img src="${esc(img.thumb)}" alt="${esc(f.face)}" loading="lazy">` : `<div class="noimg">${esc(c.name)}<br>kein Bild</div>`}
    <div class="tags">${c.qty > 1 ? `<span class="tag">${c.qty}×</span>` : ""}${c.commander ? '<span class="tag">Commander</span>' : ""}${c.back ? '<span class="tag dfc" title="Doppelseitige Karte – ↻ dreht sie um">DFC</span>' : ""}${originTag(img)}${f && prepared.faces?.[f.face]?.upscaled ? '<span class="tag ai">KI</span>' : ""}</div>
    ${c.back ? `<span class="flip" data-flip="${i}" title="${side === "front" ? "Rückseite zeigen" : "Vorderseite zeigen"}" aria-label="Karte umdrehen">↻</span>` : ""}
    ${f && prepared.faces?.[f.face] ? `<span class="zoom" data-compare="${esc(f.face)}" title="Vorher/Nachher vergleichen">🔍 ${prepared.faces[f.face].dpi ? esc(prepared.faces[f.face].dpi) + " DPI" : ""}</span>` : ""}
    <div class="cap">${esc(f?.face || c.name)}${c.back ? `<span class="hint"> · ${side === "front" ? "Vorderseite" : "Rückseite"}</span>` : ""}</div>
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

let pickerCtx = null;
async function openPicker(i, side) {
  const c = printPlan.cards[i];
  pickerCtx = { card: c, side, face: c[side].face };
  $("#picker-title").textContent = `${c[side].face}${side === "back" ? " (Rückseite)" : ""}`;
  $("#picker-hint").textContent = "Lade Bilder von MPC Autofill und alle Scryfall-Drucke …";
  $("#picker-grid").innerHTML = "";
  $("#picker").showModal();
  try {
    const opts = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/alternatives?card=${encodeURIComponent(c.name)}&side=${side}`);
    pickerCtx.options = opts;
    const current = c[side].image?.id;
    $("#picker-hint").textContent = `${opts.length} Bilder · MPC-Autofill-Scans sind druckoptimiert (mit Beschnitt-Rand)`;
    $("#picker-grid").innerHTML = opts.map((o, k) => `<button type="button" class="pcard ${o.id === current ? "selected" : ""}" data-k="${k}">
      <img src="${esc(o.thumb)}" alt="" loading="lazy">
      <div class="tags">${o.origin === "mpcfill" ? '<span class="tag mpc">MPC</span>' : '<span class="tag">Scryfall</span>'}${o.dpi ? `<span class="tag">${esc(o.dpi)} DPI</span>` : ""}</div>
      <div class="cap">${esc(o.label || "")}</div></button>`).join("") || '<p class="hint">Keine Alternativen gefunden.</p>';
  } catch (err) { $("#picker-hint").textContent = err.message; }
}
async function pick(option) {
  await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/choose`, { method: "POST", body: { face: pickerCtx.face, option } });
  $("#picker").close();
  loadPlan();
}
$("#picker-grid").addEventListener("click", (e) => {
  const t = e.target.closest("[data-k]");
  if (t) pick(pickerCtx.options[Number(t.dataset.k)]).catch((err) => alert(err.message));
});
$("#picker-auto").addEventListener("click", () => pick(null).catch((err) => alert(err.message)));
$("#picker-close").addEventListener("click", () => $("#picker").close());

$("#prepare-btn").addEventListener("click", async () => {
  try {
    const { job } = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/prepare`, { method: "POST", body: printOpts() });
    startJob(job, `Druckdateien für ${currentDeck.name} …`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) { alert(err.message); }
});

// ---------- before/after comparison ----------
let compareFace = null;
function imageUrl(face, kind) {
  return `/api/decks/${encodeURIComponent(currentDeck.slug)}/print/image?face=${encodeURIComponent(face)}&kind=${kind}&t=${Date.now()}`;
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
  a.onload = b.onload = applyZoom;
  a.src = imageUrl(compareFace, "original");
  b.src = imageUrl(compareFace, bleed ? "file" : "trim");
  b.onload = () => {
    $("#compare-cap-b").dataset.size = `${b.naturalWidth} × ${b.naturalHeight} px`;
    $("#compare-cap-b").title = $("#compare-cap-b").dataset.size;
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
  for (const img of [$("#compare-a"), $("#compare-b")]) img.style.width = `${width}px`;
  for (const p of [$("#pane-a"), $("#pane-b")]) {
    p.scrollLeft = cx * p.scrollWidth - p.clientWidth / 2;
    p.scrollTop = cy * p.scrollHeight - p.clientHeight / 2;
  }
  const a = $("#compare-a");
  if (a.naturalWidth) $("#compare-cap-a").title = `${a.naturalWidth} × ${a.naturalHeight} px`;
}
let syncLock = null;  // the pane the user is scrolling; the other one follows
for (const [src, dst] of [["#pane-a", "#pane-b"], ["#pane-b", "#pane-a"]]) {
  $(src).addEventListener("scroll", () => {
    if (syncLock && syncLock !== src) return;
    syncLock = src;
    const s = $(src), d = $(dst);
    d.scrollLeft = s.scrollLeft * (d.scrollWidth / Math.max(s.scrollWidth, 1));
    d.scrollTop = s.scrollTop * (d.scrollHeight / Math.max(s.scrollHeight, 1));
    clearTimeout(window._syncTimer);
    window._syncTimer = setTimeout(() => { syncLock = null; }, 120);
  });
}
$("#compare-zoom").addEventListener("change", applyZoom);
$("#compare-bleed").addEventListener("change", loadCompareImages);
$("#compare-close").addEventListener("click", () => $("#compare").close());

$("#open-folder-btn").addEventListener("click", async () => {
  try {
    const r = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/open-folder`, { method: "POST" });
    if (!r.opened) alert(`Ordner: ${r.path}`);
  } catch (err) { alert(err.message); }
});

async function onPrepared(result) {
  logLine("result", `Druckbilder: ${result.images_dir}`);
  if (result.missing.length) logLine("error", `Ohne Bild: ${result.missing.join(", ")}`);
  updateDownloadLinks();
  if (currentDeck && !$("#print-panel").classList.contains("hidden")) {
    prepared = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/prepared`).catch(() => prepared);
    renderPlan();
    renderPreparedInfo();
  }
}

async function updateDownloadLinks() {
  const base = `/api/decks/${encodeURIComponent(currentDeck.slug)}/print/files`;
  for (const [id, kind] of [["#xml-link", "xml"], ["#pdf-link", "pdf"]]) {
    const ok = (await fetch(`${base}/${kind}`, { method: "HEAD" }).catch(() => null))?.ok;
    $(id).href = `${base}/${kind}`;
    $(id).classList.toggle("hidden", !ok);
  }
}

$("#pdf-btn").addEventListener("click", async () => {
  $("#pdf-btn").disabled = true;
  $("#pdf-btn").textContent = "Erstelle PDF …";
  try {
    const r = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/pdf`, { method: "POST", body: { paper: $("#pdf-paper").value, include_backs: $("#pdf-backs").checked } });
    await updateDownloadLinks();
    window.open($("#pdf-link").href, "_blank");
    $("#pdf-btn").textContent = `PDF: ${r.cards} Karten, ${r.pages} Seiten ✓`;
  } catch (err) { alert(err.message); $("#pdf-btn").textContent = "PDF zum Selbstdrucken"; }
  finally { $("#pdf-btn").disabled = false; }
});

$("#mpc-btn").addEventListener("click", async () => {
  try {
    const r = await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/print/autofill`, { method: "POST", body: { mode: "mpc", window: $("#mpc-window").checked, ...TERM_SIZE } });
    if (r.job) { startJob(r.job, `MPC Autofill: ${currentDeck.name}`); window.scrollTo({ top: 0, behavior: "smooth" }); }
    else alert("MPC Autofill wurde in einem eigenen Konsolenfenster gestartet – dort weiter bedienen.");
  } catch (err) { alert(err.message); }
});

// ---------- terminal (MPC Autofill runs in a pseudo-terminal; its menus need arrow keys) ----------
const ANSI_RE = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b\][^\x07]*\x07|\r/g;
const TERM_SIZE = { rows: 32, cols: 110 };
const KEYS = { up: "\x1b[A", down: "\x1b[B", enter: "\r" };
let term = null;
let keyQueue = Promise.resolve();

function sendKeys(data, raw = true) {
  const job = currentJob;
  keyQueue = keyQueue.then(() => api(`/api/jobs/${job}/input`, { method: "POST", body: { text: data, raw } })
    .catch((err) => logLine("error", err.message)));
}

function resetTerminal() {
  if (term) { term.dispose(); term = null; }
  $("#terminal").innerHTML = "";
  $("#terminal-wrap").classList.add("hidden");
}

function openTerminal() {
  if (term) return;
  if (!window.Terminal) {  // xterm.js missing -> simple line input as fallback
    $("#console-form").classList.remove("hidden");
    return;
  }
  $("#terminal-wrap").classList.remove("hidden");
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

$("#console-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = e.target.elements.text;
  sendKeys(input.value, false);
  input.value = "";
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
  $("#progress").classList.add("hidden");
  $("#console-form").classList.add("hidden");
  resetTerminal();
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
    case "progress": {
      const bar = $("#progress");
      bar.classList.remove("hidden");
      bar.querySelector("div").style.width = `${(ev.done / Math.max(ev.total, 1)) * 100}%`;
      bar.querySelector("span").textContent = `${ev.done} / ${ev.total} · ${ev.text || ""}`;
      break;
    }
    case "print": onPrepared(ev.result); break;
    case "console":
      if (ev.running) openTerminal();
      else if (term) term.options.disableStdin = true;
      break;
    case "term":
      if (term) term.write(ev.data);
      else logLine("text", ev.data.replace(ANSI_RE, "").trimEnd());
      break;
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

// ---------- questions about the deck ----------
let qaRun = null;  // { job, slug, question, source, error }
const QA_TOOLS = {
  load_deck: "lädt das Deck", get_cards: "liest Kartentexte", find_combos: "sucht Combos",
  edhrec_average_deck: "schaut sich ein Deck auf EDHREC an", edhrec_recommendations: "prüft EDHREC",
  validate_deck: "prüft Bracket & Legalität", bracket_rules: "liest die Bracket-Regeln",
  game_changers: "prüft Game Changer", search_cards: "sucht Karten", local_card_search: "sucht Karten",
  import_deck: "importiert ein Deck", compare_deck_versions: "vergleicht Versionen",
  list_deck_versions: "liest den Verlauf", Skill: "lädt die Deckbau-Anleitung", Read: "liest eine Referenz",
};

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
  const when = q.asked ? new Date(q.asked).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "";
  const older = q.version && currentDeck?.version && q.version !== currentDeck.version;
  const ver = q.version ? ` · v${q.version}${older ? " (ältere Version)" : ""}` : "";
  return `<article class="qa-item" data-id="${esc(q.id)}">
    <div class="qa-q"><span>${esc(q.question)}</span>
      <span class="meta">${esc(when)}${esc(ver)}<button type="button" class="qa-del" title="Frage löschen" aria-label="Frage löschen">✕</button></span></div>
    <div class="qa-a">${md(q.answer, q.cards || {})}</div></article>`;
}

async function renderQuestions(d) {
  const items = await api(`/api/decks/${encodeURIComponent(d.slug)}/questions`).catch(() => []);
  if (currentDeck?.slug !== d.slug) return;
  $("#qa-list").innerHTML = items.map(qaItem).join("");
  $("#qa-clear").classList.toggle("hidden", !items.length);
  const list = $("#qa-list");
  list.scrollTop = list.scrollHeight;
  updateQaLive();
}

function updateQaLive() {
  const run = qaRun && currentDeck && qaRun.slug === currentDeck.slug ? qaRun : null;
  $("#qa-live").classList.toggle("hidden", !run);
  $("#qa-btn").disabled = !!(qaRun && !qaRun.finished);
  if (!run) return;
  $("#qa-question").textContent = run.question;
  $("#qa-live .spinner").classList.toggle("hidden", !!run.finished);
  $("#qa-status").textContent = run.error ? "Fehler: " + run.error : run.status || "Claude denkt nach …";
  $("#qa-status").classList.toggle("bad", !!run.error);
  $("#qa-cancel").textContent = run.finished ? "Schließen" : "Abbrechen";
}

function onQaEvent(run, ev) {
  switch (ev.type) {
    case "tool": run.status = `Claude ${QA_TOOLS[ev.name] || ev.name} …`; break;
    case "status": run.status = ev.text; break;
    case "error": run.error = ev.text; break;
    case "answer":
      if (currentDeck?.slug === run.slug) {
        $("#qa-list").insertAdjacentHTML("beforeend", qaItem(ev.entry));
        $("#qa-clear").classList.remove("hidden");
        $("#qa-list").lastElementChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
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
    const { job } = await api(`/api/decks/${encodeURIComponent(slug)}/ask`, { method: "POST", body: { question } });
    const run = { job, slug, question, source: new EventSource(`/api/jobs/${job}/events`) };
    run.source.onmessage = (ev) => onQaEvent(run, JSON.parse(ev.data));
    qaRun = run;
    e.target.reset();
    updateQaLive();
  } catch (err) { alert(err.message); }
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
  await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/questions?id=${encodeURIComponent(item.dataset.id)}`, { method: "DELETE" });
  item.remove();
  $("#qa-clear").classList.toggle("hidden", !$("#qa-list").children.length);
});
$("#qa-clear").addEventListener("click", async () => {
  if (!currentDeck || !confirm("Alle Fragen und Antworten zu diesem Deck löschen?")) return;
  await api(`/api/decks/${encodeURIComponent(currentDeck.slug)}/questions`, { method: "DELETE" });
  renderQuestions(currentDeck);
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
  $("#print-panel").classList.add("hidden");
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
  renderQuestions(d);
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
      return `<div class="card" data-img="${esc(cd.image || "")}" data-img-back="${esc(cd.image_back || "")}" data-name="${esc(c.name)}"
          title="${cd.image_back ? "Doppelseitige Karte – Klick zeigt beide Seiten" : "Klick für große Ansicht"}">
        <span>${c.qty > 1 ? c.qty + "× " : ""}${esc(c.name)}${cd.image_back ? '<span class="dfc" aria-label="doppelseitig">⇄</span>' : ""}${cd.game_changer ? '<span class="gc">GC</span>' : ""}</span>
        <span class="price">${cd[priceKey] ? cd[priceKey] : ""}</span></div>`;
    }).join("")}</div>`).join("");
}

// hover preview of card images
const preview = $("#preview");
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest(".card[data-img]");
  if (!el || !el.dataset.img) return;
  const [front, back] = preview.querySelectorAll("img");
  front.src = el.dataset.img;
  back.classList.toggle("hidden", !el.dataset.imgBack);  // double-faced: both sides side by side
  if (el.dataset.imgBack) back.src = el.dataset.imgBack;
  preview.classList.remove("hidden");
});
document.addEventListener("mouseout", (e) => { if (e.target.closest(".card[data-img]")) preview.classList.add("hidden"); });
document.addEventListener("mousemove", (e) => {
  const w = preview.querySelector("img.back:not(.hidden)") ? 500 : 260;
  const x = e.clientX + w > window.innerWidth ? e.clientX - w : e.clientX + 20;
  const y = Math.min(e.clientY - 40, window.innerHeight - 350);
  preview.style.left = x + "px"; preview.style.top = Math.max(8, y) + "px";
});

// click on a card: large view, both faces for double-faced cards (works on touch devices too)
$("#cards").addEventListener("click", (e) => {
  const el = e.target.closest(".card[data-name]");
  if (!el || !currentDeck) return;
  showCardView(el.dataset.name, currentDeck.card_data?.[el.dataset.name] || {});
});
function showCardView(name, cd) {
  const faces = name.split(" // ");
  const imgs = [[cd.image, faces[0]], ...(cd.image_back ? [[cd.image_back, faces[1] || "Rückseite"]] : [])];
  $("#card-view-title").textContent = name + (cd.image_back ? " – doppelseitig" : "");
  $("#card-view-faces").innerHTML = imgs.filter(([u]) => u).map(([u, label], i) =>
    `<figure><img src="${esc(u.replace("/normal/", "/large/"))}" alt="${esc(label)}"><figcaption>${i ? "Rückseite" : "Vorderseite"}: ${esc(label)}</figcaption></figure>`).join("")
    || '<p class="hint">Kein Bild verfügbar.</p>';
  $("#card-view-link").href = cd.scryfall_uri || `https://scryfall.com/search?q=${encodeURIComponent('!"' + name + '"')}`;
  preview.classList.add("hidden");
  $("#card-view").showModal();
}
$("#card-view-close").addEventListener("click", () => $("#card-view").close());

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
  else el.textContent = `${st.cards} Karten, ${st.tags} Tags, ${st.languages.length} Sprachen · Stand ${st.cards_updated_at?.slice(0, 10) ?? "?"}`
    + (st.schema_outdated ? " · Update empfohlen: Datenbank kennt noch keine Rückseiten doppelseitiger Karten (werden solange live bei Scryfall nachgeladen)"
      : st.needs_refresh ? " · Update empfohlen" : "");
}
$("#db-btn").addEventListener("click", async () => {
  await api("/api/carddb/refresh", { method: "POST" });
  refreshDbStatus();
});

wireAutocomplete($("#commander"), $("#ac-commander"), previewCommander);
wireAutocomplete($("#partner"), $("#ac-partner"));
wireAutocomplete($("#bl-input"), $("#ac-bl"));
refreshBlacklist();
loadSettings().catch((err) => console.error(err));
initBrackets();
refreshDeckList();
refreshDbStatus();
