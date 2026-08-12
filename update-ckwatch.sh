#!/bin/bash
# update-ckwatch.sh — redeploy ckwatch from the tarball in the sandbox share.
# Usage: bash /mnt/user/sandbox/update-ckwatch.sh
set -euo pipefail

APP=/mnt/user/appdata/ckwatch
TARBALL=/mnt/user/sandbox/ckwatch.tar.gz

echo "==> Preserving config.toml (holds your ntfy credentials)"
cp "$APP/config.toml" /tmp/ckwatch-config.bak

echo "==> Extracting $TARBALL"
tar xzf "$TARBALL" -C "$APP" --strip-components=1
cp /tmp/ckwatch-config.bak "$APP/config.toml"

echo "==> Building image"
cd "$APP"
docker build -t ckwatch .

echo "==> Restarting container"
docker rm -f ckwatch 2>/dev/null || true
docker run -d --name ckwatch --restart unless-stopped \
  -p 8081:8080 \
  -v /mnt/user/appdata/ckwatch/data:/data \
  -v /mnt/user/appdata/ckpool/logs:/ckpool/logs:ro \
  ckwatch

sleep 3
echo "==> Status:"
docker ps --filter name=ckwatch --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail 5 ckwatch
echo "==> Done. Dashboard: http://192.168.1.123:8081"
