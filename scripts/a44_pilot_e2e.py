#!/usr/bin/env python3
"""A4.4 Peer pilot egress full e2e (run by a44_peer_egress_smoke.py; standalone OK).

On-box, no internet, no default route anywhere: scripts/a43_netns_up.sh = the A4.2 relay-only
topology (a42-gw / a42-relay 10.73.0.254 / a42-peer .200; Gateway and Peer meet only via our
relay) + a43-site, the TLS stand-in 198.51.100.10:443 only the Peer ns routes to. Real Control
(SPIKE_A4_LANE=local), real relay, real Gateway (iroh_pilot), real stream-peer
(--features iroh_pilot,a4_local, SPIKE_PUBLIC_EGRESS=1, P9 headless). Reuses Platform's
a42/a43 smoke helpers (same /tmp/stream-spike-a42.lock, waits for it).
  1. switch on; TLS 1.3 GET /hello (38 B) and /big 300 KB through Gateway pipe -> relay -> Peer
     DATA pump -> pinned 198.51.100.10 and back, sha256 match; Peer CLOSE site_closed
  2. Gateway endpoint UDP datagram counters stay 0; Peer logs its counter line
  3. two held streams + a 3rd refused (egress_stream_limit); flag off -> both closed <= 5 s,
     Peer P8 egress_off
  4. Peer restart (SIGTERM) keeps bytes_today (persistent budget)
  5. a4_local hook SPIKE_A4_FAKE_UDP_DATAGRAMS=1 -> exact P8 relay_path_required line <= 2 s,
     `not retrying`, no redial
Prints A44_PILOT_E2E_OK.
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a42_self_relay_smoke as A  # noqa: E402
import a43_gateway_egress_smoke as G  # noqa: E402

P8_RELAY = next(c for c in json.load(open(A.ROOT / "fixtures" / "screens.json"))["consent"]
                if "reason_lines" in c)["reason_lines"]["relay_path_required"]
PEER_ADMIN = "http://127.0.0.1:9200"


def fail(msg):
    print(f"FAIL a44 e2e: {msg}", flush=True)
    raise SystemExit(1)


A.fail = fail
G.fail = fail


def ok(msg):
    print(f"OK {msg}", flush=True)


def gw_udp(label):
    ip = G.health().get("iroh_path", {})
    if ip.get("udp_send") or ip.get("udp_recv") or ip.get("path") not in ("relay", "none"):
        fail(f"{label}: gateway UDP datagram counters not 0: {ip}")
    return ip


def peer_egress():
    return A.ns_http("a42-peer", "GET", PEER_ADMIN + "/peer/egress")[1]


def stop_peer(p):
    """SIGTERM the real stream-peer (graceful: budget flush), then reap the sudo wrapper."""
    pid = G.ns_pid("a42-peer", str(A.PEER))
    subprocess.run(["sudo", "-n", "kill", "-TERM", str(pid)], capture_output=True)
    try:
        p.p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        G.kill_ns_proc("a42-peer", "SPIKE_PEER_ADMIN=127.0.0.1:9200")
    G.drain(p, 0.3)
    time.sleep(0.5)


def body(tmp: Path):
    cert, key = tmp / "site.pem", tmp / "site.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj",
                    "/CN=echo.example.com", "-addext", "subjectAltName=DNS:echo.example.com", "-keyout", key,
                    "-out", cert], check=True, capture_output=True)
    os.chmod(key, 0o644)
    site = A.Proc("a43-site", [sys.executable, "-u", G.SITE, cert, key], A.base_env(), "site")
    site.expect("SITE_READY 198.51.100.10:443")
    A.Proc("a42-relay", [A.RELAY], A.base_env({"STREAM_RELAY_MODE": "dev", "STREAM_RELAY_HTTP_BIND": "10.73.0.254:3340"}),
           "relay").expect("STREAM_RELAY_READY")
    A.Proc("a42-relay", [sys.executable, "-u", A.ROOT / "control" / "main.py"], A.base_env({
        "SPIKE_DB": tmp / "control.sqlite", "SPIKE_LISTEN": "10.73.0.254:8080", "SPIKE_TICKET_SECRET": A.SECRET,
        "FIXTURES": A.ROOT / "fixtures", "SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_A4_LANE": "local",
        "SPIKE_RELAY_ALLOW_URL": A.RELAY_URL, "SPIKE_GATEWAY_ENDPOINT_ID": A.DEV_ID, "PYTHONPATH": A.ROOT / "control",
        "SPIKE_EGRESS_ALLOWLIST": G.ALLOW,
    }), "control")
    A.wait_http("a42-relay", A.CONTROL + "/health")
    gw = A.Proc("a42-gw", [A.GW], G.gw_env(), "gateway")
    gw.expect("IROH_PILOT_READY", timeout=30)
    A.wait_http("a42-gw", A.GW_HTTP + "/health")
    G.flag(True)
    G.wait_effective(True)

    budget = tmp / "peer_egress_budget"
    pe = A.peer_env({"SPIKE_PEER_ID": "peer_a44", "SPIKE_PUBLIC_EGRESS": "1", "SPIKE_P9_ACK": "1",
                     "SPIKE_EGRESS_ALLOWLIST": G.ALLOW, "SPIKE_PEER_ADMIN": "127.0.0.1:9200", "HOME": str(tmp),
                     "SPIKE_IROH_KEY_PATH": str(tmp / "peer.key"), "SPIKE_EGRESS_BUDGET_PATH": str(budget)})

    def real_peer(name, extra=None):
        p = A.Proc("a42-peer", [A.PEER], {**pe, **(extra or {})}, name)
        b = p.expect("iroh_pilot bound", timeout=40)
        if "quic=none" not in b:
            fail(f"relay map QAD not off: {b}")
        p.expect("iroh_pilot connected", timeout=60)
        return p, p.expect("peer iroh_pilot path ", timeout=5)

    # 1. TLS through the DATA pump
    peer, path = real_peer("peer")
    if not ("udp_counters total=0 " in path or "udp_counters=unavailable" in path):
        fail(f"peer UDP counters at connect: {path}")
    st, r, sid = G.start("peer_a44", "echo.example.com")
    if st != 200 or not r.get("pipe_addr"):
        fail(f"/gw/start {st} {r}")
    res = G.tls(r["pipe_addr"], "/hello", str(cert)).expect("TLS_RESULT", timeout=20)
    if "tls=TLSv1.3" not in res or "status=200" not in res or "bytes=38 " not in res or "sha_ok=True" not in res:
        fail(f"hello {res}")
    peer.expect(f"peer egress_open {sid} echo.example.com:443 pinned=198.51.100.10:443", timeout=5)
    peer.expect(f"peer egress_end {sid} reason=site_closed", timeout=10)
    c = G.closed_entry(sid)
    if c.get("reason") != "site_closed" or c.get("closed_by") != "peer":
        fail(f"closed entry {c}")
    ok(f"TLS GET /hello via the real Peer DATA pump: {res.split('TLS_RESULT ')[1]}; Peer CLOSE site_closed ({c})")
    st, r, sid = G.start("peer_a44", "echo.example.com")
    res = G.tls(r["pipe_addr"], "/big?n=300000", str(cert)).expect("TLS_RESULT", timeout=40)
    if "tls=TLSv1.3" not in res or "bytes=300000" not in res or "sha_ok=True" not in res:
        fail(f"big {res}")
    ok(f"300 KB over DATA frames: {res.split('TLS_RESULT ')[1].split(' first=')[0]}")

    # 2. UDP counters
    ip = gw_udp("after transfers")
    ok(f"no UDP datagrams: gateway iroh_path {ip}; peer {path.split('path ', 1)[1].strip()}")

    # 3. stream cap + flag off
    holds = []
    for i in (1, 2):
        st, r, hsid = G.start("peer_a44", "echo.example.com")
        if st != 200:
            fail(f"hold {i}: {st} {r}")
        t = G.tls(r["pipe_addr"], "/hold", str(cert), "hold")
        t.expect("HOLD_OPEN", timeout=20)
        holds.append((t, hsid))
    st, r, _ = G.start("peer_a44", "echo.example.com")
    if st == 200 or "egress_stream_limit" not in json.dumps(r):
        fail(f"3rd stream not refused: {st} {r}")
    peer.expect("a4_refuse_egress_stream_limit:2", timeout=5)
    ok(f"2 held streams open; 3rd -> {st} {r.get('error')} (a4_refuse_egress_stream_limit:2)")
    t0 = time.time()
    G.flag(False)
    for t, hsid in holds:
        eof = t.expect("HOLD_EOF", timeout=8)
        dt = float(eof.split("t=")[1]) - t0
        if dt > 5.0:
            fail(f"held {hsid} closed after {dt:.2f}s")
    p8 = peer.expect("offline_reason=egress_off", timeout=8)
    ok(f"flag off -> both held streams closed in <= {time.time() - t0:.2f}s; Peer P8 {p8.strip()}")
    G.flag(True)
    G.wait_effective(True)

    # 4. restart keeps budget
    b1 = peer_egress().get("bytes_today", 0)
    if b1 < 300000:
        fail(f"bytes_today {b1} < 300 KB after the transfers")
    stop_peer(peer)
    if not any("egress_budget flushed on shutdown" in l for l in peer.lines):
        fail(f"no shutdown flush: {peer.lines[-6:]}")
    peer, _ = real_peer("peer-restart")
    b2 = peer_egress().get("bytes_today")
    if b2 != b1 or oct(budget.stat().st_mode & 0o777) != "0o600":
        fail(f"budget after restart {b2} != {b1} (mode {oct(budget.stat().st_mode & 0o777)})")
    ok(f"Peer restart (SIGTERM, flushed) keeps bytes_today={b2} (file 0600)")
    stop_peer(peer)

    # 5. fake UDP datagram -> trip once, no retry
    c = A.Proc("a42-peer", [A.PEER], {**pe, "SPIKE_A4_FAKE_UDP_DATAGRAMS": "1"}, "peer-fake")
    c.expect("SPIKE_A4_FAKE_UDP_DATAGRAMS=1", timeout=30)
    c.expect("iroh_pilot connected", timeout=60)
    t0 = time.time()
    trip = c.expect("a4_refuse_relay_refused:udp_datagrams:1", timeout=2)
    c.expect(P8_RELAY, timeout=max(0.1, 2 - (time.time() - t0)))
    dt = time.time() - t0
    nr = c.expect("not retrying", timeout=3)
    G.drain(c, 4)
    if dt > 2.0 or sum("iroh_pilot connected" in l for l in c.lines) != 1 or any("dial failed" in l for l in c.lines):
        fail(f"hook: dt={dt:.2f} lines={c.lines[-8:]}")
    if sum(l.strip() == P8_RELAY for l in c.lines) != 1:
        fail("P8 relay_path_required not shown exactly once")
    ok(f"hook: {trip.strip()} -> P8 '{P8_RELAY}' in {dt:.2f}s; {nr.strip()}; one connect, no redial in 4 s")
    stop_peer(c)
    print("A44_PILOT_E2E_OK", flush=True)


def main():
    lockf = open("/tmp/stream-spike-a42.lock", "w")
    end = time.time() + 1200  # other agents run a42/a43 on the same netns names
    while True:
        try:
            A.fcntl.flock(lockf, A.fcntl.LOCK_EX | A.fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() > end:
                fail("another a42/a43 run held /tmp/stream-spike-a42.lock for 20 min")
            time.sleep(5)
    cargo = A.shutil.which("cargo") or fail("cargo not found")
    td = A.RUST / "target" / "pilot"
    for pkg, feats, extra in (("stream-gateway", "iroh_pilot,a4_local", ["--bins"]),
                              ("stream-relay", "server,a4_local", []),
                              ("stream-peer", "iroh_pilot,a4_local", [])):
        A.run([cargo, "build", "--locked", "-q", "--target-dir", td, "-p", pkg, "--features", feats, *extra],
              cwd=A.RUST, capture_output=True)
    subprocess.run(["bash", A.ROOT / "scripts" / "a43_netns_down.sh"], capture_output=True)
    A.run(["bash", A.ROOT / "scripts" / "a43_netns_up.sh"], capture_output=True)
    tmp = Path(tempfile.mkdtemp(prefix="a44_"))
    os.chmod(tmp, 0o777)
    try:
        body(tmp)
    finally:
        for p in A.procs:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        subprocess.run(["bash", A.ROOT / "scripts" / "a43_netns_down.sh"], capture_output=True)
        A.shutil.rmtree(tmp, ignore_errors=True)
        for d in Path(tempfile.gettempdir()).glob("a42_peer_*"):
            if d.is_dir() and d.owner() == A.USER:
                A.shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
