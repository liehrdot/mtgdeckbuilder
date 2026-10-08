"""Local web GUI. Builds decks by running Claude Code (Claude Agent SDK) with the project's
skills and the 'mtg' MCP server, and shows saved decks with images, stats and bracket check.

Start: ``uv run mtg-gui`` -> http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import opponents as opponents_mod
from .. import overview
from ..jsonstore import ConflictError, StoreError, write_json
from .. import backup, blacklist, brackets, chat, carddb, collection, deckedit, deckimport, deskmat, exports, importers, games, glossary, health, opponents, precons, printorders, proxy, rule0, scryfall, storage, tablerules
from .. import settings as settings_mod
from ..cards import card_text, deck_tokens, resolve
from ..deck import DeckEntry, to_text
from ..fmt import money as fmt_money
from ..http import HttpError
from ..power import TIER_LABELS, PowerProfile, target_value
from . import terminal

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
PROJECT_ROOT = storage.PROJECT_ROOT

app = FastAPI(title="MTG Commander Deckbuilder")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _local_only() -> bool:
    """Only when bound to this computer: a GUI opened to the network (MTG_GUI_HOST=0.0.0.0) accepts any host."""
    return os.environ.get("MTG_GUI_HOST", "127.0.0.1") in ("127.0.0.1", "localhost", "::1")


@app.middleware("http")
async def _guard(request: Request, call_next: Any) -> Any:
    """Protection against web pages that talk to the local GUI (DNS rebinding, cross-site POSTs): the Host must
    be this computer, and changing requests must not come from another site's page."""
    if _local_only():
        from urllib.parse import urlsplit

        host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
        if host not in _LOCAL_HOSTS:
            return JSONResponse({"detail": "Unbekannter Host"}, status_code=400)
        origin = request.headers.get("origin")
        if origin and request.method not in _SAFE_METHODS and (urlsplit(origin).hostname or "") not in _LOCAL_HOSTS:
            return JSONResponse({"detail": "Anfrage von einer fremden Seite abgelehnt"}, status_code=403)
    return await call_next(request)


@app.exception_handler(StoreError)
async def _store_error(_request: Request, exc: StoreError) -> JSONResponse:
    """Damaged/locked data files and edit conflicts: a clear message instead of a bare 500."""
    return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, ConflictError) else 503)


@app.exception_handler(HttpError)
async def _http_error(_request: Request, exc: HttpError) -> JSONResponse:
    """Scryfall, EDHREC … unreachable or failing: say which service and what to do, not a bare 500."""
    return JSONResponse({"detail": exc.friendly}, status_code=502)


def _error_text(exc: BaseException) -> str:
    """A message for the user: friendly for known failures, the exception type for real bugs."""
    if isinstance(exc, HttpError):
        return exc.friendly
    if isinstance(exc, (StoreError, LookupError, ValueError)) and not isinstance(exc, (KeyError, IndexError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


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
    ended: float | None = None

    def emit(self, **event: Any) -> None:
        self.events.append(event)
        self.changed.set()

    def emit_threadsafe(self, **event: Any) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(lambda: self.emit(**event))

    def close(self) -> None:
        """End the job. One that never said ``done`` (e.g. a crash in its last step) says it now, so the browser
        never waits forever."""
        if not any(e.get("type") == "done" for e in self.events):
            self.emit(type="done", ok=False)
        self.done = True
        self.ended = time.time()
        self.changed.set()


JOBS: dict[str, Job] = {}
JOB_KEEP_SECONDS = 3600  # finished jobs stay replayable this long (page reload, reconnect)


def _register(job: Job, work: Awaitable[None]) -> dict[str, str]:
    """Run ``work`` for ``job`` in the background; whatever happens, the job ends with a ``done`` event."""
    now = time.time()
    for old in [j for j in JOBS.values() if j.done and now - (j.ended or now) > JOB_KEEP_SECONDS]:
        JOBS.pop(old.id, None)
    JOBS[job.id] = job

    async def supervised() -> None:
        try:
            await work
        except asyncio.CancelledError:
            job.emit(type="error", text="Abgebrochen.")
        except Exception as exc:
            log.exception("Job %s failed", job.id)
            job.emit(type="error", text=_error_text(exc))
        finally:
            job.close()

    job.task = asyncio.create_task(supervised())
    return {"job": job.id}


def _start_runner(runner: Any) -> dict[str, str]:
    """Run ``runner(job)`` as a background job whose events stream to the browser."""
    job = Job(id=uuid.uuid4().hex[:12], loop=asyncio.get_running_loop())
    return _register(job, runner(job))


def _tool_summary(name: str, args: dict[str, Any]) -> str:
    for key in ("query", "commander", "name", "names", "tags", "url", "slug", "skill"):
        if key in args and args[key]:
            val = args[key]
            if isinstance(val, list):
                val = ", ".join(map(str, val[:5])) + (" …" if len(val) > 5 else "")
            return f"{key}={val}"
    return ""


# Tools for questions about a deck: research only, nothing that saves or changes anything.
READ_ONLY_TOOLS = ["app_overview", "search_cards", "local_card_search", "get_cards", "find_commanders", "game_changers",
                   "card_db_status", "edhrec_recommendations", "edhrec_average_deck", "find_combos",
                   "bracket_rules", "validate_deck", "get_blacklist", "table_rules", "opponent_decks", "list_decks", "load_deck", "deck_games",
                   "list_deck_versions", "compare_deck_versions", "export_deck", "import_deck", "search_precons", "print_orders",
                   "similar_cards", "collection_search", "collection_status"]  # fmt: skip
WRITE_TOOLS = ["save_deck", "update_blacklist", "update_table_rule", "update_opponent_deck", "restore_deck_version", "copy_deck", "update_card_database",
               "create_proxy_order", "export_proxy_pdf", "launch_proxy_tool", "proxy_settings",
               "edit_deck", "update_collection", "import_precon", "create_deskmat", "update_print_order"]  # fmt: skip
Finish = Callable[[Job, bool, str, Any], Awaitable[None]]


async def _run_claude(
    job: Job,
    prompt: str,
    model: str | None,
    output_format: dict[str, Any] | None = None,
    *,
    read_only: bool = False,
    finish: Finish | None = None,
    table_rule: str | None = None,
    extras: dict[str, Any] | None = None,
    effort: str | None = None,
    web: bool = False,
) -> None:
    """Run Claude Code on ``prompt``. ``read_only`` restricts the mtg tools to research;
    ``finish(job, ok, final_text, structured_output)`` replaces the default end (detect the saved deck)."""
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
        job.emit(type="error", text="Das KI-Paket fehlt (claude-agent-sdk): `uv sync --extra gui` ausführen.")
        job.emit(type="done", ok=False)
        return

    base_tools = ["Skill", "ToolSearch", "Read", "Glob", "Grep"] + (["WebSearch", "WebFetch"] if web else [])
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        setting_sources=["project"],  # loads .claude/skills and CLAUDE.md
        mcp_servers={"mtg": {"type": "stdio", "command": sys.executable, "args": ["-m", "mtgdeck.mcp_server"],
                             "env": {**os.environ, "MTG_JOB_ID": job.id}}},  # decks saved by this job carry last_job
        strict_mcp_config=True,
        allowed_tools=base_tools + ([f"mcp__mtg__{t}" for t in READ_ONLY_TOOLS] if read_only else ["mcp__mtg"]),
        disallowed_tools=(["Write", "Edit", "Bash"] + [f"mcp__mtg__{t}" for t in WRITE_TOOLS]) if read_only else [],
        permission_mode="dontAsk",
        model=model or None,
        effort=effort,
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
                if ok:
                    _ai_failure.clear()
                structured = msg.structured_output
                final_text = (msg.result or "").strip() or last_text
                cost = f" · Kosten {fmt_money(msg.total_cost_usd, 'usd')}" if getattr(msg, "total_cost_usd", None) else ""
                job.emit(type="result", text=f"{'Fertig' if ok else 'Abgebrochen'} nach {msg.num_turns} Schritten{cost}")
    except asyncio.CancelledError:
        job.emit(type="error", text="Abgebrochen.")
    except Exception as exc:  # surface everything in the UI
        _note_ai_failure(exc)
        job.emit(type="error", text=_error_text(exc) + (" – Claude Code ist nicht bereit: installiert und angemeldet? "
                 "Ohne KI kannst du weiter drucken, importieren und bearbeiten." if _ai_failure else ""))

    if finish is not None:
        await finish(job, ok, final_text, structured)
        return

    if output_format is not None:  # commander finder: structured suggestions instead of a deck
        suggestions = await _enrich_suggestions(structured) if ok else []
        if suggestions:
            job.emit(type="suggestions", items=suggestions)
        elif ok:
            job.emit(type="error", text="Keine verwertbaren Vorschläge erhalten.")
        job.emit(type="done", ok=bool(suggestions), deck=None)
        return

    decks = storage.list_decks()
    deck = next((d for d in decks if d.get("last_job") == job.id), None)  # saved by this job's MCP server
    if deck is None and not any(d.get("last_job") for d in decks if (d.get("updated") or "") >= _iso(job.started)):
        deck = next((d for d in decks if (d.get("updated") or "") >= _iso(job.started)), None)  # older MCP server
    for key, value in (extras or {}).items() if deck else []:  # e.g. which opponents a meta build targeted
        try:
            storage.set_extra(deck["slug"], key, value)
        except (FileNotFoundError, ValueError):
            pass
    if deck and table_rule and deck.get("table_rule") != table_rule:  # Claude forgot to pass it on
        try:
            await _set_table_rule(deck["slug"], table_rule)
        except Exception as exc:
            job.emit(type="error", text=f"Tischregel konnte nicht gesetzt werden: {exc}")
    job.emit(type="done", ok=ok and deck is not None, deck=deck["slug"] if deck else None)


async def _set_table_rule(slug: str, rule_id: str | None, note: str = "") -> dict[str, Any]:
    """Give a saved deck a table rule (or none), re-validate and save it as a new version."""
    for attempt in range(3):
        try:
            return await _set_table_rule_once(slug, rule_id, note)
        except ConflictError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


async def _set_table_rule_once(slug: str, rule_id: str | None, note: str) -> dict[str, Any]:
    deck = storage.load(slug)
    start_version = deck.get("version")
    rs = tablerules.get(rule_id) if rule_id else None
    if rule_id and rs is None:
        raise FileNotFoundError(f"Unbekannte Tischregel: {rule_id}")
    old = tablerules.get(deck.get("table_rule"))
    deck["table_rule"] = rs["id"] if rs else None
    await deckedit.revalidate(deck)
    deck["change_note"] = note or (f"Tischregel: {rs['name']}" if rs else "Tischregel entfernt" + (f" ({old['name']})" if old else ""))
    storage.save(deck, expect_version=start_version)
    return deck


def _deck_table_lines(deck: dict[str, Any]) -> list[str]:
    return tablerules.prompt_lines(tablerules.get(deck.get("table_rule")))


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts - 1))


# --- is Claude there? Everything without AI (printing, collection, import, editing …) works regardless ---------

_ai_failure: dict[str, Any] = {}  # the last run that failed because Claude Code itself was missing/not logged in
_AI_SETUP_HINTS = ("not found", "nicht gefunden", "login", "log in", "logged in", "authenticat", "api key", "api_key",
                   "credit balance", "unauthorized", "401", "claude code")  # fmt: skip


def _find_claude_cli() -> str | None:
    import shutil

    try:
        import claude_agent_sdk

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
        for name in ("claude", "claude.exe"):
            if (bundled / name).is_file():
                return str(bundled / name)
    except ImportError:
        return None
    return shutil.which("claude")


def ai_status() -> dict[str, Any]:
    """``{enabled, available, reason}`` – reason is a German sentence when AI features cannot run."""
    enabled = bool(settings_mod.load().get("ai_enabled", True))
    if not enabled:
        return {"enabled": False, "available": False, "reason": "KI-Funktionen sind in den Einstellungen ausgeschaltet."}
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return {"enabled": True, "available": False,
                "reason": "Das KI-Paket fehlt (claude-agent-sdk) – `uv sync --extra gui` ausführen."}  # fmt: skip
    if not _find_claude_cli():
        return {"enabled": True, "available": False,
                "reason": "Claude Code wurde nicht gefunden – installieren (claude.ai/code) und einmal `claude` zum Anmelden starten."}  # fmt: skip
    if _ai_failure:
        return {"enabled": True, "available": True, "reason": None, "last_error": _ai_failure.get("text"),
                "last_error_at": _ai_failure.get("at")}  # fmt: skip
    return {"enabled": True, "available": True, "reason": None}


def _require_ai() -> None:
    st = ai_status()
    if not st["available"]:
        raise HTTPException(503, f"{st['reason']} Alles ohne KI – Drucken, Sammlung, Import, Bearbeiten – funktioniert weiter.")


def _note_ai_failure(exc: BaseException) -> None:
    text = f"{type(exc).__name__}: {exc}"
    if type(exc).__name__ in ("CLINotFoundError", "CLIConnectionError") or any(h in text.lower() for h in _AI_SETUP_HINTS):
        _ai_failure.update(text=text[:300], at=time.time())


def _start(prompt: str, model: str | None, output_format: dict[str, Any] | None = None, **kw: Any) -> dict[str, str]:
    _require_ai()
    job = Job(id=uuid.uuid4().hex[:12])
    return _register(job, _run_claude(job, prompt, model, output_format, **kw))


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
    prefer_collection: bool = False
    table_rule: str | None = None
    model: str | None = None


COLLECTION_LINE = (
    "- Sammlung: Bevorzuge Karten, die der Nutzer schon besitzt – hol sie mit `collection_search` (Farbidentität "
    "des Commanders) und nimm sie, wo sie gleich gut passen. Besessene Karten (echt oder Proxy) kosten nichts; beim "
    "Budget zählen nur fehlende Karten. Nenne am Ende, wie viele Karten aus der Sammlung stammen (`collection_status`)."
)


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
        (profile.allow_extra_turns, "Extra-Züge"),
        (profile.allow_mass_land_denial, "Massen-Landzerstörung"),
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
    if req.prefer_collection:
        lines.append(COLLECTION_LINE)
    lines += tablerules.prompt_lines(tablerules.get(req.table_rule))
    if req.table_rule:  # the opponents of that table
        lines += opponents_mod.prompt_lines({"table_rule": req.table_rule})
    lines += [
        "",
        "Du läufst im GUI-Modus: Stelle keine Rückfragen, triff sinnvolle Annahmen und nenne sie in der Deckbeschreibung.",
        "Karten und Regeln der Blacklist (`get_blacklist`) sind tabu.",
        "Speichere das fertige Deck mit `save_deck` und behebe alle Fehler und Bracket-Verstöße, bevor du fertig bist.",
        "Antworte auf Deutsch.",
    ]
    return "\n".join(lines)


# --- meta build: the strongest deck against the user's playgroup ---------------------------------


class MetaBuildRequest(BaseModel):
    commander: str | None = None  # empty = Claude picks
    partner: str | None = None
    opponent_ids: list[str] = Field(default_factory=list)  # empty = all opponent decks
    bracket: int = Field(3, ge=1, le=5)
    budget: float | None = None
    proxy: bool = False
    currency: str = "eur"
    strategy: str | None = None
    notes: str | None = None
    profile: PowerProfile | None = None
    prefer_collection: bool = False
    table_rule: str | None = None
    model: str | None = None


def _meta_context(req: MetaBuildRequest, *, free: bool) -> list[str]:
    """The playgroup, the user's decks and the build options – shared by meta builds and meta suggestions."""
    b = brackets.BY_NUMBER[req.bracket]
    lines = [
        "Die Gegnerdecks der Runde (nur Commander, Merkmale, Beobachtungen und Bilanz – keine Listen):",
        *opponents_mod.meta_lines(req.opponent_ids),
        "",
        "Die eigenen Decks des Nutzers:",
        *(opponents_mod.own_deck_lines() or ["  - noch keine"]),
        "",
        "Vorgaben:",
        f"- Commander: {req.commander}" + (f" + {req.partner}" if req.partner else "") if not free else
        "- Commander: frei wählbar – wähle den, der gegen genau diese Gegner am besten steht (Tempo, Interaktion, "
        "Widerstandskraft) und der noch keinem eigenen Deck des Nutzers entspricht.",
        f"- Bracket: {req.bracket} ({b['name']})",
        _budget_line(req.budget, req.proxy, req.currency),
        f"- Währung für Preise: {req.currency}",
        *profile_lines(req.bracket, req.profile),
        *tablerules.prompt_lines(tablerules.get(req.table_rule)),
    ]
    if req.strategy:
        lines.append(f"- Strategie/Thema: {req.strategy}")
    if req.notes:
        lines.append(f"- Weitere Wünsche: {req.notes}")
    if req.prefer_collection:
        lines.append(COLLECTION_LINE)
    return lines


def meta_prompt(req: MetaBuildRequest) -> str:
    free = not (req.commander or "").strip()
    lines = [
        "Baue das stärkste Commander-Deck gegen die Runde des Nutzers. Nutze den Skill `commander-deckbuilder` "
        "(Abschnitt „Gegen die Runde bauen“)" + (" und für die Wahl des Commanders den Skill `commander-finder`." if free else "."),
        "",
        *_meta_context(req, free=free),
    ]
    lines += [
        "",
        "So gehst du vor:",
        "1. Lies die Runde: Wie gewinnen die Gegner, wie schnell, womit haben sie dem Nutzer Probleme gemacht? Typische Karten "
        "eines Gegner-Commanders liefert `edhrec_recommendations`, Details zu Gegnern `opponent_decks`, zu eigenen Partien `deck_games`.",
        "2. „Am stärksten“ heißt: die besten Chancen gegen genau diese Gegner – innerhalb von Bracket, Feinstufe, Hausregeln, "
        "Tischregel, Budget und Blacklist, nie darüber hinaus.",
        "3. Baue ein stimmiges Deck mit eigener Siegstrategie und gezielter Interaktion gegen die Bedrohungen der Runde – kein "
        "reines Hate-Deck, keine Karten nur gegen einen einzigen Gegner, wenn sie sonst tot sind.",
        "4. `description`: warum das Deck gegen diese Runde stark ist. `notes`: pro Gegner ein kurzer Spielplan "
        "(worauf achten, welche Antworten zurückhalten).",
        "",
        "Du läufst im GUI-Modus: Stelle keine Rückfragen, triff sinnvolle Annahmen und nenne sie in der Deckbeschreibung.",
        "Karten und Regeln der Blacklist (`get_blacklist`) sind tabu.",
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
                    "difficulty": {"type": "string", "enum": ["einfach", "mittel", "anspruchsvoll"],
                                   "description": "How hard the deck is to pilot for a new player"},
                    "difficulty_note": {"type": "string", "description": "One sentence (German) why it is easy or hard to play"},
                },
                "required": ["name", "archetype", "why"],
            },
        }
    },
    "required": ["suggestions"],
}


# guided finder: quiz answers -> wording for the prompt (keys come from the GUI)
FINDER_FEEL = {
    "big": "große Kreaturen und angreifen", "tokens": "viele kleine Kreaturen / Spielsteine",
    "spells": "Spontanzauber und Hexereien, Tricks", "control": "Kontrolle, auf alles eine Antwort haben",
    "graveyard": "Friedhof, Dinge zurückholen", "sacrifice": "opfern und Lebenspunkte abziehen",
    "voltron": "einen Helden mit Auren/Ausrüstungen groß machen", "chaos": "Politik, Chaos, Überraschungen",
    "combo": "Combos und Engines bauen", "lands": "Länder und viel Mana (Landfall, Ramp)",
}  # fmt: skip
FINDER_THEMES = {
    "dragons": "Drachen", "vampires": "Vampire", "elves": "Elfen", "zombies": "Zombies", "angels": "Engel",
    "dinosaurs": "Dinosaurier", "pets": "Katzen und Hunde", "pirates": "Piraten", "artifacts": "Artefakte und Maschinen",
    "wizards": "Magier und Zauberer", "horror": "Horror", "nature": "Natur und Tiere",
}  # fmt: skip
FINDER_EXPERIENCE = {
    "new": "neu bei Commander – bevorzuge Commander, die einfach zu spielen sind: klarer Plan, wenige Entscheidungen "
           "pro Zug, verzeihend bei Fehlern, kein Stax und keine komplizierten Combos. Mindestens drei Vorschläge "
           "sollen „einfach“ sein.",
    "some": "hat schon ein paar Partien gespielt – einfache bis mittlere Commander.",
    "experienced": "erfahren – Schwierigkeit egal, gerne auch anspruchsvolle Commander.",
}  # fmt: skip
COLOR_WORDS = {"W": "Weiß", "U": "Blau", "B": "Schwarz", "R": "Rot", "G": "Grün"}


class FinderRequest(BaseModel):
    prompt: str = ""
    feel: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    experience: str | None = Field(None, pattern="^(new|some|experienced)$")
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
    ]
    if req.prompt.strip():
        lines.append(f"- Wunsch des Spielers: {req.prompt.strip()}")
    feel = [FINDER_FEEL[f] for f in req.feel if f in FINDER_FEEL]
    if feel:
        lines.append(f"- Spielgefühl, das Spaß macht: {'; '.join(feel)}")
    colors = [COLOR_WORDS[c] for c in "WUBRG" if c in req.colors]
    if colors:
        lines.append(f"- Lieblingsfarben: {', '.join(colors)} – die Farbidentität soll daraus bestehen oder sie enthalten "
                     "(nicht zwingend alle auf einmal).")
    themes = [FINDER_THEMES[t] for t in req.themes if t in FINDER_THEMES]
    if themes:
        lines.append(f"- Lieblingsthemen: {', '.join(themes)}")
    if req.experience:
        lines.append(f"- Erfahrung: {FINDER_EXPERIENCE[req.experience]}")
    lines.append(f"- Anzahl Vorschläge: {req.count}")
    if req.bracket:
        lines.append(f"- Ziel-Bracket: {req.bracket} ({brackets.BY_NUMBER[req.bracket]['name']})")
    lines.append(_budget_line(req.budget, req.proxy, req.currency).replace("`validate_deck`/`save_deck`", "die Deck-Planung"))
    lines += [
        "",
        "Du läufst im GUI-Modus: keine Rückfragen. Baue KEIN Deck und speichere nichts.",
        "Gib die Vorschläge als strukturierte Ausgabe zurück (exakte englische Kartennamen), Begründungen auf Deutsch. "
        "`why` erklärt dem Spieler direkt („du“), warum der Commander zu seinen Antworten passt; "
        "`difficulty` und `difficulty_note` sagen, wie leicht er zu spielen ist.",
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
        *_deck_table_lines(deck),
        *opponents_mod.prompt_lines(deck),
    ]
    if req.request:
        lines.append(f"- Zusätzlicher Wunsch: {req.request}")
    lines += [
        "",
        "Du läufst im GUI-Modus: keine Rückfragen. Commander bleibt gleich. Karten und Regeln der Blacklist (`get_blacklist`) sind tabu.",
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
            *_deck_table_lines(deck),
            *opponents_mod.prompt_lines(deck),
            "Karten und Regeln der Blacklist (`get_blacklist`) sind tabu.",
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
        "- Festgehaltene Partien (Ergebnisse, Probleme, Gegner) liefert `deck_games` – nutze sie bei Fragen zu "
        "Schwächen, Matchups oder Verbesserungen.",
        *[ln.replace("gilt für dieses Deck", "gilt für dieses Deck – beachte sie bei Tausch-Vorschlägen") for ln in _deck_table_lines(deck)[:-1]],
        *opponents_mod.prompt_lines(deck),
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


DECK_REF = re.compile(r"\{\{([a-z0-9][a-z0-9-]{0,80})\}\}")


def _deck_refs(text: str) -> dict[str, dict[str, Any]]:
    """The saved decks an answer links as {{slug}}: slug -> name and commanders (unknown slugs are left out)."""
    slugs = list(dict.fromkeys(DECK_REF.findall(text)))
    if not slugs:
        return {}
    known = {d["slug"]: d for d in storage.list_decks()}
    return {s: {"name": known[s]["name"], "commanders": known[s]["commanders"]} for s in slugs if s in known}


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    chat_id: str | None = None
    deep: bool = False  # „Gründlich“: Opus 5.5 at extra-high effort
    model: str | None = None


CHAT_HISTORY = 6  # earlier exchanges passed along for follow-ups


def chat_prompt(question: str, history: list[dict[str, Any]] | None = None, data: dict[str, Any] | None = None) -> str:
    lines = [
        "Du bist der Assistent der ganzen Commander-Deckbuilder-App. Beantworte die Frage des Nutzers über seine "
        "Decks, Partien, Gegnerdecks, Tischregeln und Sammlung. Nutze den Skill `commander-deckbuilder`, Abschnitt "
        "„Chat mit der ganzen App“ (references/app-chat.md).",
        "",
        "Regeln:",
        "- Nur lesen: Nichts wird verändert oder gespeichert. Läuft die Frage auf Änderungen hinaus, schlage sie "
        "konkret vor (welches Deck, + rein / − raus mit Grund, oder welcher Schritt in der App) – umsetzen kann der "
        "Nutzer sie im Deck unter „Anpassen“.",
        "- Du läufst im GUI-Modus: keine Rückfragen. Triff sinnvolle Annahmen und nenne sie kurz.",
        "- Die Übersicht unten ist der aktuelle Stand. Für Details: `load_deck` (Karten, Beschreibung), `deck_games` "
        "(Partien), `list_deck_versions`, `opponent_decks`, `table_rules`, `collection_status`/`collection_search`, "
        "`get_cards`, `find_combos`, EDHREC-Tools. Stütze Aussagen auf Daten, nicht auf dein Gedächtnis.",
        "- Verlinke gespeicherte Decks immer als {{slug}} (genau der Slug aus der Übersicht, z. B. {{meren-aristocrats}}) – "
        "die App macht daraus einen Link mit dem Decknamen. Schreibe Kartennamen als [[Kartenname]] (englischer Oracle-Name).",
        "- Wenige Partien sind wenig Aussagekraft – sag das, statt aus 1–2 Spielen Schlüsse zu ziehen.",
        "- Antworte auf Deutsch in Markdown: Kernaussage zuerst, dann kurze Absätze, Listen oder eine Tabelle. "
        "Keine Vorrede über deine Arbeitsschritte.",
        "",
        "Übersicht der App:",
        *overview.prompt_lines(data),
    ]
    earlier = (history or [])[-CHAT_HISTORY:]
    if earlier:
        lines += ["", "Bisheriges Gespräch (zur Einordnung von Anschlussfragen):"]
        for h in earlier:
            answer = h.get("answer", "")
            answer = answer if len(answer) <= 1500 else answer[:1500] + " …"
            lines += [f"Frage: {h.get('question', '')}", f"Antwort: {answer}", ""]
    lines += ["", f"Frage: {question.strip()}"]
    return "\n".join(lines)


# --- routes ------------------------------------------------------------------------------------


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/ai")
async def api_ai() -> dict[str, Any]:
    return ai_status()


class NewDeckRequest(BaseModel):
    name: str = Field("", max_length=120)
    commander: str = Field(min_length=2)
    partner: str | None = None
    bracket: int = Field(2, ge=1, le=5)
    currency: str = "eur"


@app.post("/api/decks/new")
async def api_deck_new(req: NewDeckRequest) -> dict[str, Any]:
    """An empty deck with just its commander(s) – put together by hand (no AI needed)."""
    names = [n.strip() for n in (req.commander, req.partner) if n and n.strip()]
    try:
        cards, renames, missing = await resolve(names)
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc
    if missing:
        raise HTTPException(404, f"Karte nicht gefunden: {', '.join(missing)}")
    commanders = [renames.get(n, n) for n in names]
    try:
        deck = await deckimport.save(name=req.name.strip() or " + ".join(commanders), commanders=commanders, cards=[],
                                     bracket=req.bracket, currency=req.currency, note="Leer angelegt")  # fmt: skip
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"slug": deck["slug"]}


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
        raise HTTPException(502, f"Kartendaten nicht erreichbar: {_error_text(exc)}") from exc
    if missing or not cards:
        raise HTTPException(404, f"Karte nicht gefunden: {name}")
    return next(iter(cards.values()))


@app.get("/api/cards/text")
async def api_card_text(name: str, lang: str = "de") -> dict[str, Any]:
    try:
        data = await card_text(name, lang)
    except Exception as exc:
        raise HTTPException(502, f"Kartendaten nicht erreichbar: {_error_text(exc)}") from exc
    if not data:
        raise HTTPException(404, f"Karte nicht gefunden: {name}")
    return data


@app.get("/api/glossary")
async def api_glossary() -> list[dict[str, Any]]:
    return glossary.entries()


@app.post("/api/build")
async def api_build(req: BuildRequest) -> dict[str, str]:
    if req.table_rule and not tablerules.get(req.table_rule):
        raise HTTPException(404, "Diese Tischregel gibt es nicht mehr.")
    return _start(build_prompt(req), req.model, table_rule=req.table_rule)


META_MODEL = "claude-opus-5-5"  # meta suggestions always run on Opus 5.5 at extra-high effort
META_EFFORT = "xhigh"
META_COUNT = (3, 5)
META_SUGGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string", "description": "3-5 sentences (German): how the pod wins, its main threats, what the user's decks lack against it"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact English card name of the commander"},
                    "partner": {"type": "string", "description": "Partner/background if a pair, else empty"},
                    "archetype": {"type": "string", "description": "Short strategy label, e.g. 'Stax-Kontrolle', 'Aristocrats'"},
                    "why": {"type": "string", "description": "2-3 sentences (German, 'du'): why this deck is strong against exactly this pod"},
                    "win_plan": {"type": "string", "description": "1-2 sentences (German): how the deck wins"},
                    "matchups": {"type": "array", "description": "One entry per opponent deck", "items": {
                        "type": "object", "properties": {
                            "opponent": {"type": "string", "description": "Opponent deck as named in the prompt"},
                            "plan": {"type": "string", "description": "One sentence (German): how to beat it"}},
                        "required": ["opponent", "plan"]}},
                    "key_cards": {"type": "array", "items": {"type": "string"}, "description": "5-8 key cards, exact English names, checked with the tools"},
                    "risks": {"type": "string", "description": "One sentence (German): the main weakness"},
                    "bracket_fit": {"type": "string", "description": "How it fits bracket, sub-tier, table rule and budget"},
                    "strategy": {"type": "string", "description": "Strategy text for the build form (German)"},
                    "difficulty": {"type": "string", "enum": ["einfach", "mittel", "anspruchsvoll"]},
                    "difficulty_note": {"type": "string"},
                },
                "required": ["name", "archetype", "why", "win_plan", "strategy"],
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}, "description": "Web sources used (URL or title), if any"},
    },
    "required": ["analysis", "suggestions"],
}  # fmt: skip


class MetaSuggestRequest(MetaBuildRequest):
    count: int = Field(4, ge=META_COUNT[0], le=META_COUNT[1])
    research: bool = True  # allow WebSearch / WebFetch


def meta_suggest_prompt(req: MetaSuggestRequest) -> str:
    lines = [
        f"Schlage {req.count} Commander-Decks vor, die gegen die Runde des Nutzers am stärksten wären. Nutze den Skill "
        "`commander-deckbuilder` (Abschnitt „Gegen die Runde bauen“) und für die Commander-Wahl den Skill `commander-finder`. "
        "Baue noch KEIN Deck und speichere nichts – der Nutzer wählt einen Vorschlag aus.",
        "",
        *_meta_context(req, free=True),
        "",
        "So gehst du vor:",
        "1. Analysiere die Runde gründlich: Wie gewinnen die Gegner, wie schnell, womit haben sie dem Nutzer Probleme gemacht, "
        "was fehlt seinen Decks? Typische Karten der Gegner-Commander: `edhrec_recommendations`; Details: `opponent_decks`, `deck_games`.",
        "2. „Am stärksten“ heißt: die besten Chancen gegen genau diese Gegner – innerhalb von Bracket, Feinstufe, Hausregeln, "
        "Tischregel, Budget und Blacklist, nie darüber hinaus.",
        f"3. Wähle {req.count} deutlich verschiedene Ansätze (Farben, Strategie, Tempo), sortiert nach erwarteter Stärke, keinen "
        "Commander, den der Nutzer schon spielt. Prüfe Schlüsselkarten und Combos mit den Tools (`get_cards`, `find_combos`).",
        "4. Pro Vorschlag: warum er gegen diese Runde stark ist, wie er gewinnt, ein Satz Spielplan pro Gegnerdeck, 5–8 "
        "Schlüsselkarten, die größte Schwäche, wie er in Bracket/Tischregel/Budget passt, ein Strategietext fürs Bauformular.",
    ]
    if req.research:
        lines.append("5. Du darfst im Web recherchieren (`WebSearch`, `WebFetch`): aktuelle Meta-Artikel, EDHREC-Seiten, Turnier- und "
                     "Decklisten-Datenbanken. Nutze das gezielt für die Gegner-Commander und deine Kandidaten und nenne die Quellen "
                     "in `sources`. Kartentexte und Legalität prüfst du trotzdem mit den mtg-Tools.")  # fmt: skip
    lines += [
        "",
        "Du läufst im GUI-Modus: keine Rückfragen. Gib das Ergebnis als strukturierte Ausgabe zurück, alle Texte auf Deutsch, "
        "Kartennamen exakt auf Englisch.",
    ]
    return "\n".join(lines)


def _meta_file() -> Path:
    return storage.DECKS_DIR / ".meta-suggestions.json"


@app.post("/api/build-meta/suggest")
async def api_build_meta_suggest(req: MetaSuggestRequest) -> dict[str, str]:
    known = {o["id"] for o in opponents_mod.all_opponents()}
    if not known:
        raise HTTPException(400, "Noch keine Gegnerdecks – lege sie unter „Gegnerdecks“ an oder halte Partien mit Gegnern fest.")
    req.opponent_ids = [i for i in req.opponent_ids if i in known]
    if req.table_rule and not tablerules.get(req.table_rule):
        raise HTTPException(404, "Diese Tischregel gibt es nicht mehr.")

    async def finish(job: Job, ok: bool, _text: str, structured: Any) -> None:
        items = await _enrich_suggestions(structured) if ok else []
        if items:
            result = {"created": storage._now(), "model": META_MODEL, "effort": META_EFFORT, "research": req.research,
                      "analysis": (structured or {}).get("analysis", ""), "sources": (structured or {}).get("sources") or [],
                      "suggestions": items, "request": req.model_dump(exclude={"model"})}  # fmt: skip
            write_json(_meta_file(), result)
            job.emit(type="meta_suggestions", result=result)
        elif ok:
            job.emit(type="error", text="Keine verwertbaren Vorschläge erhalten.")
        job.emit(type="done", ok=bool(items), deck=None)

    output_format = {"type": "json_schema", "schema": META_SUGGEST_SCHEMA}
    return _start(meta_suggest_prompt(req), META_MODEL, output_format, read_only=True, finish=finish,
                  effort=META_EFFORT, web=req.research)  # fmt: skip


@app.get("/api/build-meta/suggestions")
async def api_build_meta_last() -> dict[str, Any]:
    try:
        return json.loads(_meta_file().read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


@app.post("/api/build-meta")
async def api_build_meta(req: MetaBuildRequest) -> dict[str, str]:
    known = {o["id"] for o in opponents_mod.all_opponents()}
    if not known:
        raise HTTPException(400, "Noch keine Gegnerdecks – lege sie unter „Gegnerdecks“ an oder halte Partien mit Gegnern fest.")
    req.opponent_ids = [i for i in req.opponent_ids if i in known]
    if req.table_rule and not tablerules.get(req.table_rule):
        raise HTTPException(404, "Diese Tischregel gibt es nicht mehr.")
    against = req.opponent_ids or sorted(known)
    return _start(meta_prompt(req), req.model, table_rule=req.table_rule, extras={"built_against": against})


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


# --- upgrade suggestions ----------------------------------------------------------------------

UPGRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences: what the upgrades achieve together"},
        "upgrades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "add": {"type": "string", "description": "English card name to put in"},
                    "remove": {"type": "string", "description": "Card of the deck to take out"},
                    "price": {"type": ["number", "null"], "description": "Price of the new card in the deck currency"},
                    "owned": {"type": "boolean", "description": "The user already owns the new card (collection)"},
                    "impact": {"type": "string", "description": "Short effect label, e.g. 'mehr Kartenzug', 'schnellerer Ramp'"},
                    "reason": {"type": "string", "description": "Why this swap, one or two sentences (German)"},
                },
                "required": ["add", "remove", "reason"],
            },
        },
    },
    "required": ["upgrades"],
}


class UpgradeRequest(BaseModel):
    budget: float | None = Field(default=None, ge=0)
    focus: str | None = None
    opponent_id: str | None = None
    count: int = Field(default=8, ge=1, le=20)
    model: str | None = None


def upgrade_prompt(deck: dict[str, Any], req: UpgradeRequest, has_collection: bool) -> str:
    cur = deck.get("currency", "eur").upper()
    if deck.get("proxy"):
        money = "- Proxy-Deck: Preise spielen keine Rolle, nimm die stärksten passenden Karten."
    elif req.budget is not None:
        money = f"- Budget für alle Upgrades zusammen: max. {req.budget:g} {cur} (Summe der Preise der neuen Karten)."
    else:
        money = "- Kein Budget-Limit, aber nenne den Preis jeder neuen Karte."
    lines = [
        f"Schlage Upgrades für das gespeicherte Commander-Deck `{deck['slug']}` vor (lade es mit `load_deck`, "
        f"{storage.level_text(deck)}). Recherchiere wie beim Bauen (Skill `commander-deckbuilder`): "
        "`edhrec_recommendations`, `similar_cards`, `find_combos`, `get_cards`.",
        "",
        money,
        f"- Bis zu {req.count} Tausche, sortiert nach Wirkung (stärkster zuerst). Jeder Tausch: genau eine Karte rein "
        "(`add`), eine Karte raus (`remove`, muss im Deck sein, keine Basic Lands außer für Länder-Tausche).",
        "- Bracket, Hausregeln, Farbidentität und Blacklist (`get_blacklist`) einhalten; keine zusätzlichen Game Changer "
        "über dem Limit, keine im Bracket verbotenen Combos.",
        *_deck_table_lines(deck),
        *opponents_mod.prompt_lines(deck, focus_id=req.opponent_id),
    ]
    if req.focus:
        lines.append(f"- Fokus des Nutzers: {req.focus}")
    record = games.stats(games.games(deck["slug"]))
    if record["games"]:
        common = ", ".join(f"{i['label']} ({i['count']}×)" for i in record["issues"][:4]) or "keine notiert"
        lines.append(f"- Festgehaltene Partien: {record['wins']} Siege, {record['losses']} Niederlagen; häufigste Probleme: "
                     f"{common}. Details über `deck_games` – die Tausche sollen genau diese Probleme angehen.")
    if has_collection:
        lines.append("- Sammlung: Karten, die der Nutzer schon besitzt (`collection_search`), kosten nichts – bevorzuge sie "
                     "bei gleicher Wirkung und setze `owned` auf true.")
    lines += ["", "Du läufst im GUI-Modus: keine Rückfragen und nichts speichern – der Nutzer wählt die Tausche selbst aus. "
              "Texte auf Deutsch."]
    return "\n".join(lines)


async def _enrich_upgrades(deck: dict[str, Any], structured: Any) -> list[dict[str, Any]]:
    """Check Claude's suggestions against the deck and add images, current prices and ownership."""
    items = (structured or {}).get("upgrades") if isinstance(structured, dict) else None
    if not items:
        return []
    in_deck = {c["name"] for c in deck.get("cards", [])}
    try:
        data, renames, _ = await resolve([i.get("add", "") for i in items] + [i.get("remove", "") for i in items])
    except Exception:
        data, renames = {}, {}
    owned = collection.owned_counts()
    cur = "price_usd" if deck.get("currency") == "usd" else "price_eur"
    out, seen = [], set()
    for i in items:
        add = renames.get(i.get("add", ""), i.get("add", ""))
        remove = renames.get(i.get("remove", ""), i.get("remove", ""))
        if add not in data or add in in_deck or remove not in in_deck or add in seen:
            continue
        seen.add(add)
        have = owned.get(add, {})
        price = data[add].get(cur)
        out.append({
            "add": add, "remove": remove, "reason": i.get("reason", ""), "impact": i.get("impact", ""),
            "price": float(price) if price else None, "owned": bool(have.get("real") or have.get("proxy")),
            "image": data[add].get("image"), "image_remove": (data.get(remove) or {}).get("image"),
            "type_line": data[add].get("type_line"),
        })  # fmt: skip
    return out


@app.post("/api/decks/{slug}/upgrades")
async def api_upgrades(slug: str, req: UpgradeRequest) -> dict[str, str]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    async def finish(job: Job, ok: bool, _text: str, structured: Any) -> None:
        items = await _enrich_upgrades(deck, structured) if ok else []
        if items:
            job.emit(type="upgrades", items=items, summary=(structured or {}).get("summary", ""), currency=deck.get("currency", "eur"))
        elif ok:
            job.emit(type="error", text="Keine verwertbaren Vorschläge erhalten.")
        job.emit(type="done", ok=bool(items), deck=None)

    prompt = upgrade_prompt(deck, req, bool(collection.load()))
    output_format = {"type": "json_schema", "schema": UPGRADE_SCHEMA}
    return _start(prompt, req.model, output_format, read_only=True, finish=finish)


_STR_LIST = {"type": "array", "items": {"type": "string"}}
GUIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {"type": "string", "description": "Game plan in at most three sentences, for a beginner"},
        "early": {**_STR_LIST, "description": "Early game (turns 1-3): what to do, 2-4 bullets"},
        "mid": {**_STR_LIST, "description": "Mid game: 2-4 bullets"},
        "late": {**_STR_LIST, "description": "Late game / closing: 2-4 bullets"},
        "mulligan": {**_STR_LIST, "description": "Keep/mulligan rules for the opening hand, 2-4 bullets"},
        "key_cards": {"type": "array", "description": "4-8 most important cards of the deck", "items": {
            "type": "object", "properties": {"name": {"type": "string", "description": "English card name"},
                                             "why": {"type": "string", "description": "One sentence: role and when to play it"}},
            "required": ["name", "why"]}},
        "win_conditions": {**_STR_LIST, "description": "How the deck actually wins, 1-4 bullets"},
        "watch_out": {**_STR_LIST, "description": "Weaknesses, threats to respect, common mistakes, 2-4 bullets"},
        "tips": {**_STR_LIST, "description": "Play tips and interactions that are easy to miss, 2-5 bullets"},
    },
    "required": ["plan", "early", "mid", "late", "mulligan", "key_cards", "win_conditions"],
}  # fmt: skip


class GuideRequest(BaseModel):
    model: str | None = None


def guide_prompt(deck: dict[str, Any]) -> str:
    return "\n".join([
        f"Schreibe eine Spielanleitung für das gespeicherte Commander-Deck `{deck['slug']}` ({storage.level_text(deck)}). "
        "Lade es mit `load_deck`, lies die Schlüsselkarten mit `get_cards` und prüfe Combos mit `find_combos`. "
        "Nutze den Skill `commander-deckbuilder` (Abschnitt „Deck-Anleitung“).",
        "",
        "Zielgruppe: Einsteiger, die das Deck zum ersten Mal spielen. Die Anleitung passt auf eine gedruckte Seite – "
        "kurze, konkrete Stichpunkte, keine Floskeln. Nenne Karten beim englischen Namen als [[Kartenname]].",
        "Du läufst im GUI-Modus: keine Rückfragen, nichts speichern. Alle Texte auf Deutsch.",
    ])  # fmt: skip


async def _clean_guide(deck: dict[str, Any], structured: Any) -> dict[str, Any] | None:
    """Keep the schema fields, drop key cards that are not in the deck, add images for [[refs]]."""
    if not isinstance(structured, dict) or not structured.get("plan"):
        return None
    lists = ("early", "mid", "late", "mulligan", "win_conditions", "watch_out", "tips")
    guide: dict[str, Any] = {"plan": str(structured["plan"]).strip()}
    for key in lists:
        guide[key] = [str(x).strip() for x in structured.get(key) or [] if str(x).strip()][:6]
    in_deck = {c["name"] for c in deck.get("cards", [])} | set(deck.get("commanders", []))
    names = [k.get("name", "") for k in structured.get("key_cards") or [] if isinstance(k, dict)]
    try:
        data, renames, _ = await resolve(names) if names else ({}, {}, [])
    except Exception:
        data, renames = {}, {}
    guide["key_cards"] = []
    for k in structured.get("key_cards") or []:
        name = renames.get(k.get("name", ""), k.get("name", "")) if isinstance(k, dict) else ""
        if name in in_deck:
            guide["key_cards"].append({"name": name, "why": k.get("why", ""), "image": (data.get(name) or {}).get("image")})
    text = " ".join([guide["plan"], *(x for key in lists for x in guide[key])])
    guide["cards"] = await _card_refs(text)
    guide["version"] = deck.get("version")
    guide["created"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    return guide


class GameIn(BaseModel):
    result: str = Field(pattern="^(win|loss|draw)$")
    opponents: list[str] = Field(default_factory=list, max_length=5)
    opponent_ids: list[str | None] = Field(default_factory=list, max_length=5)  # chosen opponent decks, same order
    opponent_notes: list[str] = Field(default_factory=list, max_length=5)  # "what stood out", same order
    remember_opponents: bool = True
    turn: int | None = Field(default=None, ge=1, le=60)
    issues: list[str] = Field(default_factory=list)
    mvp: str | None = None
    note: str = Field(default="", max_length=2000)


@app.get("/api/decks/{slug}/games")
async def api_games(slug: str) -> dict[str, Any]:
    _not_found(storage.load, slug)
    return games.summary(slug)


@app.post("/api/decks/{slug}/games")
async def api_game_add(slug: str, req: GameIn) -> dict[str, Any]:
    deck = _not_found(storage.load, slug)
    slots = [{"commander": o.strip(), "id": (req.opponent_ids[i] if i < len(req.opponent_ids) else None),
              "note": (req.opponent_notes[i] if i < len(req.opponent_notes) else "")}
             for i, o in enumerate(req.opponents) if o.strip()]  # fmt: skip
    mvp, opponents = req.mvp, [s["commander"] for s in slots]
    names = [n for n in [mvp or "", *opponents] if n.strip()]
    if names:  # English Oracle names where they resolve (German input works too)
        try:
            _, renames, _ = await resolve(names)
        except Exception:
            renames = {}
        mvp = renames.get(mvp or "", mvp)
        opponents = [renames.get(o, o) for o in opponents]
    if req.result not in games.RESULTS or any(i not in games.ISSUES for i in req.issues):
        raise HTTPException(400, "Ungültiges Ergebnis oder Problem")
    game_id = uuid.uuid4().hex[:10]
    for s, name in zip(slots, opponents):
        s["commander"] = name
    ids = await opponents_mod.link_game(slug, game_id, slots, remember=req.remember_opponents) if slots else []
    try:
        entry = games.add(slug, result=req.result, opponents=opponents, turn=req.turn, issues=req.issues, mvp=mvp,
                          note=req.note, version=deck.get("version"), entry_id=game_id, opponent_ids=ids)  # fmt: skip
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"game": entry, **games.summary(slug)}


@app.delete("/api/decks/{slug}/games/{game_id}")
async def api_game_delete(slug: str, game_id: str) -> dict[str, Any]:
    if not games.delete(slug, game_id):
        raise HTTPException(404, "Partie nicht gefunden")
    return games.summary(slug)


@app.get("/api/decks/{slug}/rule0")
async def api_rule0(slug: str) -> dict[str, Any]:
    try:
        return rule0.build(storage.load(slug))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/decks/{slug}/guide")
async def api_guide(slug: str, req: GuideRequest) -> dict[str, str]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    async def finish(job: Job, ok: bool, _text: str, structured: Any) -> None:
        guide = await _clean_guide(deck, structured) if ok else None
        if guide:
            storage.set_extra(slug, "guide", guide)
            job.emit(type="guide", guide=guide)
        elif ok:
            job.emit(type="error", text="Keine verwertbare Anleitung erhalten.")
        job.emit(type="done", ok=bool(guide), deck=None)

    output_format = {"type": "json_schema", "schema": GUIDE_SCHEMA}
    return _start(guide_prompt(deck), req.model, output_format, read_only=True, finish=finish)


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences: where the plan takes the deck"},
        "stages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short stage title (German), e.g. 'Manabasis & Ramp'"},
                    "goal": {"type": "string", "description": "What this stage improves, one sentence (German)"},
                    "upgrades": UPGRADE_SCHEMA["properties"]["upgrades"],
                },
                "required": ["title", "upgrades"],
            },
        },
    },
    "required": ["stages"],
}


class PlanRequest(BaseModel):
    stages: list[float] = Field(default_factory=lambda: [20, 50, 100], min_length=1, max_length=4)
    focus: str | None = None
    model: str | None = None


def plan_prompt(deck: dict[str, Any], req: PlanRequest, has_collection: bool) -> str:
    cur = deck.get("currency", "eur").upper()
    stages = sorted(b for b in req.stages if b >= 0)
    if deck.get("proxy"):
        money = [f"- Proxy-Deck: Preise spielen keine Rolle. Mache {len(stages)} Stufen nach Wirkung (wichtigste zuerst)."]
    else:
        money = [f"- {len(stages)} Stufen mit kumuliertem Budget: " + ", ".join(f"Stufe {i + 1} bis {b:g} {cur} insgesamt"
                 for i, b in enumerate(stages)) + ". Die Summe der neuen Karten aller Stufen bis einschließlich Stufe n "
                 "bleibt unter dem Betrag von Stufe n."]  # fmt: skip
    lines = [
        f"Erstelle einen Upgrade-Plan in Stufen für das gespeicherte Commander-Deck `{deck['slug']}` (lade es mit `load_deck`, "
        f"{storage.level_text(deck)}). Recherchiere wie beim Bauen (Skill `commander-deckbuilder`): "
        "`edhrec_recommendations`, `similar_cards`, `find_combos`, `get_cards`, und schau in `deck_games`, ob Partien festgehalten sind.",
        "",
        *money,
        "- Jede Stufe hat ein klares Thema (z. B. erst Manabasis und Ramp, dann Kartenzug und Removal, dann Synergie) und 3–8 Tausche: "
        "genau eine Karte rein (`add`), eine raus (`remove`, muss im aktuellen Deck sein).",
        "- Die Stufen bauen aufeinander auf: keine Karte zweimal hinzufügen, keine Karte entfernen, die eine frühere Stufe erst "
        "hinzugefügt hat, jede Karte nur einmal entfernen.",
        "- Bracket, Hausregeln, Farbidentität und Blacklist (`get_blacklist`) einhalten.",
        *_deck_table_lines(deck),
        *opponents_mod.prompt_lines(deck),
    ]
    if deck.get("precon"):
        lines.append(f"- Das Deck ist das Precon „{deck['precon'].get('name')}“: raus zuerst die schwächsten Karten des Precons.")
    if req.focus:
        lines.append(f"- Fokus des Nutzers: {req.focus}")
    if has_collection:
        lines.append("- Karten aus der Sammlung (`collection_search`) kosten nichts – bevorzuge sie und setze `owned` auf true.")
    lines += ["", "Du läufst im GUI-Modus: keine Rückfragen und nichts speichern. Texte auf Deutsch."]
    return "\n".join(lines)


async def _enrich_plan(deck: dict[str, Any], structured: Any, budgets: list[float]) -> dict[str, Any] | None:
    """Validate every stage like single upgrade suggestions; a card is added or removed only once."""
    stages = (structured or {}).get("stages") if isinstance(structured, dict) else None
    if not isinstance(stages, list):
        return None
    added: set[str] = set()
    removed: set[str] = set()
    out = []
    for i, st in enumerate(stages[:4]):
        if not isinstance(st, dict):
            continue
        items = [u for u in await _enrich_upgrades(deck, {"upgrades": st.get("upgrades") or []})
                 if u["add"] not in added and u["remove"] not in removed]  # fmt: skip
        if not items:
            continue
        added |= {u["add"] for u in items}
        removed |= {u["remove"] for u in items}
        budget = sorted(budgets)[i] if i < len(budgets) and not deck.get("proxy") else None
        out.append({"title": str(st.get("title") or f"Stufe {i + 1}"), "goal": str(st.get("goal") or ""), "budget": budget,
                    "cost": round(sum(u["price"] or 0 for u in items if not u["owned"]), 2), "upgrades": items})  # fmt: skip
    if not out:
        return None
    return {"summary": str((structured or {}).get("summary") or ""), "stages": out, "currency": deck.get("currency", "eur"),
            "version": deck.get("version"), "created": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}  # fmt: skip


@app.post("/api/decks/{slug}/upgrade-plan")
async def api_upgrade_plan(slug: str, req: PlanRequest) -> dict[str, str]:
    deck = _not_found(storage.load, slug)

    async def finish(job: Job, ok: bool, _text: str, structured: Any) -> None:
        plan = await _enrich_plan(deck, structured, req.stages) if ok else None
        if plan:
            storage.set_extra(slug, "upgrade_plan", plan)
            job.emit(type="plan", plan=plan)
        elif ok:
            job.emit(type="error", text="Kein verwertbarer Plan erhalten.")
        job.emit(type="done", ok=bool(plan), deck=None)

    prompt = plan_prompt(deck, req, bool(collection.load()))
    return _start(prompt, req.model, {"type": "json_schema", "schema": PLAN_SCHEMA}, read_only=True, finish=finish)


class ImportPreviewRequest(BaseModel):
    url: str | None = None
    text: str | None = None
    name: str | None = None


@app.post("/api/import/preview")
async def api_import_preview(req: ImportPreviewRequest) -> dict[str, Any]:
    try:
        if req.url and req.url.strip():
            data = await importers.import_url(req.url)
        else:
            data = importers.import_text(req.text or "", req.name)
    except HttpError as exc:  # before RuntimeError: HttpError is one
        status = 404 if exc.status == 404 else 502
        raise HTTPException(status, "Deck nicht gefunden – ist es öffentlich?" if status == 404 else exc.friendly) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Seite nicht erreichbar: {_error_text(exc)}") from exc
    if not data["cards"]:
        raise HTTPException(400, "Keine Karten gefunden.")
    return await deckimport.preview(data)


class ImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    commanders: list[str] = Field(min_length=1, max_length=2)
    cards: list[str] = Field(min_length=1)
    categories: dict[str, str] = Field(default_factory=dict)
    bracket: int | None = Field(None, ge=1, le=5)
    currency: str = Field("eur", pattern="^(eur|usd)$")
    site: str = ""
    source: str = ""


@app.post("/api/import")
async def api_import(req: ImportRequest) -> dict[str, Any]:
    where = f"{req.site}: {req.source}" if req.source else (req.site or "Liste")
    try:
        return await deckimport.save(
            name=req.name.strip(), commanders=req.commanders, cards=req.cards, categories=req.categories, bracket=req.bracket,
            currency=req.currency, description=f"Importiert von {where}." if req.source else "",
            note=f"Importiert ({req.site or 'Liste'})", extra={"imported_from": {"site": req.site, "source": req.source}},
        )  # fmt: skip
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --- deskmat studio -------------------------------------------------------------------------------

DESKMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short German title for the motif (2-5 words)"},
        "prompt": {"type": "string", "description": "English text-to-image prompt, max. 550 characters"},
    },
    "required": ["title", "prompt"],
}


class DeskmatGenerateRequest(BaseModel):
    setting: str = Field(min_length=3, max_length=1500)
    style: str = "painting"
    deck: str | None = None
    format: str = "playmat"
    variants: int = Field(3, ge=1, le=4)
    model: str | None = None


def deskmat_prompt(req: DeskmatGenerateRequest, deck: dict[str, Any] | None) -> str:
    style = deskmat.STYLES.get(req.style, deskmat.STYLES["painting"])[1]
    label, w_mm, h_mm = deskmat.FORMATS[req.format]
    lines = [
        "Schreibe einen Bild-Prompt für einen Text-zu-Bild-Generator (Flux). Daraus wird eine Deskmat/Playmat "
        f"für Magic: The Gathering ({label}, Querformat {w_mm}:{h_mm}), gedruckt mit 300–600 DPI.",
        "",
        f"- Setting des Nutzers: {req.setting.strip()}",
        f"- Stil: {style}",
    ]
    if deck:
        lines.append(f"- Stimmung aus dem Deck „{deck.get('name')}“ (Commander: {' + '.join(deck.get('commanders') or [])}"
                     + (f"; {deck.get('description')}" if deck.get("description") else "") + "). Lies das Aussehen des "
                     "Commanders bei Bedarf mit `get_cards` nach, nenne im Prompt aber keine Kartennamen, sondern beschreibe.")
    lines += [
        "",
        "Regeln für den Prompt (Englisch, höchstens 550 Zeichen, ein Absatz):",
        "- breite Panorama-Komposition; das Hauptmotiv eher seitlich, ruhigere Flächen dort, wo Karten liegen;",
        "- sehr detailliert, klare Formen und saubere Kanten (wird stark vergrößert), stimmiges Licht, hohe Qualität;",
        "- ausdrücklich: no text, no letters, no logos, no card frame, no border, no watermark.",
        "",
        "Du läufst im GUI-Modus: keine Rückfragen, nichts speichern. Gib Titel (deutsch) und Prompt strukturiert zurück.",
    ]
    return "\n".join(lines)


@app.get("/api/deskmat/options")
async def api_deskmat_options() -> dict[str, Any]:
    return deskmat.formats()


@app.get("/api/deskmats")
async def api_deskmats() -> list[dict[str, Any]]:
    return deskmat.projects()


@app.get("/api/deskmat/mpc")
async def api_deskmat_mpc(name: str) -> list[dict[str, Any]]:
    try:
        return await deskmat.mpc_options(name)
    except Exception as exc:
        raise HTTPException(502, f"MPC Autofill nicht erreichbar: {exc}") from exc


class DeskmatCardRequest(BaseModel):
    name: str = Field(min_length=1)
    scryfall_id: str | None = Field(None, pattern="^[0-9a-f-]{36}$")
    face: str = Field("front", pattern="^(front|back)$")
    mpc_id: str | None = Field(None, pattern="^[A-Za-z0-9_-]{5,120}$")


@app.post("/api/deskmat/card")
async def api_deskmat_card(req: DeskmatCardRequest) -> dict[str, Any]:
    try:
        return await deskmat.from_card(req.name, scryfall_id=req.scryfall_id, face=req.face, mpc_id=req.mpc_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc


@app.post("/api/deskmat/upload")
async def api_deskmat_upload(request: Request, filename: str = "") -> dict[str, Any]:
    try:
        return deskmat.from_upload(await request.body(), filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/deskmat/generate")
async def api_deskmat_generate(req: DeskmatGenerateRequest) -> dict[str, str]:
    if req.format not in deskmat.FORMATS:
        raise HTTPException(400, "Unbekanntes Format")
    deck = None
    if req.deck:
        deck = _not_found(storage.load, req.deck)

    if not ai_status()["available"]:  # without Claude: the user's description is the image prompt
        if not req.setting.strip():
            raise HTTPException(400, "Beschreibe das Motiv – ohne KI wird deine Beschreibung direkt als Bild-Prompt genutzt.")
        style = deskmat.STYLES.get(req.style, deskmat.STYLES["painting"])[1]
        prompt = ", ".join(p for p in (req.setting.strip(), style, "wide panorama, highly detailed, no text") if p)

        async def runner(job: Job) -> None:
            job.emit(type="status", text="Ohne KI: deine Beschreibung geht direkt an den Bildgenerator …")
            project = await deskmat.generate(req.setting.strip()[:40], prompt[:900], req.format, variants=req.variants,
                                             setting=req.setting, style=req.style, progress=lambda t: job.emit(type="status", text=t))  # fmt: skip
            for e in project.get("errors") or []:
                job.emit(type="status", text=f"Eine Variante fehlte: {e}")
            job.emit(type="deskmat", project=project)
            job.emit(type="done", ok=True, deck=None)

        return _start_runner(runner)

    async def finish(job: Job, ok: bool, _text: str, structured: Any) -> None:
        prompt = str((structured or {}).get("prompt") or "").strip() if isinstance(structured, dict) else ""
        if not ok or not prompt:
            if ok:
                job.emit(type="error", text="Claude hat keinen Bild-Prompt geliefert.")
            job.emit(type="done", ok=False, deck=None)
            return
        job.emit(type="status", text=f"Prompt: {prompt[:160]}{'…' if len(prompt) > 160 else ''}")
        try:
            project = await deskmat.generate(
                str(structured.get("title") or req.setting[:40]), prompt[:900], req.format, variants=req.variants,
                setting=req.setting, style=req.style, progress=lambda t: job.emit(type="status", text=t),
            )  # fmt: skip
        except Exception as exc:
            job.emit(type="error", text=str(exc))
            job.emit(type="done", ok=False, deck=None)
            return
        for e in project.get("errors") or []:
            job.emit(type="status", text=f"Eine Variante fehlte: {e}")
        job.emit(type="deskmat", project=project)
        job.emit(type="done", ok=True, deck=None)

    output_format = {"type": "json_schema", "schema": DESKMAT_SCHEMA}
    return _start(deskmat_prompt(req, deck), req.model, output_format, read_only=True, finish=finish)


@app.get("/api/deskmat/{pid}")
async def api_deskmat(pid: str) -> dict[str, Any]:
    return _not_found(deskmat.load, pid)


@app.delete("/api/deskmat/{pid}")
async def api_deskmat_delete(pid: str) -> dict[str, bool]:
    _not_found(deskmat.load, pid)
    deskmat.delete(pid)
    return {"deleted": True}


class ChooseVariant(BaseModel):
    n: int = Field(ge=0, le=3)


@app.post("/api/deskmat/{pid}/choose")
async def api_deskmat_choose(pid: str, req: ChooseVariant) -> dict[str, Any]:
    _not_found(deskmat.load, pid)
    try:
        return deskmat.choose(pid, req.n)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class DeskmatRender(BaseModel):
    format: str = "playmat"
    dpi: int = 300
    bleed_mm: float = 0
    fit: str = Field("fill", pattern="^(fill|fit)$")
    crop: dict[str, float] | None = None
    upscale: bool = True
    filetype: str = Field("png", pattern="^(png|jpg)$")
    passes: int = Field(1, ge=1, le=2)


@app.post("/api/deskmat/{pid}/render")
async def api_deskmat_render(pid: str, req: DeskmatRender) -> dict[str, str]:
    _not_found(deskmat.load, pid)
    try:
        deskmat.check_size(req.format, req.dpi, req.bleed_mm)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def runner(job: Job) -> None:
        job.emit(type="status", text="Bereite das Motiv vor …")
        project = await deskmat.render(pid, fmt=req.format, dpi=req.dpi, bleed_mm=req.bleed_mm, fit=req.fit, crop=req.crop,
                                       upscale=req.upscale, filetype=req.filetype, passes=req.passes,
                                       progress=lambda t: job.emit(type="status", text=t))  # fmt: skip
        res = project["result"]
        for w in res["warnings"]:
            job.emit(type="status", text=w)
        job.emit(type="result", text=f"Fertig: {res['size'][0]} × {res['size'][1]} px mit {res['dpi']} DPI"
                 + (f" · {res['ai_passes']}× KI-hochskaliert" if res["ai_passes"] else ""))  # fmt: skip
        job.emit(type="deskmat", project=project)
        job.emit(type="done", ok=True)

    return _start_runner(runner)


class DeskmatPoint(BaseModel):
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


@app.post("/api/deskmat/{pid}/compare")
async def api_deskmat_compare(pid: str, req: DeskmatPoint) -> dict[str, str]:
    meta = _not_found(deskmat.load, pid)
    if not meta.get("render"):
        raise HTTPException(400, "Erst die Deskmat erstellen.")

    async def runner(job: Job) -> None:
        project = await deskmat.compare(pid, x=req.x, y=req.y, progress=lambda t: job.emit(type="status", text=t))
        job.emit(type="deskmat", project=project, what="compare")
        job.emit(type="done", ok=bool(project["compare"]["tiles"]))

    return _start_runner(runner)


@app.get("/api/deskmat/{pid}/compare/{passes}")
async def api_deskmat_compare_tile(pid: str, passes: int) -> FileResponse:
    return FileResponse(_not_found(deskmat.compare_file, pid, passes), headers={"Cache-Control": "no-cache"})


@app.get("/api/deskmat/{pid}/testprint")
async def api_deskmat_testprint(pid: str, x: float = 0.5, y: float = 0.5) -> FileResponse:
    _not_found(deskmat.load, pid)
    try:
        path = await asyncio.to_thread(deskmat.testprint, pid, x=min(max(x, 0), 1), y=min(max(y, 0), 1),
                                       paper=settings_mod.load().get("paper", "A4"))  # fmt: skip
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    title = re.sub(r"[^\w\- ]+", "", deskmat.load(pid).get("title") or "deskmat").strip().replace(" ", "-") or "deskmat"
    return FileResponse(path, filename=f"Probedruck-{title}.pdf", media_type="application/pdf")


@app.api_route("/api/deskmat/{pid}/image", methods=["GET", "HEAD"])
async def api_deskmat_image(pid: str, kind: str = "source", n: int | None = None, download: bool = False) -> FileResponse:
    path = _not_found(deskmat.file, pid, kind, n)
    if download:
        meta = deskmat.load(pid)
        safe = re.sub(r"[^\w\- ]+", "", meta.get("title") or "deskmat").strip().replace(" ", "-") or "deskmat"
        return FileResponse(path, filename=f"{safe}-{path.stem.removeprefix('deskmat-')}{path.suffix}")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.post("/api/deskmat/{pid}/open-folder")
async def api_deskmat_open_folder(pid: str) -> dict[str, Any]:
    _not_found(deskmat.load, pid)
    folder = deskmat._dir(pid)
    try:
        open_in_file_manager(folder)
    except Exception as exc:
        return {"opened": False, "path": str(folder), "error": str(exc)}
    return {"opened": True, "path": str(folder)}


# --- collective print orders (Sammelbestellung) ------------------------------------------------------


class OrderCreate(BaseModel):
    name: str = ""


class OrderItem(BaseModel):
    kind: str = Field("card", pattern="^(card|token)$")
    name: str = Field(min_length=1, max_length=200)
    qty: int = Field(1, ge=1, le=printorders.MAX_QTY)
    source: str | None = None
    source_slug: str | None = None
    type_line: str | None = None
    token_id: str | None = Field(None, pattern="^[0-9a-f-]{36}$")
    image: str | None = None


class OrderAdd(BaseModel):
    items: list[OrderItem] = Field(default_factory=list)
    deck: str | None = None  # add cards of this deck ...
    names: list[str] | None = None  # ... only these
    only_missing: bool = False  # ... only what the collection lacks
    since_version: int | None = None  # ... only the cards that came in after this version of the deck
    text: str | None = None  # pasted list "2 Sol Ring"
    url: str | None = None  # deck link (Moxfield, Archidekt, ...): all its cards incl. commanders


@app.get("/api/orders")
async def api_orders() -> list[dict[str, Any]]:
    return printorders.orders()


@app.post("/api/orders")
async def api_order_create(req: OrderCreate) -> dict[str, Any]:
    return printorders.create(req.name)


@app.get("/api/orders/{oid}")
async def api_order(oid: str) -> dict[str, Any]:
    return printorders.summary(_not_found(printorders.load, oid))


@app.patch("/api/orders/{oid}")
async def api_order_rename(oid: str, req: OrderCreate) -> dict[str, Any]:
    return _not_found(printorders.rename, oid, req.name)


@app.delete("/api/orders/{oid}")
async def api_order_delete(oid: str) -> dict[str, bool]:
    _not_found(printorders.delete, oid)
    return {"deleted": True}


@app.post("/api/orders/{oid}/items")
async def api_order_add(oid: str, req: OrderAdd) -> dict[str, Any]:
    _not_found(printorders.load, oid)
    items = [i.model_dump() for i in req.items]
    if req.deck and req.since_version:
        items += _not_found(lambda: printorders.added_since(req.deck, req.since_version, only_missing=req.only_missing))
    elif req.deck:
        deck = _not_found(storage.load, req.deck)
        items += printorders.deck_items(deck, names=req.names, only_missing=req.only_missing)
    if req.text and req.text.strip():
        from ..deck import parse_decklist

        parsed = parse_decklist(req.text)
        items += [{"kind": "card", "name": n, "qty": 1, "source": "Liste"} for n in parsed.commanders]
        items += [{"kind": "card", "name": e.name, "qty": e.qty, "source": "Liste"} for e in parsed.entries]
    if req.url and req.url.strip():
        try:
            data = await importers.import_url(req.url)
        except HttpError as exc:  # before RuntimeError: HttpError is one
            raise HTTPException(404 if exc.status == 404 else 502, "Deck nicht gefunden – ist es öffentlich?"
                                if exc.status == 404 else exc.friendly) from exc  # fmt: skip
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Seite nicht erreichbar: {_error_text(exc)}") from exc
        label = f"{data.get('name') or 'Deck'} ({data.get('site')})"
        items += [{"kind": "card", "name": n, "qty": 1, "source": label} for n in data["commanders"]]
        for line in data["cards"]:
            qty, name = line.split(" ", 1)
            items.append({"kind": "card", "name": name, "qty": int(qty), "source": label})
    if not items:
        raise HTTPException(400, "Nichts hinzuzufügen" + (" – die Sammlung hat schon alle Karten." if req.only_missing else "."))
    try:
        return await printorders.add(oid, items)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class OrderQty(BaseModel):
    qty: int = Field(ge=0, le=printorders.MAX_QTY)


@app.patch("/api/orders/{oid}/items/{item_id}")
async def api_order_item(oid: str, item_id: str, req: OrderQty) -> dict[str, Any]:
    return _not_found(printorders.update_item, oid, item_id, req.qty)


@app.delete("/api/orders/{oid}/items")
async def api_order_remove(oid: str, item: str | None = None, source: str | None = None) -> dict[str, Any]:
    if not item and not source:
        raise HTTPException(400, "item oder source angeben")
    try:
        return printorders.remove(oid, item_id=item, source=source)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/decks/{slug}/added")
async def api_deck_added(slug: str, since: int, only_missing: bool = False) -> dict[str, Any]:
    """Cards a rebuild brought in since version ``since`` (for "add to a collective order?")."""
    items = _not_found(lambda: printorders.added_since(slug, since, only_missing=only_missing))
    return {"version": storage.load(slug).get("version"), "since": since, "items": items,
            "cards": sum(i["qty"] for i in items)}  # fmt: skip


@app.get("/api/tokens/search")
async def api_token_search(q: str) -> list[dict[str, Any]]:
    """Tokens and emblems by name (Scryfall), one entry per artwork: ``[{name, type_line, id, image}]``."""
    if len(q.strip()) < 2:
        return []
    try:
        res = await scryfall.search(f'(t:token or t:emblem) name:"{q.strip()}"', order="released", unique="art", max_results=60)
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc
    out = []
    for c in res["cards"]:
        cid = c.get("id")
        if cid and c.get("name"):
            out.append({"name": c["name"], "type_line": c.get("type_line", ""), "id": cid,
                        "image": scryfall.compact(c).get("image"), "set_name": c.get("set_name")})  # fmt: skip
    return out


@app.get("/api/precons")
async def api_precons(q: str = "", limit: int = 60) -> list[dict[str, Any]]:
    try:
        return await precons.search(q, limit=min(limit, 200))
    except Exception as exc:  # offline or MTGJSON down
        raise HTTPException(502, f"MTGJSON nicht erreichbar: {_error_text(exc)}") from exc


@app.get("/api/precons/{file_name}")
async def api_precon(file_name: str) -> dict[str, Any]:
    try:
        p = await precons.load(file_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(404 if exc.status == 404 else 502, exc.friendly) from exc
    except Exception as exc:
        raise HTTPException(502, f"MTGJSON nicht erreichbar: {_error_text(exc)}") from exc
    try:  # commander images for the preview
        data, _, _ = await resolve(p["commanders"])
        p["commander_images"] = {n: c.get("image") for n, c in data.items()}
    except Exception:
        p["commander_images"] = {}
    return p


class PreconImport(BaseModel):
    file: str
    bracket: int = Field(2, ge=1, le=5)
    currency: str = Field("eur", pattern="^(eur|usd)$")


@app.post("/api/precons/import")
async def api_precon_import(req: PreconImport) -> dict[str, Any]:
    try:
        return await precons.import_precon(req.file, bracket=req.bracket, currency=req.currency)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"MTGJSON nicht erreichbar: {_error_text(exc)}") from exc


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

    async def finish(job: Job, ok: bool, answer: str, _structured: Any = None) -> None:
        if ok and answer:
            cards = await _card_refs(answer)
            entry = storage.add_question(slug, question, answer, version=deck.get("version"), cards=cards)
            job.emit(type="answer", entry=entry)
        elif ok:
            job.emit(type="error", text="Keine Antwort erhalten.")
        job.emit(type="done", ok=bool(ok and answer), deck=None)

    prompt = ask_prompt(question, deck, storage.questions(slug))
    return _start(prompt, req.model, read_only=True, finish=finish)


@app.get("/api/chats")
async def api_chats() -> list[dict[str, Any]]:
    return chat.chats()


@app.get("/api/chats/{chat_id}")
async def api_chat_get(chat_id: str) -> dict[str, Any]:
    try:
        return chat.get(chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


class ChatRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@app.put("/api/chats/{chat_id}")
async def api_chat_rename(chat_id: str, req: ChatRename) -> dict[str, Any]:
    try:
        return chat.rename(chat_id, req.title)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/chats/{chat_id}")
async def api_chat_delete(chat_id: str) -> dict[str, bool]:
    try:
        chat.delete(chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> dict[str, Any]:
    """A question to the whole app: a read-only Claude run with the app overview; the answer goes into the conversation."""
    _require_ai()  # before a new conversation is created
    question = req.question.strip()
    if req.chat_id:
        try:
            conv = chat.get(req.chat_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    else:
        conv = chat.create(question)
    chat_id = conv["id"]

    async def finish(job: Job, ok: bool, answer: str, _structured: Any = None) -> None:
        if ok and answer:
            entry = chat.add(chat_id, question, answer, cards=await _card_refs(answer), decks=_deck_refs(answer),
                             deep=req.deep)  # fmt: skip
            job.emit(type="answer", entry=entry, chat_id=chat_id)
        elif ok:
            job.emit(type="error", text="Keine Antwort erhalten.")
        if not (ok and answer) and not req.chat_id:  # a new conversation without an answer is not kept
            try:
                if not chat.get(chat_id).get("messages"):
                    chat.delete(chat_id)
            except FileNotFoundError:
                pass
        job.emit(type="done", ok=bool(ok and answer), deck=None)

    prompt = chat_prompt(question, conv.get("messages") or [])
    if req.deep:
        started = _start(prompt, META_MODEL, read_only=True, finish=finish, effort=META_EFFORT)
    else:
        started = _start(prompt, req.model, read_only=True, finish=finish)
    return {**started, "chat_id": chat_id, "title": conv["title"]}


@app.delete("/api/decks/{slug}/questions")
async def api_questions_delete(slug: str, id: str | None = None) -> dict[str, int]:
    return {"deleted": storage.delete_questions(slug, id)}


@app.post("/api/find-commander")
async def api_find_commander(req: FinderRequest) -> dict[str, str]:
    if not (req.prompt.strip() or req.feel or req.colors or req.themes):
        raise HTTPException(400, "Beantworte mindestens eine Frage oder beschreibe deinen Wunsch.")
    output_format = {"type": "json_schema", "schema": SUGGESTION_SCHEMA}
    return _start(finder_prompt(req), req.model, output_format)


class BlacklistUpdate(BaseModel):
    add: list[str] = []
    remove: list[str] = []


class OpponentIn(BaseModel):
    commanders: list[str] | None = Field(None, max_length=2)
    label: str | None = None
    player: str | None = None
    bracket: int | None = Field(None, ge=1, le=5)
    table_rule: str | None = None
    tags: list[str] | None = None
    note: str = Field("", max_length=1000)
    remove_note: str | None = None


@app.get("/api/opponents")
async def api_opponents() -> dict[str, Any]:
    return {"opponents": opponents_mod.all_opponents(), "tags": {k: v[0] for k, v in opponents_mod.TAGS.items()}}


@app.post("/api/opponents")
async def api_opponent_create(req: OpponentIn) -> dict[str, Any]:
    changes = req.model_dump(exclude_unset=True, exclude={"commanders", "note", "remove_note"})
    try:
        o = await opponents_mod.create(req.commanders or [], note=req.note, **changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return opponents_mod.describe(o)


@app.get("/api/opponents/{opp_id}")
async def api_opponent(opp_id: str) -> dict[str, Any]:
    o = opponents_mod.get(opp_id)
    if o is None:
        raise HTTPException(404, "Unbekanntes Gegnerdeck")
    return opponents_mod.describe(o)


@app.patch("/api/opponents/{opp_id}")
async def api_opponent_update(opp_id: str, req: OpponentIn) -> dict[str, Any]:
    changes = req.model_dump(exclude_unset=True, exclude={"commanders", "note", "remove_note"})
    try:
        o = await opponents_mod.update(opp_id, commanders=req.commanders, add_note=req.note, remove_note=req.remove_note, **changes)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return opponents_mod.describe(o)


@app.delete("/api/opponents/{opp_id}")
async def api_opponent_delete(opp_id: str) -> dict[str, str]:
    try:
        opponents_mod.delete(opp_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": opp_id}


class TableRuleIn(BaseModel):
    name: str | None = None
    description: str | None = None
    add: list[str] = []
    remove: list[str] = []
    max_bracket: int | None = Field(None, ge=1, le=5)
    max_game_changers: int | None = Field(None, ge=0)
    max_tutors: int | None = Field(None, ge=0)
    deck_budget: float | None = Field(None, ge=0)
    currency: str | None = None
    no_proxies: bool | None = None


def _table_changes(req: TableRuleIn) -> dict[str, Any]:
    return {k: v for k, v in req.model_dump(exclude_unset=True).items() if k not in ("add", "remove")}


@app.get("/api/tablerules")
async def api_tablerules() -> dict[str, Any]:
    counts: dict[str, int] = {}
    for d in storage.list_decks():
        if d.get("table_rule"):
            counts[d["table_rule"]] = counts.get(d["table_rule"], 0) + 1
    return {"sets": [{**s, "decks": counts.get(s["id"], 0)} for s in tablerules.all_sets()], "catalog": blacklist.catalog()}


@app.post("/api/tablerules")
async def api_tablerule_create(req: TableRuleIn) -> dict[str, Any]:
    try:
        changes = _table_changes(req)
        name = changes.pop("name", None) or ""
        return await tablerules.create(name, add=req.add, remove=req.remove, **changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/tablerules/{rule_id}")
async def api_tablerule_update(rule_id: str, req: TableRuleIn) -> dict[str, Any]:
    try:
        out = await tablerules.update(rule_id, add=req.add, remove=req.remove, **_table_changes(req))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    out["revalidated"] = await tablerules.revalidate_decks(rule_id)
    return out


@app.delete("/api/tablerules/{rule_id}")
async def api_tablerule_delete(rule_id: str) -> dict[str, Any]:
    rs = tablerules.get(rule_id)
    if rs is None:
        raise HTTPException(404, f"Unbekannte Tischregel: {rule_id}")
    cleared = []  # decks first: if this stops halfway, the rule still exists and no deck points to nothing
    for d in storage.list_decks():
        if d.get("table_rule") == rule_id:
            await _set_table_rule(d["slug"], None, f"Tischregel „{rs['name']}“ gelöscht")
            cleared.append(d["slug"])
    tablerules.delete(rule_id)
    return {"deleted": rule_id, "decks": cleared}


@app.get("/api/tablerules/{rule_id}/decks")
async def api_tablerule_decks(rule_id: str) -> list[dict[str, Any]]:
    try:
        return await tablerules.check_all(rule_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


class DeckTableRule(BaseModel):
    table_rule: str | None = None


@app.put("/api/decks/{slug}/table-rule")
async def api_deck_table_rule(slug: str, req: DeckTableRule) -> dict[str, Any]:
    try:
        deck = await _set_table_rule(slug, req.table_rule or None)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"slug": slug, "table_rule": deck.get("table_rule"), "validation": deck.get("validation")}


@app.get("/api/blacklist")
async def api_blacklist() -> dict[str, Any]:
    return {"cards": blacklist.load(), "rules": blacklist.rules(), "catalog": blacklist.catalog()}


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
            "game_changer", "scryfall_uri", "color_identity", "roles")  # fmt: skip
    deck["card_data"] = {n: {k: c.get(k) for k in keep} for n, c in card_data.items()}
    entries = [DeckEntry(c["name"], c.get("qty", 1)) for c in deck.get("cards", [])]
    deck["export_text"] = to_text(deck.get("commanders", []), entries)
    deck["health"] = health.check(deck)
    rs = tablerules.get(deck.get("table_rule"))
    deck["table_rule_info"] = {"id": rs["id"], "name": rs["name"], "summary": tablerules.summary_lines(rs)} if rs else None
    against = [opponents_mod.get(i) for i in deck.get("built_against") or []]
    deck["built_against_info"] = [{"id": o["id"], "title": opponents_mod.title(o)} for o in against if o]
    return deck


@app.post("/api/decks/{slug}/validate")
async def api_validate(slug: str) -> dict[str, Any]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    result = await deckedit.revalidate(deck)
    storage.save(deck, expect_version=deck.get("version"))  # never overwrite a newer save with this copy
    return result


class AddCard(BaseModel):
    name: str
    qty: int = Field(default=1, ge=1, le=99)
    category: str | None = None


class EditRequest(BaseModel):
    add: list[AddCard] = []
    remove: list[str] = []
    set_qty: dict[str, int] = {}
    set_category: dict[str, str] = {}
    note: str = ""


@app.post("/api/decks/{slug}/cards")
async def api_edit_cards(slug: str, req: EditRequest) -> dict[str, Any]:
    """Change cards directly (no Claude run): re-validates and saves a new version."""
    try:
        return await deckedit.edit_deck(
            slug, add=[a.model_dump() for a in req.add], remove=req.remove, set_qty=req.set_qty,
            set_category=req.set_category, note=req.note,
        )  # fmt: skip
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/decks/{slug}/similar")
async def api_similar(slug: str, card: str, limit: int = 12) -> list[dict[str, Any]]:
    try:
        return await deckedit.similar_cards(storage.load(slug), card, limit=min(limit, 30))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc


@app.get("/api/decks/{slug}/role-candidates")
async def api_role_candidates(slug: str, role: str, limit: int = 18) -> list[dict[str, Any]]:
    try:
        deck = storage.load(slug)
        budget = deck.get("budget") if not deck.get("proxy") else None
        # cards for one slot should not blow the budget: at most a tenth of it per card
        return await deckedit.role_candidates(deck, role, limit=min(limit, 40), max_price=budget / 10 if budget else None)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc


@app.get("/api/decks/{slug}/export/{fmt}")
async def api_export(slug: str, fmt: str) -> Response:
    """Download the deck: text (Moxfield/Archidekt), cockatrice (.cod) or tts (Tabletop Simulator)."""
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    names = deck.get("commanders", []) + [c["name"] for c in deck.get("cards", [])]
    if fmt == "text":
        entries = [DeckEntry(c["name"], c.get("qty", 1)) for c in deck.get("cards", [])]
        body, media, ext = to_text(deck.get("commanders", []), entries), "text/plain; charset=utf-8", "txt"
    elif fmt in ("cockatrice", "tts"):
        try:
            card_data, _, _ = await resolve(names)
        except Exception as exc:
            raise HTTPException(502, f"Kartendaten nicht erreichbar: {_error_text(exc)}") from exc
        if fmt == "cockatrice":
            body, media, ext = exports.to_cockatrice(deck, card_data), "application/xml", "cod"
        else:
            body, media, ext = json.dumps(exports.to_tts(deck, card_data), ensure_ascii=False, indent=1), "application/json", "json"
    else:
        raise HTTPException(404, "Format: text, cockatrice oder tts")
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{deck["slug"]}.{ext}"'})


@app.get("/api/decks/{slug}/tokens")
async def api_tokens(slug: str) -> list[dict[str, Any]]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        return await deck_tokens(deck.get("commanders", []) + [c["name"] for c in deck.get("cards", [])])
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc


@app.get("/api/decks/{slug}/ownership")
async def api_ownership(slug: str) -> dict[str, Any]:
    try:
        deck = storage.load(slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return await collection.deck_ownership(deck)


# --- my collection -------------------------------------------------------------------------------


class CollectionItem(BaseModel):
    name: str | None = None
    qty: int = Field(default=1, ge=1, le=9999)
    proxy: bool = False
    foil: bool = False
    lang: str | None = None
    set: str | None = None
    collector_number: str | None = None
    scryfall_id: str | None = None
    printing: dict[str, Any] | None = None  # a printing from /api/cards/prints (artwork picker)


class CollectionAdd(BaseModel):
    items: list[CollectionItem]


class CollectionImport(BaseModel):
    text: str = Field(max_length=5_000_000)
    proxy: bool = False
    replace: bool = False


class EntryUpdate(BaseModel):
    qty: int | None = None
    proxy: bool | None = None
    foil: bool | None = None
    lang: str | None = None
    note: str | None = None
    printing: dict[str, Any] | None = None


@app.get("/api/collection")
async def api_collection() -> dict[str, Any]:
    entries = collection.load()
    usage = collection.decks_by_card() if entries else {}
    return {"entries": entries, "summary": collection.summary(entries),
            "decks": {name: [d["name"] for d in decks] for name, decks in usage.items()}}  # fmt: skip


@app.post("/api/collection")
async def api_collection_add(req: CollectionAdd) -> dict[str, Any]:
    return await collection.add([i.model_dump() for i in req.items])


@app.post("/api/collection/import")
async def api_collection_import(req: CollectionImport) -> dict[str, Any]:
    try:
        return await collection.import_text(req.text, proxy=req.proxy, replace=req.replace)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/collection/{entry_id}")
async def api_collection_update(entry_id: str, req: EntryUpdate) -> dict[str, Any]:
    try:
        entry = collection.update(entry_id, **req.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(404, "Eintrag nicht gefunden") from exc
    return {"entry": entry}


@app.delete("/api/collection/{entry_id}")
async def api_collection_delete(entry_id: str) -> dict[str, int]:
    return {"deleted": collection.delete(entry_id)}


@app.delete("/api/collection")
async def api_collection_clear(confirm: bool = False) -> dict[str, int]:
    if not confirm:
        raise HTTPException(400, "Zum Leeren der ganzen Sammlung confirm=true mitschicken.")
    return {"deleted": collection.delete()}


@app.get("/api/collection/export")
async def api_collection_export() -> Response:
    return Response(collection.to_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="sammlung.csv"'})  # fmt: skip


@app.get("/api/cards/prints")
async def api_prints(name: str, page: int = 1) -> dict[str, Any]:
    try:
        return await scryfall.prints(name, page=max(1, page))
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc


@app.delete("/api/decks/{slug}")
async def api_delete(slug: str) -> dict[str, Any]:
    """Moves the deck (with versions, questions and games) to the trash."""
    return {"ok": True, "trash_id": _not_found(storage.delete, slug)}


# --- backups ------------------------------------------------------------------------------------


@app.get("/api/backups")
async def api_backups() -> dict[str, Any]:
    return {"backups": backup.backups(), "dir": str(backup.BACKUP_DIR), "keep_auto": backup.KEEP_AUTO}


@app.post("/api/backups")
async def api_backup_create() -> dict[str, Any]:
    return backup.create("manuell")


@app.get("/api/backups/{name}")
async def api_backup_download(name: str) -> FileResponse:
    path = _not_found(backup.path_of, name)
    return FileResponse(path, media_type="application/zip", filename=f"mtgdeck-{name}")


@app.post("/api/backups/upload")
async def api_backup_upload(request: Request) -> dict[str, Any]:
    data = await request.body()
    if len(data) > 2_000_000_000:
        raise HTTPException(413, "Datei zu groß")
    try:
        return backup.upload(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/backups/{name}/restore")
async def api_backup_restore(name: str) -> dict[str, Any]:
    if any(not j.done for j in JOBS.values()):
        raise HTTPException(409, "Es läuft gerade ein Auftrag – warte, bis er fertig ist, und stell dann wieder her.")
    try:
        return _not_found(backup.restore, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/trash")
async def api_trash() -> list[dict[str, Any]]:
    return storage.trash()


@app.post("/api/trash/{trash_id}/restore")
async def api_trash_restore(trash_id: str) -> dict[str, Any]:
    return _not_found(storage.restore_deleted, trash_id)


@app.delete("/api/trash/{trash_id}")
async def api_trash_purge(trash_id: str) -> dict[str, int]:
    return {"deleted": _not_found(storage.purge_deleted, trash_id)}


@app.delete("/api/trash")
async def api_trash_purge_all() -> dict[str, int]:
    return {"deleted": storage.purge_deleted()}


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
    image_generator_url: str | None = None
    ai_enabled: bool | None = None


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
    url = (req.image_generator_url or "").strip()
    if url and ("{prompt}" not in url or not url.startswith(("http://", "https://"))):
        raise HTTPException(400, "Bildgenerator: eine http(s)-Adresse mit {prompt} angeben (optional {width} {height} {seed}).")
    return _settings_view(settings_mod.update(req.model_dump(exclude_none=True)))


class PrintRequest(BaseModel):
    source: str = "auto"
    stock: str | None = None
    foil: bool | None = None
    upscale: bool | None = None
    only_missing: bool = False
    tokens: int = Field(default=0, ge=0, le=20)
    version: int | None = None


def _print_deck(slug: str, version: int | None = None) -> dict[str, Any]:
    if printorders.is_order_slug(slug):  # a collective order prints like a deck
        return _not_found(printorders.as_deck, slug)
    deck = _not_found(storage.load_version, slug, version)
    deck["slug"] = storage.slug(slug)
    return deck


@app.get("/api/decks/{slug}/print/plan")
async def api_print_plan(slug: str, source: str = "auto", version: int | None = None, only_missing: bool = False,
                         tokens: int = 0) -> dict[str, Any]:  # fmt: skip
    try:
        return await proxy.plan(_print_deck(slug, version), source=source, only_missing=only_missing, tokens=min(max(tokens, 0), 20))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/decks/{slug}/collection/add-printed")
async def api_add_printed(slug: str, req: PrintRequest) -> dict[str, Any]:
    """Add the printed proxies (with the chosen artwork) to the collection."""
    try:
        plan = await proxy.plan(_print_deck(slug, req.version), source=req.source, only_missing=req.only_missing)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await collection.add_printed(plan)


@app.get("/api/decks/{slug}/print/alternatives")
async def api_print_alternatives(slug: str, card: str, side: str = "front", token: bool = False, page: int = 1) -> dict[str, Any]:
    try:
        return await proxy.alternatives(_print_deck(slug), card, "back" if side == "back" else "front", token=token, page=max(1, page))
    except HttpError as exc:
        raise HTTPException(502, exc.friendly) from exc


class ChooseRequest(BaseModel):
    face: str
    option: dict[str, Any] | None = None


@app.post("/api/decks/{slug}/print/choose")
async def api_print_choose(slug: str, req: ChooseRequest) -> dict[str, Any]:
    return proxy.choose(storage.slug(slug), req.face, req.option)


@app.post("/api/decks/{slug}/print/upload")
async def api_print_upload(slug: str, request: Request, face: str, filename: str = "", bleed: str = "auto",
                           choose: bool = True) -> dict[str, Any]:  # fmt: skip
    """Own image for one card face (raw image body): made print-ready, stored and – by default – chosen."""
    _print_deck(slug)  # 404 for unknown decks/orders
    if int(request.headers.get("content-length") or 0) > proxy.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Das Bild ist zu groß (höchstens {proxy.MAX_UPLOAD_BYTES // 1_000_000} MB).")
    data = await request.body()
    try:
        option = await asyncio.to_thread(proxy.save_upload, storage.slug(slug), face, data, filename, bleed)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if choose:
        proxy.choose(storage.slug(slug), face, option)
    return option


@app.get("/api/decks/{slug}/print/uploads/{name}")
async def api_print_upload_file(slug: str, name: str) -> FileResponse:
    if not proxy.UPLOAD_NAME.match(name):
        raise HTTPException(404, "Kein Bild")
    path = proxy.order_dir(storage.slug(slug)) / proxy.UPLOADS / name
    if not path.is_file():
        raise HTTPException(404, "Bild nicht gefunden")
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@app.delete("/api/decks/{slug}/print/uploads/{key}")
async def api_print_upload_delete(slug: str, key: str) -> dict[str, bool]:
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        raise HTTPException(404, "Kein Bild")
    try:
        proxy.delete_upload(storage.slug(slug), key)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


class TokenQtyRequest(BaseModel):
    counts: dict[str, int | None] = Field(description="token face -> copies (0 = leave out, null = default)")


@app.post("/api/decks/{slug}/print/token-qty")
async def api_print_token_qty(slug: str, req: TokenQtyRequest) -> dict[str, int]:
    """Copies per token for this deck's print (collective orders keep their quantities in the order items)."""
    if slug.startswith(printorders.SLUG_PREFIX):
        raise HTTPException(400, "In einer Sammelbestellung änderst du die Token-Anzahl in der Positionsliste.")
    if any(v is not None and not 0 <= v <= proxy.TOKEN_QTY_MAX for v in req.counts.values()):
        raise HTTPException(400, f"Anzahl pro Token: 0 bis {proxy.TOKEN_QTY_MAX}.")
    return proxy.set_token_qty(storage.slug(slug), req.counts)


@app.post("/api/decks/{slug}/print/prepare")
async def api_print_prepare(slug: str, req: PrintRequest) -> dict[str, str]:
    deck = _print_deck(slug, req.version)

    async def runner(job: Job) -> None:
        job.emit(type="status", text="Plane Bilder …")

        def progress(done: int, total: int, name: str) -> None:
            job.emit(type="progress", done=done, total=total, text=name)

        try:
            result = await proxy.prepare(
                deck, source=req.source, stock=req.stock, foil=req.foil, upscale=req.upscale,
                only_missing=req.only_missing, tokens=req.tokens, progress=progress,
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


@app.get("/api/decks/{slug}/print/files")
async def api_print_files(slug: str) -> dict[str, bool]:
    s = storage.slug(slug)
    return {kind: (proxy.order_dir(s) / f"{s}.{kind}").exists() for kind in ("xml", "pdf")}


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
    try:
        made = backup.auto_backup()
        if made:
            print(f"Automatische Sicherung: {backup.BACKUP_DIR / made['name']}")
    except Exception as exc:  # a failed backup must not stop the GUI
        print(f"Automatische Sicherung fehlgeschlagen: {exc}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
