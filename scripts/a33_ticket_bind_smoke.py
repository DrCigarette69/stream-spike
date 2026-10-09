#!/usr/bin/env python3
"""A3.3 Control ticket binding smoke -> A3.3_TICKET_BIND_GREEN.

Reuses the A3.1 harness (scripts/a31_gateway_endpoint_smoke.py): throwaway `sudo ip netns`
with only `lo` (+10.73.0.1/24 on lo), no default route; Control + Rust Gateway
(`--features iroh`, SPIKE_TRANSPORT=iroh_local) + the a31 iroh test client. No relays,
no discovery, no public egress. All tickets here are minted by Control.

Shows:
  - Control (iroh_local) refuses to mint: missing / malformed peer endpoint ID -> endpoint_bind_required;
    empty direct_addrs -> direct_addrs_required; public direct addr -> public_addr;
    private-but-not-allowlisted -> allowlist_miss (SPIKE_IROH_ALLOW_CIDRS semantics from A3.0)
  - non-iroh transport mints exactly as before (no direct_addrs, old gateway id)
  - good bind end-to-end: Control-minted ticket binds the Gateway-authenticated peer ID,
    gateway_endpoint_id = dev ID, direct_addrs = gateway addr -> AUTH_TICKET -> AUTH_OK -> OPEN
  - mismatch at the Gateway: ticket minted for peer key A, presented after peer key B took over
    the same peer_id -> AUTH_REJECT endpoint_mismatch + close

Usage: python3 scripts/a33_ticket_bind_smoke.py   (box user with sudo)
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a31_gateway_endpoint_smoke as h  # noqa: E402

GOOD_ID = "a" * 64  # well-formed EndpointId text (Control only checks form; no dial in these cases)


def start_control(port: int, extra: dict) -> tuple[subprocess.Popen, str]:
    tmp = Path(tempfile.mkdtemp(prefix="a33_"))
    env = h.base_env()
    for k in ("SPIKE_TRANSPORT", "SPIKE_IROH_GATEWAY_ADDR", "SPIKE_IROH_ALLOW_CIDRS", "SPIKE_GATEWAY_ENDPOINT_ID"):
        env.pop(k, None)
    env.update({"SPIKE_DB": str(tmp / "control.sqlite"), "SPIKE_LISTEN": f"127.0.0.1:{port}",
                "SPIKE_TICKET_SECRET": h.SECRET, "FIXTURES": str(h.ROOT / "fixtures")})
    env.update(extra)
    p = subprocess.Popen([sys.executable, "-u", str(h.ROOT / "control" / "main.py")], cwd=str(h.ROOT / "control"),
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    h.procs.append(p)
    url = f"http://127.0.0.1:{port}"
    h.wait_http(url + "/health")
    return p, url


def try_mint(url: str, endpoint_id: str, peer_id: str = "peer_a33") -> tuple[int, dict]:
    st, _ = h.http("POST", url + "/v1/peers/enroll", {"peer_id": peer_id, "endpoint_id": endpoint_id,
                                                      "isp_ack_version": "v1", "host_tier": "always_on"})
    if st != 200:
        h.fail(f"enroll {st}")
    st, m = h.http("POST", url + "/v1/match", {"account_id": "acct_demo", "geo": {"country": "US", "city": "new_orleans"},
                                               "rematch_mode": "city"})
    if st != 200:
        h.fail(f"/v1/match {st} {m}")
    return h.http("POST", url + "/v1/sessions", {"quote_id": m["quote_id"]})


def expect_refusal(label, port, extra, endpoint_id, reason):
    p, url = start_control(port, extra)
    st, r = try_mint(url, endpoint_id)
    p.terminate()
    if st != 422 or r.get("error") != reason:
        h.fail(f"{label}: expected 422 {reason}, got {st} {r}")
    h.ok(f"{label}: /v1/sessions {st} error={r['error']} detail={r.get('detail')!r}")


def inner() -> int:
    routes = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
    routes6 = subprocess.run(["ip", "-6", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
    if routes or routes6:
        h.fail(f"netns has a default route: {routes!r} {routes6!r}")
    h.ok("netns: no default route (v4/v6)")

    iroh = {"SPIKE_TRANSPORT": "iroh_local", "SPIKE_IROH_GATEWAY_ADDR": h.GW_ADDR}
    # --- Control-only refusals (each on a fresh Control) ---
    expect_refusal("missing peer endpoint id", 8091, iroh, "", "endpoint_bind_required")
    expect_refusal("malformed peer endpoint id (uppercase)", 8092, iroh, "A" * 64, "endpoint_bind_required")
    expect_refusal("empty direct_addrs", 8093, {**iroh, "SPIKE_IROH_GATEWAY_ADDR": ""}, GOOD_ID, "direct_addrs_required")
    expect_refusal("public direct addr", 8094, {**iroh, "SPIKE_IROH_GATEWAY_ADDR": "10.73.0.1:9102,8.8.8.8:9102"},
                   GOOD_ID, "public_addr")
    expect_refusal("private addr outside default allowlist", 8095, {**iroh, "SPIKE_IROH_GATEWAY_ADDR": "192.168.1.5:9102"},
                   GOOD_ID, "allowlist_miss")
    p, url = start_control(8096, {**iroh, "SPIKE_IROH_GATEWAY_ADDR": "192.168.1.5:9102", "SPIKE_IROH_ALLOW_CIDRS": ""})
    st, r = try_mint(url, GOOD_ID)
    p.terminate()
    if st != 200 or r["ticket"]["payload"].get("direct_addrs") != ["192.168.1.5:9102"]:
        h.fail(f"SPIKE_IROH_ALLOW_CIDRS='' should allow any private addr: {st} {r}")
    h.ok("SPIKE_IROH_ALLOW_CIDRS='' (no narrowing): 192.168.1.5:9102 accepted")
    p, url = start_control(8097, {})
    st, r = try_mint(url, "iroh_ep_demo_001")
    p.terminate()
    pl = r.get("ticket", {}).get("payload", {})
    if st != 200 or "direct_addrs" in pl or pl.get("gateway_endpoint_id") != "iroh_ep_gateway_spike":
        h.fail(f"non-iroh transport changed: {st} {r}")
    h.ok("non-iroh transport unchanged: 200, no direct_addrs, gateway_endpoint_id=iroh_ep_gateway_spike")

    # --- End-to-end through the real Rust Gateway ---
    tmp = Path(tempfile.mkdtemp(prefix="a33_"))
    cenv = h.base_env()
    cenv.update({"SPIKE_DB": str(tmp / "control.sqlite"), "SPIKE_LISTEN": "127.0.0.1:8080",
                 "SPIKE_TICKET_SECRET": h.SECRET, "FIXTURES": str(h.ROOT / "fixtures"), **iroh})
    h.procs.append(subprocess.Popen([sys.executable, "-u", str(h.ROOT / "control" / "main.py")],
                                    cwd=str(h.ROOT / "control"), env=cenv,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    h.wait_http(h.CONTROL + "/health")
    gw = h.Proc([str(h.GW_IROH)], h.gw_env(), "gateway")
    gw.expect("IROH_LOCAL_READY")
    h.wait_http(h.GW_HTTP + "/health")

    a = h.Proc(h.client("--peer-id", "peer_a33"), h.base_env(), "peerA")
    aid = a.expect("CLIENT_ID").split()[1]
    a.expect('"HELLO_OK"')
    for _ in range(50):
        _, peers = h.http("GET", h.GW_HTTP + "/gw/peers")
        if any(p.get("peer_id") == "peer_a33" for p in peers.get("peers", [])):
            break
        time.sleep(0.1)
    sess = h.mint_session(h.CONTROL)
    pl = sess["ticket"]["payload"]
    want = {"peer_endpoint_id": aid, "gateway_endpoint_id": h.DEV_ID, "direct_addrs": [h.GW_ADDR]}
    got = {k: pl.get(k) for k in want}
    if got != want:
        h.fail(f"minted payload {got} != {want}")
    h.ok(f"Control minted: peer_endpoint_id={aid[:16]}… (gateway-authenticated) gateway_endpoint_id={h.DEV_ID[:16]}… direct_addrs={pl['direct_addrs']}")
    st, r = h.http("POST", h.GW_HTTP + "/gw/start", {"peer_id": sess["peer_id"], "stream_id": sess["stream_id"],
                                                      "ticket_json": sess["ticket_json"],
                                                      "dest_host": "echo.local", "dest_port": 443})
    a.expect('"AUTH_TICKET"')
    a.expect('"OPEN"')
    if st != 200 or not r.get("started"):
        h.fail(f"good bind /gw/start {st} {r}")
    h.ok(f"good bind end-to-end: /gw/start {st} started=True (AUTH_TICKET -> AUTH_OK -> OPEN)")
    h.http("POST", h.GW_HTTP + "/gw/stop", {"stream_id": sess["stream_id"], "peer_id": "peer_a33"})
    a.expect('"CLOSE"')

    # Ticket minted while key A is enrolled ...
    stale = h.mint_session(h.CONTROL)
    if stale["ticket"]["payload"]["peer_endpoint_id"] != aid:
        h.fail("second ticket not bound to A")
    # ... then key B takes over the same peer_id (gateway replaces A, Control re-enrolls B)
    b = h.Proc(h.client("--peer-id", "peer_a33"), h.base_env(), "peerB")
    bid = b.expect("CLIENT_ID").split()[1]
    b.expect('"HELLO_OK"')
    st, r = h.http("POST", h.GW_HTTP + "/gw/start", {"peer_id": stale["peer_id"], "stream_id": stale["stream_id"],
                                                      "ticket_json": stale["ticket_json"],
                                                      "dest_host": "echo.local", "dest_port": 443})
    rej = b.expect('"AUTH_REJECT"')
    closed = b.expect("CONN_CLOSED")
    if st != 403 or r.get("error") != "endpoint_mismatch" or "endpoint_mismatch" not in rej:
        h.fail(f"mismatch: {st} {r} {rej}")
    h.ok(f"mismatched ID at the Gateway (ticket for A={aid[:12]}…, conn B={bid[:12]}…): /gw/start {st} "
         f"error={r['error']}; peerB got AUTH_REJECT endpoint_mismatch; {closed}")
    # The Gateway dropped B on the mismatch and (A3.4) told Control, so B is no longer matchable.
    _, ev = h.http("GET", h.CONTROL + "/v1/events")
    off = [e for e in ev.get("events", []) if e.get("event") == "peer.offline" and e.get("peer_id") == "peer_a33"]
    if not off or off[-1].get("endpoint_id") != bid:
        h.fail(f"Control not told B went offline: {off}")
    h.ok(f"Gateway reported the dropped connection: Control peer.offline endpoint={bid[:12]}… reason={off[-1].get('reason')}")

    print("\nA3.3_TICKET_BIND_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(inner() if "--inner" in sys.argv else h.outer(script=__file__))
    finally:
        h.cleanup()
