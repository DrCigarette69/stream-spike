#!/usr/bin/env python3
"""Run spike DoD asserts locally (no Docker). Starts stack once, runs client-cli commands."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spike_ports  # noqa: E402  SPIKE_PORT_BASE + explicit-env-wins resolution
CLI = ROOT / "client-cli" / "main.py"
procs: list[subprocess.Popen] = []


def http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def cleanup(*_):
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
    time.sleep(0.3)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


def start_stack():
    env = spike_ports.apply(os.environ.copy())  # ports/URLs: setdefault, never overwrite
    db = spike_ports.db_file(ROOT, ".dod.sqlite", env)
    db.unlink(missing_ok=True)
    env.update(
        {
            "SPIKE_DB": str(db),
            "SPIKE_TICKET_SECRET": "dev-only-change-me",
            "SPIKE_ISP_ACK_VERSION": "v1",
            "SPIKE_HOST_TIER": "always_on",
            "SPIKE_PEER_ID": "peer_demo",
            "SPIKE_ENDPOINT_ID": "iroh_ep_demo_001",
            "SPIKE_HEARTBEAT_S": "2",
            "CLI_KEEP_UP": "1",
            "FIXTURES": str(ROOT / "fixtures"),
        }
    )
    # Control always Python; gateway+peer honor SPIKE_IMPL (python|rust).
    from spike_peer_launch import control_cmd, gateway_cmd, peer_cmd

    for argv, cwd in (control_cmd(env), gateway_cmd(env), peer_cmd(env)):
        procs.append(
            subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        )
        time.sleep(0.4)
    control, gateway, peer = spike_ports.urls(env)
    print(f"stack: control={control} gateway={gateway} peer={peer}", flush=True)
    for url in (control + "/health", gateway + "/health", peer + "/health"):
        for _ in range(50):
            if http_ok(url):
                break
            time.sleep(0.1)
        else:
            raise SystemExit(f"failed to start {url}")
    # wait peer online
    import urllib.error
    for _ in range(50):
        try:
            with urllib.request.urlopen(gateway + "/gw/peers", timeout=2) as r:
                peers = json.loads(r.read().decode()).get("peers", [])
                if any(p.get("peer_id") == "peer_demo" for p in peers):
                    return env
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit("peer not on gateway")


def run_cli(env, cmd):
    print(f"\n=== client-cli {cmd} ===", flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(CLI), cmd],
        cwd=str(ROOT / "client-cli"),
        env=env,
    )
    if p.returncode != 0:
        raise SystemExit(f"FAIL {cmd}")
    print(f"PASS {cmd}", flush=True)


def run_peer_alpha1():
    """A1.2 Peer CLI walkthrough (starts its own stack)."""
    print("\n=== peer_alpha1_cli (A1.2) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "peer_alpha1_cli.py")], cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit("FAIL peer_alpha1_cli")
    print("PASS peer_alpha1_cli", flush=True)


def run_iroh_loopback():
    """A1.1 Iroh loopback smoke (opt-in; starts its own stack; no public egress)."""
    print("\n=== iroh_loopback_smoke (A1.1) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "iroh_loopback_smoke.py")], cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit("FAIL iroh_loopback_smoke")
    print("PASS iroh_loopback_smoke", flush=True)


def run_a13_mock_topup():
    """A1.3 mock Stripe top-up smoke (starts its own stack)."""
    print("\n=== a13_mock_topup_smoke (A1.3) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "a13_mock_topup_smoke.py")], cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit("FAIL a13_mock_topup_smoke")
    print("PASS a13_mock_topup_smoke", flush=True)


def run_a30_private_guard():
    """A3.0 private-address guard: Rust stream-proto::guard tests + Python mirror self-test.
    No stack, no network, no iroh dependency."""
    import shutil

    print("\n=== A3.0 private guard (rust stream-proto::guard) ===", flush=True)
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    cargo = shutil.which("cargo")
    if cargo is None:
        if impl == "rust":
            raise SystemExit("FAIL a30: cargo not found but SPIKE_IMPL=rust")
        print("SKIP a30 rust guard tests: cargo not found (SPIKE_IMPL!=rust)", flush=True)
    else:
        p = subprocess.run(
            [cargo, "test", "-p", "stream-proto", "--locked", "guard"],
            cwd=str(ROOT / "rust"),
        )
        if p.returncode != 0:
            raise SystemExit("FAIL a30 rust guard tests")
        print("PASS a30 rust guard tests", flush=True)

    print("\n=== A3.0 private guard (python spike_private_guard self-test) ===", flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "spike_private_guard.py")],
        cwd=str(ROOT),
    )
    if p.returncode != 0:
        raise SystemExit("FAIL a30 python guard self-test")
    print("PASS a30 python guard self-test", flush=True)
    print("\nA3.0_PRIVATE_GUARD_GREEN", flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("a11", "iroh-loopback", "iroh_loopback"):
        run_iroh_loopback()
        return 0
    if mode in ("a12", "alpha1-peer", "peer-alpha1"):
        run_peer_alpha1()
        return 0
    if mode in ("a13", "mock-topup", "mock_topup"):
        run_a13_mock_topup()
        return 0
    if mode in ("a30", "private-guard", "private_guard"):
        run_a30_private_guard()
        return 0
    env = start_stack()
    grace = ["grace-stop", "assert-grace-ledger"]
    gates = [
        "gate-denylist",
        "gate-freeze",
        "gate-peer-ack",
        "gate-peer-egress",
        "gate-peer-kill",
        "gate-auth-ticket",
        "gate-strict-unavailable",
        "gate-aup",
        "gate-cashout",
    ]
    if mode == "grace":
        cmds = grace
    elif mode == "gates":
        cmds = gates
    elif mode in ("alpha1", "all+a12"):
        cmds = grace + gates
    else:
        cmds = grace + gates
    for cmd in cmds:
        run_cli(env, cmd)
    print("\nSPIKE_DOD_GREEN", flush=True)
    if mode in ("alpha1", "all+a12"):
        cleanup()
        procs.clear()
        run_iroh_loopback()
        run_peer_alpha1()
        print("\nALPHA1_ASSERT_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        cleanup()
