"""Control plane — loads from compressed parts (MCP-safe)."""
import base64, zlib
from pathlib import Path
_DIR = Path(__file__).resolve().parent
_parts = sorted(_DIR.glob("_src_part_*.b85"))
_SRC = zlib.decompress(base64.b85decode("".join(p.read_text().strip() for p in _parts).encode("ascii")))
_g = {"__name__": __name__, "__file__": str(Path(__file__).resolve())}
exec(compile(_SRC, _g["__file__"], "exec"), _g)
globals().update({k: v for k, v in _g.items() if k == "main" or not k.startswith("_")})
if __name__ == "__main__":
    main()
