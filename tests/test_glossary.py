"""Glossary and card text for the beginner card view."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mtgdeck import glossary
from mtgdeck.cards import card_text
from mtgdeck.gui.app import app


def test_entries_have_explanations():
    items = glossary.entries()
    assert len(items) > 80
    assert all(i["text"] and i["kind"] in ("keyword", "action", "concept") for i in items)
    assert {"Trample", "Ward", "Commander tax", "Game Changer"} <= {i["term"] for i in items}


def test_find_terms_english_german_and_keywords():
    terms = {t["term"] for t in glossary.find_terms("Flying, trample\nWhenever ~ attacks, scry 2.")}
    assert {"Flying", "Trample", "Scry"} <= terms
    assert "Flying" in {t["term"] for t in glossary.find_terms("Flugfähigkeit, Wachsamkeit")}
    assert "Vigilance" in {t["term"] for t in glossary.find_terms("Flugfähigkeit, Wachsamkeit")}
    assert "Ward" in {t["term"] for t in glossary.find_terms("", keywords=["ward"])}
    # no partial-word hits and no concept highlighting in card text
    assert not {"Ward", "Flash"} & {t["term"] for t in glossary.find_terms("Reward yourself. Flash-forward.")}
    assert "Tutor" not in {t["term"] for t in glossary.find_terms("Tutor a card")}


async def test_card_text_german_printing():
    data = await card_text("Cultivate", "de")
    assert data["name"] == "Cultivate"
    assert data["faces"][0]["text"].startswith("Search your library")
    assert data["printed"]["faces"][0]["name"] == "Kultivieren"
    assert data["printed"]["faces"][0]["text"].startswith("Durchsuche")


async def test_card_text_double_faced_and_keywords():
    data = await card_text("Delver of Secrets // Insectile Aberration", "de")
    assert [f["name"] for f in data["printed"]["faces"]] == ["Hüter der Geheimnisse", "Insektoide Abnormität"]
    assert [f["text"] for f in data["faces"]] == ["", "Flying"]  # the front face keeps its (empty) slot
    assert "Flying" in {t["term"] for t in data["terms"]}


async def test_card_text_without_german_printing():
    data = await card_text("Pitiless Plunderer", "de")
    assert data["printed"] is None
    assert data["faces"][0]["name"] == "Pitiless Plunderer"
    assert "Treasure" in {t["term"] for t in data["terms"]}
    assert await card_text("No Such Card Xyz") is None


def test_routes():
    client = TestClient(app)
    assert client.get("/api/glossary").json()[0]["term"]
    r = client.get("/api/cards/text", params={"name": "Cultivate", "lang": "de"})
    assert r.status_code == 200 and r.json()["printed"]["faces"][0]["name"] == "Kultivieren"
    assert client.get("/api/cards/text", params={"name": "Nope Nope"}).status_code == 404
