#!/usr/bin/env bash
# Richtet den Sync-Server des MTG-Deckbuilders auf einem frischen Ubuntu-/Debian-Server ein (als root):
#
#   curl -fsSL https://raw.githubusercontent.com/liehrdot/mtgdeckbuilder/HEAD/deploy/sync/install.sh | bash -s -- sync.deine-domain.de
#
# Installiert Docker (falls nötig), legt /opt/mtgdeck-sync an, startet Sync-Server + Caddy (HTTPS),
# richtet nächtliche Updates ein und zeigt am Ende den Kopplungscode für den ersten PC.
# Ein zweiter Aufruf schadet nicht: er aktualisiert die Dateien und startet neu, die Daten bleiben.
set -euo pipefail

DOMAIN="${1:-${SYNC_DOMAIN:-}}"
RAW="${MTG_SYNC_RAW:-https://raw.githubusercontent.com/liehrdot/mtgdeckbuilder/HEAD/deploy/sync}"
DIR=/opt/mtgdeck-sync

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nFEHLER: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Bitte als root ausführen (oder mit sudo)."
if [ -z "$DOMAIN" ] && [ -r /dev/tty ]; then
  read -rp "Domain für den Sync-Server (z. B. sync.deine-domain.de): " DOMAIN </dev/tty
fi
DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
[ -n "$DOMAIN" ] || die "Ohne Domain geht es nicht – HTTPS braucht einen Namen, der auf diesen Server zeigt."

say "DNS prüfen"
MYIP=$(hostname -I 2>/dev/null | awk '{print $1}')
DNSIP=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1}' || true)
if [ -z "$DNSIP" ]; then
  echo "Achtung: $DOMAIN hat (noch) keinen DNS-Eintrag. Leg einen A-Eintrag auf $MYIP an – Caddy holt das"
  echo "Zertifikat automatisch, sobald der Name auf diesen Server zeigt."
elif [ "$DNSIP" != "$MYIP" ]; then
  echo "Achtung: $DOMAIN zeigt auf $DNSIP, dieser Server hat $MYIP. Bitte den A-Eintrag prüfen."
else
  echo "$DOMAIN zeigt auf diesen Server ($MYIP)."
fi

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null
docker compose version >/dev/null 2>&1 || die "„docker compose“ fehlt – bitte das Docker-Compose-Plugin installieren."

say "Dateien nach $DIR"
mkdir -p "$DIR/data"
chown 1000:1000 "$DIR/data"
for f in compose.yml Caddyfile update.sh mtgdeck-sync-update.service mtgdeck-sync-update.timer; do
  curl -fsSL "$RAW/$f" -o "$DIR/$f.neu" || die "Konnte $f nicht laden ($RAW/$f)."
  mv "$DIR/$f.neu" "$DIR/$f"
done
chmod +x "$DIR/update.sh"
printf 'SYNC_DOMAIN=%s\n' "$DOMAIN" > "$DIR/.env"

say "Automatische Updates"
cp "$DIR/mtgdeck-sync-update.service" "$DIR/mtgdeck-sync-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mtgdeck-sync-update.timer >/dev/null
echo "Neue Versionen des Sync-Servers und von Caddy werden jede Nacht gegen 4:15 Uhr geholt."
if command -v apt-get >/dev/null 2>&1; then  # security updates of the system itself (Ubuntu: usually on already)
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades >/dev/null 2>&1 || true
fi

say "Starten"
cd "$DIR"
docker compose pull --quiet || die "Konnte die Images nicht laden. Ist das Paket „mtgdeck-sync“ auf GitHub öffentlich? (Anleitung, Schritt „Image“)"
docker compose up -d --remove-orphans
for _ in $(seq 1 60); do
  docker compose exec -T sync python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" >/dev/null 2>&1 && break
  sleep 1
done

say "Fertig"
echo "Server: https://$DOMAIN  (das Zertifikat kann beim ersten Start eine Minute dauern)"
echo
docker compose exec -T sync mtg-sync-server pair
echo
echo "Später einen neuen Code:   cd $DIR && docker compose exec sync mtg-sync-server pair"
echo "Geräte anzeigen:           cd $DIR && docker compose exec sync mtg-sync-server devices"
echo "Protokoll ansehen:         cd $DIR && docker compose logs -f"
