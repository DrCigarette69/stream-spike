#!/usr/bin/env python3
"""A1.3 mock Stripe top-up smoke. Expect: A1.3_MOCK_TOPUP_GREEN. Honors SPIKE_IMPL."""
from __future__ import annotations
import json, os, signal, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_peer_launch import control_cmd, gateway_cmd, peer_cmd
import spike_ports  # SPIKE_PORT_BASE / explicit env wins / legacy defaults
CLI = ROOT / "client-cli" / "main.py"
procs: list[subprocess.Popen] = []

def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False

def cleanup(*_):
    for p in procs:
        try: p.send_signal(signal.SIGTERM)
        except Exception: pass
    time.sleep(0.25)
    for p in procs:
        try: p.kill()
        except Exception: pass

def start_stack():
    env = spike_ports.apply(os.environ.copy())  # SPIKE_PORT_BASE / explicit env wins
    db = spike_ports.db_file(ROOT, ".a13.sqlite", env)
    db.unlink(missing_ok=True)
    ctrl_url, gw_url, peer_url = spike_ports.urls(env)
    env.update({
        "SPIKE_DB": str(db),
        "SPIKE_TICKET_SECRET": env.get("SPIKE_TICKET_SECRET", "dev-only-change-me"),
        "SPIKE_DENYLIST": str(ROOT / "fixtures/denylist.seed.json"),
        "SPIKE_ISP_ACK_VERSION": "v1",
        "SPIKE_HOST_TIER": "always_on", "SPIKE_PEER_ID": "peer_demo",
        "SPIKE_ENDPOINT_ID": "iroh_ep_demo_001", "SPIKE_HEARTBEAT_S": "2",
        "CLI_KEEP_UP": "1", "FIXTURES": str(ROOT / "fixtures"),
    })
    for argv, cwd in (control_cmd(env), gateway_cmd(env), peer_cmd(env)):
        procs.append(subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
        time.sleep(0.35)
    for url in (f"{ctrl_url}/health", f"{gw_url}/health", f"{peer_url}/health"):
        for _ in range(60):
            if http_ok(url): break
            time.sleep(0.1)
        else:
            raise SystemExit(f"failed to start {url}")
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{gw_url}/gw/peers", timeout=2) as r:
                peers = json.loads(r.read().decode()).get("peers", [])
                if any(p.get("peer_id") == "peer_demo" for p in peers):
                    return env
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit("peer not on gateway")

def main():
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    env = start_stack()
    try:
        p = subprocess.run([sys.executable, "-u", str(CLI), "mock-topup"], cwd=str(ROOT / "client-cli"), env=env)
        if p.returncode != 0:
            raise SystemExit(p.returncode or 1)
        print("A1.3_MOCK_TOPUP_GREEN", flush=True)
        return 0
    finally:
        cleanup()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print("FAIL:", e, flush=True)
        cleanup()
        raise SystemExit(1)
