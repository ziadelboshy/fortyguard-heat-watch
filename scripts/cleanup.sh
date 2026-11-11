#!/usr/bin/env bash
# Remove what this project created locally: the n8n container and its data volume
# (workflows, credentials, execution history), then the n8n image if nothing else uses it.
#   bash scripts/cleanup.sh               container + volume + image
#   bash scripts/cleanup.sh --keep-image  container + volume only
#   bash scripts/cleanup.sh --all         also delete ./data (alert log and dedupe state)
set -euo pipefail
cd "$(dirname "$0")/.."
keep_image=0; wipe_data=0
for arg in "$@"; do
  case "$arg" in
    --keep-image) keep_image=1 ;;
    --all) wipe_data=1 ;;
    *) echo "Unknown option: $arg (use --keep-image and/or --all)" >&2; exit 2 ;;
  esac
done
image="$(docker compose config --images 2>/dev/null | head -1 || true)"
docker compose down --volumes
if [[ $keep_image -eq 0 && -n "$image" ]]; then
  if docker image rm "$image" >/dev/null 2>&1; then
    echo "Removed image $image."
  else
    echo "Kept image $image (missing, or used by another container)."
  fi
fi
if [[ $wipe_data -eq 1 ]]; then
  rm -rf ./data
  echo "Removed ./data."
fi
echo "Left over from this project (should be empty):"
docker ps -a --filter label=project=fortyguard-heat-watch --format '{{.Names}}'
docker volume ls --filter label=project=fortyguard-heat-watch --format '{{.Name}}'
