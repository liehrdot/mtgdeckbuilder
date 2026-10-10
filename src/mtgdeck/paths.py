"""Where this installation keeps its files.

Three roots, each overridable by one environment variable:

- ``home()`` – the user's data (decks, collection, blacklist, table rules, backups, proxies, settings, sync state).
  ``MTG_HOME``; otherwise the repository root when running from a checkout (``uv run mtg-gui``), or the platform's
  app-data folder when running as the packaged desktop app (``%APPDATA%\\MTG Deckbuilder`` on Windows).
  The per-file variables (``MTG_DECKS_DIR`` …) still win over it.
- ``cache_home()`` – card database and HTTP cache (large, re-creatable). ``MTG_CACHE_HOME``; otherwise
  ``~/.cache/mtgdeck`` or ``%LOCALAPPDATA%\\MTG Deckbuilder\\cache`` when packaged.
- ``agent_root()`` – the folder Claude runs in (its ``.claude/skills`` and ``CLAUDE.md``). ``MTG_AGENT_ROOT``;
  otherwise the repository root, or the bundled ``agent/`` folder of the packaged app.

No imports from the rest of the package: every module reads its default path from here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "MTG Deckbuilder"
REPO_ROOT = Path(__file__).resolve().parents[2]


def frozen() -> bool:
    """Running as the packaged desktop backend (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """The packaged app's read-only files (``_internal/`` of the PyInstaller folder build)."""
    return Path(getattr(sys, "_MEIPASS", REPO_ROOT))


def _platform_data() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "mtgdeck"


def _platform_cache() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "mtgdeck"


def home() -> Path:
    env = os.environ.get("MTG_HOME")
    if env:
        return Path(env).expanduser()
    return _platform_data() if frozen() else REPO_ROOT


def cache_home() -> Path:
    env = os.environ.get("MTG_CACHE_HOME")
    if env:
        return Path(env).expanduser()
    return _platform_cache() if frozen() else Path.home() / ".cache" / "mtgdeck"


def agent_root() -> Path:
    env = os.environ.get("MTG_AGENT_ROOT")
    if env:
        return Path(env).expanduser()
    return bundle_dir() / "agent" if frozen() else REPO_ROOT


def bundled_cli() -> Path | None:
    """The Claude Code CLI the Agent SDK ships (``claude_agent_sdk/_bundled/claude[.exe]``), if present."""
    name = "claude.exe" if sys.platform == "win32" else "claude"
    candidates = [bundle_dir() / "claude_agent_sdk" / "_bundled" / name]
    try:
        import claude_agent_sdk

        candidates.append(Path(claude_agent_sdk.__file__).parent / "_bundled" / name)
    except ImportError:
        pass
    return next((p for p in candidates if p.is_file()), None)
