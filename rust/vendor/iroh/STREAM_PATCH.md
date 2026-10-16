# Stream patch on iroh 0.95.1

`rust/vendor/iroh` is the crates.io iroh 0.95.1 source, unchanged except for the one hunk below. It's wired in by
`[patch.crates-io] iroh = { path = "vendor/iroh" }` in `rust/Cargo.toml` (same version, so `Cargo.lock`
only drops iroh's registry source and checksum).

## Why

Under `PathSelection::RelayOnly` (our `iroh_pilot` builds, which enable `iroh/test-utils`), upstream 0.95.1
still queues a call-me-maybe over the relay on every heartbeat (~5 s, `stayin_alive` → `send_call_me_maybe`).
The remote treats it as a hole-punch request, and it carries our direct addresses. The patch returns after
`send_pings` (which already skips UDP pings in RelayOnly) without queueing `SendCallMeMaybe`.

## Exact diff

```diff
--- a/src/magicsock/endpoint_map/endpoint_state.rs (crates.io iroh 0.95.1)
+++ b/src/magicsock/endpoint_map/endpoint_state.rs (rust/vendor/iroh)
@@ -608,6 +608,14 @@
         // accepts the connection.
         let mut msgs = self.send_pings(now);
 
+        // stream-spike patch (A4.2): RelayOnly never hole-punches, so don't ask the remote to
+        // (call-me-maybe also advertises our direct addresses). Upstream 0.95.1 sends it anyway.
+        #[cfg(any(test, feature = "test-utils"))]
+        if self.path_selection == PathSelection::RelayOnly {
+            trace!("RelayOnly: not sending call-me-maybe");
+            return msgs;
+        }
+
         if let Some(url) = self.relay_url() {
             debug!(%url, "queue call-me-maybe");
             msgs.push(PingAction::SendCallMeMaybe {
```

## Not from call-me-maybe: the Peer-side RelayOnly warning

The Peer's log line `in \`RelayOnly\` mode, ignoring request to respond to a hole punching attempt.`
(`endpoint_state.rs:657` in this vendored copy) comes from `send_pings`, which the Peer's **own** 5 s
heartbeat (`stayin_alive`) calls. It isn't triggered by a call-me-maybe from the Gateway, so it keeps appearing
after this patch. It's harmless: RelayOnly returns before any UDP ping is sent.

## How to remove it (e.g. after the iroh bump, ALPHA4 slice A4.R)

1. Delete the vendored copy: `git rm -r rust/vendor/iroh`.
2. Delete the `[patch.crates-io]` block (and its comment) from `rust/Cargo.toml`.
3. `cd rust && cargo update -p iroh` (restores the registry source + checksum in `Cargo.lock`; add
   `--precise <version>` if bumping).
4. **Re-check `hole_punch_attempts=0`:** run `python3 scripts/run_local_asserts.py a4` and confirm
   `OK [a42] gateway path=relay, hole_punch_attempts=0`, `OK [a44] … hole_punch_attempts=0` and
   `A4_PILOT_GREEN`. Upstream 0.95.1 without this patch fails that check. Then redeploy the Gateway and
   confirm `gateway hole_punch attempts=0` in its log.
