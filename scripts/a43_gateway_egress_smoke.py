#!/usr/bin/env python3
"""A4.3 Gateway egress check + kill propagation smoke -> A4.3_GATEWAY_EGRESS_GREEN.

On-box, no internet, no default route anywhere. Topology: scripts/a43_netns_up.sh = the A4.2
relay-only topology (a42-gw / a42-relay / a42-peer; Gateway and Peer meet only via the relay at
10.73.0.254) + a43-site, a TLS stand-in "public" site at 198.51.100.10:443 (TEST-NET-2) that only
a42-peer can route to. Control (10.73.0.254:8080) runs with SPIKE_A4_LANE=local.

Allowlist everywhere: echo.example.com:443=198.51.100.10 (pinned stand-in), priv.example.com:443
(resolves to 10.73.0.1 via /etc/netns/a42-peer/hosts -> the Peer's SSRF check must refuse it).

Phase A — Gateway proofs with the a31 test client as the Peer's data path stand-in
(`--egress-allowlist`, wider list incl. other.example.com, i.e. a Peer that *would* allow it):
  default OFF (Control flag off) -> /gw/start 403 egress_off/flag_off at the Gateway, nothing forwarded;
  local_off / no_state shown by two extra Gateways; non-local Control switch -> 403 admin_local_only;
  switch on -> EGRESS_STATE frames; non-allowlisted dest -> 403 egress_not_allowlisted at the Gateway
  (no AUTH_TICKET reaches the stand-in); allowlisted -> TLS GET end-to-end client -> Gateway pipe ->
  DATA over relay -> stand-in Peer -> 198.51.100.10 and back (hello + 300 KB sha256-checked);
  Peer CLOSE {stream_id, reason: site_closed} accepted; flag off -> held stream closed <= 5 s with
  CLOSE egress_off/flag_off; Control frozen (SIGSTOP) -> stale -> closed (flag_stale);
  allowlist version mismatch -> closed (egress_allowlist_mismatch); byte cap -> egress_budget_exceeded.
Phase B — the real stream-peer (--features iroh_pilot,a4_local, SPIKE_PUBLIC_EGRESS=1 + P9 headless):
  switch on -> Gateway OPEN -> Peer dials the pinned stand-in (site sees 198.51.100.1);
  priv.example.com -> refused at the Peer (egress_resolved_non_public); flag off -> stream closed
  <= 5 s and the Peer shows P8 egress_off; Peer with a different allowlist -> egress_allowlist_mismatch.

Usage: python3 scripts/a43_gateway_egress_smoke.py  (passwordless sudo; builds into rust/target/pilot)
Holds /tmp/stream-spike-a42.lock (same a42-* netns names as the a42 smoke).
"""
from __future__ import annotations

import atexit
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a42_self_relay_smoke as a42  # noqa: E402  (shared netns/process helpers)
from a42_self_relay_smoke import (  # noqa: E402
    CONTROL, GW, GW_HTTP, PEER, RELAY, RELAY_URL, ROOT, RUST, SECRET, DEV_ID, Proc, base_env, ns_http, ns_run, ok,
    wait_http,
)

ALLOW = "echo.example.com:443=198.51.100.10,priv.example.com:443"
WIDER = ALLOW + ",other.example.com:443"
CAP = 2_000_000
SITE = ROOT / "scripts" / "a43_standin_site.py"


def fail(msg):
    print(f"FAIL a43: {msg}", flush=True)
    raise SystemExit(1)


a42.fail = fail  # Proc.expect etc. report as a43

TLS_CLIENT = r"""
import hashlib,socket,ssl,sys,time
addr,path,ca,mode=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
h,p=addr.rsplit(':',1)
s=socket.create_connection((h,int(p)),timeout=10)
if mode=='raw':
    print('RAW_OPEN',flush=True); s.settimeout(None)
    while True:
        d=s.recv(4096)
        if not d: print(f'RAW_EOF t={time.time():.3f}',flush=True); break
    sys.exit(0)
ctx=ssl.create_default_context(cafile=ca)
t=ctx.wrap_socket(s,server_hostname='echo.example.com')
ver=t.version()
t.sendall(f'GET {path} HTTP/1.1\r\nHost: echo.example.com\r\n\r\n'.encode())
buf=b''
if mode=='hold':
    while b'hold' not in buf:
        d=t.recv(4096)
        if not d: break
        buf+=d
    print(f'HOLD_OPEN tls={ver}',flush=True); t.settimeout(None)
    try:
        while t.recv(4096): pass
    except (OSError, ssl.SSLError) as e: pass
    print(f'HOLD_EOF t={time.time():.3f}',flush=True); sys.exit(0)
try:
    while True:
        d=t.recv(65536)
        if not d: break
        buf+=d
except (OSError, ssl.SSLError) as e:
    print(f'TLS_READ_ERR {type(e).__name__}',flush=True)
hdr,_,body=buf.partition(b'\r\n\r\n')
want=[l.split(b': ',1)[1].decode() for l in hdr.split(b'\r\n') if l.lower().startswith(b'x-sha256')]
got=hashlib.sha256(body).hexdigest()
print(f"TLS_RESULT tls={ver} status={hdr.split(b' ')[1].decode() if hdr else '-'} bytes={len(body)} sha_ok={bool(want) and want[0]==got} first={body[:40]!r}",flush=True)
"""


def gw_env(extra=None):
    return a42.gw_env({"SPIKE_PUBLIC_EGRESS": "1", "SPIKE_EGRESS_ALLOWLIST": ALLOW,
                       "SPIKE_EGRESS_BYTE_CAP": str(CAP), **(extra or {})})


def flag(on, allowlist=None):
    body = {"public_egress": on}
    if allowlist is not None:
        body["allowlist"] = allowlist.split(",")
    st, r = ns_http("a42-relay", "POST", CONTROL + "/v1/admin/egress", body)  # same host as Control
    if st != 200 or r.get("public_egress") is not on:
        fail(f"flag {on}: {st} {r}")
    return r


def health():
    return ns_http("a42-gw", "GET", GW_HTTP + "/health")[1]


def wait_effective(want, detail=None, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        h = health()
        if h.get("public_egress_effective") is want and (detail is None or h.get("egress_detail") == detail
                                                         or h.get("egress_reason") == detail):
            return h
        time.sleep(0.1)
    fail(f"gateway egress effective != {want} ({detail}): {health()}")


def session(peer_id):
    for _ in range(50):
        _, peers = ns_http("a42-gw", "GET", GW_HTTP + "/gw/peers")
        if any(p.get("peer_id") == peer_id for p in peers.get("peers", [])):
            break
        time.sleep(0.1)
    else:
        fail(f"{peer_id} not enrolled: {peers}")
    st, m = ns_http("a42-relay", "POST", CONTROL + "/v1/match", {"account_id": "acct_demo",
                    "geo": {"country": "US", "city": "new_orleans"}, "rematch_mode": "city"})
    if st != 200:
        fail(f"/v1/match {st} {m}")
    st, s = ns_http("a42-relay", "POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"]})
    if st != 200 or s.get("peer_id") != peer_id:
        fail(f"/v1/sessions {st} {s}")
    return s


def start(peer_id, host, port=443):
    s = session(peer_id)
    st, r = ns_http("a42-gw", "POST", GW_HTTP + "/gw/start", {"peer_id": peer_id, "stream_id": s["stream_id"],
                    "ticket_json": s["ticket_json"], "dest_host": host, "dest_port": port})
    return st, r, s["stream_id"]


def pilot():
    return ns_http("a42-gw", "GET", GW_HTTP + "/gw/pilot")[1]


def closed_entry(sid, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        for c in pilot().get("closed", []):
            if c.get("stream_id") == sid:
                return c
        time.sleep(0.1)
    fail(f"no closed entry for {sid}: {pilot()}")


def tls(addr, path, ca, mode="get"):
    return Proc("a42-gw", [sys.executable, "-u", "-c", TLS_CLIENT, addr, path, ca, mode], base_env(), f"tls{path}")


def ns_pid(ns, needle):
    pids = subprocess.run(["sudo", "-n", "ip", "netns", "pids", ns], capture_output=True, text=True).stdout.split()
    for pid in pids:
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if needle in cmd and "sudo" not in cmd.split()[0] and not cmd.startswith(("env ", "ip ")):
            return int(pid)
    fail(f"no {needle} process in {ns}")


def no_forward(cli, label):
    if any('"AUTH_TICKET"' in l for l in drain(cli)):
        fail(f"{label}: AUTH_TICKET reached the stand-in peer")


def drain(p, wait=0.5):
    end = time.time() + wait
    out = []
    while time.time() < end:
        try:
            l = p.q.get(timeout=0.05)
        except Exception:  # noqa: BLE001
            continue
        if l is None:
            break
        p.lines.append(l)
        out.append(l)
    return out


# --- teardown: never leave stopped (T) processes behind -------------------------------------------
# The stale-flag test SIGSTOPs Control inside a42-relay. Control runs under
# `sudo ip netns exec a42-relay sudo -u $USER env ... python3 control/main.py`; when the python child
# stops, each sudo wrapper above it suspends itself too (sudo job-control relay) and SIGCONT to the
# python alone does not wake them. A stopped root sudo also ignores SIGTERM until continued, so the
# old teardown (Popen.terminate on the outer sudo) left one stopped wrapper + zombie per run.
# Fix: every Proc tree we start is CONT -> TERM -> KILL'd via sudo in a finally/atexit/signal path,
# and teardown asserts no process from this run is still stopped (A43_TEARDOWN stopped=0).
_SEEN: set[int] = set()      # every pid that belonged to one of our Proc trees
_STOPPED: set[int] = set()   # pids we SIGSTOPped (plus their sudo ancestors)
_TORN_DOWN = False


def _ps_table():
    """pid -> (ppid, state) for every live process (from /proc/<pid>/stat)."""
    tab = {}
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            st = (d / "stat").read_text()
        except OSError:
            continue
        rest = st[st.rfind(")") + 2:].split()
        tab[int(d.name)] = (int(rest[1]), rest[0])
    return tab


def _tree(root, tab=None):
    """root + all descendants (pids), parents first."""
    tab = tab if tab is not None else _ps_table()
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _) in tab.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        pid = todo.pop(0)
        if pid in tab and pid not in out:
            out.append(pid)
            todo.extend(kids.get(pid, []))
    return out


def _ancestors(pid, stop_at, tab):
    """pid's ancestor chain up to and including one of stop_at (empty if not under stop_at)."""
    chain = []
    while pid in tab and pid > 1:
        chain.append(pid)
        if pid in stop_at:
            return chain
        pid = tab[pid][0]
    return []


def _sudo_kill(sig, pids):
    if pids:
        subprocess.run(["sudo", "-n", "kill", f"-{sig}", *map(str, pids)], capture_output=True)


def freeze(pid):
    """SIGSTOP pid and remember its whole sudo chain so thaw()/teardown can resume it."""
    tab = _ps_table()
    roots = {p.pid for p in a42.procs}
    chain = _ancestors(pid, roots, tab) or [pid]
    _STOPPED.update(chain)
    _SEEN.update(chain)
    subprocess.run(["sudo", "-n", "kill", "-STOP", str(pid)], check=True)


def thaw(pid):
    """SIGCONT pid and every sudo wrapper above it (they suspend themselves when the child stops)."""
    chain = _ancestors(pid, {p.pid for p in a42.procs}, _ps_table()) or [pid]
    targets = list(dict.fromkeys([*chain, *_STOPPED]))  # child first, outer sudo last
    for _ in range(10):
        _sudo_kill("CONT", targets)
        time.sleep(0.2)
        tab = _ps_table()
        if not any(q in tab and tab[q][1] in ("T", "t") for q in targets):
            return


def teardown(final=True):
    """CONT -> TERM -> KILL every process tree we started (sudo wrappers + netns child), then assert
    nothing from this run is left stopped. Idempotent; runs from finally, atexit and SIGINT/SIGTERM."""
    global _TORN_DOWN
    if _TORN_DOWN:
        return 0
    _TORN_DOWN = True
    tab = _ps_table()
    pids: list[int] = []
    for p in a42.procs:
        for q in _tree(p.pid, tab):
            if q not in pids:
                pids.append(q)
    pids += [q for q in _STOPPED if q in tab and q not in pids]
    _SEEN.update(pids)
    _sudo_kill("CONT", pids)
    _sudo_kill("TERM", list(reversed(pids)))
    end = time.time() + 4
    while time.time() < end:
        live = [q for q in pids if q in _ps_table() and _ps_table()[q][1] != "Z"]
        if not live:
            break
        time.sleep(0.2)
    tab = _ps_table()
    live = [q for q in pids if q in tab and tab[q][1] != "Z"]
    if live:
        _sudo_kill("CONT", live)
        _sudo_kill("KILL", live)
    for p in a42.procs:
        try:
            p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.3)
    tab = _ps_table()
    stopped = sorted(q for q in _SEEN if q in tab and tab[q][1] in ("T", "t"))
    print(f"A43_TEARDOWN stopped={len(stopped)}" + (f" pids={stopped}" if stopped else ""), flush=True)
    return len(stopped)


def _on_signal(signum, _frame):
    raise SystemExit(128 + signum)  # unwinds through main()'s finally -> teardown()


def main() -> int:
    lockf = open("/tmp/stream-spike-a42.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fail("another a42/a43 run holds /tmp/stream-spike-a42.lock")
    cargo = shutil.which("cargo") or fail("cargo not found")
    tgt = RUST / "target" / "pilot"
    for pkg, feats, extra in (("stream-gateway", "iroh_pilot,a4_local", ["--bins", "--examples"]),
                              ("stream-relay", "server,a4_local", []), ("stream-peer", "iroh_pilot,a4_local", [])):
        a42.run([cargo, "build", "--locked", "-q", "--target-dir", tgt, "-p", pkg, "--features", feats, *extra], cwd=RUST)
    subprocess.run(["bash", ROOT / "scripts" / "a43_netns_down.sh"], capture_output=True)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    atexit.register(teardown)
    a42.run(["bash", ROOT / "scripts" / "a43_netns_up.sh"])
    rc = 1
    try:
        rc = body()
    finally:
        left = teardown()
        subprocess.run(["bash", ROOT / "scripts" / "a43_netns_down.sh"], capture_output=True)
        for d in Path(tempfile.gettempdir()).glob("a4[23]_*"):
            if d.is_dir() and d.owner() == a42.USER:
                shutil.rmtree(d, ignore_errors=True)
    if left:
        fail(f"teardown left {left} stopped process(es) from this run")
    return rc


def body() -> int:
    # 1. Topology
    for ns in ("a42-gw", "a42-relay", "a42-peer", "a43-site"):
        d = subprocess.run(["sudo", "-n", "ip", "-n", ns, "route", "show", "default"], capture_output=True, text=True).stdout
        d6 = subprocess.run(["sudo", "-n", "ip", "-n", ns, "-6", "route", "show", "default"], capture_output=True,
                            text=True).stdout
        if d.strip() or d6.strip():
            fail(f"{ns} has a default route")
    for ns, host, port in (("a42-gw", "198.51.100.10", 443), ("a42-relay", "198.51.100.10", 443),
                           ("a42-peer", "10.73.0.1", 9102)):
        r = a42.unreachable(ns, host, port)
        if "reached" in r:
            fail(f"{ns} -> {host}:{port} reachable ({r})")
    ok("no default route in a42-gw/a42-relay/a42-peer/a43-site; Gateway+relay cannot reach 198.51.100.10; "
       "no gw<->peer path")

    tmp = Path(tempfile.mkdtemp(prefix="a43_"))
    os.chmod(tmp, 0o777)
    cert, key = tmp / "site.pem", tmp / "site.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj",
                    "/CN=echo.example.com", "-addext", "subjectAltName=DNS:echo.example.com", "-keyout", key,
                    "-out", cert], check=True, capture_output=True)
    os.chmod(key, 0o644)
    site = Proc("a43-site", [sys.executable, "-u", SITE, cert, key], base_env(), "site")
    site.expect("SITE_READY 198.51.100.10:443")
    relay = Proc("a42-relay", [RELAY], base_env({"STREAM_RELAY_MODE": "dev", "STREAM_RELAY_HTTP_BIND": "10.73.0.254:3340"}),
                 "relay")
    relay.expect("STREAM_RELAY_READY")
    Proc("a42-relay", [sys.executable, "-u", ROOT / "control" / "main.py"], base_env({
        "SPIKE_DB": tmp / "control.sqlite", "SPIKE_LISTEN": "10.73.0.254:8080", "SPIKE_TICKET_SECRET": SECRET,
        "FIXTURES": ROOT / "fixtures", "SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_A4_LANE": "local",
        "SPIKE_RELAY_ALLOW_URL": RELAY_URL, "SPIKE_GATEWAY_ENDPOINT_ID": DEV_ID, "PYTHONPATH": ROOT / "control",
        "SPIKE_EGRESS_ALLOWLIST": ALLOW,
    }), "control")
    wait_http("a42-relay", CONTROL + "/health")

    # 2. Control state: default OFF; switch is local-only
    _, s0 = ns_http("a42-relay", "GET", CONTROL + "/v1/egress/state")
    if s0.get("public_egress") is not False or not s0.get("allowlist_version"):
        fail(f"control default state {s0}")
    st, r = ns_http("a42-gw", "POST", CONTROL + "/v1/admin/egress", {"public_egress": True})
    if st != 403 or r.get("error") != "admin_local_only":
        fail(f"remote switch {st} {r}")
    ok(f"Control GET /v1/egress/state default public_egress=false allowlist_version={s0['allowlist_version'][:12]}…; "
       f"POST from the Gateway ns -> {st} {r['error']}")

    # 3. Gateway refusal without allowlist; local_off / no_state on extra gateways
    no_list = gw_env()
    no_list.pop("SPIKE_EGRESS_ALLOWLIST")
    a42.refuse_start(GW, no_list, "a4_refuse_egress_allowlist_config", "gateway: iroh_pilot without SPIKE_EGRESS_ALLOWLIST refused")
    for extra, want in (({"SPIKE_PUBLIC_EGRESS": "0"}, "local_off"), ({"CONTROL_URL": "http://10.73.0.254:8099"}, "no_state")):
        g2 = Proc("a42-gw", [GW], gw_env({"SPIKE_LISTEN_PROXY": "127.0.0.1:1082", "SPIKE_IROH_LISTEN": "10.73.0.1:9104",
                                          **extra}), f"gw-{want}")
        g2.expect("IROH_PILOT_READY", timeout=40, show=False)
        h2 = {}
        for _ in range(40):
            _, h2 = ns_http("a42-gw", "GET", "http://127.0.0.1:1082/health")
            if h2.get("egress_detail"):
                break
            time.sleep(0.2)
        if h2.get("public_egress_effective") is not False or h2.get("egress_detail") != want:
            fail(f"extra gateway {want}: {h2}")
        ok(f"gateway {list(extra)[0]}={list(extra.values())[0]}: egress_reason={h2['egress_reason']} detail={want}")
        kill_ns_proc("a42-gw", "SPIKE_LISTEN_PROXY=127.0.0.1:1082")

    gw = Proc("a42-gw", [GW], gw_env(), "gateway")
    cfg = gw.expect("A43_EGRESS_CONFIG", timeout=30)
    if f"allowlist_version={s0['allowlist_version']}" not in cfg:
        fail(f"gateway/control allowlist version differ: {cfg} vs {s0['allowlist_version']}")
    gw.expect("IROH_PILOT_READY", timeout=30)
    wait_http("a42-gw", GW_HTTP + "/health")
    h = wait_effective(False, "flag_off")
    ok(f"gateway up: {cfg.strip()} (== Control version); /health public_egress_effective=false "
       f"{h['egress_reason']}/{h['egress_detail']}")

    # 4. Phase A: stand-in peer (test client egress mode)
    cli = Proc("a42-peer", a42.client_argv("--peer-id", "peer_a43tc", "--egress-allowlist", WIDER), base_env(), "standin")
    cli.expect("EGRESS_STANDIN")
    cli.expect('"HELLO_OK"', timeout=40)
    st, r, _ = start("peer_a43tc", "echo.example.com")
    if st != 403 or r.get("error") != "egress_off" or r.get("detail") != "flag_off" or r.get("refused_at") != "gateway":
        fail(f"default off /gw/start {st} {r}")
    no_forward(cli, "default off")
    ok(f"default OFF: /gw/start -> {st} {r['error']}/{r['detail']} refused_at=gateway; nothing forwarded")

    flag(True)
    wait_effective(True)
    cli.expect("RX_EGRESS_STATE on=true", timeout=5)
    ok("Control flag on -> gateway public_egress_effective=true; peer receives EGRESS_STATE on=true")

    st, r, _ = start("peer_a43tc", "other.example.com")
    if st != 403 or r.get("error") != "egress_not_allowlisted" or r.get("refused_at") != "gateway":
        fail(f"non-allowlisted {st} {r}")
    gw.expect("a4_refuse_egress_not_allowlisted:other.example.com:443", timeout=3)
    no_forward(cli, "non-allowlisted")
    ok(f"other.example.com:443 (stand-in peer would allow) -> {st} {r['error']} at the gateway; no AUTH_TICKET sent")
    if health().get("public_egress_effective") is not True:
        fail("per-connection refusal must not stop egress globally")

    st, r, sid = start("peer_a43tc", "echo.example.com")
    if st != 200 or not r.get("pipe_addr"):
        fail(f"allowlisted start {st} {r}")
    cli.expect(f"SITE_CONNECTED {sid} 198.51.100.10:443", timeout=10)
    t = tls(r["pipe_addr"], "/hello", str(cert))
    res = t.expect("TLS_RESULT", timeout=20)
    if "status=200" not in res or "sha_ok=True" not in res or "a43-stand-in hello" not in res:
        fail(f"hello {res}")
    site.expect("SITE_ACCEPT 198.51.100.1:", timeout=5)
    c = closed_entry(sid)
    if c.get("reason") != "site_closed" or c.get("closed_by") != "peer" or not c.get("bytes_down"):
        fail(f"closed entry {c}")
    ok(f"TLS GET through gateway pipe -> relay -> stand-in peer -> 198.51.100.10: {res.split('TLS_RESULT ')[1]}")
    ok(f"peer CLOSE {{stream_id, reason: site_closed}} accepted: a4_egress_log {json.dumps(c)}")

    st, r, sid = start("peer_a43tc", "echo.example.com")
    t = tls(r["pipe_addr"], "/big?n=300000", str(cert))
    res = t.expect("TLS_RESULT", timeout=30)
    if "bytes=300000" not in res or "sha_ok=True" not in res:
        fail(f"big {res}")
    ok(f"300 KB over DATA frames: {res.split('TLS_RESULT ')[1].split(' first=')[0]}")

    def held(label):
        st, r, sid = start("peer_a43tc", "echo.example.com")
        if st != 200:
            fail(f"{label}: start {st} {r}")
        t = tls(r["pipe_addr"], "/hold", str(cert), "hold")
        t.expect("HOLD_OPEN", timeout=20)
        return sid, t

    sid, t = held("flag off")
    t0 = time.time()
    flag(False)
    eof = t.expect("HOLD_EOF", timeout=8)
    dt = float(eof.split("t=")[1]) - t0
    rc = cli.expect(f"RX_CLOSE {sid} reason=egress_off detail=flag_off", timeout=3)
    if dt > 5.0:
        fail(f"flag off took {dt:.2f}s")
    st, r, _ = start("peer_a43tc", "echo.example.com")
    if st != 403 or r.get("error") != "egress_off":
        fail(f"after off {st} {r}")
    ok(f"flag off -> held stream closed in {dt:.2f}s (<= 5 s); peer got {rc.strip()}; new OPEN -> 403 egress_off")

    flag(True)
    wait_effective(True)
    sid, t = held("stale")
    cpid = ns_pid("a42-relay", "control/main.py")
    freeze(cpid)
    t0 = time.time()
    try:
        eof = t.expect("HOLD_EOF", timeout=12)
        rc = cli.expect(f"RX_CLOSE {sid} reason=egress_off detail=flag_stale", timeout=3)
    finally:
        thaw(cpid)
    tab = _ps_table()
    still = [q for q in _STOPPED if q in tab and tab[q][1] in ("T", "t")]
    if still:
        fail(f"Control chain still stopped after SIGCONT: {still}")
    dt = float(eof.split("t=")[1]) - t0
    if dt > 5.0 + 1.5:
        fail(f"stale took {dt:.2f}s")
    ok(f"Control frozen (SIGSTOP) -> no fresh state -> closed after {dt:.2f}s (max age 5 s + 1 s poll); {rc.strip()}")
    wait_effective(True, timeout=10)

    sid, t = held("mismatch")
    t0 = time.time()
    flag(True, ALLOW + ",new.example.com:443")
    eof = t.expect("HOLD_EOF", timeout=8)
    dt = float(eof.split("t=")[1]) - t0
    rc = cli.expect(f"RX_CLOSE {sid} reason=egress_allowlist_mismatch", timeout=3)
    h = wait_effective(False, "egress_allowlist_mismatch")
    if dt > 5.0:
        fail(f"mismatch took {dt:.2f}s")
    ok(f"Control allowlist changed (version mismatch) -> closed in {dt:.2f}s; {rc.strip()}; "
       f"/health reason={h['egress_reason']}")
    flag(True, ALLOW)
    wait_effective(True)

    st, r, sid = start("peer_a43tc", "echo.example.com")
    t = tls(r["pipe_addr"], f"/big?n={CAP + 500_000}", str(cert))
    t.expect(("TLS_RESULT", "TLS_READ_ERR"), timeout=30)
    c = closed_entry(sid)
    if c.get("reason") != "egress_budget_exceeded":
        fail(f"budget close {c}")
    st, r, _ = start("peer_a43tc", "echo.example.com")
    if st != 403 or r.get("error") != "egress_budget_exceeded":
        fail(f"after budget {st} {r}")
    used = pilot()["used"].get("peer_a43tc")
    ok(f"byte cap {CAP}: stream closed egress_budget_exceeded (gateway count {used} B, authoritative); "
       f"next OPEN -> 403 {r['error']}")
    cli.p.terminate()
    for _ in range(100):
        _, peers = ns_http("a42-gw", "GET", GW_HTTP + "/gw/peers")
        if not any(p.get("peer_id") == "peer_a43tc" for p in peers.get("peers", [])):
            break
        time.sleep(0.2)

    # 5. Phase B: real stream-peer
    def real_peer(name, allow, admin, peer_id="peer_a43", wait="iroh_pilot connected"):
        pe = a42.peer_env({"SPIKE_PEER_ID": peer_id, "SPIKE_PUBLIC_EGRESS": "1", "SPIKE_P9_ACK": "1",
                           "SPIKE_EGRESS_ALLOWLIST": allow, "SPIKE_PEER_ADMIN": admin, "HOME": str(tmp),
                           "SPIKE_IROH_KEY_PATH": str(tmp / f"{name}.key")})
        p = Proc("a42-peer", [PEER], pe, name)
        if wait:
            p.expect(wait, timeout=60)
        return p

    peer = real_peer("peer", ALLOW, "127.0.0.1:9200")
    st, r, sid = start("peer_a43", "echo.example.com")
    if st != 200:
        fail(f"real peer start {st} {r}")
    eo = peer.expect(f"peer egress_open {sid} echo.example.com:443 pinned=198.51.100.10:443", timeout=10)
    sa = site.expect("SITE_TCP_ACCEPT 198.51.100.1:", timeout=5)
    ok(f"real peer: switch on -> /gw/start 200 -> {eo.strip()}; stand-in saw {sa.strip()}")
    raw = Proc("a42-gw", [sys.executable, "-u", "-c", TLS_CLIENT, r["pipe_addr"], "/", str(cert), "raw"], base_env(), "raw")
    raw.expect("RAW_OPEN")

    st2, r2, _ = start("peer_a43", "priv.example.com")
    if st2 != 403 or r2.get("error") != "egress_resolved_non_public":
        fail(f"priv at peer {st2} {r2}")
    pr = peer.expect("a4_refuse_egress_resolved_non_public", timeout=5)
    ok(f"priv.example.com (allowlisted, resolves to 10.73.0.1) -> refused at the real peer: {pr.strip()}; /gw/start {st2}")

    t0 = time.time()
    flag(False)
    eof = raw.expect("RAW_EOF", timeout=8)
    dt = float(eof.split("t=")[1]) - t0
    pc = peer.expect(f"peer egress_close {sid}", timeout=6)
    p8 = peer.expect("offline_reason=egress_off", timeout=8)
    dtp = time.time() - t0
    if dt > 5.0 or dtp > 5.0 + 1.0:
        fail(f"real peer close took gw={dt:.2f}s peer={dtp:.2f}s")
    c = closed_entry(sid)
    ok(f"real peer: flag off -> stream closed in {dt:.2f}s at the gateway ({c['reason']}/{c.get('detail')}, "
       f"closed_by={c['closed_by']}); peer: {pc.strip()}; P8 {p8.strip()} by {dtp:.2f}s")
    flag(True)
    wait_effective(True)
    peer.p.terminate()
    kill_ns_proc("a42-peer", "SPIKE_PEER_ADMIN=127.0.0.1:9200")
    time.sleep(1)

    # The first Peer must be fully offline (gateway dropped it, Control told) before a new Peer is matched.
    for _ in range(150):
        _, peers = ns_http("a42-gw", "GET", GW_HTTP + "/gw/peers")
        if not any(p.get("peer_id") == "peer_a43" for p in peers.get("peers", [])):
            break
        time.sleep(0.2)
    else:
        fail(f"peer_a43 still on the gateway after kill: {peers}")
    time.sleep(1)
    # Fresh peer_id: peer_a43 was bound to the first Peer's endpoint (A4.5 peer_id binding).
    peer2 = real_peer("peer2", ALLOW + ",other.example.com:443", "127.0.0.1:9201", peer_id="peer_a43b")
    def mismatch_check(label):
        for _ in range(60):
            st, r, _ = start("peer_a43b", "echo.example.com")
            if r.get("error") != "peer_offline":
                break
            time.sleep(0.5)
        if st != 403 or r.get("error") != "egress_allowlist_mismatch":
            fail(f"peer mismatch{label} {st} {r}")
        return r
    r = mismatch_check("")
    ok(f"real peer with a different allowlist -> refused at the peer: {r['error']}")

    # A4.5: a different endpoint claiming the live peer_a43b is refused; the live session stays.
    peer3 = real_peer("peer3", ALLOW, "127.0.0.1:9202", peer_id="peer_a43b", wait=None)
    ref = gw.expect("peer_id_in_use peer_a43b", timeout=60)
    end = time.time() + 3
    while time.time() < end:  # drain the gateway: no "peer online peer_a43b" for the claimant
        try:
            line = gw.q.get(timeout=0.2)
        except Exception:
            continue
        if line is None:
            break
        gw.lines.append(line)
        if "peer online peer_a43b" in line:
            fail(f"different endpoint went online under live peer_a43b: {line}")
    mismatch_check(" after peer_id_in_use")
    ok(f"different endpoint claiming live peer_a43b -> {ref.strip()}; existing session kept (still egress_allowlist_mismatch)")
    peer3.p.terminate()
    kill_ns_proc("a42-peer", "SPIKE_PEER_ADMIN=127.0.0.1:9202")
    peer2.p.terminate()

    print("\nA4.3_GATEWAY_EGRESS_GREEN", flush=True)
    return 0


def kill_ns_proc(ns, env_needle):
    """Kill processes inside `ns` whose environ contains env_needle (our own extra gateways/peers)."""
    pids = subprocess.run(["sudo", "-n", "ip", "netns", "pids", ns], capture_output=True, text=True).stdout.split()
    for pid in pids:
        r = subprocess.run(["sudo", "-n", "cat", f"/proc/{pid}/environ"], capture_output=True)
        if env_needle.encode() in r.stdout:
            subprocess.run(["sudo", "-n", "kill", "-9", pid], capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
