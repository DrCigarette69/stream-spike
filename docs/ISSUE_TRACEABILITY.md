# Phase 0 issue ↔ spike gate traceability

**Verified:** 2026-09-28 CT · `python3 scripts/run_local_asserts.py all` → `SPIKE_DOD_GREEN`  
**Rule:** stubs / fail-closed only — **no production tunnels**, no public egress, no live Stripe/KYC.

| Issue | Gate / DoD cmd | Primary code | Owners (next) |
|-------|----------------|--------------|---------------|
| [#3](https://github.com/DrCigarette69/stream-spike/issues/3) denylist | `gate-denylist` | `control/` denylist + admin · `gateway/` assign check · `peer/` egress dual | Platform → Peer |
| [#4](https://github.com/DrCigarette69/stream-spike/issues/4) freeze | `gate-freeze` | `control/` `POST /v1/admin/accounts/{id}/freeze` · match/session reject | Platform |
| [#5](https://github.com/DrCigarette69/stream-spike/issues/5) ISP ack | `gate-peer-ack` | `peer/` enroll gate · Designer P1 copy | Peer → Designer |
| [#6](https://github.com/DrCigarette69/stream-spike/issues/6) egress floor | `gate-peer-egress` | `peer/main.py` `egress_denied` | Peer |
| [#7](https://github.com/DrCigarette69/stream-spike/issues/7) kill switch | `gate-peer-kill` | `peer/` kill + delist · Designer P4 | Peer → Designer |
| [#8](https://github.com/DrCigarette69/stream-spike/issues/8) AUTH_TICKET | `gate-auth-ticket` | mint in `control/` · verify in `peer/` · `docs/FAKE_RELAY_PROTOCOL.md` | Platform → Peer |
| [#9](https://github.com/DrCigarette69/stream-spike/issues/9) grace Screens 1–3 | `grace-stop` · `assert-grace-ledger` · `gate-strict-unavailable` | control grace/ledger · peer teardown-only · `fixtures/screens.json` · `client-cli/` | Platform · Peer · Designer · Architect (asserts) |

Epic: [#2](https://github.com/DrCigarette69/stream-spike/issues/2) · Backlog map: [`PHASE0_BACKLOG.md`](./PHASE0_BACKLOG.md)

## Spike vs Phase 0 hardening

| Status | Meaning |
|--------|---------|
| **SPIKE_GREEN** | Fail-closed stub + local assert passes |
| **HARDENING** | Versioned lists, real abuse hooks, copy legal review, self-hosted relay ops — still no open internet Peer egress in Phase 0 until threat-model gates say go |

Optional Iroh lane stays **parked** until #3–#9 are claimed for HARDENING.
