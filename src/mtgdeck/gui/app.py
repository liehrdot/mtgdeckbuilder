"""Local web GUI. Builds decks by running Claude Code (Claude Agent SDK) with the project's
skills and the 'mtg' MCP server, and shows saved decks with images, stats and bracket check.

Start: ``uv run mtg-gui`` -> http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import brackets, carddb, scryfall, storage
from ..cards import resolve
from ..deck import DeckEntry, to_text
from ..http import HttpError
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

    def emit(self, **event: Any) -> None:
        self.events.append(event)
        self.changed.set()


JOBS: dict[str, Job] = {}


def _tool_summary(name: str, args: dict[str, Any]) -> str:
    for key in ("query", "commander", "name", "names", "tags", "url", "slug", "skill"):
        if key in args and args[key]:
            val = args[key]
            if isinstance(val, list):
                val = ", ".join(map(str, val[:5])) + (" …" if len(val) > 5 else "")
            return f"{key}={val}"
    return ""


async def _run_claude(job: Job, prompt: str, model: str | None) -> None:
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

    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        setting_sources=["project"],  # loads .claude/skills and CLAUDE.md
        mcp_servers={"mtg": {"type": "stdio", "command": sys.executable, "args": ["-m", "mtgdeck.mcp_server"]}},
        strict_mcp_config=True,
        allowed_tools=["Skill", "ToolSearch", "Read", "Glob", "Grep", "mcp__mtg"],
        permission_mode="dontAsk",
        model=model or None,
        max_turns=int(os.environ.get("MTG_MAX_TURNS", 120)),
    )
    ok = False
    job.emit(type="status", text="Starte Claude Code …")
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        job.emit(type="text", text=block.text.strip())
                    elif isinstance(block, ToolUseBlock):
                        name = block.name.removeprefix("mcp__mtg__")
                        job.emit(type="tool", name=name, summary=_tool_summary(name, block.input or {}))
            elif isinstance(msg, ResultMessage):
                ok = not msg.is_error
                cost = f" · Kosten ${msg.total_cost_usd:.2f}" if getattr(msg, "total_cost_usd", None) else ""
                job.emit(type="result", text=f"{'Fertig' if ok else 'Abgebrochen'} nach {msg.num_turns} Schritten{cost}")
    except asyncio.CancelledError:
        job.emit(type="error", text="Abgebrochen.")
    except Exception as exc:  # surface everything in the UI
        job.emit(type="error", text=f"{type(exc).__name__}: {exc}")

    deck = next((d for d in storage.list_decks() if (d.get("updated") or "") >= _iso(job.started)), None)
    job.emit(type="done", ok=ok and deck is not None, deck=deck["slug"] if deck else None)
    job.done = True


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts - 1))


def _start(prompt: str, model: str | None) -> dict[str, str]:
    job = Job(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job
    job.task = asyncio.create_task(_run_claude(job, prompt, model))
    return {"job": job.id}


class BuildRequest(BaseModel):
    commander: str
    partner: str | None = None
    bracket: int = Field(3, ge=1, le=5)
    budget: float | None = None
    currency: str = "eur"
    strategy: str | None = None
    notes: str | None = None
    model: str | None = None


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
    cur = req.currency.upper()
    lines.append(f"- Budget: max. {req.budget:g} {cur} für das ganze Deck" if req.budget else f"- Budget: keins (Preise in {cur})")
    lines.append(f"- Währung für Preise: {req.currency}")
    if req.strategy:
        lines.append(f"- Strategie/Thema: {req.strategy}")
    if req.notes:
        lines.append(f"- Weitere Wünsche: {req.notes}")
    lines += [
        "",
        "Du läufst im GUI-Modus: Stelle keine Rückfragen, triff sinnvolle Annahmen und nenne sie in der Deckbeschreibung.",
        "Speichere das fertige Deck mit `save_deck` und behebe alle Fehler und Bracket-Verstöße, bevor du fertig bist.",
        "Antworte auf Deutsch.",
    ]
    return "\n".join(lines)


class RefineRequest(BaseModel):
    slug: str
    request: str
    model: str | None = None


def refine_prompt(req: RefineRequest) -> str:
    return "\n".join(
        [
            f"Überarbeite das gespeicherte Commander-Deck `{req.slug}` (lade es mit `load_deck`).",
            "Nutze den Skill `commander-deckbuilder` (Abschnitt „Deck überarbeiten“).",
            f"Änderungswunsch: {req.request}",
            "",
            "Du läufst im GUI-Modus: keine Rückfragen. Behalte Bracket und Commander bei, sofern nicht anders gewünscht.",
            f"Speichere das Ergebnis mit `save_deck` und slug=`{req.slug}`. Fasse die Änderungen (rein/raus) kurz zusammen. Antworte auf Deutsch.",
        ]
    )


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
        storage.load(req.slug)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _start(refine_prompt(req), req.model)


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
    keep = ("image", "type_line", "mana_cost", "cmc", "price_eur", "price_usd", "game_changer", "scryfall_uri")
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
    result = await validate_deck(deck["commanders"], lines, int(deck.get("bracket") or 3), currency=deck.get("currency", "eur"))
    result.pop("_card_data", None)
    result.pop("cards", None)
    deck["validation"] = result
    storage.save(deck)
    return result


@app.delete("/api/decks/{slug}")
async def api_delete(slug: str) -> dict[str, bool]:
    for ext in ("json", "txt"):
        path = storage.DECKS_DIR / f"{storage.slug(slug)}.{ext}"
        if path.exists():
            path.unlink()
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
