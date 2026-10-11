# A4.5 Peer packaging notes (two home devices)

**Relay URL:** `https://catnet.world/`  
**Build:** `cargo build --locked --release -p stream-peer --features iroh_pilot` (or the Docker image from `rust/crates/stream-peer/Dockerfile` with the feature).

## Per-device setup

1. Generate or copy a unique 32-byte key: `SPIKE_IROH_KEY_PATH=/etc/stream-peer/key` (0600).
2. Config / env file (example):

```bash
SPIKE_TRANSPORT=iroh_pilot
SPIKE_PUBLIC_EGRESS=1
SPIKE_RELAY_ALLOW_URL=https://catnet.world/
SPIKE_EGRESS_ALLOWLIST=echo.catnet.world:443
SPIKE_ISP_ACK_VERSION=v1
SPIKE_IROH_KEY_PATH=/etc/stream-peer/key
SPIKE_PEER_ID=peer-home-1   # unique per device
```

3. On-device self-test: `stream-peer --selftest` (when the flag exists) or the health endpoint after start.
4. Consent order with public egress on: P1 ISP ack → P2 consent → P9 allowlist note → dial.

## Packaging options

- Native binary (aarch64 or x86_64 depending on the device).
- Docker image built with `--features iroh_pilot` (pilot builds never enable `a4_local`).

The pilot build refuses to start unless `SPIKE_TRANSPORT=iroh_pilot` (`transport_not_pilot`).
