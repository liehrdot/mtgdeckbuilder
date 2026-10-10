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
                                        ├─► Archidekt, Moxfield, MTGGoldfish, TappedOut, Deckstats (Deck-Import)
                                        └─► MTGJSON (Starterdecks / Precons)
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
„Aktualisiere die Kartendatenbank“ sagen oder in der GUI unter „Einstellungen → Kartendatenbank“ laden.

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
- „Wie spiele ich `meren-aristocrats`, und wie schlägt es sich gegen Atraxa?“ – Fragen zum Deck
  beantwortet Claude nur lesend, das Deck bleibt unverändert.
- „Was ist mein stärkstes Deck und warum?“, „Welches Deck hat das meiste Potential?“, „Wie würdest du
  welches Deck umbauen?“ – Fragen über alle Decks; Claude startet mit `app_overview` (alle Decks mit
  Stufe, Power, Deck-Check, Bilanz, fehlenden Karten, dazu Gegner, Tischregeln, Sammlung) und ändert nichts.
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
Changern“, „eine Stufe schwächer und witziger“. In der GUI: Deck-Tab „Anpassen“ → „Stärke ändern“ mit
Skala (aktuell vs. Ziel), ▲/▼ „Stärker/Schwächer“, Hausregeln, Stil und „Deck umbauen“.
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
   (wenn ein Server eingestellt ist) › Scryfall. Im Deck-Tab **Drucken** der GUI siehst du alle Karten als
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
- **Blacklist:** Karten – und ganze Gruppen –, die nie in ein Deck dürfen. Gepflegt in der GUI, per
  Claude („setz X auf die Blacklist“, „keine günstigen Tutoren“, Tool `update_blacklist`) oder direkt in
  `blacklist.txt` (eine Zeile pro Eintrag, `#` = Kommentar; Datei ist gitignored). Deutsche Namen
  werden in Oracle-Namen übersetzt. Neben Karten gehen **Begriffe** (deutsch oder englisch):
  True Duals, Fetchlands, Shocklands, Günstige Tutoren (≤ 2 Mana), Alle Tutoren, Fast Mana,
  Game Changer, Extra-Züge, Massen-Landzerstörung, Gratis-Zauber, Counterspells, Board Wipes,
  Stax (kuratierte Liste), 2-Karten-Combos (über Commander Spellbook) und „teurer als 20 €“. In der
  Datei stehen sie als `@true-duals`, `@cheap-tutors`, `@price>20` …; alles andere mit `@` davor
  (`@keine Gedankenkontrolle`) wird als freier Begriff gespeichert, den Claude beachtet, der aber
  nicht automatisch geprüft wird. Suchergebnisse blenden Blacklist-Karten und Regel-Treffer aus,
  die Validierung meldet sie als Fehler („Blacklist-Regel „Günstige Tutoren“: Demonic Tutor“).
- **Gegnerdecks:** Decks, gegen die du gespielt hast – nur Commander und was dir aufgefallen ist, keine
  Kartenliste (Seite „Gegnerdecks“). Steckbrief mit eigenem Namen, Spieler, geschätztem Bracket, Runde
  (Tischregel) und Merkmalen wie Combo, viele Board Wipes, Flieger, Stax; Beobachtungen mit Datum, auch
  direkt aus „Partie festhalten“. Die Seite zeigt die Bilanz aller deiner Decks gegen das Gegnerdeck, verlinkt
  die typischen Karten des Commanders auf EDHREC und bereitet „Wie spiele ich dagegen?“ (Fragen) oder
  „Upgrades gegen dieses Deck“ vor. Umbauen, Bracket ändern, Upgrade-Vorschläge, Stufenplan und Fragen
  berücksichtigen die Gegner, die zählen: gegen das Deck schon gespielt oder in seiner Runde, sonst die
  häufigsten. Gespeichert in `decks/.opponents.json`; per Claude: „merk dir: Tims Atraxa gewinnt mit
  Thassa's Oracle“ (Tools `opponent_decks`, `update_opponent_deck`).
- **Tischregeln:** benannte Regelsätze für eine Runde („Freitagsrunde“, „Laden-Abend“). Anders als die
  Blacklist gelten sie nur für Decks, die sie gewählt haben – beim Bauen („Neues Deck“ → Tischregel) oder
  im Deck unter „Anpassen“; das Deck merkt sich seine Tischregel, Umbauen und Upgrade-Vorschläge halten
  sie ein. Ein Regelsatz kann enthalten: höchstes Bracket, max. Game Changer, max. Tutoren, Deckbudget
  (zählt auch für Proxy-Decks), „keine Proxies“, verbotene Begriffe wie bei der Blacklist (True Duals,
  Stax, 2-Karten-Combos, teurer als 10 € …), einzelne verbotene Karten und freie Absprachen, die Claude
  beachtet. Verstöße sind Fehler der Prüfung, die Deck-Ampel und der Rule-0-Text zeigen sie; gilt
  zusätzlich ein Power-Profil mit Hausregeln, gewinnt die strengere Regel. Ändert sich ein Regelsatz,
  werden seine Decks neu geprüft. „Welche meiner Decks passen?“ prüft alle Decks gegen eine Runde.
  Gespeichert in `tablerules.json` (gitignored); per Claude: „leg die Tischregel Freitagsrunde an:
  höchstens Bracket 3, keine Combos“ (Tools `table_rules`, `update_table_rule`).

## Datensicherung & Papierkorb

- **Sicherung:** Unter *Einstellungen → Daten & Sicherung* sicherst du mit einem Klick alles, was du angelegt hast –
  Decks mit Versionen, Partien und Fragen, Sammlung, Gegnerdecks, Tischregeln, Blacklist, Sammelbestellungen,
  Bildauswahl fürs Drucken, Deskmat-Motive und Einstellungen – als eine ZIP-Datei zum Herunterladen. Beim Start der
  GUI entsteht einmal am Tag automatisch eine Sicherung (die letzten 10 bleiben), gespeichert in `backups/`
  (`MTG_BACKUP_DIR`, gitignored). „Sicherung einspielen“ lädt eine ZIP hoch und stellt sie wieder her; vorher wird
  der aktuelle Stand automatisch gesichert, du kannst also zurück. Ein Lauf von Claude muss dafür beendet sein.
  Ist der PC mit einem Sync-Server verbunden, wird der Stand der Sicherung mit dem der anderen Geräte zusammengeführt;
  dort wird dabei nichts gelöscht.
- **Papierkorb:** Gelöschte Decks landen samt Versionen, Fragen und Partien im Papierkorb (`decks/.trash/`).
  Direkt nach dem Löschen gibt es „Rückgängig“, später *Einstellungen → Papierkorb* (zurückholen oder endgültig löschen).
- **Sicheres Speichern:** Alle Dateien werden atomar geschrieben (erst eine temporäre Datei, dann ausgetauscht) und
  beim Ändern gesperrt – auch wenn die GUI und ein laufender Claude-Auftrag gleichzeitig speichern. Ist eine Datei
  doch einmal beschädigt, wird sie nie als „leer“ behandelt und überschrieben: Die App legt eine Kopie
  `<name>.beschaedigt-…` daneben und meldet den Fehler; ein beschädigtes Deck erscheint als „(beschädigt)“ in der Liste.
- **Gleicher Name:** Ein neues Deck überschreibt nie ein anderes Deck mit gleichem Namen (es bekommt z. B.
  `meren-2`). Nur ein erneutes Speichern desselben Bauvorgangs (gleicher Auftrag, bzw. gleicher Commander innerhalb
  von 30 Minuten) aktualisiert das eigene Deck. Umbauten behalten Anleitung, Upgrade-Plan, „Gebaut gegen …“ und die
  Precon-Herkunft.
- **Schutz der lokalen Oberfläche:** Die GUI nimmt nur Anfragen an, die an diesen Rechner gerichtet sind, und lehnt
  ändernde Anfragen von fremden Webseiten ab. Mit `MTG_GUI_HOST=0.0.0.0` (im Netzwerk freigegeben) entfällt die Host-Prüfung.

## Sync zwischen Geräten (eigener Server)

PC, Laptop und später die Handy-App gleichen sich über einen **eigenen kleinen Sync-Server** ab, z. B. bei Hetzner
für rund 6–7 € im Monat. Wie du ihn einrichtest, steht in der [Anleitung](docs/sync-server-hetzner.md); es ist
ein Befehl auf dem Server.

- **Lokal zuerst:** Jedes Gerät hat alle Daten und funktioniert ohne Netz. Der Server ist nur die Drehscheibe.
- **Was abgeglichen wird:** Decks samt Versionen, Partien, Fragen, Chats, Gegnerdecks, Sammlung, Tischregeln,
  Blacklist, Sammelbestellungen, Druckauswahl, Token-Anzahlen, eigene Bilder und Deskmat-Motive.
  Einstellungen, Caches und Druckdateien bleiben pro Gerät.
- **Verbinden:** *Einstellungen → Sync zwischen Geräten* → Kopplungslink einfügen. Den Link zeigt der Server nach
  der Einrichtung, später jedes verbundene Gerät über „Weiteres Gerät koppeln“ (mit QR-Code; 15 Minuten gültig,
  einmal verwendbar).
- **Wann abgeglichen wird:** beim Start, kurz nach jeder Änderung, alle 5 Minuten (einstellbar), beim Zurückkehren
  ins Fenster und per Knopf. Neues von anderen Geräten meldet ein Hinweis.
- **Zusammenführen:** Einträge beider Geräte bleiben erhalten, also Partien, Gegnerdecks, Sammlung und Notizen. Ein
  Deck, das auf zwei Geräten umgebaut wurde, wird kartenweise zusammengeführt und behält alle Versionen; danach wird
  es neu geprüft. Haben beide Geräte dasselbe Feld unterschiedlich geändert, gilt die Fassung vom Server, und die
  andere steht unter „Konflikte“.
- **Sicherheit:** nur HTTPS; Geräte-Schlüssel liegen auf dem Server nur als Hash; Geräte lassen sich einzeln abmelden.
- **Handy-App „Am Tisch“** (in Arbeit, [Plan](docs/plan-handy-app.md)): Partien und Gegnerdecks am Tisch in Sekunden
  eintragen, Rule 0 zeigen, Karten auf Deutsch nachschlagen – auch offline. Eine Vorschau mit Beispieldaten liegt unter
  `https://<dein-sync-server>/app/`.

## Web-GUI

```bash
uv run mtg-gui              # → http://127.0.0.1:8765
```

Aufbau (bewusst schlicht: eine Hauptaktion pro Seite, Selteneres eingeklappt):

**Ohne KI nutzbar:** Claude Code braucht nur, wer Decks bauen, umbauen oder bewerten lässt (Deck bauen lassen,
Commander vorschlagen, Gegen meine Runde bauen, Anpassen, Upgrade-Vorschläge/-Plan, Anleitung, Fragen, „Frag
Claude“). Alles andere läuft ohne: Deck importieren (Link, Liste, Starterdeck) oder unter „Ich habe schon ein Deck“
→ **„Selbst zusammenstellen“** ein leeres Deck mit Commander anlegen und Karten im Bearbeiten-Modus hinzufügen,
Deck-Check mit Kartenvorschlägen, Testhand, Rule-0-Text, Drucken samt eigener Bilder und Tokens, Sammlung,
Sammelbestellungen, Partien, Gegnerdecks, Tischregeln, Blacklist, Glossar, Sicherung. Das Deskmat-Studio
schickt ohne KI deine Beschreibung direkt an den Bildgenerator. Die App erkennt, ob Claude Code installiert
ist (und merkt sich, wenn ein Lauf an fehlender Anmeldung scheitert); unter Einstellungen → „KI-Funktionen“ lassen
sie sich auch bewusst ausschalten. KI-Knöpfe sind dann gesperrt und ein Hinweis sagt, was stattdessen geht.


- **Seitenleiste:** „+ Neues Deck“, **Frag Claude** (Chat mit der ganzen App, siehe unten), deine Decks (ab 8 Decks mit Filter; ein roter Punkt markiert nicht
  legale), unten Meine Sammlung und drei Gruppen: **Meine Runde** (Gegnerdecks, Tischregeln, Blacklist),
  **Werkstatt** (Sammelbestellungen, Deskmat-Studio) und **Hilfe** (Glossar, Einstellungen). Läuft ein Auftrag, zeigt ein Hinweis mit Spinner oben
  in der Leiste, woran Claude gerade arbeitet – ein Klick führt zurück. Auf dem Handy klappt die Leiste
  hinter ☰ weg.
- **Neues Deck** – vier Einstiege:
  - **Ich habe einen Commander:** ein Formular in vier Schritten: 1 Commander (Autovervollständigung,
    Kartenbild, optional Partner/Background) · 2 Stärke (Bracket 1–5 mit Erklärung, „Feinabstimmung“ für
    Stufe, Hausregeln, Stil) · 3 Budget oder Proxy-Deck · 4 Wünsche. Das KI-Modell steckt unter „Erweitert“.
  - **Commander vorschlagen lassen** – ein kurzes Quiz: was dir Spaß macht (große Kreaturen, Tokens,
    Friedhof …), Lieblingsfarben mit ihrer Bedeutung, deine Erfahrung, Lieblingsthemen, optional Freitext.
    Die Vorschläge zeigen „Warum passt der zu dir?“ und wie leicht der Commander zu spielen ist; Einsteiger
    bekommen einfache Commander bevorzugt. Dann „Übernehmen“ oder direkt „Deck bauen“.
  - **Ich habe schon ein Deck:** per **Link** (Archidekt, Moxfield, MTGGoldfish, TappedOut, Deckstats,
    EDHREC-Durchschnittsdeck), als **eingefügte Liste** (Moxfield-/Arena-/Text-Export, auch deutsche
    Namen) oder als **Starterdeck** (Precons von MTGJSON, Suche nach Name, Set-Kürzel, Jahr). Vor dem Import
    zeigt eine Vorschau Name, Quelle, Kartenzahl und nicht erkannte Karten; markiert die Seite den Commander
    nicht, wählst du ihn aus den legendären Karten der Liste. Das Bracket wird geschätzt oder gewählt, die
    Kategorien der Quelle werden übernommen, wo sie passen. Moxfield blockt fremde Zugriffe oft – dann
    „Export“ → „Copy Plain Text“ und die Liste einfügen.
  - **Gegen meine Runde bauen:** Claude sieht sich deine eigenen Decks (Stufe, Power, Bilanz, gegen wen sie
    verloren haben) und deine Gegnerdecks an (Merkmale, Notizen, Bilanz) und baut das stärkste Deck dagegen –
    „am stärksten“ heißt die besten Chancen gegen genau diese Gegner, immer innerhalb von Bracket, Feinstufe,
    Tischregel, Budget und Blacklist. Du wählst die Gegnerdecks (eine Tischregel wählt ihre Runde vor), gibst
    optional einen Commander vor (sonst sucht Claude einen, den du noch nicht spielst) und hast dieselben
    Optionen wie beim normalen Bau. Das Deck merkt sich, gegen wen es gebaut wurde („Gebaut gegen …“), und
    spätere Upgrades achten besonders auf diese Gegner. In Claude Code genügt: „Bau mir das stärkste Deck gegen
    meine Runde, Bracket 3“.
    Standard ist **„3–5 Decks vorschlagen“**: Claude analysiert die Runde und schlägt 3–5 deutlich verschiedene
    Decks vor – je mit Begründung, Siegplan, Spielplan pro Gegner, Schlüsselkarten, Schwäche und Einordnung in
    Bracket/Tischregel/Budget. Dieser Lauf nutzt **immer Opus 5.5 mit extra hohem Denkaufwand** (`xhigh`), optional
    mit **Web-Recherche** (aktuelle Meta-Artikel, EDHREC, Turnier- und Decklisten; Quellen werden angezeigt). Die
    letzten Vorschläge bleiben gespeichert (`decks/.meta-suggestions.json`); „Dieses Deck bauen“ startet den Bau mit
    dem gewählten Commander und denselben Optionen, „Direkt ein Deck bauen“ überspringt die Vorschläge.
- **Frag Claude** – ein Chat mit der ganzen App: „Was ist mein stärkstes Deck und warum?“, „Welches Deck
  hat das meiste Potential?“, „Wie würdest du welches Deck umbauen?“, „Welches Deck passt zu meiner
  Runde?“, „Welche Karten fehlen mir in mehreren Decks?“ – als Freitext oder per Vorschlag. Claude bekommt
  eine Übersicht über alle Decks (Stufe, Power, legal, Preis/Budget, Tischregel, Deck-Check, Bilanz mit
  häufigen Problemen und Gegnern, fehlende Sammlungskarten, offene Upgrade-Plan-Stufen), deine Gegnerdecks,
  Tischregeln und die Sammlung und holt sich Details bei Bedarf selbst. Er **ändert nichts** – Vorschläge
  setzt du im Deck unter „Anpassen“ um. Decks in Antworten sind Links (plus „… öffnen“-Knöpfe), Kartennamen
  zeigen beim Hovern das Bild. Gespräche bleiben gespeichert (`decks/.chats/`, Teil der Sicherung), lassen
  sich umbenennen, kopieren und löschen; Anschlussfragen kennen den Verlauf. „Gründlich nachdenken“ nutzt
  Opus 5.5 mit extra hohem Denkaufwand. Läuft eine Antwort, kannst du woanders weiterarbeiten – ein Hinweis
  meldet, wenn sie da ist.
- **Während Claude arbeitet:** Status in Klartext („Claude prüft EDHREC-Empfehlungen …“), Laufzeit,
  Abbrechen; das technische Protokoll steckt unter „Details“. Ist das Deck fertig, öffnet es sich. Ein Auftrag
  endet immer sichtbar – auch wenn sein letzter Schritt scheitert oder die App inzwischen neu gestartet wurde
  („Die Verbindung zum Auftrag ist weg …“); kein Lade-Kreisel dreht ewig. Ist Scryfall, EDHREC & Co. nicht
  erreichbar, steht da, welcher Dienst und was zu tun ist (statt „Internal Server Error“).
- **Deck-Ansicht** mit Kopfzeile (Commander, Stufe, legal/nicht legal, Preis, Version) und acht Tabs –
  die Adresse merkt sich den Tab, der Zurück-Button des Browsers funktioniert:
  - **Karten:** gruppiert nach Kategorie, Typ, Manawert, Farbe oder Besitz, sortiert nach Name, Manawert
    oder Preis, als Liste (Bild beim Hovern) oder Bildraster; daneben der **Deck-Check** (Prüfung und
    Ampel-Bereiche in einem Kasten), Statistik,
    Sammlungs-Abgleich und „Tokens & Marker“. Klick auf eine Karte zeigt sie groß – bei doppelseitigen Karten
    beide Seiten – mit dem **Kartentext auf Deutsch** (gedruckter Text der neuesten deutschen Ausgabe,
    umschaltbar auf den englischen Oracle-Text), Manasymbolen und markierten Schlüsselwörtern samt Erklärung;
    „Erklär mir die Karte“ fragt Claude im Tab „Fragen“, was sie tut und wann du sie spielst.
    **Deck-Check (Ampel):** Länder (Richtwert abhängig von Kurve und Ramp), Ramp, Kartenzug, Removal, Board
    Wipes, Manakurve und Siegbedingungen – je grün/gelb/rot mit „Warum wichtig?“. „Karten vorschlagen“ zeigt
    beliebte Karten dieser Rolle in deinen Farben (ohne KI) und merkt sie im Bearbeiten-Modus vor;
    „Upgrades mit KI“ trägt den passenden Fokus bei den Upgrade-Vorschlägen ein.
    **Bearbeiten** (ohne Claude): Karten hinzufügen, Anzahl ±, entfernen, Kategorie ändern und über ⇄
    „Ähnliche Karten“ (gleiche Rolle, passende Farben) tauschen – gespeichert wird alles zusammen als neue,
    geprüfte Version.
  - **Anleitung:** der **Rule-0-Text** für die Runde (Stufe, Spielweise, Tempo, Game Changer, Combos,
    Tutoren, Extra-Züge, Massen-Landzerstörung, Proxys, Hausregeln – ohne KI aus der Prüfung; kopieren oder
    „Am Tisch zeigen“ im Vollbild) und eine **Deck-Anleitung** von Claude: Spielplan, früh/mitte/spät,
    Starthand behalten?, Schlüsselkarten, Siegwege, worauf achten, Tipps. Sie wird im Deck gespeichert
    (mit der Version, für die sie gilt). „Drucken / als PDF“ ergibt einen einseitigen Spickzettel.
  - **Testen:** Starthand ziehen, London-Mulligan (der erste ist in Commander frei), Zug für Zug nachziehen,
    auf dem Play oder Draw; dazu exakte Wahrscheinlichkeiten: Länder in der Starthand, Landdrops bis Zug 5,
    Ramp bis Zug 2, Kartenzug bis Zug 3, Interaktion bis Zug 4.
  - **Anpassen:** oben die Tischregel, darunter eine Frage „Was willst du ändern?“ mit vier Antworten – es
    ist immer nur der gewählte Bereich offen (die Wahl bleibt gespeichert; ein Deck mit offenem Upgrade-Plan
    öffnet direkt den Plan): „Mit eigenen Worten ändern“ (Freitext + Schnellwahl), **Upgrade-Vorschläge** (Budget und
    Fokus angeben → Claude liefert Tausche mit Preis und Grund, Karten aus der Sammlung zählen als kostenlos;
    ankreuzen und übernehmen, ohne weiteren KI-Lauf), der **Upgrade-Plan in Stufen** (z. B. 20 € · 50 € · 100 €
    insgesamt; jede Stufe mit Thema, Tauschen und Kosten, Stufe für Stufe übernehmen – ideal für
    Starterdecks) und „Stärke ändern“ (Retune).
  - **Fragen:** Fragen in natürlicher Sprache – Strategie, Mulligan, ist es zu stark für meine Runde, wie
    schlägt es sich gegen Commander XY (Claude holt sich dessen typisches Deck von EDHREC), Schwächen,
    warum ist Karte X drin. Anschlussfragen („und gegen Kinnan?“) kennen den bisherigen Verlauf.
    Claude darf dabei nur lesen (kein Speichern); Fragen und Antworten liegen pro Deck in
    `decks/.questions/<slug>.json`, Kartennamen in Antworten zeigen beim Hovern das Bild.
  - **Partien:** nach dem Spiel festhalten – Ergebnis, Gegner-Commander, letzter Zug, was schiefging
    (Schnellwahl wie „zu wenige Länder“, „kein Weg zu gewinnen“), beste Karte, Notiz. Die Bilanz zeigt
    Siegquote, Ø Zug, häufigste Probleme, Ergebnis pro Version, beste Karten und Gegner.
    „Aus den Partien lernen“ trägt die häufigsten Probleme als Fokus für Upgrade-Vorschläge ein; Fragen und
    Upgrades kennen die Partien auch (`deck_games`). Gespeichert in `decks/.games/<slug>.json`.
    Zu jedem Gegner gibt es ein Feld „Aufgefallen“; bekannte Gegnerdecks wählst du per Klick aus,
    neue Commander werden automatisch als Gegnerdeck gemerkt (abschaltbar).
  - **Verlauf:** Versionen vergleichen, wiederherstellen, als neues Deck kopieren. Darunter (und im ⋯-Menü)
    **„Mit anderem Deck vergleichen“**: zeigt, welche Karten dieses Deck zusätzlich braucht, welche frei werden und
    welche gemeinsam sind – vorausgewählt ist das Deck, von dem es kopiert wurde, sonst das mit den meisten
    gemeinsamen Karten. Mit Sammlung lässt sich „Nur was mir fehlt“ wählen; einzelne Karten abwählen, die
    Auswahl als Liste kopieren oder direkt **in eine Sammelbestellung** packen – ideal nach einem Umbau als neues Deck.
  - **Drucken:** oben Bildquelle, Kartenstärke, „Tokens mitdrucken“ und „Nur fehlende Karten“, darunter die
    Schritte 1 Druckdateien vorbereiten · 2 PDF · 3 MakePlayingCards · 4 gedruckte Karten in die Sammlung
    übernehmen. „Bilder prüfen und tauschen“ ist darunter eingeklappt (öffnet sich von selbst, wenn Bilder fehlen).
    Mit „Tokens mitdrucken“ erscheint eine Liste aller Tokens, Embleme und Marker des Decks – jeweils mit
    Artwork (Klick wählt ein anderes) und eigener Anzahl (−/+ oder eintippen, 0 = nicht drucken), z. B.
    10 Humans, 5 Treasures, 1 Monarch. „Standard je N“ gilt für Tokens ohne eigene Anzahl; Embleme und Marker
    bekommen standardmäßig 1. Die Anzahlen merkt sich das Deck (`proxies/<deck>/tokens.json`, Teil der
    Sicherung); „↺ Standard“ setzt eine zurück. In Sammelbestellungen stehen die Anzahlen in den Positionen.
    Im Bildwähler einer Karte (Klick auf die Karte) gibt es neben MPC-Autofill- und Scryfall-Bildern
    **„Eigenes Bild hochladen …“** (oder ein Bild in den Dialog ziehen): JPG, PNG, WebP, TIFF oder BMP. Die App macht
    es druckfertig – Beschnittrand automatisch erkennen (MPC-Vorlage 2,74 × 3,74 Zoll) oder ergänzen, Bilder ohne
    Kartenformat mittig auf 63 × 88 mm zuschneiden, 600 DPI ab etwa 1400 px Breite, sonst 300 DPI – und wählt es
    gleich aus; bei zu niedriger Auflösung gibt es eine Warnung. Eigene Bilder stehen danach oben im Bildwähler
    (mit ✕ zum Löschen), liegen in `proxies/<deck>/uploads/` und sind Teil der Sicherung.
  - „Liste kopieren“ steht oben rechts; im ⋯-Menü: „Als neues Deck kopieren“, „Neu prüfen“, Export für
    Cockatrice (.cod) und Tabletop Simulator (.json), Decklist als Textdatei, „Löschen“.
- **Meine Sammlung** (eigene Seite, siehe unten), **Glossar** (Schlüsselwörter, Aktionen und
  Commander-Begriffe in einem Satz, mit Suche), **Blacklist** und **Einstellungen** (Kartendatenbank,
  Proxy-Druck, KI-Hochskalierung) als eigene Seiten.
- Durchgehend Deutsch: Kategorien heißen in der Oberfläche Kartenzug, Schutz, Siegbedingung, Länder … (gespeichert
  bleiben die englischen Werte), Zahlen und Preise im deutschen Format (32,50 €, Ø Manawert 3,1).
- **Schnellsuche Strg+K** (⌘K): Decks, Seiten, Deck-Tabs, Glossar-Begriffe und Aktionen wie „Sammlung
  importieren“, „Deck per Link importieren“ oder „Partie festhalten“.
- Hell/Dunkel folgt dem Betriebssystem; bedienbar mit Tastatur (Pfeiltasten in den Tabs, Esc schließt
  Dialoge und Menüs).
- Die GUI nutzt deine Claude-Code-Anmeldung; Kosten fallen wie bei einer normalen Claude-Code-Sitzung an.

## Sammelbestellungen

Mehrere Dinge auf einmal drucken – wie ein Deck, nur als Sammlung: z. B. 18 neue Karten für Aesi, 20 aus einem
anderen Deck und 50 Treasure-Tokens, zusammen in **einer** MakePlayingCards-Bestellung oder einem PDF.

- **Hinzufügen** auf der Seite „Sammelbestellungen“:
  - **Karte** (Suche mit Autovervollständigung, auch deutsche Namen) mit Anzahl;
  - **Aus einem Deck:** nur fehlende (was die Sammlung nicht abdeckt), alle oder ausgewählte Karten;
  - **Tokens:** Scryfall-Suche („Treasure“, „Zombie“ …) oder die Tokens eines Decks, „je N“ Stück;
  - **Liste / Link:** eingefügte Liste („1 Craterhoof Behemoth“) oder ein Deck-Link (Moxfield, Archidekt,
    MTGGoldfish, TappedOut, Deckstats, EDHREC).
- **Aus der Kartenansicht:** Klick auf eine Karte (oder einen Token unter „Tokens & Marker“) → „Zur
  Sammelbestellung“ neben dem Scryfall-Link, mit Anzahl.
- **Umbau bestellen:** im Deck „Mit anderem Deck vergleichen“ → den Unterschied zum alten Deck hinzufügen.
- **Von überall:** Upgrade-Vorschläge („Zur Sammelbestellung“ – drucken, ohne sie schon ins Deck zu übernehmen),
  eine Stufe des Upgrade-Plans, das ⋯-Menü eines Decks und Claude (`update_print_order`).
- **Nach jedem Umbau** eines Decks (Claude überarbeitet oder ändert die Stärke, manuelles Bearbeiten, Upgrades
  oder eine Plan-Stufe übernommen) fragt die App, ob die **neu hinzugekommenen Karten** in eine Sammelbestellung
  sollen – auf Wunsch nur die, die in der Sammlung fehlen.
- **Inhalt** gruppiert nach Herkunft (Deck, Upgrades, Liste, Tokens …), Anzahl änderbar, einzelne Positionen oder
  ganze Gruppen entfernen; oben stehen Karten, Tokens, Druckplätze und die MPC-Staffel (18, 36, 55, 72 …).
- **Als Moxfield-Liste:** „Als Moxfield-Liste kopieren“ oder „.txt herunterladen“ – alle Karten zusammengezählt
  („3 Sol Ring“), passend für den Import bei Moxfield, Archidekt oder ManaBox. Tokens sind nicht dabei
  (Moxfield importiert keine); die App sagt, wie viele ausgelassen wurden.
- **Drucken** wie bei einem Deck – derselbe Bereich mit Bildauswahl (MPC Autofill/Scryfall, eigene Wahl pro
  Karte), KI-Hochskalierung, Druckdateien, PDF, MakePlayingCards und „In die Sammlung übernehmen“. Tokens ohne
  gewähltes Bild bekommen das neueste passende Scryfall-Token.

## Deskmat-Studio

Eine **Druckdatei** für Deskmat oder Playmat in echter Druckauflösung: **300 DPI** (Minimum) oder **600 DPI**
(beste Qualität), bezogen auf die Mattengröße – z. B. Playmat 61 × 35,5 cm = 7205 × 4193 px bei 300 DPI bzw.
14409 × 8386 px bei 600 DPI. Die DPI stehen in der Datei.

1. **Motiv** – drei Wege:
   - **Kartenartwork:** das reine Artwork (Scryfall „art crop“, ohne Rahmen und Text) in jedem Druck, auch Rückseiten;
     mit eingetragenem MPC-Autofill-Server auch hochaufgelöste Scans (automatisch auf die Artwork-Box zugeschnitten).
   - **Setting beschreiben:** Claude schreibt aus deiner Beschreibung (plus Stil und optional der Stimmung eines
     Decks) einen Bild-Prompt; der kostenlose Generator pollinations.ai malt 1–4 Varianten, du wählst eine.
     Der Prompt wird dorthin geschickt. In den Einstellungen lässt sich ein anderer Generator eintragen (URL mit
     `{prompt}`, `{width}`, `{height}`, `{seed}`), etwa ein eigener lokaler.
   - **Eigenes Bild** hochladen.
2. **Format & Zuschnitt:** Playmat 61 × 35,5 cm, Deskmat 80 × 30 / 90 × 40 / 120 × 60 cm; 300 oder 600 DPI;
   optional **Beschnitt (Bleed)** 3 oder 5 mm je Seite – die Schnittkante ist in der Vorschau gestrichelt;
   PNG (verlustfrei) oder JPEG 95 % (viel kleiner bei 600 DPI). Den Ausschnitt ziehst du mit Maus oder Finger
   zurecht, Zoom per Regler, Mausrad oder +/−. „Einpassen“ zeigt das ganze Bild und füllt die Ränder mit einer
   unscharfen, abgedunkelten Erweiterung. Angezeigt werden Pixel, Megapixel und der Vergrößerungsfaktor.
   Über 250 Megapixel (120 × 60 cm bei 600 DPI) ist gesperrt – das passt nicht sinnvoll in den Speicher.
3. **Deskmat erstellen:** Karten-Scans werden entrastert, dann skaliert **Real-ESRGAN ×4** hoch (dasselbe
   Programm wie im Druckstudio, siehe Einstellungen). Wahlweise folgt bei großen Faktoren (über ~4,5) ein
   **zweiter KI-Durchgang**: das Zwischenbild wird exakt auf ein Viertel der Zielgröße gebracht und nochmals ×4
   gerechnet – so landet es genau auf der Druckgröße. Den Rest erledigt Lanczos mit leichtem Nachschärfen.
   Ohne Real-ESRGAN wird ohne KI vergrößert (mit Hinweis). Alles liegt in `deskmats/<id>/`.

4. **Vor dem Bestellen prüfen:** Klick ins fertige Bild wählt eine Stelle mit vielen Details (Gesichter, Türme,
   Reiter).
   - **Details vergleichen** rechnet dort einen Ausschnitt in Druckauflösung ohne KI, mit einem und mit zwei
     KI-Durchgängen – nebeneinander, auf Wunsch in Originalpixeln; „Damit erstellen“ übernimmt die Variante.
     Standard ist **ein** Durchgang (wirkt oft natürlicher), zwei sind schärfer, können aber künstlich wirken.
   - **Probedruck als PDF:** der gewählte Ausschnitt in Originalgröße (27 × 19 cm auf A4 quer, Letter
     entsprechend) mit Schnittmarken. Mit 100 % („Tatsächliche Größe“) drucken, auf den Tisch legen und aus
     Spielabstand (50–60 cm) ansehen – sieht es da gut aus, bestellen.

Erreichbar über die Seitenleiste, Strg+K oder im Deck über ⋯ → „Deskmat aus diesem Deck“ (Commander vorausgefüllt).
Hinweis: Die Datei hat immer die gewählten DPI – wie viel echtes Detail drinsteckt, hängt vom Motiv ab.
Scryfall-Artworks sind klein (oft ~600 px breit), für 300 DPI auf 61 cm ist das ein Faktor um 12. Deutlich
schärfer werden MPC-Scans, generierte oder eigene große Bilder; bei zu kleinen Motiven warnt die Seite.
Die zwei KI-Durchgänge bei 600 DPI brauchen eine Weile und eine Grafikkarte mit genug Speicher.

## Meine Sammlung

Die Seite „Meine Sammlung“ merkt sich, welche Karten du hast (`collection.json`, lokal):

- pro Eintrag **Anzahl**, **echt oder Proxy**, Foil, Sprache und das **Artwork** (der konkrete Druck –
  über „Artwork wählen“ aus allen Scryfall-Drucken, mit Bild, Set, Nummer und Preis)
- **Import** aus ManaBox, Moxfield (inkl. Proxy-Spalte) und Archidekt als CSV oder als Liste
  („2 Sol Ring (C21) 263 *F*“, „1 Demonic Tutor [proxy]“); **Export** als CSV
- Suche, Filter echt/Proxy, Sortierung, Liste oder Bilder, Anzahl ±, Proxy/Foil umschalten, in welchen
  Decks eine Karte steckt
- **Im Deck:** ✓ = hast du, P = als Proxy, „2/3“ = teilweise; Panel „Sammlung“ mit „X von 100 vorhanden“,
  Preis der fehlenden Karten, **Einkaufsliste** (für Cardmarket-Wants) und Warnung, wenn mehrere Decks
  dieselbe Karte brauchen, du aber zu wenige hast. Basic Lands zählen als vorhanden.
- **Beim Bauen:** „Karten aus meiner Sammlung bevorzugen“ – Claude nimmt passende Karten aus deiner
  Sammlung, beim Budget zählen nur fehlende.
- **Beim Drucken:** „Nur fehlende Karten“ druckt nur, was dir fehlt; danach trägt „Zur Sammlung
  hinzufügen“ die gedruckten Proxies samt Artwork ein.

## Datenquellen & Zugänge

Für keine der Quellen ist ein API-Key oder Account nötig.

| Quelle | Zugang | Wofür |
|---|---|---|
| **Scryfall API** | offizielle REST-API, kein Key (User-Agent + Rate-Limits werden eingehalten) | Kartensuche, Preise, Legalität, Game Changer, Autocomplete |
| **Scryfall Bulk Data** | tägliche `jsonl.gz`-Exporte über `/bulk-data` | lokale DB: **All Cards** (alle Drucke, alle Sprachen) + **Oracle Tags** (Tagger) |
| **EDHREC** | öffentliche JSON-Dateien (`json.edhrec.com`), inoffiziell | Empfehlungen, Synergie, Themen, Average Deck – auch gefiltert nach Bracket und Budget |
| **Commander Spellbook** | offizielle Backend-API, kein Key | Combos im Deck / fast im Deck, Bracket-Schätzung |
| **Archidekt** | öffentliche Deck-API, kein Key | Decks importieren (inkl. Kategorien) |
| **Moxfield** | *keine* öffentliche API (Cloudflare, User-Agent-Whitelist) | Import nur „best effort“ – sonst Text-Export einfügen |
| **MTGGoldfish / TappedOut / Deckstats** | öffentliche Text-Exporte der Deckseiten | Decks importieren |
| **pollinations.ai** | kostenloser Bildgenerator, kein Key (austauschbar in den Einstellungen) | Deskmat-Motive aus einem Setting |
| **MTGJSON** | öffentliche JSON-Dateien (`DeckList.json`, `decks/<Datei>.json`), kein Key | Starterdecks (Precons) |

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
| `edit_deck` / `similar_cards` | gezielte Tausche ohne die ganze Liste neu zu schicken; Ersatzkarten für eine Karte |
| `collection_search` / `collection_status` / `update_collection` | Sammlung durchsuchen, Deck mit Sammlung abgleichen, Karten eintragen |
| `import_deck` | Deck-Link importieren (Archidekt, Moxfield, MTGGoldfish, TappedOut, Deckstats, EDHREC) |
| `search_precons` / `import_precon` | Starterdecks (Precons) von MTGJSON suchen / als Deck speichern |
| `print_orders` / `update_print_order` | Sammelbestellungen lesen / Karten, Tokens oder ein Deck hinzufügen |
| `create_deskmat` | Deskmat/Playmat-Druckdatei (300/600 DPI, optional Beschnitt) aus einem Kartenartwork oder einem Bild-Prompt |
| `compare_decks` | zwei Decks vergleichen (z. B. Umbau gegen altes Deck): neue, frei werdende und gemeinsame Karten, mit Sammlungs-Abgleich; `update_print_order(from_deck=, compare_with=)` bestellt den Unterschied |
| `deck_games` | festgehaltene Partien eines Decks mit Bilanz und häufigsten Problemen |
| `app_overview` | alles auf einen Blick: alle Decks mit Stufe, Power, Legalität, Preis, Deck-Check, Bilanz, fehlenden Karten, dazu Gegnerdecks, Tischregeln, Sammlung |
| `opponent_decks` / `update_opponent_deck` | Gegnerdecks mit Merkmalen, Beobachtungen und Bilanz lesen / anlegen, Beobachtung notieren, löschen |
| `table_rules` / `update_table_rule` | Tischregeln lesen / anlegen, ändern, löschen; `validate_deck` und `save_deck` nehmen `table_rule` |
| `get_blacklist` / `update_blacklist` | Blacklist lesen / Karten und Begriffe (True Duals, Günstige Tutoren, teurer als 20 € …) hinzufügen oder entfernen |
| `list_deck_versions` / `compare_deck_versions` | Versionsverlauf, Diff zwischen Versionen |
| `restore_deck_version` / `copy_deck` | alte Version wiederherstellen, Deck (oder alte Version) kopieren |
| `create_proxy_order` / `export_proxy_pdf` | Proxy-Druckdateien (MPC-Autofill-XML + Bilder, optional mit Tokens: `tokens` = Standard-Anzahl, `token_counts` = Anzahl pro Token wie `{"Human": 10}`), PDF zum Selbstdrucken |
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
| `MTG_TABLERULES_FILE` | `./tablerules.json` | Tischregeln |
| `MTG_BACKUP_DIR` | `./backups` | Sicherungen (ZIP) |
| `MTG_COLLECTION_FILE` | `./collection.json` | deine Sammlung |
| `MTG_PROXIES_DIR` | `./proxies` | Druckdateien (XML, Bilder-Verweise, PDF) |
| `MTG_DESKMAT_DIR` | `./deskmats` | Deskmat-Projekte und fertige Dateien |
| `MTG_SYNC_DIR` | `./.sync` | Sync-Zustand dieses Geräts (Verbindung, Stand, Basisdateien, Konfliktprotokoll) |
| `MTG_SYNC_AUTO` | `1` | `0` = kein automatischer Abgleich im Hintergrund (nur per Knopf) |
| `MTG_SYNC_DATA` / `MTG_SYNC_PUBLIC_URL` / `MTG_SYNC_HOST` / `MTG_SYNC_PORT` | `./sync-data` / – / `0.0.0.0` / `8080` | nur der Sync-Server (`mtg-sync-server`) |
| `MTG_AUTOFILL_PATH` / `MTG_MPCFILL_SERVER` / `MTG_CARDBACK` / `MTG_UPSCALER_PATH` | – | überschreiben die Proxy-Einstellungen (`mtgdeck.settings.json`) |
| `MTG_GUI_HOST` / `MTG_GUI_PORT` | `127.0.0.1` / `8765` | GUI-Adresse |
| `MTG_MAX_TURNS` | `120` | max. Agent-Schritte pro GUI-Auftrag |

## Grenzen

- EDHREC hat keine offizielle API; ändert sich das JSON-Format, liefern die EDHREC-Tools Fehler, der Rest funktioniert weiter.
- Die Bracket-Prüfung deckt die harten Regeln ab (Game Changer, MLD, Extra Turns, 2-Karten-Combos).
  „Wie schnell/konsistent ist das Deck?“ bleibt eine Einschätzung – Claude begründet sie in der Deckbeschreibung.
- Die Rollen-Zählung (Ramp, Draw, Removal …) ist heuristisch (Tagger-Tags bzw. Textmuster) – das gilt auch
  für den Deck-Check.
- Moxfield, MTGGoldfish, TappedOut, Deckstats und MTGJSON bieten keine offiziell dokumentierte Deck-API;
  ändern sie ihre Exporte, schlägt der Link-Import fehl – eine eingefügte Liste geht immer.
- Deutsche Kartentexte gibt es nur für Karten mit deutscher Ausgabe (sonst der englische Oracle-Text); bei
  alten Drucken kann der gedruckte Text vom aktuellen Oracle-Text abweichen.

## Entwicklung

```bash
uv sync --all-extras
uv run pytest          # läuft komplett offline (HTTP wird gemockt)
```

Struktur: `src/mtgdeck/` (Python-Paket), `.claude/skills/` (Skills `commander-deckbuilder`, `commander-finder`),
`.mcp.json` (MCP-Registrierung), `tests/`.
