# Peer Alpha-1 status

**Owner:** Peer Engineer · **Date:** 2026-09-29 CT  
**Repo tip baseline:** Alpha-0 closed (`A0.4_COMPOSE_GREEN` / `SPIKE_DOD_GREEN`)

## Done

| Slice | Status | Proof |
|-------|--------|-------|
| **A1.2** Minimal Peer tray/CLI: enroll → ISP ack → kill → resume | **done** | `python3 scripts/peer_alpha1_cli.py` → **PEER_ALPHA1_CLI_GREEN** |

HARDENING prerequisites already on `main` (bb57d17-era `peer/main.py`): P1/P4 Designer copy, `p1_ack_gate`, kill/resume, egress floor. A1.2 is the one-command walkthrough + runbook pointer — stubs only, `[Brand]` never Stream in UX.

## Parked

| Slice | Status | Notes |
|-------|--------|-------|
| **A1.1** Iroh loopback lane | **parked** | Owns with Platform + Peer later. Keep fake Relay. Do **not** implement real Iroh here. |

## Related

- [`ALPHA.md`](ALPHA.md) · [`ALPHA1_WALKTHROUGH.md`](ALPHA1_WALKTHROUGH.md) A1.2 · [`PEER_RUNBOOK.md`](PEER_RUNBOOK.md) · [`P1_P4_COPY.md`](P1_P4_COPY.md)
