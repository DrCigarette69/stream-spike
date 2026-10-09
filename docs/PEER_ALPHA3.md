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
| Dial gate + screen IDs | `src/iroh_local.rs` | `pre_dial()` requires ISP ack (P1), P2 consent and not killed, then prints `UX_SCREEN p1_isp_ack` then `UX_SCREEN p2_consent` (stderr, one per line) before any dial. `/peer/kill` prints `UX_SCREEN p4_kill` (every transport) after the existing `UX P4` block. |
| Kill ≤ 2 s | `src/kill.rs` | `TransportSlot` + `ActiveTransport` trait; `/peer/kill` calls `kill("peer_kill")`: graceful close bounded by `KILL_DEADLINE` (2 s), then hard drop. |

P2 consent: `PeerState.p2_consent` is set together with an accepted ISP ack (enroll accepts P1 + P2 together) and cleared with it; a preset `SPIKE_ISP_ACK_VERSION` counts as enrolled. Not exposed in `/peer/status` (keeps Python parity).

## Env

| Var | Default | Use |
|--|--|--|
| `SPIKE_TRANSPORT=iroh_local` | `fake_relay` | opt-in real-iroh mode (Rust only) |
| `SPIKE_IROH_KEY_PATH` | `./.peer_iroh_key` | persistent Peer secret key (0600) |
| `SPIKE_GATEWAY_ENDPOINT_ID` | empty (→ every ticket refused) | fixed dev Gateway endpoint ID |
| `SPIKE_IROH_ALLOW_CIDRS` | `10.73.0.0/24,127.0.0.0/8` | A3.0 guard narrowing (empty = all private ranges) |

## Part 2 (after the iroh lock lands)

1. Under `#[cfg(feature = "iroh_local")]`: `SecretKey::from_bytes(key.bytes())`; set `endpoint_id` from the key (ignore `SPIKE_ENDPOINT_ID` in this mode).
2. Endpoint from the empty builder, `RelayMode::Disabled`, `clear_discovery()`, bind to a guard-checked private addr; `guard::check_discovery(false)`.
3. Loop: `pre_dial()` → get ticket → `check_dial_target()` → connect to `EndpointAddr(gateway_id, direct_addrs)` on ALPN `stream/tunnel/1` → frames on a bi-stream (reuse `frames.rs`) → register connection in `TransportSlot` so kill closes it ≤ 2 s.
4. Run inside `ns-peer-a` / `ns-peer-b` (A3.4, no default route) → **A3.2_PEER_DIAL_GREEN**.
