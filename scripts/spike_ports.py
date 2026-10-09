"""Shared port/URL resolution for local asserts + smokes (concurrency-safe).

Precedence for every value (never overwrites what the caller already set):

  1. explicit env var (SPIKE_LISTEN, CONTROL_URL, SPIKE_LISTEN_PROXY, ...)
  2. derived from SPIKE_PORT_BASE (optional):
       control = base+0   gateway = base+1   fake relay = base+2
       iroh loopback = base+3   peer admin = base+4
  3. legacy defaults: 8080 / 1080 / 9100 / 9101 / 9200

All derived binds are 127.0.0.1 (loopback only). Unset SPIKE_PORT_BASE and no
overrides == exactly today's ports, so default runs and green markers are unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

HOST = "127.0.0.1"
DEFAULTS = {"control": 8080, "gateway": 1080, "relay": 9100, "iroh": 9101, "peer": 9200}
OFFSETS = {"control": 0, "gateway": 1, "relay": 2, "iroh": 3, "peer": 4}


def port_base(env=None) -> int | None:
    env = os.environ if env is None else env
    raw = str(env.get("SPIKE_PORT_BASE", "")).strip()
    if not raw:
        return None
    try:
        base = int(raw)
    except ValueError:
        raise SystemExit(f"SPIKE_PORT_BASE must be an integer, got {raw!r}")
    if not 1024 <= base <= 65535 - max(OFFSETS.values()):
        raise SystemExit(f"SPIKE_PORT_BASE out of range (1024..{65535 - max(OFFSETS.values())}): {base}")
    return base


def port(service: str, env=None) -> int:
    base = port_base(env)
    return DEFAULTS[service] if base is None else base + OFFSETS[service]


def _client_hostport(hostport: str) -> str:
    """Turn a bind addr into something dialable (0.0.0.0 / [::] -> 127.0.0.1)."""
    host, _, p = hostport.rpartition(":")
    if host in ("0.0.0.0", "", "[::]", "::"):
        host = HOST
    return f"{host}:{p}"


def _url(hostport: str) -> str:
    return "http://" + _client_hostport(hostport)


def apply(env: dict) -> dict:
    """setdefault all port/URL vars on ``env`` (mutates + returns it)."""
    def addr(svc):
        return f"{HOST}:{port(svc, env)}"

    env.setdefault("SPIKE_LISTEN", addr("control"))
    env.setdefault("CONTROL_URL", _url(env["SPIKE_LISTEN"]))
    env.setdefault("SPIKE_LISTEN_PROXY", addr("gateway"))
    env.setdefault("GATEWAY_PROXY", _url(env["SPIKE_LISTEN_PROXY"]))
    # fake relay: listen <-> dial default to each other if only one is given
    if "SPIKE_FAKE_RELAY" not in env and "SPIKE_FAKE_RELAY_DIAL" in env:
        env["SPIKE_FAKE_RELAY"] = env["SPIKE_FAKE_RELAY_DIAL"]
    env.setdefault("SPIKE_FAKE_RELAY", addr("relay"))
    env.setdefault("SPIKE_FAKE_RELAY_DIAL", _client_hostport(env["SPIKE_FAKE_RELAY"]))
    if "SPIKE_IROH_LOOPBACK" not in env and "SPIKE_IROH_LOOPBACK_DIAL" in env:
        env["SPIKE_IROH_LOOPBACK"] = env["SPIKE_IROH_LOOPBACK_DIAL"]
    env.setdefault("SPIKE_IROH_LOOPBACK", addr("iroh"))
    env.setdefault("SPIKE_IROH_LOOPBACK_DIAL", _client_hostport(env["SPIKE_IROH_LOOPBACK"]))
    env.setdefault("SPIKE_PEER_ADMIN", addr("peer"))
    env.setdefault("PEER_ADMIN", _url(env["SPIKE_PEER_ADMIN"]))
    return env


def urls(env=None) -> tuple[str, str, str]:
    """(CONTROL, GATEWAY, PEER) base URLs as the stack will see them."""
    e = apply(dict(os.environ if env is None else env))
    gw = e["GATEWAY_PROXY"]
    if not gw.startswith("http"):
        gw = "http://" + gw
    return e["CONTROL_URL"].rstrip("/"), gw.rstrip("/"), e["PEER_ADMIN"].rstrip("/")


def relay_dial(env=None) -> tuple[str, int]:
    e = apply(dict(os.environ if env is None else env))
    host, _, p = e["SPIKE_FAKE_RELAY_DIAL"].rpartition(":")
    return host.strip("[]"), int(p)


def db_file(root: Path, name: str, env=None) -> Path:
    """Per-run sqlite path: '.dod.sqlite' stays as-is on the default control
    port; otherwise '.dod.<control_port>.sqlite' so concurrent runs from one
    checkout don't unlink each other's DB."""
    e = apply(dict(os.environ if env is None else env))
    cport = int(e["SPIKE_LISTEN"].rpartition(":")[2])
    if cport == DEFAULTS["control"]:
        return root / name
    stem, dot, ext = name.rpartition(".")
    return root / f"{stem}.{cport}.{ext}"


if __name__ == "__main__":
    e = apply(dict(os.environ))
    for k in ("SPIKE_PORT_BASE", "SPIKE_LISTEN", "CONTROL_URL", "SPIKE_LISTEN_PROXY", "GATEWAY_PROXY",
              "SPIKE_FAKE_RELAY", "SPIKE_FAKE_RELAY_DIAL", "SPIKE_IROH_LOOPBACK",
              "SPIKE_IROH_LOOPBACK_DIAL", "SPIKE_PEER_ADMIN", "PEER_ADMIN"):
        print(f"{k}={e.get(k, '')}")
