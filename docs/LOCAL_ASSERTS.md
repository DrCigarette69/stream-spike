# Local assert + smoke runbook (Platform + Peer)

**Audience:** anyone greening the spike DoD without Docker  
**Owners:** Platform (`control/` `gateway/` `platform_smoke.py`) · Peer (`peer/` `peer_smoke.py`) · Architect (`run_local_asserts.py` / client-cli)

## One command (DoD)

```bash
git clone git@github.com:DrCigarette69/stream-spike.git
cd stream-spike
python3 scripts/run_local_asserts.py all
```

Success line: **`SPIKE_DOD_GREEN`**

### What it asserts

**Grace path**

1. US/City match → session → tunnel (AUTH_TICKET)
2. Force $0 → `balance_grace` / Screen 1 copy
3. ≤5 MB or ≤15 s → `balance_exhausted` / Screen 2 copy
4. Ledger: `peer_payout` includes grace bytes; customer balance not negative; Peer did not early-cut

**P0 gates** (all fail closed)

| Gate | Script / CLI | Issue |
|------|--------------|-------|
| Denylist | `gate-denylist` | #3 |
| Freeze | `gate-freeze` | #4 |
| Peer ISP ack | `gate-peer-ack` | #5 |
| Peer egress floor | `gate-peer-egress` | #6 |
| Peer kill | `gate-peer-kill` | #7 |
| AUTH_TICKET / ALPN | `gate-auth-ticket` | #8 |
| Strict Screen 3 | fixture `strict_unavailable` | #9 |
| AUP | match blocked until accepted | — |
| Cashout $25 | mock Connect below min | — |

Designer fixture IDs: [`../fixtures/screens.json`](../fixtures/screens.json) · checklist [`../fixtures/designer-exercise.md`](../fixtures/designer-exercise.md) · runbook [`DESIGNER_RUNBOOK.md`](DESIGNER_RUNBOOK.md) · P1/P4 HARDENING copy [`P1_P4_COPY.md`](P1_P4_COPY.md)

## Component smokes

```bash
python3 scripts/platform_smoke.py   # → PLATFORM_SMOKE_GREEN
python3 scripts/peer_smoke.py       # → PEER_SMOKE_GREEN
```

Use these when debugging one side before full DoD.

## Ports

| Port | Service |
|------|---------|
| 8080 | Control |
| 1080 | Gateway admin HTTP (`/gw/*`) |
| 9100 | Fake Relay (Peer dials in) |
| 9200 | Peer admin (`/peer/kill`, health) |

## Env knobs (common)

| Var | Default / notes |
|-----|-----------------|
| `SPIKE_TICKET_SECRET` | Must match control mint + Peer verify |
| `SPIKE_ISP_ACK_VERSION` | Empty → Peer cannot tunnel |
| `SPIKE_HOST_TIER` | `casual` / `always_on` |
| `SPIKE_DENYLIST` | Path to `fixtures/denylist.seed.json` |
| `FIXTURES` | Path to `fixtures/` for CLI copy asserts |


## SPIKE_IMPL (A2.3)

| Value | Behavior |
|-------|----------|
| unset / `python` | Python `peer/` + `gateway/` (default; DoD must stay green) |
| `rust` | `rust/target/debug/stream-peer` + `stream-gateway`; Control stays Python |

```bash
python3 scripts/run_local_asserts.py all
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py all
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py a11
```

Helper: [`scripts/spike_peer_launch.py`](../scripts/spike_peer_launch.py). Details: [`SPIKE_IMPL_RUST.md`](SPIKE_IMPL_RUST.md).

## Related docs

- [`PLATFORM_RUNBOOK.md`](PLATFORM_RUNBOOK.md) — Platform endpoints + tips  
- [`PEER_RUNBOOK.md`](PEER_RUNBOOK.md) — Peer admin, ack/egress/kill, smoke  
- [`DESIGNER_RUNBOOK.md`](DESIGNER_RUNBOOK.md) — fixture screen IDs + UX copy asserts  
- [`P1_P4_COPY.md`](P1_P4_COPY.md) — HARDENING stub copy for #5 / #7  
- [`FAKE_RELAY_PROTOCOL.md`](FAKE_RELAY_PROTOCOL.md) — HELLO / AUTH_TICKET / BYTES / CLOSE  
- [`PHASE0_BACKLOG.md`](PHASE0_BACKLOG.md) — GitHub issue map

Still **stubs only** — no production tunnels.

## Alpha-1 Iroh loopback (A1.1)

Opt-in; default DoD stays on fake_relay. See [`IROH_LOOPBACK.md`](IROH_LOOPBACK.md).

```bash
python3 scripts/iroh_loopback_smoke.py      # → A1.1_IROH_LOOPBACK_GREEN
python3 scripts/run_local_asserts.py a11    # same via assert runner
```

## Alpha-1 Peer CLI (A1.2)

```bash
python3 scripts/peer_alpha1_cli.py          # → PEER_ALPHA1_CLI_GREEN
python3 scripts/run_local_asserts.py a12    # same via assert runner
python3 scripts/run_local_asserts.py alpha1 # A0 DoD then A1.1 + A1.2
./scripts/demo_alpha.sh                     # DoD + A1.1 + A1.2 walkthrough
```
