# Stream Alpha-4 — two-device pilot (DRAFT)

**Status:** DRAFT — Stream Architect locks after Stream Dev room review · **Date:** 2026-10-09 CT · **Author:** Stream Architect
**Base:** `main` @ `4a57884` (Alpha-3 done: `A3_IROH_LOCAL_GREEN`, TOM-12 closed)
**Scope (Jeff, 2026-10-09):** self-hosted relay only (A3.6 promoted), Jeff's own two devices on real home networks as Peers, Stripe **test mode** top-up (no live charges), public egress **only** to a small test-site allowlist.

## Decision

Alpha-4 is the first cut that crosses the old "no public egress" rule. The crossing is **opt-in, fail-closed, and allowlist-only**. Everything new sits behind a new transport plus one kill switch. With default settings the repo behaves exactly like Alpha-3.

| | A3 `iroh_local` (keep) | A4 `iroh_pilot` (new, opt-in) |
|--|--|--|
| Env | `SPIKE_TRANSPORT=iroh_local` | `SPIKE_TRANSPORT=iroh_pilot` (Rust only, cargo feature `iroh_pilot` on stream-peer / stream-gateway) |
| Path Peer ↔ Gateway | direct private addrs, no relay | **our one self-hosted relay** (`SPIKE_RELAY_ALLOW_URL`); n0/any other relay refused |
| Discovery | refused | refused (unchanged) |
| Peer egress | egress floor (#6); netns has no default route | floor (#6) **and** host:port allowlist, checked at Peer **and** Gateway, resolved IP must be public |
| Public egress switch | n/a | `SPIKE_PUBLIC_EGRESS` (default `0`) **AND** Control flag; either off → every OPEN refused |
| Where | this box, netns | on-box lanes: netns + local stand-ins (no internet). Pilot run: Jeff's two devices only |
| Top-up | A1.3 mock | Stripe **test mode** (`sk_test_…` only) |

Control stays **Python** (TOM-8 stays on hold). Python Peer/Gateway refuse `iroh_pilot` with a clear error, as they do `iroh_local`.

## Hard rules

- **Default = Alpha-3.** With `SPIKE_TRANSPORT` / `SPIKE_PUBLIC_EGRESS` unset, nothing changes. These stay green, untouched, on default settings: **SPIKE_DOD_GREEN**, **A1.1_IROH_LOOPBACK_GREEN**, **PEER_ALPHA1_CLI_GREEN**, **A1.3_MOCK_TOPUP_GREEN**, **A2.4_COMPOSE_GREEN**, **A3.0_PRIVATE_GUARD_GREEN** … **A3.4_MULTINODE_GREEN**, **A3_IROH_LOCAL_GREEN** (default and `SPIKE_IMPL=rust`). Existing guard functions (`check_relay_url`, `check_direct_addr*`, `check_discovery`) keep their Alpha-3 behavior and reason strings; Alpha-4 adds `_with`/new functions next to them.
- **Fail closed everywhere.** Missing, malformed or stale config → refuse. Config errors refuse at startup, before anything binds or dials.
- **On-box lanes stay egress-free.** Every `a4*` assert on this box runs in a no-default-route netns, with local stand-ins for the relay, the "public" test sites and Stripe webhooks (see *Local stand-ins*). Real-internet egress happens **only** from Jeff's two devices during the A4.9 pilot run.
- **Pilot build refuses old transports.** A `--features iroh_pilot` Peer refuses to start unless `SPIKE_TRANSPORT=iroh_pilot` (`transport_not_pilot`). The packaged binary can't fall back to floor-only egress, which on a real home network would allow any public host:443.
- **No open proxy.** The Gateway's client-facing SOCKS/proxy and admin listeners never bind a public interface. Jeff reaches them over SSH/WireGuard or localhost on the Gateway host.
- **Consent unchanged, one screen added.** P1 ISP ack + P2 consent gate before any dial; P4 kill closes ≤2 s (user pause). Peer order with public egress on: `p1_isp_ack` → `p2_consent` → `p9_allowlist` → dial. `p9_allowlist` appears **only** when `SPIKE_PUBLIC_EGRESS` is on and never names the test sites. Any system stop (reject, drop, kill-switch off, stale state) → `p8_offline`: plain sentences mapped from reason codes, **never raw codes**, and **no auto-retry after `endpoint_mismatch`**. Existing copy checks unchanged. Copy source: `fixtures/screens.json` (`docs/A4_COPY.md`).
- **Stripe test mode only.** The key is `STRIPE_TEST_SECRET_KEY` (Jeff saves it through his 1:1 with Platform Engineer; never in the room, never committed). Control refuses to start if it is set to anything that isn't `sk_test_…` / `rk_test_…` (`stripe_live_key_refused`). **Unset → the A1.3 mock top-up (`c2_add_funds_mock`) stays the path**, so keyless runs and A1.3_MOCK_TOPUP_GREEN are unchanged.
- **Logs are metadata only:** account, time, destination host and port, bytes, location tier, decision reason (P2 copy). Never contents.
- `[Brand]`, never `Stream`, in user-facing copy. Small commits via `git` CLI.

## Guard changes (A4.1, Stream Architect)

All three live in `stream_proto::guard` with the same rules, env names and reason strings mirrored in `scripts/spike_private_guard.py`. Table tests on both sides.

### 1. Relay allowlist — exactly one relay

- New: `check_relay_url_with(url, allow: Option<&RelayAllow>)`. `allow` comes from `SPIKE_RELAY_ALLOW_URL` (exactly one URL; a comma means `relay_config`). Existing `check_relay_url(url)` = `_with(url, None)` = refuse all (Alpha-3 behavior, A3 tests unchanged).
- Match is **exact after normalisation**: scheme must be `https` (`http` only for the local lane, see stand-ins), lowercase host, trailing dot stripped, explicit/default port compared, no userinfo/query/fragment, path `/` only. Anything else → `relay_refused`.
- The allow URL itself is refused at parse time (`relay_config`) if its host is an n0/community relay (`*.iroh.network`, `*.iroh.link`, `*.n0.*`) or a literal IP that isn't public. So "allow the n0 relay" can't be configured by accident.
- Used by: Gateway `SPIKE_IROH_RELAY_URL` (existing env, now checked against the allowlist), Peer endpoint builder, and the ticket's new `relay_url` field (Control mints it; Peer and Gateway both re-check).
- `check_discovery` unchanged: on → `discovery_refused`.
- `direct_addrs` in `iroh_pilot` tickets may be empty (relay-only); `relay_url` is then required (`relay_required`). Any `direct_addrs` entry still goes through the A3.0 private guard.

### 2. Egress allowlist — enforced twice

- New: `EgressAllow` parsed from `SPIKE_EGRESS_ALLOWLIST` = comma-separated `host:port`, **exact hostnames, no wildcards, no literal IPs, ports 443 (80 only if a slice asks for it)**. Malformed / IP literal / wildcard / empty-in-pilot → `egress_allowlist_config`.
- `check_egress_dest(host, port, allow)` → `egress_not_allowlisted` unless host (lowercased, trailing dot stripped, IDNA'd) **and** port match an entry exactly. An OPEN with a literal IP is always `egress_not_allowlisted`.
- `check_egress_resolved(ips)` — SSRF guard: **every** resolved address must be public. Refused (`egress_resolved_non_public`): the A3.0 private ranges, `0/8`, `169.254/16` (incl. metadata `169.254.169.254`), `100.64/10`, `192.0.0/24`, TEST-NETs, `198.18/15`, multicast, `240/4`, broadcast, `::`, `fe80::/10`, `fc00::/7`, `64:ff9b::/96` (NAT64), IPv4-mapped forms judged as IPv4. One bad address fails the whole answer.
- **DNS rebinding:** resolve once at connect time, check, then connect to the **checked IP** (pinned; no second lookup by the socket layer). No caching across OPENs beyond the record TTL. Re-resolution on a new OPEN is re-checked.
- **Order at the Peer (A4.4)** on every OPEN, before dialing: kill switch → existing floor (#6, unchanged) → `check_egress_dest` → resolve → `check_egress_resolved` → connect to pinned IP → log. **Order at the Gateway (A4.3)** before forwarding the OPEN: kill switch → `check_egress_dest` (same allowlist, from Control) → forward. A bug on one side can't open general egress.
- The existing floor keeps checking host strings only (it passes any hostname). The allowlist is the layer that closes that gap in pilot mode; don't relax the floor.
- Byte budget: `SPIKE_EGRESS_BYTE_CAP` per Peer per UTC day (pilot default 50 MB); over the cap → CLOSE `egress_budget_exceeded`. Counted at both ends; the Gateway's number is authoritative for the ledger.

### 3. Kill switch — one setting, off everywhere

- `public_egress_effective = SPIKE_PUBLIC_EGRESS==1 (local env) AND control_flag==on AND control_flag_fresh`.
- `SPIKE_PUBLIC_EGRESS`: unset/`0` (**default**) → in `iroh_pilot` every OPEN gets `egress_off`; in other transports nothing changes (they never reach public egress).
- Control flag: `GET /v1/egress/state` → `{"public_egress": bool, "allowlist_version": "<sha256>", "ts": ...}`; flipped by `POST /v1/admin/egress` (admin auth). Gateway polls every 1 s; the Peer gets it via the Gateway's frames (new `EGRESS_STATE` frame) and checks freshness itself.
- **Stale = off.** No fresh state within `SPIKE_EGRESS_STATE_MAX_AGE_MS` (default 5000) → off. Control unreachable → off.
- On flip to off: Gateway refuses new OPENs and CLOSEs open public-egress streams (`egress_off`); Peer does the same on receipt or on staleness. Target: **all public-egress streams closed ≤5 s** after the flag flips (that's the `N` in A4.8).
- `allowlist_version` must match on Gateway and Peer, or OPENs are refused (`egress_allowlist_mismatch`), so the two checks can't drift apart.
- Guard primitive: `EgressSwitch { env_on, flag, max_age_ms }` → `check(now_ms)` / `is_on(now_ms)` / `check_open(now_ms, local_version)`, pure and time-injectable for tests. Every off state is reason `egress_off` (matches `p8_offline.reason_lines`); the detail says why: `local_off`, `no_state`, `flag_off`, `flag_stale` (a future timestamp counts as stale). `SPIKE_EGRESS_STATE_MAX_AGE_MS` can only narrow: clamped to 100–5000 ms.

New reason strings (stable; landed in A4.1, see *A4.1 as landed*): `relay_config`, `relay_required`, `egress_allowlist_config`, `egress_not_allowlisted`, `egress_resolved_non_public`, `egress_resolve_failed`, `egress_off`, `egress_allowlist_mismatch`, `egress_budget_exceeded`, `transport_not_pilot`, `stripe_live_key_refused`. Log lines `a4_refuse_<reason>:<detail>` (detail = host:port or URL, never payload).

### A4.1 as landed

`rust/crates/stream-proto/src/guard/pilot.rs` (re-exported as `stream_proto::guard::*`, tests in `guard/pilot_tests.rs`) + `scripts/spike_private_guard.py` (A4.1 section, `--pilot` self-test). `python3 scripts/run_local_asserts.py a41` (alias `pilot-guard`) → **A4.1_PILOT_GUARD_GREEN**. Alpha-3 functions, reasons and log lines unchanged (`check_relay_url` still refuses every relay); A4 reasons log as `a4_refuse_<reason>:<detail>`; `relay_refused` from the allowlist check keeps the A3 log line.

- Allowlist entries must be ASCII (punycode `xn--…` for IDNs); a non-ASCII OPEN host is `egress_not_allowlisted`. Ports: `ALLOWED_EGRESS_PORTS = [443]`.
- The SSRF check refuses IPv6 outside `2000::/3` plus `2001::/23`, `2001:db8::/32`, `2002::/16` (6to4), `3fff::/20`, ORCHID; NAT64 `64:ff9b::/96` and `64:ff9b:1::/48` fall outside `2000::/3`.
- `resolve_and_pin` refuses off-allowlist names **before** any DNS lookup.
- `check_stripe_test_key` logs only the key prefix (e.g. `sk_live_`), never the key.
- `allowlist_version` = sha256 of the sorted canonical `host:port\n` lines (std-only SHA-256 in Rust, `hashlib` in Python; same hex).

## Slices & owners

| Slice | Owner | Scope | Green |
|-------|-------|-------|-------|
| A4.0 Control container fix | Platform Engineer | `control/Dockerfile` copies `scripts/spike_private_guard.py`; compose Control mints `iroh_local` tickets (no `guard_unavailable`); default compose unchanged. **Already started.** | **A4.0_COMPOSE_GUARD_GREEN** (+ A2.4_COMPOSE_GREEN unchanged) |
| A4.1 Pilot guard | Stream Architect | Relay allowlist, egress allowlist + resolved-IP SSRF check, kill-switch primitive, new reasons, in `stream_proto::guard` + Python mirror, table tests; A3 tests untouched | **A4.1_PILOT_GUARD_GREEN** |
| A4.2 Self-hosted relay | Platform Engineer | A3.6 / TOM-19 promoted. `iroh-relay` we run ourselves; on-box lane at `10.73.0.254` (http, local only); pilot instance at the host chosen in open question 1, with TLS. Peer + Gateway connect relay-only; n0/other relay refused | **A4.2_SELF_RELAY_GREEN** |
| A4.3 Gateway egress check + kill propagation | Platform Engineer | Gateway re-checks every OPEN against the allowlist; polls `/v1/egress/state`; `EGRESS_STATE` frame to Peers; closes streams on off/stale; byte cap + metadata logging | **A4.3_GATEWAY_EGRESS_GREEN** |
| A4.4 Peer egress + relay-only dial | Peer Engineer | `iroh_pilot` mode: relay-only dial, allowlist + resolve-and-pin before connect, kill-switch freshness, byte cap; P1/P2/P4 unchanged; `transport_not_pilot` in pilot build | **A4.4_PEER_EGRESS_GREEN** |
| A4.5 Peer packaging | Peer Engineer | `--locked --features iroh_pilot` build for Jeff's two devices (Docker image or native; OS from Jeff via Grok Bot); per-device key; config file with relay URL + allowlist; on-device `stream-peer --selftest` | **A4.5_PEER_PACKAGE_GREEN** (selftest on each device) |
| A4.6 Stripe test top-up | Platform Engineer | `STRIPE_TEST_SECRET_KEY` set → test-mode Checkout/PaymentIntent, `UX c2_add_funds_test`, balance updates only after Control gets the webhook; unset → mock path unchanged; webhook signature verified; credit lands once (idempotent on event ID); `sk_live_` refused. On-box lane replays a locally signed fixture event (no Stripe network). Reachability: tunnel or Stripe CLI (Jeff's login, his 1:1 with Platform) | **A4.6_STRIPE_TEST_TOPUP_GREEN** |
| A4.7 Pilot copy | Stream Designer | P1 ISP warning `legal_status: pending_counsel` (OK while only Jeff's devices share; counsel wording **required** before anyone else shares); `c2_add_funds_test` keeps "test top-up, no real charge"; new `p8_offline` (connection rejected / offline) and `p9_allowlist` — **landed in `fixtures/screens.json` at `fd19e0d`** (see `docs/A4_COPY.md`); remaining: Rust fixture mirror, `[Brand]` only | **A4.7_COPY_FIXTURES_GREEN** (fixture drift + forbidden-copy tests) |
| A4.8 Asserts + demo wiring | Stream Architect | `run_local_asserts.py a4` (A4.0–A4.7 sub-greens + negatives below); `demo_alpha.sh` adds the A4 block only when `SPIKE_A4=1` | **A4_PILOT_GREEN** |
| A4.9 Two-device pilot + sign-off | Grok Bot (with Jeff) | Real run from Jeff's two devices through our relay to the allowlist; kill-switch drill; ledger + logs reviewed; Linear filing once this doc locks | **A4_PILOT_RUN_SIGNED_OFF** (manual checklist, not on box) |

Order: A4.0 → A4.1 → (A4.2, A4.3, A4.4, A4.6, A4.7 in parallel) → A4.5 → A4.8 → A4.9.

### A4.8 must prove (all on-box, no internet)

1. Off-allowlist host refused **at the Peer** (`egress_not_allowlisted`, no connect attempted) and, with a test Peer that skips its own check, **at the Gateway** (same reason, OPEN never forwarded).
2. Allowlisted name resolving to a private / loopback / link-local / `169.254.169.254` / mixed public+private answer → `egress_resolved_non_public`; rebinding stand-in (first answer public, second private) → second OPEN refused.
3. Literal-IP OPEN → `egress_not_allowlisted`.
4. n0 relay URL, another relay URL, a look-alike of ours (different port/host/scheme) → `relay_refused`; n0 host as `SPIKE_RELAY_ALLOW_URL` → `relay_config`; discovery on → `discovery_refused`.
5. Kill switch: flag off → open streams closed and new OPENs `egress_off` within **5 s**; Control stopped → off within 5 s (stale); `SPIKE_PUBLIC_EGRESS=0` with flag on → off.
6. Allowlist version mismatch → `egress_allowlist_mismatch`; byte cap → `egress_budget_exceeded`.
7. `STRIPE_TEST_SECRET_KEY=sk_live_…` → Control refuses to start; replayed signed webhook credits once and prints `UX c2_add_funds_test`; unset → mock top-up still green.
8. Default path unchanged: `all` → SPIKE_DOD_GREEN and `a3` → A3_IROH_LOCAL_GREEN in the same run, plus `iroh_pilot` with no `SPIKE_PUBLIC_EGRESS` refuses every OPEN.
9. Screen order and copy: `UX_SCREEN p1_isp_ack` → `p2_consent` → `p9_allowlist` → relay dial with `SPIKE_PUBLIC_EGRESS=1`; **no** `p9_allowlist` with it off; `UX_SCREEN p8_offline` on reject / drop / kill-switch off, with no raw reason code in the user-facing lines and no redial after `endpoint_mismatch`; `c2_add_funds_test` on test top-up. **Every required phrase is read from `fixtures/screens.json`** (`required_copy` of `p1_isp_ack`, `p8_offline`, `p9_allowlist`, `c2_add_funds_test`, plus `forbidden_user_facing_substrings`), nothing hardcoded in `run_local_asserts.py`, same as a3 does for `p2_consent`. `p9_allowlist` output must not contain any `SPIKE_EGRESS_ALLOWLIST` hostname.
10. No default route in any a4 netns (same check as a3).

### Local stand-ins (on-box lanes only)

- **Cargo feature `a4_local`** is defined on **stream-proto** (no deps, `Cargo.lock` unchanged) and switches `guard::Lane::build()` to `Lane::Local`. Platform's stream-peer / stream-gateway features forward it: `a4_local = ["stream-proto/a4_local"]`. Pilot builds never enable it. The Python mirror takes the lane as an explicit argument (`LANE_PILOT` default).
- **Relay:** `iroh-relay` dev mode on `10.73.0.254` (http). Accepted only in `Lane::Local`, and only as `http://10.73.0.254:<port>/`; the pilot build doesn't contain it.
- **"Public" test sites:** an echo/HTTPS server in its own netns on `198.51.100.10` (TEST-NET-2: never routed on the internet). Names come from a test resolver override (`SPIKE_EGRESS_TEST_RESOLVER`, `a4_local` builds only). The SSRF check treats exactly `198.51.100.0/24` as public **only** under `a4_local`; private / metadata ranges stay refused even there.
- **Stripe:** signed fixture webhook events; no call to `api.stripe.com` from the box in `a4`.

## Run (target)

```bash
# On-box (needs passwordless `sudo -n`; single-instance like a34, own lock /tmp/stream-spike-a4.lock;
# builds into rust/target/pilot with --features iroh_pilot,a4_local; no internet).
python3 scripts/run_local_asserts.py a41                     # guard only → A4.1_PILOT_GUARD_GREEN
python3 scripts/run_local_asserts.py a4                      # A4.0–A4.7 + negatives → A4_PILOT_GREEN
SPIKE_A4=1 bash scripts/demo_alpha.sh                        # Alpha proof (+A3 if SPIKE_A3=1) + A4 block → A4_PILOT_GREEN
python3 scripts/run_local_asserts.py all                     # default path still → SPIKE_DOD_GREEN
SPIKE_A3=1 bash scripts/demo_alpha.sh                        # still → A3_IROH_LOCAL_GREEN

# Pilot run (A4.9, Jeff's devices only; values from the locked config, not this doc):
SPIKE_TRANSPORT=iroh_pilot SPIKE_PUBLIC_EGRESS=1 \
SPIKE_RELAY_ALLOW_URL=https://<our-relay-host>/ \
SPIKE_EGRESS_ALLOWLIST=<test-site-1>:443,<test-site-2>:443 \
SPIKE_ISP_ACK_VERSION=<v> SPIKE_IROH_KEY_PATH=<per-device> stream-peer
```

Done = A4.0–A4.8 green on one tip with every existing green unchanged, then A4.9 signed off by Jeff.

## Risks / open questions

1. **Where the relay (and Gateway + Control) run.** Jeff's box vs a small VPS. Peers on home networks must reach the relay and Control over the internet; this box isn't reachable from outside. Proposal: relay + Gateway + Control on one small VPS, TLS via Let's Encrypt on a hostname we own, Gateway client/admin bound to localhost (SSH tunnel). Needs Jeff's call and a domain.
2. **Test-site allowlist contents.** Proposal: 1–2 hosts **we control** (a tiny self-hosted echo/httpbin-style service on the VPS hostname, e.g. `echo.<our-domain>:443`), plus at most `example.com:443` as a third-party sanity target. Avoid CDN-fronted sites: on shared CDN IPs, SNI/Host could reach other tenants (domain fronting), which the host:port check can't see.
3. **Jeff's ISP terms.** The P1 ack covers it legally only with counsel's wording; until then sharing is limited to Jeff's own devices and traffic stays tiny (byte cap).
4. **Stripe webhook reachability.** Control must be reachable from Stripe (VPS public HTTPS, a tunnel, or `stripe listen` with Jeff's login). Test key + login only in Jeff's 1:1 with Platform.
5. **Per-device keys.** Each device gets its own `SPIKE_IROH_KEY_PATH` (a shared key → `endpoint_mismatch`, as in A3.4). Keys are generated on the device and never leave it; Control records endpoint IDs at enroll.
6. **iroh hole-punching / direct paths.** With a relay on, iroh 0.95.1 learns public direct addresses via the relay and may upgrade to a direct UDP path to the Gateway. That's transport only (not Peer egress), but it breaks "relay-only". Peer Engineer to confirm whether 0.95.1 can pin relay-only; if not, accept direct Peer↔Gateway paths and document it, or bump iroh (the toolchain bump from ALPHA3 risk 1 becomes slice **A4.R**).
7. **DNS on the device.** Resolve-and-pin needs our own resolver call (no `connect(host)` shortcuts). Device DNS may be ISP/router-controlled; the public-IP check is the safety net, not the resolver.
8. **What's logged.** Per OPEN/CLOSE: account, time, host, port, bytes, location tier, reason. Retention for the pilot: 30 days on the VPS, deleted after sign-off unless Jeff says otherwise. Never contents, never full URLs (CONNECT carries host:port only).
9. **Budget caps.** Pilot default 50 MB/day/Peer and ≤2 concurrent streams per Peer; Jeff to confirm. Caps are enforced at both ends.
10. **Kill-switch latency vs polling.** 1 s poll / 5 s stale is simple; a push channel can come later. If Control is down, egress is off: that's intended, but the Peer shows `p8_offline`, not a crash.

## Out of Alpha-4

Anyone other than Jeff's two devices sharing; general / non-allowlisted egress; n0/community relays and public discovery; live Stripe charges and payouts; KYC; mobile app stores; NAT simulation on box; Control→Rust (TOM-8); final P1 legal wording (needed before Alpha-5 widens the pilot).
