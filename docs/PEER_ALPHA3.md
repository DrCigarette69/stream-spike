# Peer — Alpha-3 A3.2 (Rust)

**Owner:** Peer Engineer · **Spec:** [`ALPHA3_IROH.md`](ALPHA3_IROH.md) A3.2 / A3.3 / A3.5

## Status

**Part 1 (prep, no iroh crate) — done.** Default build only; nothing feature-gated except the refusal check.
**Part 2 (real iroh dial) — waiting on** Platform's workspace commit (`iroh =0.95.1`, `iroh_local` feature on `stream-peer`, 4 crypto pins, lock) and A3.1 Gateway endpoint.

| Piece | File | Notes |
|--|--|--|
| Persistent secret key | `rust/crates/stream-peer/src/iroh_key.rs` | 32 raw bytes from `/dev/urandom`, created once with mode 0600, reloaded unchanged; refuses group/world-accessible (`iroh_key_insecure_perms`) or wrong-size (`iroh_key_bad_len`) files. `IrohKey::bytes()` → `iroh::SecretKey::from_bytes` in part 2. |
| Ticket dial checks | `src/iroh_ticket.rs` | Uses `stream_proto::guard` (A3.0). `gateway_endpoint_id` ≠ `SPIKE_GATEWAY_ENDPOINT_ID` (or either empty) → `gateway_mismatch`; empty/missing `direct_addrs` → `direct_addrs_required`; guard reasons pass through (`public_addr`, `allowlist_miss`, `bad_addr`, `relay_refused`) and log `a3_refuse_non_private:<addr>` / `a3_refuse_<reason>:<detail>`. Wired into `/peer/verify_ticket` when transport is `iroh_local`. |
| Transport scaffolding | `src/iroh_local.rs`, `config.rs`, `main.rs` | `SPIKE_TRANSPORT=iroh_local` skips the TCP relay loop. Without the `iroh_local` feature: `last_error = iroh_local_feature_not_built`, log `A3 iroh_local refused: iroh_local_feature_not_built`, no dial, admin `/health` stays up. `fake_relay` / `iroh_loopback` unchanged. |
| Dial gate + screen IDs | `src/iroh_local.rs` | `pre_dial()` requires ISP ack (P1), P2 consent (own gate, below) and not killed, then prints `UX_SCREEN p1_isp_ack` then `UX_SCREEN p2_consent` (stderr, one per line) before any dial. `/peer/kill` prints `UX_SCREEN p4_kill` (every transport) after the existing `UX P4` block. |
| Kill ≤ 2 s | `src/kill.rs` | `TransportSlot` + `ActiveTransport` trait; `/peer/kill` calls `kill("peer_kill")`: graceful close bounded by `KILL_DEADLINE` (2 s), then hard drop. |

### P2 consent gate (own step after P1)

- `GET /peer/consent/p2` → `{screen, id, title, body_lines, button, cta, required_copy, user_facing, ok}` from the `p2_consent` entry of `fixtures/screens.json` (Designer, 39edb49), vendored verbatim as `rust/crates/stream-peer/src/p2_consent.json` because the Docker build context is `rust/`. A unit test fails if the two differ, if any `required_copy` fragment is missing from `body_lines`, or if a forbidden string appears. To update the copy, edit the fixture and re-copy the entry.
- `POST /peer/consent {"accepted": true}` → 200 + `p2_consent: true` only if the ISP ack is set; else 403 `{"error": "isp_ack_required_before_consent", "code": "p1_ack_required"}`. `{"accepted": false}` clears it, closes any active iroh_local transport (≤ 2 s) and marks the Peer offline in `iroh_local` mode. Non-bool → 400 `bad_request`.
- Clearing the ISP ack (`POST /peer/ack {"isp_ack_version": ""}`) also clears P2. A preset `SPIKE_ISP_ACK_VERSION` no longer implies P2; use `SPIKE_P2_CONSENT=1` (ignored without an ack).
- stderr when P2 is shown or accepted (title, body_lines one per line, button):
  ```
  UX P2
  Sharing preferences
  We log your account, time, destination host and port, bytes, and location tier. We do not read page contents.
  Matching may pause when the network is thin or under load in your area.
  A compromised device can still see the destinations it connects to, so keep your OS updated.
  I agree, start sharing
  UX_SCREEN p2_consent
  ```
- P2 gates **only** `iroh_local` (`pre_dial` → `p2_consent_required`); fake_relay / iroh_loopback unchanged. Python Peer has no `/peer/consent`; `scripts/peer_alpha1_cli.py` tolerates its 404 only when `SPIKE_IMPL` is not `rust`.

## Env

| Var | Default | Use |
|--|--|--|
| `SPIKE_TRANSPORT=iroh_local` | `fake_relay` | opt-in real-iroh mode (Rust only) |
| `SPIKE_IROH_KEY_PATH` | `./.peer_iroh_key` | persistent Peer secret key (0600) |
| `SPIKE_P2_CONSENT` | unset | `1` = headless P2 preset (needs an ISP ack too) |
| `SPIKE_GATEWAY_ENDPOINT_ID` | empty (→ every ticket refused) | fixed dev Gateway endpoint ID |
| `SPIKE_IROH_ALLOW_CIDRS` | `10.73.0.0/24,127.0.0.0/8` | A3.0 guard narrowing (empty = all private ranges) |

## Part 2 (after the iroh lock lands)

1. Under `#[cfg(feature = "iroh_local")]`: `SecretKey::from_bytes(key.bytes())`; set `endpoint_id` from the key (ignore `SPIKE_ENDPOINT_ID` in this mode).
2. Endpoint from the empty builder, `RelayMode::Disabled`, `clear_discovery()`, bind to a guard-checked private addr; `guard::check_discovery(false)`.
3. Loop: `pre_dial()` → get ticket → `check_dial_target()` → connect to `EndpointAddr(gateway_id, direct_addrs)` on ALPN `stream/tunnel/1` → frames on a bi-stream (reuse `frames.rs`) → register connection in `TransportSlot` so kill closes it ≤ 2 s.
4. Run inside `ns-peer-a` / `ns-peer-b` (A3.4, no default route) → **A3.2_PEER_DIAL_GREEN**.
