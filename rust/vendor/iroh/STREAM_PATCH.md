# Stream patch on iroh 0.95.1 (crates.io source, unchanged otherwise)

`src/magicsock/endpoint_map/endpoint_state.rs` `send_call_me_maybe`: under `PathSelection::RelayOnly`
(test-utils builds = our `iroh_pilot`), return after `send_pings` (which already skips UDP pings in
RelayOnly) without queueing `SendCallMeMaybe`. Upstream keeps sending call-me-maybe over the relay
every heartbeat (~5 s), which the remote treats as a hole-punch request and which carries our direct
addresses. Drop this vendor copy when iroh is bumped past a fix (ALPHA4 slice A4.R).
Diff: `diff -r ~/.cargo/registry/src/*/iroh-0.95.1 rust/vendor/iroh`.
