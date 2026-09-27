"""Deck exports for playing online: Cockatrice (.cod) and Tabletop Simulator (saved object JSON)."""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape, quoteattr

# Scryfall's standard Magic card back
CARD_BACK = "https://backs.scryfall.io/large/0/a/0aeebaf5-8c7d-4636-9e82-8c27447861f7.jpg"
_DFC = {"transform", "modal_dfc", "reversible_card", "meld"}


def _play_name(name: str, card: dict[str, Any]) -> str:
    """Cockatrice/TTS use the front face for double-faced cards, the full name for split cards."""
    return name.split(" // ")[0] if card.get("layout") in _DFC else name


def to_cockatrice(deck: dict[str, Any], card_data: dict[str, dict[str, Any]]) -> str:
    """Cockatrice deck file; the commander goes to the sideboard zone (common Commander convention)."""
    main = "\n".join(
        f'    <card number="{int(c.get("qty", 1))}" name={quoteattr(_play_name(c["name"], card_data.get(c["name"], {})))}/>'
        for c in deck.get("cards", [])
    )
    side = "\n".join(f"    <card number=\"1\" name={quoteattr(_play_name(n, card_data.get(n, {})))}/>" for n in deck.get("commanders", []))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<cockatrice_deck version="1">\n'
        f"  <deckname>{escape(deck.get('name', ''))}</deckname>\n"
        f"  <comments>{escape(deck.get('description', ''))}</comments>\n"
        f'  <zone name="main">\n{main}\n  </zone>\n  <zone name="side">\n{side}\n  </zone>\n</cockatrice_deck>\n'
    )


def _transform(x: float = 0.0, face_up: bool = False) -> dict[str, float]:
    return {"posX": x, "posY": 1.0, "posZ": 0.0, "rotX": 0.0, "rotY": 180.0, "rotZ": 0.0 if face_up else 180.0,
            "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0}  # fmt: skip


def to_tts(deck: dict[str, Any], card_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Tabletop Simulator saved object: the library as one deck plus the commander(s) face up.

    Copy the file to ``Documents/My Games/Tabletop Simulator/Saves/Saved Objects`` and spawn it.
    Cards without an image are skipped (listed under ``skipped``).
    """
    custom: dict[str, dict[str, Any]] = {}
    ids: dict[str, int] = {}
    skipped: list[str] = []

    def card_obj(name: str, transform: dict[str, float]) -> dict[str, Any] | None:
        c = card_data.get(name, {})
        image = c.get("image")
        if not image:
            skipped.append(name)
            return None
        if name not in ids:
            ids[name] = len(ids) + 1
            custom[str(ids[name])] = {"FaceURL": image.replace("/normal/", "/large/"), "BackURL": CARD_BACK,
                                      "NumWidth": 1, "NumHeight": 1, "BackIsHidden": True, "UniqueBack": False}  # fmt: skip
        deck_id = ids[name]
        return {"Name": "Card", "Nickname": _play_name(name, c), "CardID": deck_id * 100, "Transform": transform,
                "CustomDeck": {str(deck_id): custom[str(deck_id)]}}  # fmt: skip

    library = []
    for entry in deck.get("cards", []):
        for _ in range(int(entry.get("qty", 1))):
            if obj := card_obj(entry["name"], _transform()):
                library.append(obj)
    states: list[dict[str, Any]] = [{
        "Name": "DeckCustom", "Nickname": deck.get("name", ""), "Description": deck.get("description", ""),
        "Transform": _transform(), "DeckIDs": [o["CardID"] for o in library],
        "CustomDeck": {k: v for k, v in custom.items()}, "ContainedObjects": library,
    }]  # fmt: skip
    for i, name in enumerate(deck.get("commanders", [])):
        if obj := card_obj(name, _transform(3.0 + 2.5 * i, face_up=True)):
            states.append(obj)
    return {"SaveName": "", "GameMode": "", "Date": "", "Table": "", "Sky": "", "Note": "", "Rules": "", "XmlUI": "",
            "LuaScript": "", "ObjectStates": states, "TabStates": {}, "VersionNumber": "", "skipped": sorted(set(skipped))}  # fmt: skip
