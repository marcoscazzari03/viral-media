#!/usr/bin/env bash
# Pulls the latest code and rebuilds the worker (always refreshes yt-dlp) and the control panel.
# Usage (as root):  bash /opt/viral-media/media-worker/scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git -C .. pull --ff-only
docker compose build --build-arg YTDLP_REFRESH="$(date +%s)" worker
docker compose build portal
docker compose up -d
# disk: remove the old images left by previous builds (each update makes new ones) and build cache older than
# 3 days. Running containers and the media volume are never touched.
docker image prune -f
docker builder prune -f --filter until=72h
df -h / | tail -1
docker compose ps
