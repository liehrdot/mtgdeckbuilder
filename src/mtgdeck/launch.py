"""Entry point of ``mtg-gui`` and of the packaged desktop backend.

Arguments become environment variables *before* the app modules are imported (they read their paths at import
time), then the server starts::

    mtg-gui                                  # http://127.0.0.1:8765, data in the repository folder
    mtg-gui --data D:\\Decks --port 0 --token auto   # the desktop app: free port, access token, own data folder
    mtgdeck-backend mcp                      # the MCP server over stdio (what Claude Code talks to)

With ``--port 0`` the chosen address is printed as one line ``MTGDECK_URL=http://127.0.0.1:<port>/?token=<token>``
so the desktop shell can open it. With a token every request needs it (cookie set by that first link, or
``Authorization: Bearer``); without one the app is open to this computer, as before.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mtg-gui", description="Commander-Deckbuilder: lokale Oberfläche")
    p.add_argument("command", nargs="?", choices=["serve", "mcp"], default="serve", help="serve (Standard) oder mcp (MCP-Server auf stdio)")
    p.add_argument("--data", metavar="ORDNER", help="Datenordner (Decks, Sammlung, Einstellungen …); Standard: Projektordner bzw. %%APPDATA%%")
    p.add_argument("--cache", metavar="ORDNER", help="Ordner für Kartendatenbank und HTTP-Cache")
    p.add_argument("--host", help="Adresse (Standard 127.0.0.1)")
    p.add_argument("--port", type=int, help="Port; 0 = ein freier Port, wird als MTGDECK_URL ausgegeben")
    p.add_argument("--token", metavar="TOKEN", help="Zugriffstoken für die Oberfläche; 'auto' erzeugt eines")
    return p


def apply(args: argparse.Namespace) -> None:
    """Turn the arguments into the environment the modules read."""
    if args.data:
        os.environ["MTG_HOME"] = args.data
    if args.cache:
        os.environ["MTG_CACHE_HOME"] = args.cache
    if args.host:
        os.environ["MTG_GUI_HOST"] = args.host
    if args.port is not None:
        os.environ["MTG_GUI_PORT"] = str(args.port)
    if args.token:
        os.environ["MTG_GUI_TOKEN"] = secrets.token_urlsafe(24) if args.token == "auto" else args.token


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    apply(args)
    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return
    from .gui.app import serve

    serve()


if __name__ == "__main__":
    main(sys.argv[1:])
