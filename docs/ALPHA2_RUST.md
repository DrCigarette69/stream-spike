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
| A2.4 Compose Rust images | Platform |
| A2.5 Control→Rust (optional later) | Platform + Architect |

## Out of Alpha-2

Production egress, community Iroh relays, live Stripe/KYC, rewriting asserts in Rust.
