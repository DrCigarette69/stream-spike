#!/usr/bin/env python3
"""Local Platform smoke (no Docker): control + gateway + mini Peer acceptor."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL = "http://127.0.0.1:8080"
GATEWAY = "http://127.0.0.1:1080"
RELAY = ("127.0.0.1", 9100)
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


def start_services():
    env = os.environ.copy()
    env["SPIKE_DB"] = str(ROOT / ".smoke.sqlite")
    env["SPIKE_LISTEN"] = "127.0.0.1:8080"
    env["SPIKE_TICKET_SECRET"] = "dev-only-change-me"
    env["CONTROL_URL"] = CONTROL
    env["SPIKE_LISTEN_PROXY"] = "127.0.0.1:1080"
    env["SPIKE_FAKE_RELAY"] = "127.0.0.1:9100"
    Path(env["SPIKE_DB"]).unlink(missing_ok=True)
    procs.append(
        subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "control/main.py")],
            cwd=str(ROOT / "control"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    )
    time.sleep(0.4)
    procs.append(
        subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "gateway/main.py")],
            cwd=str(ROOT / "gateway"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    )
    for _ in range(40):
        try:
            st, _ = http("GET", CONTROL + "/health")
            if st == 200:
                st2, _ = http("GET", GATEWAY + "/health")
                if st2 == 200:
                    return
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit("services failed to start")


def mini_peer(ack="v1", endpoint="iroh_ep_demo_001", reject_ticket=False, deny_egress_host=None):
    """Background Peer that speaks fake Relay."""
    stop = threading.Event()

    def run():
        s = socket.create_connection(RELAY, timeout=5)
        f = s.makefile("rwb")
        hello = {
            "type": "HELLO",
            "peer_id": "peer_demo",
            "endpoint_id": endpoint,
            "isp_ack_version": ack,
            "host_tier": "always_on",
        }
        f.write((json.dumps(hello) + "\n").encode())
        f.flush()
        line = f.readline().decode()
        msg = json.loads(line or "{}")
        if msg.get("type") != "HELLO_OK":
            print("peer HELLO failed", msg, flush=True)
            s.close()
            return
        while not stop.is_set():
            s.settimeout(0.5)
            try:
                line = f.readline().decode()
            except socket.timeout:
                continue
            if not line:
                break
            msg = json.loads(line)
            if msg.get("type") == "AUTH_TICKET":
                if reject_ticket or msg.get("alpn") != "stream/tunnel/1":
                    f.write((json.dumps({"type": "AUTH_REJECT", "error": "bad"}) + "\n").encode())
                    f.flush()
                    continue
                if deny_egress_host and msg.get("dest_host") == deny_egress_host:
                    f.write(
                        (
                            json.dumps(
                                {"type": "AUTH_REJECT", "error": "egress_denied", "egress_denied": True}
                            )
                            + "\n"
                        ).encode()
                    )
                    f.flush()
                    continue
                st, ver = http("POST", CONTROL + "/v1/tickets/verify", {"ticket_json": msg["ticket_json"]})
                if st != 200 or not ver.get("ok"):
                    f.write((json.dumps({"type": "AUTH_REJECT", "error": "bad_ticket"}) + "\n").encode())
                    f.flush()
                    continue
                # endpoint bind check
                ticket = json.loads(msg["ticket_json"])
                if ticket["payload"].get("peer_endpoint_id") != endpoint:
                    f.write((json.dumps({"type": "AUTH_REJECT", "error": "endpoint_mismatch"}) + "\n").encode())
                    f.flush()
                    continue
                f.write((json.dumps({"type": "AUTH_OK"}) + "\n").encode())
                f.flush()
            elif msg.get("type") in ("OPEN", "BYTES", "CLOSE"):
                pass  # teardown-only; ignore early cut
        try:
            s.close()
        except Exception:
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return stop


def cleanup(*_):
    for p in procs:
        p.send_signal(signal.SIGTERM)
    time.sleep(0.2)
    for p in procs:
        p.kill()


def main():
    start_services()
    http("POST", CONTROL + "/v1/admin/reset")
    stop_peer = mini_peer()
    time.sleep(0.3)

    # match + session
    st, m = http(
        "POST",
        CONTROL + "/v1/match",
        {"account_id": "acct_demo", "geo": {"country": "US", "city": "new_orleans"}, "rematch_mode": "city"},
    )
    assert st == 200, m
    st, s = http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": "smoke1"})
    assert st == 200, s
    ticket_json = s["ticket_json"]
    stream_id = s["stream_id"]

    st, g = http(
        "POST",
        GATEWAY + "/gw/start",
        {
            "peer_id": "peer_demo",
            "stream_id": stream_id,
            "ticket_json": ticket_json,
            "label": "smoke1",
            "dest_host": "echo.local",
            "dest_port": 443,
            "alpn": "stream/tunnel/1",
        },
    )
    assert st == 200, g

    # force grace
    st, z = http("POST", CONTROL + f"/v1/sessions/smoke1/force-zero")
    assert st == 200 and z["balance_state"] == "grace", z
    # wait for exhaust
    deadline = time.time() + 20
    exhausted = False
    while time.time() < deadline:
        st, ev = http("GET", CONTROL + "/v1/events")
        codes = [e.get("code") or e.get("event") for e in ev.get("events", [])]
        if "balance_exhausted" in codes or any(
            e.get("event") == "balance.grace_exhausted" for e in ev.get("events", [])
        ):
            exhausted = True
            break
        time.sleep(0.2)
    assert exhausted, "grace exhaust not seen"

    st, led = http("GET", CONTROL + f"/v1/ledger/{stream_id}")
    kinds = {e["kind"] for e in led.get("entries", [])}
    assert "peer_payout" in kinds, led
    st, acct = http("GET", CONTROL + "/v1/accounts/acct_demo")
    assert acct["balance_usd"] >= 0, acct

    # denylist
    st, d = http(
        "POST",
        GATEWAY + "/gw/start",
        {
            "peer_id": "peer_demo",
            "stream_id": "str_deny",
            "ticket_json": ticket_json,
            "dest_host": "foo.bank.example",
            "dest_port": 443,
            "alpn": "stream/tunnel/1",
        },
    )
    assert st == 403 and d.get("user_copy") == "blocked", d

    # strict unavailable fixture (needs ≥T2 to reach capacity gate)
    http("POST", CONTROL + "/v1/admin/reset")
    stop_peer.set()
    time.sleep(0.2)
    stop_peer = mini_peer()
    time.sleep(0.3)
    http("POST", CONTROL + "/v1/admin/accounts/acct_demo/patch", {"kyc_tier": 2})
    http("POST", CONTROL + "/v1/admin/fixtures", {"strict_unavailable": True})
    st, m = http(
        "POST",
        CONTROL + "/v1/match",
        {"account_id": "acct_demo", "geo": {"country": "US", "city": "new_orleans"}, "rematch_mode": "strict"},
    )
    assert st == 503 and m.get("code") == "strict_unavailable", m

    # freeze
    http("POST", CONTROL + "/v1/admin/fixtures", {"strict_unavailable": False})
    http("POST", CONTROL + "/v1/admin/accounts/acct_demo/freeze")
    st, m = http(
        "POST",
        CONTROL + "/v1/match",
        {"account_id": "acct_demo", "geo": {"country": "US", "city": "new_orleans"}},
    )
    assert st == 403, m

    # cashout min
    http("POST", CONTROL + "/v1/admin/reset")
    st, c = http("POST", CONTROL + "/v1/mock/cashout", {"account_id": "acct_demo", "peer_id": "peer_demo"})
    assert st == 403 and "Minimum cashout: $25" in c.get("user_copy", ""), c

    # bad ticket
    stop_peer.set()
    time.sleep(0.2)
    stop_peer = mini_peer()
    time.sleep(0.3)
    st, bad = http("POST", GATEWAY + "/gw/inject_bad_ticket", {"peer_id": "peer_demo"})
    assert st == 200 and bad.get("rejected"), bad

    print("PLATFORM_SMOKE_GREEN", flush=True)
    stop_peer.set()
    cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        cleanup()
