# Stream Alpha-2 — Rust cut (locked)

**Date:** 2026-10-06 CT · **Directive:** Jeff — next cut; Rust where it makes sense · no waiting

## Decision

| Component | Language | Why |
|-----------|----------|-----|
| **Peer agent** | **Rust** | Tunnel, egress floor, kill, ALPN/`AUTH_TICKET`; Iroh-native later; shippable binary |
| **Gateway** | **Rust** | SOCKS/proxy path, denylist mid-stream, fake relay / loopback, metering flush |
| **stream-proto** | **Rust** | Shared ALPN + ticket wire format |
| **Control** | **Python (keep)** | Match/ledger/admin iterate fast; same HTTP API. Rust/`axum` later once APIs freeze |
| **client-cli + asserts** | **Python (keep)** | DoD harness stays; talks to same ports/env |
| **Designer fixtures** | unchanged | Copy/greps unchanged |

## Parity contract

Rust Peer/Gateway **must** pass existing DoD with same env vars (`SPIKE_*`, `CONTROL_URL`, ports 1080/9100/9200). Proof:

```bash
SPIKE_IMPL=rust ./scripts/demo_alpha.sh
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py all   # → SPIKE_DOD_GREEN
# → SPIKE_DOD_GREEN + A1.* green (A2.3 wired)
```

Python Peer/Gateway remain as `SPIKE_IMPL=python` fallback until Rust is default.

## Hard rules

- Still stubs / localhost / no public egress / no live Stripe
- `[Brand]` not `Stream` in UX
- **No big MCP `push_files` blobs** — small files / `git` CLI preferred for Rust trees
- Iroh: loopback only until Control APIs + Rust Peer dial are green

## Owners

| Slice | Owner |
|-------|-------|
| A2.0 workspace + proto | Grok Bot (scaffold) + Architect |
| A2.1 Peer Rust parity (ack/egress/kill/ticket) | Peer Engineer |
| A2.2 Gateway Rust parity (denylist/relay/meter/grace) | Platform Engineer |
| A2.3 Asserts wire `SPIKE_IMPL=rust` | Stream Architect |
| A2.4 Compose Rust images | Platform — **DONE** (`docker-compose.rust.yml` · `SPIKE_IMPL=rust ./scripts/compose_health.sh` → **A2.4_COMPOSE_GREEN**) |
| A2.5 Control→Rust (optional later) | Platform + Architect |

## DONE — 2026-10-08 CT

Epic done criterion met on tip `5428be3` (rustc 1.85.1, pinned `Cargo.lock`; Control stays Python):

- `SPIKE_IMPL=rust python3 scripts/run_local_asserts.py all` → **SPIKE_DOD_GREEN** (default Python also green)
- `SPIKE_IMPL=rust … a11` → **A1.1_IROH_LOOPBACK_GREEN** · `a12` → **PEER_ALPHA1_CLI_GREEN** · `a13` → **A1.3_MOCK_TOPUP_GREEN**
- `SPIKE_IMPL=rust bash scripts/demo_alpha.sh` → all four markers green (gateway `"impl":"rust"`)
- `SPIKE_IMPL=rust bash scripts/compose_health.sh` → **A2.4_COMPOSE_GREEN**

A2.1–A2.4 done. A2.5 (Control→Rust) stays backlog.

Known nits (follow-up): `scripts/*.sh` committed 100644 (use `bash scripts/…` until chmod +x lands); `rust/Cargo.lock` not yet tracked — fresh resolve pulls `idna_adapter 1.2.2` (needs rustc 1.86); commit the pinned lock (`idna_adapter 1.2.0`).

## Out of Alpha-2

Production egress, community Iroh relays, live Stripe/KYC, rewriting asserts in Rust.
