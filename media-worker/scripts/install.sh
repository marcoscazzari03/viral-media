#!/usr/bin/env bash
# Installs / updates the US VIRAL media worker on a fresh Ubuntu 24.04 server.
# Usage (as root):  bash scripts/install.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> System update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y git curl openssl unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker"
  curl -fsSL https://get.docker.com | sh
fi

if [ ! -f .env ]; then
  echo "==> Creating .env with a new random API_TOKEN"
  cp .env.example .env
  sed -i "s/^API_TOKEN=.*/API_TOKEN=$(openssl rand -hex 32)/" .env
fi

echo "==> Building and starting containers (first build takes a few minutes)"
docker compose up -d --build

echo
echo "==> Done. Containers:"
docker compose ps
echo
echo "API token (put it in the n8n credential, keep it secret):"
grep '^API_TOKEN=' .env | cut -d= -f2
