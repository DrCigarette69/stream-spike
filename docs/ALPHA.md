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
| A0.4 | `docker-compose up` starts Control/Gateway/Peer | Platform | `/health` on 8080/1080/9200 |
| A0.5 | ALPHA runbook + README pointer | Spec Keeper / Architect | This file + README Status line |

### Alpha-1 (next slice — after A0 green)

| # | Deliverable | Owner | Notes |
|---|-------------|-------|-------|
| A1.1 | Optional Iroh loopback lane (still no public egress) | Platform + Peer | Unpark only after A0 closed |
| A1.2 | Minimal Peer tray/CLI: enroll → ISP ack → kill → resume | Peer + Designer | P1/P4 copy already in fixtures |
| A1.3 | Client quote → match → grace → add-funds mock | Platform + Designer | Mock Stripe only |
| A1.4 | Alpha acceptance checklist video/script | Designer | Fixture walkthrough |

## Hard rules (unchanged)

- Localhost only · brand placeholder `[Brand]` never `Stream` in UX · Control updates **must** stay as small MCP-safe parts (`_zlib_*.txt`), never one 43KB blob
- HOLD production Iroh/community relays · HOLD live KYC/Stripe

## How to run Alpha-0

```bash
cd stream-spike
python3 scripts/run_local_asserts.py all   # → SPIKE_DOD_GREEN
./scripts/demo_alpha.sh                    # same stack + human-readable walkthrough
```
