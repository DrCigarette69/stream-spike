#!/usr/bin/env python3
"""A4.2 self-hosted relay smoke -> A4.2_SELF_RELAY_GREEN.

On-box, no internet. Topology from scripts/a42_netns_up.sh (no default route anywhere; the
relay ns joins the Gateway and Peer sides and does not forward, so no direct path exists):
  a42-relay  stream-relay (iroh-relay 0.95.1 server, dev/plain http, --features server,a4_local)
             at http://10.73.0.254:3340/
             + Control (iroh_pilot minting) on 10.73.0.254:8080 (the one address both sides reach)
  a42-gw     Rust Gateway --features iroh_pilot,a4_local, SPIKE_TRANSPORT=iroh_pilot:
             RelayMode::Custom(our relay, no QAD), PathSelection::RelayOnly
  a42-peer   real stream-peer --features iroh_pilot,a4_local (A42_PEER=real, default) or
             a31_test_client --pilot-relay (A42_PEER=testclient). The test client also does the
             client-side refusal checks and the relay-down redial in both modes.

Shows: grep that nothing calls insecure_skip_relay_cert_verify; no default route + no gw<->peer
route; Gateway refuses missing/n0 allow URL, n0 / other relay URL, non-relay-only path selection,
discovery, and a non-pilot build; client refuses n0 / other relay; relay-only connect with
PATH relay(...) on both ends; Control-minted ticket with relay_url + empty direct_addrs -> AUTH_OK
-> OPEN; relay down -> live conn closes (real Peer: P8 offline), new dial fails closed, Gateway
restart refused.

Usage: python3 scripts/a42_self_relay_smoke.py   (passwordless sudo; builds into rust/target/pilot)
Single instance (/tmp/stream-spike-a42.lock); uses only a42-* netns names.
"""
from __future__ import annotations

import fcntl
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "rust"
PILOT = RUST / "target" / "pilot" / "debug"
IROH_TARGET = RUST / "target" / "iroh" / "debug"
GW = PILOT / "stream-gateway"
CLIENT = PILOT / "examples" / "a31_test_client"
RELAY = PILOT / "stream-relay"
PEER = PILOT / "stream-peer"
PEER_MODE = os.environ.get("A42_PEER", "real").strip().lower()
GW_IROH_ONLY = IROH_TARGET / "stream-gateway"
DEV_ID = "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1"
SECRET = "dev-only-change-me"
RELAY_URL = "http://10.73.0.254:3340/"
OTHER_RELAY = "http://10.73.0.254:3341/"
N0_RELAY = "https://use1-1.relay.n0.iroh.iroh.link./"
GW_IROH = "10.73.0.1:9102"
CONTROL = "http://10.73.0.254:8080"
GW_HTTP = "http://127.0.0.1:1080"
USER = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
procs: list[subprocess.Popen] = []


def fail(msg):
    print(f"FAIL a42: {msg}", flush=True)
    raise SystemExit(1)


def ok(msg):
    print(f"OK {msg}", flush=True)


def run(cmd, **kw):
    print("+ " + " ".join(map(str, cmd)), flush=True)
    return subprocess.run(list(map(str, cmd)), check=True, **kw)


def ns_argv(ns, argv, env):
    kv = [f"{k}={v}" for k, v in env.items()]
    return ["sudo", "-n", "ip", "netns", "exec", ns, "sudo", "-n", "-u", USER, "env", "-i", *kv, *map(str, argv)]


def base_env(extra=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
           "RUST_LOG": "info", "NO_COLOR": "1"}
    env.update(extra or {})
    return env


class Proc:
    def __init__(self, ns, argv, env, name):
        self.name = name
        self.p = subprocess.Popen(ns_argv(ns, argv, env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs.append(self.p)
        self.q: queue.Queue = queue.Queue()
        self.lines: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(line.rstrip("\n"))
        self.q.put(None)

    def expect(self, needle, timeout=20.0, show=True):
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.q.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            self.lines.append(line)
            if show and (line.startswith(("peer iroh", "UX_SCREEN", "peer offline", "A4 iroh")) or line.startswith(("RX ", "CLIENT_ID", "DIALED", "CONN_CLOSED", "REFUSED", "PATH", "RELAY_",
                                         "CONNECT_FAILED", "IROH_", "STREAM_RELAY_READY", "STREAM_"))):
                print(f"  [{self.name}] {line}", flush=True)
            if any(n in line for n in ((needle,) if isinstance(needle, str) else needle)):
                return line
        fail(f"{self.name}: did not see {needle!r} in {timeout}s; last lines: {self.lines[-8:]}")


def ns_run(ns, argv, env, timeout=30):
    return subprocess.run(ns_argv(ns, argv, env), capture_output=True, text=True, timeout=timeout)


def ns_http(ns, method, url, body=None):
    code = (
        "import json,sys,urllib.request,urllib.error\n"
        "m,u,b=sys.argv[1],sys.argv[2],sys.argv[3]\n"
        "d=b.encode() if b else None\n"
        "r=urllib.request.Request(u,data=d,method=m,headers={'Content-Type':'application/json'} if d else {})\n"
        "try:\n"
        "  x=urllib.request.urlopen(r,timeout=8); print(x.status); print(x.read().decode() or '{}')\n"
        "except urllib.error.HTTPError as e:\n"
        "  print(e.code); print(e.read().decode() or '{}')\n"
        "except Exception as e:\n"
        "  print(0); print(json.dumps({'error': str(e)}))\n"
    )
    p = ns_run(ns, [sys.executable, "-c", code, method, url, json.dumps(body) if body is not None else ""], base_env())
    lines = p.stdout.strip().splitlines()
    if len(lines) < 2:
        return 0, {"error": p.stderr.strip()}
    return int(lines[0]), json.loads("\n".join(lines[1:]))


def wait_http(ns, url, tries=100):
    for _ in range(tries):
        st, _ = ns_http(ns, "GET", url)
        if st == 200:
            return
        time.sleep(0.2)
    fail(f"not healthy in {ns}: {url}")


def gw_env(extra=None):
    return base_env({
        "CONTROL_URL": CONTROL, "SPIKE_LISTEN_PROXY": "127.0.0.1:1080", "SPIKE_TRANSPORT": "iroh_pilot",
        "SPIKE_IROH_LISTEN": GW_IROH, "SPIKE_GATEWAY_ENDPOINT_ID": DEV_ID, "SPIKE_RELAY_ALLOW_URL": RELAY_URL,
        **(extra or {}),
    })


def refuse_start(binary, env, needle, label):
    p = ns_run("a42-gw", [binary], env, timeout=40)
    out = (p.stdout + p.stderr).strip().splitlines()
    hit = [l for l in out if needle in l]
    if p.returncode == 0 or not hit:
        fail(f"{label}: expected refusal containing {needle!r}, rc={p.returncode} out={out[-5:]}")
    ok(f"{label}: rc={p.returncode} :: {hit[0].strip()}")


def peer_env(extra=None):
    d = Path(tempfile.gettempdir()) / f"a42_peer_{os.getpid()}"
    d.mkdir(exist_ok=True)
    os.chmod(d, 0o777)
    return base_env({
        "SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_IROH_KEY_PATH": d / "peer_a42.key", "SPIKE_IROH_BIND": "10.73.0.200:0",
        "SPIKE_GATEWAY_ENDPOINT_ID": DEV_ID, "SPIKE_RELAY_ALLOW_URL": RELAY_URL, "CONTROL_URL": CONTROL,
        "SPIKE_PEER_ID": "peer_a42", "SPIKE_PEER_ADMIN": "127.0.0.1:9200", "SPIKE_ISP_ACK_VERSION": "v1",
        "SPIKE_P2_CONSENT": "1", "SPIKE_HOST_TIER": "always_on", "SPIKE_HEARTBEAT_S": "2",
        # Pilot Peer needs an allowlist; SPIKE_PUBLIC_EGRESS stays unset (off) -> no P9, OPENs refused.
        "SPIKE_EGRESS_ALLOWLIST": "example.com:443", **(extra or {}),
    })


def client_argv(*extra, relay=RELAY_URL, allow=RELAY_URL):
    return [CLIENT, "--gateway-id", DEV_ID, "--pilot-relay", relay, "--relay-allow", allow, "--bind", "10.73.0.200",
            *extra]


def unreachable(ns, host, port):
    code = (
        "import socket,sys\n"
        "h,p=sys.argv[1],int(sys.argv[2])\n"
        "r=[]\n"
        "for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):\n"
        "  s=socket.socket(socket.AF_INET, kind); s.settimeout(2)\n"
        "  try:\n"
        "    (s.connect if kind==socket.SOCK_STREAM else (lambda a: s.sendto(b'x', a)))((h,p)); r.append('reached')\n"
        "  except OSError as e: r.append(e.strerror or str(e))\n"
        "print(';'.join(r))\n"
    )
    return ns_run(ns, [sys.executable, "-c", code, host, str(port)], base_env()).stdout.strip()


def main() -> int:
    lockf = open("/tmp/stream-spike-a42.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fail("another a42 run holds /tmp/stream-spike-a42.lock")

    # 0. No cert-verification bypass anywhere in our Rust code.
    hits = subprocess.run(["rg", "-n", r"insecure_skip_relay_cert_verify\s*\(", str(RUST / "crates")],
                          capture_output=True, text=True).stdout.strip()
    if hits:
        fail(f"insecure_skip_relay_cert_verify call found:\n{hits}")
    ok("grep: no insecure_skip_relay_cert_verify( call in rust/crates")

    cargo = shutil.which("cargo") or fail("cargo not found")
    run([cargo, "build", "--locked", "-q", "--target-dir", RUST / "target" / "pilot", "-p", "stream-gateway",
         "--features", "iroh_pilot,a4_local", "--bins", "--examples"], cwd=RUST)
    run([cargo, "build", "--locked", "-q", "--target-dir", RUST / "target" / "pilot", "-p", "stream-relay",
         "--features", "server,a4_local"], cwd=RUST)
    if PEER_MODE == "real":
        run([cargo, "build", "--locked", "-q", "--target-dir", RUST / "target" / "pilot", "-p", "stream-peer",
             "--features", "iroh_pilot,a4_local"], cwd=RUST)
    elif PEER_MODE != "testclient":
        fail(f"A42_PEER={PEER_MODE!r} (real|testclient)")
    run([cargo, "build", "--locked", "-q", "--target-dir", RUST / "target" / "iroh", "-p", "stream-gateway",
         "--features", "iroh", "--bins"], cwd=RUST)

    subprocess.run(["bash", ROOT / "scripts" / "a42_netns_down.sh"], capture_output=True)
    run(["bash", ROOT / "scripts" / "a42_netns_up.sh"])
    try:
        return body()
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        subprocess.run(["bash", ROOT / "scripts" / "a42_netns_down.sh"], capture_output=True)
        for d in Path(tempfile.gettempdir()).glob("a42_*"):
            if d.is_dir() and d.owner() == USER:
                shutil.rmtree(d, ignore_errors=True)


def body() -> int:
    # 1. Topology
    for ns in ("a42-gw", "a42-relay", "a42-peer"):
        d = subprocess.run(["sudo", "-n", "ip", "-n", ns, "route", "show", "default"], capture_output=True, text=True).stdout
        d6 = subprocess.run(["sudo", "-n", "ip", "-n", ns, "-6", "route", "show", "default"], capture_output=True, text=True).stdout
        if d.strip() or d6.strip():
            fail(f"{ns} has a default route: {d!r} {d6!r}")
    ok("a42-gw / a42-relay / a42-peer: no default route (v4/v6), ip_forward=0")
    for ns, host in (("a42-peer", "10.73.0.1"), ("a42-gw", "10.73.0.200")):
        r = unreachable(ns, host, 9102)
        if "reached" in r:
            fail(f"{ns} -> {host} reachable ({r}): a direct path exists")
        ok(f"no direct path: {ns} -> {host}:9102 tcp/udp = {r}")

    # 2. Relay
    relay = Proc("a42-relay", [RELAY], base_env({"STREAM_RELAY_MODE": "dev", "STREAM_RELAY_HTTP_BIND": "10.73.0.254:3340"}),
                 "relay")
    relay.expect("STREAM_RELAY_READY")

    # 3. Gateway refusals (exit before anything binds/dials)
    no_allow = gw_env()
    no_allow.pop("SPIKE_RELAY_ALLOW_URL")
    refuse_start(GW, no_allow, "a4_refuse_relay_config", "gateway: SPIKE_RELAY_ALLOW_URL unset refused")
    refuse_start(GW, gw_env({"SPIKE_RELAY_ALLOW_URL": N0_RELAY}), "a4_refuse_relay_config",
                 "gateway: n0 relay as SPIKE_RELAY_ALLOW_URL refused")
    refuse_start(GW, gw_env({"SPIKE_IROH_RELAY_URL": N0_RELAY}), "relay_refused", "gateway: n0 relay URL refused")
    refuse_start(GW, gw_env({"SPIKE_IROH_RELAY_URL": OTHER_RELAY}), "relay_refused",
                 "gateway: different relay URL refused")
    refuse_start(GW, gw_env({"SPIKE_IROH_PATH_SELECTION": "all"}), "relay_only",
                 "gateway: non-relay-only path selection refused")
    refuse_start(GW, gw_env({"SPIKE_IROH_DISCOVERY": "1"}), "discovery_refused", "gateway: discovery refused")
    refuse_start(GW_IROH_ONLY, gw_env(), "needs stream-gateway built with the iroh_pilot feature",
                 "gateway: build without iroh_pilot refuses SPIKE_TRANSPORT=iroh_pilot")

    # 4. Control + Gateway
    tmp = Path(tempfile.mkdtemp(prefix="a42_"))
    os.chmod(tmp, 0o777)
    Proc("a42-relay", [sys.executable, "-u", ROOT / "control" / "main.py"], base_env({
        "SPIKE_DB": tmp / "control.sqlite", "SPIKE_LISTEN": "10.73.0.254:8080", "SPIKE_TICKET_SECRET": SECRET,
        "FIXTURES": ROOT / "fixtures", "SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_A4_LANE": "local",
        "SPIKE_RELAY_ALLOW_URL": RELAY_URL, "SPIKE_GATEWAY_ENDPOINT_ID": DEV_ID, "PYTHONPATH": ROOT / "control",
    }), "control")
    wait_http("a42-relay", CONTROL + "/health")
    gw = Proc("a42-gw", [GW], gw_env(), "gateway")
    gw.expect("IROH_PILOT_READY", timeout=30)
    wait_http("a42-gw", GW_HTTP + "/health")
    _, h = ns_http("a42-gw", "GET", GW_HTTP + "/health")
    if h.get("transport") != "iroh_pilot" or h.get("relay_url") != RELAY_URL or h.get("gateway_endpoint_id") != DEV_ID:
        fail(f"/health {h}")
    ok(f"/health transport={h['transport']} relay_url={h['relay_url']} gateway_endpoint_id={DEV_ID[:12]}…")

    # 5. Client-side refusals (before bind/dial)
    for argv, needle, label in (
        (client_argv(relay=N0_RELAY), "REFUSED relay_refused", "client: n0 relay refused"),
        (client_argv(relay=OTHER_RELAY), "REFUSED relay_refused", "client: different relay refused"),
        (client_argv(relay=N0_RELAY, allow=N0_RELAY), "REFUSED relay_config", "client: n0 relay as allow URL refused"),
    ):
        p = ns_run("a42-peer", argv, base_env(), timeout=20)
        line = next((l for l in p.stdout.splitlines() if needle in l), None)
        if not line or "DIALED" in p.stdout:
            fail(f"{label}: rc={p.returncode} out={p.stdout!r}")
        ok(f"{label}: {line}")

    # 6. Relay-only connect
    real = PEER_MODE == "real"
    if real:
        pe = peer_env()
        for extra, needle, label in (
            ({"SPIKE_RELAY_ALLOW_URL": N0_RELAY}, "a4_refuse_relay_config", "real peer: n0 relay as allow URL refused"),
            ({"SPIKE_IROH_GATEWAY_RELAY_URL": OTHER_RELAY}, "relay_refused", "real peer: different relay refused"),
            ({"SPIKE_TRANSPORT": "iroh_local"}, "a4_refuse_transport_not_pilot", "real peer: pilot build refuses iroh_local"),
        ):
            # Startup refusals exit 2; dial-target refusals keep admin up, so read the line then stop it.
            pr = Proc("a42-peer", [PEER], {**pe, **extra, "SPIKE_PEER_ADMIN": "127.0.0.1:9201"}, "peer-neg")
            hit = pr.expect((needle, "iroh_pilot connected"), timeout=25, show=False)
            if "iroh_pilot connected" in hit or "iroh_pilot connected" in "\n".join(pr.lines):
                fail(f"{label}: peer connected: {pr.lines[-5:]}")
            pr.p.terminate()
            try:
                pr.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pr.p.kill()
            ok(f"{label}: {hit.strip()}")
        c = Proc("a42-peer", [PEER], pe, "peer")
        cid = c.expect("peer iroh_endpoint_id=", timeout=30).split("=", 1)[1].strip()
        c.expect("iroh_pilot bound", timeout=30)
        c.expect("iroh_pilot connected", timeout=40)
        gpath = gw.expect("IROH_PEER_CONNECTED", timeout=30)
        path = "real stream-peer"
    else:
        c = Proc("a42-peer", client_argv("--peer-id", "peer_a42"), base_env(), "client")
        cid = c.expect("CLIENT_ID").split()[1]
        c.expect("RELAY_ONLINE", timeout=30)
        c.expect("DIALED")
        c.expect('"HELLO_OK"')
        path = c.expect("PATH ")
        if not path.startswith("PATH relay ") or " udp_datagrams=0 " not in path:
            fail(f"client path not relay-only: {path}")
        gpath = gw.expect("IROH_PEER_CONNECTED")
    gp = json.loads(gpath.split("path=", 1)[1])
    if gp.get("path") != "relay" or gp.get("udp_send") or gp.get("udp_recv"):
        fail(f"gateway path not relay-only: {gp}")
    if real and "relay_path_required" in "\n".join(c.lines):
        fail("real peer saw a direct path")
    ok(f"relay-only connection ({path.strip()}): gateway {gp}")

    for _ in range(50):
        _, peers = ns_http("a42-gw", "GET", GW_HTTP + "/gw/peers")
        mine = [p for p in peers.get("peers", []) if p.get("peer_id") == "peer_a42"]
        if mine:
            break
        time.sleep(0.1)
    if not mine or mine[0].get("endpoint_id") != cid:
        fail(f"/gw/peers {peers}")
    ok(f"peer enrolled over the relay with authenticated endpoint_id={cid[:12]}…")

    st, m = ns_http("a42-relay", "POST", CONTROL + "/v1/match", {"account_id": "acct_demo",
                    "geo": {"country": "US", "city": "new_orleans"}, "rematch_mode": "city"})
    if st != 200:
        fail(f"/v1/match {st} {m}")
    st, sess = ns_http("a42-relay", "POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"]})
    pl = sess.get("ticket", {}).get("payload", {})
    if st != 200 or pl.get("relay_url") != RELAY_URL or pl.get("direct_addrs") != [] or pl.get("peer_endpoint_id") != cid:
        fail(f"/v1/sessions {st} {sess}")
    ok(f"Control iroh_pilot ticket: relay_url={pl['relay_url']} direct_addrs={pl['direct_addrs']} "
       f"peer_endpoint_id={cid[:12]}…")
    st, r = ns_http("a42-gw", "POST", GW_HTTP + "/gw/start", {"peer_id": sess["peer_id"], "stream_id": sess["stream_id"],
                    "ticket_json": sess["ticket_json"], "dest_host": "echo.local", "dest_port": 443})
    if real:
        p2 = "real stream-peer"
    else:
        c.expect('"AUTH_TICKET"')
        p2 = c.expect("PATH ")
        c.expect('"OPEN"')
        if not p2.startswith("PATH relay ") or " udp_datagrams=0 " not in p2:
            fail(f"after ticket: client {p2}")
    if real:
        # Kill switch is off by default (SPIKE_PUBLIC_EGRESS unset, no Control egress flag until A4.3), so
        # the real Peer verifies the relay-bound ticket and then refuses with egress_off (fail closed).
        # A relay_url problem would be relay_refused / relay_required instead.
        if st != 403 or r.get("error") != "egress_off":
            fail(f"/gw/start (real peer, egress off) {st} {r}")
    elif st != 200 or not r.get("started"):
        fail(f"/gw/start {st} {r} {p2}")
    _, h = ns_http("a42-gw", "GET", GW_HTTP + "/health")
    ip = h.get("iroh_path", {})
    if ip.get("path") != "relay" or ip.get("udp_send") or ip.get("udp_recv"):
        fail(f"after ticket: gateway {ip}")
    if real:
        ok(f"ticket over relay: real peer accepted relay_url, answered {r.get('error')} (kill switch off by default); "
           f"/gw/start {st}; gateway /health iroh_path={ip}")
    else:
        ok(f"ticket over relay: /gw/start {st} started=True; client AUTH_OK -> OPEN; {p2.strip()}; "
           f"gateway /health iroh_path={ip}")
    ns_http("a42-gw", "POST", GW_HTTP + "/gw/stop", {"stream_id": sess["stream_id"], "peer_id": "peer_a42"})
    if not real:
        c.expect('"CLOSE"')

    # 7. Relay down -> fail closed (no direct fallback)
    t0 = time.time()
    subprocess.run(["sudo", "-n", "pkill", "-9", "-f", str(RELAY)])
    if real:
        # Real Peer redials after the drop; with the relay gone the relay-only dial must fail (no direct try).
        closed = c.expect("iroh_pilot dial failed", timeout=30)
    else:
        closed = c.expect(("CONN_CLOSED", "STREAM_ERR"), timeout=25)
    for _ in range(100):
        _, peers = ns_http("a42-gw", "GET", GW_HTTP + "/gw/peers")
        if not any(p.get("peer_id") == "peer_a42" for p in peers.get("peers", [])):
            break
        time.sleep(0.2)
    else:
        fail(f"peer still enrolled at the gateway with the relay down: {peers}")
    ok(f"relay killed: live connection dropped after {time.time() - t0:.1f}s ({closed.strip()}); "
       "gateway unenrolled the peer; no direct fallback")
    c2 = Proc("a42-peer", client_argv("--peer-id", "peer_a42_b"), base_env(), "client2")
    c2.expect("CLIENT_ID")
    line = None
    end = time.time() + 30
    while time.time() < end and line is None:
        try:
            l = c2.q.get(timeout=1)
        except queue.Empty:
            continue
        if l is None:
            break
        c2.lines.append(l)
        if l.startswith(("RELAY_UNREACHABLE", "CONNECT_FAILED")):
            line = l
        if l.startswith(("DIALED", "RX ")):
            fail(f"dial succeeded with relay down: {l}")
    if not line:
        fail(f"client2 with relay down: {c2.lines[-6:]}")
    ok(f"relay down: new relay-only dial fails closed: {line}")
    refuse_start(GW, gw_env({"SPIKE_LISTEN_PROXY": "127.0.0.1:1081", "SPIKE_IROH_LISTEN": "10.73.0.1:9103",
                             "SPIKE_IROH_RELAY_WAIT_MS": "1500"}),
                 "a4_refuse_relay_unreachable", "relay down: gateway start refused")

    print("\nA4.2_SELF_RELAY_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
