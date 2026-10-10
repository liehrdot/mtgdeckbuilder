# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of the desktop backend: one folder (``--onedir`` – starts faster than a single file and
does not unpack to %TEMP%, which virus scanners dislike).

    uv run pyinstaller desktop/sidecar/backend.spec            → dist/mtgdeck-backend/

Bundled besides the code: the GUI's static files, the Agent SDK's Claude CLI (``claude_agent_sdk/_bundled``,
found by the SDK relative to its package), and the ``agent/`` folder Claude runs in (skills + CLAUDE.md).
The sync server, its phone app and the test helpers are left out."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

datas = [
    (str(ROOT / "src/mtgdeck/gui/static"), "mtgdeck/gui/static"),
    (str(ROOT / ".claude/skills"), "agent/.claude/skills"),
    (str(ROOT / "desktop/agent/CLAUDE.md"), "agent"),
]
datas += collect_data_files("claude_agent_sdk", includes=["_bundled/*"])
datas += collect_data_files("mcp")  # schema/json files of the MCP package, if any
datas += collect_data_files("certifi")

hiddenimports = (
    collect_submodules("mtgdeck")
    + collect_submodules("uvicorn")
    + collect_submodules("claude_agent_sdk")
    + ["segno", "PIL.Image", "PIL.ImageFilter", "PIL.ImageEnhance", "PIL.ImageDraw", "PIL.ImageFont"]
    + (["winpty"] if sys.platform == "win32" else ["ptyprocess"])
)
excludes = ["mtgdeck.sync.server", "tkinter", "numpy", "pytest", "inquirerpy", "IPython", "matplotlib"]

a = Analysis(
    [str(ROOT / "desktop/sidecar/entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mtgdeck-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # the shell reads MTGDECK_URL from stdout; it starts the exe without a visible window
    disable_windowed_traceback=False,
    icon=str(ROOT / "desktop/icon.ico") if (ROOT / "desktop/icon.ico").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mtgdeck-backend")
