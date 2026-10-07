# SPIKE_IMPL=rust (A2.3)

Helper: `scripts/spike_peer_launch.py` — `peer_cmd()` · `gateway_cmd()` · `control_cmd()` (always Python).

| `SPIKE_IMPL` | Peer | Gateway | Control |
|--------------|------|---------|---------|
| unset / `python` (default) | `peer/main.py` | `gateway/main.py` | `control/main.py` |
| `rust` | `rust/target/debug/stream-peer` | `rust/target/debug/stream-gateway` | `control/main.py` |

Same env/ports: **8080** Control · **1080** Gateway admin · **9100** fake Relay · **9101** iroh loopback · **9200** Peer admin.

```bash
cd rust && ./pin-msrv-deps.sh   # if needed
cd rust && cargo build -p stream-peer -p stream-gateway

# Default Python peer+gateway (DoD must stay green)
python3 scripts/run_local_asserts.py all          # → SPIKE_DOD_GREEN

# Rust peer+gateway
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py all   # → SPIKE_DOD_GREEN
SPIKE_IMPL=rust python3 scripts/run_local_asserts.py a11   # → A1.1_IROH_LOOPBACK_GREEN
SPIKE_IMPL=rust ./scripts/demo_alpha.sh
```

Smokes that honor the helper: `peer_smoke.py`, `peer_alpha1_cli.py`, `iroh_loopback_smoke.py`, `platform_smoke.py` (gateway).

See `docs/PEER_ALPHA2.md` · `docs/ALPHA2_RUST.md` · `docs/LOCAL_ASSERTS.md`.
