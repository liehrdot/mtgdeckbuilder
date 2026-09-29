# Roadmap: Einsteiger-Features

Stand: 29.09.2026. Ziel: Wer mit Commander anfängt, soll Karten verstehen, sein Deck am Tisch erklären
können und gezielt besser werden. Jede Phase wird einzeln getestet, committet und gepusht.

## Phase A – Karten verstehen

- [x] Glossar (`glossary.py`): Schlüsselwörter und Fähigkeitswörter mit einer Zeile Deutsch
      (Trample, Ward, Cascade, Flash, Convoke, …) plus Commander-Begriffe (Commander-Steuer,
      Farbidentität, Game Changer, Ramp, Removal, Wipe, …); Route `/api/glossary`.
- [x] Kartentext auf Deutsch: `/api/cards/text?name=&lang=de` liefert Oracle-Text, gedruckten deutschen
      Text (sofern es einen deutschen Druck gibt) und die Schlüsselwörter der Karte.
- [x] Große Kartenansicht: Text neben dem Bild (Deutsch/Englisch umschaltbar), Schlüsselwörter mit
      Erklärung beim Hovern/Antippen, Knopf „Erklär mir die Karte“ (fragt Claude im Tab „Fragen“, wofür
      die Karte in diesem Deck da ist und wann man sie spielt).
- [x] Glossar-Seite zum Nachschlagen (Suche), erreichbar über Strg+K und die Seitenleiste.

## Phase B – Deck-Check als Ampel

- [x] `health.py`: pro Bereich grün/gelb/rot mit Zahl, Richtwert und „Warum ist das wichtig?“ –
      Länder (abhängig vom Manawert und Ramp), Ramp, Kartenzug, Removal, Board Wipes, Manakurve,
      Siegbedingungen (Kategorie „Win Condition“ oder Combos).
- [x] Deck-Ansicht: Panel „Deck-Check“ oben neben der Kartenliste.
- [x] „Beheben“: Kandidaten für die Rolle ansehen (ohne KI, passend zu den Farben) und in den
      Bearbeiten-Modus übernehmen – oder Upgrade-Vorschläge mit vorausgefülltem Fokus.

## Phase C – Deck-Anleitung, Spickzettel und Rule-0-Text

- [ ] Rule-0-Text ohne KI (`rule0.py`) aus der Prüfung: Stufe, Spielweise, Game Changer, Combos,
      Tutoren, Extra Turns, Tempo – zum Kopieren und als Vollbild zum Vorzeigen.
- [ ] Deck-Anleitung per Claude (strukturierte Antwort, nur lesend): Spielplan in drei Sätzen, frühes/
      mittleres/spätes Spiel, Mulligan-Regeln, Schlüsselkarten mit Grund, Siegwege, Combos, worauf man
      achten muss, Tipps. Gespeichert im Deck (mit Version, für die sie gilt).
- [ ] Neuer Tab „Anleitung“ mit Rule-0-Karte und Anleitung; „Drucken / als PDF“ druckt genau eine
      Spickzettel-Seite (Druck-Stylesheet).

## Phase D – Spiele festhalten

- [ ] Partien pro Deck (`decks/.games/<slug>.json`): Ergebnis, gegnerische Commander, Zug, Probleme als
      Schnellwahl („zu wenig Länder“, „kein Removal“, „zu langsam“, …), beste Karte, Notiz, Deck-Version.
- [ ] Statistik: Siege/Niederlagen, häufigste Probleme, Bilanz pro Version.
- [ ] „Aus den Partien lernen“: Upgrade-Vorschläge mit Fokus aus den häufigsten Problemen.
- [ ] MCP-Tool `deck_games` (lesend), damit Fragen und Upgrades die Partien kennen.

## Phase E – Geführter Commander-Finder

- [ ] Quiz statt nur Freitext: Spielgefühl, Farben (mit Bedeutung jeder Farbe), Schwierigkeit,
      Lieblingsthemen – optional ergänzt durch Freitext.
- [ ] Vorschläge mit „Warum passt der zu dir?“ und Schwierigkeitsgrad; Anfänger bekommen einfache
      Commander bevorzugt.

## Phase F – Einstieg über Precons

- [ ] Precon-Liste von MTGJSON (DeckList + Deck-Dateien, gecacht), Suche nach Name, Commander, Jahr.
- [ ] Dritter Einstieg in „Neues Deck“: „Ich habe ein Starterdeck“ → Deck wird importiert und geprüft.
- [ ] Upgrade-Plan in Stufen (z. B. 20 € · 50 € · 100 €): eine KI-Anfrage, pro Stufe Tausche mit
      Begründung; stufenweise übernehmen.

## Abschluss

- [ ] README, CLAUDE.md, Skill-Dokumentation.
- [ ] Browser-Prüfung aller neuen Abläufe (hell, dunkel, Handy), Tests offline grün.
