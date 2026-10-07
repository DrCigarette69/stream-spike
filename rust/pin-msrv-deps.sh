#!/usr/bin/env bash
# Re-pin transitive deps for rustc 1.85 (run from rust/)
set -euo pipefail
cargo update encoding_rs --precise 0.8.35
cargo update idna_adapter --precise 1.2.0
cargo update icu_normalizer --precise 1.5.0
cargo update icu_properties --precise 1.5.1
cargo update icu_collections --precise 1.5.0
cargo update icu_provider --precise 1.5.0
cargo update icu_locid --precise 1.5.0
echo "Pinned. Commit Cargo.lock."
