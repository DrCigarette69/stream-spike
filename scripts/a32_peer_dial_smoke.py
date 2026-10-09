#!/usr/bin/env python3
"""A3.2 Peer iroh_local dial smoke -> A3.2_PEER_DIAL_GREEN.

Runs Control + Rust Gateway (`--features iroh`) + Rust Peer (`--features iroh_local`)
inside a throwaway `sudo ip netns` with only `lo` (+ 10.73.0.1/24) and NO default
route (iroh 0.95.1's portmapper cannot be disabled). No relays, discovery or egress.

Shows:
  - Peer start-time refusals: public gateway addr, relay URL, wildcard bind, and a
    default (non-iroh) build -> iroh_local_feature_not_built; admin /health stays up
  - no dial before P1 ack + P2 consent; then `UX_SCREEN p1_isp_ack`,
    `UX_SCREEN p2_consent` print before the dial
  - Peer dials Gateway by endpoint ID + direct addr on ALPN stream/tunnel/1;
    Gateway sees the Peer's authenticated iroh id; HELLO -> AUTH_TICKET -> OPEN ->
    BYTES -> CLOSE work over the bi-stream
  - kill: Gateway drops the Peer < 2 s, `UX_SCREEN p4_kill`; resume redials
  - tickets are minted by Control (A3.3: /v1/match -> /v1/sessions) and carry the
    Peer's key-authenticated peer_endpoint_id, gateway_endpoint_id and direct_addrs
  - wrong key: ticket minted for key A, Peer restarted with key B (same peer_id) ->
    Gateway AUTH_REJECT endpoint_mismatch -> Peer closes, does not retry
  - persistent key: restart -> same iroh endpoint id

Run as the normal user (cargo builds stay user-owned); only netns setup/exec uses
`sudo -n`. If cargo or non-interactive sudo is unavailable: SKIP + exit 0, unless
SPIKE_IMPL=rust or SPIKE_A3=1 (then FAIL, exit 1). If the Gateway has no iroh
listener: A3.2_WAITING_GATEWAY, exit 3.
Usage: python3 scripts/a32_peer_dial_smoke.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a31_gateway_endpoint_smoke as a31  # noqa: E402  (Proc, http, ticket, env helpers)

ROOT, RUST = a31.ROOT, a31.RUST
PEER_IROH = a31.IROH_TARGET / "debug" / "stream-peer"
PEER_DEFAULT = RUST / "target" / "debug" / "stream-peer"
DEV_ID, GW_ADDR, CONTROL, GW_HTTP = a31.DEV_ID, a31.GW_ADDR, a31.CONTROL, a31.GW_HTTP
PEER = "http://127.0.0.1:9200"
ok, http, Proc = a31.ok, a31.http, a31.Proc


def fail(msg: str):
    print(f"FAIL a32: {msg}", flush=True)
    raise SystemExit(1)


a31.fail = fail  # a31 helpers (Proc.expect, wait_http) report as a32


def gateway_has_iroh() -> bool:
    return (RUST / "crates" / "stream-gateway" / "src" / "iroh_local.rs").exists()


def preflight() -> bool:
    """Same skip rules as run_local_asserts.py a31."""
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    strict = impl == "rust" or os.environ.get("SPIKE_A3", "").strip() == "1"
    why = "SPIKE_IMPL=rust" if impl == "rust" else "SPIKE_A3=1"

    def unavailable(reason: str) -> bool:
        if strict:
            fail(f"{reason} but {why}")
        print(f"SKIP a32 peer dial smoke: {reason} (set SPIKE_A3=1 or SPIKE_IMPL=rust to make this fatal)", flush=True)
        return False

    if shutil.which("cargo") is None:
        return unavailable("cargo not found")
    if shutil.which("sudo") is None:
        return unavailable("sudo not found (needed for ip netns)")
    if os.geteuid() != 0:
        try:
            p = subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            sudo_ok = p.returncode == 0
        except Exception:
            sudo_ok = False
        if not sudo_ok:
            return unavailable("non-interactive sudo (sudo -n) unavailable")
    return True


def outer() -> int:
    if not preflight():
        return 0
    if not gateway_has_iroh():
        print("A3.2_WAITING_GATEWAY (stream-gateway has no iroh_local listener yet)", flush=True)
        return 3
    cargo = shutil.which("cargo")
    a31.run([cargo, "build", "--locked", "-q", "-p", "stream-peer", "-p", "stream-gateway"], cwd=RUST)
    a31.run([cargo, "build", "--locked", "-q", "--target-dir", str(a31.IROH_TARGET),
             "-p", "stream-gateway", "--features", "iroh", "--bins"], cwd=RUST)
    a31.run([cargo, "build", "--locked", "-q", "--target-dir", str(a31.IROH_TARGET),
             "-p", "stream-peer", "--features", "iroh_local"], cwd=RUST)
    ns = f"a32-{os.getpid()}"
    user = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
    try:
        a31.run(["sudo", "-n", "ip", "netns", "add", ns])
        a31.run(["sudo", "-n", "ip", "-n", ns, "link", "set", "lo", "up"])
        a31.run(["sudo", "-n", "ip", "-n", ns, "addr", "add", "10.73.0.1/24", "dev", "lo"])
        p = subprocess.run(["sudo", "-n", "ip", "netns", "exec", ns, "sudo", "-n", "-u", user, "env",
                            f"PATH={os.environ.get('PATH', '')}", f"HOME={os.environ.get('HOME', '')}",
                            sys.executable, "-u", str(Path(__file__).resolve()), "--inner"],
                           stdin=subprocess.DEVNULL)
        return p.returncode
    finally:
        subprocess.run(["sudo", "-n", "ip", "netns", "del", ns])


def peer_env(tmp: Path, extra=None) -> dict:
    env = a31.base_env()
    for k in ("SPIKE_ISP_ACK_VERSION", "SPIKE_P2_CONSENT", "SPIKE_PORT_BASE"):
        env.pop(k, None)
    env.update({
        "CONTROL_URL": CONTROL, "SPIKE_PEER_ADMIN": "127.0.0.1:9200", "SPIKE_PEER_ID": "peer_a32",
        "SPIKE_TRANSPORT": "iroh_local", "SPIKE_IROH_KEY_PATH": str(tmp / "peer_iroh.key"),
        "SPIKE_GATEWAY_ENDPOINT_ID": DEV_ID, "SPIKE_IROH_GATEWAY_ADDR": GW_ADDR,
        "SPIKE_IROH_BIND": "10.73.0.1:0", "SPIKE_HEARTBEAT_S": "1",
    })
    env.update(extra or {})
    return env


def stop(proc: Proc):
    proc.p.terminate()
    try:
        proc.p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.p.kill()


def peer_refusal(binary, tmp, extra, needle, label):
    p = Proc([str(binary)], peer_env(tmp, extra), label)
    line = p.expect(needle, timeout=10)
    a31.wait_http(PEER + "/health")
    _, h = http("GET", PEER + "/health")
    if h.get("connected"):
        fail(f"{label}: connected despite refusal")
    ok(f"{label}: {line.strip()} (admin up, last_error={h.get('last_error')!r})")
    stop(p)


def gw_peer():
    _, peers = http("GET", GW_HTTP + "/gw/peers")
    return next((p for p in peers.get("peers", []) if p.get("peer_id") == "peer_a32"), None)


def wait(pred, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    fail(f"timeout: {what}")


def inner() -> int:
    if subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.strip():
        fail("netns has a default route")
    ok("netns: no default route")
    tmp = Path(tempfile.mkdtemp(prefix="a32_"))

    peer_refusal(PEER_IROH, tmp, {"SPIKE_IROH_GATEWAY_ADDR": "8.8.8.8:9102"}, "A3 iroh_local refused: public_addr", "peer public direct addr refused")
    peer_refusal(PEER_IROH, tmp, {"SPIKE_IROH_GATEWAY_ADDR": "https://use1-1.relay.n0.iroh.iroh.link./"}, "A3 iroh_local refused: relay_refused", "peer relay URL refused")
    peer_refusal(PEER_IROH, tmp, {"SPIKE_IROH_BIND": "0.0.0.0:0"}, "A3 iroh_local refused: public_addr", "peer wildcard bind refused")
    peer_refusal(PEER_DEFAULT, tmp, {}, "A3 iroh_local refused: iroh_local_feature_not_built", "default build refuses iroh_local")

    cenv = a31.base_env()
    cenv.update({"SPIKE_DB": str(tmp / "control.sqlite"), "SPIKE_LISTEN": "127.0.0.1:8080",
                 "SPIKE_TICKET_SECRET": a31.SECRET, "FIXTURES": str(ROOT / "fixtures"),
                 "SPIKE_TRANSPORT": "iroh_local", "SPIKE_IROH_GATEWAY_ADDR": GW_ADDR})
    a31.procs.append(subprocess.Popen([sys.executable, "-u", str(ROOT / "control" / "main.py")], cwd=str(ROOT / "control"),
                                      env=cenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    a31.wait_http(CONTROL + "/health")
    gw = Proc([str(a31.GW_IROH)], a31.gw_env(), "gateway")
    gw.expect("IROH_LOCAL_READY")
    a31.wait_http(GW_HTTP + "/health")

    peer = Proc([str(PEER_IROH)], peer_env(tmp), "peer")
    pid = peer.expect("peer iroh_endpoint_id=").split("=", 1)[1].strip()
    a31.wait_http(PEER + "/health")
    ok(f"peer iroh_endpoint_id={pid}")
    http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1", "understood": True})
    time.sleep(1.2)
    if gw_peer() or any("UX_SCREEN p1_isp_ack" in l for l in peer.lines):
        fail("dialed before P2 consent")
    ok("P1 ack only: no dial (P2 consent required)")
    st, c = http("POST", PEER + "/peer/consent", {"accepted": True})
    if st != 200 or not c.get("p2_consent"):
        fail(f"consent {st} {c}")
    peer.expect("UX_SCREEN p1_isp_ack")
    peer.expect("UX_SCREEN p2_consent")
    conn_line = peer.expect("peer iroh_local connected")
    peer.expect("peer online")
    ok(f"P1 -> P2 screen IDs before dial; {conn_line.strip()}")
    g = wait(gw_peer, 5, "gateway lists peer")
    if g.get("endpoint_id") != pid:
        fail(f"gateway endpoint_id {g.get('endpoint_id')} != {pid}")
    ok(f"gateway sees authenticated endpoint_id={pid}")

    sess = a31.mint_session(CONTROL)
    pl = sess["ticket"]["payload"]
    want = {"peer_endpoint_id": pid, "gateway_endpoint_id": DEV_ID, "direct_addrs": [GW_ADDR]}
    if sess.get("peer_id") != "peer_a32" or {k: pl.get(k) for k in want} != want:
        fail(f"Control-minted ticket {sess.get('peer_id')} {pl}")
    ok(f"Control minted ticket: peer_endpoint_id={pid[:16]}… gateway_endpoint_id={DEV_ID[:16]}… direct_addrs={pl['direct_addrs']}")
    sid = sess["stream_id"]
    st, r = http("POST", GW_HTTP + "/gw/start", {"peer_id": "peer_a32", "stream_id": sid, "ticket_json": sess["ticket_json"],
                                                 "dest_host": "echo.local", "dest_port": 443})
    if st != 200 or not r.get("started"):
        fail(f"/gw/start {st} {r}")
    s = wait(lambda: (http("GET", PEER + "/peer/status")[1].get("streams", {}).get(sid) or {}).get("bytes", 0) > 0
             and http("GET", PEER + "/peer/status")[1]["streams"][sid], 5, "BYTES on peer")
    http("POST", GW_HTTP + "/gw/stop", {"stream_id": sid, "peer_id": "peer_a32"})
    peer.expect(f"peer teardown stream {sid} (gateway CLOSE)")
    ok(f"AUTH_TICKET -> AUTH_OK -> OPEN(opened={s.get('opened')}) -> BYTES(>0) -> CLOSE over iroh bi-stream")

    t0 = time.time()
    http("POST", PEER + "/peer/kill")
    wait(lambda: gw_peer() is None, 2.0, "gateway drops peer within 2 s of kill")
    dt = time.time() - t0
    peer.expect("UX_SCREEN p4_kill")
    ok(f"kill: gateway dropped peer in {dt:.2f}s (<2s); UX_SCREEN p4_kill")
    http("POST", PEER + "/peer/resume")
    peer.expect("peer iroh_local connected")
    wait(gw_peer, 5, "redial after resume")
    ok("resume: redialed")

    stale = a31.mint_session(CONTROL)  # bound to key A
    if stale["ticket"]["payload"].get("peer_endpoint_id") != pid:
        fail("second ticket not bound to key A")
    stop(peer)
    peerb = Proc([str(PEER_IROH)], peer_env(tmp, {"SPIKE_IROH_KEY_PATH": str(tmp / "peer_b.key"),
                                                   "SPIKE_ISP_ACK_VERSION": "v1", "SPIKE_P2_CONSENT": "1"}), "peerB")
    bid = peerb.expect("peer iroh_endpoint_id=").split("=", 1)[1].strip()
    peerb.expect("peer iroh_local connected")
    wait(lambda: (gw_peer() or {}).get("endpoint_id") == bid, 5, "gateway lists key B")
    ok(f"key B {bid[:12]}… online via SPIKE_ISP_ACK_VERSION + SPIKE_P2_CONSENT=1 presets")
    st, r = http("POST", GW_HTTP + "/gw/start", {"peer_id": "peer_a32", "stream_id": stale["stream_id"],
                                                 "ticket_json": stale["ticket_json"], "dest_host": "echo.local", "dest_port": 443})
    rej = peerb.expect("peer iroh_local AUTH_REJECT endpoint_mismatch")
    if st != 403 or r.get("error") != "endpoint_mismatch":
        fail(f"mismatch /gw/start {st} {r}")
    time.sleep(2.0)
    _, h = http("GET", PEER + "/health")
    if gw_peer() or h.get("connected") or h.get("last_error") != "endpoint_mismatch":
        fail(f"after mismatch: gw={gw_peer()} peer={h.get('connected')} {h.get('last_error')}")
    if sum("peer iroh_local connected" in l for l in peerb.lines) != 1:
        fail("peer redialed after endpoint_mismatch")
    ok(f"wrong key (ticket for A={pid[:12]}…, conn B={bid[:12]}…): /gw/start 403 endpoint_mismatch; {rej.strip()}; no retry after 2 s")
    stop(peerb)

    peer2 = Proc([str(PEER_IROH)], peer_env(tmp), "peer2")
    pid2 = peer2.expect("peer iroh_endpoint_id=").split("=", 1)[1].strip()
    if pid2 != pid:
        fail(f"key not persistent: {pid} -> {pid2}")
    mode = oct((tmp / "peer_iroh.key").stat().st_mode & 0o777)
    ok(f"restart: same endpoint id; key mode {mode}")
    stop(peer2)

    print("\nA3.2_PEER_DIAL_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(inner() if "--inner" in sys.argv else outer())
    finally:
        a31.cleanup()
