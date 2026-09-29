# A1.1 — Iroh loopback lane (stubs)

**Owner:** Platform Engineer (+ Peer for dial side)  
**Stance:** stubs only · **not** real Iroh · **no public egress** · community/production relays HOLD

## What this is

An **opt-in** transport mode that reuses the same newline-delimited JSON frames as fake Relay (`HELLO` / `AUTH_TICKET` / `ALPN stream/tunnel/1`), but:

| | fake Relay (default) | iroh_loopback (A1.1) |
|--|----------------------|----------------------|
| Env | `SPIKE_TRANSPORT=fake_relay` (default) | `SPIKE_TRANSPORT=iroh_loopback` (or `iroh`) |
| Listen | `SPIKE_FAKE_RELAY` (default `0.0.0.0:9100`) | `SPIKE_IROH_LOOPBACK` (default **`127.0.0.1:9101`**) |
| Dial | `SPIKE_FAKE_RELAY_DIAL` | `SPIKE_IROH_LOOPBACK_DIAL` (default `127.0.0.1:9101`) |
| Guard | — | Gateway **refuses** non-loopback bind; Peer **refuses** non-loopback dial |

UX stays invisible: same screens / `[Brand]` / no new waive strings.

## Proof

```bash
python3 scripts/iroh_loopback_smoke.py
# → A1.1_IROH_LOOPBACK_GREEN
```

Default DoD (`run_local_asserts.py all`) stays on **fake_relay**.

## Non-goals

Real Iroh stack, mDNS/WAN discovery, community relays, public listen addresses.
