#!/usr/bin/env bash
# Runs ON the VPS as root (via deploy.sh). Idempotent. Never prints secrets.
set -euo pipefail
D=/opt/stream-pilot
IP_PUBLIC=${IP_PUBLIC:?}; PUBLIC_BIND=${PUBLIC_BIND:?}; BASE=${IP_PUBLIC//./-}.sslip.io
RELAY_HOST=$BASE; ECHO_HOST=echo.$BASE; CONTROL_HOST=control.$BASE
cd "$D"; chmod 0750 "$D"
umask 077
# Secrets (generated here once; never leave the server).
if [ ! -s secrets.env ]; then
  { echo "SPIKE_TICKET_SECRET=$(openssl rand -hex 32)"; echo "SPIKE_ADMIN_TOKEN=$(openssl rand -hex 32)"; } > secrets.env
fi
chmod 0600 secrets.env
# Gateway pilot key: 32 random bytes in the gateway-key volume (0600). Not the committed dev key.
docker volume create stream-pilot_gateway-key >/dev/null
docker run --rm -v stream-pilot_gateway-key:/key debian:bookworm-slim sh -c \
  'umask 077; [ -s /key/gateway.key ] || head -c32 /dev/urandom > /key/gateway.key; chmod 0600 /key/gateway.key; chmod 0700 /key'
KEYF=$(docker volume inspect -f '{{.Mountpoint}}' stream-pilot_gateway-key)/gateway.key
# ed25519 public key from the raw seed (PKCS#8 wrap) = iroh EndpointId (hex). Seed is not printed.
GW_ID=$( (printf '\x30\x2e\x02\x01\x00\x30\x05\x06\x03\x2b\x65\x70\x04\x22\x04\x20'; cat "$KEYF") \
  | openssl pkey -inform DER -pubout -outform DER | tail -c 32 | od -An -tx1 -v | tr -d ' \n')
[ ${#GW_ID} -eq 64 ] || { echo "gateway id derive failed"; exit 1; }
if [ "${ACME_PROD:-0}" = 1 ]; then ACME_CA_DIR=acme-v02.api.letsencrypt.org-directory; else ACME_CA_DIR=acme-staging-v02.api.letsencrypt.org-directory; fi
PEER_IDS=$(cat peer_endpoints.txt 2>/dev/null | tr -d ' \r' | grep -E '^[0-9a-f]{64}$' | paste -sd, - || true)
umask 022
cat > pilot.env <<ENV
IP_PUBLIC=$IP_PUBLIC
PUBLIC_BIND=$PUBLIC_BIND
RELAY_HOST=$RELAY_HOST
ECHO_HOST=$ECHO_HOST
CONTROL_HOST=$CONTROL_HOST
STREAM_RELAY_HOSTNAME=$RELAY_HOST
STREAM_RELAY_ALLOW_ENDPOINTS=$GW_ID${PEER_IDS:+,$PEER_IDS}
SPIKE_RELAY_ALLOW_URL=https://$RELAY_HOST/
SPIKE_GATEWAY_ENDPOINT_ID=$GW_ID
SPIKE_EGRESS_ALLOWLIST=${SPIKE_EGRESS_ALLOWLIST:-$ECHO_HOST:443}
ACME_PROD=${ACME_PROD:-0}
ACME_CA=$( [ "${ACME_PROD:-0}" = 1 ] && echo https://acme-v02.api.letsencrypt.org/directory || echo https://acme-staging-v02.api.letsencrypt.org/directory )
RELAY_HOST_RE=${RELAY_HOST//./\\.}
STREAM_RELAY_TLS_CERT=/caddy/caddy/certificates/${ACME_CA_DIR}/$RELAY_HOST/$RELAY_HOST.crt
STREAM_RELAY_TLS_KEY=/caddy/caddy/certificates/${ACME_CA_DIR}/$RELAY_HOST/$RELAY_HOST.key
ENV
chmod 0644 pilot.env
# Weekly relay restart picks up Caddy's renewed cert (LE 90 d, Caddy renews ~30 d before expiry).
echo '17 4 * * 1 root docker restart stream-pilot-relay-1 >/dev/null 2>&1' > /etc/cron.d/stream-pilot-relay
chmod 0644 /etc/cron.d/stream-pilot-relay
for i in /tmp/stream-pilot-images/*.tar.gz; do [ -e "$i" ] && { gunzip -c "$i" | docker load -q; rm -f "$i"; }; done
docker compose --env-file pilot.env up -d --remove-orphans
echo "GATEWAY_ENDPOINT_ID=$GW_ID"
