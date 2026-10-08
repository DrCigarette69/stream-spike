#!/usr/bin/env bash
# Alpha-0 A0.4 — docker compose stack health (8080/1080/9200)
# Alpha-2 A2.4 — SPIKE_IMPL=rust uses docker-compose.rust.yml override
#   → A0.4_COMPOSE_GREEN (python default) or A2.4_COMPOSE_GREEN (rust)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMPL="${SPIKE_IMPL:-python}"
IMPL="$(echo "$IMPL" | tr '[:upper:]' '[:lower:]')"

COMPOSE=(sudo docker compose -f docker-compose.yml)
if [[ "$IMPL" == "rust" ]]; then
  COMPOSE+=(-f docker-compose.rust.yml)
  echo "=== A2.4 compose up (Rust peer+gateway, Python control) ==="
else
  echo "=== A0.4 compose up (Python) ==="
fi

"${COMPOSE[@]}" up -d --build control gateway peer

echo "=== wait health ==="
ok=0
c="" g="" p=""
for i in $(seq 1 90); do
  c=$(curl -sf http://127.0.0.1:8080/health 2>/dev/null || true)
  g=$(curl -sf http://127.0.0.1:1080/health 2>/dev/null || true)
  p=$(curl -sf http://127.0.0.1:9200/health 2>/dev/null || true)
  if echo "$c" | grep -q '"ok"' && echo "$g" | grep -q '"ok"' && echo "$p" | grep -q .; then
    if [[ "$IMPL" == "rust" ]]; then
      if echo "$g" | grep -qE '"impl"[[:space:]]*:[[:space:]]*"rust"'; then
        ok=1
        break
      fi
    else
      ok=1
      break
    fi
  fi
  sleep 1
done

echo "control: $c"
echo "gateway: $g"
echo "peer:    $p"

if [[ "$ok" != 1 ]]; then
  if [[ "$IMPL" == "rust" ]]; then
    echo "A2.4_COMPOSE_RED"
  else
    echo "A0.4_COMPOSE_RED"
  fi
  "${COMPOSE[@]}" ps
  "${COMPOSE[@]}" logs --tail=80
  exit 1
fi

if [[ "$IMPL" == "rust" ]]; then
  echo "A2.4_COMPOSE_GREEN"
else
  echo "A0.4_COMPOSE_GREEN"
fi
