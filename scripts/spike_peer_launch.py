"""SPIKE_IMPL=python|rust peer process argv helper for smokes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def peer_cmd(env: dict | None = None):
    """Return (argv, cwd) for peer. Default python peer/main.py."""
    env = env or os.environ
    impl = str(env.get("SPIKE_IMPL", os.environ.get("SPIKE_IMPL", "python"))).strip().lower()
    if impl == "rust":
        bin_path = ROOT / "rust" / "target" / "debug" / "stream-peer"
        if not bin_path.is_file():
            raise SystemExit(
                f"SPIKE_IMPL=rust but missing {bin_path} "
                "(run: cd rust && cargo build -p stream-peer)"
            )
        return [str(bin_path)], ROOT / "rust"
    return [sys.executable, "-u", str(ROOT / "peer" / "main.py")], ROOT / "peer"
