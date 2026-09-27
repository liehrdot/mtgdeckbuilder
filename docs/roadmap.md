# Roadmap: Sammlung, eigene Tausche, Tokens, Testhand, Upgrades

Stand: 27.09.2026. Jede Phase ist für sich nutzbar und wird einzeln getestet, committet und gepusht.
Reihenfolge nach Abhängigkeit: Phase 1 (Karten selbst tauschen) liefert die Speicherfunktion, die
Upgrades (Phase 5) und die Sammlung (Phase 2) wiederverwenden.

## Phase 1 – Karten selbst tauschen (ohne Claude)

- [x] Backend `deckedit.py`: `edit_deck(slug, add, remove, set_qty, set_category, note)` löst Namen
      auf (auch deutsch), prüft neu (Legalität, Bracket, Budget, Blacklist) und speichert eine neue
      Version mit Änderungsnotiz („Manuell: + X, − Y“).
- [x] API `POST /api/decks/{slug}/cards`, MCP-Tool `edit_deck` (Claude kann gezielt tauschen, ohne die
      ganze Liste neu zu schicken).
- [x] „Ähnliche Karten“: `GET /api/decks/{slug}/similar?card=` – gleiche Rolle/Oracle-Tags, passende
      Farbidentität, nicht im Deck, nicht auf der Blacklist, nach EDHREC-Beliebtheit sortiert.
- [x] GUI Tab „Karten“: Bearbeiten-Modus mit Kartensuche („+ Karte hinzufügen“), Anzahl ±, Entfernen,
      Kategorie ändern, pro Karte „Ähnliche Karten“ → Tausch mit einem Klick.

## Phase 2 – Meine Sammlung

Datenmodell (`collection.json`, gitignored, `MTG_COLLECTION_FILE`), ein Eintrag pro Druck:
`name` (englischer Oracle-Name) · `qty` · `proxy` (ja/nein) · `foil` · `lang` · Druck/Artwork
(`set`, `set_name`, `collector_number`, `scryfall_id`, `image`) · `note` · `added`.
Gleicher Name + Druck + Proxy + Foil + Sprache werden zusammengeführt (Anzahl addiert).

- [ ] Backend `collection.py`: hinzufügen/ändern/löschen, Import (ManaBox-, Moxfield-, Archidekt-CSV
      mit Proxy-Spalte, Textlisten „2 Sol Ring (C21) 263 *F*“), Export als CSV, Druck-Auflösung über
      Scryfall-ID oder Set+Nummer, Artwork-Auswahl aus allen Drucken.
- [ ] Deck-Abgleich: pro Karte echt/Proxy/fehlt, Summe „72 von 100 vorhanden“, Preis der fehlenden
      Karten, Einkaufsliste, Hinweis wenn eine Karte in mehreren Decks steckt, aber zu wenige da sind.
- [ ] Druckstudio: „Nur fehlende Karten drucken“ und nach dem Druck „Gedruckte Karten als Proxies in
      die Sammlung übernehmen“ (mit dem gedruckten Artwork).
- [ ] Claude: Option „Karten aus meiner Sammlung bevorzugen“ beim Bauen, MCP-Tools `collection_search`
      und `collection_status`, Skill-Anleitung.
- [ ] GUI Seite „Meine Sammlung“: Suche, Filter (alle/echt/Proxy), Sortierung, Liste oder Bilder,
      Anzahl ±, Proxy-/Foil-Schalter, Artwork wählen, in welchen Decks, Import/Export, Karte hinzufügen.
      Im Deck: Besitz-Markierung pro Karte und Sammlungs-Zusammenfassung.

## Phase 3 – Tokens und Marker

- [ ] Kartendaten um `tokens` erweitern (Scryfall `all_parts`: Tokens, Embleme, Monarch, Initiative,
      Dungeons); Datenbank-Schema v3, ältere Datenbanken werden live ergänzt.
- [ ] Deck-Ansicht: Liste „Tokens & Marker“ mit Bildern und welche Karte sie erzeugt.
- [ ] Druckstudio: „Tokens mitdrucken“ (MPC-Autofill-Token-Suche, sonst Scryfall), Anzahl pro Token.

## Phase 4 – Testhand und Wahrscheinlichkeiten

- [ ] Tab „Testen“: Starthand ziehen, London-Mulligan, Karten nachziehen (Zug für Zug).
- [ ] Wahrscheinlichkeiten (hypergeometrisch, auf dem Play/Draw): Länder in der Starthand,
      Landdrops bis Zug 2/3/4, Ramp in den ersten Zügen, Kartenzug und Removal bis Zug 4.

## Phase 5 – Upgrade-Pfad

- [ ] „Upgrade-Vorschläge“ im Tab „Anpassen“: Budget (z. B. 20 €) und optional ein Fokus → Claude
      liefert eine sortierte Liste „+ rein / − raus, Preis, Wirkung, Grund“ (strukturierte Antwort,
      nur lesend; Karten aus der Sammlung zählen als kostenlos).
- [ ] Vorschläge einzeln anhaken und „Übernehmen“ – ohne weiteren Claude-Lauf (nutzt Phase 1).

## Phase 6 – Kleinere Verbesserungen

- [x] Kartenliste gruppieren (Kategorie, Typ, Manawert, Farbe) und sortieren (Name, Manawert, Preis).
- [ ] Schnellsuche Strg+K: Decks und Aktionen.
- [ ] Export für Cockatrice (.cod) und Tabletop Simulator (.json).
- [ ] Überschneidungen zwischen Decks (in Sammlung und Deck-Abgleich, siehe Phase 2).

## Abschluss

- [ ] README, CLAUDE.md, Skill-Dokumentation aktualisieren.
- [ ] Alle Abläufe im Browser prüfen (hell, dunkel, Handy), Tests offline grün.
