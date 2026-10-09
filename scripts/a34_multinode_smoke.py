#!/usr/bin/env python3
"""A3.4 multi-node smoke -> A3.4_MULTINODE_GREEN.

Topology from scripts/a3_netns_up.sh (bridge br-a3 10.73.0.0/24, no default route anywhere):
  ns-gw     10.73.0.1   Control (10.73.0.1:8080) + Rust Gateway (--features iroh, iroh 10.73.0.1:9102)
  ns-peer-a 10.73.0.11  Peer A (own key)
  ns-peer-b 10.73.0.12  Peer B (own key)
Peers reach Control/Gateway only over br-a3. No relays, no discovery, no public egress.

Shows: no default route in any ns; both Peers enrolled with their own authenticated IDs;
Control-minted sessions land on Peer A and on Peer B (tickets bound to each); Peer A hard-killed
(SIGKILL, no goodbye) -> Gateway notices (QUIC idle timeout) and tells Control -> new sessions are
served by Peer B; a3_netns_down.sh removes everything.

Peer implementation: A34_PEER=real|testclient|auto (default auto = real stream-peer --features iroh_local
when it has the A3.2 iroh dial, which it does since c21c0e5; testclient = a31 test client stand-in). Everything else is identical.

Usage: python3 scripts/a34_multinode_smoke.py   (box user with sudo)
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
import a31_gateway_endpoint_smoke as h  # noqa: E402

GW_IP = "10.73.0.1"
GW_ADDR = f"{GW_IP}:9102"
CONTROL = f"http://{GW_IP}:8080"
NODES = {"peer_a": ("ns-peer-a", "10.73.0.11"), "peer_b": ("ns-peer-b", "10.73.0.12")}
ALL_NS = ["ns-a3-br", "ns-gw", "ns-peer-a", "ns-peer-b"]
PEER_BIN = h.IROH_TARGET / "debug" / "stream-peer"
USER = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()


def peer_mode() -> str:
    m = os.environ.get("A34_PEER", "auto").strip().lower()
    if m in ("real", "testclient"):
        return m
    src = "".join(p.read_text() for p in (h.RUST / "crates" / "stream-peer" / "src").glob("*.rs"))
    return "real" if "empty_builder" in src else "testclient"


# ---------------------------------------------------------------- outer
def outer() -> int:
    cargo = shutil.which("cargo") or h.fail("cargo not found")
    mode = peer_mode()
    h.run([cargo, "build", "--locked", "-q", "--target-dir", str(h.IROH_TARGET), "-p", "stream-gateway",
           "--features", "iroh", "--bins", "--examples"], cwd=h.RUST)
    if mode == "real":
        h.run([cargo, "build", "--locked", "-q", "--target-dir", str(h.IROH_TARGET), "-p", "stream-peer",
               "--features", "iroh_local"], cwd=h.RUST)
    up = Path(__file__).resolve().parent / "a3_netns_up.sh"
    down = Path(__file__).resolve().parent / "a3_netns_down.sh"
    rc = 1
    try:
        h.run(["bash", str(up)])
        rc = subprocess.run(
            ["sudo", "ip", "netns", "exec", "ns-gw", "sudo", "-u", USER, "env",
             f"PATH={os.environ.get('PATH', '')}", f"HOME={os.environ.get('HOME', '')}", f"A34_PEER={mode}",
             sys.executable, "-u", str(Path(__file__).resolve()), "--inner"]).returncode
    finally:
        d = subprocess.run(["bash", str(down)], capture_output=True, text=True)
        left = [n for n in subprocess.run(["sudo", "ip", "netns", "list"], capture_output=True, text=True)
                .stdout.split() if n in ALL_NS]
        if d.returncode != 0 or left:
            print(f"FAIL a34 teardown: rc={d.returncode} left={left} {d.stderr.strip()}", flush=True)
            rc = rc or 1
        else:
            print(f"OK {d.stdout.strip()}: ns-gw/ns-peer-a/ns-peer-b/ns-a3-br + br-a3 removed", flush=True)
    if rc == 0:
        print("\nA3.4_MULTINODE_GREEN", flush=True)
    return rc


# ---------------------------------------------------------------- inner (runs in ns-gw)
def ns_cmd(ns: str, argv: list[str], env: dict) -> list[str]:
    kv = [f"{k}={v}" for k, v in env.items()]
    return ["sudo", "ip", "netns", "exec", ns, "sudo", "-u", USER, "env", "-i", *kv, *argv]


def start_peer(mode: str, peer_id: str, tmp: Path) -> tuple[h.Proc, str | None]:
    ns, ip = NODES[peer_id]
    base = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "RUST_LOG": "info", "NO_COLOR": "1"}
    if mode == "testclient":
        p = h.Proc(ns_cmd(ns, [str(h.CLIENT), "--gateway-id", h.DEV_ID, "--direct-addr", GW_ADDR,
                               "--peer-id", peer_id, "--bind", ip], base), base, peer_id)
        cid = p.expect("CLIENT_ID").split()[1]
        p.expect('"HELLO_OK"')
        return p, cid
    env = {**base,
           # own key per Peer (else both share an endpoint id); bind on the ns address; headless
           # consent = ISP ack (P1) + SPIKE_P2_CONSENT=1 together
           "SPIKE_TRANSPORT": "iroh_local", "SPIKE_IROH_KEY_PATH": str(tmp / f"{peer_id}.key"),
           "SPIKE_IROH_BIND": f"{ip}:0",
           "SPIKE_GATEWAY_ENDPOINT_ID": h.DEV_ID, "SPIKE_IROH_GATEWAY_ADDR": GW_ADDR,
           "CONTROL_URL": CONTROL, "SPIKE_PEER_ID": peer_id, "SPIKE_PEER_ADMIN": "127.0.0.1:9200",
           "SPIKE_ISP_ACK_VERSION": "v1", "SPIKE_P2_CONSENT": "1", "SPIKE_HOST_TIER": "always_on",
           "SPIKE_HEARTBEAT_S": "2"}
    return h.Proc(ns_cmd(ns, [str(PEER_BIN)], env), base, peer_id), None


def gw_peers() -> dict[str, str]:
    _, r = h.http("GET", h.GW_HTTP + "/gw/peers")
    return {p["peer_id"]: p.get("endpoint_id", "") for p in r.get("peers", [])}


def wait_peers(pred, label, timeout=30.0) -> dict[str, str]:
    end = time.time() + timeout
    while time.time() < end:
        cur = gw_peers()
        if pred(cur):
            return cur
        time.sleep(0.2)
    h.fail(f"{label}: gateway peers={gw_peers()}")


def serve(sess: dict, procs: dict, mode: str) -> None:
    pid = sess["peer_id"]
    st, r = h.http("POST", h.GW_HTTP + "/gw/start", {"peer_id": pid, "stream_id": sess["stream_id"],
                                                      "ticket_json": sess["ticket_json"],
                                                      "dest_host": "echo.local", "dest_port": 443})
    if st != 200 or not r.get("started"):
        h.fail(f"/gw/start on {pid}: {st} {r}")
    if mode == "testclient":
        procs[pid].expect('"AUTH_TICKET"', show=False)
        procs[pid].expect('"OPEN"', show=False)
    h.http("POST", h.GW_HTTP + "/gw/stop", {"stream_id": sess["stream_id"], "peer_id": pid})


def inner() -> int:
    mode = os.environ.get("A34_PEER", "testclient")
    print(f"peer implementation: {mode}" + (" (a31 test client stand-in, A34_PEER=testclient)"
                                            if mode == "testclient" else " (real stream-peer --features iroh_local)"), flush=True)
    for ns in ALL_NS:
        d = subprocess.run(["sudo", "ip", "-n", ns, "route", "show", "default"], capture_output=True, text=True).stdout.strip()
        d6 = subprocess.run(["sudo", "ip", "-n", ns, "-6", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
        if d or d6:
            h.fail(f"default route in {ns}: {d} {d6}")
    h.ok("no default route (v4/v6) in ns-a3-br, ns-gw, ns-peer-a, ns-peer-b")

    tmp = Path(tempfile.mkdtemp(prefix="a34_"))
    cenv = h.base_env()
    cenv.update({"SPIKE_DB": str(tmp / "control.sqlite"), "SPIKE_LISTEN": f"{GW_IP}:8080",
                 "SPIKE_TICKET_SECRET": h.SECRET, "FIXTURES": str(h.ROOT / "fixtures"),
                 "SPIKE_TRANSPORT": "iroh_local", "SPIKE_IROH_GATEWAY_ADDR": GW_ADDR})
    h.procs.append(subprocess.Popen([sys.executable, "-u", str(h.ROOT / "control" / "main.py")],
                                    cwd=str(h.ROOT / "control"), env=cenv,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    h.wait_http(CONTROL + "/health")
    gw = h.Proc([str(h.GW_IROH)], h.gw_env({"CONTROL_URL": CONTROL, "SPIKE_IROH_LISTEN": GW_ADDR}), "gateway")
    gw.expect("IROH_LOCAL_READY")
    h.wait_http(h.GW_HTTP + "/health")

    procs: dict[str, h.Proc] = {}
    ids: dict[str, str | None] = {}
    for pid in NODES:
        procs[pid], ids[pid] = start_peer(mode, pid, tmp)
    cur = wait_peers(lambda c: all(p in c for p in NODES), "both peers online")
    for pid in NODES:
        if ids[pid] and cur[pid] != ids[pid]:
            h.fail(f"{pid} enrolled {cur[pid]} != its key {ids[pid]}")
        ids[pid] = cur[pid]
    if ids["peer_a"] == ids["peer_b"]:
        h.fail("peers share an endpoint id")
    h.ok(f"both peers online over br-a3: peer_a={ids['peer_a'][:16]}… (10.73.0.11) peer_b={ids['peer_b'][:16]}… (10.73.0.12)")

    landed = []
    for i in range(2):
        s = h.mint_session(CONTROL)
        pl = s["ticket"]["payload"]
        if pl["peer_endpoint_id"] != ids[s["peer_id"]] or pl.get("direct_addrs") != [GW_ADDR]:
            h.fail(f"session {i} ticket not bound to {s['peer_id']}: {pl}")
        serve(s, procs, mode)
        landed.append(s["peer_id"])
        h.ok(f"session {i + 1} -> {s['peer_id']} (ticket peer_endpoint_id={pl['peer_endpoint_id'][:16]}…) served: AUTH_TICKET -> AUTH_OK -> OPEN")
    if sorted(landed) != ["peer_a", "peer_b"]:
        h.fail(f"sessions did not land on both peers: {landed}")

    # Hard-kill Peer A: SIGKILL every process in ns-peer-a (no QUIC close, no kill-switch).
    t0 = time.time()
    pids = subprocess.run(["sudo", "ip", "netns", "pids", "ns-peer-a"], capture_output=True, text=True).stdout.split()
    subprocess.run(["sudo", "kill", "-9", *pids])
    wait_peers(lambda c: "peer_a" not in c and "peer_b" in c, "gateway drops killed peer_a", timeout=20)
    dt = time.time() - t0
    _, ev = h.http("GET", CONTROL + "/v1/events")
    off = [e for e in ev.get("events", []) if e.get("event") == "peer.offline" and e.get("peer_id") == "peer_a"]
    if not off:
        h.fail("Control never marked peer_a offline")
    h.ok(f"peer_a SIGKILLed: gateway dropped it after {dt:.1f}s (QUIC idle timeout), Control peer.offline reason={off[-1].get('reason')}")

    for i in range(2):
        s = h.mint_session(CONTROL)
        if s["peer_id"] != "peer_b" or s["ticket"]["payload"]["peer_endpoint_id"] != ids["peer_b"]:
            h.fail(f"post-kill session went to {s['peer_id']}")
        serve(s, procs, mode)
        h.ok(f"post-kill session {i + 1} -> peer_b served")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(inner() if "--inner" in sys.argv else outer())
    finally:
        h.cleanup()
