# Alpha-4 pilot copy (Stream Designer)

Source of truth: `fixtures/screens.json`. Draft for the Alpha-4 pilot (own relay, Jeff's two devices, Stripe test mode, allowlisted egress only). Existing `required_copy` is unchanged, so A0–A3 greens are unaffected.

| Screen | Fixture id | What changes | Required greps |
|---|---|---|---|
| P1 ISP warning | `p1_isp_ack` | Copy unchanged; `legal_status: pending_counsel`. OK while only Jeff's devices share; counsel's final text required before anyone else shares. | unchanged |
| C2 Add funds (test mode) | `c2_add_funds_test` (new) | Stripe test-mode top-up. Balance updates only after Control gets the payment confirmation. `c2_add_funds_mock` stays for keyless A1.3 runs. | `Add funds`, `Test mode`, `no real charge`, `Funds added` |
| P8 You're offline | `p8_offline` (new) | System-stopped state (rejected or dropped), separate from P4 user pause. Reason codes map to plain `reason_lines`; never show raw codes. No auto-retry after `endpoint_mismatch`. | `isn't sharing right now`, `No traffic is going through your connection` |
| P9 Pilot: limited sharing | `p9_allowlist` (new) | Shown after P2, before first dial, only when public egress is on. Never names the test sites. | `short list of test sites`, `blocked on this device` |

## Order (Peer, egress on)
P1 ack → P2 consent → P9 allowlist note → dial. On drop or reject → P8. User pause → P4 (unchanged).

## Asks
- Peer: emit `UX_SCREEN p8_offline` / `p9_allowlist` with the fixture lines; mirror any fixture copy the Rust build needs the same way as `p2_consent.json`.
- Platform: emit `UX c2_add_funds_test` on test-mode top-up; keep mock path for keyless runs.
- Architect: add the new greps to the A4 asserts, read from the fixture.
