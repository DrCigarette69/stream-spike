# Stream Alpha-3 — Iroh past loopback (DRAFT)

**Status:** DRAFT — pending Stream Dev room lock · **Date:** 2026-10-08 CT · **Author:** Stream Architect
**Base:** `main` @ `01eef77` (Alpha-2 done, rustc 1.85.1, pinned `Cargo.lock`)

## Decision

Today `iroh_loopback` (A1.1) is **not real Iroh**: it is the fake-relay newline-JSON framing over TCP on `127.0.0.1:9101`, and no `iroh` crate is in `rust/`. Alpha-3 replaces it, **behind an opt-in mode**, with a real Iroh QUIC endpoint that dials between **separate processes in separate network namespaces on this one machine**.

| | A1.1 `iroh_loopback` (keep) | A3 `iroh_local` (new, opt-in) |
|--|--|--|
| Env | `SPIKE_TRANSPORT=iroh_loopback` | `SPIKE_TRANSPORT=iroh_local` (Rust only) |
| Stack | TCP + JSON frames | `iroh` QUIC endpoint, ALPN `stream/tunnel/1`, frames on a bi-stream |
| Addressing | `127.0.0.1:9101` | endpoint ID + **direct private addrs only** (e.g. `10.73.0.0/24`) |
| Relay / discovery | — | `RelayMode::Disabled`, no DNS/pkarr/mDNS discovery, portmapper cannot be disabled in 0.95.1 (netns with no default route blocks it) |
| Topology | 1 Peer + 1 Gateway, same netns | 1 Gateway + ≥2 Peers, each in its own `ip netns`, veth to bridge `br-a3` |

Control stays **Python**. Python Peer/Gateway stay the `SPIKE_IMPL=python` default and refuse `iroh_local` with a clear error.

## Hard rules

- **This box only.** Namespaces have **no default route**; only `br-a3` (`10.73.0.0/24`). Control + test destinations bind `10.73.0.254`.
- **Private-CIDR guard** (extends A1.1 loopback guard), enforced in Peer **and** Gateway: bind/dial/direct-addr only in `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `::1`, `fd00::/8`; narrowable via `SPIKE_IROH_ALLOW_CIDRS` (default `10.73.0.0/24,127.0.0.0/8`). Anything else → refuse + log `a3_refuse_non_private:<addr>`.
- **Refuse any relay URL** unless A3.6 is on *and* its host is a guard-allowed IP. n0/community relay hosts and discovery config → refuse at startup.
- Existing greens unchanged: **SPIKE_DOD_GREEN**, **A1.1_IROH_LOOPBACK_GREEN**, **PEER_ALPHA1_CLI_GREEN**, **A1.3_MOCK_TOPUP_GREEN**, **A2.4_COMPOSE_GREEN** (default and `SPIKE_IMPL=rust`).
- Stubs / no live Stripe / `[Brand]` not `Stream` in UX. Small commits via `git` CLI.

## Room defaults (2026-10-08)

- **Toolchain:** stay on rustc 1.85.1 + `iroh =0.95.1` as long as a `--locked` build passes; if it stops passing, a Rust bump becomes its own slice **A3.R** (toolchain + Dockerfile `RUST_VERSION`) ahead of A3.1.
- **Gateway endpoint ID:** fixed dev key for Alpha-3; Control-issued IDs come later.
  - Dev key: `rust/crates/stream-gateway/dev/gateway_dev.key` (32 raw bytes, **DEV-ONLY**, committed; never production). `SPIKE_GATEWAY_ENDPOINT_ID=`
    `162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1` (derived with iroh 0.95.1 `SecretKey::from_bytes(..).public()`, round-trips via `EndpointId::from_str`; re-derive: `cargo run --locked -p stream-gateway --features iroh --example print_dev_endpoint_id`).
- **Ticket field format:** set by Platform Engineer, see *A3.3 ticket format* below.
- **Probe (Stream Architect, 2026-10-08):** PASS — `iroh =0.95.1` (`default-features = false`) builds `--locked` on 1.85.1 and an `empty_builder(RelayMode::Disabled)` endpoint binds `127.0.0.1:0` and starts in a netns with no default route, **only with pins** `ed25519 3.0.0-rc.2`, `pkcs8 0.11.0-rc.8`, `spki 0.8.0-rc.4`, `der 0.8.0-rc.10` (fresh resolve pulls the final 3.0.0/0.11.0/0.8.x, which break `ed25519-dalek 3.0.0-pre.1`); add them to `rust/pin-msrv-deps.sh` when A3.1 adds the `iroh` feature.

## Slices & owners

| Slice | Owner | Scope | Green |
|-------|-------|-------|-------|
| A3.0 Transport guard | Stream Architect | Spec + `stream-proto::guard` (CIDR allowlist, relay-URL refusal) + table tests; Peer/Gateway call it | **A3.0_PRIVATE_GUARD_GREEN** |
| A3.1 Gateway endpoint | Platform Engineer | `iroh` behind cargo feature `iroh`; listen on private addr, accept ALPN `stream/tunnel/1`, check remote endpoint ID == ticket's; Gateway key = dev key, `SPIKE_GATEWAY_ENDPOINT_ID=162e075fff…` (full in Room defaults) | **A3.1_GATEWAY_ENDPOINT_GREEN** |
| A3.2 Peer dial | Peer Engineer | Persistent per-peer secret key; dial Gateway by endpoint ID + direct addrs; ISP ack/consent gate **before** dial; kill-switch closes conn ≤2 s; egress floor unchanged | **A3.2_PEER_DIAL_GREEN** |
| A3.3 Control endpoint IDs / tickets | Platform Engineer | Python Control: Peer registers endpoint ID; `AUTH_TICKET` binds `endpoint_id`; hands Peer the Gateway endpoint ID + direct addrs (env override `SPIKE_IROH_GATEWAY_ADDR`) | **A3.3_TICKET_BIND_GREEN** (incl. mismatched ID refused) |
| A3.4 Multi-node net | Platform Engineer | `scripts/a3_netns_up.sh` / `_down.sh`: `br-a3`, `ns-gw` `.1`, `ns-peer-a` `.11`, `ns-peer-b` `.12`; client session lands on each Peer; kill one → other still serves. Compose variant is stretch (see risks) | **A3.4_MULTINODE_GREEN** |
| A3.5 Asserts + demo wiring | Stream Architect | `run_local_asserts.py a3` (A3.0–A3.4 + negatives: public IP dial, n0 relay URL, discovery on, all refused; `ip route` in each ns has no default); in `iroh_local` mode the a3 path greps screen IDs `p1_isp_ack` + `p2_consent` **before** the dial and `p4_kill` on the kill-switch, so the `fixtures/screens.json` copy checks (incl. `forbidden_user_facing_substrings`) run unchanged; `demo_alpha.sh` adds A3 block only when `SPIKE_A3=1` | **A3_IROH_LOCAL_GREEN** |
| A3.6 Local iroh-relay (optional) | Platform Engineer | Self-hosted `iroh-relay` dev mode on `10.73.0.254` only; used solely if direct path is flaky | **A3.6_LOCAL_RELAY_GREEN** |
| A3.7 Verify + Linear | Grok Bot | File A3.x issues once locked; re-verify on tip | — |
| Copy | Stream Designer | **None expected** — transport is invisible; same screens/greps | — |

### A3.3 ticket format (Platform Engineer, 2026-10-08)

The existing `AUTH_TICKET` fields stay unchanged. `iroh_local` adds three:

| Field | Type | Rule |
|-------|------|------|
| `peer_endpoint_id` | string | iroh `EndpointId` in its canonical `Display` form; must round-trip through `FromStr` to the same ID |
| `gateway_endpoint_id` | string | same encoding; the fixed dev key for Alpha-3 (`162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1`) |
| `direct_addrs` | list of `"ip:port"` | every entry must pass the A3.0 private-address guard; one public entry fails the whole ticket |

- **Control** refuses to issue a ticket when the Peer's endpoint ID is missing or does not parse, with reason `endpoint_bind_required`. An empty `direct_addrs` list is refused with its own reason, `direct_addrs_required`. Guard failures pass the A3.0 reason through unchanged (`public_addr`, `allowlist_miss`, and so on). `SPIKE_IROH_GATEWAY_ADDR` overrides `direct_addrs`, but it still goes through the guard.
- **Gateway** compares the authenticated remote `EndpointId` of the connection with `peer_endpoint_id`. A mismatch gets `AUTH_REJECT` with reason `endpoint_mismatch`, and the connection closes.
- **Peer** (A3.2) checks `gateway_endpoint_id` and `direct_addrs` the same way on its side before it dials.
- A3.3_TICKET_BIND_GREEN needs to show: a good bind is accepted, a missing ID gets `endpoint_bind_required`, an empty `direct_addrs` gets `direct_addrs_required`, a mismatched ID gets `endpoint_mismatch`, and a public `direct_addrs` entry is refused.

## Run (target)

```bash
# Needs passwordless `sudo -n`. Each smoke builds its own iroh binaries into rust/target/iroh
# and creates/tears down its own netns. Do NOT run a3_netns_up.sh first: a34 refuses to start
# if ns-gw / ns-peer-* already exist. a34 is single-instance (lock /tmp/stream-spike-a34.lock).
python3 scripts/run_local_asserts.py a3                      # a30..a34 + refusal/UX greps → A3_IROH_LOCAL_GREEN
SPIKE_A3=1 bash scripts/demo_alpha.sh                        # Alpha proof + A3 block → A3_IROH_LOCAL_GREEN
python3 scripts/run_local_asserts.py all                     # default path still → SPIKE_DOD_GREEN
# a3_netns_up.sh / _down.sh remain for manual poking at the A3.4 topology (run _down.sh after).
```

Done = A3.0–A3.5 green on one tip, existing five greens unchanged, A3.6 optional.

## Risks / open questions

1. **iroh vs rustc 1.85.1.** `iroh` 1.x and 0.96+ need rustc ≥1.89/1.91; **0.95.1 is the last on 1.85**. Pin `iroh =0.95.1` (stale API) **or** bump toolchain + Dockerfile `RUST_VERSION` (touches the A2 lock pin). Proposal: pin 0.95.1 for A3, bump in A4.
2. **Default builder phones home.** iroh's default builder uses n0 relays, DNS/pkarr discovery and portmapper/net-report probes. Must use the empty builder with `RelayMode::Disabled`, discovery off (`clear_discovery()`), `default-features = false`. Portmapper is a hard dependency in 0.95.1 with no off switch, so the no-default-route netns is the **required** guard, not just a backstop; A3.4 must keep every node in one.
3. **Network substrate.** `docker-compose.yml` notes the box's Docker bridge drops inter-container TCP (hence `network_mode: host`). UDP/QUIC over bridge is unproven, so `ip netns` (needs `sudo`, works on this box) is primary; compose multi-node is stretch.
4. **Ticket ↔ endpoint ID binding.** Resolved for Alpha-3: format in *A3.3 ticket format*; Gateway ID is a static dev key, Control-issued IDs later.
5. **Not simulated:** NAT, hole-punching, packet loss, real relays, WAN. A3 says nothing about real-world reachability.
6. **Build weight.** `iroh` adds a large dep tree / build time; keep it behind cargo feature `iroh` so A2 builds stay fast and `--locked` keeps working.

## Out of Alpha-3

Community/n0 relays, public discovery, public listen or egress, NAT traversal, mobile peers, Control→Rust (A2.5 / TOM-8), live Stripe/KYC, new UX.

## DONE — 2026-10-09 CT (A3.7 re-verify)

Grok Bot re-ran `SPIKE_A3=1 SPIKE_PORT_BASE=24000 ./scripts/demo_alpha.sh` on main `a5ea565` (code at `dc5b0e5`, plus a docs-only commit on top) with no a34 running and no netns present. Exit 0. Markers: SPIKE_DOD_GREEN, A1.1_IROH_LOOPBACK_GREEN, PEER_ALPHA1_CLI_GREEN, A1.3_MOCK_TOPUP_GREEN, A3.0_PRIVATE_GUARD_GREEN, A3.1_GATEWAY_ENDPOINT_GREEN, A3.2_PEER_DIAL_GREEN, A3.3_TICKET_BIND_GREEN, A3.4_MULTINODE_GREEN, **A3_IROH_LOCAL_GREEN**. No netns left behind. Epic TOM-12 closed; A3.6 (TOM-19, self-hosted relay) stays parked.

Known gap: `control/Dockerfile` doesn't copy `spike_private_guard.py`, so compose Control refuses `iroh_local` tickets with `guard_unavailable` (Alpha-3 runs in host netns only).
