"""Offline fixtures: a fake Scryfall / EDHREC / Commander Spellbook behind httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mtgdeck import blacklist, carddb, http, storage


def card(name: str, *, ci: str = "", type_line: str = "Creature — Human", text: str = "", cmc: float = 2,
         gc: bool = False, legal: bool = True, eur: str | None = "0.50", mana_cost: str = "{1}{G}") -> dict[str, Any]:  # fmt: skip
    return {
        "object": "card",
        "oracle_id": "oid-" + name.lower().replace(" ", "-"),
        "name": name,
        "lang": "en",
        "layout": "normal",
        "mana_cost": mana_cost,
        "cmc": cmc,
        "type_line": type_line,
        "oracle_text": text,
        "color_identity": list(ci),
        "legalities": {"commander": "legal" if legal else "banned"},
        "game_changer": gc,
        "edhrec_rank": 100,
        "prices": {"eur": eur, "usd": eur},
        "image_uris": {"normal": f"https://img.example/{name}.jpg"},
    }


CARDS = {
    c["name"]: c
    for c in [
        card("Meren of Clan Nel Toth", ci="BG", type_line="Legendary Creature — Human Shaman", cmc=4,
             text="Whenever another creature you control dies, you get an experience counter."),
        card("Sol Ring", type_line="Artifact", text="{T}: Add {C}{C}.", cmc=1, mana_cost="{1}"),
        card("Cultivate", ci="G", type_line="Sorcery", cmc=3,
             text="Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand."),
        card("Demonic Tutor", ci="B", type_line="Sorcery", gc=True, cmc=2,
             text="Search your library for a card, put that card into your hand, then shuffle."),
        card("Vampiric Tutor", ci="B", type_line="Instant", gc=True, cmc=1,
             text="Search your library for a card, then shuffle and put that card on top."),
        card("Necropotence", ci="B", type_line="Enchantment", gc=True, cmc=3, text="Pay 1 life: Exile the top card of your library face down."),
        card("Survival of the Fittest", ci="G", type_line="Enchantment", gc=True, cmc=2, text="{G}, Discard a creature card: Search your library for a creature card."),
        card("Armageddon", ci="W", type_line="Sorcery", text="Destroy all lands.", cmc=4),
        card("Time Warp", ci="U", type_line="Sorcery", text="Target player takes an extra turn after this one.", cmc=5),
        card("Forest", type_line="Basic Land — Forest", text="({T}: Add {G}.)", cmc=0, eur=None, mana_cost=""),
        card("Swamp", type_line="Basic Land — Swamp", text="({T}: Add {B}.)", cmc=0, eur=None, mana_cost=""),
        card("Relentless Rats", ci="B", text="A deck can have any number of cards named Relentless Rats."),
        card("Hullbreacher", ci="U", legal=False),
    ]
}


def lookup_card(name: str) -> dict[str, Any] | None:
    """Case-insensitive like the real Scryfall API."""
    exact = next((c for n, c in CARDS.items() if n.lower() == name.lower()), None)
    if exact:
        return exact
    if name.lower().startswith("filler"):
        return card(name.title(), ci="G", text="Draw a card." if "draw" in name.lower() else "")
    return None


def handler(request: httpx.Request) -> httpx.Response:
    url = request.url
    if url.host == "api.scryfall.com":
        if url.path == "/cards/collection":
            idents = json.loads(request.content)["identifiers"]
            data, missing = [], []
            for ident in idents:
                c = lookup_card(ident["name"])
                (data.append(c) if c else missing.append(ident))
            return httpx.Response(200, json={"data": data, "not_found": missing})
        if url.path == "/cards/named":
            c = lookup_card(url.params.get("fuzzy") or url.params.get("exact") or "")
            return httpx.Response(200, json=c) if c else httpx.Response(404, json={"details": "not found"})
        if url.path == "/cards/search":
            q = url.params["q"]
            if "gamechanger" in q:
                data = [c for c in CARDS.values() if c["game_changer"]]
                return httpx.Response(200, json={"total_cards": len(data), "has_more": False, "data": data})
            return httpx.Response(404, json={"details": "no cards"})
    if url.host == "json.edhrec.com":
        if "meren-of-clan-nel-toth" in url.path:
            return httpx.Response(200, json={
                "num_decks_avg": 1234,
                "panels": {"taglinks": [{"value": "Reanimator", "slug": "reanimator", "count": 500}]},
                "container": {"json_dict": {"cardlists": [{"header": "High Synergy Cards", "cardviews": [
                    {"name": "Sol Ring", "synergy": 0.01, "num_decks": 900, "potential_decks": 1000}]}]}},
            })  # fmt: skip
        return httpx.Response(403)
    if url.host == "backend.commanderspellbook.com":
        body = json.loads(request.content)
        main = {c["card"] for c in body["main"]}
        combo = {
            "id": "1-2",
            "uses": [{"card": {"name": "Filler Combo A"}}, {"card": {"name": "Filler Combo B"}}],
            "produces": [{"feature": {"name": "Infinite mana"}}],
            "bracketTag": "S",
        }
        has_combo = {"Filler Combo A", "Filler Combo B"} <= main
        if url.path.startswith("/find-my-combos"):
            return httpx.Response(200, json={"results": {"identity": "BG", "included": [combo] if has_combo else [], "almostIncluded": []}})
        if url.path.startswith("/estimate-bracket"):
            return httpx.Response(200, json={
                "bracketTag": "S" if has_combo else "C",
                "cards": [{"card": {"name": n}, "gameChanger": CARDS.get(n, {}).get("game_changer", False)} for n in main],
                "combos": [{"combo": combo, "definitelyTwoCard": True, "relevant": True}] if has_combo else [],
            })  # fmt: skip
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(http, "_MIN_INTERVAL", {})
    monkeypatch.setattr(http, "_DEFAULT_INTERVAL", 0)
    monkeypatch.setattr(carddb, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(carddb, "DB_PATH", tmp_path / "data" / "cards.sqlite")
    monkeypatch.setattr(storage, "DECKS_DIR", tmp_path / "decks")
    monkeypatch.setattr(blacklist, "BLACKLIST_FILE", tmp_path / "blacklist.txt")
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": http.USER_AGENT})
    yield
    http._client = None


def deck_lines(extra: list[str] | None = None, fillers: int | None = None) -> list[str]:
    """A legal 99-card main deck for Meren (BG)."""
    extra = extra or []
    base = ["1 Sol Ring", "1 Cultivate", *extra]
    n_fill = fillers if fillers is not None else 99 - 36 - len(base)
    return base + [f"1 Filler {i}" for i in range(n_fill)] + ["18 Forest", "18 Swamp"]
