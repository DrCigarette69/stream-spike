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
`scripts/a44_peer_egress_smoke.py` -> `A4.4_PEER_EGRESS_GREEN` (TEST-NET-2
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

Endpoint (`iroh_pilot_dial.rs`): `RelayMode::Custom(RelayMap::from(RelayConfig { url: <our relay>, quic: None }))`
(QUIC address discovery off),
`clear_discovery()`, `PathSelection::RelayOnly`, Peer key, guarded private binds
(`SPIKE_IROH_BIND`, default 127.0.0.1:0). Relay TLS is always verified; a unit test fails if
the skip-verify builder option appears anywhere in `stream-peer/src`.
`peer iroh_pilot bound [..] relay=<url> path=relay_only discovery=off quic=none`.

Dial target: `SPIKE_GATEWAY_ENDPOINT_ID` must match; relay = `SPIKE_IROH_GATEWAY_RELAY_URL`
(default: the allowed URL) checked with `check_relay_url_with` (else `relay_refused`);
`check_relay_required`; any `SPIKE_IROH_GATEWAY_ADDR` entries pass the A3.0 private guard but
are never dialed. Dial = `EndpointAddr(gateway).with_relay_url(relay)` after
`UX_SCREEN p1_isp_ack` -> `p2_consent` -> `p9_allowlist`.
AUTH_TICKET (pilot): ticket `relay_url` (top level or `payload`) must be our relay
(`relay_refused`), empty `direct_addrs` needs one (`relay_required`); per-OPEN reject only.

Relay-path watcher (`relay_path.rs` + `pilot_target::watch_path`, every 100 ms): primary =
ANY non-zero endpoint UDP datagram count (send+recv, v4+v6; trait `UdpCounterSource`), secondary
= `conn_type` Direct/Mixed (iroh 0.95.1 keeps it `None` under RelayOnly). Trip -> close all pilot
egress (CLOSE reason `relay_path_required`), kill the transport via the slot (<= 2 s),
`a4_refuse_relay_path_required:udp_datagrams:<n> (...)` or `:direct_path:<addr>` (same reason as P8), P8
`relay_path_required` once, `left relay path: closed, not retrying`, no redial.
Logs: `peer iroh_pilot udp_counters ...` after bind, `path ...` at connect, `session_end ...`.
**Counter source:** iroh 0.95.1 only exposes the magicsock counters via `Endpoint::metrics()`
(iroh feature `metrics`); stream-peer's `iroh_pilot` turns it on (as Gateway's does):
`iroh_pilot = ["iroh_local", "iroh/test-utils", "iroh/metrics"]` (no lock change).
`udp_source()` (`MagicsockUdp` in `iroh_pilot_dial.rs`) reads `send_ipv4`/`send_ipv6` /
`recv_data_ipv4`/`recv_data_ipv6` + `recv_disco_udp` (inbound disco over UDP, not split by
family, folded into `recv_v4`; the logged line format is unchanged). Values are bytes (+ disco
packets); only zero vs non-zero matters. If counters were ever unreadable the pilot lane fails closed
after bind (`a4_refuse_relay_path_required:udp_counters_unavailable`, P8 `relay_path_required`, no dial);
a4_local lanes would go on with conn_type only.
On-box hook (a4_local builds only; the name is not in pilot binaries):
`SPIKE_A4_FAKE_UDP_DATAGRAMS=<n>` adds n to the counters.
Gateway `AUTH_REJECT` / `endpoint_mismatch` is terminal for iroh_pilot as for iroh_local:
`peer iroh_pilot AUTH_REJECT endpoint_mismatch: closed, not retrying`, P8 `endpoint_mismatch`, no
redial (a44: Control ticket for key A sent to the Peer restarted on key B).
A pilot egress P8 (`egress_*`) is not replaced by a later generic `connection_lost`.

Relay-path e2e (now part of `scripts/a44_pilot_e2e.py`, run by a44; A4.2 topology, relay
`http://10.73.0.254:3340/`, Peer .200): real relay-only connect, Gateway UDP counters 0 at
connect / after ticket / after 3 s, Control relay ticket -> Peer `egress_off` (kill switch off
until A4.3); hook=1 -> exact P8 line within 2 s, `not retrying`, one connect only.
a44 marker: `A4.4_PEER_EGRESS_GREEN` since the DATA pump (A4.3 on main).

## A4.1 amendment (pins) + A4.3 part 1 frames on the Peer

Pins: `SPIKE_EGRESS_ALLOWLIST` entries `host:443=IP` and `*.sslip.io` hosts (embedded IPv4) are
pinned by `EgressAllow::parse_for(.., Lane::build())`; `resolve_and_pin` returns the pin without
calling the resolver, and the Peer connects to exactly that address (`pilot_pin_tests.rs`: a
resolver that panics if called; a44 netns `a44_netns_pinned` -> TEST-NET-2 stand-in). The
reported `allowlist_version` covers pins. Load failures (non-public pin / embedded IP ->
`a4_refuse_egress_allowlist_config`, conflicting pins -> `a4_refuse_egress_pin_mismatch`) exit 2
at startup before any state/admin exists, so they never reach P8 (a44 checks the real binary).
The allowlist is never re-loaded at runtime; a Control-side load failure arrives as
`public_egress:false` + version `""` -> P8 `egress_off` (flag off wins over the mismatch).

Frames (Platform e20498d / 9cff11c): `EGRESS_STATE` is parsed as `stream_proto::relay::EgressState`
(type/public_egress/allowlist_version/ts required; malformed ignored -> stale -> off; `ts` is not
age-checked, freshness is time of receipt). Peer CLOSE = `relay::Close::with_reason` (`reason`,
no `detail`); Gateway CLOSE `reason`/`detail` are logged (`peer gateway_close ...`).

## A4.4 DATA pump (`pilot_data.rs`)

Gateway `DATA` -> that stream's queue (64 chunks) -> pinned site socket; site bytes -> `DATA`
(<= 16384 raw bytes per frame) -> Gateway; site EOF/write error -> `CLOSE {reason: site_closed}`
after the last DATA (both go through one per-session outbound channel, written by the session
loop); Gateway `CLOSE` drops the socket, no echo. Budget counts only bytes read from / written
to the pinned socket. Kill switch off / stale / mismatch / cap -> `close_all` (<= 5 s via the
watchdog) with their CLOSE reasons. Session end closes all streams. The session loop now reads
lines with a cancel-safe reader (`frames::LineReader`, `read_until` keeps partial bytes) so large
DATA lines are never torn by the 200 ms tick. Bad / > 16 KiB / unknown-stream DATA is ignored.
E2E: `scripts/a44_pilot_e2e.py` (run by a44) -> `A4.4_PEER_EGRESS_GREEN`.

## Open / asks
- Kill-switch reason is `egress_off` everywhere (guard, fixture `reason_lines`, Peer).

## A4.5 pilot image (amd64 only)

`rust/crates/stream-peer/Dockerfile.pilot` (compose Dockerfile untouched): builder
`FROM --platform=$BUILDPLATFORM rust:1.85-bookworm`, native x86_64-unknown-linux-gnu,
`cargo build --release --locked -p stream-peer
--features iroh_pilot` (never `a4_local`). Runtime: debian bookworm-slim with no RUN step, CA bundle copied from the builder, uid 10001, `VOLUME /data` (key +
budget file, nothing baked in), `SPIKE_TRANSPORT=iroh_pilot`, admin 127.0.0.1:9200, no consent
presets, HEALTHCHECK on `/health` via `peer-admin` (`pilot/peer-admin`: bash /dev/tcp client,
also the documented admin tool, so no curl in the image).
`scripts/a45_peer_package_smoke.py` -> `A4.5_PEER_PACKAGE_GREEN`: builds amd64, refuses without
`SPIKE_RELAY_ALLOW_URL`, on-box relay 10.73.0.254 refused (Pilot lane), `SPIKE_A4_FAKE_UDP_DATAGRAMS`
and `insecure_skip_relay_cert_verify` absent from the binary, no key/budget in any layer,
non-root, key 0600 in `/data`, runs healthy with `--network none`, same endpoint id after
restart. Metrics check: the image logs `udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0
recv_v6=0` after bind and never `udp_counters_unavailable`. Docker only via `-H unix:///var/run/docker.sock`; refuses if DOCKER_HOST is not unix://.
Pilot ships amd64 only (both pilot devices are x86_64). Approved image, pinned by ID (never by
the `peer-pilot:amd64` tag): `sha256:2452f56d86fe01d99cbfb3f284ef79fb99471017014c0dacbdb364a2dc41e21a`. Install/run
reference that ID; `a45` checks it with `SPIKE_PEER_IMAGE_ID` and prints `A45_IMAGE_ID=<id>`. A new
ID replaces it only after `a45` passes on it and the room agrees.
Install steps for the pilot devices: `docs/PEER_PILOT_INSTALL.md`.
