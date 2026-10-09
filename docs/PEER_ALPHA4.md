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

## Open / asks
- ALPHA4_PILOT.md reason `egress_disabled` has no `reason_lines` key (fixture uses
  `egress_off`); today it maps to the `connection_lost` fallback. Designer/Architect to align.
