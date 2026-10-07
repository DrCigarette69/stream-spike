# Stream Alpha cut (locked by Grok Bot — Jeff: ship without waiting)

**Date:** 2026-09-29 CT · **Repo:** stream-spike · **Stance:** stubs / fake Relay · no public egress · no live Stripe

## What "working alpha" means

A **runnable local product demo** Jeff (or any teammate) can start in one command and see the Client↔Peer vertical slice plus P0 fail-closed gates — not production tunnels.

### Alpha-0 (ship now — DoD)

| # | Deliverable | Owner | Proof |
|---|-------------|-------|-------|
| A0.1 | Control loads + stack healthy on `main` | Grok Bot / Platform | `python3 scripts/run_local_asserts.py all` → **SPIKE_DOD_GREEN** |
| A0.2 | Close GitHub #3–#9 (spike complete) | Architect | Issues closed after green re-verify |
| A0.3 | One-command demo | Architect + Platform | `./scripts/demo_alpha.sh` prints Screens 1–3 + gate passes |
| A0.4 | `docker compose up` starts Control/Gateway/Peer | Platform | **A0.4_COMPOSE_GREEN** — `/health` on 8080/1080/9200 (`scripts/compose_health.sh`; host networking — bridge/internal blocked host publish + inter-container TCP on this box) |
| A0.5 | ALPHA runbook + README pointer | Spec Keeper / Architect | This file + README Status line |

### Alpha-1 (next slice — after A0 green)

| # | Deliverable | Owner | Notes |
|---|-------------|-------|-------|
| A1.1 | Optional Iroh loopback lane (still no public egress) | Platform + Peer · Architect asserts | `SPIKE_TRANSPORT=iroh_loopback` · `run_local_asserts.py a11` / `iroh_loopback_smoke.py` → **A1.1_IROH_LOOPBACK_GREEN** · [`IROH_LOOPBACK.md`](IROH_LOOPBACK.md) |
| A1.2 | Minimal Peer tray/CLI: enroll → ISP ack → kill → resume | Peer + Designer · Architect asserts | `peer_alpha1_cli.py` → **PEER_ALPHA1_CLI_GREEN**; `./scripts/demo_alpha.sh` or `run_local_asserts.py a12` / `alpha1` |
| A1.3 | Client quote → match → grace → add-funds mock | Platform + Designer | **DONE** mock Stripe — `a13_mock_topup_smoke.py` → **A1.3_MOCK_TOPUP_GREEN**; `POST /v1/mock/topup`; client-cli `mock-topup` |
| A1.4 | Alpha acceptance checklist video/script | Designer | Fixture walkthrough |

## Hard rules (unchanged)

- Localhost only · brand placeholder `[Brand]` never `Stream` in UX · Control updates **must** stay as small MCP-safe parts (`_zlib_*.txt`), never one 43KB blob
- HOLD production Iroh/community relays · HOLD live KYC/Stripe

## How to run Alpha-0 / Alpha-1

```bash
cd stream-spike
python3 scripts/run_local_asserts.py all   # → SPIKE_DOD_GREEN (fake_relay)
python3 scripts/run_local_asserts.py a11   # → A1.1_IROH_LOOPBACK_GREEN
python3 scripts/run_local_asserts.py a12   # → PEER_ALPHA1_CLI_GREEN
python3 scripts/run_local_asserts.py a13   # → A1.3_MOCK_TOPUP_GREEN
./scripts/demo_alpha.sh                    # DoD + A1.2 (+ A1.1 when smoke on tip)
./scripts/compose_health.sh                # → A0.4_COMPOSE_GREEN (needs Docker)
SPIKE_IMPL=rust ./scripts/compose_health.sh # → A2.4_COMPOSE_GREEN (Rust peer+gateway)
python3 scripts/iroh_loopback_smoke.py     # → A1.1_IROH_LOOPBACK_GREEN (opt-in)
python3 scripts/a13_mock_topup_smoke.py    # → A1.3_MOCK_TOPUP_GREEN (mock Stripe)
```

### Alpha-2 (Rust cut — next)

Peer + Gateway move to **Rust**; Control + asserts stay Python. Locked decision and owners: [`ALPHA2_RUST.md`](ALPHA2_RUST.md).

| # | Deliverable | Owner | Notes |
|---|-------------|-------|-------|
| A2.0 | Rust workspace + `stream-proto` | Grok Bot + Architect | `rust/` crates; MSRV 1.85 |
| A2.1 | Peer Rust parity (ack/egress/kill/ticket) | Peer Engineer | Same env / ports as Python peer |
| A2.2 | Gateway Rust parity (denylist/relay/meter/grace) | Platform Engineer | SOCKS path + fake relay |
| A2.3 | Asserts wire `SPIKE_IMPL=rust` | Stream Architect | DoD green on Rust binaries |
| A2.4 | Compose Rust images | Platform | `SPIKE_IMPL=rust ./scripts/compose_health.sh` → **A2.4_COMPOSE_GREEN**; override `docker-compose.rust.yml` |
| A2.5 | Control→Rust (optional later) | Platform + Architect | After APIs freeze |

```bash
cd rust && ./pin-msrv-deps.sh   # if needed
cd rust && cargo build -p stream-peer -p stream-gateway
python3 scripts/run_local_asserts.py all                 # default python → SPIKE_DOD_GREEN
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py all # → SPIKE_DOD_GREEN
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py a11 # → A1.1_IROH_LOOPBACK_GREEN
SPIKE_IMPL=rust ./scripts/demo_alpha.sh
SPIKE_IMPL=rust ./scripts/compose_health.sh  # → A2.4_COMPOSE_GREEN
```

A2.3 wiring: [`SPIKE_IMPL_RUST.md`](SPIKE_IMPL_RUST.md) · helper `scripts/spike_peer_launch.py`.
