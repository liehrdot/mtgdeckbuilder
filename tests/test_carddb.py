import gzip
import json
from contextlib import closing

from conftest import card

from mtgdeck import carddb
from mtgdeck.cards import resolve


def build_db(tmp_path):
    sol_en = card("Sol Ring", type_line="Artifact", text="{T}: Add {C}{C}.", eur="2.00")
    sol_de = dict(sol_en, lang="de", printed_name="Sol-Ring", prices={"eur": "0.80"})
    swords = card("Swords to Plowshares", ci="W", type_line="Instant", text="Exile target creature.", eur="1.50")
    swords_de = dict(swords, lang="de", printed_name="Schwerter zu Pflugscharen", prices={})
    delver = card("Delver of Secrets // Insectile Aberration", ci="U")
    delver["card_faces"] = [{"name": "Delver of Secrets", "type_line": "Creature"}, {"name": "Insectile Aberration", "type_line": "Creature"}]
    token = dict(card("Goblin"), layout="token")
    digital = dict(card("Sol Ring"), digital=True, prices={"eur": "0.01"})

    path = tmp_path / "all.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        for c in [sol_de, sol_en, swords, swords_de, delver, token, digital]:
            fh.write(json.dumps(c) + "\n")
    tags = [
        {"object": "tag", "id": "t-removal", "slug": "removal", "parent_ids": [], "taggings": []},
        {"object": "tag", "id": "t-exile", "slug": "removal-exile", "parent_ids": ["t-removal"], "aliases": ["exile-removal"],
         "taggings": [{"oracle_id": swords["oracle_id"], "weight": "strong"}]},
        {"object": "tag", "id": "t-ramp", "slug": "ramp", "taggings": [{"oracle_id": sol_en["oracle_id"]}]},
    ]  # fmt: skip
    with closing(carddb._connect()) as conn, conn:
        n = carddb._import_cards(conn, carddb._iter_jsonl_gz(path))
        carddb._import_tags(conn, tags)
    return n


def test_import_aggregates_printings(tmp_path):
    assert build_db(tmp_path) == 3
    found, missing = carddb.lookup(["Sol-Ring", "schwerter zu pflugscharen", "Insectile Aberration", "Nope"])
    by_name = {c["name"]: c for c in found}
    assert missing == ["Nope"]
    # English printing represents the card, price is the cheapest paper printing
    assert by_name["Sol Ring"]["price_eur"] == "0.80"
    assert by_name["Sol Ring"]["tags"] == ["ramp"]
    # child tag rolls up to its parent
    assert set(by_name["Swords to Plowshares"]["tags"]) == {"removal", "removal-exile"}
    assert "Delver of Secrets // Insectile Aberration" in by_name
    st = carddb.status()
    assert st["available"] and set(st["languages"]) == {"de", "en"}


def test_search_filters(tmp_path):
    build_db(tmp_path)
    names = lambda **kw: [c["name"] for c in carddb.search(**kw)]  # noqa: E731
    assert names(tags=["removal"]) == ["Swords to Plowshares"]
    assert names(tags=["exile-removal"]) == ["Swords to Plowshares"]  # alias
    assert "Swords to Plowshares" not in names(color_identity="BG")
    assert "Sol Ring" in names(color_identity="")
    assert set(names(max_price=1.0, color_identity="WUBRG")) == {"Sol Ring", "Delver of Secrets // Insectile Aberration"}


async def test_resolve_prefers_local_db_and_renames(tmp_path):
    build_db(tmp_path)
    cards, renames, missing = await resolve(["Sol-Ring", "Cultivate"])  # Cultivate only via (mock) API
    assert renames == {"Sol-Ring": "Sol Ring"}
    assert set(cards) == {"Sol Ring", "Cultivate"}
    assert "ramp" in cards["Sol Ring"]["roles"]
    assert missing == []
