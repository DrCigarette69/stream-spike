#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 - <<'PY'
import base64, zlib, pathlib
root = pathlib.Path(".")
parts = sorted(root.glob("Cargo.lock.z*.b85"))
assert parts, "missing Cargo.lock.z*.b85"
blob = "".join(p.read_text().strip() for p in parts)
data = zlib.decompress(base64.b85decode(blob.encode("ascii")))
(root / "Cargo.lock").write_bytes(data)
print("wrote Cargo.lock", len(data), "bytes from", len(parts), "parts")
PY
