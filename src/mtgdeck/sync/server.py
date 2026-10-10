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
- ``GET /api/devices``, ``DELETE /api/devices/{id}``, ``POST /api/presence``;
- the phone app ("Am Tisch", ``/app/``): ``GET /api/app/data`` (read model, ETag), ``GET /api/app/status`` (PCs online),
  ``POST /api/app/ops`` (operations, idempotent by id – see ``appops``); ``/`` and ``/koppeln#CODE`` lead to the app.

Tokens are stored as sha256 only; every request with a token updates the device's ``last_seen``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .files import canonical, decode, kind_of
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
# connect-src lists the image CDN too: the service worker (which gets this header with sw.js) fetches the card images
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.scryfall.io; "
           "connect-src 'self' https://api.scryfall.com https://*.scryfall.io; manifest-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; "
           "base-uri 'none'; "
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
                CREATE TABLE IF NOT EXISTS app_ops (
                    id TEXT PRIMARY KEY, at REAL NOT NULL, device TEXT, ok INTEGER NOT NULL, error TEXT);
            """)

    def op_done(self, op_id: str) -> dict[str, Any] | None:
        """The outcome of an operation of the phone app that was already applied (sent twice)."""
        with self._db() as db:
            row = db.execute("SELECT ok, error FROM app_ops WHERE id = ?", (op_id,)).fetchone()
        return {"ok": bool(row["ok"]), "error": row["error"]} if row else None

    def op_record(self, op_id: str, device_id: str, ok: bool, error: str | None = None) -> None:
        with self._lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO app_ops(id, at, device, ok, error) VALUES (?,?,?,?,?)",
                       (op_id, time.time(), device_id, int(ok), error))  # fmt: skip
            db.execute("DELETE FROM app_ops WHERE at < ?", (time.time() - 90 * 86400,))

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


APP_READ = re.compile(r"decks/[^/.][^/]*\.json|decks/\.games/[^/.][^/]*\.json|decks/\.opponents\.json")
OP_ATTEMPTS = 8


def app_snapshot(store: SyncStore) -> dict[str, Any]:
    """The phone app's read model from the store (``appdata.snapshot``)."""
    from . import appdata

    docs: dict[str, Any] = {}
    for path, d in store.live("decks/").items():
        if APP_READ.fullmatch(path):
            try:
                docs[path] = decode("json", d["data"])
            except ValueError:
                continue
    return appdata.snapshot(docs)


def run_op(store: SyncStore, devices: Devices, op: dict[str, Any], device_id: str) -> dict[str, Any]:
    """Apply one operation of the phone app (idempotent by its id); ``{"id", "ok", "error"?}``."""
    from . import appops

    op_id = op.get("id")
    if not isinstance(op_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,40}", op_id):
        return {"id": op_id, "ok": False, "error": "Ungültige Kennung"}
    done = devices.op_done(op_id)
    if done is not None:
        return {"id": op_id, **done}
    for _ in range(OP_ATTEMPTS):
        try:
            paths = appops.paths(op)
            docs, seqs = {}, {}
            for path in paths:
                d = store.get(path)
                alive = d is not None and not d["deleted"]
                docs[path] = decode("json", d["data"]) if alive else None
                seqs[path] = d["seq"] if d is not None else None
            changed = appops.apply(op, docs)
            if changed:
                store.put_many([{"path": p, "kind": "json", "data": canonical("json", v), "base_seq": seqs[p]}
                                for p, v in changed.items()], device=device_id)  # fmt: skip
            devices.op_record(op_id, device_id, True)
            return {"id": op_id, "ok": True}
        except SyncConflict:  # a PC wrote the same document meanwhile: read again
            continue
        except (appops.OpError, ValueError) as exc:
            devices.op_record(op_id, device_id, False, str(exc))
            return {"id": op_id, "ok": False, "error": str(exc)}
        except (TypeError, KeyError, AttributeError):  # malformed payload: fail this one, never block the queue
            devices.op_record(op_id, device_id, False, "Ungültige Daten")
            return {"id": op_id, "ok": False, "error": "Ungültige Daten"}
    return {"id": op_id, "ok": False, "retry": True, "error": "Gerade viel los auf dem Server – wird gleich noch einmal versucht."}


def app_version() -> str:
    """A hash of the app's files: a new one makes the service worker update itself."""
    h = hashlib.sha256()
    for p in sorted(APP_DIR.rglob("*")):
        if p.is_file() and p.name != "sw.js" and "__pycache__" not in p.parts:
            h.update(p.relative_to(APP_DIR).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


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


class OpsRequest(BaseModel):
    ops: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class PresenceRequest(BaseModel):
    info: dict[str, Any] = Field(default_factory=dict)


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

    @app.get("/app/sw.js", include_in_schema=False)
    async def app_sw() -> Response:
        files = ["/app/"] + sorted(f"/app/{p.relative_to(APP_DIR).as_posix()}" for p in APP_DIR.rglob("*")
                                   if p.is_file() and p.name not in ("sw.js", "index.html", "README.txt", "LICENSE")
                                   and p.suffix != ".map" and "__pycache__" not in p.parts)  # fmt: skip
        text = (APP_DIR / "sw.js").read_text("utf-8").replace("__VERSION__", await asyncio.to_thread(app_version))
        text = text.replace("__FILES__", json.dumps(files))
        return Response(text, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    app.mount("/app", StaticFiles(directory=APP_DIR), name="app")

    def base_url(request: Request) -> str:
        return (public_url or os.environ.get("MTG_SYNC_PUBLIC_URL") or str(request.base_url)).rstrip("/")

    def device(authorization: str = Header(default="")) -> dict[str, Any]:
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
        dev = devices.auth(token)
        if dev is None:
            raise HTTPException(401, "Dieses Gerät ist nicht (mehr) angemeldet – bitte neu koppeln.")
        return dev

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/app/")

    @app.get("/koppeln", include_in_schema=False)
    async def pair_page() -> RedirectResponse:
        return RedirectResponse("/app/")  # the browser keeps "#CODE"; the app takes it from there

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

    snap_cache: dict[str, Any] = {"seq": None, "body": b""}

    def snapshot_body() -> tuple[bytes, str]:
        seq = store.seq()
        if snap_cache["seq"] != seq:
            snap_cache["body"] = json.dumps(app_snapshot(store), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            snap_cache["seq"] = seq
        return snap_cache["body"], f'"{store.server_id()}-{store.epoch()}-{seq}"'

    def pcs() -> list[dict[str, Any]]:
        return [{"name": d["name"], "online": d["online"], "last_seen": d["last_seen"], "ai": bool((d["info"].get("ai") or {}).get("ready"))}
                for d in devices.list() if d["kind"] == "pc"]  # fmt: skip

    @app.get("/api/app/data")
    async def app_data(request: Request, me: dict[str, Any] = Depends(device)) -> Response:
        body, etag = await asyncio.to_thread(snapshot_body)
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    @app.get("/api/app/status")
    async def app_status(me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        _, etag = await asyncio.to_thread(snapshot_body)
        return {"etag": etag, "pcs": pcs(), "device": {"id": me["id"], "name": me["name"]}, "now": time.time()}

    @app.post("/api/app/ops")
    async def app_ops(req: OpsRequest, me: dict[str, Any] = Depends(device)) -> dict[str, Any]:
        results = []
        for op in req.ops:
            results.append(await asyncio.to_thread(run_op, store, devices, op, me["id"]))
        _, etag = await asyncio.to_thread(snapshot_body)
        return {"results": results, "etag": etag}

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
