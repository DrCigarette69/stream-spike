# Peer — Alpha-4 slice 1: P8 offline + P9 allowlist gate (Rust)

Status: copy + gates only. No egress allowlist, relay-only dial, or `iroh_pilot`
dial yet (A4.4, after A4.1). No Cargo features added or referenced (Platform owns
`iroh_pilot`/`a4_local` features). Default runs (`SPIKE_PUBLIC_EGRESS` unset) == Alpha-3.

## Copy (vendored, drift-tested)
- `rust/crates/stream-peer/src/p8_offline.json`, `p9_allowlist.json`: verbatim
  copies of the `fixtures/screens.json` `consent[]` entries (Designer fd19e0d,
  9567253, 6340c85). `a4_copy.rs` tests: equal to fixture, `required_copy` in body,
  no fixture `forbidden_user_facing_substrings` / `ux::FORBIDDEN`, no raw codes or `_`
  in any UX block, P9 names no test sites.

## P8 `p8_offline` (system stop; P4 stays the user pause)
Triggered by gateway close / stream end / dial failure (`connection_lost`),
`AUTH_REJECT` / `ERR` reasons (e.g. `endpoint_mismatch`, `endpoint_bind_required`).
Reason -> sentence via fixture `reason_lines` (all 8 keys incl. `relay_path_required`);
unknown code -> `connection_lost` sentence. Not shown for user stops (kill, P1 cleared,
P2 withdrawn, P9 withdrawn). Printed once per offline episode; cleared on HELLO_OK or kill.
stderr:
```
peer offline reason=<code>          # log line, may carry the raw code
UX P8
You're offline
This device isn't sharing right now.
No traffic is going through your connection.
<plain sentence for the reason>
Turn sharing back on
UX_SCREEN p8_offline
```
`endpoint_mismatch` / `endpoint_bind_required` remain terminal (no auto-retry).
Endpoints: `GET /peer/consent/p8` and `GET /peer/screen/p8` -> `{screen:"P8", id, title,
body_lines, button, cta, required_copy, offline, reason_line, reason_code, user_facing}`
(`reason_code` machine-only).

## P9 `p9_allowlist` (ALPHA4_PILOT.md names)
Applies only when `SPIKE_TRANSPORT=iroh_pilot` **and** `SPIKE_PUBLIC_EGRESS=1`
(`Config::p9_required()`); otherwise never shown and nothing changes (incl. iroh_local).
- `GET /peer/consent/p9` -> P9 copy (prints block + `UX_SCREEN p9_allowlist`);
  404 `{code:"p9_not_applicable"}` when not required.
- `POST /peer/consent/p9 {"accepted":true|false}`: 403 `{code:"p2_consent_required"}`
  if P2 not accepted; withdrawing disconnects the active transport. Clearing P1 or P2 clears P9.
- `pre_dial` refuses `p9_allowlist_required`; prints `UX_SCREEN p1_isp_ack`, `p2_consent`,
  `p9_allowlist` before dial.
- Headless preset `SPIKE_P9_ACK=1`, honored only when P1 + P2 presets are set and P9 is required.
- `SPIKE_TRANSPORT=iroh_pilot` currently refuses at startup with `last_error=iroh_pilot_not_built`
  (admin /health and consent endpoints stay up) until A4.4 lands the dial.

## A4.4 part 1: Peer egress allowlist (iroh_pilot)

All checks come from Architect's A4.1 `stream_proto::guard` (pilot.rs); the Peer
(`pilot_egress.rs`, `egress_state.rs`) only keeps state and does the I/O. Other
transports: no plane, Alpha-3 floor (#6) unchanged.

Env (iroh_pilot only; parse errors refuse start, exit 2, before anything binds):
- `SPIKE_EGRESS_ALLOWLIST` exact `host:port` (port 443 only), required.
  stderr `peer a4_refuse_egress_allowlist_config:<detail>`.
- `SPIKE_PUBLIC_EGRESS=1` (exactly `1`, guard rule; also drives P9).
- `SPIKE_EGRESS_STATE_URL` (default `$CONTROL_URL/v1/egress/state`), polled every 1 s;
  shape `{"public_egress": bool, "allowlist_version": "<sha256>", "ts": ..}`. The
  Gateway's `EGRESS_STATE` frame (same shape) is applied too. Unreachable / non-200 /
  malformed = no refresh -> stale after `SPIKE_EGRESS_STATE_MAX_AGE_MS` (default 5000,
  clamp 100..5000) = off.
- `SPIKE_EGRESS_BYTE_CAP` bytes / Peer / UTC day (default 50 000 000), both directions.
- `allowlist_version` = guard `EgressAllow::version()` (sha256 hex of sorted, deduped
  `host:port\n` lines). Printed at start (`peer egress_allowlist_version=<hex> entries=N
  public_egress_env=<bool>`), on `/health` `.egress` and `GET /peer/egress` (404
  `transport_not_pilot` outside iroh_pilot). Not added to HELLO (doc doesn't ask).

Per AUTH_TICKET (dest known), before AUTH_OK: kill switch + version (`check_open`) ->
budget -> floor -> `resolve_and_pin` (blocking resolver on spawn_blocking) -> pinned
`SocketAddr` stored on the stream. OPEN connects to that exact address only
(`peer egress_open <sid> <host:port> pinned=<ip:port>`). Refusal -> `AUTH_REJECT
{error:<reason>, egress_refused:true}` + `a4_refuse_<reason>:<detail>`; these reject that
OPEN only, never P8.

Watchdog (100 ms): off / stale / version mismatch / budget -> close every pilot egress
connection (`peer egress_close <sid> <host:port> reason=<reason>`), queue Peer `CLOSE
{stream_id, reason}` frames, show P8: `egress_off` (detail `flag_stale` ->
`egress_state_stale` line), `egress_allowlist_mismatch`, `egress_budget_exceeded`.
Back on -> P8 cleared (`peer egress_on`). User stops (P4 kill, P1 cleared, P2/P9
withdrawn) close pilot egress too, without P8. Startup/config failures exit before
any P8 (`connection_lost` fallback if ever shown).

Stream cap: `SPIKE_EGRESS_MAX_STREAMS` (default 2 = doc value; clamp 1..=16, invalid -> 2;
**raising it above 2 needs Jeff's OK**). Slots are reserved at AUTH_TICKET and freed on
refusal / close; a third concurrent OPEN -> `AUTH_REJECT {error:"egress_stream_limit",
egress_refused:true}` + `a4_refuse_egress_stream_limit:<max>`, never P8 (no guard reason
exists, so this is a Peer-local name).

Persistent byte budget: `<dir of SPIKE_IROH_KEY_PATH>/.peer_egress_budget` (override
`SPIKE_EGRESS_BUDGET_PATH`), JSON `{"utc_day":"YYYY-MM-DD","bytes":n,"allowlist_version":"<hex>"}`,
mode 0600, atomic (temp `<file>.tmp.<pid>` in same dir, fsync, rename, fsync dir). Written
after >= 1 MiB unsaved or >= 5 s with changes (watchdog), on every stream close, on cap hit,
on UTC rollover and on SIGINT/SIGTERM (`peer egress_budget flushed on shutdown`; pilot only,
other transports keep default signal handling). Start: `peer egress_budget path=<p> bytes=<n> cap=<cap>`.
- Missing -> 0. Older UTC day -> 0. Future day (clock moved back) -> count kept.
- Unreadable / not a regular file / group- or world-accessible -> `a4_refuse_egress_budget:state_unreadable`;
  bad JSON / fields -> `a4_refuse_egress_budget:state_corrupt`; failed write ->
  `a4_refuse_egress_budget:state_unwritable:<err>`. All fail closed until the next UTC day
  (P8 `egress_budget_unreadable`; the file is left untouched that day, rewritten at rollover).
- Real cap hit -> P8 `egress_budget_exceeded` (counter is stored at the cap).
- Only real bytes on the pinned socket count (both directions, in `pump`); Gateway `BYTES n`
  frames no longer count toward the pilot budget (no double count).

Tests: `pilot_egress_tests.rs` (injected resolver; loopback sockets only) and
`scripts/a44_peer_egress_smoke.py` -> `A4.4_PEER_EGRESS_PART1_GREEN` (TEST-NET-2
stand-in 198.51.100.10:443 on `lo` of a no-default-route netns, plus 3rd-stream refusal and a
real-binary restart that keeps `bytes_today` and a corrupt counter that fails closed; a4_local lane injected
in the test since the cargo feature isn't forwarded by stream-peer yet).

Part 1 limits / part 2: frames carry byte counts, not data, so OPEN holds the pinned
connection and counts upstream bytes into a local sink; part 2 pipes it through the
Gateway data path (`pump`). Part 2 also needs Platform's `iroh_pilot` (`pilot`) feature +
lock, A4.2 relay (RelayOnly dial via `check_relay_url_with`), `check_transport_pilot`
in the pilot build, and stream-peer forwarding `a4_local` so the binary-level a4 lane
can use the TEST-NET-2 exception and test resolver.

## A4.4 part 2: relay-only pilot dial (feature `iroh_pilot`)

Build: `cargo build --locked -p stream-peer --features iroh_pilot` (pilot package) or
`--features iroh_pilot,a4_local` (on-box lane only: allows relay `http://10.73.0.254:<port>/`
and TEST-NET-2 stand-ins). Without the feature, `SPIKE_TRANSPORT=iroh_pilot` still refuses
with `iroh_pilot_not_built` and admin stays up.

Startup (exit 2 before anything binds): `peer a4_refuse_transport_not_pilot:<t>` unless
`SPIKE_TRANSPORT=iroh_pilot`; `peer a4_refuse_relay_config:<url|<unset>>` unless
`SPIKE_RELAY_ALLOW_URL` is exactly one acceptable relay (n0/community hosts, local names,
non-public IPs, `http` outside a4_local, lists all refused by `RelayAllow::parse`).

Endpoint (`iroh_pilot_dial.rs`): `RelayMode::Custom(RelayMap::from(<our relay>))`,
`clear_discovery()`, `PathSelection::RelayOnly`, Peer key, guarded private binds
(`SPIKE_IROH_BIND`, default 127.0.0.1:0). Relay TLS is always verified; a unit test fails if
the skip-verify builder option appears anywhere in `stream-peer/src`.
`peer iroh_pilot bound [..] relay=<url> path=relay_only discovery=off`.

Dial target: `SPIKE_GATEWAY_ENDPOINT_ID` must match; relay = `SPIKE_IROH_GATEWAY_RELAY_URL`
(default: the allowed URL) checked with `check_relay_url_with` (else `relay_refused`);
`check_relay_required`; any `SPIKE_IROH_GATEWAY_ADDR` entries pass the A3.0 private guard but
are never dialed. Dial = `EndpointAddr(gateway).with_relay_url(relay)` after
`UX_SCREEN p1_isp_ack` -> `p2_consent` -> `p9_allowlist`.
AUTH_TICKET (pilot): ticket `relay_url` (top level or `payload`) must be our relay
(`relay_refused`), empty `direct_addrs` needs one (`relay_required`); per-OPEN reject only.

Direct-path watcher: polls `conn_type(gateway)` every 100 ms; `Direct`/`Mixed` -> close all
pilot egress (CLOSE reason `relay_path_required`), kill the transport via the slot (<= 2 s),
`a4_refuse_relay_refused:direct_path:<udp addr>`, P8 `relay_path_required`, no auto-retry.
Gateway `AUTH_REJECT` / `endpoint_mismatch` is terminal for iroh_pilot as for iroh_local.
A pilot egress P8 (`egress_*`) is not replaced by a later generic `connection_lost`.

E2E status: not yet. Gateway has no relay support and there is no self-hosted relay on
main (A4.2), and no data frame (A4.3), so a44 stays at `A4.4_PEER_EGRESS_PART1_GREEN`.
Manual a4_local run in a no-default-route netns: bound relay-only, P1->P2->P9 printed, dial
times out (relay unreachable), P8 egress_off, kill -> P4.

## Open / asks
- Kill-switch reason is `egress_off` everywhere (guard, fixture `reason_lines`, Peer).
