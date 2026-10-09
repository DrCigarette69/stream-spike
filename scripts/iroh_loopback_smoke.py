#!/usr/bin/env python3
"""A1.1 — Iroh loopback lane smoke (stubs only; no public egress; not real Iroh).

Opt-in via SPIKE_TRANSPORT=iroh_loopback. Same AUTH_TICKET/ALPN frames as fake Relay,
bound to 127.0.0.1:9101 (or SPIKE_PORT_BASE+3). Expect: A1.1_IROH_LOOPBACK_GREEN
"""
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


def wait_ok(url, want_transport=None, timeout=8.0):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            st, body = http("GET", url)
            if st == 200 and body.get("ok"):
                if want_transport and body.get("transport") != want_transport:
                    last = body
                    time.sleep(0.1)
                    continue
                return body
            last = body
        except Exception as e:
            last = {"error": str(e)}
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting {url}: {last}")


def start():
    env = spike_ports.apply(os.environ.copy())
    db = spike_ports.db_file(ROOT, ".a11.sqlite", env)
    db.unlink(missing_ok=True)
    env.update(
        {
            "SPIKE_DB": str(db),
            "SPIKE_TICKET_SECRET": "dev-only-change-me",
            "SPIKE_DENYLIST": str(ROOT / "fixtures/denylist.seed.json"),
            "SPIKE_TRANSPORT": "iroh_loopback",
            "SPIKE_PEER_ID": "peer_demo",
            "SPIKE_ENDPOINT_ID": "iroh_ep_demo_001",
            "SPIKE_ISP_ACK_VERSION": "v1",
            "SPIKE_HOST_TIER": "casual",
        }
    )
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


def main():
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    try:
        chk2 = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os,importlib.util\n"
                    "os.environ['SPIKE_TRANSPORT']='iroh_loopback'\n"
                    "os.environ['SPIKE_IROH_LOOPBACK']='0.0.0.0:9101'\n"
                    "os.environ['CONTROL_URL']='http://127.0.0.1:9'\n"
                    "spec=importlib.util.spec_from_file_location('gw','gateway/main.py')\n"
                    "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                    "host,_=m._split(m.RELAY_LISTEN)\n"
                    "assert not m._is_loopback_host(host), host\n"
                    "print('A1.1_BIND_GUARD_OK')\n"
                ),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "A1.1_BIND_GUARD_OK" not in chk2.stdout:
            print(chk2.stdout, chk2.stderr)
            raise SystemExit("bind guard failed")

        start()
        g = wait_ok(GATEWAY + "/health", want_transport="iroh_loopback")
        wait_ok(CONTROL + "/health")
        p = None
        for _ in range(50):
            st, body = http("GET", PEER + "/health")
            if st == 200 and body.get("connected") and body.get("transport") == "iroh_loopback":
                p = body
                break
            time.sleep(0.15)
        if not p:
            raise SystemExit("peer not connected on iroh_loopback")

        http("POST", CONTROL + "/v1/admin/reset")
        st, m = http(
            "POST",
            CONTROL + "/v1/match",
            {
                "account_id": "acct_demo",
                "geo": {"country": "US", "city": "new_orleans"},
                "rematch_mode": "city",
            },
        )
        if st != 200:
            raise SystemExit(f"match failed {st} {m}")
        st, sess = http(
            "POST",
            CONTROL + "/v1/sessions",
            {"quote_id": m["quote_id"], "label": "a11_loop"},
        )
        if st != 200:
            raise SystemExit(f"session failed {st} {sess}")
        stream_id = sess.get("stream_id") or sess.get("label") or "a11_loop"
        ticket = sess.get("ticket_json")
        if not ticket:
            raise SystemExit(f"no ticket in session {sess}")
        peer_id = sess.get("peer_id") or "peer_demo"
        st, gw = http(
            "POST",
            GATEWAY + "/gw/start",
            {
                "peer_id": peer_id,
                "stream_id": stream_id,
                "ticket_json": ticket,
                "label": sess.get("label", "a11_loop"),
                "dest_host": "echo.local",
                "dest_port": 443,
                "alpn": "stream/tunnel/1",
            },
        )
        if st != 200 or not gw.get("started"):
            raise SystemExit(f"gw/start failed {st} {gw}")

        time.sleep(0.5)
        st, p2 = http("GET", PEER + "/health")
        streams = p2.get("streams") or {}
        if not streams:
            raise SystemExit(f"no peer streams after start: {p2}")

        print(
            json.dumps(
                {
                    "transport": g.get("transport"),
                    "relay_listen": g.get("relay_listen"),
                    "peer_transport": p.get("transport"),
                    "streams": list(streams.keys()),
                    "alpn": "stream/tunnel/1",
                }
            ),
            flush=True,
        )
        print("A1.1_IROH_LOOPBACK_GREEN", flush=True)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
