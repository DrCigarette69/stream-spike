#!/usr/bin/env bash
# Alpha-0 A0.4 — docker compose stack health (8080/1080/9200)
# Alpha-2 A2.4 — SPIKE_IMPL=rust uses docker-compose.rust.yml override
# Ports: explicit env (SPIKE_LISTEN, CONTROL_URL, SPIKE_LISTEN_PROXY, ...) wins, else
#   SPIKE_PORT_BASE via scripts/spike_ports.py; unset == 8080/1080/9100/9200 as before.
#   Compose project name defaults to the checkout dir; set COMPOSE_PROJECT_NAME to isolate.
#   → A0.4_COMPOSE_GREEN (python default) or A2.4_COMPOSE_GREEN (rust)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMPL="${SPIKE_IMPL:-python}"
IMPL="$(echo "$IMPL" | tr '[:upper:]' '[:lower:]')"

# Resolve ports once (same precedence as the local asserts) and hand them to compose
# explicitly: plain sudo would drop them from the environment.
PORT_ENV=()
while IFS= read -r kv; do
  [[ -n "$kv" && "$kv" != SPIKE_PORT_BASE=* ]] && PORT_ENV+=("$kv") && export "$kv"
done < <(python3 "$ROOT/scripts/spike_ports.py")
[[ -n "${COMPOSE_PROJECT_NAME:-}" ]] && PORT_ENV+=("COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME")
echo "ports: control=$CONTROL_URL gateway=$GATEWAY_PROXY peer=$PEER_ADMIN relay=$SPIKE_FAKE_RELAY"

COMPOSE=(sudo env "${PORT_ENV[@]}" docker compose -f docker-compose.yml)
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
  c=$(curl -sf "$CONTROL_URL/health" 2>/dev/null || true)
  g=$(curl -sf "$GATEWAY_PROXY/health" 2>/dev/null || true)
  p=$(curl -sf "$PEER_ADMIN/health" 2>/dev/null || true)
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
