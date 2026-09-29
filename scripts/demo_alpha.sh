#!/usr/bin/env bash
# Stream Alpha-0 demo — local stubs only
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "=== Stream Alpha-0 · SPIKE DoD walkthrough ==="
python3 scripts/run_local_asserts.py all
echo
echo "Alpha-0 proof: SPIKE_DOD_GREEN above = Client match → grace Screens 1–3 + P0 gates."
echo "Next: Alpha-1 (Iroh loopback / Peer tray / mock top-up) — see docs/ALPHA.md"
