#!/usr/bin/env bash
# Alpha-0 A0.4 — docker compose stack health (8080/1080/9200)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== A0.4 compose up ==="
sudo docker compose up -d --build control gateway peer

echo "=== wait health ==="
ok=0
for i in $(seq 1 60); do
  c=$(curl -sf http://127.0.0.1:8080/health 2>/dev/null || true)
  g=$(curl -sf http://127.0.0.1:1080/health 2>/dev/null || true)
  p=$(curl -sf http://127.0.0.1:9200/health 2>/dev/null || true)
  if echo "$c" | grep -q '"ok"' && echo "$g" | grep -q '"ok"' && echo "$p" | grep -q .; then
    ok=1
    break
  fi
  sleep 1
done

echo "control: $c"
echo "gateway: $g"
echo "peer:    $p"

if [[ "$ok" != 1 ]]; then
  echo "A0.4_COMPOSE_RED"
  sudo docker compose ps
  sudo docker compose logs --tail=80
  exit 1
fi

echo "A0.4_COMPOSE_GREEN"
