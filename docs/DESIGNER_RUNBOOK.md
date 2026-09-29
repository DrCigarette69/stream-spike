# Designer runbook — fixture IDs + UX asserts

**Owner:** Stream Designer  
**Audience:** anyone greening spike DoD or wiring CLI copy asserts  
**Still stubs only** — no pixel UI, no production tunnels

## Source of truth

| File | Role |
|------|------|
| [`fixtures/designer-exercise.md`](../fixtures/designer-exercise.md) | Human checklist (Screens 1–3 + consent) |
| [`fixtures/screens.json`](../fixtures/screens.json) | Machine map: `screen_id`, `required_copy`, consent gates, forbidden brand strings |
| [`fixtures/codes.json`](../fixtures/codes.json) | Error code → `screen_id` |
| [`P1_P4_COPY.md`](P1_P4_COPY.md) | HARDENING stub copy for [#5](https://github.com/DrCigarette69/stream-spike/issues/5) / [#7](https://github.com/DrCigarette69/stream-spike/issues/7) |
| [`ALPHA1_WALKTHROUGH.md`](ALPHA1_WALKTHROUGH.md) | Alpha-1 Peer tray + Client grace walkthrough checklist |

Wireframes (design outlines, not pixels): `/workspace/stream-design/wireframes-core-flows-v0.md`

## How asserts use fixtures

1. Run `python3 scripts/run_local_asserts.py all` (see [`LOCAL_ASSERTS.md`](LOCAL_ASSERTS.md)).
2. CLI / harness should emit lines like: `UX screen_1_grace code=balance_grace`.
3. Scripts grep `required_copy` strings from `screens.json` against that output.
4. Fail closed if user-facing lines contain any `forbidden_user_facing_substrings` (`Stream`, liability-waive phrases).

Env: `FIXTURES` → path to `fixtures/` (default: repo `fixtures/`).

## Screen IDs (Client stop)

| # | `screen_id` | Code | Issue |
|---|-------------|------|-------|
| 1 | `screen_1_grace` | `balance_grace` | [#9](https://github.com/DrCigarette69/stream-spike/issues/9) |
| 2 | `screen_2_exhausted` | `balance_exhausted` | [#9](https://github.com/DrCigarette69/stream-spike/issues/9) |
| 3 | `screen_3_strict_unavailable` | `strict_unavailable` | [#9](https://github.com/DrCigarette69/stream-spike/issues/9) |

Grace path order is **1 then 2**. Screen 3 is a fixture/P0 toggle, not mid-grace.

## Consent / P0 surface IDs

| `id` | Maps to | Related |
|------|---------|---------|
| `c0_aup` | C0 AUP gate | match precheck |
| `c3_attest_denylist` | C3 denylist refuse copy | [#3](https://github.com/DrCigarette69/stream-spike/issues/3) |
| `p1_isp_ack` | P1 ISP ack (no tunnel without) — HARDENING copy in [`P1_P4_COPY.md`](P1_P4_COPY.md) | [#5](https://github.com/DrCigarette69/stream-spike/issues/5) |
| `p2_consent` | P2 caps / residual risk / thin-market | Peer enroll |
| `p4_kill` | P4 kill → `Sharing paused` — HARDENING copy in [`P1_P4_COPY.md`](P1_P4_COPY.md) | [#7](https://github.com/DrCigarette69/stream-spike/issues/7) |
| `p6_cashout` | P6 mock Connect min **$25** | cashout gate |

## Alpha

- **A0:** Fixtures above match `client-cli` / Peer smoke greps (confirmed 2026-09-29 CT — no copy shift).
- **A1:** Walkthrough checklist → [`ALPHA1_WALKTHROUGH.md`](ALPHA1_WALKTHROUGH.md) · cut plan [`ALPHA.md`](ALPHA.md)

## Designer DoD checklist

Mark in [`fixtures/designer-exercise.md`](../fixtures/designer-exercise.md) when observed against a green harness:

- [ ] Screen 1 then 2 on grace path
- [ ] Screen 3 on `strict_unavailable` fixture
- [ ] P1 / P4 / denylist (C3) / P6 <$25
- [ ] Brand/waive asserts pass

## Related

- [`LOCAL_ASSERTS.md`](LOCAL_ASSERTS.md) · [`PLATFORM_RUNBOOK.md`](PLATFORM_RUNBOOK.md) · [`PEER_RUNBOOK.md`](PEER_RUNBOOK.md) · [`PHASE0_BACKLOG.md`](PHASE0_BACKLOG.md)
