#!/usr/bin/env bash
# Stream Alpha demo — local stubs only (A0 DoD + A1.2 Peer CLI)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "=== Stream Alpha · SPIKE DoD (A0) ==="
python3 scripts/run_local_asserts.py all
echo
echo "=== Stream Alpha · Peer A1.2 CLI ==="
python3 scripts/peer_alpha1_cli.py
echo
echo "Alpha proof: SPIKE_DOD_GREEN + PEER_ALPHA1_CLI_GREEN."
echo "Next: A1.1 Iroh loopback (no public egress) — see docs/ALPHA.md"
