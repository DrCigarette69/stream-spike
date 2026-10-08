#!/usr/bin/env bash
# Stream Alpha demo — local stubs only (A0 DoD + A1.1 loopback + A1.2 Peer CLI + A1.3 mock top-up)
# SPIKE_IMPL=python (default) or SPIKE_IMPL=rust → peer+gateway Rust bins; Control stays Python.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
IMPL="${SPIKE_IMPL:-python}"
echo "=== Stream Alpha · SPIKE DoD (A0) · SPIKE_IMPL=${IMPL} ==="
python3 scripts/run_local_asserts.py all
echo
echo "=== Stream Alpha · A1.1 Iroh loopback · SPIKE_IMPL=${IMPL} ==="
python3 scripts/iroh_loopback_smoke.py
echo
echo "=== Stream Alpha · Peer A1.2 CLI · SPIKE_IMPL=${IMPL} ==="
python3 scripts/peer_alpha1_cli.py
echo
echo "=== Stream Alpha · A1.3 mock top-up · SPIKE_IMPL=${IMPL} ==="
python3 scripts/run_local_asserts.py a13
echo
echo "Alpha proof: SPIKE_DOD_GREEN + A1.1_IROH_LOOPBACK_GREEN + PEER_ALPHA1_CLI_GREEN + A1.3_MOCK_TOPUP_GREEN."
echo "Rust cut (A2.3): SPIKE_IMPL=rust ./scripts/demo_alpha.sh  # same proofs on Rust peer+gateway"
echo "See docs/ALPHA.md · docs/SPIKE_IMPL_RUST.md · docs/ALPHA2_RUST.md"
