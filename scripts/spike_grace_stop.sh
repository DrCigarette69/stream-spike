#!/usr/bin/env bash
# DoD grace path — local python harness (no Docker required on this box).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 scripts/run_local_asserts.py grace
