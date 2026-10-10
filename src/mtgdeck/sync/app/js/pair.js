// First start and pairing: welcome, code entry, QR scan, and on an iPhone in Safari the "add to Home Screen"
// steps first – an installed web app has its own storage on iOS, so the code is redeemed inside the app.

import { connect, startDemo, state } from "./data.js";
import { closeSheet, openSheet, present, toast } from "./ui.js";
import { $, esc, icon } from "./util.js";

export const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
export const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
export const defaultName = () => (isIOS() ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android-Handy" : "Handy");
const CODE_RE = /([A-Z0-9]{4})-?([A-Z0-9]{4})/i;
export const codeFrom = (text) => { const m = String(text || "").match(CODE_RE); return m ? `${m[1]}-${m[2]}`.toUpperCase() : null; };

/** The welcome screen (not paired). ``code`` comes from a pairing link (…/koppeln#CODE). */
export function welcome(code = null) {
  if (code && isIOS() && !isStandalone()) return installSteps(code);
  if (code) return confirmLink(code);
  const html = `<div class="welcome">
      <img class="app-icon" src="/app/icons/icon-192.png" alt="" width="88" height="88">
      <h1>Am Tisch</h1>
      <p class="lead">Partien und Gegnerdecks am Spieltisch festhalten – verbunden mit deinem Deckbuilder am PC.</p>
      ${state.revoked ? `<p class="notice">${icon("shield", "sm")}<span>Dieses Handy wurde abgemeldet. Mit einem neuen Code verbindest du es wieder – was noch nicht gesendet war, geht dann mit.</span></p>` : ""}
      <div class="welcome-actions">
        <button class="btn primary big" type="button" id="scan">${icon("qr")}QR-Code scannen</button>
        <button class="btn big" type="button" id="enter">Code eingeben</button>
      </div>
      <p class="hint-small center">Den Code zeigt dein PC unter <b>Einstellungen → Sync zwischen Geräten → Weiteres Gerät koppeln</b>.</p>
      <button class="btn plain" type="button" id="demo">Erst mal mit Beispieldaten ansehen</button>
    </div>`;
  return {
    html, bar: { title: "Am Tisch" },
    bind(el) {
      $("#scan", el).addEventListener("click", scan);
      $("#enter", el).addEventListener("click", () => codeSheet());
      $("#demo", el).addEventListener("click", async () => { await startDemo(); location.hash = "#/tisch"; });
    },
  };
}

function installSteps(code) {
  const html = `<div class="welcome">
      <img class="app-icon" src="/app/icons/icon-192.png" alt="" width="72" height="72">
      <h1>Fast geschafft</h1>
      <p class="lead">Leg „Am Tisch“ auf deinen Home-Bildschirm. Dann startet es wie eine App, und deine Einträge bleiben sicher gespeichert.</p>
      <ol class="steps">
        <li><span class="step-icon">${icon("share")}</span><span>Unten in Safari auf <b>Teilen</b> tippen – siehst du es nicht, zuerst auf <b>•••</b>.</span></li>
        <li><span class="step-icon">${icon("plus")}</span><span><b>Zum Home-Bildschirm</b> wählen und <b>Hinzufügen</b>.</span></li>
        <li><span class="step-icon"><img src="/app/icons/icon-192.png" alt="" width="26" height="26"></span><span><b>Am Tisch</b> öffnen, <b>Code eingeben</b> tippen und diesen Code einfügen:</span></li>
      </ol>
      <div class="code-box"><span class="code">${esc(code)}</span><button class="btn tint" type="button" id="copy">${icon("copy", "sm")}Kopieren</button></div>
      <p class="hint-small center">Der Code gilt 15 Minuten. Abgelaufen? Am PC einfach einen neuen erzeugen.</p>
      <button class="btn plain" type="button" id="here">Lieber hier im Browser weiter</button>
    </div>`;
  return {
    html, bar: { title: "Fast geschafft" },
    bind(el) {
      $("#copy", el).addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(code); toast("Code kopiert", { ok: true, sub: "In der App unter „Code eingeben“ einfügen." }); }
        catch { toast(`Code: ${code}`); }
      });
      $("#here", el).addEventListener("click", () => codeSheet(code));
    },
  };
}

function confirmLink(code) {
  const html = `<div class="welcome">
      <img class="app-icon" src="/app/icons/icon-192.png" alt="" width="72" height="72">
      <h1>Mit deinem Deckbuilder verbinden?</h1>
      <p class="lead">Dieses Handy verbindet sich mit <b>${esc(location.host)}</b>. Danach siehst du deine Decks und Gegner und kannst Partien eintragen.</p>
      <label class="field welcome-field"><span class="field-label">Name dieses Handys</span>
        <input class="input" id="pair-name" value="${esc(defaultName())}" maxlength="60" autocomplete="off" enterkeyhint="go"></label>
      <p class="notice" id="pair-error" hidden></p>
      <div class="welcome-actions"><button class="btn primary big" type="button" id="go">Verbinden</button></div>
      <button class="btn plain" type="button" id="other">Anderen Code eingeben</button>
    </div>`;
  return {
    html, bar: { title: "Verbinden" },
    bind(el) {
      const go = async () => {
        const btn = $("#go", el);
        btn.disabled = true; btn.textContent = "Verbinde …";
        try { await connectAndGo(code, $("#pair-name", el).value.trim()); }
        catch (err) { const e = $("#pair-error", el); e.hidden = false; e.textContent = err.message; btn.disabled = false; btn.textContent = "Verbinden"; }
      };
      $("#go", el).addEventListener("click", go);
      $("#pair-name", el).addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      $("#other", el).addEventListener("click", () => codeSheet());
    },
  };
}

async function connectAndGo(code, name) {
  await connect(code, name);
  history.replaceState(null, "", "#/tisch");
  window.dispatchEvent(new Event("hashchange"));
  toast("Verbunden", { ok: true, sub: state.snap ? `${state.snap.decks.length} Decks von deinem PC` : "" });
}

/** Sheet: type the code (8 characters, the dash comes by itself) and a name for this phone. */
export function codeSheet(prefill = "") {
  let error = "";
  let value = prefill;
  let name = defaultName();
  openSheet(({ redraw }) => ({
    title: "Code eingeben",
    body: `<p class="hint-small">Den Code zeigt dein PC unter <b>Einstellungen → Sync zwischen Geräten → Weiteres Gerät koppeln</b>.</p>
      <input class="input code-input" id="code" value="${esc(value)}" placeholder="ABCD-EFGH" maxlength="9" autocomplete="off" autocapitalize="characters" spellcheck="false" enterkeyhint="go" aria-label="Kopplungscode">
      ${error ? `<p class="notice">${esc(error)}</p>` : ""}
      <div class="field-label">Name dieses Handys</div>
      <input class="input" id="name" value="${esc(name)}" maxlength="60" autocomplete="off" enterkeyhint="go">`,
    foot: `<button class="btn primary big" type="button" id="go" ${codeFrom(value) ? "" : "disabled"}>Verbinden</button>`,
    bind(el) {
      const input = $("#code", el);
      const go = $("#go", el);
      const fmt = () => {
        const raw = input.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
        input.value = raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
        value = input.value;
        go.disabled = !codeFrom(value);
      };
      input.addEventListener("input", fmt);
      $("#name", el).addEventListener("input", (e) => { name = e.target.value; });
      const submit = async () => {
        if (!codeFrom(value)) return;
        go.disabled = true; go.textContent = "Verbinde …";
        try { await connect(codeFrom(value), name.trim()); closeSheet(); history.replaceState(null, "", "#/tisch"); window.dispatchEvent(new Event("hashchange"));
          toast("Verbunden", { ok: true, sub: state.snap ? `${state.snap.decks.length} Decks von deinem PC` : "" }); }
        catch (err) { error = err.message; redraw(); }
      };
      go.addEventListener("click", submit);
      for (const x of [input, $("#name", el)]) x.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
      if (!value) setTimeout(() => input.focus(), 60);
    },
  }));
}

/** Full screen camera: scan the QR code shown on the PC. */
export async function scan() {
  let scanner = null;
  const stop = () => { try { scanner?.stop(); scanner?.destroy(); } catch { /* gone */ } scanner = null; };
  present(`<div class="scanner"><video id="scan-video" playsinline muted></video><div class="scan-frame" aria-hidden="true"></div>
      <p class="scan-hint" id="scan-hint">Richte die Kamera auf den QR-Code am PC.</p></div>`, {
    actions: `<button class="btn" type="button" id="scan-code">Code eingeben</button>`,
    bind: async (el) => {
      const hint = $("#scan-hint", el);
      $("#scan-code", el).addEventListener("click", () => { history.back(); setTimeout(() => codeSheet(), 300); });
      try {
        const { default: QrScanner } = await import("/app/vendor/qr-scanner/qr-scanner.min.js");
        scanner = new QrScanner($("#scan-video", el), async (result) => {
          const text = result?.data || "";
          const code = codeFrom(text);
          if (!code) { hint.textContent = "Das ist kein Kopplungscode – bitte den QR-Code unter „Weiteres Gerät koppeln“ scannen."; return; }
          try {
            const url = new URL(text);
            if (url.host !== location.host) { hint.textContent = `Dieser Code gehört zu einem anderen Server (${url.host}).`; return; }
          } catch { /* a bare code */ }
          stop();
          hint.textContent = "Verbinde …";
          try { await connect(code, defaultName()); history.back(); setTimeout(() => { history.replaceState(null, "", "#/tisch"); window.dispatchEvent(new Event("hashchange")); toast("Verbunden", { ok: true }); }, 50); }
          catch (err) { hint.textContent = err.message; }
        }, { returnDetailedScanResult: true, preferredCamera: "environment", maxScansPerSecond: 8 });
        await scanner.start();
      } catch {
        hint.textContent = "Die Kamera ist nicht verfügbar oder nicht erlaubt. Tippe auf „Code eingeben“.";
      }
    },
    onClose: stop,  // also on the back button: the camera goes off
  });
}
