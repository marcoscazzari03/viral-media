#!/usr/bin/env bash
# Pulls the latest code and rebuilds the worker (always refreshes yt-dlp) and the control panel.
# Usage (as root):  bash /opt/viral-media/media-worker/scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git -C .. pull --ff-only
docker compose build --build-arg YTDLP_REFRESH="$(date +%s)" worker
docker compose build portal
docker compose up -d
docker compose ps
