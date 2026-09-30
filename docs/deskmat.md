# Deskmat-Studio – Plan

Ziel: aus einem Kartenartwork, einem beschriebenen Setting oder einem eigenen Bild eine Deskmat-/Playmat-Druckdatei
in **300 DPI (Minimum) oder 600 DPI** für die echte Mattengröße erzeugen – zugeschnitten aufs Format, optional
mit Beschnitt. (Erste Version: „etwa 4K“; auf Wunsch auf Druck-DPI umgestellt.)

## Bausteine

- [x] `deskmat.py`
  - Formate: Playmat 61 × 35,5 cm, Deskmat 80 × 30 / 90 × 40 / 120 × 60 cm; Zielgröße = (Maß + 2 × Beschnitt)
    × DPI; über 250 MP gesperrt (120 × 60 cm bei 600 DPI).
  - Projekte in `deskmats/<id>/` (`MTG_DESKMAT_DIR`, gitignored): `source.*`, `meta.json`, Kandidaten, Ergebnis
    `deskmat-<B>x<H>-<dpi>dpi.png|jpg` (DPI in der Datei) und eine kleine Vorschau.
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
    3. Real-ESRGAN ×4 (vorhandener Upscaler); bei Faktor > 4,5 ein zweiter Durchgang, der von einem Viertel
       der Zielgröße startet und so genau auf der Druckgröße landet.
    4. Lanczos auf die exakte Zielgröße plus leichtes Nachschärfen.
    - Ohne Upscaler oder ohne GPU: Lanczos mit Warnung.
- [x] Claude-Job „Setting → Bild“: strukturierte Antwort (Titel, englischer Bild-Prompt), optional mit
      Stimmung aus einem Deck (Commander, Beschreibung); danach 1–4 Varianten (Seeds) erzeugen, eine auswählen.
- [x] Routen `/api/deskmat…` (Formate, Projekte, Karte, Upload, Generieren, Auswahl, Rendern als Hintergrund-Job
      mit Fortschritt, Dateien, Löschen, Ordner öffnen); MCP-Tool `create_deskmat` für Claude Code.
- [x] GUI-Seite „Deskmat-Studio“ (`#/deskmat[/<id>]`, Seitenleiste, Strg+K, ⋯-Menü im Deck „Deskmat aus diesem
      Deck“):
  1. Motiv – Kartenartwork · Setting beschreiben · Eigenes Bild;
  2. Format & Zuschnitt – Vorschau zum Ziehen, Zoom, Füllen/Einpassen, DPI, Beschnitt (Schnittlinie), PNG/JPEG;
  3. „Deskmat erstellen“ – Fortschritt, Ergebnis, Download;
  4. „Meine Deskmats“.
- [x] Tests offline (Mocks für Scryfall-Bilder, MPC, Generator, Fake-Upscaler), Browser-Prüfung, Doku.

## Hinweise

- Scryfall-`art_crop` ist klein (oft ~600 px breit). Für 300 DPI auf 61 cm ist das ein Faktor ~12: zwei KI-Durchgänge.
  Scharf wird es mit einem MPC-Scan, einem generierten oder eigenen großen Bild.
- Dateien: Playmat 300 DPI = 30 MP, 600 DPI = 121 MP; JPEG 95 % ist dann die handlichere Wahl.
- Die externen Dienste (Scryfall, pollinations.ai, MPC Autofill) waren aus der Entwicklungsumgebung nicht erreichbar;
  getestet ist mit nachgebauten Antworten.
