"""Pseudo-terminal for interactive console tools (MPC Autofill uses arrow-key menus that need a real
console): ConPTY via pywinpty on Windows, a POSIX pty via ptyprocess elsewhere. The browser shows it
with xterm.js."""

from __future__ import annotations

import os
import time
from typing import Any

try:  # pragma: no cover - platform specific import
    if os.name == "nt":
        from winpty import PtyProcess as _Pty  # type: ignore[import-not-found]
    else:
        from ptyprocess import PtyProcessUnicode as _Pty  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _Pty = None


def available() -> bool:
    return _Pty is not None


class Terminal:
    def __init__(self, cmd: list[str], cwd: str, rows: int = 32, cols: int = 110):
        if _Pty is None:
            raise RuntimeError("Kein Pseudo-Terminal verfügbar (pywinpty/ptyprocess fehlt: `uv sync --extra gui`).")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        if os.name != "nt":
            env["TERM"] = "xterm-256color"
        self._p: Any = _Pty.spawn(cmd, cwd=cwd, env=env, dimensions=(rows, cols))

    def read(self, size: int = 4096) -> str:
        """Blocking read; returns '' when the process has finished."""
        try:
            return self._p.read(size)
        except EOFError:
            return ""

    def write(self, data: str) -> None:
        self._p.write(data)

    def resize(self, rows: int, cols: int) -> None:
        self._p.setwinsize(rows, cols)

    def alive(self) -> bool:
        return bool(self._p.isalive())

    def terminate(self) -> None:
        try:
            self._p.terminate(force=True)
        except Exception:
            pass

    def exit_code(self, timeout: float = 5.0) -> int | None:
        end = time.time() + timeout
        while self.alive() and time.time() < end:
            time.sleep(0.05)
        code = getattr(self._p, "exitstatus", None)
        return code if code is not None else (None if self.alive() else 0)
