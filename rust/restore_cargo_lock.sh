#!/usr/bin/env bash
# Ensure Cargo.lock exists for rustc 1.85.
# Prefer: commit a real Cargo.lock. Else: pin transitive deps via pin-msrv-deps.sh.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f Cargo.lock ]]; then
  echo "Cargo.lock already present"
  exit 0
fi
if [[ -f pin-msrv-deps.sh ]]; then
  echo "No Cargo.lock — running pin-msrv-deps.sh (needs crates.io)"
  exec bash pin-msrv-deps.sh
fi
echo "missing Cargo.lock and pin-msrv-deps.sh" >&2
exit 1
