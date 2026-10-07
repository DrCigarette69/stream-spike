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
    env = os.environ.copy()
    db = ROOT / ".dod.sqlite"
    db.unlink(missing_ok=True)
    env.update(
        {
            "SPIKE_DB": str(db),
            "SPIKE_LISTEN": "127.0.0.1:8080",
            "SPIKE_TICKET_SECRET": "dev-only-change-me",
            "CONTROL_URL": "http://127.0.0.1:8080",
            "SPIKE_LISTEN_PROXY": "127.0.0.1:1080",
            "SPIKE_FAKE_RELAY": "127.0.0.1:9100",
            "SPIKE_FAKE_RELAY_DIAL": "127.0.0.1:9100",
            "SPIKE_PEER_ADMIN": "127.0.0.1:9200",
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
    sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    for url in (
        "http://127.0.0.1:8080/health",
        "http://127.0.0.1:1080/health",
        "http://127.0.0.1:9200/health",
    ):
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
            with urllib.request.urlopen("http://127.0.0.1:1080/gw/peers", timeout=2) as r:
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


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("a11", "iroh-loopback", "iroh_loopback"):
        run_iroh_loopback()
        return 0
    if mode in ("a12", "alpha1-peer", "peer-alpha1"):
        run_peer_alpha1()
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
