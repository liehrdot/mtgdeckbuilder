"""This device and its sync server: connect, sync with a status log, devices, pairing codes, presence.

Files in ``Roots.state`` (``.sync/``, never synced, not in backups):
- ``device.json`` – server address and id, this device's token, id and name (the token never goes to the browser);
- ``status.json`` – the last runs (what came in, what went out, errors);
- plus the client's own state (``client.py``).
"""

from __future__ import annotations

import os
import platform
import time
from typing import Any

import httpx

from ..jsonstore import locked, read_json, update_json, write_json
from .client import SyncClient
from .files import Roots, describe
from .remote import AuthError, HttpTransport, RemoteError, normalize_url, parse_link

RUNS_KEEP = 20
LABELS_MAX = 12


def _roots(roots: Roots | None) -> Roots:
    return roots or Roots.current()


def _device_file(roots: Roots | None = None):
    return _roots(roots).state / "device.json"


def config(roots: Roots | None = None) -> dict[str, Any] | None:
    return read_json(_device_file(roots), None)


def default_name() -> str:
    return (platform.node() or "Mein PC").split(".")[0][:60]


def transport(cfg: dict[str, Any], client: httpx.Client | None = None) -> HttpTransport:
    return HttpTransport(cfg["server"], cfg["token"], client=client)


def connect(link: str = "", *, url: str = "", code: str = "", name: str = "", kind: str = "pc",
            roots: Roots | None = None, client: httpx.Client | None = None) -> dict[str, Any]:  # fmt: skip
    """Pair this device with a server (pairing link, or address + code)."""
    roots = _roots(roots)
    link_url, link_code = parse_link(link)
    server = normalize_url(url) or link_url
    code = (code or link_code).strip()
    if not server:
        raise ValueError("Bitte die Adresse des Sync-Servers angeben (z. B. https://sync.meine-domain.de).")
    if not code:
        raise ValueError("Bitte den Kopplungscode angeben (steht auf dem Server bzw. auf dem anderen Gerät).")
    with HttpTransport(server, client=client) as t:
        health = t.health()
        got = t.claim(code, (name or default_name()).strip(), kind)
    old = config(roots)
    if (old or {}).get("server_id") != got["server_id"]:  # another server: everything is merged afresh
        SyncClient(roots, None).reset_state()  # type: ignore[arg-type]
    elif (old or {}).get("epoch") not in (None, health.get("epoch")):  # same server, restored from a backup
        SyncClient(roots, None).reset_state()  # type: ignore[arg-type]
    cfg = {"server": server, "server_id": got["server_id"], "epoch": health.get("epoch"), "token": got["token"],
           "device_id": got["device_id"], "name": got["name"], "kind": kind, "paired_at": time.time()}  # fmt: skip
    roots.state.mkdir(parents=True, exist_ok=True)
    write_json(_device_file(roots), cfg)
    return public(cfg)


def disconnect(roots: Roots | None = None, client: httpx.Client | None = None) -> None:
    """Sign this device off (on the server too, when reachable). Local data and sync state stay, so connecting
    to the same server again continues where it stopped."""
    cfg = config(roots)
    if not cfg:
        return
    if not cfg.get("revoked"):
        try:
            with transport(cfg, client) as t:
                t.revoke(cfg["device_id"])
        except RemoteError:
            pass
    _device_file(roots).unlink(missing_ok=True)


def public(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: v for k, v in cfg.items() if k != "token"} if cfg else None


def reset(roots: Roots | None = None) -> None:
    """Merge everything afresh on the next sync (after a backup was restored): nothing gets deleted elsewhere."""
    if config(roots):
        SyncClient(_roots(roots), None).reset_state()  # type: ignore[arg-type]


def _deck_names() -> dict[str, str]:
    from .. import storage

    try:
        return {d["slug"]: d.get("name") or d["slug"] for d in storage.list_decks() if d.get("slug")}
    except Exception:
        return {}


def _labels(paths: list[str], names: dict[str, str]) -> list[str]:
    out = [describe(p, names) for p in paths if "/.versions/" not in p]
    return list(dict.fromkeys(out))[:LABELS_MAX]


def run(reason: str = "", roots: Roots | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """One sync (blocking – run it in a thread); the result is stored in ``status.json`` and returned."""
    roots = _roots(roots)
    cfg = config(roots)
    if not cfg:
        raise ValueError("Dieses Gerät ist mit keinem Sync-Server verbunden.")
    started = time.time()
    entry: dict[str, Any] = {"at": started, "reason": reason, "ok": False}
    try:
        with transport(cfg, client) as t:
            health = t.health()
            sync = SyncClient(roots, t, cfg.get("name", ""))
            if health.get("server_id") != cfg.get("server_id"):
                raise RemoteError("Unter der Adresse läuft jetzt ein anderer Sync-Server – bitte neu koppeln.")
            restored = health.get("epoch") != cfg.get("epoch")  # mtg-sync-server restore
            if restored or t.changes(sync.cursor()).get("head", 0) < sync.cursor():  # (or the file was copied back)
                sync.reset_state()
                entry["reset"] = True
            if restored:
                with update_json(_device_file(roots), {}) as stored:
                    stored["epoch"] = health.get("epoch")
            report = sync.sync()
        names = _deck_names()
        entry.update(
            ok=not report["errors"], pulled=report["pulled"], pushed=report["pushed"], merged=report["merged"],
            skipped=report["skipped"], revalidate=report["revalidate"], conflicts=len(report["conflicts"]),
            errors=report["errors"][:20], incoming=_labels(report["pulled"] + report["merged"], names),
            outgoing=_labels(report["pushed"], names),
        )  # fmt: skip
        if report["errors"]:
            entry["error"] = report["errors"][0]
    except AuthError as exc:
        entry["error"] = str(exc)
        with update_json(_device_file(roots), {}) as stored:
            if stored:
                stored["revoked"] = True
    except RemoteError as exc:
        entry["error"] = str(exc)
    entry["took"] = round(time.time() - started, 2)
    _store_run(roots, entry)
    return entry


def _store_run(roots: Roots, entry: dict[str, Any]) -> None:
    path = roots.state / "status.json"
    roots.state.mkdir(parents=True, exist_ok=True)
    with locked(path):
        st = read_json(path, {}) or {}
        runs = [*st.get("runs", []), entry][-RUNS_KEEP:]
        out = {"runs": runs, "last": entry, "last_ok": entry["at"] if entry.get("ok") else st.get("last_ok")}
        write_json(path, out)


def status(roots: Roots | None = None) -> dict[str, Any]:
    roots = _roots(roots)
    cfg = config(roots)
    st = read_json(roots.state / "status.json", {}) or {}
    return {"connected": bool(cfg), "revoked": bool((cfg or {}).get("revoked")), "device": public(cfg),
            "last": st.get("last"), "last_ok": st.get("last_ok"), "conflicts": len(conflicts(roots)),
            "default_name": default_name()}  # fmt: skip


def conflicts(roots: Roots | None = None) -> list[dict[str, Any]]:
    """The conflict log, newest first, with a label per path."""
    items = read_json(_roots(roots).state / "conflicts.json", []) or []
    names = _deck_names() if items else {}
    return [{**c, "label": describe(c.get("path", ""), names)} for c in reversed(items)]


def clear_conflicts(roots: Roots | None = None) -> None:
    path = _roots(roots).state / "conflicts.json"
    with locked(path):
        path.unlink(missing_ok=True)


def _connected(roots: Roots | None) -> dict[str, Any]:
    cfg = config(roots)
    if not cfg:
        raise ValueError("Dieses Gerät ist mit keinem Sync-Server verbunden.")
    return cfg


def devices(roots: Roots | None = None, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    with transport(_connected(roots), client) as t:
        return t.devices()


def revoke(device_id: str, roots: Roots | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    cfg = _connected(roots)
    if device_id == cfg.get("device_id"):
        disconnect(roots, client)
        return {"ok": True, "self": True}
    with transport(cfg, client) as t:
        return t.revoke(device_id)


def pair_code(roots: Roots | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """A pairing code for another device, with the link and a QR code (SVG) of the link."""
    with transport(_connected(roots), client) as t:
        got = t.pair_start()
    try:
        import segno

        got["qr"] = segno.make(got["link"], error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")
    except ImportError:
        got["qr"] = None
    return got


def presence(info: dict[str, Any], roots: Roots | None = None, client: httpx.Client | None = None) -> bool:
    """Tell the server this device is running (and whether Claude is ready here); ``False`` when unreachable."""
    cfg = config(roots)
    if not cfg or cfg.get("revoked"):
        return False
    try:
        with transport(cfg, client) as t:
            t.presence({**info, "platform": os.name})
        return True
    except AuthError:
        with update_json(_device_file(roots), {}) as stored:
            if stored:
                stored["revoked"] = True
        return False
    except RemoteError:
        return False


def pending_revalidation() -> list[str]:
    """Decks a merge combined from two devices whose check (legality, bracket, price) is not done yet."""
    from .. import storage

    return [d["slug"] for d in storage.list_decks() if d.get("needs_revalidation")]


async def revalidate_pending() -> list[str]:
    """Check merged decks again (needs the card data, so it may fail offline – then it waits for the next sync)."""
    from .. import deckedit, storage

    done = []
    for slug in pending_revalidation():
        try:
            deck = storage.load(slug)
            await deckedit.revalidate(deck)
            deck.pop("needs_revalidation", None)
            storage.save(deck, expect_version=deck.get("version"))
            done.append(slug)
        except Exception:
            continue
    return done
