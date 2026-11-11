#!/usr/bin/env bash
# Start the local n8n container and prepare the two state files the workflow uses.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found on PATH. Install Docker first." >&2
  exit 1
fi

mkdir -p data
[[ -f data/seen-alerts.json ]] || echo '{"seen": []}' >data/seen-alerts.json
[[ -f data/alerts-log.csv ]] ||
  echo 'logged_utc,site,alert_id,predicted_breach_utc,timezone,predicted_temp_c,level,rule' >data/alerts-log.csv

# n8n runs as uid 1000 in the container. Docker Desktop maps ownership for you; on a Linux
# host with another uid, hand the folder over once instead of making it world-writable.
if [[ "$(uname -s)" == "Linux" && "$(id -u)" != "1000" ]]; then
  echo "Note: n8n runs as uid 1000. If it can't write ./data, run once: sudo chown -R 1000:1000 data" >&2
fi

# --wait blocks until the healthcheck in compose.yaml passes.
docker compose up -d --wait --wait-timeout 120
echo "n8n is up: http://localhost:5678"
