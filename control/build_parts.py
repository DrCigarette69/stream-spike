#!/usr/bin/env python3
"""Rebuild Control's compressed source parts from main_expanded.py (the editable source).

main.py loads `_zlib_*.txt` (preferred) or `_src_part_*.b85` (fallback); both are
base85(zlib(source)) split into chunks. This writes both sets from the same source and
verifies they decode back byte-identical. Usage: python3 control/build_parts.py [--check]
"""
import base64
import sys
import zlib
from pathlib import Path

D = Path(__file__).resolve().parent
SRC = D / "main_expanded.py"
SETS = {"_zlib_{}.txt": 4, "_src_part_{:02d}.b85": 3}


def decode(pattern_glob):
    parts = sorted(D.glob(pattern_glob))
    return zlib.decompress(base64.b85decode("".join(p.read_text().strip() for p in parts).encode("ascii")))


def main():
    src = SRC.read_bytes()
    compile(src, str(SRC), "exec")
    enc = base64.b85encode(zlib.compress(src, 9)).decode("ascii")
    check = "--check" in sys.argv
    for pat, n in SETS.items():
        glob = pat.split("{")[0] + "*" + pat.split("}")[1]
        if check:
            if decode(glob) != src:
                print(f"STALE {glob}")
                return 1
            continue
        for old in D.glob(glob):
            old.unlink()
        size = -(-len(enc) // n)
        for i in range(n):
            (D / pat.format(i)).write_text(enc[i * size:(i + 1) * size] + "\n")
        assert decode(glob) == src, glob
        print(f"wrote {n} x {glob} ({len(enc)} b85 chars)")
    print("parts OK (match main_expanded.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
