"""Local card database built from Scryfall bulk data (https://scryfall.com/docs/api/bulk-data).

Scryfall publishes daily gzipped JSONL exports. We stream two of them into SQLite:

- Cards: by default ``all_cards`` (every printing in every language, ~375 MB compressed).
  It is aggregated to one row per Oracle ID, which gives us
    * card names in all languages (German, French, ... names resolve to the English card),
    * the cheapest printing's price (useful for budget decks).
  Set ``MTG_BULK_TYPE=oracle_cards`` for a much smaller/faster download (~25 MB, English
  names only, price of one representative printing) or ``default_cards``.
- Oracle Tags (Tagger project): community tags such as ``ramp``, ``draw``, ``removal`` ->
  much better card role detection than regexes over oracle text.

Gameplay data changes rarely, so the database is refreshed at most weekly (configurable).
Prices in bulk data are up to ~24 h old; they are fine for a budget estimate, nothing more.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import scryfall
from .http import client, get_json

DATA_DIR = Path(os.environ.get("MTG_DATA_DIR", Path.home() / ".cache" / "mtgdeck"))
DB_PATH = DATA_DIR / "cards.sqlite"
MAX_AGE = float(os.environ.get("MTG_BULK_MAX_AGE_DAYS", 7)) * 86400
BULK_TYPE = os.environ.get("MTG_BULK_TYPE", "all_cards")
# Bump when the stored card JSON (scryfall.compact) gains fields; older DBs are flagged for refresh.
# 2: layout + image_back (double-faced cards)
SCHEMA_VERSION = 2

# Layouts in the bulk file that are not deck cards.
_SKIP_LAYOUTS = {"token", "double_faced_token", "emblem", "art_series", "planar", "scheme", "vanguard", "augment", "host"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    oracle_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cmc REAL,
    type_line TEXT,
    oracle_text TEXT,
    color_identity TEXT,      -- e.g. 'BGU' (sorted letters)
    commander_legal INTEGER,
    is_commander INTEGER,     -- can be a commander (legendary creature / 'can be your commander')
    game_changer INTEGER,
    edhrec_rank INTEGER,
    price_usd REAL,
    price_eur REAL,
    data TEXT NOT NULL        -- compact JSON (scryfall.compact)
);
CREATE TABLE IF NOT EXISTS names (      -- full/face names in all languages -> oracle_id
    name TEXT NOT NULL COLLATE NOCASE,
    lang TEXT NOT NULL,
    oracle_id TEXT NOT NULL,
    PRIMARY KEY (name, oracle_id)
);
CREATE TABLE IF NOT EXISTS card_tags (
    oracle_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (oracle_id, tag)
);
CREATE INDEX IF NOT EXISTS card_tags_tag ON card_tags(tag);
CREATE TABLE IF NOT EXISTS tag_aliases (alias TEXT PRIMARY KEY, tag TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def available() -> bool:
    if not DB_PATH.exists():
        return False
    with closing(_connect()) as conn:
        return conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is not None


def status() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"available": False, "path": str(DB_PATH)}
    with closing(_connect()) as conn:
        return {
            "available": conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is not None,
            "path": str(DB_PATH),
            "cards": conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0],
            "tagged_cards": conn.execute("SELECT COUNT(DISTINCT oracle_id) FROM card_tags").fetchone()[0],
            "tags": conn.execute("SELECT COUNT(DISTINCT tag) FROM card_tags").fetchone()[0],
            "languages": [r[0] for r in conn.execute("SELECT DISTINCT lang FROM names ORDER BY lang")],
            "cards_bulk_type": _meta(conn, "cards_bulk_type"),
            "cards_updated_at": _meta(conn, "cards_updated_at"),
            "oracle_tags_updated_at": _meta(conn, "oracle_tags_updated_at"),
            "last_refresh": _meta(conn, "last_refresh"),
            "schema_outdated": (_meta(conn, "schema_version") or "1") != str(SCHEMA_VERSION),
        }


def schema_outdated() -> bool:
    if not DB_PATH.exists():
        return False
    with closing(_connect()) as conn:
        return (_meta(conn, "schema_version") or "1") != str(SCHEMA_VERSION)


def needs_refresh() -> bool:
    if not available() or schema_outdated():
        return True
    with closing(_connect()) as conn:
        last = _meta(conn, "last_refresh_epoch")
    return last is None or time.time() - float(last) > MAX_AGE


# --- download & import ------------------------------------------------------------------------


async def _download(url: str, dest: Path) -> None:
    async with client().stream("GET", url, timeout=None) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 16):
                fh.write(chunk)


def _iter_jsonl_gz(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _is_commander(card: dict[str, Any]) -> bool:
    type_line = (card.get("type_line") or "").split("//")[0]
    text = card.get("oracle_text") or " ".join(f.get("oracle_text", "") for f in card.get("card_faces") or [])
    return ("Legendary" in type_line and "Creature" in type_line) or "can be your commander" in text


def _oracle_id(card: dict[str, Any]) -> str | None:
    if card.get("oracle_id"):
        return card["oracle_id"]
    faces = card.get("card_faces") or []  # reversible cards keep the oracle id on the faces
    return faces[0].get("oracle_id") if faces else None


def _printing_score(card: dict[str, Any]) -> int:
    """Which printing represents the card (image, default text): English, paper, normal frame."""
    return (
        8 * (card.get("lang") == "en")
        + 4 * (not card.get("digital"))
        + 2 * (not card.get("promo"))
        + (card.get("image_status") == "highres_scan")
    )


def _min_price(current: float | None, *values: Any) -> float | None:
    for v in values:
        if v:
            f = float(v)
            current = f if current is None else min(current, f)
    return current


def _import_cards(conn: sqlite3.Connection, cards: Iterable[dict[str, Any]]) -> int:
    """Aggregate any Scryfall card bulk file (oracle/default/all cards) to one row per Oracle ID."""
    best: dict[str, tuple[int, dict[str, Any], bool]] = {}
    cheapest: dict[str, dict[str, float | None]] = {}
    names: set[tuple[str, str, str]] = set()

    for card in cards:
        if card.get("object") != "card" or card.get("layout") in _SKIP_LAYOUTS:
            continue
        oid = _oracle_id(card)
        if not oid:
            continue
        lang = card.get("lang", "en")
        faces = card.get("card_faces") or []
        names.add((card["name"], "en", oid))
        names.update((f["name"], "en", oid) for f in faces if f.get("name"))
        if lang != "en":
            printed = card.get("printed_name") or " // ".join(f.get("printed_name", "") for f in faces if f.get("printed_name"))
            if printed:
                names.add((printed, lang, oid))
            names.update((f["printed_name"], lang, oid) for f in faces if f.get("printed_name"))

        if not card.get("digital"):
            prices = card.get("prices") or {}
            p = cheapest.setdefault(oid, {"eur": None, "usd": None})
            # non-foil if any, otherwise foil/etched
            p["eur"] = _min_price(p["eur"], prices.get("eur") or prices.get("eur_foil"))
            p["usd"] = _min_price(p["usd"], prices.get("usd") or prices.get("usd_foil") or prices.get("usd_etched"))

        score = _printing_score(card)
        if oid not in best or score > best[oid][0]:
            best[oid] = (score, scryfall.compact(card), _is_commander(card))

    conn.execute("DELETE FROM cards")
    conn.execute("DELETE FROM names")
    for oid, (_, c, is_cmdr) in best.items():
        p = cheapest.get(oid, {})
        c["price_eur"] = f"{p['eur']:.2f}" if p.get("eur") is not None else c["price_eur"]
        c["price_usd"] = f"{p['usd']:.2f}" if p.get("usd") is not None else c["price_usd"]
        conn.execute(
            "INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                oid, c["name"], c["cmc"], c["type_line"], c.get("oracle_text", ""),
                "".join(sorted(c["color_identity"])), int(c["commander_legal"]), int(is_cmdr),
                int(c["game_changer"]), c["edhrec_rank"],
                float(c["price_usd"]) if c["price_usd"] else None,
                float(c["price_eur"]) if c["price_eur"] else None,
                json.dumps(c, ensure_ascii=False),
            ),
        )  # fmt: skip
    conn.executemany("INSERT OR IGNORE INTO names VALUES (?, ?, ?)", sorted(names))
    return len(best)


def _tag_rows(objs: Iterable[dict[str, Any]]) -> Iterator[tuple[str, str]]:
    """Yield (oracle_id, tag slug) pairs from the Oracle Tags bulk file.

    One line per tag: {"object": "tag", "id", "slug", "type": "oracle", "parent_ids": [...],
    "aliases": [...], "taggings": [{"oracle_id", "weight", ...}]}. Membership in a child tag does
    not roll up to its parents in the file, so we add every ancestor ourselves: a card tagged
    'removal-creature' also counts as 'removal'.
    """
    tags = [o for o in objs if o.get("slug") or o.get("label")]
    by_id = {t.get("id"): t for t in tags if t.get("id")}

    def ancestors(tag: dict[str, Any], seen: set[str]) -> set[str]:
        out: set[str] = set()
        for pid in tag.get("parent_ids") or []:
            parent = by_id.get(pid)
            if parent is None or pid in seen:
                continue
            seen.add(pid)
            out.add(parent.get("slug") or parent["label"])
            out |= ancestors(parent, seen)
        return out

    for tag in tags:
        slugs = {tag.get("slug") or tag["label"]} | ancestors(tag, set())
        for tagging in tag.get("taggings") or tag.get("oracle_ids") or []:
            oid = tagging.get("oracle_id") if isinstance(tagging, dict) else tagging
            if oid:
                for slug in slugs:
                    yield oid, slug


def _import_tags(conn: sqlite3.Connection, objs: Iterable[dict[str, Any]]) -> int:
    objs = list(objs)
    conn.execute("DELETE FROM card_tags")
    conn.execute("DELETE FROM tag_aliases")
    rows = {(oid, tag.lower()) for oid, tag in _tag_rows(objs)}
    conn.executemany("INSERT OR IGNORE INTO card_tags VALUES (?, ?)", sorted(rows))
    aliases = {
        (alias.lower(), (o.get("slug") or "").lower())
        for o in objs
        for alias in o.get("aliases") or []
        if o.get("slug")
    }
    conn.executemany("INSERT OR IGNORE INTO tag_aliases VALUES (?, ?)", sorted(aliases))
    return len(rows)


def _find_bulk(items: list[dict[str, Any]], *types: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("type") in types:
            return item
    for item in items:  # fallback on the human-readable name
        if (item.get("name") or "").lower().replace(" ", "_") in types:
            return item
    return None


async def refresh(force: bool = False, with_tags: bool = True) -> dict[str, Any]:
    """Download the latest bulk files (if older than MTG_BULK_MAX_AGE_DAYS) and rebuild the DB."""
    if not force and not needs_refresh():
        return {"refreshed": False, **status()}

    listing = await get_json(f"{scryfall.BASE}/bulk-data", ttl=3600)
    items = listing.get("data", [])
    cards_item = _find_bulk(items, BULK_TYPE)
    if not cards_item:
        raise RuntimeError(f"Scryfall bulk-data listing has no '{BULK_TYPE}' entry")
    tags_item = _find_bulk(items, "oracle_tags", "oracle_tag") if with_tags else None

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as tmp:
        cards_file = Path(tmp) / "cards.jsonl.gz"
        await _download(cards_item.get("jsonl_download_uri") or cards_item["download_uri"], cards_file)
        tags_file = None
        if tags_item:
            tags_file = Path(tmp) / "oracle_tags.jsonl.gz"
            await _download(tags_item.get("jsonl_download_uri") or tags_item["download_uri"], tags_file)

        def build() -> dict[str, int]:
            with closing(_connect()) as conn, conn:
                counts = {"cards": _import_cards(conn, _iter_jsonl_gz(cards_file))}
                _set_meta(conn, "cards_bulk_type", BULK_TYPE)
                _set_meta(conn, "cards_updated_at", cards_item.get("updated_at", ""))
                if tags_file:
                    counts["tag_links"] = _import_tags(conn, _iter_jsonl_gz(tags_file))
                    _set_meta(conn, "oracle_tags_updated_at", tags_item.get("updated_at", ""))
                _set_meta(conn, "last_refresh", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                _set_meta(conn, "schema_version", str(SCHEMA_VERSION))
                _set_meta(conn, "last_refresh_epoch", str(time.time()))
                return counts

        counts = await asyncio.to_thread(build)
    return {"refreshed": True, **counts, **status()}


# --- queries ----------------------------------------------------------------------------------


def lookup(names: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Exact (case-insensitive) name lookup incl. single faces of DFCs/split cards."""
    found: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    with closing(_connect()) as conn:
        for name in dict.fromkeys(n.strip() for n in names if n.strip()):
            row = conn.execute(
                "SELECT c.oracle_id, c.data, n.lang FROM names n JOIN cards c USING (oracle_id) "
                "WHERE n.name = ? ORDER BY n.lang != 'en' LIMIT 1",
                (name,),
            ).fetchone()
            if row is None:
                missing.append(name)
                continue
            card = json.loads(row["data"])
            card["tags"] = [r[0] for r in conn.execute("SELECT tag FROM card_tags WHERE oracle_id = ?", (row["oracle_id"],))]
            card["_query_name"] = name
            found[row["oracle_id"]] = card
    return list(found.values()), missing


def search(
    *,
    color_identity: str | None = None,
    tags: list[str] | None = None,
    text: str | None = None,
    type_contains: str | None = None,
    max_cmc: float | None = None,
    max_price: float | None = None,
    currency: str = "eur",
    commanders_only: bool = False,
    exclude_game_changers: bool = False,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Filter the local DB. color_identity 'WUB' means 'fits inside a W/U/B commander'."""
    where = ["commander_legal = 1"]
    args: list[Any] = []
    if color_identity is not None:
        allowed = {ch for ch in color_identity.upper() if ch in "WUBRG"}
        for col in "WUBRG":
            if col not in allowed:
                where.append(f"instr(color_identity, '{col}') = 0")
    for tag in tags or []:
        where.append(
            "oracle_id IN (SELECT oracle_id FROM card_tags WHERE tag = "
            "COALESCE((SELECT tag FROM tag_aliases WHERE alias = ?), ?))"
        )
        args += [tag.lower(), tag.lower()]
    if text:
        where.append("oracle_text LIKE ?")
        args.append(f"%{text}%")
    if type_contains:
        where.append("type_line LIKE ?")
        args.append(f"%{type_contains}%")
    if max_cmc is not None:
        where.append("cmc <= ?")
        args.append(max_cmc)
    if max_price is not None:
        col = "price_usd" if currency == "usd" else "price_eur"
        where.append(f"({col} IS NOT NULL AND {col} <= ?)")
        args.append(max_price)
    if commanders_only:
        where.append("is_commander = 1")
    if exclude_game_changers:
        where.append("game_changer = 0")
    sql = f"SELECT data FROM cards WHERE {' AND '.join(where)} ORDER BY edhrec_rank IS NULL, edhrec_rank LIMIT ?"
    args.append(limit)
    with closing(_connect()) as conn:
        return [json.loads(r[0]) for r in conn.execute(sql, args)]


def tag_counts(tags: list[str]) -> dict[str, int]:
    """Number of cards per Tagger tag (how specific a tag is)."""
    if not tags:
        return {}
    with closing(_connect()) as conn:
        return {t: conn.execute("SELECT COUNT(*) FROM card_tags WHERE tag = ?", (t,)).fetchone()[0] for t in tags}


def game_changers() -> list[str]:
    with closing(_connect()) as conn:
        return [r[0] for r in conn.execute("SELECT name FROM cards WHERE game_changer = 1 ORDER BY name")]


def popular_tags(prefix: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT tag, COUNT(*) AS n FROM card_tags WHERE tag LIKE ? GROUP BY tag ORDER BY n DESC LIMIT ?",
            (f"{prefix.lower()}%", limit),
        )
        return [{"tag": r[0], "cards": r[1]} for r in rows]
