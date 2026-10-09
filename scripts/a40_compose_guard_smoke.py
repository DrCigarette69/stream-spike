#!/usr/bin/env python3
"""A4.0 compose Control mints iroh_local tickets -> A4.0_COMPOSE_GUARD_GREEN.

Brings up ONLY the compose `control` service under its own project (`a40-<port>`, so the
default stack is untouched) with host networking on 127.0.0.1:<port> (SPIKE_PORT_BASE or
27310) and SPIKE_TRANSPORT=iroh_local, then checks:
  - the image carries /app/spike_private_guard.py byte-identical to scripts/ (single source)
  - SPIKE_IROH_GATEWAY_ADDR=10.73.0.1:9102 -> /v1/sessions 200 with direct_addrs (no guard_unavailable)
  - SPIKE_IROH_GATEWAY_ADDR=8.8.8.8:9102 -> 422 public_addr
Tears the project down (containers + volume). Loopback only.
Usage: python3 scripts/a40_compose_guard_smoke.py   (sudo docker)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PEER_ID_HEX = "5a" * 32
DEV_GW = "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1"


def http(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def fail(msg):
    print(f"FAIL a40: {msg}", flush=True)
    raise SystemExit(1)


def compose(project, env, *args, capture=False):
    kv = [f"{k}={v}" for k, v in env.items()]
    return subprocess.run(["sudo", "env", *kv, "docker", "compose", "-p", project, "-f", "docker-compose.yml", *args],
                          cwd=ROOT, check=True, capture_output=capture, text=True)


def up(project, port, addr):
    env = {"SPIKE_LISTEN": f"127.0.0.1:{port}", "CONTROL_URL": f"http://127.0.0.1:{port}",
           "SPIKE_TRANSPORT": "iroh_local", "SPIKE_IROH_GATEWAY_ADDR": addr}
    compose(project, env, "up", "-d", "--build", "--force-recreate", "--wait", "control", capture=True)
    for _ in range(100):
        try:
            if http("GET", f"http://127.0.0.1:{port}/health")[0] == 200:
                return env
        except Exception:
            pass
        time.sleep(0.1)
    fail(f"compose control on {port} not healthy")


def mint(port):
    base = f"http://127.0.0.1:{port}"
    http("POST", base + "/v1/peers/enroll", {"peer_id": "peer_a40", "endpoint_id": PEER_ID_HEX,
                                            "isp_ack_version": "v1", "host_tier": "always_on"})
    st, m = http("POST", base + "/v1/match", {"account_id": "acct_demo", "geo": {"country": "US", "city": "new_orleans"},
                                              "rematch_mode": "city"})
    if st != 200:
        fail(f"match {st} {m}")
    return http("POST", base + "/v1/sessions", {"quote_id": m["quote_id"]})


def main() -> int:
    port = int(os.environ.get("SPIKE_PORT_BASE") or 27310)
    if subprocess.run(["ss", "-ltnH", f"sport = :{port}"], capture_output=True, text=True).stdout.strip():
        fail(f"port {port} busy; set SPIKE_PORT_BASE to a free port")
    project = f"a40-{port}"
    env = {}
    try:
        env = up(project, port, "10.73.0.1:9102")
        inside = compose(project, env, "exec", "-T", "control", "cat", "/app/spike_private_guard.py", capture=True).stdout
        src = (ROOT / "scripts" / "spike_private_guard.py").read_text()
        if hashlib.sha256(inside.encode()).hexdigest() != hashlib.sha256(src.encode()).hexdigest():
            fail("image guard differs from scripts/spike_private_guard.py")
        print(f"OK compose control image has /app/spike_private_guard.py == scripts/spike_private_guard.py "
              f"(sha256 {hashlib.sha256(src.encode()).hexdigest()[:12]}…)", flush=True)
        st, r = mint(port)
        pl = r.get("ticket", {}).get("payload", {})
        if st != 200 or pl.get("direct_addrs") != ["10.73.0.1:9102"] or pl.get("peer_endpoint_id") != PEER_ID_HEX \
                or pl.get("gateway_endpoint_id") != DEV_GW:
            fail(f"good mint: {st} {r}")
        print(f"OK compose control iroh_local mint: /v1/sessions {st} peer_endpoint_id={PEER_ID_HEX[:12]}… "
              f"gateway_endpoint_id={DEV_GW[:12]}… direct_addrs={pl['direct_addrs']}", flush=True)
        env = up(project, port, "8.8.8.8:9102")
        st, r = mint(port)
        if st != 422 or r.get("error") != "public_addr":
            fail(f"public addr: {st} {r}")
        print(f"OK compose control public direct addr refused: /v1/sessions {st} error={r['error']} "
              f"detail={r.get('detail')!r} (not guard_unavailable)", flush=True)
    finally:
        try:
            compose(project, env, "down", "-v", capture=True)
        except subprocess.CalledProcessError as e:
            print(f"WARN teardown: {e.stderr}", flush=True)
    print("\nA4.0_COMPOSE_GUARD_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
