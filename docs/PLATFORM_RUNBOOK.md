# Platform runbook — local assert + smoke

**Owner:** Platform Engineer · **Repo:** https://github.com/DrCigarette69/stream-spike  
**Scope:** `control/` + `gateway/` stubs · no production tunnels · no live Stripe

## Prerequisites

- Python 3.11+ (stdlib only; no pip deps)
- Ports free locally: `8080` (control), `1080` (gateway HTTP), `9100` (fake Relay)
- From repo root

## Full DoD (preferred)

Starts control + gateway + peer, then runs client-cli scenarios:

```bash
python3 scripts/run_local_asserts.py all
# expect: SPIKE_DOD_GREEN
```

Slices:

```bash
python3 scripts/run_local_asserts.py grace   # Screen 1→2 + ledger
python3 scripts/run_local_asserts.py gates   # P0 fail-closed
```

Shell wrappers (same runner):

```bash
./scripts/spike_grace_stop.sh
./scripts/spike_p0_gates.sh
```

## Platform-only smoke (no Peer binary required)

Uses a mini Peer inside the script to exercise control+gateway:

```bash
python3 scripts/platform_smoke.py
# expect: PLATFORM_SMOKE_GREEN
```

Covers: match → session → AUTH_TICKET tunnel → force grace → exhaust → ledger `peer_payout` + non-negative balance · denylist refuse · `strict_unavailable` fixture · freeze · mock cashout <$25 · bad ticket reject.

## Manual control / gateway bring-up

```bash
export SPIKE_DB=/tmp/spike.sqlite
export SPIKE_LISTEN=127.0.0.1:8080
export SPIKE_TICKET_SECRET=dev-only-change-me
export SPIKE_DENYLIST=$PWD/fixtures/denylist.seed.json
python3 -u control/main.py &

export CONTROL_URL=http://127.0.0.1:8080
export SPIKE_LISTEN_PROXY=127.0.0.1:1080
export SPIKE_FAKE_RELAY=127.0.0.1:9100
python3 -u gateway/main.py &

curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:1080/health
```

Peer must dial fake Relay with `HELLO` + non-empty `isp_ack_version` — see [`FAKE_RELAY_PROTOCOL.md`](FAKE_RELAY_PROTOCOL.md).

## Key Platform endpoints (spike)

| Path | Role |
|------|------|
| `POST /v1/match` | Quote + capacity; codes `strict_unavailable`, `capacity_*`, `kyc_insufficient` |
| `POST /v1/sessions` | Mint session + signed `ticket_json` (AUTH_TICKET) |
| `POST /v1/sessions/{label}/force-zero` | Enter grace (`balance_grace`) |
| `POST /v1/usage/flush` | Gateway meter; may emit `balance_exhausted` + settle |
| `POST /v1/dest/check` | Denylist gate |
| `POST /v1/admin/accounts/{id}/freeze` | Freeze + stop in-flight |
| `POST /v1/admin/denylist` | Replace denylist seed |
| `POST /v1/admin/fixtures` | Toggle `strict_unavailable` / capacity fixtures |
| `POST /v1/mock/cashout` | 403 if accrued <$25 |
| `POST /gw/start` (gateway) | Denylist + AUTH_TICKET open + meter loop |
| `POST /gw/inject_bad_ticket` | Auth fail demo |

## Mapping to Phase 0 issues

| Issue | Platform hook |
|-------|----------------|
| [#3](https://github.com/DrCigarette69/stream-spike/issues/3) denylist | `/v1/dest/check` + `/gw/start` |
| [#4](https://github.com/DrCigarette69/stream-spike/issues/4) freeze | `/v1/admin/accounts/{id}/freeze` |
| [#8](https://github.com/DrCigarette69/stream-spike/issues/8) AUTH_TICKET | session ticket mint + gateway first frame |
| [#9](https://github.com/DrCigarette69/stream-spike/issues/9) grace screens | force-zero + usage flush events |

## Failure tips

| Symptom | Check |
|---------|--------|
| `peer_offline` on `/gw/start` | Peer not HELLO'd / ack empty / kill closed TCP |
| `auth_ticket_failed` | Wrong secret, expired ticket, endpoint mismatch, bad ALPN |
| Grace never exhausts | Meter not flushing; force-zero not called; chunk loop stuck |
| Port in use | Kill prior smoke (`pkill -f control/main.py` etc.) |
| Docker compose | Optional; local asserts do **not** require Docker |

## Non-goals

Production tunnels, public egress, live Stripe/KYC, community Iroh relays.
