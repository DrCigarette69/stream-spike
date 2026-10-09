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

## A3.3 Control ticket binding (`SPIKE_TRANSPORT=iroh_local`, TOM-16)

Control source of truth: `control/main_expanded.py`. After editing it, run `python3 control/build_parts.py` to rewrite both `_zlib_*.txt` (preferred by `main.py`) and the `_src_part_*.b85` fallback. `--check` verifies both decode to the same bytes as `main_expanded.py`. The loader format is unchanged. The old b85 fallback was undecodable ("base85 overflow"); it has been regenerated.

With `SPIKE_TRANSPORT=iroh_local` in Control's env, `POST /v1/sessions` mints:

| Ticket field | Source | Refusal (HTTP 422, `{"error":R,"code":"ticket_bind_refused","reason":R,"detail":…}`) |
|---|---|---|
| `peer_endpoint_id` | the Peer's enrolled `endpoint_id`. The iroh Gateway enrolls the QUIC-authenticated id, so this is the real key. | not 64 lowercase hex (iroh `Display`) → `endpoint_bind_required` |
| `gateway_endpoint_id` | `SPIKE_GATEWAY_ENDPOINT_ID`, default dev `162e075f…d7a1`. The request body can't override it in this mode. | malformed env → `gateway_endpoint_invalid` |
| `direct_addrs` | `SPIKE_IROH_GATEWAY_ADDR` (comma list). Unset → `127.0.0.1:9102`; set to empty → `[]`. | empty → `direct_addrs_required`; any entry failing `scripts/spike_private_guard.check_direct_addrs` → guard reason unchanged (`public_addr`, `allowlist_miss`, `bad_addr`, `allowlist_config`) |

- **Allowlist:** `SPIKE_IROH_ALLOW_CIDRS` follows the A3.0 rules. Unset → `10.73.0.0/24,127.0.0.0/8`; empty → any private range.
- **Verify:** in this mode `/v1/tickets/verify` also re-checks `peer_endpoint_id` form and the guarded `direct_addrs`. The Gateway then checks the authenticated remote id against `peer_endpoint_id` (`endpoint_mismatch`, see A3.1).
- **Signing is unchanged:** HMAC-SHA256 over the sorted, compact JSON of `payload`, so the Rust Gateway → Control verify path works as-is.
- **Other transports are unchanged:** no `direct_addrs`, and `gateway_endpoint_id` still defaults to `iroh_ep_gateway_spike`.
- **Docker image:** compose builds Control from the repo root (`control/Dockerfile`). `control/Dockerfile.dockerignore` keeps the build context to `control/` plus `scripts/spike_private_guard.py`, which is copied next to `main.py`. One source file, no duplicated guard logic. If the module is ever missing, Control still fails closed with `guard_unavailable`. `docker build control/` alone no longer works; use `docker compose build control` or `docker build -f control/Dockerfile .`. Proof: `python3 scripts/a40_compose_guard_smoke.py` → `A4.0_COMPOSE_GUARD_GREEN`.

Proof (sudo; reuses the A3.1 netns harness, all tickets minted by Control):

```bash
python3 scripts/a33_ticket_bind_smoke.py   # → A3.3_TICKET_BIND_GREEN
```

It checks:
- missing or malformed id → `endpoint_bind_required`
- empty addrs → `direct_addrs_required`
- `8.8.8.8` → `public_addr`
- `192.168.1.5` → `allowlist_miss`, but accepted with `SPIKE_IROH_ALLOW_CIDRS=''`
- non-iroh minting unchanged
- good bind end-to-end through the Rust Gateway (AUTH_TICKET → AUTH_OK → OPEN)
- a ticket minted for key A, presented after key B took over the same `peer_id` → `endpoint_mismatch` at the Gateway, plus close

`a31_gateway_endpoint_smoke.py` now also uses a Control-minted ticket for its good case.

## A3.4 Multi-node netns (TOM-17)

```bash
sudo bash scripts/a3_netns_up.sh     # idempotent → A3_NETNS_UP …
sudo bash scripts/a3_netns_down.sh   # idempotent; kills processes left in the ns, deletes them → A3_NETNS_DOWN
python3 scripts/a34_multinode_smoke.py   # up → run → down → A3.4_MULTINODE_GREEN
```

| netns | iface | addr | runs |
|---|---|---|---|
| `ns-a3-br` | bridge `br-a3` (no IP) | — | nothing (keeps the bridge off the host stack) |
| `ns-gw` | `a3eth0` (veth `a3v-gw`) | `10.73.0.1/24` | Control `10.73.0.1:8080`, Gateway iroh `10.73.0.1:9102` (admin `127.0.0.1:1080`, ns-local) |
| `ns-peer-a` | `a3eth0` (veth `a3v-pa`) | `10.73.0.11/24` | Peer A |
| `ns-peer-b` | `a3eth0` (veth `a3v-pb`) | `10.73.0.12/24` | Peer B |

- **No default route anywhere.** Each node ns has only `lo` and the connected `10.73.0.0/24` route. IPv6 router advertisements are off, so no v6 default route appears either. Both the up script and the smoke check every ns for a default route. Peers reach Control and the Gateway only over `br-a3`.
- **Real Peer env for each ns:**
  - `SPIKE_TRANSPORT=iroh_local`
  - `SPIKE_IROH_BIND=10.73.0.1x:0` (its ns address)
  - its own `SPIKE_IROH_KEY_PATH` (0600). Sharing a key means sharing an endpoint ID, and the Gateway then rejects one Peer with `endpoint_mismatch`.
  - `SPIKE_GATEWAY_ENDPOINT_ID`=dev ID, `SPIKE_IROH_GATEWAY_ADDR=10.73.0.1:9102`, `CONTROL_URL=http://10.73.0.1:8080`
  - headless consent: `SPIKE_ISP_ACK_VERSION=v1` **and** `SPIKE_P2_CONSENT=1`. P2 alone is ignored.
- **Smoke** (`A34_PEER=real|testclient|auto`; the default `auto` picks the real `stream-peer --features iroh_local`):
  1. Both Peers enroll with their own authenticated IDs.
  2. Two Control-minted sessions land on Peer A and on Peer B, each ticket bound to that Peer.
  3. Peer A is SIGKILLed (no QUIC close, no kill-switch). The Gateway notices after its idle timeout and reports to Control.
  4. Two new sessions are both served by Peer B.
  5. The down script runs and the smoke checks that all four netns are gone.
- **What this added to the Gateway and Control:**
  - Gateway (`iroh_local`): QUIC `max_idle_timeout` = `SPIKE_IROH_IDLE_TIMEOUT_MS` (default 6000, clamped 1000–60000) with a 1 s keep-alive, so a dead Peer is noticed in about 6 s.
  - When an iroh Peer's connection ends (drop, `endpoint_mismatch`, idle timeout), the Gateway calls Control `POST /v1/peers/offline {peer_id, endpoint_id, reason}`. It skips the call when a newer connection replaced the entry. Control sets `online=0` only if `endpoint_id` still matches, and emits `peer.offline`.
  - Control matching: lowest `load` first; ties go to the least recently matched Peer (in-memory). With one online Peer this behaves as before.
- No `dummy` link type on this box. `veth` and `bridge` work.

## Compose ports (`compose_health.sh`)

`scripts/compose_health.sh` resolves ports with `scripts/spike_ports.py`, the same precedence as the local asserts:
1. explicit env (`SPIKE_LISTEN`, `CONTROL_URL`, `SPIKE_LISTEN_PROXY`, `GATEWAY_PROXY`, `SPIKE_FAKE_RELAY[_DIAL]`, `SPIKE_PEER_ADMIN`, `PEER_ADMIN`)
2. `SPIKE_PORT_BASE` (control +0, gateway +1, relay +2, peer +4)
3. the legacy 8080/1080/9100/9200

It passes these through `sudo env … docker compose`. `docker-compose.yml` / `docker-compose.rust.yml` interpolate them, healthchecks included, with the old values as defaults. Binds stay `127.0.0.1` (host networking).

```bash
SPIKE_PORT_BASE=27300 ./scripts/compose_health.sh                 # → A0.4_COMPOSE_GREEN on 27300/27301/27304
SPIKE_PORT_BASE=27300 SPIKE_IMPL=rust ./scripts/compose_health.sh # → A2.4_COMPOSE_GREEN
```

The compose project is still named after the checkout dir. Set `COMPOSE_PROJECT_NAME` to run a second stack from the same checkout.

## A4.2 Self-hosted relay (`SPIKE_TRANSPORT=iroh_pilot`, TOM-19)

Our own iroh relay, built from the pinned lock; Peer and Gateway reach each other **only** through it.

**Relay binary** — `rust/crates/stream-relay` (iroh-relay 0.95.1 `server`; not in the default build):

```bash
cd rust && cargo build --locked -p stream-relay --features server,a4_local   # on-box lane build
STREAM_RELAY_MODE=dev STREAM_RELAY_HTTP_BIND=10.73.0.254:3340 ./target/debug/stream-relay
# -> STREAM_RELAY_READY url=http://10.73.0.254:3340/ ... quic=off mode=dev access=everyone
```

- `dev` = plain http, only in `a4_local` builds and only on `10.73.0.254:<port>` (the URL the guard's Local lane accepts). A pilot build refuses `dev`.
- QUIC address discovery and the metrics listener are always off.
- `STREAM_RELAY_ALLOW_ENDPOINTS=<id>,<id>` restricts who may use the relay (iroh `AccessConfig::Restricted`); required in `tls` mode.
- Never binds `0.0.0.0`/`[::]`.

**Gateway** (`--features iroh_pilot[,a4_local]`), `SPIKE_TRANSPORT=iroh_pilot`:

| Env | Meaning |
|-----|---------|
| `SPIKE_RELAY_ALLOW_URL` | required; our one relay (`guard::RelayAllow::parse`, lane from the build). Unset / n0 host / non-public IP → `a4_refuse_relay_config` |
| `SPIKE_IROH_RELAY_URL` | optional, defaults to the allow URL; anything else (n0, other port/host/scheme) → `a3_refuse_relay_refused` |
| `SPIKE_IROH_PATH_SELECTION` | unset or `relay_only`; anything else refused |
| `SPIKE_IROH_LISTEN` | still the A3.0-guarded local bind (UDP socket exists but no direct path is used) |
| `SPIKE_IROH_RELAY_WAIT_MS` | wait for the home relay at start (default 10000); no relay → `a4_refuse_relay_unreachable`, exit 2 |

Endpoint: `RelayMode::Custom(<allow URL>, quic: None)`, `PathSelection::RelayOnly`, `clear_discovery()`. Nothing calls `insecure_skip_relay_cert_verify` (a42 greps for it). A build without `iroh_pilot` refuses `SPIKE_TRANSPORT=iroh_pilot`. `/health` adds `relay_url` and `iroh_path` (`relay_*` / `udp_*` datagram counters; `ConnectionType` stays `none` under RelayOnly in iroh 0.95.1, so the counters are the path evidence). Tickets must carry `relay_url` equal to the allow URL (`relay_required` / `relay_refused`); any `direct_addrs` still go through A3.0.

**Control** (`SPIKE_TRANSPORT=iroh_pilot`): mints `relay_url` = `SPIKE_RELAY_ALLOW_URL` (canonical), `direct_addrs` = `SPIKE_IROH_GATEWAY_ADDR` (default empty = relay-only), plus the A3.3 endpoint binding. `SPIKE_A4_LANE=local` selects the Python guard's Local lane (on-box only).

**Proof:** `python3 scripts/a42_self_relay_smoke.py` → **A4.2_SELF_RELAY_GREEN** (single instance via `/tmp/stream-spike-a42.lock`; netns `a42-gw` / `a42-relay` / `a42-peer` from `scripts/a42_netns_up.sh`, torn down by `a42_netns_down.sh`). The relay ns is the only one attached to both sides and has `ip_forward=0`, so Gateway (10.73.0.1) and Peer (10.73.0.200) have no route to each other; the smoke shows TCP/UDP to the other side is `Network is unreachable`, then relay-only connect, Control-minted ticket → AUTH_OK → OPEN with 0 UDP datagrams on both ends, n0/other relay refused, and relay down → live conn closed, new dial `RELAY_UNREACHABLE`, Gateway start refused.

**VPS deploy (TODO, pending Jeff — ALPHA4_PILOT open question 1):** nothing is deployed.
- Needs: host (small VPS), a hostname we own, DNS A/AAAA record, firewall allowing 80 (ACME / captive portal) + 443 (relay) only; Gateway client/admin listeners stay on localhost (SSH tunnel).
- `tls` hook (validated now, then refused with `relay_tls_todo` until the deploy slice): `STREAM_RELAY_MODE=tls`, `STREAM_RELAY_HOSTNAME`, `STREAM_RELAY_HTTPS_BIND=<public-ip>:443`, `STREAM_RELAY_HTTP_BIND=<public-ip>:80`, `STREAM_RELAY_TLS_CERT` / `STREAM_RELAY_TLS_KEY` (Let's Encrypt via certbot, or iroh-relay's built-in ACME), `STREAM_RELAY_ALLOW_ENDPOINTS` (Gateway + Jeff's two devices).
- Clients then use `SPIKE_RELAY_ALLOW_URL=https://<hostname>/` from pilot builds (no `a4_local`); certs are always verified.
