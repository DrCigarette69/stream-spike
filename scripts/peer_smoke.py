#!/usr/bin/env python3
"""Local Peer §9 smoke (no Docker): control + gateway + real peer/main.py."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_peer_launch import control_cmd, gateway_cmd, peer_cmd  # SPIKE_IMPL=rust|python
import spike_ports  # SPIKE_PORT_BASE / explicit env wins / legacy defaults
CONTROL, GATEWAY, PEER = spike_ports.urls()
procs: list[subprocess.Popen] = []


def http(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_http(url, n=50):
    for _ in range(n):
        try:
            st, _ = http("GET", url)
            if st == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting {url}")


def start():
    env = spike_ports.apply(os.environ.copy())
    env["SPIKE_DB"] = str(spike_ports.db_file(ROOT, ".peer_smoke.sqlite", env))
    Path(env["SPIKE_DB"]).unlink(missing_ok=True)
    env["SPIKE_TICKET_SECRET"] = "dev-only-change-me"
    env["SPIKE_ISP_ACK_VERSION"] = "v1"
    env["SPIKE_HOST_TIER"] = "always_on"
    env["SPIKE_PEER_ID"] = "peer_demo"
    env["SPIKE_ENDPOINT_ID"] = "iroh_ep_demo_001"
    env["SPIKE_HEARTBEAT_S"] = "2"
    for argv, cwd in (control_cmd(env), gateway_cmd(env), peer_cmd(env)):
        procs.append(
            subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        )
        time.sleep(0.35)
    wait_http(CONTROL + "/health")
    wait_http(GATEWAY + "/health")
    wait_http(PEER + "/health")
    # peer online on gateway
    for _ in range(40):
        st, g = http("GET", GATEWAY + "/gw/peers")
        if st == 200 and any(p["peer_id"] == "peer_demo" for p in g.get("peers", [])):
            return
        time.sleep(0.1)
    raise SystemExit("peer never appeared on gateway")


def cleanup(*_):
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
    time.sleep(0.2)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


def match_session(label="peer_smoke"):
    st, m = http(
        "POST",
        CONTROL + "/v1/match",
        {"account_id": "acct_demo", "geo": {"country": "US", "city": "new_orleans"}, "rematch_mode": "city"},
    )
    assert st == 200, m
    st, s = http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": label})
    assert st == 200, s
    return m, s


def main():
    start()
    http("POST", CONTROL + "/v1/admin/reset")
    # re-ack after reset (peer still connected)
    http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1"})
    time.sleep(0.5)

    # --- egress unit checks ---
    st, e = http("POST", PEER + "/peer/egress_check", {"host": "10.0.0.1", "port": 443})
    assert st == 200 and e["denied"], e
    st, e = http("POST", PEER + "/peer/egress_check", {"host": "echo.local", "port": 22})
    assert st == 200 and e["denied"], e
    st, e = http("POST", PEER + "/peer/egress_check", {"host": "echo.local", "port": 443})
    assert st == 200 and not e["denied"], e

    # --- happy tunnel + grace teardown-only ---
    _, s = match_session("grace1")
    st, g = http(
        "POST",
        GATEWAY + "/gw/start",
        {
            "peer_id": "peer_demo",
            "stream_id": s["stream_id"],
            "ticket_json": s["ticket_json"],
            "label": "grace1",
            "dest_host": "echo.local",
            "dest_port": 443,
            "alpn": "stream/tunnel/1",
        },
    )
    assert st == 200, g
    st, z = http("POST", CONTROL + "/v1/sessions/grace1/force-zero")
    assert st == 200 and z["balance_state"] == "grace", z
    deadline = time.time() + 20
    exhausted = False
    while time.time() < deadline:
        st, ev = http("GET", CONTROL + "/v1/events")
        if any(
            e.get("code") == "balance_exhausted" or e.get("event") == "balance.grace_exhausted"
            for e in ev.get("events", [])
        ):
            exhausted = True
            break
        time.sleep(0.2)
    assert exhausted, "grace exhaust missing"
    st, status = http("GET", PEER + "/peer/status")
    assert status["early_cut_attempts"] == 0, status
    # stream should be closed by gateway
    streams = status.get("streams") or {}
    closed = [v for v in streams.values() if v.get("closed_by") == "gateway"]
    assert closed, status

    # --- peer egress deny on AUTH ---
    http("POST", CONTROL + "/v1/admin/reset")
    http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1"})
    time.sleep(0.6)
    _, s = match_session("egress1")
    st, g = http(
        "POST",
        GATEWAY + "/gw/start",
        {
            "peer_id": "peer_demo",
            "stream_id": s["stream_id"],
            "ticket_json": s["ticket_json"],
            "label": "egress1",
            "dest_host": "192.168.1.50",
            "dest_port": 443,
            "alpn": "stream/tunnel/1",
        },
    )
    assert st == 403, g

    # --- bad ticket ---
    st, bad = http("POST", GATEWAY + "/gw/inject_bad_ticket", {"peer_id": "peer_demo"})
    assert st == 200 and bad.get("rejected"), bad

    # --- ISP ack gate ---
    # checkbox gate: understood=false must not set ack
    st, deny = http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1", "understood": False})
    assert st == 403 and deny.get("code") == "p1_ack_gate", deny
    for needle in ("does not guarantee your ISP", "may suspend service", "I understand and want to continue"):
        blob = json.dumps(deny)
        assert needle in blob, needle
    http("POST", PEER + "/peer/ack", {"isp_ack_version": ""})
    time.sleep(1.2)
    st, status = http("GET", PEER + "/peer/status")
    assert not status.get("connected"), status
    # Gateway eventually drops on EOF peek / next write
    for _ in range(30):
        st, peers = http("GET", GATEWAY + "/gw/peers")
        if not any(p["peer_id"] == "peer_demo" for p in peers.get("peers", [])):
            break
        time.sleep(0.2)
    st, peers = http("GET", GATEWAY + "/gw/peers")
    assert not any(p["peer_id"] == "peer_demo" for p in peers.get("peers", [])), peers
    # restore
    http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1"})
    http("POST", PEER + "/peer/resume")
    for _ in range(40):
        st, peers = http("GET", GATEWAY + "/gw/peers")
        if any(p["peer_id"] == "peer_demo" for p in peers.get("peers", [])):
            break
        time.sleep(0.15)
    st, peers = http("GET", GATEWAY + "/gw/peers")
    assert any(p["peer_id"] == "peer_demo" for p in peers.get("peers", [])), peers

    # --- kill switch ---
    _, s = match_session("kill1")
    st, g = http(
        "POST",
        GATEWAY + "/gw/start",
        {
            "peer_id": "peer_demo",
            "stream_id": s["stream_id"],
            "ticket_json": s["ticket_json"],
            "label": "kill1",
            "dest_host": "echo.local",
            "dest_port": 443,
            "alpn": "stream/tunnel/1",
        },
    )
    assert st == 200, g
    time.sleep(0.3)
    st, k = http("POST", PEER + "/peer/kill")
    assert st == 200, k
    blob = json.dumps(k)
    assert "Sharing paused" in blob, k
    assert "No traffic through your connection until you turn it back on" in blob, k
    assert "Stream" not in blob and "waive all liability" not in blob, k
    time.sleep(0.5)
    st, status = http("GET", PEER + "/peer/status")
    assert not status.get("connected"), status
    for _ in range(30):
        st, peers = http("GET", GATEWAY + "/gw/peers")
        if not any(p["peer_id"] == "peer_demo" for p in peers.get("peers", [])):
            break
        # nudge gateway to notice closed sock
        http("POST", GATEWAY + "/gw/stop", {"peer_id": "peer_demo", "stream_id": s["stream_id"]})
        time.sleep(0.2)
    st, peers = http("GET", GATEWAY + "/gw/peers")
    assert not any(p["peer_id"] == "peer_demo" for p in peers.get("peers", [])), peers

    print("PEER_SMOKE_GREEN", flush=True)
    print("PEER_HARDENING_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        cleanup()
