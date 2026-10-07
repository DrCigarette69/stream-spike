# Stream Spike — Rust workspace (Alpha-2)

Workspace members: `stream-proto`, `stream-peer`, `stream-gateway`.

- **MSRV:** rustc 1.85
- **Pinned deps:** axum 0.7.9, encoding_rs 0.8.35, idna_adapter 1.2.0, icu_* 1.5  
  If `Cargo.lock` is missing: `cd rust && ./pin-msrv-deps.sh` (or `./restore_cargo_lock.sh`), then commit the lock.

```bash
cd rust && cargo check
cargo build -p stream-gateway
cargo build -p stream-peer
```

## `SPIKE_IMPL=rust` — Gateway (A2.2)

Binary: `target/debug/stream-gateway`

| Env | Default |
|-----|---------|
| `CONTROL_URL` | `http://127.0.0.1:8080` |
| `SPIKE_LISTEN_PROXY` | `127.0.0.1:1080` |
| `SPIKE_FAKE_RELAY` | `127.0.0.1:9100` |
| `SPIKE_TRANSPORT` | `fake_relay` |
| `SPIKE_IROH_LOOPBACK` | `127.0.0.1:9101` (when transport is `iroh` / `iroh_loopback`) |

HTTP admin mirrors Python gateway: `/health`, `/gw/peers`, `/gw/start`, `/gw/stop`, `/gw/inject_bad_ticket`.  
Fake relay is NDJSON (`docs/FAKE_RELAY_PROTOCOL.md`). Keep Python `gateway/` as fallback.

Asserts / `demo_alpha.sh` wiring: **A2.3** (Architect).
