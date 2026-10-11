#!/usr/bin/env python3
"""A4.4 relay-path watcher e2e (run by a44_peer_egress_smoke.py; standalone OK).

Real relay-only dial on the A4.2 topology (scripts/a42_netns_up.sh: relay http://10.73.0.254:3340/,
Gateway 10.73.0.1, Peer 10.73.0.200, no default route, no gw<->peer route), on-box only.
Reuses Platform's a42_self_relay_smoke helpers (same lock, same a42-* netns names).
  run A: real stream-peer (iroh_pilot,a4_local) connects relay-only; Peer logs its UDP counter
         line (0, or `unavailable` until stream-peer's iroh_pilot enables iroh/metrics), the
         Gateway's endpoint UDP counters stay 0 at connect, after the ticket and after 3 s; the
         Peer verifies a Control-minted relay ticket and answers `egress_off` (kill switch off
         until A4.3); no relay_path_required.
  run B: same Peer with the a4_local hook SPIKE_A4_FAKE_UDP_DATAGRAMS=1: exact P8
         relay_path_required line within 2 s of connect, `not retrying`, no redial for 4 s.
Prints A44_RELAY_PATH_OK.
"""
import json, os, queue, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a42_self_relay_smoke as A  # noqa: E402

P8_LINE = next(c for c in json.load(open(A.ROOT / "fixtures" / "screens.json"))["consent"]
               if "reason_lines" in c)["reason_lines"]["relay_path_required"]


def fail(msg):
    print(f"FAIL a44 relay path: {msg}", flush=True)
    raise SystemExit(1)


def drain(p, secs):
    end, out = time.time() + secs, []
    while time.time() < end:
        try:
            l = p.q.get(timeout=0.1)
        except queue.Empty:
            continue
        if l is None:
            break
        p.lines.append(l)
        out.append(l)
    return out


def gw_udp(label):
    _, h = A.ns_http("a42-gw", "GET", A.GW_HTTP + "/health")
    ip = h.get("iroh_path", {})
    if ip.get("udp_send") or ip.get("udp_recv") or ip.get("path") not in ("relay", "none"):
        fail(f"{label}: gateway UDP counters not 0: {ip}")
    return ip


def stop(p):
    p.p.terminate()
    try:
        p.p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.p.kill()


def body():
    relay = A.Proc("a42-relay", [A.RELAY], A.base_env({"STREAM_RELAY_MODE": "dev", "STREAM_RELAY_HTTP_BIND": "10.73.0.254:3340"}), "relay")
    relay.expect("STREAM_RELAY_READY")
    tmp = Path(A.tempfile.mkdtemp(prefix="a42_a44_"))
    os.chmod(tmp, 0o777)
    A.Proc("a42-relay", [sys.executable, "-u", A.ROOT / "control" / "main.py"], A.base_env({
        "SPIKE_DB": tmp / "control.sqlite", "SPIKE_LISTEN": "10.73.0.254:8080", "SPIKE_TICKET_SECRET": A.SECRET,
        "FIXTURES": A.ROOT / "fixtures", "SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_A4_LANE": "local",
        "SPIKE_RELAY_ALLOW_URL": A.RELAY_URL, "SPIKE_GATEWAY_ENDPOINT_ID": A.DEV_ID, "PYTHONPATH": A.ROOT / "control",
    }), "control")
    A.wait_http("a42-relay", A.CONTROL + "/health")
    gw = A.Proc("a42-gw", [A.GW], A.gw_env(), "gateway")
    gw.expect("IROH_PILOT_READY", timeout=30)
    A.wait_http("a42-gw", A.GW_HTTP + "/health")

    # run A: plain relay-only dial
    pe = A.peer_env()
    c = A.Proc("a42-peer", [A.PEER], pe, "peer")
    bound = c.expect("iroh_pilot bound", timeout=30)
    if "quic=none" not in bound:
        fail(f"relay map QAD not off: {bound}")
    cnt = c.expect("peer iroh_pilot udp_counters", timeout=10)
    c.expect("iroh_pilot connected", timeout=40)
    path = c.expect("peer iroh_pilot path ", timeout=5)
    if not ("udp_counters total=0 " in path or "udp_counters=unavailable" in path):
        fail(f"peer UDP counters not 0 at connect: {path}")
    gp = json.loads(gw.expect("IROH_PEER_CONNECTED", timeout=30).split("path=", 1)[1])
    if gp.get("udp_send") or gp.get("udp_recv"):
        fail(f"gateway path at connect: {gp}")
    st, m = A.ns_http("a42-relay", "POST", A.CONTROL + "/v1/match", {"account_id": "acct_demo",
                      "geo": {"country": "US", "city": "new_orleans"}, "rematch_mode": "city"})
    st, sess = A.ns_http("a42-relay", "POST", A.CONTROL + "/v1/sessions", {"quote_id": m["quote_id"]})
    if st != 200 or sess["ticket"]["payload"].get("relay_url") != A.RELAY_URL:
        fail(f"/v1/sessions {st} {sess}")
    st, r = A.ns_http("a42-gw", "POST", A.GW_HTTP + "/gw/start", {"peer_id": sess["peer_id"], "stream_id": sess["stream_id"],
                      "ticket_json": sess["ticket_json"], "dest_host": "example.com", "dest_port": 443})
    if st != 403 or r.get("error") != "egress_off":
        fail(f"/gw/start expected 403 egress_off, got {st} {r}")
    ip1 = gw_udp("after ticket")
    later = drain(c, 3)
    if any("relay_path_required" in l or "a4_refuse_relay_refused" in l for l in later):
        fail(f"run A tripped: {later}")
    ip2 = gw_udp("after 3 s")
    print(f"OK run A: relay-only connect; peer {cnt.split('iroh_pilot ', 1)[1]} / {path.split('path ', 1)[1]}; "
          f"ticket over relay -> peer answered egress_off (403); gateway iroh_path {ip1} then {ip2}; no trip", flush=True)
    stop(c)

    # run B: a4_local fake UDP datagram -> trip once, no retry
    c = A.Proc("a42-peer", [A.PEER], {**pe, "SPIKE_A4_FAKE_UDP_DATAGRAMS": "1"}, "peer-fake")
    c.expect("SPIKE_A4_FAKE_UDP_DATAGRAMS=1", timeout=30)
    c.expect("iroh_pilot connected", timeout=40)
    t0 = time.time()
    trip = c.expect("a4_refuse_relay_refused:udp_datagrams:1", timeout=2)
    c.expect(P8_LINE, timeout=max(0.1, 2 - (time.time() - t0)))
    dt = time.time() - t0
    if dt > 2.0:
        fail(f"P8 after {dt:.2f}s")
    nr = c.expect("not retrying", timeout=3)
    after = drain(c, 4)
    allb = c.lines
    if sum("iroh_pilot connected" in l for l in allb) != 1 or any("dial failed" in l for l in after):
        fail(f"redial after relay_path_required: {after}")
    if sum(l.strip() == P8_LINE for l in allb) != 1:
        fail(f"P8 line not shown exactly once: {[l for l in allb if 'secure route' in l]}")
    _, p8 = A.ns_http("a42-peer", "GET", "http://127.0.0.1:9200/peer/screen/p8")
    if P8_LINE not in json.dumps(p8):
        fail(f"/peer/screen/p8 {p8}")
    print(f"OK run B: {trip.strip()} -> P8 '{P8_LINE}' in {dt:.2f}s; {nr.strip()}; one connect, no redial in 4 s", flush=True)
    stop(c)
    print("A44_RELAY_PATH_OK", flush=True)


def main():
    lockf = open("/tmp/stream-spike-a42.lock", "w")
    # Other agents run a42/a43 on the same a42-* netns: wait for the shared lock (<= 20 min).
    end = time.time() + 1200
    while True:
        try:
            A.fcntl.flock(lockf, A.fcntl.LOCK_EX | A.fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() > end:
                fail("another a42 run held /tmp/stream-spike-a42.lock for 20 min")
            time.sleep(5)
    cargo = A.shutil.which("cargo") or fail("cargo not found")
    td = A.RUST / "target" / "pilot"
    for pkg, feats, extra in (("stream-gateway", "iroh_pilot,a4_local", ["--bins"]),
                              ("stream-relay", "server,a4_local", []),
                              ("stream-peer", "iroh_pilot,a4_local", [])):
        A.run([cargo, "build", "--locked", "-q", "--target-dir", td, "-p", pkg, "--features", feats, *extra], cwd=A.RUST)
    subprocess.run(["bash", A.ROOT / "scripts" / "a42_netns_down.sh"], capture_output=True)
    A.run(["bash", A.ROOT / "scripts" / "a42_netns_up.sh"], capture_output=True)
    try:
        body()
    finally:
        for p in A.procs:
            try:
                p.terminate()
            except Exception:
                pass
        subprocess.run(["bash", A.ROOT / "scripts" / "a42_netns_down.sh"], capture_output=True)
        for d in Path(A.tempfile.gettempdir()).glob("a42_*"):
            if d.is_dir() and d.owner() == A.USER:
                A.shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
