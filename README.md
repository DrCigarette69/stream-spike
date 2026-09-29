# Stream spike harness (local)

**Status:** client-cli + local assert runner wired · Platform/Peer stubs · no public egress · no live Stripe  
**Plan:** `/workspace/product-docs/stream-spike-plan-v0.md`  
**Default transport:** fake Relay · ALPN `stream/tunnel/1` + `AUTH_TICKET`

## DoD (no Docker)

```bash
./scripts/spike_grace_stop.sh    # or: python3 scripts/run_local_asserts.py grace
./scripts/spike_p0_gates.sh      # or: python3 scripts/run_local_asserts.py gates
python3 scripts/run_local_asserts.py all   # both → prints SPIKE_DOD_GREEN
```

Also: `python3 scripts/platform_smoke.py` · `python3 scripts/peer_smoke.py`

## Layout

| Path | Owner |
|------|-------|
| `control/` `gateway/` | Platform Engineer |
| `peer/` | Peer Engineer |
| `client-cli/` `scripts/run_local_asserts.py` | Stream Architect |
| `fixtures/` | Stream Designer |

## Hard rules

- Localhost only · no public internet · no live Stripe/KYC
