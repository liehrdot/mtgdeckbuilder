"""Offline fixtures: a fake Scryfall / EDHREC / Commander Spellbook behind httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mtgdeck import blacklist, carddb, collection, deskmat, http, proxy, settings, storage


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
        "image_uris": {"normal": f"https://cards.scryfall.io/normal/front/{name}.jpg?1"},
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
        {**card("Pitiless Plunderer", ci="B", text="Whenever another creature you control dies, create a Treasure token."),
         "all_parts": [{"id": "self", "component": "combo_piece", "name": "Pitiless Plunderer", "type_line": "Creature — Zombie Pirate"},
                       {"id": "a0b0-treasure", "component": "token", "name": "Treasure", "type_line": "Token Artifact — Treasure"},
                       {"id": "c0d0-monarch", "component": "combo_piece", "name": "The Monarch", "type_line": "Card"}]},  # fmt: skip
        card("Hullbreacher", ci="U", legal=False),
        {
            **card("Delver of Secrets // Insectile Aberration", ci="U"),
            "layout": "transform",
            "image_uris": None,
            "card_faces": [
                {"name": "Delver of Secrets", "type_line": "Creature", "oracle_text": "",
                 "image_uris": {"normal": "https://cards.scryfall.io/normal/front/delver.jpg"}},
                {"name": "Insectile Aberration", "type_line": "Creature", "oracle_text": "Flying",
                 "image_uris": {"normal": "https://cards.scryfall.io/normal/back/delver.jpg"}},
            ],
        },
    ]
}
for _c in CARDS.values():  # drop None image_uris (DFC) so compact() falls back to the faces
    if _c.get("image_uris") is None:
        _c.pop("image_uris", None)


def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (745, 1040)) -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=35, fill=(20, 20, 20, 255))
    buf = BytesIO()
    (img if fmt == "PNG" else img.convert("RGB")).save(buf, fmt)
    return buf.getvalue()


def _printing(name: str, sid: str, set_code: str, cn: str, lang: str = "en") -> dict[str, Any]:
    return {**CARDS[name], "id": sid, "set": set_code, "set_name": f"Set {set_code.upper()}", "collector_number": cn, "lang": lang,
            "released_at": "2021-04-23", "image_uris": {"normal": f"https://cards.scryfall.io/normal/front/{sid}.jpg"},
            "prices": {"eur": "1.50", "eur_foil": "4.00", "usd": "2.00"}}  # fmt: skip


PRINTINGS = {
    "a1b2-sol": _printing("Sol Ring", "a1b2-sol", "c21", "263"),
    "c21/263": _printing("Sol Ring", "a1b2-sol", "c21", "263"),
    "d3e4-cult": _printing("Cultivate", "d3e4-cult", "m21", "177", "de"),
}

PRINT_COUNTS = {"Forest": 250}

# printed (translated) text of a card's German printing, for lang:de searches
GERMAN = {"Cultivate": {"printed_name": "Kultivieren", "printed_type_line": "Hexerei",
                        "printed_text": "Durchsuche deine Bibliothek nach bis zu zwei Standardland-Karten, zeige sie offen vor, bringe eine davon getappt ins Spiel und nimm die andere auf deine Hand."},
          "Delver of Secrets // Insectile Aberration": {"card_faces": [
              {"printed_name": "Hüter der Geheimnisse", "printed_type_line": "Kreatur", "printed_text": ""},
              {"printed_name": "Insektoide Abnormität", "printed_type_line": "Kreatur", "printed_text": "Flugfähigkeit"}],
              "keywords": ["Flying", "Transform"]}}  # fmt: skip

MPC_SERVER = "https://mpc.test"
MPC_HITS = {"sol ring": ["drive-sol-1", "drive-sol-2"], "insectile aberration": ["drive-insect"], "treasure": ["drive-treasure"]}


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
    if url.host == "cards.scryfall.io":
        return httpx.Response(200, content=_image_bytes("PNG"), headers={"Content-Type": "image/png"})
    if url.host == "image.pollinations.ai":  # free image generator: an image of the requested size
        if "fail" in url.path:
            return httpx.Response(500, text="boom")
        size = (int(url.params.get("width", 1024)), int(url.params.get("height", 1024)))
        return httpx.Response(200, content=_image_bytes("JPEG", size), headers={"Content-Type": "image/jpeg"})
    if url.host == "cdn.mpcautofill.com":
        return httpx.Response(200, content=_image_bytes("JPEG", (1644, 2244)), headers={"Content-Type": "image/jpeg"})
    if url.host == "mpc.test":
        body = json.loads(request.content) if request.content else {}
        if url.path == "/2/sources/":
            return httpx.Response(200, json={"results": {"1": {"pk": 1, "name": "Chilli", "ordinal": 0}}})
        if url.path == "/3/editorSearch/":
            assert body["searchSettings"]["sourceSettings"]["sources"] == [[1, True]]
            return httpx.Response(200, json={"results": {k: MPC_HITS.get(v["query"], []) for k, v in body["queries"].items()}})
        if url.path == "/2/cards/":
            return httpx.Response(200, json={"results": {
                i: {"identifier": i, "name": i.replace("drive-", ""), "extension": "png", "dpi": 1200,
                    "sourceName": "Chilli", "sourceType": "Google Drive"} for i in body["cardIdentifiers"]}})  # fmt: skip
        if url.path == "/2/cardbacks/":
            return httpx.Response(200, json={"cardbacks": ["drive-back"]})
    if url.host == "api.scryfall.com":
        if url.path == "/cards/collection":
            idents = json.loads(request.content)["identifiers"]
            data, missing = [], []
            for ident in idents:
                if "id" in ident or "set" in ident:  # a specific printing
                    c = PRINTINGS.get(ident.get("id")) or PRINTINGS.get(f"{ident.get('set')}/{ident.get('collector_number')}")
                else:
                    c = lookup_card(ident["name"])
                (data.append(c) if c else missing.append(ident))
            return httpx.Response(200, json={"data": data, "not_found": missing})
        if url.path == "/cards/named":
            c = lookup_card(url.params.get("fuzzy") or url.params.get("exact") or "")
            return httpx.Response(200, json=c) if c else httpx.Response(404, json={"details": "not found"})
        if url.path == "/cards/search":
            q = url.params["q"]
            if q.startswith('!"'):  # all printings of one card
                name = q.split('"')[1]
                base = lookup_card(name)
                if not base:
                    return httpx.Response(404, json={"details": "no cards"})
                if "lang:" in q:  # printings in one language (German card text)
                    de = GERMAN.get(base["name"])
                    if not de or "lang:de" not in q:
                        return httpx.Response(404, json={"details": "no cards"})
                    printed = {**base, **de, "lang": "de", "set_name": "Innistrad"}
                    if "card_faces" in de:  # real faces carry Oracle and printed fields side by side
                        printed["card_faces"] = [{**f, **d} for f, d in zip(base["card_faces"], de["card_faces"])]
                    return httpx.Response(200, json={"total_cards": 1, "has_more": False, "data": [printed]})
                total = PRINT_COUNTS.get(base["name"], 3)  # basic lands have hundreds of printings
                page = int(url.params.get("page", 1))
                ids = range((page - 1) * 175, min(page * 175, total))
                data = [{**base, "set_name": f"Set {i}", "collector_number": str(i),
                         "image_uris": {"normal": f"https://cards.scryfall.io/normal/front/p{i}.jpg"}} for i in ids]  # fmt: skip
                return httpx.Response(200, json={"total_cards": total, "has_more": page * 175 < total, "data": data})
            if "otag:" in q or q.startswith("t:"):  # role search for replacement suggestions
                data = [lookup_card("Filler Ramp Rock"), CARDS["Cultivate"], CARDS["Sol Ring"]]
                return httpx.Response(200, json={"total_cards": len(data), "has_more": False, "data": data})
            if "gamechanger" in q:
                data = [c for c in CARDS.values() if c["game_changer"]]
                return httpx.Response(200, json={"total_cards": len(data), "has_more": False, "data": data})
            return httpx.Response(404, json={"details": "no cards"})
    if url.host == "archidekt.com" and url.path == "/api/decks/4242/":
        def ae(name, cats, qty=1):
            return {"quantity": qty, "categories": cats, "card": {"oracleCard": {"name": name}}}
        return httpx.Response(200, json={"name": "Meren Archidekt", "categories": [
            {"name": "Considering", "includedInDeck": False}, {"name": "Ramp", "includedInDeck": True}],
            "cards": [ae("Meren of Clan Nel Toth", ["Commander"]), ae("Sol Ring", ["Ramp"]), ae("Cultivate", ["Mana Ramp"]),
                      ae("Necropotence", ["Considering"]), ae("Demonic Tutor", ["Maybeboard"]), ae("Pitiless Plunderer", ["Aristocrats"]),
                      *[ae(f"Filler {i}", ["Synergy"]) for i in range(60)], ae("Forest", ["Land"], 18), ae("Swamp", ["Lands"], 18)]})  # fmt: skip
    if url.host in ("api2.moxfield.com", "api.moxfield.com"):
        if "blocked" in url.path:
            return httpx.Response(403, text="cloudflare")
        if url.host == "api2.moxfield.com":
            return httpx.Response(403, text="cloudflare")  # v3 blocked, v2 answers
        main = {f"k{i}": {"quantity": 1, "card": {"name": f"Filler {i}"}} for i in range(61)}
        main["sol"] = {"quantity": 1, "card": {"name": "Sol Ring"}}
        main["forest"] = {"quantity": 37, "card": {"name": "Forest"}}
        return httpx.Response(200, json={"name": "Mox Meren", "commanders": {"m": {"quantity": 1, "card": {"name": "Meren of Clan Nel Toth"}}},
                                         "mainboard": main})  # fmt: skip
    if url.host == "www.mtggoldfish.com" and url.path == "/deck/download/777":
        body = "\n".join(["1 Sol Ring", "1 Cultivate", *[f"1 Filler {i}" for i in range(61)], "18 Forest", "18 Swamp", "", "1 Meren of Clan Nel Toth"])
        return httpx.Response(200, text=body)
    if url.host == "tappedout.net":
        return httpx.Response(200, text="1x Meren of Clan Nel Toth *CMDR*\n1x Sol Ring\n36x Forest\n" + "\n".join(f"1x Filler {i}" for i in range(62)))
    if url.host == "deckstats.net":
        return httpx.Response(200, text="//Main\n1 Meren of Clan Nel Toth # !Commander\n1 Sol Ring # mana\n36 Swamp\n" + "\n".join(f"1 Filler {i}" for i in range(62)))
    if url.host == "mtgjson.com":
        if url.path.endswith("/DeckList.json"):
            return httpx.Response(200, json={"meta": {}, "data": [
                {"code": "C99", "fileName": "GraveTroupe_C99", "name": "Grave Troupe", "releaseDate": "2024-05-01", "type": "Commander Deck"},
                {"code": "C98", "fileName": "OldGuard_C98", "name": "Old Guard", "releaseDate": "2019-02-01", "type": "Commander Deck"},
                {"code": "XYZ", "fileName": "Intro_XYZ", "name": "Grave Intro", "releaseDate": "2024-06-01", "type": "Intro Pack"},
                {"name": "kaputt"}]})  # fmt: skip
        if url.path.endswith("/decks/GraveTroupe_C99.json"):
            main = [{"name": n, "count": 1} for n in ("Sol Ring", "Cultivate", "Pitiless Plunderer")]
            main += [{"name": f"Filler {i}", "count": 1} for i in range(60)] + [{"name": "Forest", "count": 18}, {"name": "Swamp", "count": 18}]
            return httpx.Response(200, json={"data": {"name": "Grave Troupe", "code": "C99", "releaseDate": "2024-05-01", "type": "Commander Deck",
                                                      "commander": [{"name": "Meren of Clan Nel Toth", "count": 1}], "mainBoard": main + [{"bad": 1}],
                                                      "sideBoard": []}})  # fmt: skip
        return httpx.Response(404)
    if url.host == "json.edhrec.com":
        if url.path == "/pages/average-decks/meren-of-clan-nel-toth.json":
            return httpx.Response(200, json={"deck": ["1 Meren of Clan Nel Toth", "1 Sol Ring", "1 Pitiless Plunderer", "36 Forest",
                                                      *[f"1 Filler {i}" for i in range(61)]]})  # fmt: skip
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
    monkeypatch.setattr(collection, "COLLECTION_FILE", tmp_path / "collection.json")
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(proxy, "PROXIES_DIR", tmp_path / "proxies")
    monkeypatch.setattr(deskmat, "DESKMAT_DIR", tmp_path / "deskmats")
    for env in ("MTG_AUTOFILL_PATH", "MTG_MPCFILL_SERVER", "MTG_CARDBACK"):
        monkeypatch.delenv(env, raising=False)
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": http.USER_AGENT})
    yield
    http._client = None


def deck_lines(extra: list[str] | None = None, fillers: int | None = None) -> list[str]:
    """A legal 99-card main deck for Meren (BG)."""
    extra = extra or []
    base = ["1 Sol Ring", "1 Cultivate", *extra]
    n_fill = fillers if fillers is not None else 99 - 36 - len(base)
    return base + [f"1 Filler {i}" for i in range(n_fill)] + ["18 Forest", "18 Swamp"]
