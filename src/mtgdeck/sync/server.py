"""The sync server: a small FastAPI app over ``SyncStore`` plus devices and pairing codes (same SQLite file).

Run: ``mtg-sync-server`` (the Docker image, see ``deploy/sync`` and ``docs/sync-server-hetzner.md``).
Environment: ``MTG_SYNC_DATA`` (data folder, default ``./sync-data``), ``MTG_SYNC_PUBLIC_URL`` (the https address,
used in pairing links), ``MTG_SYNC_HOST`` / ``MTG_SYNC_PORT`` (default ``0.0.0.0:8080``).
``mtg-sync-server pair`` prints a pairing code for the first device, ``devices`` lists the paired ones, ``backup`` /
``restore <file>`` save and restore the store.

Routes (JSON; document content as base64):
- ``GET /api/health`` – no login; with ``server_id`` (another server?) and ``epoch`` (restored from a backup?);
- ``POST /api/pair/claim`` – a pairing code becomes a device token (no login, failed attempts are limited);
- ``POST /api/pair/start`` – a paired device creates a code for the next one;
- ``GET /api/sync/changes?since=`` / ``PUT /api/sync/doc`` – the protocol of ``SyncStore`` (409 + current document);
- ``GET /api/devices``, ``DELETE /api/devices/{id}``, ``POST /api/presence``.

Tokens are stored as sha256 only; every request with a token updates the device's ``last_seen``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .files import kind_of
from .store import SyncConflict, SyncStore, decode_b64, encode

PROTOCOL = 1
PAIR_MINUTES = 15
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to type from a screen
ONLINE_SECONDS = 150  # the desktop app reports every minute
SEEN_EVERY = 20  # seconds between two last_seen writes of one device
MAX_DOC_BYTES = 64 * 1024 * 1024
MAX_INFO_BYTES = 4096
CLAIM_FAILS, CLAIM_WINDOW = 20, 600  # at most 20 wrong codes per 10 minutes
BACKUP_KEEP = 14
APP_DIR = Path(__file__).parent / "app"  # the phone app ("Am Tisch"), served under /app/
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.scryfall.io; "
           "connect-src 'self' https://api.scryfall.com; manifest-src 'self'; worker-src 'self'; base-uri 'none'; "
           "form-action 'self'; frame-ancestors 'none'")  # fmt: skip


def data_dir() -> Path:
    return Path(os.environ.get("MTG_SYNC_DATA", "sync-data"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_code(text: str) -> str:
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


class Devices:
    """Paired devices (token hashes, last seen, presence info) and one-time pairing codes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                    created REAL NOT NULL, last_seen REAL, info TEXT);
                CREATE TABLE IF NOT EXISTS pairing (
                    code_hash TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL, created_by TEXT, used REAL);
            """)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        seen = row["last_seen"]
        try:
            info = json.loads(row["info"]) if row["info"] else {}
        except ValueError:
            info = {}
        return {"id": row["id"], "name": row["name"], "kind": row["kind"], "created": row["created"], "last_seen": seen,
                "online": bool(seen and time.time() - seen < ONLINE_SECONDS), "info": info}  # fmt: skip

    def new_code(self, by: str = "", minutes: int = PAIR_MINUTES) -> dict[str, Any]:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        now = time.time()
        with self._lock, self._db() as db:
            db.execute("DELETE FROM pairing WHERE expires < ?", (now - 86400,))
            db.execute("INSERT INTO pairing(code_hash, created, expires, created_by) VALUES (?,?,?,?)",
                       (_sha(code), now, now + minutes * 60, by))  # fmt: skip
        return {"code": format_code(code), "expires": now + minutes * 60, "minutes": minutes}

    def claim(self, code: str, name: str, kind: str) -> dict[str, Any] | None:
        """A valid, unused code → a new device with its token (shown once); ``None`` for a wrong/old code."""
        now = time.time()
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM pairing WHERE code_hash = ? AND used IS NULL AND expires > ?",
                             (_sha(normalize_code(code)), now)).fetchone()  # fmt: skip
            if row is None:
                return None
            db.execute("UPDATE pairing SET used = ? WHERE code_hash = ?", (now, row["code_hash"]))
            token, did = secrets.token_urlsafe(32), uuid.uuid4().hex[:12]
            db.execute("INSERT INTO devices(id, name, kind, token_hash, created, last_seen) VALUES (?,?,?,?,?,?)",
                       (did, name.strip()[:60] or "Gerät", kind, _sha(token), now, now))  # fmt: skip
        return {"token": token, "device_id": did, "name": name.strip()[:60] or "Gerät"}

    def auth(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM devices WHERE token_hash = ?", (_sha(token),)).fetchone()
            if row is None:
                return None
            now = time.time()
            if not row["last_seen"] or now - row["last_seen"] > SEEN_EVERY:
                db.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now, row["id"]))
        return self._row(row)

    def list(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM devices ORDER BY created").fetchall()
        return [self._row(r) for r in rows]

    def revoke(self, device_id: str) -> bool:
        with self._lock, self._db() as db:
            return db.execute("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount > 0

    def presence(self, device_id: str, info: dict[str, Any]) -> None:
        text = json.dumps(info, ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_INFO_BYTES:
            raise ValueError("Angaben zu groß")
        with self._db() as db:
            db.execute("UPDATE devices SET info = ?, last_seen = ? WHERE id = ?", (text, time.time(), device_id))


def daily_backup(store: SyncStore, folder: Path, keep: int = BACKUP_KEEP) -> Path | None:
    """One copy of the store per day in ``folder`` (``sync-YYYYMMDD.sqlite``), the newest ``keep`` stay."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"sync-{time.strftime('%Y%m%d')}.sqlite"
    made = None
    if not target.exists():
        made = store.backup(target)
    for old in sorted(folder.glob("sync-*.sqlite"))[:-keep]:
        old.unlink(missing_ok=True)
    return made


def _out(doc: dict[str, Any]) -> dict[str, Any]:
    return {**doc, "data": encode(doc.get("data"))}


class ClaimRequest(BaseModel):
    code: str = Field(min_length=4, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["pc", "phone", "other"] = "pc"


class DocRequest(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    kind: str
    data: str | None = None
    base_seq: int | None = None
    deleted: bool = False


class PresenceRequest(BaseModel):
    info: dict[str, Any] = Field(default_factory=dict)


_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>MTG-Deckbuilder Sync</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:2rem auto;padding:0 1rem;color:#1d1d1f;background:#fafafa}
@media (prefers-color-scheme:dark){body{color:#eee;background:#16161a}} code{font-size:1.4rem;letter-spacing:.1em}</style>
</head><body><h1>MTG-Deckbuilder Sync</h1>__BODY__</body></html>"""


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(_PAGE.replace("__BODY__", body))


def create_app(data: Path | None = None, public_url: str | None = None) -> FastAPI:
    folder = Path(data) if data else data_dir()
    db_file = folder / "sync.sqlite"
    store, devices = SyncStore(db_file), Devices(db_file)
    store.server_id(), store.epoch()
    fails: deque[float] = deque()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def backups() -> None:
            while True:
                try:
                    await asyncio.to_thread(daily_backup, store, folder / "backups")
                except Exception as exc:  # a failed backup must not stop the server
                    print(f"Sicherung fehlgeschlagen: {exc}", flush=True)
                await asyncio.sleep(3600)

        task = asyncio.create_task(backups())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="MTG-Deckbuilder Sync", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.devices = store, devices

    @app.middleware("http")
    async def _headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith("/app"):
            response.headers.setdefault("Content-Security-Policy", APP_CSP)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("Cache-Control", "no-cache")  # revalidate (ETag); the service worker caches
        return response

    @app.get("/app", include_in_schema=False)
    async def app_redirect() -> RedirectResponse:
        return RedirectResponse("/app/")

    @app.get("/app/", include_in_schema=False)
    async def app_index() -> FileResponse:
        return FileResponse(APP_DIR / "index.html", media_type="text/html")

    app.mount("/app", StaticFiles(directory=APP_DIR), name="app")

    def base_url(request: Request) -> str:
        return (public_url or os.environ.get("MTG_SYNC_PUBLIC_URL") or str(request.base_url)).rstrip("/")

    def device(authorization: str = Header(default="")) -> dict[str, Any]:
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
        dev = devices.auth(token)
        if dev is None:
            raise HTTPException(401, "Dieses Gerät ist nicht (mehr) angemeldet – bitte neu koppeln.")
        return dev

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return _page("<p>Der Sync-Server läuft. Geräte verbindest du in der App unter "
                     "<b>Einstellungen → Sync</b>.</p><p>Vorschau der Handy-App mit Beispieldaten: "
                     "<a href=\"/app/\">Am Tisch</a></p>")  # fmt: skip

    @app.get("/koppeln", response_class=HTMLResponse)
    async def pair_page() -> HTMLResponse:
        return _page("<p>Kopplungscode: <code id=c>–</code></p><p>Am PC: <b>Einstellungen → Sync</b> öffnen und "
                     "diesen Link (oder Server-Adresse und Code) einfügen. Die Handy-App folgt.</p>"
                     "<script>document.getElementById('c').textContent=decodeURIComponent(location.hash.slice(1))||'–'"
                     "</script>")  # fmt: skip

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": "mtgdeck-sync", "protocol": PROTOCOL, "server_id": store.server_id(),
                "epoch": store.epoch()}  # fmt: skip

    @app.post("/api/pair/claim")
    async def pair_claim(req: ClaimRequest, request: Request) -> dict[str, Any]:
        now = time.time()
        while fails and now - fails[0] > CLAIM_WINDOW:
            fails.popleft()
        if len(fails) >= CLAIM_FAILS:
            raise HTTPException(429, "Zu viele falsche Codes – bitte in ein paar Minuten noch einmal versuchen.")
        got = devices.claim(req.code, req.name, req.kind)
        if got is None:
            fails.append(now)
            raise HTTPException(403, "Der Kopplungscode stimmt nicht oder ist abgelaufen.")
        return {**got, "server": base_url(request), "server_id": store.server_id()}

    @app.post("/api/pair/start")
    async def pair_start(request: Request, me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        got = devices.new_code(by=me["id"])
        return {**got, "link": f"{base_url(request)}/koppeln#{got['code']}", "server": base_url(request)}

    @app.get("/api/sync/changes")
    async def changes(since: int = 0, limit: int | None = None, me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        page = await asyncio.to_thread(store.changes, max(0, since), limit and max(1, min(limit, 1000)))
        return {**page, "docs": [_out(d) for d in page["docs"]], "server_id": store.server_id()}

    @app.put("/api/sync/doc")
    async def put_doc(req: DocRequest, me: dict[str, Any] = Depends(device)) -> Any:
        if kind_of(req.path) != req.kind:
            raise HTTPException(400, f"Pfad und Art passen nicht: {req.path} ({req.kind})")
        if req.data is not None and len(req.data) * 3 // 4 > MAX_DOC_BYTES:
            raise HTTPException(413, "Datei zu groß")
        try:
            data = None if req.deleted else decode_b64(req.data)
        except ValueError:
            raise HTTPException(400, "Inhalt ist kein gültiges Base64") from None
        try:
            return await asyncio.to_thread(lambda: store.put(req.path, kind=req.kind, data=data, base_seq=req.base_seq,
                                                             deleted=req.deleted, device=me["id"]))  # fmt: skip
        except SyncConflict as exc:
            return JSONResponse({"detail": str(exc), "current": _out(exc.current)}, status_code=409)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/devices")
    async def list_devices(me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        return {"devices": [{**d, "this": d["id"] == me["id"]} for d in devices.list()], "now": time.time()}

    @app.delete("/api/devices/{device_id}")
    async def revoke(device_id: str, me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        if not devices.revoke(device_id):
            raise HTTPException(404, "Gerät nicht gefunden")
        return {"ok": True, "self": device_id == me["id"]}

    @app.post("/api/presence")
    async def presence(req: PresenceRequest, me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        try:
            devices.presence(me["id"], req.info)
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from None
        return {"ok": True, "now": time.time()}

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mtg-sync-server", description="Sync-Server des MTG-Deckbuilders")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Server starten (Standard)")
    pair = sub.add_parser("pair", help="Kopplungscode für ein neues Gerät ausgeben")
    pair.add_argument("--minuten", type=int, default=PAIR_MINUTES)
    sub.add_parser("devices", help="gekoppelte Geräte auflisten")
    rev = sub.add_parser("revoke", help="ein Gerät abmelden")
    rev.add_argument("device_id")
    sub.add_parser("backup", help="jetzt eine Sicherung anlegen")
    res = sub.add_parser("restore", help="eine Sicherung zurückspielen (die Geräte gleichen danach alles neu ab)")
    res.add_argument("datei")
    args = parser.parse_args(argv)
    folder = data_dir()
    db_file = folder / "sync.sqlite"

    if args.cmd == "pair":
        got = Devices(db_file).new_code(by="console", minutes=args.minuten)
        url = os.environ.get("MTG_SYNC_PUBLIC_URL", "").rstrip("/")
        print(f"Kopplungscode: {got['code']}  (gültig {got['minutes']} Minuten, nur einmal verwendbar)")
        if url:
            print(f"Kopplungslink: {url}/koppeln#{got['code']}")
        print("In der App: Einstellungen → Sync → Link (oder Server-Adresse und Code) einfügen.")
    elif args.cmd == "devices":
        for d in Devices(db_file).list():
            seen = time.strftime("%d.%m.%Y %H:%M", time.localtime(d["last_seen"])) if d["last_seen"] else "nie"
            print(f"{d['id']}  {d['kind']:<5}  {d['name']:<30}  zuletzt {seen}{'  (online)' if d['online'] else ''}")
    elif args.cmd == "revoke":
        print("abgemeldet" if Devices(db_file).revoke(args.device_id) else "kein Gerät mit dieser Kennung")
    elif args.cmd == "backup":
        target = folder / "backups" / f"manuell-{time.strftime('%Y%m%d-%H%M%S')}.sqlite"  # not pruned
        print(SyncStore(db_file).backup(target))
    elif args.cmd == "restore":
        safety = SyncStore(db_file).restore_from(Path(args.datei))
        print(f"Zurückgespielt: {args.datei}. Der vorherige Stand liegt in {safety}.")
        print("Die Geräte merken das beim nächsten Abgleich und führen ihre Daten neu zusammen.")
    else:
        import uvicorn

        host = os.environ.get("MTG_SYNC_HOST", "0.0.0.0")
        port = int(os.environ.get("MTG_SYNC_PORT", 8080))
        uvicorn.run(create_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
