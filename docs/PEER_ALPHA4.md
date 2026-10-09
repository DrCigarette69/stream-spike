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

Tests: `pilot_egress_tests.rs` (injected resolver; loopback sockets only) and
`scripts/a44_peer_egress_smoke.py` -> `A4.4_PEER_EGRESS_PART1_GREEN` (TEST-NET-2
stand-in 198.51.100.10:443 on `lo` of a no-default-route netns; a4_local lane injected
in the test since the cargo feature isn't forwarded by stream-peer yet).

Part 1 limits / part 2: frames carry byte counts, not data, so OPEN holds the pinned
connection and counts upstream bytes into a local sink; part 2 pipes it through the
Gateway data path (`pump`). Part 2 also needs Platform's `iroh_pilot` (`pilot`) feature +
lock, A4.2 relay (RelayOnly dial via `check_relay_url_with`), `check_transport_pilot`
in the pilot build, and stream-peer forwarding `a4_local` so the binary-level a4 lane
can use the TEST-NET-2 exception and test resolver.

## Open / asks
- Kill-switch reason is `egress_off` everywhere (guard, fixture `reason_lines`, Peer).
