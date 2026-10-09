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

Covers: match → session → AUTH_TICKET tunnel → force grace → exhaust → ledger `peer_payout` + non-negative balance · denylist refuse · `strict_unavailable` fixture · freeze · mock cashout &$lt;$25 · bad ticket reject.

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
| `POST /v1/mock/cashout` | 403 if accrued &lt; $25 |
| `POST /v1/mock/topup` | Mock Stripe customer credit (A1.3); 400 if amount<=0 |
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


## HARDENING (#3 #4 #8) — stubs

Still stubs only; Iroh parked.

| Issue | Control | Gateway |
|-------|---------|---------|
| #3 denylist | `GET/POST /v1/admin/denylist` versioned; mid-stream recheck on `usage/flush` when `dest_host` present | meter loop attaches dest every 3 ticks; start returns `denylist_version` |
| #4 freeze | freeze → `abuse_cases` + `attribution` preserve + `account.frozen_stop`; `POST .../unfreeze` | meter stops on `frozen` from flush |
| #8 AUTH_TICKET | mint payload includes `alpn=stream/tunnel/1`; `POST /v1/tickets/verify` rejects missing/bad alpn | `/gw/start` verifies ticket with Control before Peer AUTH |

`python3 scripts/platform_smoke.py` prints `HARDENING_SMOKE_GREEN #3 #4 #8` then `PLATFORM_SMOKE_GREEN`.

## A1.1 Iroh loopback (opt-in)

```bash
python3 scripts/iroh_loopback_smoke.py   # → A1.1_IROH_LOOPBACK_GREEN
```

See [`IROH_LOOPBACK.md`](IROH_LOOPBACK.md). Default transport remains `fake_relay`.

## A1.3 Mock customer top-up (Stripe stub)

**HOLD live Stripe / KYC.** Env: none (uses existing `SPIKE_*` stack).

| Item | Value |
|------|-------|
| Endpoint | `POST /v1/mock/topup` body `{account_id?, amount_usd?}` (default amount 10) |
| Codes | `mock_topup` · screen `c2_add_funds_mock` · rail `mock_stripe` |
| Copy | `Add funds` · `This is a test top-up` · `Funds added` ([`A1_3_COPY.md`](A1_3_COPY.md)) |
| Proof | `python3 scripts/a13_mock_topup_smoke.py` → **A1.3_MOCK_TOPUP_GREEN** |
| Assert mode | `python3 scripts/run_local_asserts.py a13` / `mock-topup` |

Credits `accounts.balance_usd`; does **not** auto-resume a stopped session (reconnect = new match+session). Rejects `amount_usd <= 0` with 400. Peer cashout mock remains `POST /v1/mock/cashout`.

## A2.2 Rust Gateway (`SPIKE_IMPL=rust`)

Parity binary: `rust/target/debug/stream-gateway` (or `cargo run -p stream-gateway`).

Python `gateway/` remains the `SPIKE_IMPL=python` fallback. Same env vars / ports.

```bash
# Control + Peer stay Python for this gate
export SPIKE_DB=/tmp/spike_a22.sqlite
export SPIKE_LISTEN=127.0.0.1:8080
export SPIKE_TICKET_SECRET=dev-only-change-me
export SPIKE_DENYLIST=$PWD/fixtures/denylist.seed.json
python3 -u control/main.py &

export SPIKE_PEER_ADMIN=127.0.0.1:9200
export SPIKE_ISP_ACK_VERSION=v1
export SPIKE_FAKE_RELAY_DIAL=127.0.0.1:9100
export CONTROL_URL=http://127.0.0.1:8080
python3 -u peer/main.py &

export SPIKE_LISTEN_PROXY=127.0.0.1:1080
export SPIKE_FAKE_RELAY=127.0.0.1:9100
export CONTROL_URL=http://127.0.0.1:8080
(cd rust && cargo run -p stream-gateway)

curl -s http://127.0.0.1:1080/health   # ok, impl=rust, hardening=[…]
curl -s http://127.0.0.1:1080/gw/peers
curl -s -X POST http://127.0.0.1:1080/gw/inject_bad_ticket \
  -H 'content-type: application/json' -d '{"peer_id":"peer_demo"}'
# → {"rejected":true,…}
```

Iroh loopback: `SPIKE_TRANSPORT=iroh_loopback` binds `SPIKE_IROH_LOOPBACK` (default `127.0.0.1:9101`) and **refuses non-loopback**. Asserts wire `SPIKE_IMPL=rust` in A2.3.

## A2.4 Compose Rust Peer/Gateway images

Multi-stage Dockerfiles under `rust/crates/stream-gateway/Dockerfile` and `rust/crates/stream-peer/Dockerfile` (`cargo build --release`, rustc 1.85). Control stays Python.

| Path | Proof |
|------|-------|
| Default Python | `./scripts/compose_health.sh` → **A0.4_COMPOSE_GREEN** |
| Rust peer+gateway | `SPIKE_IMPL=rust ./scripts/compose_health.sh` → **A2.4_COMPOSE_GREEN** |

```bash
# Rust stack (host networking; localhost binds)
sudo docker compose -f docker-compose.yml -f docker-compose.rust.yml up -d --build
# or:
SPIKE_IMPL=rust ./scripts/compose_health.sh

curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:1080/health   # must include "impl":"rust"
curl -s http://127.0.0.1:9200/health
```

Override file: `docker-compose.rust.yml` (does not change default Python compose). Healthchecks use `curl` (Rust images have no Python). Commit `rust/Cargo.lock` (or run `./pin-msrv-deps.sh` if missing).

## A3.1 Gateway iroh endpoint (`SPIKE_TRANSPORT=iroh_local`, TOM-14)

Opt-in, Rust-only, behind `cargo build --locked -p stream-gateway --features iroh`. Without the feature, `SPIKE_TRANSPORT=iroh_local` exits 1 with a clear error. `fake_relay` / `iroh_loopback` are unchanged.

- **Endpoint:** iroh 0.95.1 `Endpoint::empty_builder(RelayMode::Disabled)` + `clear_discovery()`, ALPN `stream/tunnel/1`, secret key from the DEV-ONLY key file. The other address family is pinned to loopback, never the `0.0.0.0`/`[::]` default. If the requested port is busy, the Gateway refuses instead of taking iroh's random-port fallback.
- **Guards (refuse before bind, exit 2):** listen must pass `stream_proto::guard::check_ip` (env allowlist `SPIKE_IROH_ALLOW_CIDRS`, default `10.73.0.0/24,127.0.0.0/8`); `SPIKE_IROH_RELAY_URL` set → `relay_refused`; `SPIKE_IROH_DISCOVERY` truthy → `discovery_refused`. Bound sockets are re-checked as private.
- **Must run in a no-default-route netns:** the iroh 0.95.1 portmapper can't be turned off.

| Env | Default |
|-----|---------|
| `SPIKE_TRANSPORT` | `iroh_local` to enable |
| `SPIKE_IROH_LISTEN` | `127.0.0.1:9102` (A3 netns: `10.73.0.1:9102`) |
| `SPIKE_GATEWAY_KEY_PATH` | `rust/crates/stream-gateway/dev/gateway_dev.key` (32 raw bytes; 0644 from git is tolerated with a warning) |
| `SPIKE_GATEWAY_ENDPOINT_ID` | optional; if set, it must match the key, or the Gateway refuses to start. Dev value `162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1` |

On start, stdout prints `IROH_LOCAL_READY endpoint_id=<id> direct_addr=<ip:port>`. `/health` adds `"transport":"iroh_local"`, `"gateway_endpoint_id"` and `"relay_listen"` (the advertised direct addr).

**Framing (Peer must match):** the Peer dials `EndpointAddr(gateway_id).with_ip_addr(direct_addr)` with ALPN `stream/tunnel/1` and opens **one** bi stream (`open_bi`, Peer side). It then speaks the fake_relay NDJSON unchanged, starting with Peer→Gateway `HELLO`. The Gateway answers `HELLO_OK`, then `AUTH_TICKET` per stream, and the Peer replies `AUTH_OK`. `OPEN`/`BYTES`/`CLOSE` are unchanged. Note that quinn only surfaces the stream to the Gateway after the Peer writes on it, so send HELLO right after `open_bi`.

**Endpoint binding:** the Gateway treats the QUIC-authenticated remote `EndpointId` as the truth.
- A HELLO whose `endpoint_id` differs gets `ERR endpoint_mismatch` + close. An empty `endpoint_id` is filled in from the auth.
- Before each AUTH_TICKET, the ticket's `payload.peer_endpoint_id` must parse and equal the remote id:
  - mismatch → `{"type":"AUTH_REJECT","error":"endpoint_mismatch","reason":"endpoint_mismatch","stream_id":…}`, then the stream finishes and the connection closes with app code 1, reason `endpoint_mismatch`. `/gw/start` returns 403 `endpoint_mismatch`.
  - missing or unparseable → same, with reason `endpoint_bind_required`.

Proof (box user with sudo; builds the iroh variant into `rust/target/iroh` so `rust/target/debug` stays the default build):

```bash
python3 scripts/a31_gateway_endpoint_smoke.py   # → A3.1_GATEWAY_ENDPOINT_GREEN
```

The smoke makes a throwaway netns with only `lo` (plus `10.73.0.1/24` on lo) and no default route. Inside it, it runs Control, the Gateway and `examples/a31_test_client` (random key; refuses a public `--direct-addr` / any `--relay-url` before dialing). It checks the gateway refusals, `/health`, the good ticket → AUTH_OK/OPEN, the mismatched ticket → AUTH_REJECT `endpoint_mismatch` + close, and the HELLO mismatch. Then it deletes the netns.
