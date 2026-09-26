# MTG Commander Deckbuilder für Claude Code

Du beschreibst in Claude Code, was du willst („Baue mir ein Meren-Deck, Bracket 3, max. 150 €“).
Claude nutzt dann den Skill **`commander-deckbuilder`** und den MCP-Server **`mtg`**: Es recherchiert
auf Scryfall, EDHREC und Commander Spellbook, baut ein 100-Karten-Deck, prüft Legalität und Bracket-Regeln
und speichert das Ergebnis. Alternativ geht das Ganze auch über eine **Web-GUI**.

```
Du ──► Claude Code ──► Skill: commander-deckbuilder (Workflow, Bracket-Regeln, Deck-Template)
                  └──► MCP-Server "mtg" ──► Scryfall API + Bulk-Daten (lokale SQLite-DB)
                                        ├─► EDHREC (Empfehlungen, nach Bracket/Budget/Thema)
                                        ├─► Commander Spellbook (Combos, Bracket-Schätzung)
                                        └─► Archidekt / Moxfield (Deck-Import)
GUI (Browser) ──► FastAPI ──► Claude Agent SDK ──► gleiche Skills + MCP-Server
```

## Voraussetzungen

- [Claude Code](https://code.claude.com) (eingeloggt, oder `ANTHROPIC_API_KEY` gesetzt)
- [uv](https://docs.astral.sh/uv/) (Python-Paketmanager; installiert Python 3.10+ bei Bedarf selbst)

## Einrichtung

```bash
git clone https://github.com/liehrdot/mtgdeckbuilder.git
cd mtgdeckbuilder
uv sync --extra gui          # Abhängigkeiten inkl. GUI installieren
```

Beim ersten Start von `claude` in diesem Ordner fragt Claude Code, ob du dem Projekt und dem
MCP-Server `mtg` aus `.mcp.json` vertraust – bestätigen. Kontrolle: `/mcp` in Claude Code.

**Optional, empfohlen:** lokale Kartendatenbank laden (siehe unten). In Claude Code einfach
„Aktualisiere die Kartendatenbank“ sagen oder in der GUI den Button „Scryfall-Bulk-Daten laden“ drücken.

## Benutzung in Claude Code

```bash
claude
```

Dann z. B.:

- `/commander-deckbuilder Meren of Clan Nel Toth, Bracket 2, Budget 100 €`
- „Baue mir ein Atraxa-Superfriends-Deck für Bracket 4.“
- „Mir fällt kein Commander ein: ich mag Drachen und große Zauber, Bracket 3.“ → Skill
  `commander-finder` schlägt 3–5 passende Commander mit Begründung vor, danach wird gebaut
  (auch direkt: `/commander-finder Vampire mit Lifedrain, budgetfreundlich`).
- „Baue ein Proxy-Deck mit Atraxa für Bracket 4“ – bei Proxy-Decks spielen Preise keine Rolle.
- „Setz Cyclonic Rift und Smothering Tithe auf meine Blacklist.“
- „Mach mein Deck `meren-aristocrats` billiger, max. 80 €.“
- „Welches Bracket hat dieses Deck? https://archidekt.com/decks/123456“
- Deutsche Kartennamen („Schwerter zu Pflugscharen“, „Sol-Ring“) funktionieren mit der lokalen DB.

Fertige Decks landen in `decks/<name>.json` und `decks/<name>.txt`. Die `.txt` kannst du direkt in
Moxfield, Archidekt oder ManaBox importieren.

## Brackets

| Bracket | Name | Game Changer | Mass Land Denial | Extra Turns | 2-Karten-Combos |
|---|---|---|---|---|---|
| 1 | Exhibition | 0 | nein | nein | nein |
| 2 | Core | 0 | nein | wenige, nicht chainen | nein |
| 3 | Upgraded | max. 3 | nein | wenige, nicht chainen | nicht früh im Spiel |
| 4 | Optimized | beliebig | ja | ja | ja |
| 5 | cEDH | beliebig | ja | ja | ja |

Stand: WotC-Update vom 21.10.2025 (keine Tutor-Limits mehr) und Game-Changer-Update vom 09.02.2026.
Die Game-Changer-Liste wird **live von Scryfall** (`is:gamechanger`) gelesen, nicht hart kodiert.
`validate_deck` fragt zusätzlich Commander Spellbook nach einer Bracket-Schätzung und nach Combos.

## Power-Feinabstimmung: Bracket rauf/runter

Ein Bracket ist breit – deshalb gibt es pro Deck ein **Power-Profil**:

- **Feinstufe:** unteres / mittleres / oberes Bracket („Lower 4“, „Upper 3“).
- **Hausregeln** (nur strenger als das Bracket): max. Game Changer (z. B. Bracket 4 ohne Game
  Changer, Bracket 3 mit max. 2), max. Tutoren, keine 2-Karten-Combos / Extra Turns / Mass Land Denial.
- **Stil/Vibe:** z. B. „witzig“, „chaotisch“, „politisch“, „thematisch“ („Bracket 2, aber witzig“).
- **Power-Score:** `validate_deck` schätzt die Stärke auf der Bracket-Skala (z. B. 3.8 = oberes
  Bracket 3) aus Game Changern, Tutoren, Fast Mana, Free Spells, Combos, Kurve und Interaktion –
  transparent mit Einzelposten. Weicht das Deck deutlich von der Ziel-Stufe ab, gibt es eine Warnung.

In Claude Code: „Mach mein Meren-Deck zu einem unteren Bracket 4“, „Bracket 3 mit max. 2 Game
Changern“, „eine Stufe schwächer und witziger“. In der GUI: Panel „Power & Bracket“ mit Skala
(aktuell vs. Ziel), ▲/▼ „Eine Stufe stärker/schwächer“, Hausregeln, Stil und „Deck umbauen“.
Beim Neubau gibt es dieselben Optionen unter „Feinabstimmung“.

## Versionen: Verlauf, Diff, Wiederherstellen, Kopieren

Jede Speicherung eines Decks ist eine **Version** mit vollständigem Snapshot
(`decks/.versions/<deck>/`). Der Verlauf zeigt pro Version Notiz, Karten rein/raus,
Stufe (z. B. „oberes Bracket 3 → unteres Bracket 4“), Preis und Power-Score.

- **Vergleichen:** zwei beliebige Versionen (Karten, Stufe, Power, Preis)
- **Wiederherstellen:** alte Version wird wieder aktuell – als neue Version, nichts geht verloren
- **Als neues Deck kopieren:** z. B. eine Bracket-2- und eine Bracket-4-Variante desselben Commanders
- **Liste kopieren:** Decklist jeder Version für Moxfield/Archidekt

In Claude Code: „Was hat sich seit Version 2 geändert?“, „Mach die letzte Änderung rückgängig“,
„Kopier das Deck als Bracket-2-Variante“ (Tools `list_deck_versions`, `compare_deck_versions`,
`restore_deck_version`, `copy_deck`).

## Proxies drucken: Druckstudio mit MPC Autofill

Das [MPC Autofill](https://github.com/chilli-axe/mpc-autofill)-Programm (`autofill-windows.exe`)
lädt Bestellungen auf MakePlayingCards hoch. Der Deckbuilder erledigt alles davor selbst:

1. **Bildauswahl** – pro Karte automatisch der beste Scan: eigene Wahl › MPC-Autofill-Community-Scan
   (wenn ein Server eingestellt ist) › Scryfall. Im **Druckstudio** der GUI siehst du alle Karten als
   Vorschau und kannst per Klick jede Karte gegen einen anderen Scan oder einen anderen Druck tauschen.
   Doppelseitige Karten bekommen ihre Rückseite.
2. **Druckdateien vorbereiten** – paralleler Download mit Cache (über alle Decks), Scryfall-Scans
   bekommen automatisch Beschnitt-Rand (Bleed) und gefüllte Ecken, dazu ein Kartenrücken (eigener,
   vom MPC-Autofill-Server oder ein schlichter). Ergebnis: `proxies/<deck>/<deck>.xml` mit nur
   lokalen Dateien – das Autofill-Programm muss nichts mehr herunterladen oder umrechnen.
   Die fertigen Druckbilder liegen lesbar benannt in `proxies/<deck>/images/` (z. B. `Sol Ring.jpg`,
   `_Kartenrücken.jpg`; Hardlinks auf den Cache, kein doppelter Speicher). Button **„Ordner öffnen“**
   öffnet ihn im Explorer/Finder. Die Lupe 🔍 auf jeder vorbereiteten Karte zeigt **Vorher/Nachher**:
   Original-Scan und Druckdatei nebeneinander, mit Zoom (Einpassen – 8×) und gekoppeltem Scrollen –
   ideal, um KI-hochskalierte Bilder zu kontrollieren. Der Bild-Cache selbst liegt unter
   `~/.cache/mtgdeck/images/` (Windows: `C:\Users\<Name>\.cache\mtgdeck\images\`).
   **Optional: KI-Hochskalierung (Opt-in, standardmäßig aus).** Mit Häkchen „KI-Hochskalierung“ im
   Druckstudio werden Scryfall-Scans mit [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)
   (ncnn-vulkan, kostenlos) vierfach hochgerechnet und mit 600 statt 300 DPI aufbereitet – Text und
   Linien werden deutlich schärfer. Einrichtung: `realesrgan-ncnn-vulkan-20220424-windows.zip` (bzw. `-ubuntu`/`-macos`) aus dem
   [Release v0.2.5.0](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.2.5.0) herunterladen und nach
   `tools/realesrgan/` entpacken (der Ordner `models/` muss daneben liegen) oder den Pfad in den
   Einstellungen eintragen. Benötigt eine Vulkan-fähige Grafikkarte (integrierte Grafik reicht), dauert je
   nach GPU ein bis wenige Sekunden pro Karte; Ergebnisse werden gecacht. MPC-Autofill-Scans werden nie
   hochskaliert (die haben schon 800 DPI). KI kann feine Details verfälschen – stichprobenartig prüfen.
   **Druckraster entfernen:** Scryfall-Bilder sind Scans *gedruckter* Karten; Real-ESRGAN schärft das
   Druckraster sonst zu sichtbarer Schraffur (Himmel, Textfeld). Vor dem Hochskalieren wird der Scan
   deshalb minimal weichgezeichnet (Gauß 0,6 px) – in Tests mit echtem Real-ESRGAN verschwand die
   Schraffur, Text und Details blieben scharf. Einstellbar unter „KI-Hochskalierung“:
   aus / leicht / **normal** (Standard) / stark. Tipp: für Kartenscans `realesrgan-x4plus` nutzen,
   nicht das Anime-Modell.
3. **PDF zum Selbstdrucken** – direkt im Deckbuilder (ohne exe): A4/Letter, 3 × 3 Karten à 63 × 88 mm,
   Schnittmarken, optional mit DFC-Rückseiten.
4. **An MakePlayingCards senden** – startet das Autofill-Programm mit passenden Optionen in einem
   **Terminal direkt in der GUI** (echtes Pseudo-Terminal: ConPTY unter Windows 10/11, pty unter
   macOS/Linux). Seine Menüs – z. B. „How would you like to upload this order?“ – bedienst du dort mit
   Pfeiltasten und Enter (oder den Buttons ↑ ↓ Enter darunter). Login und Upload laufen im sich
   öffnenden Browser. Alternativ Häkchen „eigenes Fenster“ für ein separates Konsolenfenster.

Einrichtung: `autofill-windows.exe` in den Ordner `tools/` legen (oder Pfad unter „Einstellungen“),
optional die MPC-Autofill-Server-URL eintragen (die du auf mpcfill.com verwendest) und einen eigenen
Kartenrücken. Die `order.xml` lässt sich auch auf mpcfill.com importieren.

In Claude Code: „Druck mir das Deck als Proxies“, „Mach ein PDF zum Selbstdrucken“,
„Schick es an MakePlayingCards“ (Tools `create_proxy_order`, `export_proxy_pdf`,
`launch_proxy_tool`, `proxy_settings`).

## Doppelseitige Karten (DFC)

Transform-, Modal-DFC- und ähnliche Karten (z. B. *Delver of Secrets // Insectile Aberration*)
werden überall mit beiden Seiten behandelt:

- **Deckansicht:** Symbol ⇄ hinter dem Namen; beim Überfahren erscheinen beide Seiten nebeneinander,
  ein Klick öffnet eine große Ansicht (auch am Tablet).
- **Druckstudio:** Schild „DFC“, ↻ dreht die Kachel um; für die Rückseite lässt sich ein eigenes Bild
  wählen und per 🔍 kontrollieren.
- **MPC:** Die Rückseite wird auf die Rückseite *derselben* Karte gedruckt (statt des Kartenrückens) –
  wie das Original, keine Platzhalterkarte nötig.
- **PDF zum Selbstdrucken:** Mit „DFC-Rückseiten“ kommen die Rückseiten als zusätzliche Karten ans
  Ende (zum Beilegen in die Hülle oder als Checkliste).
- Kartendatenbanken, die vor dieser Funktion geladen wurden, kennen die Rückseiten noch nicht: Sie werden
  dann automatisch live bei Scryfall nachgeladen, und die GUI empfiehlt ein Update der Datenbank.

## Commander finden, Proxy-Decks, Blacklist

- **Commander finden:** Beschreibe frei, was du spielen willst (Farben, Kreaturtypen, Mechaniken,
  Spielstil). Der Skill `commander-finder` sucht Kandidaten (Scryfall, lokale DB, EDHREC), prüft
  Oracle-Text, Bracket-Tauglichkeit und Budget und liefert 3–5 Vorschläge mit Begründung.
- **Proxy-Deck:** Wird das Deck geproxt, ignoriert Claude jedes Budget und wählt die besten
  Karten für Bracket und Strategie (Bracket-Regeln gelten weiterhin). Das Deck wird als
  Proxy-Deck markiert, der echte Kartenwert nur informativ angezeigt.
- **Budget:** `validate_deck`/`save_deck` warnen, wenn das Deck das Budget überschreitet.
- **Blacklist:** Karten, die nie in ein Deck dürfen. Gepflegt in der GUI, per Claude
  („setz X auf die Blacklist“, Tool `update_blacklist`) oder direkt in `blacklist.txt`
  (eine Karte pro Zeile, `#` = Kommentar; Datei ist gitignored). Deutsche Namen werden in
  Oracle-Namen übersetzt. Suchergebnisse blenden Blacklist-Karten aus, die Validierung meldet
  sie als Fehler.

## Web-GUI

```bash
uv run mtg-gui              # → http://127.0.0.1:8765
```

- „Keine Idee? Commander finden“: Wunsch eingeben → Vorschlagskarten mit Bild und Begründung →
  „Übernehmen“ (füllt das Formular) oder direkt „Deck bauen“
- Commander mit Autovervollständigung (Scryfall), Bracket 1–5, Budget/Währung oder Proxy-Schalter,
  Thema, Wünsche, Modell
- Blacklist-Panel: Karten hinzufügen (mit Autovervollständigung) oder entfernen
- „Deck bauen lassen“ startet Claude Code im Hintergrund (Claude Agent SDK); der Fortschritt
  (Tool-Aufrufe, Zwischentexte) wird live angezeigt
- Deckansicht: Prüfung (legal, Bracket, Game Changer, Combos …), Manakurve, Rollen, Preis,
  Kartenbilder beim Hovern, Liste kopieren, „Überarbeiten lassen“ per Freitext
- Die GUI nutzt deine Claude-Code-Anmeldung; Kosten fallen wie bei einer normalen Claude-Code-Sitzung an.

## Datenquellen & Zugänge

Für keine der Quellen ist ein API-Key oder Account nötig.

| Quelle | Zugang | Wofür |
|---|---|---|
| **Scryfall API** | offizielle REST-API, kein Key (User-Agent + Rate-Limits werden eingehalten) | Kartensuche, Preise, Legalität, Game Changer, Autocomplete |
| **Scryfall Bulk Data** | tägliche `jsonl.gz`-Exporte über `/bulk-data` | lokale DB: **All Cards** (alle Drucke, alle Sprachen) + **Oracle Tags** (Tagger) |
| **EDHREC** | öffentliche JSON-Dateien (`json.edhrec.com`), inoffiziell | Empfehlungen, Synergie, Themen, Average Deck – auch gefiltert nach Bracket und Budget |
| **Commander Spellbook** | offizielle Backend-API, kein Key | Combos im Deck / fast im Deck, Bracket-Schätzung |
| **Archidekt** | öffentliche Deck-API, kein Key | Decks importieren |
| **Moxfield** | *keine* öffentliche API (Cloudflare, User-Agent-Whitelist) | Import nur „best effort“ – sonst Text-Export einfügen |

Alle Anfragen werden 24 h auf der Platte gecacht (`~/.cache/mtgdeck`).

### Lokale Kartendatenbank (Scryfall Bulk Data)

`update_card_database` (bzw. der GUI-Button) lädt die Scryfall-Bulk-Files und baut daraus
`~/.cache/mtgdeck/cards.sqlite`:

- **All Cards** (≈ 375 MB komprimiert, Standard): wird zu einer Zeile pro Oracle-ID zusammengefasst.
  Dadurch werden **Kartennamen in allen Sprachen** erkannt und als Preis gilt der
  **günstigste Papier-Druck** – gut für Budget-Decks. Preise sind bis zu ~24 h alt (Schätzwert).
- **Oracle Tags** (Scryfall Tagger): Kartenrollen wie `ramp`, `draw`, `removal`, `sweeper`, `tutor`
  inkl. Tag-Hierarchie – deutlich bessere Rollenerkennung als Textmuster.
- Aktualisierung höchstens wöchentlich (Spieldaten ändern sich selten); `force` erzwingt es.
- Ohne lokale DB funktioniert alles weiterhin über die Live-API (langsamer, nur englische Namen).

## MCP-Tools (Server `mtg`)

| Tool | Zweck |
|---|---|
| `search_cards` | Live-Suche mit Scryfall-Syntax (`id<=bg otag:ramp -is:gamechanger eur<3`) |
| `local_card_search` | schnelle Offline-Suche: Farbidentität, Tagger-Tags, Text, Typ, Preis, ohne Game Changer |
| `get_cards` | Kartendaten zu Namen (auch deutsch) |
| `find_commanders` | Commander-Suche |
| `edhrec_recommendations` / `edhrec_average_deck` | EDHREC-Daten (optional Bracket, Thema, Budget) |
| `find_combos` | Commander Spellbook: Combos im Deck / fehlt eine Karte |
| `bracket_rules` / `game_changers` | Bracket-Regeln, aktuelle Game-Changer-Liste |
| `validate_deck` | 100 Karten, Singleton, Farbidentität, Bannliste, Blacklist, Budget, Rollen-Richtwerte, Bracket-Prüfung, Power-Profil & Power-Score |
| `save_deck` / `load_deck` / `list_decks` / `export_deck` | Decks speichern, laden, exportieren |
| `import_deck` | Archidekt-/Moxfield-URL importieren |
| `get_blacklist` / `update_blacklist` | Blacklist lesen / Karten hinzufügen oder entfernen |
| `list_deck_versions` / `compare_deck_versions` | Versionsverlauf, Diff zwischen Versionen |
| `restore_deck_version` / `copy_deck` | alte Version wiederherstellen, Deck (oder alte Version) kopieren |
| `create_proxy_order` / `export_proxy_pdf` | Proxy-Druckdateien (MPC-Autofill-XML + Bilder), PDF zum Selbstdrucken |
| `launch_proxy_tool` / `proxy_settings` | MPC-Autofill-Programm starten, Proxy-Einstellungen |
| `card_db_status` / `update_card_database` | lokale Kartendatenbank |

Der Server lässt sich auch in anderen MCP-Clients nutzen (z. B. Claude Desktop):
`{"command": "uv", "args": ["run", "--directory", "/pfad/zu/mtgdeckbuilder", "mtg-mcp"]}`.

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `MTG_BULK_TYPE` | `all_cards` | `oracle_cards` (≈ 25 MB, schneller, nur Englisch) oder `default_cards` |
| `MTG_BULK_MAX_AGE_DAYS` | `7` | ab wann die lokale DB als veraltet gilt |
| `MTG_DATA_DIR` / `MTG_CACHE_DIR` | `~/.cache/mtgdeck` | Speicherort DB / HTTP-Cache |
| `MTG_CACHE_TTL` | `86400` | HTTP-Cache-Dauer in Sekunden |
| `MTG_DECKS_DIR` | `./decks` | Speicherort der Decks |
| `MTG_BLACKLIST_FILE` | `./blacklist.txt` | Blacklist-Datei |
| `MTG_PROXIES_DIR` | `./proxies` | Druckdateien (XML, Bilder-Verweise, PDF) |
| `MTG_AUTOFILL_PATH` / `MTG_MPCFILL_SERVER` / `MTG_CARDBACK` / `MTG_UPSCALER_PATH` | – | überschreiben die Proxy-Einstellungen (`mtgdeck.settings.json`) |
| `MTG_GUI_HOST` / `MTG_GUI_PORT` | `127.0.0.1` / `8765` | GUI-Adresse |
| `MTG_MAX_TURNS` | `120` | max. Agent-Schritte pro GUI-Auftrag |

## Grenzen

- EDHREC hat keine offizielle API; ändert sich das JSON-Format, liefern die EDHREC-Tools Fehler, der Rest funktioniert weiter.
- Die Bracket-Prüfung deckt die harten Regeln ab (Game Changer, MLD, Extra Turns, 2-Karten-Combos).
  „Wie schnell/konsistent ist das Deck?“ bleibt eine Einschätzung – Claude begründet sie in der Deckbeschreibung.
- Die Rollen-Zählung (Ramp, Draw, Removal …) ist heuristisch (Tagger-Tags bzw. Textmuster).

## Entwicklung

```bash
uv sync --all-extras
uv run pytest          # läuft komplett offline (HTTP wird gemockt)
```

Struktur: `src/mtgdeck/` (Python-Paket), `.claude/skills/` (Skills `commander-deckbuilder`, `commander-finder`),
`.mcp.json` (MCP-Registrierung), `tests/`.
