#!/usr/bin/env bash
# Re-pin transitive deps for rustc 1.85 (works from any cwd)
set -euo pipefail
cd "$(dirname "$0")"
cargo update encoding_rs --precise 0.8.35
cargo update idna_adapter --precise 1.2.0
cargo update icu_normalizer --precise 1.5.0
cargo update icu_properties --precise 1.5.1
cargo update icu_collections --precise 1.5.0
cargo update icu_provider --precise 1.5.0
cargo update icu_locid --precise 1.5.0
# Alpha-3: iroh 0.95.1 (optional features) uses ed25519-dalek 3.0.0-pre.1, which
# breaks on the final RustCrypto releases. Pin the rc versions it was built against.
cargo update ed25519 --precise 3.0.0-rc.2
cargo update pkcs8 --precise 0.11.0-rc.8
cargo update spki --precise 0.8.0-rc.4
cargo update der --precise 0.8.0-rc.10
echo "Pinned. Commit Cargo.lock."
