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
| 9101 | Iroh loopback lane (A1.1, `SPIKE_TRANSPORT=iroh_loopback`) |
| 9200 | Peer admin (`/peer/kill`, health) |

### Concurrent runs: `SPIKE_PORT_BASE`

The box is shared, so two runs on the default ports break each other. Every
harness script (`run_local_asserts.py`, `platform_smoke.py`, `peer_smoke.py`,
`iroh_loopback_smoke.py`, `peer_alpha1_cli.py`, `a13_mock_topup_smoke.py`,
client-cli) resolves ports through [`scripts/spike_ports.py`](../scripts/spike_ports.py):

1. **Explicit env wins** — `SPIKE_LISTEN`, `CONTROL_URL`, `SPIKE_LISTEN_PROXY`,
   `GATEWAY_PROXY`, `SPIKE_FAKE_RELAY`, `SPIKE_FAKE_RELAY_DIAL`,
   `SPIKE_IROH_LOOPBACK`, `SPIKE_IROH_LOOPBACK_DIAL`, `SPIKE_PEER_ADMIN`,
   `PEER_ADMIN` are never overwritten (setdefault semantics).
2. **Else derived from `SPIKE_PORT_BASE`** (all on 127.0.0.1):

   | Service | Port |
   |---------|------|
   | Control | base+0 |
   | Gateway | base+1 |
   | Fake Relay | base+2 |
   | Iroh loopback | base+3 |
   | Peer admin | base+4 |

3. **Else today's defaults** (8080 / 1080 / 9100 / 9101 / 9200) — unset = unchanged behavior.

URLs follow their listen addr (`CONTROL_URL` from `SPIKE_LISTEN`, etc.) unless set.
With a non-default Control port the sqlite files get a port suffix
(`.dod.28080.sqlite`) so runs from one checkout don't clobber each other.

```bash
python3 scripts/run_local_asserts.py all &                        # 8080...
SPIKE_PORT_BASE=28080 python3 scripts/run_local_asserts.py all &  # 28080..28084
SPIKE_PORT_BASE=38080 SPIKE_IMPL=rust python3 scripts/run_local_asserts.py all
python3 scripts/spike_ports.py   # print the resolved values
```

Pick a base whose five ports are free (`ss -ltn`). The A1.1 bind guard still
refuses a non-loopback `SPIKE_IROH_LOOPBACK`. `compose_health.sh` (Docker) stays on fixed ports.

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
./scripts/demo_alpha.sh                     # DoD + A1.1 + A1.2 + A1.3 mock top-up
```

## Alpha-1 mock Stripe top-up (A1.3)

Mock customer credit only — **no live Stripe/KYC**. Designer fixture `c2_add_funds_mock`.

```bash
python3 scripts/a13_mock_topup_smoke.py     # → A1.3_MOCK_TOPUP_GREEN
python3 scripts/run_local_asserts.py a13    # same via assert runner
python3 scripts/run_local_asserts.py mock-topup
```

See [`A1_3_COPY.md`](A1_3_COPY.md) · [`PLATFORM_RUNBOOK.md`](PLATFORM_RUNBOOK.md) §A1.3.

## Alpha-3 private-address guard (A3.0)

Shared guard for `iroh_local` (no iroh dependency, no stack, no network). Spec: [`ALPHA3_IROH.md`](ALPHA3_IROH.md).

```bash
python3 scripts/run_local_asserts.py a30            # → A3.0_PRIVATE_GUARD_GREEN (alias: private-guard)
cd rust && cargo test -p stream-proto --locked guard # Rust table tests only
python3 scripts/spike_private_guard.py              # Python mirror self-test only
```

- Rust: `rust/crates/stream-proto/src/guard.rs` (`stream_proto::guard`) — Peer/Gateway.
- Python: `scripts/spike_private_guard.py` — Control (A3.3 `direct_addrs`).
- Private = `127/8`, `10/8`, `172.16/12`, `192.168/16`, `::1`, `fd00::/8`; IPv4-mapped IPv6 judged as IPv4. `SPIKE_IROH_ALLOW_CIDRS` narrows (unset → `10.73.0.0/24,127.0.0.0/8`; empty → private ranges only).
- Reasons: `public_addr`, `bad_addr`, `allowlist_miss`, `allowlist_config`, `relay_refused`, `discovery_refused`. Log line `a3_refuse_non_private:<addr>` for public/allowlist misses.
- If `cargo` is missing the Rust half is skipped with a message, unless `SPIKE_IMPL=rust` (then it fails).

## Alpha-3 Gateway iroh endpoint (A3.1)

Platform's smoke `scripts/a31_gateway_endpoint_smoke.py` (TOM-14), wrapped as an assert mode. **Opt-in, not in `all`** (needs sudo for `ip netns`).

```bash
python3 scripts/run_local_asserts.py a31               # → A3.1_GATEWAY_ENDPOINT_GREEN (aliases: gateway-endpoint, gateway_endpoint)
python3 scripts/a31_gateway_endpoint_smoke.py          # the smoke directly
```

- Builds `stream-gateway --features iroh` (+ `a31_test_client` example) into `rust/target/iroh`; `rust/target/debug` stays the default non-iroh build.
- Runs Control + Gateway + test client in a throwaway netns with only `lo` (+ `10.73.0.1/24`) and **no default route**. Gateway iroh listen `10.73.0.1:9102`; Control/Gateway HTTP on 8080/1080 *inside the netns*, so it never collides with host stacks or `SPIKE_PORT_BASE` runs. The smoke resets env inside the netns; `SPIKE_PORT_BASE` / `SPIKE_IROH_*` do not apply to it.
- The runner invokes the smoke as the current user (the smoke calls `sudo` itself for the netns and drops back to the user inside). It pre-checks `sudo -n true` (skipped when already root).
- Pass = exit 0 **and** the `A3.1_GATEWAY_ENDPOINT_GREEN` line; prints `PASS a31_gateway_endpoint_smoke`.
- If `cargo` or non-interactive sudo is missing it prints `SKIP a31 ...` and exits 0, unless `SPIKE_IMPL=rust` or `SPIKE_A3=1` (then `FAIL a31: ...`, exit 1).
- Proves: good ticket → AUTH_OK/OPEN; mismatched `peer_endpoint_id` → AUTH_REJECT `endpoint_mismatch` + close; HELLO with a foreign `endpoint_id` → ERR `endpoint_mismatch`; Gateway refuses public/wildcard/unparseable listen, relay URL, discovery; non-iroh build refuses `iroh_local`.

## Alpha-3 Peer iroh_local dial (A3.2)

Peer's smoke `scripts/a32_peer_dial_smoke.py` (TOM-15), wrapped like `a31`/`a33` (same runner helper). **Opt-in, not in `all`** (needs sudo for `ip netns`).

```bash
python3 scripts/run_local_asserts.py a32               # → A3.2_PEER_DIAL_GREEN (aliases: peer-dial, peer_dial)
python3 scripts/a32_peer_dial_smoke.py                 # the smoke directly
```

- Builds `stream-gateway --features iroh` and `stream-peer --features iroh_local` into `rust/target/iroh` (plus the default bins), then runs Control (8080), Gateway (1080 / iroh `10.73.0.1:9102`) and Peer (admin 9200, iroh bind `10.73.0.1:0`) inside the netns only; it strips `SPIKE_PORT_BASE`, `SPIKE_ISP_ACK_VERSION`, `SPIKE_P2_CONSENT`. No extra env needed.
- The smoke uses `sudo -n` itself and has its own identical SKIP/FAIL preflight; the runner pre-checks first, so semantics match `a31`: `SKIP a32 ...` (exit 0) without `cargo`/`sudo -n`, `FAIL a32: ...` (exit 1) under `SPIKE_IMPL=rust` or `SPIKE_A3=1`. Pass = exit 0 **and** the marker, then `PASS a32_peer_dial_smoke`. The smoke's `A3.2_WAITING_GATEWAY` (exit 3) counts as a failure here.
- Proves: Peer refuses public gateway addr / relay URL / wildcard bind, and a non-iroh build refuses `iroh_local`; no dial before P1 ack + P2 consent (`UX_SCREEN p1_isp_ack`, `UX_SCREEN p2_consent` printed before the dial); dial by endpoint ID + direct addr on `stream/tunnel/1` with HELLO → AUTH_TICKET → OPEN → BYTES → CLOSE using Control-minted tickets; kill drops the Peer < 2 s (`UX_SCREEN p4_kill`) and resume redials; a ticket for key A presented by key B → AUTH_REJECT `endpoint_mismatch`, Peer closes and doesn't retry; persistent key keeps the same endpoint ID across restarts.

## Alpha-3 Control ticket binding (A3.3)

Platform's smoke `scripts/a33_ticket_bind_smoke.py` (TOM-16), wrapped as an assert mode exactly like `a31`/`a32` (same runner helper). **Opt-in, not in `all`** (needs sudo for `ip netns`).

```bash
python3 scripts/run_local_asserts.py a33               # → A3.3_TICKET_BIND_GREEN (aliases: ticket-bind, ticket_bind)
python3 scripts/a33_ticket_bind_smoke.py               # the smoke directly
```

- Reuses the A3.1 harness (same `--features iroh` build into `rust/target/iroh`, same netns with only `lo` + `10.73.0.1/24`, no default route). Control instances on 8080 and 8091–8097 and the Gateway on 1080 / iroh `10.73.0.1:9102` exist only inside the netns; no extra env, ports or build features needed, and `SPIKE_PORT_BASE` does not apply.
- Same runner semantics as `a31`: `sudo -n true` pre-check (skipped when root), pass = exit 0 **and** the marker line, then `PASS a33_ticket_bind_smoke`. Missing `cargo`/sudo → `SKIP a33 ...` (exit 0), unless `SPIKE_IMPL=rust` or `SPIKE_A3=1` → `FAIL a33: ...` (exit 1).
- Proves (Control, `SPIKE_TRANSPORT=iroh_local`): 422 `endpoint_bind_required` for missing/malformed peer endpoint ID; `direct_addrs_required` for empty `SPIKE_IROH_GATEWAY_ADDR`; `public_addr` / `allowlist_miss` via the A3.0 guard (`SPIKE_IROH_ALLOW_CIDRS=''` lifts narrowing); non-iroh transports mint unchanged. End-to-end: a Control-minted ticket binds the Gateway-authenticated peer ID → AUTH_TICKET → AUTH_OK → OPEN; a stale ticket after another key takes over the `peer_id` → AUTH_REJECT `endpoint_mismatch` + close.
- **Compose Control:** the image is built from the repo root and includes `scripts/spike_private_guard.py`, so `SPIKE_TRANSPORT=iroh_local` minting works in the container. Proof: `python3 scripts/a40_compose_guard_smoke.py` → `A4.0_COMPOSE_GUARD_GREEN`. `compose_health.sh` honors `SPIKE_PORT_BASE` and explicit port env.

## Alpha-3 multi-node (A3.4)

Platform's smoke `scripts/a34_multinode_smoke.py` (TOM-17), wrapped with the shared netns-smoke helper. **Opt-in, not in `all`.** It takes about 60 s.

```bash
python3 scripts/run_local_asserts.py a34               # → A3.4_MULTINODE_GREEN (aliases: multinode, multi-node, multi_node)
sudo bash scripts/a3_netns_up.sh / a3_netns_down.sh    # reusable topology setup/teardown (br-a3, ns-gw .1, ns-peer-a .11, ns-peer-b .12)
```

- **Single-instance on the box.** The smoke uses fixed netns names (`ns-a3-br`, `ns-gw`, `ns-peer-a`, `ns-peer-b`, bridge `br-a3`) and tears them down at the end. The runner takes `flock /tmp/stream-spike-a34.lock` and waits up to 120 s for another run to finish, then fails. If any of those namespaces already exist it **fails without deleting them**, because they may be someone else's live run. Remove your own leftovers with `bash scripts/a3_netns_down.sh`.
- Skip/fail rules are the same as `a31`–`a33`.
- **Behaviour changes in A3.4 to be aware of:**
  - The Gateway's `iroh_local` idle timeout is 6 s (`SPIKE_IROH_IDLE_TIMEOUT_MS`), so a SIGKILLed Peer is dropped after about 6 s.
  - The Gateway tells Control through `POST /v1/peers/offline`.
  - Matching breaks ties least-recently-used first, so sessions alternate between Peers.

## Alpha-3 iroh_local roll-up (A3.5 / TOM-18)

`a3` (aliases `iroh-local`, `iroh_local`) is the A3 roll-up from [`ALPHA3_IROH.md`](ALPHA3_IROH.md). **Opt-in, not in `all`.** It prints **A3_IROH_LOCAL_GREEN** only when every part below passes.

```bash
python3 scripts/run_local_asserts.py a3                 # → A3_IROH_LOCAL_GREEN, or A3_IROH_LOCAL_NOT_GREEN pending: ...
python3 scripts/run_local_asserts.py a34                # A3.4 alone (single-instance)
SPIKE_A3=1 ./scripts/demo_alpha.sh                      # default demo steps, then the a3 block
```

1. **Sub-greens:** `a30`, then `a31`, `a32`, `a33` and `a34`, run **one at a time** through the shared netns-smoke helper (same skip/fail rules; `a34` takes its lock). If a sub-smoke is skipped, or `scripts/a34_multinode_smoke.py` is missing (`PENDING a34 ...`), `a3` does **not** print the green.
2. **Refusals, grepped from the sub-smoke output** (each smoke drives the real binaries in a no-default-route netns). The list is `A3_REFUSAL_GREPS` in `run_local_asserts.py`:
   - a31 Gateway: `a3_refuse_non_private:8.8.8.8` (public listen), `a3_refuse_non_private:0.0.0.0` (wildcard listen), `a3_refuse_relay_refused:https://use1-1.relay.n0.iroh.iroh.link./` (n0 relay), `a3_refuse_discovery_refused`.
   - a31 test client: `REFUSED public_addr a3_refuse_non_private:8.8.8.8` (public-IP dial), `REFUSED relay_refused`.
   - a32 Peer: `A3 iroh_local refused: public_addr` (public-IP dial and wildcard bind), `A3 iroh_local refused: relay_refused` (n0 relay), `iroh_local_feature_not_built`.
   - a33 Control: `error=public_addr detail='a3_refuse_non_private:8.8.8.8'`, `error=allowlist_miss`, `error=endpoint_bind_required`, `error=direct_addrs_required`.
   - No default route: `OK netns: no default route` in a31, a32 and a33; `OK no default route (v4/v6) in ns-a3-br, ns-gw, ns-peer-a, ns-peer-b` in a34; plus a check in a3's own UX netns.
   - a34 multi-node (`A3_MULTINODE_GREPS`): `peer implementation: real`, `OK both peers online over br-a3`, `-> peer_a`, `-> peer_b`, `peer_a SIGKILLed: gateway dropped it`, `post-kill session`.
   - Peer discovery has no on switch: `iroh_dial::bind_endpoint` always calls `check_discovery(false)` and refuses an endpoint with discovery. The `discovery_refused` reason itself is covered by the a30 guard tests.
3. **UX greps:**
   - Order comes from a32's in-order reads of the Peer's stderr: `OK P1 ack only: no dial (P2 consent required)`, `OK P1 -> P2 screen IDs before dial; peer iroh_local connected` (`UX_SCREEN p1_isp_ack`, then `UX_SCREEN p2_consent`, before the dial), and `UX_SCREEN p4_kill` with `(<2s)` on kill. a32 prints only these summary lines, not the Peer's raw stderr.
   - Copy: a3 starts the `iroh_local` Peer in its own netns (no ack, so it never dials), calls `GET /peer/consent/p2` and captures the real stderr. The `UX P2` block must be followed by `UX_SCREEN p2_consent` and contain every `p2_consent.required_copy` fragment, **read from `fixtures/screens.json`** (nothing hardcoded), with none of the fixture's `forbidden_user_facing_substrings`.
4. **Banned words (existing checks, unchanged):** `cargo test --locked -p stream-peer -- ux:: iroh_local::` (fixture drift, forbidden copy, screen order). The client-cli `forbid_brand` and `peer_alpha1_cli` `assert_no_forbidden` checks still run in `all` / `demo_alpha.sh`.
5. **Exit codes:** green prints `A3_IROH_LOCAL_GREEN` (exit 0). Not green prints `A3_IROH_LOCAL_NOT_GREEN pending: ...` and exits 1 under `SPIKE_A3=1` / `SPIKE_IMPL=rust`, otherwise 3. It never exits 0 without the green. Any failed check prints `FAIL ...` and exits 1.

## Alpha-4 compose guard (A4.0)

Platform's smoke `scripts/a40_compose_guard_smoke.py`, wrapped with the shared smoke helper. **Opt-in, not in `all`.** Needs `sudo docker` (no cargo).

```bash
python3 scripts/run_local_asserts.py a40               # → A4.0_COMPOSE_GUARD_GREEN (aliases: compose-guard, compose_guard)
```

- Missing `docker` / `sudo -n` → `SKIP a40 ...` (exit 0), unless `SPIKE_A4=1` or `SPIKE_IMPL=rust` → `FAIL a40: ...` (exit 1). Uses its own compose project (`a40-<port>`, `SPIKE_PORT_BASE` or 27310).
- The smoke checks the image's `spike_private_guard.py` is byte-identical to `scripts/`; after the guard changes, rebuild the Control image.

## Alpha-4 pilot guard (A4.1)

Spec: [`ALPHA4_PILOT.md`](ALPHA4_PILOT.md) (Guard changes). No stack, no network: the resolver is injected in tests.

```bash
python3 scripts/run_local_asserts.py a41                                   # → A4.1_PILOT_GUARD_GREEN (aliases: pilot-guard, pilot_guard)
cd rust && cargo test -p stream-proto --locked guard::pilot                # Rust tests (Lane::Pilot build)
cd rust && cargo test -p stream-proto --locked --features a4_local guard::pilot
python3 scripts/spike_private_guard.py --pilot                             # Python mirror pilot self-test
```

- Rust: `rust/crates/stream-proto/src/guard/pilot.rs` (re-exported from `stream_proto::guard`). Python: A4.1 section of `scripts/spike_private_guard.py`.
- Reasons: `relay_config`, `relay_required`, `egress_allowlist_config`, `egress_not_allowlisted`, `egress_resolved_non_public`, `egress_resolve_failed`, `egress_off`, `egress_allowlist_mismatch`, `egress_budget_exceeded`, `transport_not_pilot`, `stripe_live_key_refused`. Log line `a4_refuse_<reason>:<detail>`.
- Missing `cargo` → Rust half skipped with a message, unless `SPIKE_IMPL=rust` or `SPIKE_A4=1` (then `FAIL a41`). `a30` is unchanged (its `guard` filter now also runs the pilot tests).
