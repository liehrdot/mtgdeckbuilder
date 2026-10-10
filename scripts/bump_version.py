"""Set the app's version everywhere it is written down – one command before a release:

    uv run python scripts/bump_version.py 0.2.0

pyproject.toml, src/mtgdeck/gui/app.py (VERSION), desktop/src-tauri/tauri.conf.json, Cargo.toml and Cargo.lock.
The release workflow refuses a tag whose number differs from these files."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "pyproject.toml": (r'^(version = ")[^"]+(")', re.M),
    "src/mtgdeck/gui/app.py": (r'^(VERSION = ")[^"]+(")', re.M),
    "desktop/src-tauri/Cargo.toml": (r'^(version = ")[^"]+(")', re.M),
    "desktop/src-tauri/Cargo.lock": (r'(name = "mtgdeck-desktop"\nversion = ")[^"]+(")', 0),
}


def main(version: str) -> int:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print("Version wie 0.2.0 angeben", file=sys.stderr)
        return 2
    for rel, (pattern, flags) in FILES.items():
        path = ROOT / rel
        text = path.read_text("utf-8")
        new, n = re.subn(pattern, rf"\g<1>{version}\g<2>", text, count=1, flags=flags)
        if n != 1:
            print(f"{rel}: Versionszeile nicht gefunden", file=sys.stderr)
            return 1
        path.write_text(new, "utf-8")
    conf = ROOT / "desktop/src-tauri/tauri.conf.json"
    data = json.loads(conf.read_text("utf-8"))
    data["version"] = version
    conf.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"Version {version} in {len(FILES) + 1} Dateien gesetzt – jetzt committen, taggen (v{version}) und pushen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
