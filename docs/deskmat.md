# Deskmat-Studio – Plan

Ziel: aus einem Kartenartwork, einem beschriebenen Setting oder einem eigenen Bild eine Deskmat-/Playmat-Datei
mit **etwa 4K** (lange Seite 4096 px, wahlweise 3840 / 5120) erzeugen – zugeschnitten auf das Mattenformat.

## Bausteine

- [x] `deskmat.py`
  - Formate: Playmat 61 × 35,5 cm, Deskmat 80 × 30 / 90 × 40 / 120 × 60 cm, 16:9 (Bildschirm); Zielgröße aus
    langer Seite und Seitenverhältnis.
  - Projekte in `deskmats/<id>/` (`MTG_DESKMAT_DIR`, gitignored): `source.*`, `meta.json`, Kandidaten, Ergebnis
    `deskmat-<B>x<H>.png` und eine kleine Vorschau.
  - Quellen:
    - Scryfall `art_crop` eines Drucks (reines Artwork, ohne Rahmen und Text – nichts muss entfernt werden);
    - MPC-Autofill-Scan (hochaufgelöst, mit Rahmen → Standard-Zuschnitt auf die Artwork-Box);
    - hochgeladenes Bild;
    - generiertes Bild.
  - Bildgenerator: URL-Vorlage in den Einstellungen (`image_generator_url`), Standard ist das kostenlose
    pollinations.ai (kein Key). Der Prompt wird dorthin geschickt; die GUI sagt das.
  - Rendern:
    1. Zuschnitt (Füllen: Fenster im Zielformat, Mittelpunkt + Zoom) bzw. Einpassen (Bild vollständig, Ränder
       als unscharf erweiterter, abgedunkelter Hintergrund).
    2. Bei Karten-Scans Entrasterung (Einstellung `descreen`).
    3. Real-ESRGAN ×4 (vorhandener Upscaler, Modell aus den Einstellungen).
    4. Lanczos auf die exakte Zielgröße plus leichtes Nachschärfen.
    - Ohne Upscaler oder ohne GPU: Lanczos mit Warnung.
- [x] Claude-Job „Setting → Bild“: strukturierte Antwort (Titel, englischer Bild-Prompt), optional mit
      Stimmung aus einem Deck (Commander, Beschreibung); danach 1–4 Varianten (Seeds) erzeugen, eine auswählen.
- [x] Routen `/api/deskmat…` (Formate, Projekte, Karte, Upload, Generieren, Auswahl, Rendern als Hintergrund-Job
      mit Fortschritt, Dateien, Löschen, Ordner öffnen); MCP-Tool `create_deskmat` für Claude Code.
- [x] GUI-Seite „Deskmat-Studio“ (`#/deskmat[/<id>]`, Seitenleiste, Strg+K, ⋯-Menü im Deck „Deskmat aus diesem
      Deck“):
  1. Motiv – Kartenartwork · Setting beschreiben · Eigenes Bild;
  2. Format & Zuschnitt – Vorschau zum Ziehen, Zoom, Füllen/Einpassen, Angabe von Pixeln und effektiven DPI;
  3. „Deskmat erstellen“ – Fortschritt, Ergebnis, Download;
  4. „Meine Deskmats“.
- [x] Tests offline (Mocks für Scryfall-Bilder, MPC, Generator, Fake-Upscaler), Browser-Prüfung, Doku.

## Hinweise

- Scryfall-`art_crop` ist klein (oft ~600 px breit). Für 4K ist das ein Faktor ~6,5: KI ×4 plus Lanczos. Scharf
  wird es mit einem MPC-Scan oder einem generierten Bild in hoher Auflösung.
- 4096 px auf 61 cm sind ≈ 170 DPI – für Stoffmatten üblich ausreichend. Die GUI zeigt die effektiven DPI an.
- Die externen Dienste (Scryfall, pollinations.ai, MPC Autofill) waren aus der Entwicklungsumgebung nicht erreichbar;
  getestet ist mit nachgebauten Antworten.
