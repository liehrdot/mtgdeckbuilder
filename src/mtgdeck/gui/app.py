"""Local web GUI. Builds decks by running Claude Code (Claude Agent SDK) with the project's
skills and the 'mtg' MCP server, and shows saved decks with images, stats and bracket check.

Start: ``uv run mtg-gui`` -> http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import blacklist, brackets, carddb, proxy, scryfall, storage
from .. import settings as settings_mod
from ..cards import resolve
from ..deck import DeckEntry, to_text
from ..http import HttpError
from ..power import TIER_LABELS, PowerProfile, target_value
from . import terminal
from ..validate import validate_deck

STATIC = Path(__file__).parent / "static"
PROJECT_ROOT = storage.PROJECT_ROOT

app = FastAPI(title="MTG Commander Deckbuilder")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- jobs: one Claude Code run each -------------------------------------------------------------


@dataclass
class Job:
    id: str
    started: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    proc: Any = None  # terminal.Terminal of the MPC Autofill job
    loop: asyncio.AbstractEventLoop | None = None

    def emit(self, **event: Any) -> None:
        self.events.append(event)
        self.changed.set()

    def emit_threadsafe(self, **event: Any) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(lambda: self.emit(**event))


JOBS: dict[str, Job] = {}


def _start_runner(runner: Any) -> dict[str, str]:
    """Run ``runner(job)`` as a background job whose events stream to the browser."""
    job = Job(id=uuid.uuid4().hex[:12], loop=asyncio.get_running_loop())
    JOBS[job.id] = job

    async def wrapped() -> None:
        try:
            await runner(job)
        except asyncio.CancelledError:
            job.emit(type="error", text="Abgebrochen.")
            job.emit(type="done", ok=False)
        except Exception as exc:
            job.emit(type="error", text=f"{type(exc).__name__}: {exc}")
            job.emit(type="done", ok=False)
        finally:
            job.done = True

    job.task = asyncio.create_task(wrapped())
    return {"job": job.id}


def _tool_summary(name: str, args: dict[str, Any]) -> str:
    for key in ("query", "commander", "name", "names", "tags", "url", "slug", "skill"):
        if key in args and args[key]:
            val = args[key]
            if isinstance(val, list):
                val = ", ".join(map(str, val[:5])) + (" …" if len(val) > 5 else "")
            return f"{key}={val}"
    return ""


# Tools for questions about a deck: research only, nothing that saves or changes anything.
READ_ONLY_TOOLS = ["search_cards", "local_card_search", "get_cards", "find_commanders", "game_changers",
                   "card_db_status", "edhrec_recommendations", "edhrec_average_deck", "find_combos",
                   "bracket_rules", "validate_deck", "get_blacklist", "list_decks", "load_deck",
                   "list_deck_versions", "compare_deck_versions", "export_deck", "import_deck"]  # fmt: skip
WRITE_TOOLS = ["save_deck", "update_blacklist", "restore_deck_version", "copy_deck", "update_card_database",
               "create_proxy_order", "export_proxy_pdf", "launch_proxy_tool", "proxy_settings"]  # fmt: skip
Finish = Callable[[Job, bool, str], Awaitable[None]]


async def _run_claude(
    job: Job,
    prompt: str,
    model: str | None,
    output_format: dict[str, Any] | None = None,
    *,
    read_only: bool = False,
    finish: Finish | None = None,
) -> None:
    """Run Claude Code on ``prompt``. ``read_only`` restricts the mtg tools to research;
    ``finish(job, ok, final_text)`` replaces the default end (detect the saved deck)."""
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )
    except ImportError:
        job.emit(type="error", text="claude-agent-sdk fehlt: `uv sync --extra gui` ausführen.")
        job.emit(type="done", ok=False)
        job.done = True
        return

    base_tools = ["Skill", "ToolSearch", "Read", "Glob", "Grep"]
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        setting_sources=["project"],  # loads .claude/skills and CLAUDE.md
        mcp_servers={"mtg": {"type": "stdio", "command": sys.executable, "args": ["-m", "mtgdeck.mcp_server"]}},
        strict_mcp_config=True,
        allowed_tools=base_tools + ([f"mcp__mtg__{t}" for t in READ_ONLY_TOOLS] if read_only else ["mcp__mtg"]),
        disallowed_tools=(["Write", "Edit", "Bash"] + [f"mcp__mtg__{t}" for t in WRITE_TOOLS]) if read_only else [],
        permission_mode="dontAsk",
        model=model or None,
        max_turns=int(os.environ.get("MTG_MAX_TURNS", 120)),
        output_format=output_format,
    )
    ok = False
    structured: Any = None
    final_text = ""
    last_text = ""
    job.emit(type="status", text="Starte Claude Code …")
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
                        job.emit(type="text", text=last_text)
                    elif isinstance(block, ToolUseBlock) and block.name == "StructuredOutput":
                        job.emit(type="status", text="Übergebe Ergebnis …")
                    elif isinstance(block, ToolUseBlock):
                        name = block.name.removeprefix("mcp__mtg__")
                        job.emit(type="tool", name=name, summary=_tool_summary(name, block.input or {}))
            elif isinstance(msg, ResultMessage):
                ok = not msg.is_error
                structured = msg.structured_output
                final_text = (msg.result or "").strip() or last_text
                cost = f" · Kosten ${msg.total_cost_usd:.2f}" if getattr(msg, "total_cost_usd", None) else ""
                job.emit(type="result", text=f"{'Fertig' if ok else 'Abgebrochen'} nach {msg.num_turns} Schritten{cost}")
    except asyncio.CancelledError:
        job.emit(type="error", text="Abgebrochen.")
    except Exception as exc:  # surface everything in the UI
        job.emit(type="error", text=f"{type(exc).__name__}: {exc}")

    if finish is not None:
        await finish(job, ok, final_text)
        job.done = True
        return

    if output_format is not None:  # commander finder: structured suggestions instead of a deck
        suggestions = await _enrich_suggestions(structured) if ok else []
        if suggestions:
            job.emit(type="suggestions", items=suggestions)
        elif ok:
            job.emit(type="error", text="Keine verwertbaren Vorschläge erhalten.")
        job.emit(type="done", ok=bool(suggestions), deck=None)
        job.done = True
        return

    deck = next((d for d in storage.list_decks() if (d.get("updated") or "") >= _iso(job.started)), None)
    job.emit(type="done", ok=ok and deck is not None, deck=deck["slug"] if deck else None)
    job.done = True


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts - 1))


def _start(prompt: str, model: str | None, output_format: dict[str, Any] | None = None, **kw: Any) -> dict[str, str]:
    job = Job(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job
    job.task = asyncio.create_task(_run_claude(job, prompt, model, output_format, **kw))
    return {"job": job.id}


class BuildRequest(BaseModel):
    commander: str
    partner: str | None = None
    bracket: int = Field(3, ge=1, le=5)
    budget: float | None = None
    proxy: bool = False
    currency: str = "eur"
    strategy: str | None = None
    notes: str | None = None
    profile: PowerProfile | None = None
    model: str | None = None


def profile_lines(bracket: int, profile: PowerProfile | None) -> list[str]:
    """Prompt lines for sub-tier, house rules and style; empty when nothing was chosen."""
    if not profile or profile.is_empty():
        return []
    lines = []
    if profile.tier:
        lines.append(
            f"- Feinstufe: {TIER_LABELS[profile.tier]} Bracket {bracket} (Power-Ziel ≈ {target_value(bracket, profile.tier):.1f})"
        )
    rules = []
    if profile.max_game_changers is not None:
        rules.append("keine Game Changer" if profile.max_game_changers == 0 else f"max. {profile.max_game_changers} Game Changer")
    if profile.max_tutors is not None:
        rules.append("keine Tutoren" if profile.max_tutors == 0 else f"max. {profile.max_tutors} Tutoren")
    for flag, text in (
        (profile.allow_two_card_combos, "2-Karten-Combos"),
        (profile.allow_extra_turns, "Extra Turns"),
        (profile.allow_mass_land_denial, "Mass Land Denial"),
    ):
        if flag is False:
            rules.append(f"keine {text}")
    if rules:
        lines.append("- Hausregeln (strenger als das Bracket): " + "; ".join(rules))
    if profile.style:
        lines.append(f"- Stil/Vibe: {profile.style}")
    if profile.notes:
        lines.append(f"- Notizen zur Spielstärke: {profile.notes}")
    lines.append(
        "- Übergib dieses `power_profile` an `validate_deck` und `save_deck` und lies references/power-tuning.md: "
        + profile.model_dump_json(exclude_defaults=True)
    )
    return lines


def _budget_line(budget: float | None, proxy: bool, currency: str) -> str:
    cur = currency.upper()
    if proxy:
        return (
            "- Proxy: Ja – das Deck wird geproxt. Kartenpreise spielen keine Rolle, ignoriere jedes Budget "
            f"und wähle die besten Karten für Bracket und Strategie (Preise nur informativ in {cur}). "
            "Übergib `proxy=true` an `validate_deck` und `save_deck`."
        )
    if budget:
        return f"- Budget: max. {budget:g} {cur} für das ganze Deck (übergib `budget` an `validate_deck`/`save_deck`)"
    return f"- Budget: keins (Preise in {cur})"


def build_prompt(req: BuildRequest) -> str:
    b = brackets.BY_NUMBER[req.bracket]
    lines = [
        "Baue ein Commander-Deck. Nutze dafür den Skill `commander-deckbuilder`.",
        "",
        f"- Commander: {req.commander}",
    ]
    if req.partner:
        lines.append(f"- Partner/Background: {req.partner}")
    lines.append(f"- Bracket: {req.bracket} ({b['name']})")
    lines.append(_budget_line(req.budget, req.proxy, req.currency))
    lines.append(f"- Währung für Preise: {req.currency}")
    lines += profile_lines(req.bracket, req.profile)
    if req.strategy:
        lines.append(f"- Strategie/Thema: {req.strategy}")
    if req.notes:
        lines.append(f"- Weitere Wünsche: {req.notes}")
    lines += [
        "",
        "Du läufst im GUI-Modus: Stelle keine Rückfragen, triff sinnvolle Annahmen und nenne sie in der Deckbeschreibung.",
        "Karten auf der Blacklist (`get_blacklist`) sind tabu.",
        "Speichere das fertige Deck mit `save_deck` und behebe alle Fehler und Bracket-Verstöße, bevor du fertig bist.",
        "Antworte auf Deutsch.",
    ]
    return "\n".join(lines)


# --- commander finder --------------------------------------------------------------------------

SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact English card name of the commander"},
                    "partner": {"type": "string", "description": "Partner/background if the suggestion is a pair, else empty"},
                    "archetype": {"type": "string", "description": "Short strategy label, e.g. 'Aristocrats', 'Voltron'"},
                    "why": {"type": "string", "description": "2-3 sentences in German why it fits the wish"},
                    "strategy": {"type": "string", "description": "Suggested strategy text for the deck build form"},
                    "bracket_fit": {"type": "string", "description": "How it plays in the requested bracket"},
                },
                "required": ["name", "archetype", "why"],
            },
        }
    },
    "required": ["suggestions"],
}


class FinderRequest(BaseModel):
    prompt: str
    bracket: int | None = Field(None, ge=1, le=5)
    budget: float | None = None
    proxy: bool = False
    currency: str = "eur"
    count: int = Field(5, ge=1, le=10)
    model: str | None = None


def finder_prompt(req: FinderRequest) -> str:
    lines = [
        "Finde passende Commander. Nutze dafür den Skill `commander-finder`.",
        "",
        f"- Wunsch des Spielers: {req.prompt}",
        f"- Anzahl Vorschläge: {req.count}",
    ]
    if req.bracket:
        lines.append(f"- Ziel-Bracket: {req.bracket} ({brackets.BY_NUMBER[req.bracket]['name']})")
    lines.append(_budget_line(req.budget, req.proxy, req.currency).replace("`validate_deck`/`save_deck`", "die Deck-Planung"))
    lines += [
        "",
        "Du läufst im GUI-Modus: keine Rückfragen. Baue KEIN Deck und speichere nichts.",
        "Gib die Vorschläge als strukturierte Ausgabe zurück (exakte englische Kartennamen), Begründungen auf Deutsch.",
    ]
    return "\n".join(lines)


async def _enrich_suggestions(structured: Any) -> list[dict[str, Any]]:
    items = (structured or {}).get("suggestions") if isinstance(structured, dict) else None
    if not items:
        return []
    names = [i["name"] for i in items if i.get("name")] + [i["partner"] for i in items if i.get("partner")]
    try:
        cards, renames, _ = await resolve(names)
    except Exception:
        cards, renames = {}, {}
    out = []
    for item in items:
        name = renames.get(item.get("name", ""), item.get("name", ""))
        c = cards.get(name, {})
        out.append(
            {
                **item,
                "name": name,
                "partner": renames.get(item.get("partner") or "", item.get("partner") or ""),
                "image": c.get("image"),
                "color_identity": c.get("color_identity"),
                "type_line": c.get("type_line"),
                "price_eur": c.get("price_eur"),
                "price_usd": c.get("price_usd"),
            }
        )
    return out


class RetuneRequest(BaseModel):
    slug: str
    bracket: int = Field(ge=1, le=5)
    profile: PowerProfile = PowerProfile()
    request: str | None = None
    model: str | None = None


def retune_prompt(req: RetuneRequest, deck: dict[str, Any]) -> str:
    old_profile = deck.get("power_profile") or {}
    old_tier = old_profile.get("tier")
    old = f"{TIER_LABELS[old_tier]} Bracket {deck.get('bracket')}" if old_tier else f"Bracket {deck.get('bracket')}"
    power = ((deck.get("validation") or {}).get("bracket") or {}).get("power") or {}
    new = f"{TIER_LABELS[req.profile.tier]} Bracket {req.bracket}" if req.profile.tier else f"Bracket {req.bracket}"
    lines = [
        f"Stimme das gespeicherte Commander-Deck `{req.slug}` neu ab (lade es mit `load_deck`).",
        "Nutze den Skill `commander-deckbuilder`, Abschnitt „Bracket rauf/runter & Feinabstimmung“, und references/power-tuning.md.",
        "",
        f"- Bisher: {old}" + (f" (Power-Score {power.get('value')}, {power.get('text')})" if power else ""),
        f"- Ziel: {new}",
        f"- Bracket: {req.bracket} ({brackets.BY_NUMBER[req.bracket]['name']})",
        *profile_lines(req.bracket, req.profile),
        "- " + _budget_line(deck.get("budget"), bool(deck.get("proxy")), deck.get("currency", "eur")).lstrip("- "),
    ]
    if req.request:
        lines.append(f"- Zusätzlicher Wunsch: {req.request}")
    lines += [
        "",
        "Du läufst im GUI-Modus: keine Rückfragen. Commander bleibt gleich. Karten auf der Blacklist (`get_blacklist`) sind tabu.",
        f"Speichere mit `save_deck` (slug=`{req.slug}`, neues `bracket`, `power_profile`) und einer `change_note`, "
        "die alte → neue Stufe, Power-Score vorher/nachher und die wichtigsten Tausche mit Grund nennt.",
        "Fasse die Änderungen (+ rein / - raus) kurz zusammen. Antworte auf Deutsch.",
    ]
    return "\n".join(lines)


class RefineRequest(BaseModel):
    slug: str
    request: str
    model: str | None = None


def refine_prompt(req: RefineRequest, deck: dict[str, Any] | None = None) -> str:
    deck = deck or {}
    budget = _budget_line(deck.get("budget"), bool(deck.get("proxy")), deck.get("currency", "eur"))
    return "\n".join(
        [
            f"Überarbeite das gespeicherte Commander-Deck `{req.slug}` (lade es mit `load_deck`).",
            f"Bisherige Vorgaben: {budget.lstrip('- ')}",
            "Karten auf der Blacklist (`get_blacklist`) sind tabu.",
            "Nutze den Skill `commander-deckbuilder` (Abschnitt „Deck überarbeiten“).",
            f"Änderungswunsch: {req.request}",
            "",
            "Du läufst im GUI-Modus: keine Rückfragen. Behalte Bracket und Commander bei, sofern nicht anders gewünscht.",
            f"Speichere das Ergebnis mit `save_deck` und slug=`{req.slug}`. Fasse die Änderungen (rein/raus) kurz zusammen. Antworte auf Deutsch.",
        ]
    )


CARD_REF = re.compile(r"\[\[([^\[\]]{2,141})\]\]")


async def _card_refs(text: str) -> dict[str, dict[str, Any]]:
    """Images for the [[Card Name]] references of an answer (hover preview in the GUI)."""
    names = list(dict.fromkeys(m.strip() for m in CARD_REF.findall(text)))[:80]
    if not names:
        return {}
    try:
        cards, renames, _ = await resolve(names)
    except Exception:
        return {}
    out = {}
    for name in names:
        c = cards.get(renames.get(name, name))
        if c and c.get("image"):
            out[name] = {"image": c["image"], "image_back": c.get("image_back"), "scryfall_uri": c.get("scryfall_uri")}
    return out


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    model: str | None = None


ASK_HISTORY = 4  # earlier questions passed along for follow-ups ("und gegen Kinnan?")


def ask_prompt(question: str, deck: dict[str, Any], history: list[dict[str, Any]] | None = None) -> str:
    about = [f"„{deck.get('name', deck['slug'])}“", "Commander: " + (" + ".join(deck.get("commanders") or []) or "?"),
             storage.level_text(deck)] + (["Proxy-Deck"] if deck.get("proxy") else [])  # fmt: skip
    lines = [
        f"Beantworte eine Frage zum gespeicherten Commander-Deck `{deck['slug']}` ({', '.join(about)}).",
        "Lade es mit `load_deck` und nutze den Skill `commander-deckbuilder`, Abschnitt „Fragen zum Deck“ "
        "(references/deck-questions.md).",
        "",
        "Regeln:",
        "- Nur lesen: Das Deck wird nicht verändert oder gespeichert. Läuft die Frage auf Änderungen hinaus, "
        "schlage konkrete Tausche vor (+ rein / − raus, jeweils mit Grund); umsetzen kann man sie über „Anpassen“.",
        "- Du läufst im GUI-Modus: keine Rückfragen. Triff sinnvolle Annahmen und nenne sie kurz.",
        "- Stütze Aussagen auf die Tools (Oracle-Text über `get_cards`, Combos über `find_combos`, "
        "Gegner-Commander über `edhrec_average_deck`/`edhrec_recommendations`) statt auf dein Gedächtnis.",
        "- Schreibe Kartennamen als [[Kartenname]] (englischer Oracle-Name).",
        "- Antworte auf Deutsch in Markdown: Kernaussage zuerst, dann kurze Absätze oder Listen. "
        "Keine Vorrede über deine Arbeitsschritte.",
    ]
    earlier = (history or [])[-ASK_HISTORY:]
    if earlier:
        lines += ["", "Bisherige Fragen zu diesem Deck (nur zur Einordnung von Anschlussfragen):"]
        for h in earlier:
            answer = h.get("answer", "")
            answer = answer if len(answer) <= 1200 else answer[:1200] + " …"
            lines += [f"Frage: {h.get('question', '')}", f"Antwort: {answer}", ""]
    lines += ["", f"Frage: {question.strip()}"]
    return "\n".join(lines)


# --- routes ------------------------------------------------------------------------------------


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/brackets")
async def api_brackets() -> list[dict[str, Any]]:
    return brackets.describe()


@app.get("/api/autocomplete")
async def api_autocomplete(q: str) -> list[str]:
    try:
        return await scryfall.autocomplete(q)
    except HttpError:
        return []


@app.get("/api/card")
async def api_card(name: str) -> dict[str, Any]:
    try:
        cards, _, missing = await resolve([name])
    except Exception as exc:
        raise HTTPException(503, f"Kartendaten nicht erreichbar: {exc}") from exc
    if missing or not cards:
        raise HTTPException(404, f"Karte nicht gefunden: {name}")
    return next(iter(cards.values()))


@app.post("/api/build")
async def api_build(req: BuildRequest) -> dict[str, str]:
    return _start(build_prompt(req), req.model)


@app.post("/api/refine")
async def api_refine(req: RefineRequest) -> dict[str, str]:
    try:
        deck = storage.load(req.slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _start(refine_prompt(req, deck), req.model)


@app.post("/api/retune")
async def api_retune(req: RetuneRequest) -> dict[str, str]:
    try:
        deck = storage.load(req.slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _start(retune_prompt(req, deck), req.model)


@app.get("/api/decks/{slug}/questions")
async def api_questions(slug: str) -> list[dict[str, Any]]:
    return storage.questions(slug)


@app.post("/api/decks/{slug}/ask")
async def api_ask(slug: str, req: AskRequest) -> dict[str, str]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    question = req.question.strip()

    async def finish(job: Job, ok: bool, answer: str) -> None:
        if ok and answer:
            cards = await _card_refs(answer)
            entry = storage.add_question(slug, question, answer, version=deck.get("version"), cards=cards)
            job.emit(type="answer", entry=entry)
        elif ok:
            job.emit(type="error", text="Keine Antwort erhalten.")
        job.emit(type="done", ok=bool(ok and answer), deck=None)

    prompt = ask_prompt(question, deck, storage.questions(slug))
    return _start(prompt, req.model, read_only=True, finish=finish)


@app.delete("/api/decks/{slug}/questions")
async def api_questions_delete(slug: str, id: str | None = None) -> dict[str, int]:
    return {"deleted": storage.delete_questions(slug, id)}


@app.post("/api/find-commander")
async def api_find_commander(req: FinderRequest) -> dict[str, str]:
    output_format = {"type": "json_schema", "schema": SUGGESTION_SCHEMA}
    return _start(finder_prompt(req), req.model, output_format)


class BlacklistUpdate(BaseModel):
    add: list[str] = []
    remove: list[str] = []


@app.get("/api/blacklist")
async def api_blacklist() -> list[str]:
    return blacklist.load()


@app.post("/api/blacklist")
async def api_blacklist_update(req: BlacklistUpdate) -> dict[str, Any]:
    return await blacklist.update(req.add, req.remove)


@app.get("/api/jobs/{job_id}/events")
async def api_job_events(job_id: str, request: Request) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unbekannter Job")
    start = int(request.headers.get("last-event-id", -1)) + 1

    async def stream():
        i = start
        while True:
            while i < len(job.events):
                yield f"id: {i}\ndata: {json.dumps(job.events[i], ensure_ascii=False)}\n\n"
                i += 1
            if job.done or await request.is_disconnected():
                return
            job.changed.clear()
            try:
                await asyncio.wait_for(job.changed.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(job_id: str) -> dict[str, bool]:
    job = JOBS.get(job_id)
    if job and isinstance(job.proc, terminal.Terminal):
        job.proc.terminate()
    if job and job.task and not job.done:
        job.task.cancel()
    return {"ok": True}


@app.get("/api/decks")
async def api_decks() -> list[dict[str, Any]]:
    return storage.list_decks()


@app.get("/api/decks/{slug}")
async def api_deck(slug: str) -> dict[str, Any]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    names = deck.get("commanders", []) + [c["name"] for c in deck.get("cards", [])]
    try:
        card_data, _, _ = await resolve(names)
    except Exception as exc:  # offline: show the list without images/prices
        card_data = {}
        deck["card_data_error"] = str(exc)
    keep = ("image", "image_back", "layout", "type_line", "mana_cost", "cmc", "price_eur", "price_usd",
            "game_changer", "scryfall_uri")  # fmt: skip
    deck["card_data"] = {n: {k: c.get(k) for k in keep} for n, c in card_data.items()}
    entries = [DeckEntry(c["name"], c.get("qty", 1)) for c in deck.get("cards", [])]
    deck["export_text"] = to_text(deck.get("commanders", []), entries)
    return deck


@app.post("/api/decks/{slug}/validate")
async def api_validate(slug: str) -> dict[str, Any]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    lines = [f"{c.get('qty', 1)} {c['name']}" for c in deck.get("cards", [])]
    result = await validate_deck(
        deck["commanders"], lines, int(deck.get("bracket") or 3), currency=deck.get("currency", "eur"),
        budget=deck.get("budget"), proxy=bool(deck.get("proxy")),
        profile=PowerProfile(**deck["power_profile"]) if deck.get("power_profile") else None,
    )  # fmt: skip
    result.pop("_card_data", None)
    result.pop("cards", None)
    deck["validation"] = result
    storage.save(deck)
    return result


@app.delete("/api/decks/{slug}")
async def api_delete(slug: str) -> dict[str, bool]:
    storage.delete(slug)
    return {"ok": True}


def _not_found(fn, *args):
    try:
        return fn(*args)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/decks/{slug}/versions")
async def api_versions(slug: str) -> list[dict[str, Any]]:
    return _not_found(storage.versions, slug)


@app.get("/api/decks/{slug}/versions/{version}")
async def api_version(slug: str, version: int) -> dict[str, Any]:
    deck = _not_found(storage.load_version, slug, version)
    entries = [DeckEntry(c["name"], c.get("qty", 1)) for c in deck.get("cards", [])]
    return {**deck, "export_text": to_text(deck.get("commanders", []), entries)}


@app.get("/api/decks/{slug}/diff")
async def api_diff(slug: str, a: int, b: int | None = None) -> dict[str, Any]:
    return _not_found(storage.compare, slug, a, b)


@app.post("/api/decks/{slug}/versions/{version}/restore")
async def api_restore(slug: str, version: int) -> dict[str, Any]:
    return _not_found(storage.restore, slug, version)


class CopyRequest(BaseModel):
    name: str | None = None
    version: int | None = None


@app.post("/api/decks/{slug}/copy")
async def api_copy(slug: str, req: CopyRequest) -> dict[str, Any]:
    return _not_found(storage.copy, slug, req.name, req.version)


# --- proxies (MPC Autofill) & settings ---------------------------------------------------------


class SettingsUpdate(BaseModel):
    autofill_path: str | None = None
    mpcfill_server: str | None = None
    cardback_path: str | None = None
    browser: str | None = None
    site: str | None = None
    stock: str | None = None
    foil: bool | None = None
    paper: str | None = None
    mpcfill_cdn: str | None = None
    upscale: bool | None = None
    upscaler_path: str | None = None
    upscale_model: str | None = None
    descreen: str | None = None


def _settings_view(cfg: dict[str, Any]) -> dict[str, Any]:
    found, upscaler = proxy.find_autofill(cfg), proxy.find_upscaler(cfg)
    return {
        **cfg,
        "autofill_found": str(found) if found else None,
        "upscaler_found": str(upscaler) if upscaler else None,
        "upscale_models": proxy.upscale_models(upscaler) or ["realesrgan-x4plus"],
        "stocks": proxy.STOCKS,
        "platform": os.name,
    }


@app.get("/api/settings")
async def api_settings() -> dict[str, Any]:
    return _settings_view(settings_mod.load())


@app.post("/api/settings")
async def api_settings_update(req: SettingsUpdate) -> dict[str, Any]:
    return _settings_view(settings_mod.update(req.model_dump(exclude_none=True)))


class PrintRequest(BaseModel):
    source: str = "auto"
    stock: str | None = None
    foil: bool | None = None
    upscale: bool | None = None
    version: int | None = None


def _print_deck(slug: str, version: int | None = None) -> dict[str, Any]:
    deck = _not_found(storage.load_version, slug, version)
    deck["slug"] = storage.slug(slug)
    return deck


@app.get("/api/decks/{slug}/print/plan")
async def api_print_plan(slug: str, source: str = "auto", version: int | None = None) -> dict[str, Any]:
    try:
        return await proxy.plan(_print_deck(slug, version), source=source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/decks/{slug}/print/alternatives")
async def api_print_alternatives(slug: str, card: str, side: str = "front") -> list[dict[str, Any]]:
    try:
        return await proxy.alternatives(_print_deck(slug), card, "back" if side == "back" else "front")
    except HttpError as exc:
        raise HTTPException(502, str(exc)) from exc


class ChooseRequest(BaseModel):
    face: str
    option: dict[str, Any] | None = None


@app.post("/api/decks/{slug}/print/choose")
async def api_print_choose(slug: str, req: ChooseRequest) -> dict[str, Any]:
    return proxy.choose(storage.slug(slug), req.face, req.option)


@app.post("/api/decks/{slug}/print/prepare")
async def api_print_prepare(slug: str, req: PrintRequest) -> dict[str, str]:
    deck = _print_deck(slug, req.version)

    async def runner(job: Job) -> None:
        job.emit(type="status", text="Plane Bilder …")

        def progress(done: int, total: int, name: str) -> None:
            job.emit(type="progress", done=done, total=total, text=name)

        try:
            result = await proxy.prepare(
                deck, source=req.source, stock=req.stock, foil=req.foil, upscale=req.upscale, progress=progress
            )
        except ValueError as exc:
            job.emit(type="error", text=str(exc))
            job.emit(type="done", ok=False)
            return
        for w in result["warnings"]:
            job.emit(type="status", text=w)
        for e in result["errors"]:
            job.emit(type="error", text=e)
        job.emit(type="result", text=(
            f"{result['quantity']} Karten vorbereitet ({result['images_mpcfill']} MPC-Autofill-Scans, "
            f"{result['images_scryfall']} Scryfall-Scans"
            f"{', davon ' + str(result['images_upscaled']) + ' KI-hochskaliert' if result['upscaled'] else ''}) · {result['cardback']}"
        ))  # fmt: skip
        job.emit(type="print", result=result)
        job.emit(type="done", ok=not result["missing"])

    return _start_runner(runner)


@app.api_route("/api/decks/{slug}/print/files/{kind}", methods=["GET", "HEAD"])
async def api_print_file(slug: str, kind: str) -> FileResponse:
    s = storage.slug(slug)
    path = proxy.order_dir(s) / f"{s}.{'pdf' if kind == 'pdf' else 'xml'}"
    if not path.exists():
        raise HTTPException(404, "Datei noch nicht erstellt")
    return FileResponse(path, filename=path.name)


@app.get("/api/decks/{slug}/print/prepared")
async def api_print_prepared(slug: str) -> dict[str, Any]:
    info = proxy.load_prepared(storage.slug(slug))
    return info or {"faces": {}, "images_dir": None}


@app.get("/api/decks/{slug}/print/image")
async def api_print_image(slug: str, face: str, kind: str = "file") -> Response:
    """kind: original (as downloaded) | file (print file with bleed) | trim (print file without bleed)."""
    try:
        path = proxy.prepared_image(storage.slug(slug), face, kind)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if kind != "trim":
        return FileResponse(path)

    def cropped() -> bytes:
        from io import BytesIO

        from PIL import Image

        from .. import imaging

        with Image.open(path) as im:
            buf = BytesIO()
            imaging.crop_bleed(im.convert("RGB")).save(buf, "JPEG", quality=92)
            return buf.getvalue()

    return Response(await asyncio.to_thread(cropped), media_type="image/jpeg")


def open_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@app.post("/api/decks/{slug}/print/open-folder")
async def api_print_open_folder(slug: str) -> dict[str, Any]:
    folder = proxy.order_dir(storage.slug(slug)) / "images"
    if not folder.is_dir():
        raise HTTPException(404, "Noch keine Druckdateien – zuerst „Druckdateien vorbereiten“.")
    try:
        open_in_file_manager(folder)
    except OSError as exc:
        return {"opened": False, "path": str(folder), "error": str(exc)}
    return {"opened": True, "path": str(folder)}


class PdfRequest(BaseModel):
    paper: str = "A4"
    include_backs: bool = True
    cut_marks: bool = True


@app.post("/api/decks/{slug}/print/pdf")
async def api_print_pdf(slug: str, req: PdfRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            proxy.export_pdf, storage.slug(slug), paper=req.paper, include_backs=req.include_backs, cut_marks=req.cut_marks
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


class AutofillRequest(BaseModel):
    mode: str = "mpc"
    window: bool = False  # True = own console window instead of the terminal in the GUI
    rows: int = 32
    cols: int = 110


@app.post("/api/decks/{slug}/print/autofill")
async def api_print_autofill(slug: str, req: AutofillRequest) -> dict[str, Any]:
    folder = proxy.order_dir(storage.slug(slug))
    mode = "pdf" if req.mode == "pdf" else "mpc"
    try:
        proxy._check_order(folder)
        if req.window or not terminal.available():
            started = proxy.launch_autofill(folder, mode=mode)
            return {**started, "window": True}
        cmd = proxy.autofill_command(folder, mode=mode)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    async def runner(job: Job) -> None:
        job.emit(type="status", text="Starte MPC Autofill … Bedienung direkt im Terminal unten (Pfeiltasten + Enter).")
        term = terminal.Terminal(cmd, cwd=str(folder), rows=req.rows, cols=req.cols)
        job.proc = term
        job.emit(type="console", running=True)

        def pump() -> None:
            while data := term.read():
                job.emit_threadsafe(type="term", data=data)

        await asyncio.to_thread(pump)
        code = await asyncio.to_thread(term.exit_code)
        job.emit(type="console", running=False)
        job.emit(type="result", text=f"MPC Autofill beendet (Code {code}).")
        job.emit(type="done", ok=code == 0)

    return {**_start_runner(runner), "window": False}


class InputRequest(BaseModel):
    text: str = ""
    raw: bool = False  # True: keystrokes from the terminal as-is; False: a line + Enter


@app.post("/api/jobs/{job_id}/input")
async def api_job_input(job_id: str, req: InputRequest) -> dict[str, bool]:
    job = JOBS.get(job_id)
    if not job or not isinstance(job.proc, terminal.Terminal) or not job.proc.alive():
        raise HTTPException(409, "Kein laufender Prozess für Eingaben")
    await asyncio.to_thread(job.proc.write, req.text if req.raw else req.text + "\r")
    return {"ok": True}


class ResizeRequest(BaseModel):
    rows: int = Field(ge=5, le=300)
    cols: int = Field(ge=20, le=500)


@app.post("/api/jobs/{job_id}/resize")
async def api_job_resize(job_id: str, req: ResizeRequest) -> dict[str, bool]:
    job = JOBS.get(job_id)
    if job and isinstance(job.proc, terminal.Terminal) and job.proc.alive():
        job.proc.resize(req.rows, req.cols)
    return {"ok": True}


@app.get("/api/carddb")
async def api_carddb() -> dict[str, Any]:
    return {**carddb.status(), "needs_refresh": carddb.needs_refresh(), "bulk_type": carddb.BULK_TYPE}


_db_task: asyncio.Task | None = None


@app.post("/api/carddb/refresh")
async def api_carddb_refresh() -> dict[str, Any]:
    global _db_task
    if _db_task is None or _db_task.done():
        _db_task = asyncio.create_task(carddb.refresh(force=True))
    return {"started": True}


@app.get("/api/carddb/refresh")
async def api_carddb_refresh_state() -> dict[str, Any]:
    if _db_task is None:
        return {"running": False}
    if not _db_task.done():
        return {"running": True}
    exc = _db_task.exception()
    return {"running": False, "error": str(exc) if exc else None}


def main() -> None:
    import uvicorn

    host = os.environ.get("MTG_GUI_HOST", "127.0.0.1")
    port = int(os.environ.get("MTG_GUI_PORT", 8765))
    print(f"Commander Deckbuilder GUI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
