# Peer Alpha-1 status

**Owner:** Peer Engineer · **Date:** 2026-09-29 CT  
**Repo tip baseline:** Alpha-0 closed (`A0.4_COMPOSE_GREEN` / `SPIKE_DOD_GREEN`)

## Done

| Slice | Status | Proof |
|-------|--------|-------|
| **A1.2** Minimal Peer tray/CLI: enroll → ISP ack → kill → resume | **done** | `python3 scripts/peer_alpha1_cli.py` → **PEER_ALPHA1_CLI_GREEN** |
| **A1.1** Peer dial-side Iroh loopback guard | **done** | Peer refuses non-loopback dial when `SPIKE_TRANSPORT=iroh_loopback` (status `last_error=iroh_loopback_refuses_non_loopback_dial:…`, never `create_connection`); default remains **fake_relay** / `SPIKE_FAKE_RELAY_DIAL` `:9100`. Full lane proof: Platform `python3 scripts/iroh_loopback_smoke.py` → **A1.1_IROH_LOOPBACK_GREEN** when smoke lands on `main`. |

HARDENING prerequisites already on `main` (bb57d17-era `peer/main.py`): P1/P4 Designer copy, `p1_ack_gate`, kill/resume, egress floor. A1.2 is the one-command walkthrough + runbook pointer — stubs only, `[Brand]` never Stream in UX.

A1.1 Peer dial: `SPIKE_TRANSPORT=iroh_loopback` (or `iroh`) → dials `SPIKE_IROH_LOOPBACK_DIAL` default `127.0.0.1:9101`; fail-closed before connect if host ∉ `{127.0.0.1, ::1, localhost}`. Same HELLO/AUTH_TICKET/ALPN frames — **not** real Iroh · no public egress · UX unchanged (`[Brand]` only). See [`IROH_LOOPBACK.md`](IROH_LOOPBACK.md).

## Parked

_(none for Peer A1.1 dial / A1.2)_

## Related

- [`ALPHA.md`](ALPHA.md) · [`IROH_LOOPBACK.md`](IROH_LOOPBACK.md) · [`ALPHA1_WALKTHROUGH.md`](ALPHA1_WALKTHROUGH.md) A1.2 · [`PEER_RUNBOOK.md`](PEER_RUNBOOK.md) · [`P1_P4_COPY.md`](P1_P4_COPY.md)
