# Stream spike harness (local)

**Status:** SPIKE_DOD_GREEN (local asserts) · stubs only · no public egress · no live Stripe  
**Plan:** `/workspace/product-docs/stream-spike-plan-v0.md` · backlog: [`docs/PHASE0_BACKLOG.md`](docs/PHASE0_BACKLOG.md)  
**Default transport:** fake Relay · ALPN `stream/tunnel/1` + `AUTH_TICKET`

## DoD (no Docker)

```bash
./scripts/spike_grace_stop.sh    # or: python3 scripts/run_local_asserts.py grace
./scripts/spike_p0_gates.sh      # or: python3 scripts/run_local_asserts.py gates
python3 scripts/run_local_asserts.py all   # both → prints SPIKE_DOD_GREEN
```

Also: `python3 scripts/platform_smoke.py` · `python3 scripts/peer_smoke.py`

Runbooks: [`docs/LOCAL_ASSERTS.md`](docs/LOCAL_ASSERTS.md) · [`docs/PLATFORM_RUNBOOK.md`](docs/PLATFORM_RUNBOOK.md) · Peer smoke notes in `scripts/peer_smoke.py`

## Layout

| Path | Owner |
|------|-------|
| `control/` `gateway/` | Platform Engineer |
| `peer/` | Peer Engineer |
| `client-cli/` `scripts/run_local_asserts.py` | Stream Architect |
| [`fixtures/`](fixtures/) | Stream Designer |

## Designer fixtures (screen IDs)

Harness asserts UX by emitting codes that map to these IDs. Source of truth:

| File | Purpose |
|------|---------|
| [`fixtures/designer-exercise.md`](fixtures/designer-exercise.md) | Human checklist — Screens 1–3 + consent/P0 surfaces |
| [`fixtures/screens.json`](fixtures/screens.json) | Machine map: `screen_id`, `required_copy`, consent gates |
| [`fixtures/codes.json`](fixtures/codes.json) | Error code → screen_id |

### Client stop screens (grace + strict)

| # | `screen_id` | Code | Required copy (grep) | Issue |
|---|-------------|------|----------------------|-------|
| 1 | [`screen_1_grace`](fixtures/screens.json) | `balance_grace` | `Balance hit zero` · `Grace left` | [#9](https://github.com/DrCigarette69/stream-spike/issues/9) |
| 2 | [`screen_2_exhausted`](fixtures/screens.json) | `balance_exhausted` | `Session stopped` · `Add funds` | [#9](https://github.com/DrCigarette69/stream-spike/issues/9) |
| 3 | [`screen_3_strict_unavailable`](fixtures/screens.json) | `strict_unavailable` | `Nearby exits aren’t available here right now` | [#9](https://github.com/DrCigarette69/stream-spike/issues/9) |

CLI may print `UX <screen_id> code=<code>` for script greps.

### Consent / P0 surfaces

| `id` | Screen | Gate / assert | Related issue |
|------|--------|---------------|---------------|
| `c0_aup` | C0 | `aup_accepted` before match | — |
| `c3_attest_denylist` | C3 | denylist refuse copy includes `blocked` | [#3](https://github.com/DrCigarette69/stream-spike/issues/3) |
| `p1_isp_ack` | P1 | `isp_ack_version` set or no tunnel | [#5](https://github.com/DrCigarette69/stream-spike/issues/5) |
| `p2_consent` | P2 | caps / kill / residual risk / thin-market strings | — |
| `p4_kill` | P4 | mid-stream kill → `Sharing paused` | [#7](https://github.com/DrCigarette69/stream-spike/issues/7) |
| `p6_cashout` | P6 | mock withdraw blocked if accrued < **$25** | — |

**Brand rule:** user-facing output must use `[Brand]` only. Asserts fail on substring `Stream` or liability-waive phrases — see `forbidden_user_facing_substrings` in [`fixtures/screens.json`](fixtures/screens.json).

Wireframes (design, not pixels): `/workspace/stream-design/wireframes-core-flows-v0.md`

## Hard rules

- Localhost only · no public internet · no live Stripe/KYC
