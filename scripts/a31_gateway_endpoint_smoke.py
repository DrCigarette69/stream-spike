#!/usr/bin/env python3
"""A3.1 Gateway iroh endpoint smoke -> A3.1_GATEWAY_ENDPOINT_GREEN.

Runs Control + the Rust Gateway (`--features iroh`, SPIKE_TRANSPORT=iroh_local) and a tiny
iroh test client inside a throwaway `sudo ip netns` that has only `lo` (with
10.73.0.1/24 added) and NO default route (iroh 0.95.1's portmapper cannot be disabled,
so the netns is the required egress guard). No relays, no discovery, no public egress.

Shows:
  - netns has no default route
  - Gateway refuses public listen addr / relay URL / discovery; a non-iroh build refuses iroh_local
  - /health: transport=iroh_local + the dev gateway endpoint id
  - test client refuses public direct addr / relay URL (guard) before dialing
  - good ticket (peer_endpoint_id == client's authenticated id) -> AUTH_TICKET -> AUTH_OK -> OPEN
  - mismatched ticket id -> AUTH_REJECT endpoint_mismatch + connection closed
  - HELLO with a foreign endpoint_id -> ERR endpoint_mismatch

Usage (box user with sudo; builds into rust/target/iroh so rust/target/debug stays default):
  python3 scripts/a31_gateway_endpoint_smoke.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "rust"
IROH_TARGET = RUST / "target" / "iroh"
GW_IROH = IROH_TARGET / "debug" / "stream-gateway"
CLIENT = IROH_TARGET / "debug" / "examples" / "a31_test_client"
GW_DEFAULT = RUST / "target" / "debug" / "stream-gateway"
DEV_ID = "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1"
SECRET = "dev-only-change-me"
ALPN = "stream/tunnel/1"
GW_ADDR = "10.73.0.1:9102"
CONTROL = "http://127.0.0.1:8080"
GW_HTTP = "http://127.0.0.1:1080"

procs: list[subprocess.Popen] = []


def fail(msg: str):
    print(f"FAIL a31: {msg}", flush=True)
    raise SystemExit(1)


def ok(msg: str):
    print(f"OK {msg}", flush=True)


# ---------------------------------------------------------------- outer (host side)
def run(cmd, **kw):
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def outer() -> int:
    cargo = shutil.which("cargo") or fail("cargo not found")
    run([cargo, "build", "--locked", "-q", "-p", "stream-gateway"], cwd=RUST)
    run(
        [cargo, "build", "--locked", "-q", "--target-dir", str(IROH_TARGET), "-p", "stream-gateway",
         "--features", "iroh", "--bins", "--examples"],
        cwd=RUST,
    )
    ns = f"a31-{os.getpid()}"
    user = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
    try:
        run(["sudo", "ip", "netns", "add", ns])
        run(["sudo", "ip", "-n", ns, "link", "set", "lo", "up"])
        # No dummy/veth module on this box: put the private A3 address on the netns lo.
        run(["sudo", "ip", "-n", ns, "addr", "add", "10.73.0.1/24", "dev", "lo"])
        p = subprocess.run(
            ["sudo", "ip", "netns", "exec", ns, "sudo", "-u", user, "env",
             f"PATH={os.environ.get('PATH', '')}", f"HOME={os.environ.get('HOME', '')}",
             sys.executable, "-u", str(Path(__file__).resolve()), "--inner"],
        )
        return p.returncode
    finally:
        subprocess.run(["sudo", "ip", "netns", "del", ns])


# ---------------------------------------------------------------- inner (inside netns)
def http(method, url, body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def wait_http(url, tries=100):
    for _ in range(tries):
        try:
            st, _ = http("GET", url, timeout=1)
            if st == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    fail(f"not healthy: {url}")


class Proc:
    def __init__(self, argv, env, name):
        self.name = name
        self.p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs.append(self.p)
        self.q: queue.Queue[str] = queue.Queue()
        self.lines: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(line.rstrip("\n"))
        self.q.put(None)

    def expect(self, needle, timeout=15.0, show=True):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            self.lines.append(line)
            if show and (line.startswith(("RX ", "CLIENT_ID", "DIALED", "CONN_CLOSED", "REFUSED", "STREAM_", "IROH_LOCAL_READY"))):
                print(f"  [{self.name}] {line}", flush=True)
            if needle in line:
                return line
        fail(f"{self.name}: did not see {needle!r}; last lines: {self.lines[-8:]}")


def base_env():
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("SPIKE_IROH_") or k in ("SPIKE_GATEWAY_KEY_PATH",):
            env.pop(k)
    env.update({"RUST_LOG": "info", "NO_COLOR": "1"})
    return env


def gw_env(extra=None):
    env = base_env()
    env.update({
        "CONTROL_URL": CONTROL,
        "SPIKE_LISTEN_PROXY": "127.0.0.1:1080",
        "SPIKE_TRANSPORT": "iroh_local",
        "SPIKE_IROH_LISTEN": GW_ADDR,
        "SPIKE_GATEWAY_ENDPOINT_ID": DEV_ID,
    })
    env.update(extra or {})
    return env


def refuse_start(binary, extra, needle, label):
    p = subprocess.run([str(binary)], env=gw_env(extra), capture_output=True, text=True, timeout=20)
    out = (p.stdout + p.stderr).strip().splitlines()
    hit = [l for l in out if needle in l]
    if p.returncode == 0 or not hit:
        fail(f"{label}: expected refusal containing {needle!r}, rc={p.returncode} out={out[-5:]}")
    ok(f"{label}: rc={p.returncode} :: {hit[0].strip()}")


def sign_ticket(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return json.dumps({"payload": payload, "sig": sig})


def ticket(peer_endpoint_id: str, stream_id: str) -> str:
    return sign_ticket({
        "session_id": f"ses_{stream_id}",
        "stream_id": stream_id,
        "peer_endpoint_id": peer_endpoint_id,
        "gateway_endpoint_id": DEV_ID,
        "direct_addrs": [GW_ADDR],
        "alpn": ALPN,
        "exp": time.time() + 300,
    })


def client(*extra):
    return [str(CLIENT), "--gateway-id", DEV_ID, "--direct-addr", GW_ADDR, *extra]


def inner() -> int:
    routes = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
    routes6 = subprocess.run(["ip", "-6", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
    if routes or routes6:
        fail(f"netns has a default route: {routes!r} {routes6!r}")
    ok("netns: no default route (v4/v6); links=" + " ".join(
        l.split(":")[1].strip() for l in subprocess.run(["ip", "-o", "link"], capture_output=True, text=True).stdout.splitlines()))

    # Gateway start-time refusals (exit before anything binds)
    refuse_start(GW_IROH, {"SPIKE_IROH_LISTEN": "8.8.8.8:9102"}, "a3_refuse_non_private:8.8.8.8", "gateway public listen refused")
    refuse_start(GW_IROH, {"SPIKE_IROH_LISTEN": "0.0.0.0:9102"}, "a3_refuse_non_private:0.0.0.0", "gateway wildcard listen refused")
    refuse_start(GW_IROH, {"SPIKE_LISTEN_PROXY": "not-an-addr"}, "SPIKE_LISTEN_PROXY=", "gateway unparseable admin listen refused")
    refuse_start(GW_IROH, {"SPIKE_IROH_RELAY_URL": "https://use1-1.relay.n0.iroh.iroh.link./"}, "a3_refuse_relay_refused", "gateway relay URL refused")
    refuse_start(GW_IROH, {"SPIKE_IROH_DISCOVERY": "1"}, "a3_refuse_discovery_refused", "gateway discovery refused")
    refuse_start(GW_DEFAULT, {}, "needs stream-gateway built with the iroh feature", "non-iroh build refuses iroh_local")

    # Stack
    tmp = Path(tempfile.mkdtemp(prefix="a31_"))
    cenv = base_env()
    cenv.update({"SPIKE_DB": str(tmp / "control.sqlite"), "SPIKE_LISTEN": "127.0.0.1:8080",
                 "SPIKE_TICKET_SECRET": SECRET, "FIXTURES": str(ROOT / "fixtures")})
    procs.append(subprocess.Popen([sys.executable, "-u", str(ROOT / "control" / "main.py")], cwd=str(ROOT / "control"),
                                  env=cenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait_http(CONTROL + "/health")
    gw = Proc([str(GW_IROH)], gw_env(), "gateway")
    gw.expect("IROH_LOCAL_READY")
    wait_http(GW_HTTP + "/health")
    _, h = http("GET", GW_HTTP + "/health")
    if h.get("transport") != "iroh_local" or h.get("gateway_endpoint_id") != DEV_ID:
        fail(f"/health: {h}")
    ok(f"/health transport={h['transport']} gateway_endpoint_id={h['gateway_endpoint_id']} relay_listen={h.get('relay_listen')}")

    # Client-side guard refusals (no bind, no dial)
    for args, needle, label in (
        (["--gateway-id", DEV_ID, "--direct-addr", "8.8.8.8:9102"], "REFUSED public_addr", "client public direct addr refused"),
        (["--gateway-id", DEV_ID, "--direct-addr", GW_ADDR, "--relay-url", "https://use1-1.relay.n0.iroh.iroh.link./"],
         "REFUSED relay_refused", "client relay URL refused"),
    ):
        p = subprocess.run([str(CLIENT), *args], capture_output=True, text=True, timeout=20)
        line = next((l for l in p.stdout.splitlines() if needle in l), None)
        if not line or "DIALED" in p.stdout:
            fail(f"{label}: rc={p.returncode} out={p.stdout!r}")
        ok(f"{label}: {line}")

    # Good ticket
    c = Proc(client("--peer-id", "peer_a31"), base_env(), "client")
    cid = c.expect("CLIENT_ID").split()[1]
    c.expect("DIALED")
    c.expect('"HELLO_OK"')
    for _ in range(50):
        _, peers = http("GET", GW_HTTP + "/gw/peers")
        mine = [p for p in peers.get("peers", []) if p.get("peer_id") == "peer_a31"]
        if mine:
            break
        time.sleep(0.1)
    if not mine or mine[0].get("endpoint_id") != cid:
        fail(f"/gw/peers: {peers}")
    ok(f"peer enrolled with authenticated endpoint_id={cid}")
    st, r = http("POST", GW_HTTP + "/gw/start", {"peer_id": "peer_a31", "stream_id": "str_a31_good",
                                                  "ticket_json": ticket(cid, "str_a31_good"),
                                                  "dest_host": "echo.local", "dest_port": 443})
    c.expect('"AUTH_TICKET"')
    c.expect('"OPEN"')
    if st != 200 or not r.get("started"):
        fail(f"good ticket /gw/start {st} {r}")
    ok(f"good ticket: /gw/start {st} started=True (client sent AUTH_OK, got OPEN)")
    http("POST", GW_HTTP + "/gw/stop", {"stream_id": "str_a31_good", "peer_id": "peer_a31"})
    c.expect('"CLOSE"')

    # Mismatched ticket id (valid EndpointId, not the client's)
    st, r = http("POST", GW_HTTP + "/gw/start", {"peer_id": "peer_a31", "stream_id": "str_a31_bad",
                                                  "ticket_json": ticket(DEV_ID, "str_a31_bad"),
                                                  "dest_host": "echo.local", "dest_port": 443})
    rej = c.expect('"AUTH_REJECT"')
    if "endpoint_mismatch" not in rej:
        fail(f"AUTH_REJECT without endpoint_mismatch: {rej}")
    closed = c.expect("CONN_CLOSED")
    if st != 403 or r.get("error") != "endpoint_mismatch":
        fail(f"mismatch /gw/start {st} {r}")
    _, peers = http("GET", GW_HTTP + "/gw/peers")
    if any(p.get("peer_id") == "peer_a31" for p in peers.get("peers", [])):
        fail(f"peer still enrolled after mismatch: {peers}")
    ok(f"mismatched ticket: /gw/start {st} error={r['error']}; client got AUTH_REJECT endpoint_mismatch; {closed}")

    # HELLO declaring a foreign endpoint id
    c2 = Proc(client("--peer-id", "peer_a31_liar", "--hello-endpoint-id", DEV_ID), base_env(), "client2")
    err = c2.expect('"ERR"')
    if "endpoint_mismatch" not in err:
        fail(f"HELLO mismatch: {err}")
    ok("HELLO with foreign endpoint_id -> ERR endpoint_mismatch")

    print("\nA3.1_GATEWAY_ENDPOINT_GREEN", flush=True)
    return 0


def cleanup():
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(0.3)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(inner() if "--inner" in sys.argv else outer())
    finally:
        cleanup()
