# Sub-Plan: Handy-App „Am Tisch“ (Phase 3)

Stand: Oktober 2026. Teil von [`plan-desktop-sync.md`](plan-desktop-sync.md). Diese Datei legt fest, **wie** die Handy-App
aussieht und sich anfühlt, bevor gebaut wird. Grundlage sind vier Recherchen:

- Apps, die beim schnellen Eintragen beliebt sind;
- aktuelle Mobile-Design-Systeme (iOS 26, Material 3 Expressive);
- der technische Stand von PWAs 2026;
- MTG-Begleit-Apps.

Die Quellen stehen bei den Prinzipien.

## Ziel und Messlatte

Die App ist ein **ruhiger Begleiter am Spieltisch**, kein zweites Deckbuilder-Programm. Sie macht vier Dinge
schnell und ohne Nachdenken:

| Aufgabe | Wann | Messlatte |
|---|---|---|
| **Partie eintragen** | nach jeder Partie | typischer Fall **≤ 5 Taps, ≤ 20 s**, einhändig |
| **Gegnerdeck festhalten** | jemand bringt ein neues Deck mit | **≤ 15 s**, Commander per Vorschlag |
| **Rule 0 zeigen** | vor der Partie | **1 Tap** vom Start, groß genug zum Rüberreichen |
| **Karte nachschlagen** | während der Partie | Ergebnis nach **2–3 Buchstaben**, deutscher Text |

Dazu kommen Decks ansehen (Anleitung, Karten, Bilanz) und Gegner ansehen (Bilanz, Notizen). Diese Anforderungen gelten immer:

- keine Eingabe geht verloren;
- alles funktioniert offline;
- kein Konto, kein Tutorial, keine Werbung, keine Benachrichtigungen.

## Was wir aus der Recherche mitnehmen: zehn Regeln

1. **Vorbelegen statt fragen.** Die neue Partie übernimmt Deck und Gegner der letzten Partie („Revanche“). Fragen
   bleiben nur für das, was sich wirklich ändert.
   Vorbilder: BG Stats legt eine neue Partie aus der letzten an
   ([Meeple Mountain](https://www.meeplemountain.com/articles/board-game-stats-app-review-recording-your-plays-is-simple-and-easy)),
   Apples HIG empfiehlt vernünftige Vorgaben ([HIG Entering data](https://developer.apple.com/design/human-interface-guidelines/entering-data)),
   und „Mahlzeit von gestern kopieren“ ist bei MyFitnessPal ein Lieblingsfeature.
2. **Zuletzt Benutztes zuerst, Suche danach.** Gegner aus der eigenen Runde stehen oben, die Commander-Suche ist nur für
   Neue da. Grundlage: Rückmeldungen von Nutzern von Ernährungs-Trackern und Baymards Autocomplete-Studie (78 % nutzen
   Vorschläge) ([Baymard](https://baymard.com/research-articles/autocomplete-design)).
3. **Ein Bildschirm statt Assistent.** Die Zahl der Felder stört mehr als die Zahl der Schritte
   ([Baymard](https://baymard.com/blog/checkout-flow-average-form-fields)). Assistenten taugen nur für lange, seltene Aufgaben.
4. **Nur das Wichtigste sichtbar.** Der Rest steckt in „Mehr Details“, also progressive Offenlegung
   ([NN/g](https://www.nngroup.com/articles/progressive-disclosure/)). Was auf dem ersten Bildschirm steht, signalisiert,
   was zählt.
5. **Tippen statt schreiben.** Chips, Segmente und Stepper statt Ausklapplisten und Tastatur
   ([NN/g](https://www.nngroup.com/articles/drop-down-menus/)). Tippflächen sind mindestens 44–48 px groß
   ([HIG](https://developer.apple.com/design/human-interface-guidelines/accessibility),
   [WCAG 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)).
6. **Sofort speichern und „Rückgängig“ anbieten.** Keine „Wirklich speichern?“-Dialoge; die werden aus Gewohnheit
   weggeklickt. Rückmeldung in unter 100 ms, also optimistisch
   ([NN/g Antwortzeiten](https://www.nngroup.com/videos/3-response-time-limits-interaction-design/)).
7. **Nie Eingaben verlieren.** Entwürfe werden laufend gespeichert und nach dem Neustart wiederhergestellt. Splitwise wird
   genau dafür kritisiert, dass es Eingaben verliert. Die Offline-Warteschlange zeigt, was noch nicht gesendet ist.
8. **Keine Hürden.**
   - Kein Tutorial: Laut NN/g helfen sie nicht, Aufgaben wirken damit sogar schwerer
     ([NN/g](https://www.nngroup.com/articles/mobile-tutorials/)).
   - Keine Anmeldung, kein Bewertungs-Popup, kein Installations-Banner mitten in der Aufgabe
     ([web.dev](https://web.dev/articles/promote-install)).
9. **Ein leiser Moment der Freude am Ende.** Nach dem Speichern erscheint zum Beispiel „3. Sieg gegen Tims Atraxa“: ein
   sinnvoller Kontext statt Konfetti bei jedem Tipp. Duolingo hebt sich das Feiern für das Lektionsende auf.
10. **Hauptaktion sichtbar und im Daumenbereich.**
    - Die Navigation steht sichtbar unten, kein Hamburger-Menü. Versteckte Navigation halbiert fast die Auffindbarkeit
      ([NN/g](https://www.nngroup.com/videos/hamburger-menus/)).
    - Die wichtigste Aktion liegt unten. 49 % bedienen das Handy einhändig
      ([Hoober/UXmatters](https://www.uxmatters.com/mt/archives/2013/02/how-do-users-really-hold-mobile-devices.php)).

Zum Tisch passt außerdem: Das Handy soll das Spiel nicht aufhalten. Spieler stören sich an Leuten am Handy, solange es
nicht dem Spiel dient. Also gilt: schnell rein, schnell raus. Eintragen ist für **nach** der Partie gedacht.

## Aufbau

Unten liegt eine **schwebende Tab-Leiste** mit vier Bereichen, beschriftet und immer sichtbar. Die Leiste ist leicht
transparent, nach iOS 26 nur für Bedienelemente; Inhalte bleiben auf festem Grund:

| Tab | Inhalt |
|---|---|
| **Tisch** | Startseite: „Heute spielst du“ (Deck-Karte mit Rule-0-Knopf), letzte Partien, Hauptknopf **Partie eintragen** |
| **Decks** | eigene Decks → Deck mit Rule 0 · Anleitung · Karten · Partien |
| **Gegner** | Gegnerdecks (Suche, zuletzt gesehen zuerst) → Bilanz, Notizen, Merkmale; Knopf **Gegnerdeck** |
| **Karten** | Kartensuche (deutsch/englisch), zuletzt nachgeschlagen; Karten der eigenen Decks auch offline |

- **Sheets** (von unten, mit Griff, nach unten wegwischbar) für Eingaben:
  - Partie eintragen in voller Höhe;
  - Gegnerdeck anlegen;
  - Deck wechseln;
  - eine Karte ansehen.

  Es ist nie mehr als ein Sheet offen. Abbrechen steht links, Speichern unten im Daumenbereich.
- **Vollbild** nur für den Rule-0-Modus zum Rüberreichen, mit wach bleibendem Bildschirm.
- **Einstellungen** sind klein (Gerät, Erscheinungsbild, Sync-Status, Abmelden). Man erreicht sie über das Symbol oben
  rechts auf „Tisch“.

## Die Bildschirme

### Tisch (Start)

```
 Am Tisch                         (⚙)
 ┌────────────────────────────────┐
 │  [Commander-Artwork]           │   „Heute spielst du“ – Tipp = Deck wechseln
 │  Meren Aristocrats             │
 │  Bracket 3 · 5 S / 3 N          │
 │  [ Rule 0 zeigen ]             │
 └────────────────────────────────┘
 Letzte Partien
  ● Sieg   gegen Atraxa, Krenko · Zug 9 · vor 2 Std.      ↺ wird gesendet
  ● Niederlage  gegen Atraxa · Zug 7 · gestern
   …                                   [ + Gegnerdeck ]
 ╭────────────────────────────────╮
 │      ＋  Partie eintragen       │   Hauptknopf, schwebt über der Tab-Leiste
 ╰────────────────────────────────╯
  Tisch   Decks   Gegner   Karten
```

- **„Heute spielst du“** ist das zuletzt benutzte Deck. Ein Tipp auf die Karte öffnet ein Sheet zum Wechseln (alle Decks
  mit Artwork, das zuletzt gespielte oben).
- **Leerer Zustand** (noch keine Partie): Ein Satz und der Hauptknopf, zum Beispiel „Nach der Partie hier eintragen –
  dauert ein paar Sekunden.“ Kein Rundgang.
- **Sync-Hinweis** nur, wenn etwas zu sagen ist, z. B. „2 Einträge warten auf Netz“ als kleine Zeile, kein Banner.

### Partie eintragen (Sheet, volle Höhe)

```
 ─────
 Abbrechen          Partie
 Dein Deck   [◉ Meren ✓] [Tiamat] [Aesi] …       ← zuletzt benutztes vorausgewählt
 Ergebnis    [  Sieg  | Niederlage | Remis  ]    ← einziger Pflicht-Tipp
 Gegner      [Atraxa ✕] [Krenko ✕] [＋ Gegner]   ← von der letzten Partie übernommen
             „Wie letzte Partie“ · ändern = Tipp
 Woran lag's?  [zu wenige Länder] [Combo] [Board Wipe] [mehr …]   ← nur bei Niederlage
 ▸ Mehr Details  (Zug · Wie entschieden · Wer begann · Beste Karte · Notiz)
 ╭───────────────────────────╮
 │      Sieg speichern       │    ← Beschriftung folgt dem Ergebnis
 ╰───────────────────────────╯
```

- **Pflicht ist nur das Ergebnis.** Deck und Gegner sind vorbelegt; der typische Ablauf ist *Hauptknopf → Sieg →
  Speichern*, also 3 Taps.
- **Gegner:** „＋ Gegner“ öffnet die Gegnerauswahl:
  - oben die Gegner der eigenen Runde mit Artwork und Spielername;
  - darunter ein Suchfeld. Es sucht zuerst in den eigenen Gegnerdecks, dann bei Scryfall nach Commandern, auch mit
    deutschen Namen.
  - Ein unbekannter Commander wird automatisch ein neues Gegnerdeck (wie in der Desktop-App).
  - Eine Notiz pro Gegner ist über einen Tipp auf den Chip möglich.
- **Woran lag's?** erscheint nur bei einer Niederlage, mit den 6 häufigsten Gründen und dem Rest unter „mehr“. Die Liste
  ist dieselbe wie in der Desktop-App.
- **Mehr Details** (eingeklappt, merkt sich, ob du es offen hattest):
  - **Zug:** großer Stepper.
  - **Wie entschieden:** Kampf · Commander-Schaden · Combo · Alternativer Sieg · Aufgabe.
  - **Wer hat angefangen:** Chips mit „Ich“ und den Gegnern.
  - **Beste Karte:** Vorschläge aus der eigenen Deckliste, auch offline.
  - **Notiz.**
- **Speichern:**
  1. Das Sheet schließt sofort.
  2. Ein Hinweis erscheint: „Sieg gespeichert · 6. Sieg mit Meren“ mit **Rückgängig** (8 s).
  3. Die Partie steht oben in „Letzte Partien“; ohne Netz mit „wird gesendet“.
- **Entwurf:** Alles wird beim Tippen lokal gespeichert. Schließt du das Sheet versehentlich oder beendet iOS die App,
  ist die Eingabe beim nächsten Öffnen wieder da.

### Gegnerdeck festhalten (Sheet, halbe → volle Höhe)

```
 ─────
 Abbrechen       Neues Gegnerdeck
 [ 🔍 Commander …            ]        ← Tastatur geht sofort auf
   ▸ Atraxa, Praetors' Voice   [Art]  ← Vorschläge mit Artwork, deutsche Namen gehen auch
 Spieler   [Tim] [Lisa] [Jonas] [＋ Name]
 Merkmale  [Combo] [schnell] [Stax] [Board Wipes] [Counter] [mehr …]
 Notiz     …
 ╭───────────────────────────╮
 │         Speichern         │
 ╰───────────────────────────╯
```

- Nach der Auswahl des Commanders schließt die Tastatur. Der Rest sind Tipps: Spieler aus den bekannten Namen, Merkmale
  per Chip.
- Ein zweiter Commander (Partner) kommt über „＋ Partner“ dazu.
- **Offline:** Der Commander wird als Freitext gespeichert und aufgelöst, sobald wieder Netz da ist.
- Erreichbar über den Gegner-Tab, über „＋ Gegnerdeck“ auf Tisch und aus der Gegnerauswahl beim Eintragen.

### Rule 0 zeigen (Vollbild)

- Große Schrift, hoher Kontrast, Commander-Artwork als Kopf. Das Handy kann so über den Tisch gereicht werden.
- **Inhalt:**
  - Stufe (Bracket mit Unterstufe);
  - Spielweise in einem Satz;
  - Game Changer;
  - 2-Karten-Combos, Tutoren, Extra-Züge, Land-Zerstörung;
  - Proxys und Tischregel.

  Heikle Zeilen sind farblich markiert, wie beim Rule-0-Text der Desktop-App.
- **Schließen** unten; **Teilen/Kopieren** des Textes über das Teilen-Menü des Handys.
- Der Bildschirm bleibt an (Wake Lock), solange der Modus offen ist.

### Decks und Deck

- **Liste:** Karten mit Commander-Artwork, Name, Stufe, Bilanz und Farbidentität als Manapunkte.
- **Deck:** Artwork-Kopf und ein Segment *Rule 0 · Anleitung · Karten · Partien*.
  - **Karten:** nach Kategorie gruppiert, Bilder und Texte von Scryfall, danach offline gespeichert.
  - **Anleitung:** Die gespeicherte Spielanleitung des PCs (Plan, früh/mitte/spät, Mulligan, Schlüsselkarten).
  - **Partien:** Bilanz und Liste; Wischen löscht mit Rückgängig.

### Gegner

- **Liste** mit Suche, zuletzt gesehen zuerst. Pro Eintrag: Artwork, Titel („Tims Atraxa“), Bilanz gegen dich und
  Merkmale.
- **Gegner:** Bilanz pro eigenem Deck, Notizen (neueste oben, mit „Notiz hinzufügen“), Merkmale per Chip änderbar.
- Unter 5 Partien gegen einen Gegner erscheint der Hinweis „wenige Partien“, damit die Bilanz nicht überbewertet wird
  (wie bei The Pod Companion).

### Karten

- **Suchfeld** oben, ohne Autofokus, damit nicht ständig die Tastatur aufspringt. Darunter „Zuletzt nachgeschlagen“.
- **Ergebnis-Sheet:**
  - großes Kartenbild im Verhältnis 63:88, ohne Springen beim Laden;
  - Umschalter **Deutsch | Original (Oracle)**;
  - Begriffe aus dem Glossar als antippbare Erklärungen.
- **Hinweis:** Deutsche Texte sind der Druckstand und können vom aktuellen Oracle-Text abweichen. Deshalb gibt es den
  Umschalter.

### Erster Start und Kopplung

Auf dem iPhone hat eine installierte Web-App **eigenen Speicher**, getrennt von Safari
([WebKit-Bug 181849](https://bugs.webkit.org/show_bug.cgi?id=181849)). Daraus folgt dieser Ablauf:

1. Am PC auf *Einstellungen → Sync zwischen Geräten → Weiteres Gerät koppeln* klicken; es erscheint ein QR-Code.
2. Mit der Handy-Kamera scannen. Die Seite `…/koppeln#CODE` öffnet sich im Browser.
3. **Auf dem iPhone im Browser** wird der Code **nicht** eingelöst. Die Seite zeigt eine kurze, bebilderte Anleitung
   „Zum Home-Bildschirm hinzufügen“ und den Code groß.
   **Auf Android** wird direkt verbunden, danach gibt es einen Knopf **Installieren**.
4. In der installierten App: **QR-Code scannen** (Kamera in der App) oder **Code eingeben** (8 Zeichen, mit
   Bindestrich-Hilfe). Ist der Code abgelaufen, erzeugt man am PC einen neuen; der Hinweis sagt das.
5. Danach erscheint sofort die Tisch-Seite mit den Daten. Kein weiterer Schritt.

Wer die App im Browser lässt, statt sie zu installieren, kann sie dort auch benutzen. Ein unaufdringlicher Hinweis
(einmal, wegklickbar) erklärt dann, warum Installieren besser ist: Der Speicher ist sicherer, und der Start ist
schneller.

## Gestaltung

- **Eine Linie mit der Desktop-App:** dieselbe Akzentfarbe (Violett) und dieselbe Sprache. Die Begriffe sind dieselben:
  Bracket, Game Changer, Extra-Züge, Massen-Landzerstörung.
- **Bilder tragen die Optik.** Commander-Artwork (Scryfall `art_crop`) ist der Kopf von Deck-, Gegner- und Rule-0-Karten.
  Farbidentität erscheint als kleine Manapunkte (W/U/B/R/G). So sieht die App nach Magic aus, ohne verspielt zu sein.
- **Typografie:** Systemschrift. Größen nach iOS (Large Title 34, Title 22/20, Body 17, Subhead 15, Footnote 13); alles
  in `rem`, damit die Systemschriftgröße greift. Eingabefelder haben mindestens 16 px, sonst zoomt iOS.
- **Farben:**
  - hell: gruppierter Hintergrund (#f2f2f7) mit weißen Flächen;
  - dunkel: fast schwarz mit leicht angehobenen Flächen;
  - Standard ist das System-Design, umstellbar.
  - Kontrast für Text mindestens 4,5:1, für Bedienelemente mindestens 3:1.
- **Formen:** große, konzentrische Rundungen (Sheets 28, Karten 20, Knöpfe als Kapseln). Bars schweben mit
  Unschärfe-Effekt und festem Hintergrund als Rückfall, weil Safari „weniger Transparenz“ nicht meldet.
- **Bewegung:**
  - Federn per CSS `linear()`, 200–300 ms; Sheets folgen dem Finger.
  - Seitenwechsel per View Transitions (iOS 18+, Chrome), sonst ohne Animation.
  - Bei „Bewegung reduzieren“ werden nur Einblendungen benutzt.
- **Haptik:** Nur auf Android und nur als Zugabe beim Speichern; iOS hat im Web keine Vibration.
- **Rückmeldung:**
  - Jeder Knopf hat einen sichtbaren Gedrückt-Zustand.
  - Ladezustände sind Platzhalter in der richtigen Größe statt Kreisel. Ein Kreisel kommt nur im Knopf, wenn etwas
    wirklich dauert.
  - Hinweise kommen einzeln, unten über der Tab-Leiste.

## Daten und Offline

- **Lesen:**
  - Der Sync-Server stellt ein kompaktes Lesemodell bereit (`GET /api/app/data`, mit ETag). Es enthält: Decks (Stufe,
    Farbidentität, Rule 0, Anleitung, Kartenliste mit Kategorien, Bilanz), Gegner (mit Bilanz), Partien, die
    Auswahllisten (Probleme, Merkmale) und den PC-Status.
  - Die App speichert es in IndexedDB. Beim Start zeigt sie sofort den letzten Stand und aktualisiert im Hintergrund.
- **Kartenbilder und -texte** kommen direkt von Scryfall, für Decklisten über `/cards/collection`. Bilder landen im Cache
  des Service Workers, mit Obergrenze. Scryfall erlaubt CORS, die Antworten sind also normal und nicht „opak“.
- **Schreiben:**
  - Jede Eingabe ist eine **Operation mit eigener Kennung** in einer Warteschlange (IndexedDB), z. B. „Partie
    hinzufügen“, „Gegnerdeck anlegen“, „Notiz“.
  - Der Server führt sie aus und legt Partien und Gegner **genau so an wie die Desktop-App**: dieselben Felder, dieselbe
    Gegner-Verknüpfung.
  - Wird eine Operation doppelt gesendet, passiert nichts; die Kennung schützt davor.
- **Senden:**
  - beim Start, sobald Netz da ist, beim Zurückkehren in die App und nach jeder Eingabe;
  - iOS kennt keine Hintergrund-Synchronisation.
  - Fehlgeschlagene Einträge bleiben sichtbar und werden später erneut versucht.
- **Am PC** kommt alles über den normalen Abgleich an. Die Zusammenführung ist schon dafür gebaut: Listen mit IDs
  bleiben vollständig.
- **Neue Felder für Partien:** `how` (wie entschieden) und `started` (wer begann). Die Desktop-App zeigt sie im
  Partien-Tab und kann sie dort auch eintragen.

## Technik

- **Wo die App liegt:** Der Sync-Server liefert sie unter `/` aus (`src/mtgdeck/sync/app/`). Sie ist Vanilla JS mit
  ES-Modulen, ohne Build-Schritt und ohne Framework. Der Server hat dafür neue Routen `/api/app/*` und ein Manifest.
- **Service Worker:**
  - App-Gerüst versioniert vorab speichern.
  - Daten aus dem Netz, mit Rückfall auf den letzten Stand; Bilder zuerst aus dem Cache.
  - Neue Version → Hinweis „Neue Version – neu laden“ (nach dem Sichern offener Entwürfe).
- **Gerät:** Den Geräte-Schlüssel speichert die App in IndexedDB. Eine strenge CSP verbietet Inline-Skripte und fremde
  Skripte, alle Texte werden escaped. Abmelden am PC sperrt das Handy sofort.
- **QR-Scanner:** im Browser eingebauter `BarcodeDetector` wo vorhanden (Android), sonst eine kleine mitgelieferte
  Bibliothek (nimiq `qr-scanner`, MIT). Code-Eingabe gibt es immer.
- **Manifest:**
  - Name „Am Tisch“, Symbole (normal und maskable), Hintergrundfarbe;
  - Verknüpfung „Partie eintragen“;
  - Screenshots für die schönere Android-Installation.
- **Budget:**
  - unter 150 KB JavaScript, Start aus dem Cache unter 1 s;
  - keine Layout-Sprünge (Bildgrößen vorab);
  - lange Listen mit `content-visibility`.

## Barrierefreiheit

- Alle Bedienelemente sind echte Knöpfe oder Links mit Beschriftung.
- Sheets sind `<dialog>` mit Fokus-Falle; der Griff ist auch per Tastatur oder VoiceOver bedienbar.
- Farben tragen nie allein die Bedeutung: Sieg und Niederlage haben auch Text bzw. Symbol.
- Die Schrift skaliert bis 200 %.

## Bewusst nicht

- kein Konto und kein Login außer der Kopplung;
- keine Benachrichtigungen, keine Bewertungsbitten;
- kein Deck-Bearbeiten auf dem Handy (dafür ist der PC da);
- kein Lebenspunkte-Zähler. Später denkbar: Ein Zähler, der Sieger und Zug gleich mitliefert, wäre die konsequenteste
  Form von „Vorbelegen“. Das ist ein eigenes Thema.

## Schritte

| Schritt | Inhalt | fertig, wenn |
|---|---|---|
| **3a – Prototyp** ✅ | Gestaltung und alle Kern-Bildschirme mit Demodaten, klickbar im Handy-Browser | Screenshots hell/dunkel abgenommen |
| **3b – Daten** ✅ | Lesemodell `/api/app/data`, Operationen (Partie, Gegner, Notiz) mit Kennung, neue Partie-Felder in der Desktop-App | Handy-Eintrag erscheint am PC |
| **3c – Offline** ✅ | Service Worker, Warteschlange, Entwürfe, Scryfall-Cache | Partie im Flugmodus eingetragen, später gesendet |
| **3d – Kopplung** ✅ | Kopplungsseite, Installationsanleitung, QR-Scanner, Code-Eingabe, Einstellungen, Abmelden | iPhone und Android von Null gekoppelt |
| **3e – Feinschliff** | Tests (Playwright mit iPhone- und Pixel-Emulation, Offline, Tap-Zähler, Tippflächen-Prüfung), Doku | alle Messlatten oben erfüllt |

**Stand 3a:** Der Prototyp läuft unter `/app/` des Sync-Servers mit Beispieldaten. Einträge bleiben vorerst auf dem Gerät.
Gemessen im Prototyp:

- typische Partie: 3 Taps;
- Rule 0 vom Start aus: 1 Tap;
- Gegnerdeck: 6 Taps plus ein paar Buchstaben;
- alle Tippflächen mindestens 44 px, kein seitliches Scrollen, hell und dunkel.

**Stand 3b–3d:** Die App ist echt verbunden. Sie liest die Daten des Sync-Servers (`/api/app/data`) und schreibt
Partien, Gegnerdecks und Notizen als Operationen mit Kennung (`/api/app/ops`). Der Server trägt sie so in die Dateien
ein, wie es die Desktop-App tut, und der PC holt sie beim nächsten Abgleich. Doppelt gesendete Operationen ändern
nichts.

- **Offline:** Einträge warten in einer Warteschlange und gehen raus, sobald Netz da ist. Der Service Worker startet
  die App auch ohne Netz.
- **Rückgängig:** Ist ein Eintrag schon gesendet, schickt die App das Gegenstück.
- **Kopplung:** Es gibt drei Wege, nämlich QR-Code in der App, Code-Eingabe und Kopplungslink. Auf Android wird nach
  einer Rückfrage verbunden, auf dem iPhone im Browser kommt zuerst die Anleitung zum Home-Bildschirm.
- **Einstellungen:** Sie zeigen den Abgleich, den Server, dieses Handy und die PCs (online, „Claude bereit“). Dazu
  kommen „Abmelden“ und nicht übernommene Einträge mit „Verwerfen“. Wird das Handy am PC abgemeldet, sagt die App das
  beim Start.

Geprüft mit Playwright gegen einen echten Server, emuliert als iPhone und Pixel: Koppeln per Code, Link,
iPhone-Anleitung und QR-Code über eine simulierte Kamera, Warteschlange im Flugmodus, Start ohne Netz, Rückgängig nach
dem Senden, Abmelden, abgemeldet am PC. Offen für 3e ist die Abnahme auf echten Geräten.

**Prüfung der Messlatten:** Ein Playwright-Skript zählt die Taps für den typischen Ablauf (Partie, Gegnerdeck, Rule 0).
Es prüft außerdem, dass jede Tippfläche mindestens 44 × 44 px groß ist, dass nichts seitlich scrollt und dass die
Eingaben im Flugmodus erhalten bleiben.
