# Alpha-4 pilot host — Oracle Cloud Always Free + catnet.world

**Status:** Host and domain locked (Jeff, 2026-10-11).  
**Domain:** `catnet.world` (Namecheap).  
**Relay URL:** `https://catnet.world/` (exact match after normalisation for `SPIKE_RELAY_ALLOW_URL`).

This is the publicly reachable VPS for the self-hosted Iroh relay + Control + Gateway. Gateway client-facing and admin listeners stay on `127.0.0.1` only; reach them via SSH tunnel.

## What the free tier gives you

Oracle Always Free (persists after the 30-day $300 trial):

- Ampere A1 ARM: up to 2 OCPUs / 12 GB total
- 2× AMD micro (1/8 OCPU / 1 GB)
- 200 GB block storage
- 10 TB/month outbound

A 1 OCPU / 6 GB A1 instance is plenty for the pilot.

## 1. Create the instance

1. OCI console → Compute → Instances → Create instance.
2. Image: Canonical Ubuntu 24.04 aarch64 (Always Free eligible).
3. Shape: Ampere → VM.Standard.A1.Flex, Always Free eligible. Start with 1 OCPU / 6 GB.
4. Networking: public subnet, **Assign a public IPv4 address** = Yes.
5. SSH key: upload or generate; save the private key.
6. Note the public IP.

## 2. Open ports (two layers)

**OCI Security List** (VCN → Security Lists → Default → Add Ingress Rules):

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | your home IP/32 | SSH |
| 80 | TCP | 0.0.0.0/0 | ACME + HTTP redirect |
| 443 | TCP | 0.0.0.0/0 | HTTPS relay |
| 7842 | UDP | 0.0.0.0/0 | QUIC address discovery (optional) |

**On the instance:**

```bash
sudo apt update && sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 7842/udp
sudo ufw enable
```

## 3. DNS (Namecheap)

Advanced DNS → A record:

- Host: `@` (or `relay` if you prefer a subdomain)
- Value: the Oracle public IP
- TTL: 5–30 min

Confirm with `dig catnet.world` before starting the relay.

## 4. Install and run the relay

`stream-relay` currently refuses TLS mode (`relay_tls_todo`) until the deploy slice enables it. For the pilot you can:

**Option A (recommended today):** run the upstream `iroh-relay` binary (same 0.95.1 series) with built-in Let's Encrypt. See `relay.toml.example`.

**Option B:** wait for the small change that lets `stream-relay` accept `STREAM_RELAY_MODE=tls` + cert paths, then use the same config.

Minimal install (Option A):

```bash
# on the A1 instance (aarch64)
curl -sL https://github.com/n0-computer/iroh/releases/download/v0.95.1/iroh-relay-aarch64-unknown-linux-gnu.tar.gz | tar xz
# or build from the pinned lock in this repo once TLS mode is enabled
mkdir -p /opt/relay/certs
cp relay.toml.example /opt/relay/relay.toml
# edit contact email if needed
/opt/relay/iroh-relay --config-path /opt/relay/relay.toml
```

The systemd unit `stream-relay.service` assumes the binary is at `/usr/local/bin/iroh-relay` (or adjust).

Once the cert is issued, `curl -I https://catnet.world/` should succeed.

## 5. Control + Gateway on the same box

```bash
# from a clone of the repo
SPIKE_IMPL=rust docker compose -f docker-compose.yml -f docker-compose.rust.yml up -d
```

Bind Gateway proxy and admin to `127.0.0.1` only. Reach them with:

```bash
ssh -L 8080:127.0.0.1:8080 -L 1080:127.0.0.1:1080 ubuntu@<public-ip>
```

Set `SPIKE_RELAY_ALLOW_URL=https://catnet.world/` so tickets and the gateway accept only this relay.

## 6. Pilot env (A4.9, on the two home devices)

```bash
SPIKE_TRANSPORT=iroh_pilot
SPIKE_PUBLIC_EGRESS=1
SPIKE_RELAY_ALLOW_URL=https://catnet.world/
SPIKE_EGRESS_ALLOWLIST=echo.catnet.world:443,example.com:443
# plus ISP ack, per-device key, etc.
```

## Notes

- Stay inside Always Free limits.
- Idle reclamation is possible if utilization stays very low for 7 days; short pilot is low risk.
- Free ARM capacity can be tight — try another availability domain if the shape is unavailable.
- Do not expose Gateway proxy or admin ports publicly.
