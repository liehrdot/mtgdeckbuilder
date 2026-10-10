// „Frag Claude“: questions that the PC answers (a job on the sync server – the phone itself runs no AI). The tab
// lists the conversations and says honestly whether a PC can answer right now; a conversation shows each question,
// what Claude is doing while it works, and the answer with tappable cards and decks.

import { ask, cancelQuestion, currentDeck, deckBy, isDemo, jobOf, oppBy, oppName, setPref, shortName, state } from "./data.js";
import { cardSheet } from "./forms.js";
import { md } from "./md.js";
import { closeSheet, openSheet, toast } from "./ui.js";
import { $, $$, ago, esc, icon } from "./util.js";

// ---------- can a PC answer now? ----------
export function pcState() {
  if (isDemo()) return { tone: "info", title: "Vorschau", text: "Hier antwortet noch kein echter PC. Mit deinem PC verbunden beantwortet Claude dort deine Fragen." };
  if (!state.online) return { tone: "off", title: "Offline", text: "Deine Fragen gehen raus, sobald wieder Netz da ist." };
  const on = state.pcs.filter((p) => p.online);
  const ready = on.find((p) => p.ai && p.answers);
  if (ready) return { tone: "ok", title: `PC „${ready.name}“ ist bereit`, text: "Antworten dauern meist eine halbe bis anderthalb Minuten." };
  const noAnswers = on.find((p) => p.ai && !p.answers);
  if (noAnswers) return { tone: "wait", title: `PC „${noAnswers.name}“ beantwortet keine Fragen`, text: "Am PC unter Einstellungen → Sync zwischen Geräten „Fragen vom Handy beantworten“ einschalten. Bis dahin warten deine Fragen." };
  if (on.length) return { tone: "wait", title: "Claude ist am PC nicht bereit", text: "Am PC Claude Code einrichten bzw. anmelden – bis dahin warten deine Fragen." };
  if (!state.pcs.length) return { tone: "off", title: "Noch kein PC verbunden", text: "Fragen beantwortet der Deckbuilder auf deinem PC, sobald er mit dem Sync-Server verbunden ist." };
  return { tone: "off", title: "Dein PC ist gerade aus", text: "Deine Fragen warten und werden beantwortet, sobald der Deckbuilder am PC läuft." };
}

// ---------- unread answers (a dot on the tab) ----------
const seenSet = () => new Set(state.prefs.seenAnswers || []);
const isNew = (m, seen) => !!m.answer && m.source === "phone" && !seen.has(m.id);
export function unreadCount() {
  const seen = seenSet();
  return (state.snap?.chats || []).reduce((n, c) => n + c.messages.filter((m) => isNew(m, seen)).length, 0);
}
function markSeen(c) {
  const seen = seenSet();
  const fresh = c.messages.filter((m) => isNew(m, seen));
  if (fresh.length) setPref("seenAnswers", [...seen, ...fresh.map((m) => m.id)].slice(-300));
}

// ---------- texts ----------
const deckNamer = (m) => (slug) => m.decks?.[slug] || deckBy(slug)?.name || slug;
const plain = (m) => String(m.answer || "").replace(/\{\{([a-z0-9-]+)\}\}/g, (_, s) => deckNamer(m)(s)).replace(/\[\[([^\]]+)\]\]/g, "$1")
  .replace(/[#*_`|>]+/g, " ").replace(/\s+/g, " ").trim();

function openState(m) {
  const j = jobOf(m.id);
  if (m.status === "failed" || j?.status === "failed") return { failed: true, text: `Hat nicht geklappt: ${m.error || j?.error || "keine Antwort"}` };
  if (m.pending) return { text: state.online ? "Wird gesendet …" : "Wird gesendet, sobald wieder Netz da ist." };
  if (j?.status === "running") return { busy: true, text: `Claude ${j.progress || "denkt nach"} …` };
  if (j?.status === "done") return { busy: true, text: "Antwort kommt …" };
  const pc = pcState();
  return pc.tone === "ok" ? { busy: true, text: "Dein PC übernimmt gleich …" } : { text: `${pc.title} – die Frage wartet.` };
}

const snippet = (c) => {
  const m = c.messages[c.messages.length - 1];
  return m.answer ? plain(m).slice(0, 110) : openState(m).text;
};

// ---------- suggestions for a first question ----------
function suggestions() {
  const d = currentDeck();
  const last = state.snap?.games?.find((g) => g.deck === d?.slug) || state.snap?.games?.[0];
  const oppId = last?.opponent_ids?.find(Boolean);
  const opp = oppId && oppBy(oppId) ? oppName(oppBy(oppId)) : last?.opponents?.[0] ? shortName(last.opponents[0]) : null;
  const out = [];
  if (d && opp) out.push({ label: `${d.name} gegen ${opp}`, text: `Wie spiele ich ${d.name} am besten gegen ${opp}?` });
  if (d) out.push({ label: `Schwächen von ${d.name}`, text: `Was sind die größten Schwächen von ${d.name} – und was hilft dagegen?` });
  out.push({ label: "Regelfrage", text: "Regelfrage: ", focus: true });
  if ((state.snap?.decks || []).length > 1) out.push({ label: "Welches Deck heute?", text: "Welches meiner Decks passt heute am besten zu unserer Runde?" });
  return out;
}

// ---------- the tab ----------
export function claude() {
  const chats = state.snap.chats || [];
  const pc = pcState();
  const seen = seenSet();
  const html = `<div class="large-title"><h1>Claude</h1></div>
    <div class="pc-state ${pc.tone}" role="status"><span class="status-dot ${pc.tone === "ok" ? "" : pc.tone === "wait" ? "wait" : "off"}"></span>
      <span class="main"><span class="title">${esc(pc.title)}</span><span class="subtitle">${esc(pc.text)}</span></span></div>
    ${chats.length ? `<div class="section-head"><h2>Gespräche</h2></div>
      <div class="group">${chats.map((c) => {
        const open = c.messages.some((m) => !m.answer);
        const fresh = c.messages.some((m) => isNew(m, seen));
        return `<a class="row chat-row" href="#/claude/${esc(c.id)}"><span class="res ${open ? "draw" : "win"}" aria-hidden="true">${icon(open ? "clock" : "spark", "sm")}</span>
          <span class="main"><span class="title">${esc(c.title || "Gespräch")}</span><span class="subtitle two">${esc(snippet(c))}</span></span>
          <span class="trail col">${fresh ? '<span class="dot-new" aria-label="neue Antwort"></span>' : ""}<span>${esc(ago(c.updated))}</span></span></a>`;
      }).join("")}</div>`
      : `<div class="empty">${icon("spark")}<h3>Frag Claude zu deinen Decks</h3>
        <p>Strategie, Matchups, Regeln – Claude kennt auf deinem PC deine Decks, Partien und Gegner.</p></div>
        <div class="field-label">Zum Beispiel</div>
        <div class="chips">${suggestions().map((s, i) => `<button class="chip" type="button" data-sugg="${i}">${esc(s.label)}</button>`).join("")}</div>`}`;
  return {
    html, fab: { label: "Frage stellen", run: () => askSheet() },
    bar: { title: "Claude", watch: ".large-title h1" },
    bind(el) {
      const list = suggestions();
      for (const b of $$("[data-sugg]", el)) b.addEventListener("click", () => askSheet({ prefill: list[b.dataset.sugg].text }));
    },
  };
}

// ---------- one conversation ----------
let scrollToEnd = false;  // after a follow-up: show the new question and what Claude is doing

export function chatThread(id) {
  const c = (state.snap.chats || []).find((x) => x.id === id);
  const back = { href: "#/claude", label: "Claude" };
  if (!c) {
    return { html: `<div class="empty">${icon("spark")}<h3>Gespräch nicht gefunden</h3><p>Vielleicht wurde es zurückgezogen oder am PC gelöscht.</p></div>`,
      bar: { title: "Claude", back, always: true } };
  }
  markSeen(c);
  const msg = (m) => {
    const q = `<div class="bubble">${esc(m.question)}${m.deep ? `<span class="bubble-tag">gründlich</span>` : ""}</div>`;
    if (m.answer) {
      return `${q}<article class="answer md">${md(m.answer, deckNamer(m))}
        <p class="answer-foot">${esc(ago(m.answered || m.asked))}${m.source === "pc" ? " · am PC gefragt" : ""}</p></article>`;
    }
    const s = openState(m);
    return `${q}<div class="answer open${s.failed ? " failed" : ""}" role="status">
      ${s.busy ? '<span class="think" aria-hidden="true"><i></i><i></i><i></i></span>' : icon(s.failed ? "alert" : "clock", "sm")}
      <span class="open-text">${esc(s.text)}</span>
      ${s.failed ? `<button class="btn tint" type="button" data-retry="${esc(m.id)}">Nochmal fragen</button>`
        : `<button class="btn plain" type="button" data-cancel="${esc(m.id)}">Zurückziehen</button>`}</div>`;
  };
  const html = `<div class="thread-head"><a class="back-link" href="#/claude">${icon("chev-l", "sm")}Claude</a><h1>${esc(c.title || "Gespräch")}</h1></div>
    <div class="thread">${c.messages.map(msg).join("")}</div>`;
  return {
    html, fab: { label: "Nachfrage", run: () => askSheet({ chatId: c.id }) },
    bar: { title: c.title || "Gespräch", back, watch: ".thread-head h1" },
    bind(el) {
      if (scrollToEnd) {
        scrollToEnd = false;
        const last = [...el.querySelectorAll(".bubble")].pop();
        requestAnimationFrame(() => last && scrollTo({ top: Math.max(0, last.getBoundingClientRect().top + scrollY - 120) }));
      }
      for (const b of $$("[data-card-open]", el)) b.addEventListener("click", () => cardSheet(b.dataset.cardOpen));
      for (const b of $$("[data-cancel]", el)) b.addEventListener("click", () => {
        cancelQuestion(c.id, b.dataset.cancel);
        toast("Frage zurückgezogen");
        if (c.messages.length === 1) location.hash = "#/claude";
      });
      for (const b of $$("[data-retry]", el)) b.addEventListener("click", () => {
        const m = c.messages.find((x) => x.id === b.dataset.retry);
        cancelQuestion(c.id, m.id);
        ask(m.question, { chatId: c.id, deep: m.deep });
      });
    },
  };
}

// ---------- asking (new conversation or follow-up) ----------
export function askSheet({ chatId = null, prefill = "" } = {}) {
  const key = chatId ? `askDraft.${chatId}` : "askDraft";
  let text = prefill || state.prefs[key] || "";
  let deep = false;
  const list = chatId ? [] : suggestions();
  openSheet(() => {
    const pc = pcState();
    return {
      title: chatId ? "Nachfrage" : "Frag Claude",
      body: `<textarea class="textarea ask-input" id="ask-q" maxlength="4000" placeholder="${chatId ? "Was möchtest du noch wissen?" : "Was möchtest du wissen? Zu deinen Decks, Gegnern, Regeln …"}" enterkeyhint="send" aria-label="Deine Frage">${esc(text)}</textarea>
        ${list.length ? `<div class="chips ask-sugg">${list.map((s, i) => `<button class="chip" type="button" data-sugg="${i}">${esc(s.label)}</button>`).join("")}</div>` : ""}
        <label class="toggle-row"><span class="main"><span class="title">Gründlich</span><span class="subtitle">Claude prüft mehr nach – dauert länger</span></span>
          <input type="checkbox" class="switch" id="ask-deep" ${deep ? "checked" : ""}></label>
        ${pc.tone === "ok" || pc.tone === "info" ? "" : `<p class="hint-small">${icon(pc.tone === "off" ? "clock" : "alert", "xs")} ${esc(pc.title)} – die Frage wartet, bis ein PC sie beantworten kann.</p>`}`,
      foot: `<button class="btn primary big" type="button" id="ask-go" ${text.trim().length < 2 ? "disabled" : ""}>Fragen</button>`,
      bind(el) {
        const input = $("#ask-q", el);
        const go = $("#ask-go", el);
        const keep = () => { text = input.value; setPref(key, text); go.disabled = text.trim().length < 2; };
        input.addEventListener("input", keep);
        $("#ask-deep", el).addEventListener("change", (e) => { deep = e.target.checked; });
        for (const b of $$("[data-sugg]", el)) b.addEventListener("click", () => {
          input.value = list[b.dataset.sugg].text;
          keep();
          input.focus();
          input.setSelectionRange(input.value.length, input.value.length);
        });
        const send = () => {
          if (text.trim().length < 2) return;
          const sent = ask(text, { chatId, deep });
          setPref(key, "");
          scrollToEnd = !!chatId;
          closeSheet();
          setTimeout(() => {
            if (location.hash === `#/claude/${sent.chat_id}`) window.dispatchEvent(new Event("redraw"));
            else location.hash = `#/claude/${sent.chat_id}`;
          }, 260);
        };
        go.addEventListener("click", send);
        input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && (e.metaKey || e.ctrlKey)) send(); });
        setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 80);
      },
    };
  });
}
