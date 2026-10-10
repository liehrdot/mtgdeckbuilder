"""The bridge between the backend and the desktop shell (Tauri, ``desktop/src-tauri``).

The shell polls ``POST /api/desktop/poll`` every few seconds with what it knows (its version, whether the window is
visible, whether autostart is on) and gets back the tray tooltip, new notices (it shows them as system
notifications while the window is hidden) and the commands the web app queued for it (autostart on/off, open the
data folder or the log). Everything lives in memory. When no shell polls (the app runs in a browser),
``present()`` is False, ``shell()`` is None and ``notify()`` does nothing.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any

from .. import settings

MAX_NOTICES = 50
SHELL_TIMEOUT = 45.0  # seconds without a poll: there is no shell (browser), or it is gone

_lock = threading.Lock()
_ids = itertools.count(1)
_shell: dict[str, Any] = {}
_notices: list[dict[str, Any]] = []
_commands: list[dict[str, Any]] = []


def reset() -> None:
    with _lock:
        _shell.clear()
        _notices.clear()
        _commands.clear()


def present() -> bool:
    with _lock:
        return bool(_shell) and time.time() - _shell.get("seen", 0.0) < SHELL_TIMEOUT


def shell() -> dict[str, Any] | None:
    """What the web app may show about the shell, or None in a browser."""
    if not present():
        return None
    with _lock:
        return {k: _shell.get(k) for k in ("version", "autostart", "visible", "seen")}


def poll(info: dict[str, Any], since: int) -> dict[str, Any]:
    """The shell reports its state and fetches the notices newer than ``since`` plus the queued commands (once)."""
    with _lock:
        _shell.update({k: info[k] for k in ("version", "autostart", "visible") if k in info}, seen=time.time())
        notices = [n for n in _notices if n["id"] > since]
        commands, _commands[:] = list(_commands), []
        last = _notices[-1]["id"] if _notices else since
    return {"notices": notices, "commands": commands, "since": max(since, last)}


def notify(title: str, body: str = "", kind: str = "info") -> dict[str, Any] | None:
    """Queue a system notification (the shell shows it only while the window is hidden). None when no shell is
    listening or notifications are switched off in the settings."""
    if not present() or not settings.load().get("desktop_notify", True):
        return None
    notice = {"id": next(_ids), "at": time.time(), "title": title, "body": body, "kind": kind}
    with _lock:
        _notices.append(notice)
        del _notices[:-MAX_NOTICES]
    return notice


def command(kind: str, value: Any = None) -> dict[str, Any]:
    """Queue a command for the shell; a newer command of the same kind replaces an older unsent one."""
    cmd: dict[str, Any] = {"type": kind}
    if value is not None:
        cmd["value"] = value
    with _lock:
        _commands[:] = [c for c in _commands if c["type"] != kind] + [cmd]
    return cmd
