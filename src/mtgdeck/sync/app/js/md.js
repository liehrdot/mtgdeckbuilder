// Claude's answers (Markdown) as safe HTML for the phone: everything is escaped first, then a small subset becomes
// markup – headings, paragraphs, lists, tables, bold/italic/code, https links. [[Card]] becomes a button that opens
// the card, {{slug}} a link to the deck (named from the answer's ``decks`` or the deck list).

import { esc } from "./util.js";

function inline(text, deckName) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[\[([^\][]{2,141})\]\]/g, (_, name) => `<button class="card-link" type="button" data-card-open="${name}">${name}</button>`)
    .replace(/\{\{([a-z0-9][a-z0-9-]{0,80})\}\}/g, (_, slug) => `<a class="deck-link" href="#/decks/${slug}">${esc(deckName(slug))}</a>`)
    .replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

/** ``text`` (Markdown) → HTML; ``deckName(slug)`` gives the shown name of a linked deck. */
export function md(text, deckName = (s) => s) {
  const lines = esc(text || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [];
  let list = null;  // {tag, items}
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "), deckName)}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i, deckName)}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();
    if (!t) { flushPara(); flushList(); continue; }
    const h = t.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flushPara(); flushList(); out.push(`<h4>${inline(h[2], deckName)}</h4>`); continue; }
    if (/^(-{3,}|\*{3,})$/.test(t)) { flushPara(); flushList(); out.push("<hr>"); continue; }
    if (t.startsWith("|") && /^\|?\s*:?-{2,}/.test((lines[i + 1] || "").trim())) {  // a table: header, separator, rows
      flushPara(); flushList();
      const head = cells(t);
      const rows = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(cells(lines[i]));
      i--;
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${inline(c, deckName)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c, deckName)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const li = t.match(/^(?:[-*•]|(\d+)[.)])\s+(.*)$/);
    if (li) {
      flushPara();
      const tag = li[1] ? "ol" : "ul";
      if (list && list.tag !== tag) flushList();
      (list ||= { tag, items: [] }).items.push(li[2]);
      continue;
    }
    if (list && /^\s{2,}/.test(line)) { list.items[list.items.length - 1] += ` ${t}`; continue; }  // continued list item
    flushList();
    para.push(t);
  }
  flushPara(); flushList();
  return out.join("");
}
