"""Sync between devices: local-first, three-way merges, a small server as hub (see docs/plan-desktop-sync.md).

- ``files``  – which files sync under which logical path, how they are read/hashed/written;
- ``merge``  – three-way merge rules (generic JSON, lines, decks with version renumbering);
- ``store``  – the server-side document store (SQLite) with revision numbers and conflicts;
- ``client`` – one device: pull → merge → push;
- ``server`` – the sync server (FastAPI, Docker image), ``remote`` – its HTTP client, ``service`` – this device and its
  server (connect, run with status, devices, pairing, presence) for the GUI.
"""

from .client import SyncClient
from .files import Roots
from .store import LocalTransport, SyncConflict, SyncStore

__all__ = ["LocalTransport", "Roots", "SyncClient", "SyncConflict", "SyncStore"]
