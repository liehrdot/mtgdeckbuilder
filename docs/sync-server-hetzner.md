# Sync-Server bei Hetzner einrichten

Damit gleichen sich deine Geräte ab: PC, Laptop und später die Handy-App. Jedes Gerät behält alle Daten. Der Server
ist nur die Drehscheibe; fällt er aus, arbeitest du einfach weiter.

**Aufwand:** etwa 20 Minuten, einmalig.
**Kosten:** etwa 6–7 € im Monat (kleinster Cloud-Server plus IPv4-Adresse).

## Überblick

1. Das Server-Image einmal veröffentlichen (GitHub)
2. Domain vorbereiten
3. Server bei Hetzner bestellen
4. Ein Befehl auf dem Server
5. PC verbinden, weitere Geräte koppeln

## 1. Image veröffentlichen (einmalig)

Die GitHub-Action **Sync-Server-Image** baut das Docker-Image `ghcr.io/liehrdot/mtgdeck-sync` für x86 und ARM. Sie
läuft bei jedem Push auf den Standard-Branch, der den Server betrifft, und einmal pro Woche.

1. Auf GitHub unter *Actions → Sync-Server-Image → Run workflow* einmal von Hand starten. Warte, bis der Lauf grün ist.
2. Das Paket öffentlich machen: GitHub-Profil → *Packages → mtgdeck-sync → Package settings → Change visibility → Public*.
   Neue Pakete sind zunächst privat. Das Repository ist öffentlich, und das Image enthält denselben Code.

Willst du das Paket privat lassen, muss sich der Server anmelden. Dafür auf dem Server
`docker login ghcr.io -u <dein-github-name>` ausführen und als Passwort ein *Classic Personal Access Token* mit nur
`read:packages` angeben.

## 2. Domain

HTTPS braucht einen Namen, der auf den Server zeigt. Ohne HTTPS geht es nicht: Die Geräte-Schlüssel sollen nicht
unverschlüsselt durchs Netz, und die Handy-App läuft nur über HTTPS.

- **Eigene Domain:** Lege eine Subdomain an, z. B. `sync.deine-domain.de`. Sobald der Server die IP-Adresse hat
  (Schritt 3), bekommt sie einen A-Eintrag auf die IPv4 und optional einen AAAA-Eintrag auf die IPv6.
- **Keine Domain:** Entweder eine günstige Domain kaufen oder einen kostenlosen DynDNS-Namen nehmen, z. B. bei
  DuckDNS (`deinname.duckdns.org`). Beides funktioniert mit dem automatischen Zertifikat.

## 3. Server bestellen

In der [Hetzner Cloud Console](https://console.hetzner.cloud) ein Projekt anlegen, dann *Server hinzufügen*.

| Einstellung | Wahl |
|---|---|
| Standort | Falkenstein oder Nürnberg (Deutschland); Helsinki geht auch |
| Image | Ubuntu 24.04 |
| Typ | Shared vCPU, kostenoptimiert: **CX23** (x86) oder **CAX11** (ARM), je 2 vCPU, 4 GB RAM, 40 GB SSD. Nimm, was verfügbar ist; das Image läuft auf beiden. |
| Netzwerk | öffentliche IPv4 und IPv6. Die IPv4 kostet etwas extra, lohnt sich aber, weil manche Netze kein IPv6 können. |
| SSH-Schlüssel | einen anlegen (siehe unten). Dann brauchst du kein Passwort. |
| Firewall | neue Firewall „mtgdeck“, eingehend: TCP 22, TCP 80, TCP 443, UDP 443 |
| Backups | optional (+20 % des Serverpreises). Der Server sichert sich ohnehin täglich selbst, und jeder PC hat alle Daten. |
| Name | z. B. `mtgdeck-sync` |

Preise laut Hetzner seit 15.06.2026: CX23 5,49 €, CAX11 5,99 € im Monat, jeweils plus IPv4. Die Verfügbarkeit
schwankt je Standort; prüf beides beim Bestellen.

**Warum die Hetzner-Firewall und nicht `ufw`?** Docker umgeht `ufw`, die Hetzner-Firewall nicht.

**SSH-Schlüssel unter Windows** (PowerShell):

```powershell
ssh-keygen -t ed25519
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub   # diesen Text bei Hetzner einfügen
```

Danach den DNS-Eintrag aus Schritt 2 auf die IP-Adresse des Servers setzen.

## 4. Einrichten: ein Befehl

```bash
ssh root@<IP-des-Servers>
curl -fsSL https://raw.githubusercontent.com/liehrdot/mtgdeckbuilder/HEAD/deploy/sync/install.sh | bash -s -- sync.deine-domain.de
```

Das Skript (`deploy/sync/install.sh`) macht Folgendes:

- prüft, ob die Domain auf den Server zeigt;
- installiert Docker;
- legt `/opt/mtgdeck-sync` an (`compose.yml`, `Caddyfile`, `.env`, `data/`);
- startet den Sync-Server und Caddy. Caddy holt das HTTPS-Zertifikat von Let's Encrypt und verlängert es selbst;
- richtet nächtliche Updates ein (systemd-Timer um etwa 4:15 Uhr);
- schaltet die automatischen Sicherheitsupdates von Ubuntu ein, falls sie es noch nicht sind;
- zeigt am Ende den **Kopplungscode** und den **Kopplungslink** für deinen PC.

Ein zweiter Aufruf schadet nicht: Er aktualisiert die Dateien und startet neu, die Daten bleiben.

## 5. PC verbinden

1. In der App *Einstellungen → Sync zwischen Geräten* öffnen.
2. Den Kopplungslink einfügen, oder den Code plus die Server-Adresse.
3. Einen Namen für das Gerät vergeben und **Verbinden** klicken. Der erste Abgleich lädt alles hoch.

**Weitere Geräte:** Auf einem verbundenen Gerät **Weiteres Gerät koppeln** klicken. Es erscheinen QR-Code, Code und
Link. Sie sind 15 Minuten gültig und nur einmal verwendbar. Auf dem anderen Gerät fügst du den Link genauso ein; die
Handy-App scannt später den QR-Code.

**Neuer Code auf dem Server**, falls kein Gerät mehr verbunden ist:

```bash
cd /opt/mtgdeck-sync && docker compose exec sync mtg-sync-server pair
```

## Im Alltag

- **Wann abgeglichen wird:**
  - beim Start;
  - etwa 15 Sekunden nach einer Änderung;
  - alle 5 Minuten (einstellbar: 1, 5, 15 oder 60 Minuten, oder nur per Knopf);
  - wenn du ins Fenster zurückkommst;
  - per Knopf **Jetzt abgleichen**.

  Ein grüner Punkt neben *Einstellungen* zeigt, dass alles abgeglichen ist; orange heißt, es gibt ein Problem.
- **Neues von anderen Geräten** meldet ein Hinweis („Neu von deinen anderen Geräten: Partien mit „Meren““). Mit
  **Anzeigen** lädt die App die aktuelle Ansicht neu.
- **Konflikte:** Haben zwei Geräte dasselbe Feld unterschiedlich geändert, gilt die Fassung, die zuerst auf dem Server
  war. Die andere steht unter *Konflikte*.
  - Partien, Gegnerdecks, Sammlungseinträge und Notizen beider Geräte bleiben immer alle erhalten.
  - Decks, die auf zwei Geräten umgebaut wurden, werden kartenweise zusammengeführt. Sie behalten alle Versionen und
    bekommen eine Version „Zusammengeführt“; danach prüft die App sie neu.
- **Gerät verloren:** Auf einem anderen Gerät unter *Geräte* abmelden. Alternativ auf dem Server:
  `docker compose exec sync mtg-sync-server devices`, dann `… revoke <id>`.
- **Sicherung einspielen** auf einem verbundenen PC: Die Daten der Sicherung werden mit denen der anderen Geräte
  zusammengeführt. Gelöscht wird dabei auf anderen Geräten nichts.

## Updates

- **Automatisch:** Jede Nacht holt der Timer neue Versionen von Sync-Server und Caddy und startet sie neu.
- **Von Hand:** `/opt/mtgdeck-sync/update.sh`
- **Betriebssystem:** Die Sicherheitsupdates kommen über `unattended-upgrades`. Nach Kernel-Updates gelegentlich
  `reboot`; die Dienste starten von selbst wieder.

## Sicherung und Wiederherstellung

- Der Server sichert sich **täglich** nach `/opt/mtgdeck-sync/data/backups/sync-JJJJMMTT.sqlite` und behält die
  letzten 14 Tage.
- Von Hand: `docker compose exec sync mtg-sync-server backup` (Datei `manuell-….sqlite`, wird nie automatisch gelöscht).
- Eine Kopie nach Hause holen: `scp root@<IP>:/opt/mtgdeck-sync/data/backups/* .`
- **Wiederherstellen:**

  ```bash
  cd /opt/mtgdeck-sync
  docker compose exec sync mtg-sync-server restore /data/backups/sync-20261001.sqlite
  ```

  Der bisherige Stand bleibt als `vor-wiederherstellung-….sqlite` daneben liegen. Die Geräte merken beim nächsten
  Abgleich, dass der Server zurückgespielt wurde, und führen alles neu zusammen. Was sie seitdem hatten, kommt dabei
  wieder auf den Server; gelöscht wird nichts. Geräte, die erst nach der Sicherung gekoppelt wurden, musst du neu koppeln.
- **Ganz neuer Server:** Alle Geräte neu koppeln. Der erste PC lädt alles hoch, die anderen führen zusammen.

## Fehlersuche

| Meldung / Problem | Lösung |
|---|---|
| „Unter dieser Adresse läuft kein Sync-Server“ | Adresse prüfen (`https://sync.deine-domain.de`, ohne Pfad) |
| „Der Sync-Server ist nicht erreichbar“ | Internet am Gerät? Server läuft? (`docker compose ps`) |
| Zertifikat fehlt / Browser warnt | DNS-Eintrag prüfen, Ports 80/443 in der Hetzner-Firewall offen? `docker compose logs caddy` |
| „Der Kopplungscode stimmt nicht oder ist abgelaufen“ | neuen Code erzeugen (15 Minuten gültig, nur einmal) |
| „Zu viele falsche Codes“ | 10 Minuten warten |
| „Dieses Gerät ist nicht (mehr) angemeldet“ | Das Gerät wurde abgemeldet: mit neuem Code wieder verbinden, die Daten bleiben |
| „Unter der Adresse läuft jetzt ein anderer Sync-Server“ | Der Server wurde neu aufgesetzt: Gerät trennen und neu koppeln |
| Protokoll | `cd /opt/mtgdeck-sync && docker compose logs -f` |

## Sicherheit

- Der Server ist nur über HTTPS erreichbar. Caddy setzt HSTS.
- Geräte-Schlüssel liegen auf dem Server nur als Hash (sha256). Abgemeldete Geräte kommen nicht mehr hinein.
- Kopplungscodes sind 15 Minuten gültig und nur einmal verwendbar. Falsche Versuche sind begrenzt (20 in 10 Minuten).
- Die Daten liegen unverschlüsselt in `data/sync.sqlite`. Wer Root auf dem Server hat, kann sie lesen. Melde dich
  deshalb nur mit SSH-Schlüssel an und gib den Zugang nicht weiter.
- Der Sync-Server läuft im Container als eigener, unprivilegierter Benutzer.
