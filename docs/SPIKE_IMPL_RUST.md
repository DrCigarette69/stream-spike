# SPIKE_IMPL=rust (A2.1)

Helper: `scripts/spike_peer_launch.py` (`peer_cmd`).

Until `peer_smoke.py` imports the helper on tip, launch manually:

```bash
cd rust && cargo build -p stream-peer
# terminal A
CONTROL_URL=http://127.0.0.1:8080 SPIKE_LISTEN=127.0.0.1:8080 python3 -u control/main.py
# terminal B
CONTROL_URL=http://127.0.0.1:8080 SPIKE_FAKE_RELAY=127.0.0.1:9100 SPIKE_LISTEN_PROXY=127.0.0.1:1080 python3 -u gateway/main.py
# terminal C
CONTROL_URL=http://127.0.0.1:8080 SPIKE_PEER_ADMIN=127.0.0.1:9200 SPIKE_FAKE_RELAY_DIAL=127.0.0.1:9100 \
  SPIKE_ISP_ACK_VERSION=v1 SPIKE_PEER_ID=peer_demo SPIKE_ENDPOINT_ID=iroh_ep_demo_001 \
  ./rust/target/debug/stream-peer
```

Or after wiring: `SPIKE_IMPL=rust python3 scripts/peer_smoke.py`.

See `docs/PEER_ALPHA2.md`.
