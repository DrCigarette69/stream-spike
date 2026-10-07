# Peer Alpha-2 (A2.1) -- Rust Peer parity

**Status:** A2.1 implemented | stubs / fake Relay / iroh loopback only | no public egress  
**Binary:** `rust/target/debug/stream-peer` (crate `stream-peer`)  
**Parity target:** Python `peer/main.py` (HARDENING + A1.1)

## How to run

```bash
cd rust && cargo build -p stream-peer && cargo test -p stream-peer

# Same env as Python peer:
export CONTROL_URL=http://127.0.0.1:8080
export SPIKE_PEER_ADMIN=127.0.0.1:9200
export SPIKE_FAKE_RELAY_DIAL=127.0.0.1:9100
export SPIKE_ISP_ACK_VERSION=v1
export SPIKE_PEER_ID=peer_demo
export SPIKE_ENDPOINT_ID=iroh_ep_demo_001
./rust/target/debug/stream-peer
```

Smokes (`SPIKE_IMPL=rust` → Rust peer + gateway; Control stays Python):

```bash
SPIKE_IMPL=rust python3 scripts/peer_smoke.py
# -> PEER_SMOKE_GREEN + PEER_HARDENING_GREEN

SPIKE_IMPL=rust python3 scripts/peer_alpha1_cli.py
# -> PEER_ALPHA1_CLI_GREEN

SPIKE_IMPL=rust python3 scripts/iroh_loopback_smoke.py
# -> A1.1_IROH_LOOPBACK_GREEN
```

Default `SPIKE_IMPL` / unset still launches Python `peer/` + `gateway/`. A2.3: asserts / `demo_alpha.sh` honor `SPIKE_IMPL` via `scripts/spike_peer_launch.py`.

## Parity table

| Area | Python | Rust A2.1 |
|------|--------|-----------|
| Admin HTTP | `:9200` routes | Same paths + JSON keys |
| P1 / P4 UX | `P1_UX` / `P4_UX` | `ux.rs` -- same greps; `[Brand]` not Stream |
| Egress floor | `egress_denied` | `egress.rs` |
| Relay dial | fake_relay / iroh_loopback | `relay.rs` -- refuse non-loopback on iroh* |
| AUTH_TICKET | Control verify + ALPN + endpoint | `ticket.rs` + `frames.rs` |
| Kill / resume | control kill + pause | same |
| Heartbeat | `POST /v1/peers/heartbeat` | same |

## Crate layout

`rust/crates/stream-peer/src/`: `main`, `config`, `ux`, `egress`, `state`, `ticket`, `frames`, `relay`, `admin`.  
Do not edit `stream-gateway` here (Platform A2.2). Shared ALPN in `stream-proto`.
