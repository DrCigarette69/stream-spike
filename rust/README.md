# Stream Spike — Rust workspace (Alpha-2)

Workspace members: `stream-proto`, `stream-peer`, `stream-gateway`.

- **MSRV:** rustc 1.85
- **Pinned deps:** axum 0.7.9, encoding_rs 0.8.35, idna_adapter 1.2.0, icu_* 1.5  
  If `Cargo.lock` is missing: `cd rust && ./pin-msrv-deps.sh` (or `./restore_cargo_lock.sh`), then commit the lock.
- **Alpha-3 iroh (opt-in):** workspace dep `iroh = "=0.95.1"` (`default-features = false`), optional only.
  Features: `stream-gateway/iroh`, `stream-peer/iroh_local`. The default build does not compile iroh.
  RustCrypto rc pins required by iroh 0.95.1 (`ed25519-dalek 3.0.0-pre.1`), applied in `pin-msrv-deps.sh`:
  `ed25519 3.0.0-rc.2`, `pkcs8 0.11.0-rc.8`, `spki 0.8.0-rc.4`, `der 0.8.0-rc.10`.
  `Cargo.lock` has one owner (Platform); commit it with git, not MCP. See `docs/ALPHA3_IROH.md`.

```bash
cd rust && cargo check
cargo build -p stream-gateway
cargo build -p stream-peer
# Alpha-3 opt-in (iroh 0.95.1; run nodes only inside a no-default-route netns)
cargo build --locked -p stream-gateway --features iroh
cargo build --locked -p stream-peer --features iroh_local
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

## Compose images (A2.4)

Dockerfiles: `crates/stream-gateway/Dockerfile`, `crates/stream-peer/Dockerfile` (release binary + curl healthcheck).

```bash
# from repo root
SPIKE_IMPL=rust ./scripts/compose_health.sh   # → A2.4_COMPOSE_GREEN
# equivalent:
sudo docker compose -f docker-compose.yml -f docker-compose.rust.yml up -d --build
```

Control remains the Python image from `docker-compose.yml`. Do not commit `target/`.
