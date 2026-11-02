#!/usr/bin/env bash
# Sets up the fleet simulator on a fresh Ubuntu 24.04 EC2 instance. Safe to re-run (as root).
#
# What you get: https://<ip-with-dashes>.sslip.io behind a username/password,
#   /          -> Fleet Ops UI + API
#   /grafana/  -> Grafana dashboard
# Only Caddy (ports 80/443) is reachable from the internet; the broker, database,
# API and Grafana sit on Docker's private network.
set -euo pipefail
REPO_DIR=/opt/fleet-telemetry-sim
cd "$REPO_DIR"

echo "== 1. swap (4 GB RAM is tight while building the UI) =="
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== 2. Docker =="
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker ubuntu || true

echo "== 3. public hostname (IMDSv2 metadata; sslip.io maps 1-2-3-4.sslip.io to 1.2.3.4) =="
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
HOST="${IP//./-}.sslip.io"

echo "== 4. credentials (generated once, never committed) =="
if [ ! -f deploy/secrets.env ]; then
  DEMO_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-16)
  GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-16)
  printf "DEMO_USER=demo\nDEMO_PASSWORD=%s\nGRAFANA_ADMIN_PASSWORD=%s\n" "$DEMO_PASSWORD" "$GRAFANA_ADMIN_PASSWORD" > deploy/secrets.env
  chmod 600 deploy/secrets.env
fi
# shellcheck disable=SC1091
source deploy/secrets.env
HASH=$(docker run --rm caddy:2.8 caddy hash-password --plaintext "$DEMO_PASSWORD")

echo "== 5. render Caddy config + compose .env =="
sed -e "s|__HOST__|$HOST|" -e "s|__USER__|$DEMO_USER|" -e "s|__HASH__|$HASH|" \
    deploy/Caddyfile.template > deploy/Caddyfile
printf "SITE_HOST=%s\nGRAFANA_ADMIN_PASSWORD=%s\n" "$HOST" "$GRAFANA_ADMIN_PASSWORD" > .env
chmod 600 .env

echo "== 6. build + start everything =="
docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d --build

echo
echo "READY: https://$HOST    (user: $DEMO_USER, password in $REPO_DIR/deploy/secrets.env)"
