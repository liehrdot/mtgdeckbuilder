# Plan: Desktop-App, Cloud-Sync und Handy-App

Stand: Oktober 2026. Ziel: Die App läuft als Windows-Desktop-App, alle Daten gleichen sich über einen eigenen
Sync-Server ab, und eine Handy-Web-App (PWA) erlaubt am Spieltisch Partien und Gegnerdecks einzutragen – auch
offline. Später am PC ist alles da.

## Entscheidungen

| Thema | Entscheidung |
|---|---|
| Sync-Server | eigener kleiner Server (Python, derselbe Code), als **ein Docker-Container** bei **Hetzner Cloud** in Deutschland (Nürnberg/Falkenstein) |
| Handy | **PWA** (Web-App auf dem Homescreen), kein App Store |
| Desktop | **nur Windows**, **Tauri 2** mit dem Python-Backend als mitgeliefertem Hintergrundprozess |
| KI auf dem Handy | als **Auftrag an den PC**; das Handy zeigt, ob der PC gerade erreichbar ist und ob Claude dort bereit ist |
| Neu am Tisch | **Gegnerdeck schnell anlegen** (Commander, Spieler, Merkmale, Notiz) – auch offline |

## Grundprinzip: lokal zuerst

- Jedes Gerät hat **alle Daten lokal** und funktioniert ohne Netz. Der Server ist nur die Drehscheibe.
- Fällt der Server aus, geht nichts verloren: jeder PC hat eine vollständige Kopie (plus die bisherigen Zip-Sicherungen).
- Synchronisiert wird im Hintergrund: beim Start, alle paar Minuten, nach jeder Änderung (entprellt) und per Knopf.

## Was synchronisiert wird

Jede Datei ist ein **Dokument** mit einem logischen Pfad (dieselben Namen wie in der Zip-Sicherung):

| Pfad | Art | Zusammenführen |
|---|---|---|
| `decks/<slug>.json` | Deck | eigene Regel (siehe unten) |
| `decks/.versions/<slug>/vNNNN.json` | Versions-Schnappschuss | unveränderlich; bei Nummernkollision neu nummeriert |
| `decks/.games/<slug>.json`, `decks/.questions/<slug>.json`, `decks/.opponents.json`, `decks/.chats/<id>.json`, `decks/.meta-suggestions.json` | JSON | allgemeine Regel |
| `collection.json`, `tablerules.json` | JSON | allgemeine Regel |
| `blacklist.txt` | Text | Zeilen als Menge |
| `proxies/.orders/<id>.json`, `proxies/<slug>/selection.json`, `proxies/<slug>/tokens.json`, `proxies/<slug>/uploads/index.json` | JSON | allgemeine Regel |
| `proxies/<slug>/uploads/*`, `deskmats/<id>/source.*` | Bild | die zuletzt hochgeladene Fassung gewinnt |
| `deskmats/<id>/meta.json` | JSON | allgemeine Regel |

**Nicht** synchronisiert: Kartendatenbank, Bild-Caches, Druckdateien (`images/`, XML, PDF, `prepared.json`),
Deskmat-Renderings, die `.txt`-Exporte der Decks (werden neu erzeugt), Papierkorb, Sicherungen,
`mtgdeck.settings.json` (Pfade und Programme sind pro Gerät), Sperr- und Temp-Dateien.

### Allgemeine Regel (Drei-Wege-Zusammenführung)

Jedes Gerät merkt sich den zuletzt synchronisierten Stand („Basis“). Beim Zusammenführen werden Basis, eigene und
fremde Fassung verglichen:

- **Objekte** Schlüssel für Schlüssel; was nur eine Seite geändert hat, wird übernommen.
- **Listen von Objekten mit `id`** (Partien, Sammlung, Gegner, Notizen, Positionen, Nachrichten …) Eintrag für
  Eintrag: neue Einträge beider Seiten bleiben, gelöschte verschwinden, geänderte werden wieder Feld für Feld
  zusammengeführt. Gelöscht auf der einen, geändert auf der anderen Seite → der geänderte Eintrag bleibt.
- **Andere Werte**, die beide Seiten unterschiedlich geändert haben: die Fassung, die schon auf dem Server ist,
  gewinnt; die eigene wird im **Konfliktprotokoll** festgehalten (in der App einsehbar).

### Decks

- Nur eine Seite geändert → übernehmen.
- Beide geändert: Karten werden pro Karte zusammengeführt (Anzahl, Kategorie), die übrigen Felder wie oben.
  Versionen beider Geräte bleiben im Verlauf erhalten – die eigenen, noch nicht hochgeladenen Versionen werden
  hinter die fremden **neu nummeriert**, das Ergebnis wird eine neue Version „Zusammengeführt“.
- Danach wird das Deck im Hintergrund neu geprüft (Legalität, Bracket, Preis).

## Sync-Server

- Python (FastAPI) + **SQLite**, ein Docker-Image `ghcr.io/liehrdot/mtgdeck-sync` (amd64 + arm64), gebaut von
  GitHub Actions bei jedem Release.
- Protokoll (umgesetzt in `src/mtgdeck/sync/server.py`):
  - `GET /api/sync/changes?since=<seq>` – alle Dokumente, die sich seit der Nummer geändert haben (seitenweise);
  - `PUT /api/sync/doc` mit der Basis-Nummer – angenommen, oder `409` mit der aktuellen Fassung (der PC führt
    zusammen und schickt erneut);
  - Bilder reisen vorerst als Base64 im selben Aufruf (einzeln bis 64 MB); inhaltsadressierte Blobs folgen, falls nötig.
  - Wiederherstellen einer Server-Sicherung (`mtg-sync-server restore`) setzt eine neue „Epoche“: Die Geräte führen
    danach alles neu zusammen, ohne anderswo etwas zu löschen.
- Für das Handy zusätzlich **fachliche Aufrufe** (Partie eintragen, Gegnerdeck anlegen, Notiz, Frage an den PC);
  der Server führt sie mit derselben Zusammenführ-Logik aus.
- **Geräte-Anmeldung per QR-Code**: der PC erzeugt einen Kopplungscode, das Handy scannt ihn und bekommt ein
  eigenes Token. Geräte lassen sich einzeln abmelden. Nur HTTPS.
- **Anwesenheit**: die Desktop-App meldet sich jede Minute („PC online, Claude bereit/nicht bereit“). Das Handy
  zeigt daraus „PC erreichbar“ oder „PC zuletzt vor 3 Std.“.
- **KI-Aufträge**: Fragen vom Handy werden als Dokumente `requests/<id>.json` gespeichert. Die Desktop-App holt
  offene Aufträge ab, sobald sie läuft und Claude bereit ist, beantwortet sie (wie „Fragen zum Deck“ bzw.
  „Frag Claude“) und schreibt die Antwort zurück; das Handy zeigt den Status (wartet auf PC · in Arbeit · fertig).

### Betrieb bei Hetzner

- Kleiner Cloud-Server: CX23 (x86) oder CAX11 (ARM), 5,49 € bzw. 5,99 € im Monat plus IPv4 (Preise seit 15.06.2026;
  Verfügbarkeit beim Bestellen prüfen). Anleitung: [`sync-server-hetzner.md`](sync-server-hetzner.md).
- `deploy/sync/compose.yml` mit zwei Diensten plus Update-Timer:
  - `sync` (unser Image, Daten auf einem Volume),
  - **Caddy** (automatisches HTTPS über Let's Encrypt; eigene Domain oder ein kostenloser DynDNS-Name),
  - statt Watchtower (seit Dezember 2025 nicht mehr gepflegt) ein **systemd-Timer** mit `update.sh`, der nachts
    `docker compose pull && up -d` ausführt.
- Einrichtung einmalig per Anleitung/Skript; Betriebssystem-Updates per `unattended-upgrades`.
- Sicherung: nächtlicher SQLite-Schnappschuss auf dem Volume, optional zusätzlich in Hetzner Object Storage.
- `/health` für eine einfache Überwachung; die Desktop-App zeigt den Sync-Status.

## Handy-App (PWA) „Am Tisch“

- Installierbar auf dem Homescreen, offline nutzbar (Service Worker, lokale Warteschlange in IndexedDB).
- Startseite: **Partie eintragen** – Deck wählen, Ergebnis, Zug, Gegner (aus den Gegnerdecks oder neu), Probleme
  per Schnellwahl, beste Karte, Notiz.
- **Gegnerdeck schnell anlegen**: Commander mit Autovervollständigung (Scryfall direkt vom Handy; offline als
  Freitext, wird später aufgelöst), Spieler, Merkmale per Schnellwahl (Combo, Board Wipes, schnell …), Notiz –
  in wenigen Sekunden, ohne die Partie zu verlassen.
- Decks ansehen: Rule-0-Text, Anleitung, Kartenliste mit Bildern, Bilanz.
- Karten nachschlagen (Kartentext auf Deutsch), in der Sammlung nachsehen.
- **Frag Claude** als Auftrag an den PC, mit Anzeige „PC erreichbar / Claude bereit“.

## Desktop-App (Tauri 2, Windows)

- Fenster mit der bestehenden Oberfläche (WebView2), Python-Backend als Sidecar (PyInstaller-Build),
  zufälliger lokaler Port mit Zugriffstoken.
- Symbol im Infobereich (Sync-Status), Autostart optional, automatische Updates über GitHub Releases,
  Installer (MSI/NSIS) aus GitHub Actions.
- Externe Programme (MPC Autofill, Real-ESRGAN) bleiben optional wie heute.

## Phasen

| Phase | Inhalt | fertig, wenn |
|---|---|---|
| **1 – Sync-Kern** ✅ | logische Pfade, Basis-Speicher, Drei-Wege-Zusammenführung, Deck-Regel mit Neunummerierung, Konfliktprotokoll, Server-Speicher (SQLite) als Bibliothek | zwei Datenordner gleichen sich über den Speicher ab; Tests für alle Konfliktfälle |
| **2 – Sync-Server** ✅ | HTTP-Schicht, Geräte-Token und QR-Kopplung, Anwesenheit, Docker-Image, Compose-Datei, Anleitung Hetzner; Seite „Sync“ in den Einstellungen (Status, jetzt synchronisieren, Konflikte, Geräte) | PC ↔ Server ↔ zweiter PC im Alltag |
| **3 – Handy-PWA** ✅ | „Am Tisch“: Partie eintragen, Gegnerdeck schnell anlegen, Decks/Rule 0 ansehen, Karten nachschlagen; Offline-Warteschlange | Partie offline am Tisch eingetragen, später am PC sichtbar |
| **4 – KI-Aufträge** ✅ | Aufträge vom Handy, Abarbeitung am PC, Statusanzeige | Frage vom Handy wird vom PC beantwortet |
| **5 – Desktop-App** | Tauri-Hülle, Sidecar, Infobereich, Installer, Auto-Update | Windows-Installer aus GitHub Actions |

## Phase 4 im Detail: „Frag Claude“ vom Handy

**Ablauf.** Die Frage ist ein Auftrag mit Kennung. Der Server ist die Drehscheibe, der PC arbeitet ihn ab.

1. Das Handy schickt die Operation `chat.ask` (Gespräch, Frage, „Gründlich“) über die Warteschlange, also auch
   offline. Der Server schreibt die Frage als offene Nachricht in das Gespräch `decks/.chats/<id>.json`. So
   steht sie am PC unter „Frag Claude“, und das Handy zeigt sie sofort. Zusätzlich legt der Server einen Auftrag an.
2. Der PC wartet über eine offene Leitung auf Aufträge (`POST /api/jobs/claim`, bis 25 s). Ein neuer Auftrag kommt
   ohne Verzögerung an, ein ruhender PC kostet nichts. Der PC holt sich nur Aufträge, wenn Claude bereit ist und
   „Fragen vom Handy beantworten“ an ist (Einstellung, Standard: an).
3. Der PC beantwortet die Frage mit dem gleichen App-Chat wie am PC (nur lesen, Übersicht über alles, bisheriges
   Gespräch als Zusammenhang). Unterwegs meldet er, was Claude gerade tut („liest Kartentexte“). Das ist zugleich
   das Lebenszeichen: Bleibt es länger als `JOB_LEASE` aus, gibt der Server den Auftrag wieder frei.
4. Der PC schickt die Antwort an den Server (`POST /api/jobs/<id>/finish`). Der Server trägt sie ins Gespräch ein
   und ist damit der einzige, der Antworten auf Handy-Fragen schreibt. Danach gleicht der PC ab, und das Gespräch
   steht auch dort.
5. Das Handy fragt, solange eine Frage offen ist, alle paar Sekunden den Stand ab und lädt die Antwort, sobald sie
   da ist.

**Was das Handy zeigt** (ehrlich, wie Zustellstatus in Messengern):
- „Wird gesendet, sobald Netz da ist“, solange die Frage noch auf dem Handy wartet;
- „Wartet auf deinen PC“, wenn kein PC erreichbar ist (die Frage bleibt liegen, bis er läuft);
- „Claude liest Kartentexte …“ während der Arbeit;
- die Antwort, Kartennamen und Decks darin antippbar;
- „Hat nicht geklappt“ mit Grund und „Nochmal fragen“.

Offene Fragen lassen sich zurückziehen. Ein Punkt am Tab zeigt neue Antworten. Es gibt keine Benachrichtigungen,
wie in der Handy-App überall.

**Bedienung.** Ein fünfter Tab „Claude“ zeigt die Gespräche und den Stand des PCs. Neue Fragen und Nachfragen
schreibt man in ein Sheet; das folgt der Tastatur, wie die anderen Sheets auch. Vorschläge als Chips helfen beim
Einstieg, zum Beispiel „Wie spiele ich {Deck} gegen {letzter Gegner}?“ oder „Regelfrage: …“. Vom Deck aus führt
„Claude fragen“ in eine Frage zu diesem Deck.

**Grenzen.** Pro Handy sind höchstens 10 offene Fragen erlaubt. Das Handy liest die letzten 30 Gespräche, auch
offline.

**Stand.** Phase 4 ist umgesetzt. Getestet sind:
- die Operationen;
- die Server-Routen (sofortiges Wecken eines wartenden PCs, Freigabe nach Funkstille, Zurückziehen während der
  Arbeit, Grenze offener Fragen);
- der PC mit nachgestelltem Claude gegen einen echten Server-Ablauf.

Im Browser geprüft sind die Vorschau (hell und dunkel, Tippflächen, kein seitliches Scrollen) und der Ablauf mit
echtem Server und nachgestelltem PC:
- Zwischenstand und Antwort mit Links;
- Nachfrage offline;
- Punkt am Tab;
- Zurückziehen;
- „PC beantwortet keine Fragen“.

## Risiken und offene Punkte

- **Zusammenführen ist der kritische Teil** – deshalb Phase 1 zuerst und mit vielen Tests.
- Gleichzeitige Schreibzugriffe von GUI, MCP-Server und Sync auf demselben Rechner: der Sync schreibt nur unter
  denselben Dateisperren und überspringt Dateien, die sich während des Abgleichs geändert haben.
- Bilder können groß werden (Deskmat-Motive); Speicherplatz auf dem Server im Blick behalten.
- Die Desktop-Oberfläche lädt nach einem Abgleich die geänderten Ansichten neu (Hinweis „Neu vom Handy: 1 Partie“).
