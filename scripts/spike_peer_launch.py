"""SPIKE_IMPL=python|rust process argv helpers for peer + gateway smokes/asserts.

Control always stays Python (`control/main.py`). Default SPIKE_IMPL (unset/python)
launches peer/ + gateway/ Python; SPIKE_IMPL=rust uses rust/target/debug binaries.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _impl(env: dict | None = None) -> str:
    env = env or os.environ
    return str(env.get("SPIKE_IMPL", os.environ.get("SPIKE_IMPL", "python"))).strip().lower()


def peer_cmd(env: dict | None = None):
    """Return (argv, cwd) for peer. Default python peer/main.py."""
    if _impl(env) == "rust":
        bin_path = ROOT / "rust" / "target" / "debug" / "stream-peer"
        if not bin_path.is_file():
            raise SystemExit(
                f"SPIKE_IMPL=rust but missing {bin_path} "
                "(run: cd rust && cargo build -p stream-peer)"
            )
        return [str(bin_path)], ROOT / "rust"
    return [sys.executable, "-u", str(ROOT / "peer" / "main.py")], ROOT / "peer"


def gateway_cmd(env: dict | None = None):
    """Return (argv, cwd) for gateway. Default python gateway/main.py."""
    if _impl(env) == "rust":
        bin_path = ROOT / "rust" / "target" / "debug" / "stream-gateway"
        if not bin_path.is_file():
            raise SystemExit(
                f"SPIKE_IMPL=rust but missing {bin_path} "
                "(run: cd rust && cargo build -p stream-gateway)"
            )
        return [str(bin_path)], ROOT / "rust"
    return [sys.executable, "-u", str(ROOT / "gateway" / "main.py")], ROOT / "gateway"


def control_cmd(env: dict | None = None):
    """Return (argv, cwd) for control — always Python control/main.py."""
    _ = env  # SPIKE_IMPL does not switch Control
    return [sys.executable, "-u", str(ROOT / "control" / "main.py")], ROOT / "control"
