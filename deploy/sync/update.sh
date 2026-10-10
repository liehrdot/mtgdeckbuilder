#!/usr/bin/env bash
# Neue Images holen und die Dienste neu starten – nachts per systemd-Timer oder jederzeit von Hand.
set -euo pipefail
cd "$(dirname "$0")"
docker compose pull --quiet
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
