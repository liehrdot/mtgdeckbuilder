"""Entry point of the packaged backend (``mtgdeck-backend[.exe]``): the same launcher as ``mtg-gui``.

``mtgdeck-backend --data <folder> --port 0 --token auto`` serves the GUI and prints ``MTGDECK_URL=…``;
``mtgdeck-backend mcp`` is the MCP server a Claude job starts (see ``gui.app._mcp_command``)."""

import multiprocessing
import sys

from mtgdeck.launch import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # a frozen exe that spawns helpers must not re-run the app
    main(sys.argv[1:])
