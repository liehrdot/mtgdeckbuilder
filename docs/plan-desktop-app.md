# Sub-Plan: Windows-Desktop-App (Phase 5) und Aufräumen

Stand: Oktober 2026. Ziel: Der Deckbuilder startet wie ein Programm (Symbol, Fenster, Installer, Updates), fühlt
sich auf Windows zu Hause und bleibt dabei schlank. Vorher räumen wir auf: Was zu viel ist, fliegt raus.

## Was die Recherche hergibt

### Weniger ist besser, aber erst nach dem Kauf

Vor dem Kauf wählen zwei Drittel das Produkt mit den meisten Funktionen. Nach dem Benutzen bevorzugt die Mehrheit das
einfachere („Feature Fatigue“, [Smith School 2006](https://www.rhsmith.umd.edu/news/feature-fatigue-research-proves-simpler-products-are-better-manufacturers-advised-lose-extra),
Zusammenfassung von [Surowiecki](https://boingboing.net/2007/05/24/james-surowiecki-on.html)). Neuere Arbeit trennt
die Zahl der Funktionen von ihrem Zusammenspiel: Mehr Funktionen heißt „kann viel“, aber auch „ist schwer zu
bedienen“ ([Hoyer u. a.](https://news.mccombs.utexas.edu/?p=5059)). Für uns heißt das: Jede Funktion muss eine
Aufgabe lösen, die der Nutzer wirklich hat, sonst kostet sie Aufmerksamkeit auf jedem Bildschirm, in jedem Menü und in
den Einstellungen.

### Was an beliebten Desktop-Apps geschätzt wird

| App | Was Leute daran lieben | Quelle |
|---|---|---|
| Raycast | „Fast. Think in milliseconds. Keyboard first.“ Ein Muster, das sich überall wiederholt, sodass Tastaturbedienung zur Gewohnheit wird | [raycast.com](https://www.raycast.com/), [Erfahrungsbericht](https://rmoff.net/2025/12/18/a-love-letter-to-raycast/) |
| Linear | bleibt im Hintergrund, niemand braucht eine Schulung; schnell, tastaturgetrieben, mit klarer Meinung zum Ablauf | [Linear/Raycast](https://linear.app/customers/raycast), [Übersicht 2026](https://guptadeepak.com/tools/top-5-developer-productivity-tools-2026/) |
| Things 3 | Einfachheit durch Grenzen: wenige, klare Begriffe statt Dutzender Optionen; tastaturfreundlich | [Design-Analyse](https://blakecrosley.com/guides/design/things) |
| Obsidian | lokale Dateien, kein Zwang; eine ruhige Oberfläche, die „nicht drängt, sondern wartet“ | [Analyse](https://blakecrosley.com/guides/design/obsidian), [Essay](https://brajeshwar.com/2025/obsidian/) |

Die Stärke dieser Apps liegt in wenigen Dingen, die sich zu 100 % verlässlich und schnell anfühlen. Nicht darin,
alles zu können.

### Was Windows 11 von einer App erwartet

Microsofts Liste für gute Windows-11-Apps ([Best Practices](https://learn.microsoft.com/windows/apps/get-started/best-practices),
[Top 11](https://learn.microsoft.com/pl-pl/windows/apps/get-started/make-apps-great-for-windows),
[Titelleiste](https://learn.microsoft.com/windows/apps/design/controls/title-bar)):
- Titelleiste mit Symbol und Namen, Fenster lässt sich am ganzen Kopf ziehen, Snap-Layouts und runde Ecken kommen
  von selbst;
- helles und dunkles Design folgen dem System;
- alles geht mit Tastatur, Maus und Touch; feste, übliche Tastenkürzel (NN/g: wenige Modifikatoren, bekannte
  Belegungen nicht umwidmen, [NN/g](https://www.nngroup.com/articles/ui-copy/));
- Benachrichtigungen nur, wenn sie etwas bringen; Einstellungen an einem Ort.

### Befehlspalette und Tastenkürzel

Eine Befehlspalette (Strg+K) lohnt sich bei aktionsreichen Apps: ein Ort für Befehle, Ziele und Zuletzt-Benutztes,
mit sichtbaren Kürzeln und Favoriten vorn ([Muster](https://uxpatterns.dev/patterns/advanced/command-palette),
[Design-Bootcamp](https://medium.com/design-bootcamp/command-palette-ux-patterns-1-d6b6e68f30c1)). Eine Liste aller
Befehle speist zugleich Menü, Palette und Kürzelübersicht, damit nichts auseinanderläuft. Der Deckbuilder hat die
Palette schon (Strg+K). Es fehlen die Kürzel für die häufigsten Wege und eine Übersicht dazu.

### Start und Wartezeiten

Ein Skelett des Bildschirms fühlt sich schneller an als ein Ladekreisel, bei gleicher Dauer
([VirtusLab](https://virtuslab.com/blog/software-development/ux-patterns-beyond-raw-performance)). Ein Startbild soll
unter zwei Sekunden bleiben und nichts künstlich verzögern ([UXPin](https://www.uxpin.com/studio/blog/splash-screen/)).
Für uns: Das Fenster erscheint sofort mit dem Gerüst der Oberfläche und füllt sich, sobald das Backend bereit ist.

### Technik: Tauri 2 mit Python-Backend

- Das Backend läuft als **Sidecar** (mitgeliefertes Programm). Tauri startet es, wartet auf `/api/health` und beendet
  es beim Schließen ([Beispiel FastAPI-Sidecar](https://deepwiki.com/dieharders/example-tauri-v2-python-server-sidecar)).
  Ein Windows-Projekt zeigt, worauf es ankommt: Neustart mit Backoff nach einem Absturz, Token für den lokalen Zugriff,
  Port 0 statt fester Port, eigene Konsole vermeiden, beim Update das Sidecar beenden
  ([tauri-python-sidecar](https://github.com/matshoppenbrouwers/tauri-python-sidecar)).
- **PyInstaller im Ordner-Modus** (`--onedir`): startet deutlich schneller als `--onefile` und vermeidet das Entpacken
  nach `%TEMP%`, das Virenscanner misstrauisch macht ([Diskussion](https://discuss.python.org/t/opinion-pyinstaller-onefile-or-onedir-for-program-distribution/106137),
  [Fehlalarme](https://www.codegenes.net/blog/program-made-with-pyinstaller-now-seen-as-a-trojan-horse-by-avg/)).
  Signieren bleibt die sichere Lösung gegen Fehlalarme. Ohne Zertifikat testen wir den Build gegen Defender und
  VirusTotal.
- **Ein Fenster, eine Instanz:** Das Plugin `single-instance` holt beim zweiten Start das vorhandene Fenster nach vorn
  ([Tauri](https://v2.tauri.app/plugin/single-instance/)).
- **Updates:** Das Plugin `updater` liest `latest.json` aus den GitHub-Releases, prüft die Signatur und installiert
  den NSIS-Installer im Modus `passive` ([Tauri](https://v2.tauri.app/plugin/updater/)). Tag, Manifest und Version
  müssen zusammenpassen, sonst kommen Updates still nie an.
- **Infobereich (Tray):** Fenster schließen heißt „in den Infobereich“, damit der PC Fragen vom Handy weiter
  beantwortet; dort steht der Sync-Stand. Beenden gibt es nur bewusst (Tray-Menü, Strg+Q).

## Zehn Regeln für die Desktop-App

1. **Sofort da.** Fenster in unter einer Sekunde, Gerüst zuerst, dann die Daten. Kein Startbild, kein Ladekreisel.
2. **Ein Weg pro Aufgabe.** Für Fragen an Claude gibt es einen Ort, für Upgrades einen, für Regeln einen.
   Doppelte Wege werden zusammengelegt.
3. **Tastatur für alles Häufige.** Strg+K Palette, Strg+N neues Deck, Strg+F suchen, Strg+, Einstellungen, Esc
   schließt. Kürzel stehen in der Palette und in einer Übersicht (`?`).
4. **Fühlt sich nach Windows an.** Systemtitelleiste mit App-Symbol, Design folgt dem System, Fenstergröße und
   -position werden gemerkt, Snap-Layouts funktionieren.
5. **Nie im Weg.** Benachrichtigung nur, wenn das Fenster nicht sichtbar ist und etwas fertig wurde (Deck gebaut,
   Antwort da). Keine Hinweise beim Start.
6. **Immer erreichbar, nie aufdringlich.** Im Infobereich lebt die App weiter (Sync, Fragen vom Handy). Die
   Tray-Spitze zeigt den Stand; ein Klick öffnet das Fenster.
7. **Aktualisiert sich selbst.** Prüft beim Start, meldet leise „Neue Version – beim nächsten Start“ und installiert
   nach Bestätigung. Nichts zwingt.
8. **Lokal zuerst.** Alle Daten liegen in `%APPDATA%\MTG Deckbuilder` als lesbare Dateien. Ohne Netz geht alles,
   was keine Kartensuche braucht.
9. **Ein Installer, kein Setup-Marathon.** Doppelklick, fertig. Claude Code wird erkannt oder freundlich erklärt.
   Externe Programme (MPC Autofill) bleiben optional.
10. **Weniger Oberfläche.** Jede Seite, jeder Tab, jede Einstellung muss ihren Platz verdienen. Siehe „Aufräumen“.

## Aufräumen: Was zu viel ist

Bestandsaufnahme nach Code und Oberfläche (Stand heute: 12 Seiten, 8 Deck-Tabs mit 4 Unterwegen im Tab „Anpassen“,
8 Einstellungsbereiche, ~5.200 Zeilen Oberflächen-JavaScript).

| Funktion | Umfang | Einschätzung | Empfehlung |
|---|---|---|---|
| **Deskmat-Studio** | 620 Zeilen Backend, ~150 Stellen in der Oberfläche, eigene Seite, Einstellungsbereich, Bildgenerator, MCP-Tool, Sync-Regel | Ein zweites Produkt in der App. Ein Deskmat macht man einmal im Jahr – dafür ein eigener Bildgenerator, Zuschnitt, Testdruck | **raus** |
| **KI-Hochskalierung + Entrasterung** der Proxy-Scans | ~130 Stellen, braucht Real-ESRGAN mit Vulkan-GPU, eigener Einstellungsbereich | Feintuning für wenige; MPC druckt die Scryfall-Scans in guter Qualität. Der Aufwand (Cache-Versionen, Fallbacks, Warnungen) ist größer als der Nutzen | **raus** |
| **Terminal im Browser** für MPC Autofill (xterm.js, Pseudo-Terminal, pywinpty) | 300 KB Vendor-Code, eigene Tastatur-/Resize-Routen, zwei plattformabhängige Pakete | Das Tool kann in einem eigenen Konsolenfenster laufen – das gibt es schon als Ausweg. In einer Desktop-App ist das der natürliche Weg | **raus**, Tool startet im eigenen Fenster |
| **Exporte Cockatrice / Tabletop Simulator** | 76 Zeilen, ein Menüpunkt | klein, aber für Papier- und Proxyspieler ohne Nutzen | optional raus |
| **„Fragen zum Deck“** (Tab im Deck) neben **„Frag Claude“** | Tab, Route, Prompt, eigene Speicherdatei, Sync-Regel | Zwei Wege für dieselbe Aufgabe. „Frag Claude“ kennt alle Decks; vom Deck aus reicht ein Knopf, der das Gespräch mit dem Deck vorbelegt – so macht es das Handy schon | **zusammenlegen** |
| Upgrade-Vorschläge und Upgrade-Plan in Stufen | zwei von vier Unterwegen im Tab „Anpassen“ | Verwandt, aber verschieden: einmal tauschen oder über Monate in Stufen. Beide bleiben, der Plan wird im Text klarer als „über mehrere Käufe“ erklärt | bleibt |
| Blacklist, Tischregeln, Hausregeln im Power-Profil | drei Regelsysteme | Überschneidung bei „max. Game Changer / Tutoren“. Zusammenlegen wäre richtig, greift aber tief in Skill, Prompts und Validierung | später, eigenes Thema |
| Glossar, Testhand, Rule 0, Anleitung | klein | Einsteigerhilfen, die beim Spielen helfen | bleibt |
| Sammelbestellungen, Deckvergleich, eigene Druckbilder | mittel | lösen echte Aufgaben beim Proxydruck für eine Runde | bleibt |

Nach dem Aufräumen: 11 Seiten, 7 Deck-Tabs, 6 Einstellungsbereiche, die Seitenleisten-Gruppe „Werkstatt“ schrumpft
auf „Sammelbestellungen“ und wandert zu „Meine Sammlung“.

Beim Entfernen gilt: Daten gehen nicht verloren. Alte Deck-Fragen werden einmalig in ein Gespräch „Fragen zu
<Deck>“ unter „Frag Claude“ übernommen. Deskmat-Ordner und hochskalierte Cache-Dateien bleiben auf der Platte, die
App rührt sie nicht mehr an.

## Aufbau der Desktop-App

```
desktop/                      Tauri-2-Projekt (Rust-Hülle, rund 450 Zeilen)
  src-tauri/
    tauri.conf.json           Fenster, Bundle (NSIS, Backend als Ressource), Capabilities
    src/lib.rs                Fenster, Tray, Single Instance, Fensterzustand, Autostart, --selftest
    src/backend.rs            Backend starten, überwachen, neu starten, Protokoll
    icons/
  ui/index.html               lokale Startseite (Gerüst), springt zur Adresse des Backends
  sidecar/                    PyInstaller-Spec: mtgdeck-backend als Ordner-Build, agent/ mit Skills
  agent/CLAUDE.md             Anweisungen für Claude innerhalb der App
```

- **Start:** Tauri startet `mtgdeck-backend.exe --data … --cache … --port 0 --token auto` (Port 0 bindet einen
  freien Port, der Token schützt die lokale API; das Backend meldet `MTGDECK_URL=…` auf stdout). Das Fenster zeigt
  die lokale Startseite mit Gerüst und springt zur gemeldeten Adresse; der erste Aufruf mit `?token=` setzt das
  Cookie. Stirbt das Backend, startet die Hülle es nach 1, 2, 5, 5, 5 s neu; danach zeigt die Startseite das Ende
  des Protokolls und „Nochmal versuchen“. Ein langer gesunder Lauf setzt den Zähler zurück.
- **Daten:** `--data` = `%APPDATA%\MTG Deckbuilder` (Decks, Sammlung, Einstellungen, Sync-Zustand), Cache
  `%LOCALAPPDATA%\MTG Deckbuilder\cache`, Protokoll `<data>\logs\backend.log` (plus der vorige Lauf als
  `backend.1.log`). Vorhandene Daten kommen per Sync oder über „Sicherung hochladen“ in die App; `MTG_HOME`
  in der Umgebung überstimmt den Ordner.
- **Claude:** Der Agent-SDK bringt die CLI mit (das Windows-Wheel erst ab 0.2.164, deshalb die Mindestversion in
  `pyproject.toml`); `paths.bundled_cli()` findet sie im Paket. `GET /api/ai` fragt `claude auth status` (gecacht);
  ist niemand angemeldet, sagt die Oberfläche das und bietet „Bei Claude anmelden“: `claude auth login` läuft im
  Terminal der App (Pseudo-Terminal wie bei MPC Autofill), der Browser öffnet sich zum Bestätigen.
- **Tray:** Menü Öffnen, Datenordner öffnen, „Beim Anmelden starten“ (Plugin `autostart`, Start mit `--hidden` nur
  in den Tray), Beenden; Linksklick öffnet das Fenster. Fenster schließen versteckt es, beim ersten Mal mit einem
  Hinweis. Der Tooltip zeigt den Zustand (laufende Aufträge, Sync, Frage vom Handy). Kein „Jetzt abgleichen“ im
  Menü – der Sync läuft von selbst, der Knopf steht in den Einstellungen.
- **Brücke:** Die Hülle fragt alle 10 s `POST /api/desktop/poll` (mit dem Token) und bekommt Tooltip, neue
  Hinweise (bei verstecktem Fenster als Systembenachrichtigung: Deck fertig, Antwort von Claude, Neues von anderen
  Geräten, Frage vom Handy beantwortet) und Befehle aus den Einstellungen (Autostart an/aus, Datenordner oder
  Protokoll öffnen). Die Oberfläche selbst braucht so keine Tauri-IPC; im Browser fehlt das Panel einfach.
- **Einmal:** Plugin `single-instance` (als erstes registriert): ein zweiter Start holt das Fenster nach vorn.
- **Selbsttest:** `--selftest` startet das Backend, liest `/api/health` und beendet sich mit Exit-Code – so prüft
  der Workflow die gebaute Exe auf einem frischen Windows-Runner.
- **Updates:** GitHub Actions baut bei einem Tag `v*` den NSIS-Installer, signiert die Updater-Artefakte und schreibt
  `latest.json` ins Release. Die App prüft beim Start und einmal täglich.
- **Fenster:** Plugin `window-state` merkt Größe und Position. Systemtitelleiste (einfach, Snap-Layouts frei Haus),
  Mica-Hintergrund, Design folgt dem System.
- **Kürzel:** in der Oberfläche: Strg+K Palette, Strg+N neues Deck, Strg+F Kartensuche im Deck, Strg+, Einstellungen,
  Strg+Q beenden, `?` Übersicht. Eine Befehlsliste speist Palette und Übersicht.
- **Benachrichtigung:** Plugin `notification`, nur wenn das Fenster nicht sichtbar ist: „Deck fertig“, „Antwort da“,
  „Neu vom Handy“.

## Schritte

| Schritt | Inhalt | fertig, wenn |
|---|---|---|
| **5a – Aufräumen** ✅ | Deskmat-Studio und die Online-Exporte entfernt; „Fragen zum Deck“ in „Frag Claude“ aufgegangen (Deckbezug, alte Fragen als Gespräche übernommen); Seitenleiste und Einstellungen gestrafft. KI-Hochskalierung und das Browser-Terminal bleiben auf Wunsch | Tests grün, Oberfläche ohne Leerstellen, alte Daten übernommen |
| **5b – Backend als Programm** ✅ | `mtg-gui --data … --port 0 --token auto` (Launcher `mtgdeck.launch`, Datenordner `paths.home()`, Token-Schutz, `/api/health`, `mcp`-Modus), PyInstaller-Ordner-Build (`desktop/sidecar/backend.spec`), Workflow „Desktop“ baut und prüft die Windows-Exe | `mtgdeck-backend.exe` startet ohne Python |
| **5c – Tauri-Hülle** ✅ | Fenster, Sidecar-Aufsicht mit Neustart und Protokoll, Single Instance, Fensterzustand, Tray, Autostart-Option, Hinweis beim Schließen, `--selftest`; der Workflow baut Backend und Hülle, prüft beide und lädt den NSIS-Installer hoch | Installer aus GitHub Actions läuft auf einem frischen Windows |
| **5d – Desktop-Feinschliff** ✅ | Anmelden bei Claude aus der App (Terminal), Tastenkürzel (Strg+K/N/J/,/S, `?` Übersicht), Tray-Tooltip mit Zustand, Benachrichtigungen bei verstecktem Fenster, Einstellungen „Desktop-App“ (Autostart, Benachrichtigungen, Ordner, Protokoll) | die zehn Regeln oben erfüllt |
| **5e – Updates** | Signaturschlüssel, `latest.json`, Release-Workflow bei Tag, Update-Hinweis in der App | ein Update von v0.1 auf v0.2 läuft durch |

**Stand 5b.** Gemessen am Linux-Build (Ordner, 302 MB, davon 230 MB die Claude-CLI des Agent-SDK): die Adresse
steht nach 0,98 s auf stdout, `/api/health` antwortet nach 1,05 s. Ohne Token antwortet die Oberfläche mit 401, der
erste Link setzt das Cookie, danach läuft alles wie gewohnt; `mtgdeck-backend mcp` meldet 43 Werkzeuge über stdio.
Die Windows-Exe baut und prüft der Workflow `.github/workflows/desktop.yml`.

**Stand 5c.** Die Hülle besteht aus `backend.rs` (Aufsicht) und `lib.rs` (Fenster, Tray, Plugins, Selbsttest), die
Startseite aus einer HTML-Datei ohne Framework. Die Oberfläche selbst läuft unverändert auf `127.0.0.1` und braucht
keine Tauri-IPC – deshalb bleibt die Capability auf `core:default`, `window-state`, `notification`, `autostart`
beschränkt. `cargo check --target x86_64-pc-windows-msvc` läuft auch unter Linux; gebaut wird nur auf dem
Windows-Runner (Rust stabil, `npx @tauri-apps/cli build`, NSIS lädt Tauri selbst). Der Workflow führt die Exe mit
`--selftest` aus und lädt den Installer als Artefakt hoch. Gemessen auf dem Windows-Runner: Backend-Build 30 s,
Rust-Build kalt 8 min, Installer 105 MB (NSIS/LZMA über rund 600 MB Backend), Lauf insgesamt gut 10 min; der
Selbsttest meldet `ok`, `frozen`, `skills`, `claude_cli`.

**Stand 5d.** Die Brücke (`gui/desktop.py` ↔ `bridge.rs`) hält die Oberfläche frei von Tauri-IPC: Tooltip, Hinweise
und Befehle gehen über die lokale API. Anmelden läuft über `claude auth login` im Terminal der App, der Zustand kommt
von `claude auth status --json`. Der KI-Status in den Einstellungen war seit dem Aufräumen (5a) leer geblieben
(verwaister Aufruf nach dem entfernten Fragen-Panel) – beim Prüfen im Browser gefunden und behoben.

## Bewusst nicht

- kein macOS/Linux-Build (Entscheidung aus dem Gesamtplan);
- keine eigene Titelleiste mit eigenen Knöpfen – die Systemleiste kann alles, was wir brauchen;
- kein Store-Eintrag, keine Telemetrie;
- der MCP-Server für Claude Code bleibt, wie er ist (die Desktop-App ersetzt `uv run mtg-gui`, nicht Claude Code).
