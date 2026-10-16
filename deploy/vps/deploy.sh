#!/usr/bin/env bash
# Box-side redeploy of the Alpha-4 pilot server. Usage (repo root, clean tree at the SHA to ship):
#   VPS=ubuntu@170.9.253.4 VPS_KEY=~/.ssh/vps_jeff IP_PUBLIC=170.9.253.4 PUBLIC_BIND=10.0.0.239 \
#     bash deploy/vps/deploy.sh [--no-build]
set -euo pipefail
: "${VPS:?}" "${VPS_KEY:?}" "${IP_PUBLIC:?}" "${PUBLIC_BIND:?}"
[ -z "${DOCKER_HOST:-}" ] || [[ "$DOCKER_HOST" == unix://* ]] || { echo "DOCKER_HOST must be unset/unix"; exit 1; }
SSH=(ssh -i "$VPS_KEY" -o BatchMode=yes "$VPS")
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --short HEAD)
if [ "${1:-}" != "--no-build" ]; then
  docker build -q -f deploy/vps/Dockerfile.relay -t stream-relay:pilot rust/
  docker build -q -f deploy/vps/Dockerfile.gateway -t stream-gateway:pilot rust/
  docker build -q -f control/Dockerfile -t stream-control:pilot .
fi
"${SSH[@]}" 'sudo mkdir -p /opt/stream-pilot/fixtures && mkdir -p /tmp/stream-pilot-images'
for img in relay gateway control; do
  docker save "stream-$img:pilot" | gzip -1 | "${SSH[@]}" "cat > /tmp/stream-pilot-images/$img.tar.gz"
done
tar -C deploy/vps -cf - docker-compose.yml nginx.conf Caddyfile server_setup.sh \
  | "${SSH[@]}" 'sudo tar -C /opt/stream-pilot -xf -'
tar -C fixtures -cf - denylist.seed.json | "${SSH[@]}" 'sudo tar -C /opt/stream-pilot/fixtures -xf -'
"${SSH[@]}" "echo $SHA | sudo tee /opt/stream-pilot/DEPLOYED_SHA >/dev/null; sudo env IP_PUBLIC=$IP_PUBLIC PUBLIC_BIND=$PUBLIC_BIND ACME_PROD=${ACME_PROD:-0} bash /opt/stream-pilot/server_setup.sh"
