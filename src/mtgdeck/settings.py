"""Local user settings (paths, servers) in mtgdeck.settings.json (gitignored).

Environment variables override the file: MTG_AUTOFILL_PATH, MTG_MPCFILL_SERVER, MTG_CARDBACK.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .storage import PROJECT_ROOT

SETTINGS_FILE = Path(os.environ.get("MTG_SETTINGS_FILE", PROJECT_ROOT / "mtgdeck.settings.json"))

DEFAULTS: dict[str, Any] = {
    # MPC Autofill desktop tool (https://github.com/chilli-axe/mpc-autofill/releases)
    "autofill_path": "",
    # Search server of MPC Autofill (the one configured on mpcfill.com, "?server=..."); empty = Scryfall images
    "mpcfill_server": "",
    # Image CDN of MPC Autofill (thumbnails + full-resolution images of the community scans)
    "mpcfill_cdn": "https://cdn.mpcautofill.com",
    # Own cardback image (PNG/JPG). Empty = MPC Autofill cardback (with server) or a plain generated one
    "cardback_path": "",
    "browser": "chrome",  # chrome | edge | brave
    "site": "MakePlayingCards",  # MakePlayingCards | PrinterStudio | ...
    "stock": "(S30) Standard Smooth",
    "foil": False,
    "paper": "A4",  # PDF export: A4 | Letter
    # Opt-in AI upscaling of Scryfall scans (300 -> 600 DPI) with Real-ESRGAN (ncnn-vulkan build)
    "upscale": False,
    "upscaler_path": "",  # realesrgan-ncnn-vulkan(.exe); empty = search in tools/
    "upscale_model": "realesrgan-x4plus",
    # remove the print halftone of scanned cards before upscaling: off | light | normal | strong
    "descreen": "normal",
}
_ENV = {
    "autofill_path": "MTG_AUTOFILL_PATH",
    "mpcfill_server": "MTG_MPCFILL_SERVER",
    "cardback_path": "MTG_CARDBACK",
    "upscaler_path": "MTG_UPSCALER_PATH",
}


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        data.update({k: v for k, v in json.loads(SETTINGS_FILE.read_text("utf-8")).items() if k in DEFAULTS})
    except (FileNotFoundError, ValueError):
        pass
    for key, env in _ENV.items():
        if os.environ.get(env):
            data[key] = os.environ[env]
    return data


def update(changes: dict[str, Any]) -> dict[str, Any]:
    try:
        stored = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        stored = {}
    stored.update({k: v for k, v in changes.items() if k in DEFAULTS and v is not None})
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(stored, indent=2, ensure_ascii=False), "utf-8")
    return load()
