#!/usr/bin/env python3
"""Architect client-cli — drive spike DoD scenarios against local stubs (no Docker)."""
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
FIXTURES = Path(os.environ.get("FIXTURES", str(ROOT / "fixtures")))
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8080")
GATEWAY = os.environ.get("GATEWAY_PROXY", "http://127.0.0.1:1080")
if not GATEWAY.startswith("http"):
    GATEWAY = "http://" + GATEWAY
PEER = os.environ.get("PEER_ADMIN", "http://127.0.0.1:9200")

procs: list[subprocess.Popen] = []
_managed = False


def load_screens():
    return json.loads((FIXTURES / "screens.json").read_text())


def http(method, url, body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def wait_http(url, n=60):
    for _ in range(n):
        try:
            st, _ = http("GET", url)
            if st == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting {url}")


def cleanup(*_):
    global procs
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
    time.sleep(0.25)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    procs = []


def ensure_stack():
    """Start control+gateway+peer if not already healthy."""
    global _managed
    try:
        st, _ = http("GET", CONTROL + "/health")
        st2, _ = http("GET", GATEWAY + "/health")
        st3, _ = http("GET", PEER + "/health")
        if st == 200 and st2 == 200 and st3 == 200:
            return False
    except Exception:
        pass

    env = os.environ.copy()
    db = ROOT / ".cli.sqlite"
    db.unlink(missing_ok=True)
    env.update(
        {
            "SPIKE_DB": str(db),
            "SPIKE_LISTEN": "127.0.0.1:8080",
            "SPIKE_TICKET_SECRET": "dev-only-change-me",
            "CONTROL_URL": CONTROL,
            "SPIKE_LISTEN_PROXY": "127.0.0.1:1080",
            "SPIKE_FAKE_RELAY": "127.0.0.1:9100",
            "SPIKE_FAKE_RELAY_DIAL": "127.0.0.1:9100",
            "SPIKE_PEER_ADMIN": "127.0.0.1:9200",
            "SPIKE_ISP_ACK_VERSION": "v1",
            "SPIKE_HOST_TIER": "always_on",
            "SPIKE_PEER_ID": "peer_demo",
            "SPIKE_ENDPOINT_ID": "iroh_ep_demo_001",
            "SPIKE_HEARTBEAT_S": "2",
        }
    )
    for cwd in (ROOT / "control", ROOT / "gateway", ROOT / "peer"):
        procs.append(
            subprocess.Popen(
                [sys.executable, "-u", str(cwd / "main.py")],
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
    for _ in range(50):
        st, g = http("GET", GATEWAY + "/gw/peers")
        if st == 200 and any(p.get("peer_id") == "peer_demo" for p in g.get("peers", [])):
            _managed = True
            return True
        time.sleep(0.1)
    raise SystemExit("peer never appeared on gateway")


def reset():
    http("POST", CONTROL + "/v1/admin/reset")
    http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1"})
    http("POST", PEER + "/peer/resume")
    for _ in range(50):
        st, g = http("GET", GATEWAY + "/gw/peers")
        if st == 200 and any(p.get("peer_id") == "peer_demo" for p in g.get("peers", [])):
            return
        time.sleep(0.1)
    # peer may need a moment after resume
    time.sleep(0.5)


def forbid_brand(text: str):
    screens = load_screens()
    for bad in screens.get("forbidden_user_facing_substrings", []):
        if bad in text:
            raise AssertionError(f"forbidden user-facing substring {bad!r} in {text!r}")


def emit_screen(screen_id: str, codes_seen: list[str]):
    """Print Designer-facing copy for harness observation (no Stream brand)."""
    screens = load_screens()
    for s in screens["screens"]:
        if s["id"] == screen_id:
            print(f"SCREEN {s['number']} id={s['id']} code={s['code']}", flush=True)
            for line in s.get("required_copy", []):
                forbid_brand(line)
                print(f"  COPY: {line}", flush=True)
            if s["code"] not in codes_seen and s.get("event") not in codes_seen:
                # still print — caller asserts events separately
                pass
            return s
    raise AssertionError(f"unknown screen {screen_id}")


def match_session(label: str, rematch="city", account="acct_demo"):
    st, m = http(
        "POST",
        CONTROL + "/v1/match",
        {
            "account_id": account,
            "geo": {"country": "US", "city": "new_orleans"},
            "rematch_mode": rematch,
        },
    )
    return st, m


def start_tunnel(session: dict, label: str, dest_host="echo.local", dest_port=443):
    return http(
        "POST",
        GATEWAY + "/gw/start",
        {
            "peer_id": "peer_demo",
            "stream_id": session["stream_id"],
            "ticket_json": session["ticket_json"],
            "label": label,
            "dest_host": dest_host,
            "dest_port": dest_port,
            "alpn": "stream/tunnel/1",
        },
    )


def wait_events(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, ev = http("GET", CONTROL + "/v1/events")
        events = ev.get("events", [])
        if pred(events):
            return events
        time.sleep(0.2)
    raise AssertionError("event wait timeout")


def cmd_grace_stop():
    reset()
    st, m = match_session("grace_cli")
    assert st == 200, m
    st, s = http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": "grace_cli"})
    assert st == 200, s
    st, g = start_tunnel(s, "grace_cli")
    assert st == 200, g

    st, z = http("POST", CONTROL + "/v1/sessions/grace_cli/force-zero")
    assert st == 200 and z.get("balance_state") == "grace", z
    emit_screen("screen_1_grace", ["balance_grace"])

    events = wait_events(
        lambda evs: any(
            e.get("code") == "balance_exhausted" or e.get("event") == "balance.grace_exhausted" for e in evs
        )
    )
    emit_screen("screen_2_exhausted", ["balance_exhausted"])
    codes = [e.get("code") for e in events if e.get("code")]
    assert "balance_grace" in codes or any(e.get("event") == "balance.grace_enter" for e in events)
    assert "balance_exhausted" in codes or any(e.get("event") == "balance.grace_exhausted" for e in events)

    st, status = http("GET", PEER + "/peer/status")
    assert status.get("early_cut_attempts", 0) == 0, status
    print("OK grace-stop", flush=True)
    return 0


def cmd_assert_grace_ledger():
    # Uses last grace_cli stream if present; else runs a short grace path
    st, sess = http("GET", CONTROL + "/v1/sessions/grace_cli")
    if st != 200 or not sess.get("stream_id"):
        cmd_grace_stop()
        st, sess = http("GET", CONTROL + "/v1/sessions/grace_cli")
    stream_id = sess["stream_id"]
    st, led = http("GET", CONTROL + f"/v1/ledger/{stream_id}")
    assert st == 200, led
    kinds = {e["kind"] for e in led.get("entries", [])}
    assert "peer_payout" in kinds, led
    st, acct = http("GET", CONTROL + "/v1/accounts/acct_demo")
    assert acct.get("balance_usd", -1) >= 0, acct
    print("OK assert-grace-ledger", kinds, flush=True)
    return 0


def cmd_gate_denylist():
    reset()
    st, m = match_session("deny1")
    assert st == 200, m
    st, s = http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": "deny1"})
    assert st == 200, s
    st, g = start_tunnel(s, "deny1", dest_host="foo.bank.example", dest_port=443)
    assert st == 403 and g.get("user_copy") == "blocked", g
    forbid_brand(g.get("user_copy", ""))
    print("OK gate-denylist (C3 blocked copy)", flush=True)
    return 0


def cmd_gate_freeze():
    reset()
    http("POST", CONTROL + "/v1/admin/accounts/acct_demo/freeze")
    st, m = match_session("frozen")
    assert st == 403, m
    print("OK gate-freeze", flush=True)
    return 0


def cmd_gate_peer_ack():
    reset()
    http("POST", PEER + "/peer/ack", {"isp_ack_version": ""})
    time.sleep(1.2)
    st, status = http("GET", PEER + "/peer/status")
    assert not status.get("connected"), status
    # Designer fixture contract — p1_isp_ack required_copy must appear on Peer UX
    screens = load_screens()
    p1 = next(c for c in screens.get("consent", []) if c.get("id") == "p1_isp_ack")
    blob = " ".join(
        [
            status.get("ux_prompt") or "",
            status.get("ux_status") or "",
            json.dumps(status.get("p1") or {}),
        ]
    )
    for line in p1.get("required_copy", []):
        forbid_brand(line)
        assert line in blob, (line, blob[:200])
        print(f"  COPY: {line}", flush=True)
    # understood:false must refuse ack
    st, denied = http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1", "understood": False})
    assert st == 403 and denied.get("code") == "p1_ack_gate", denied
    for _ in range(40):
        st, peers = http("GET", GATEWAY + "/gw/peers")
        if not any(p.get("peer_id") == "peer_demo" for p in peers.get("peers", [])):
            break
        time.sleep(0.15)
    st, peers = http("GET", GATEWAY + "/gw/peers")
    assert not any(p.get("peer_id") == "peer_demo" for p in peers.get("peers", [])), peers
    # restore for later gates
    http("POST", PEER + "/peer/ack", {"isp_ack_version": "v1", "understood": True})
    http("POST", PEER + "/peer/resume")
    time.sleep(0.8)
    print("OK gate-peer-ack (P1)", flush=True)
    return 0


def cmd_gate_peer_egress():
    reset()
    st, e = http("POST", PEER + "/peer/egress_check", {"host": "10.0.0.1", "port": 443})
    assert st == 200 and e.get("denied"), e
    st, m = match_session("egress1")
    assert st == 200, m
    st, s = http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": "egress1"})
    assert st == 200, s
    st, g = start_tunnel(s, "egress1", dest_host="192.168.1.50", dest_port=443)
    assert st == 403, g
    print("OK gate-peer-egress", flush=True)
    return 0


def cmd_gate_peer_kill():
    reset()
    st, m = match_session("kill1")
    assert st == 200, m
    st, s = http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": "kill1"})
    assert st == 200, s
    st, g = start_tunnel(s, "kill1")
    assert st == 200, g
    time.sleep(0.3)
    st, k = http("POST", PEER + "/peer/kill")
    assert st == 200, k
    screens = load_screens()
    time.sleep(0.5)
    st, status = http("GET", PEER + "/peer/status")
    assert not status.get("connected"), status
    blob = " ".join(
        [
            status.get("ux_status") or "",
            status.get("sharing_status") or "",
            json.dumps(status.get("p4") or {}),
            json.dumps(k),
        ]
    )
    for c in screens.get("consent", []):
        if c["id"] == "p4_kill":
            for line in c.get("required_copy", []):
                forbid_brand(line)
                assert line in blob, (line, blob[:240])
                print(f"  COPY: {line}", flush=True)
    print("OK gate-peer-kill (P4)", flush=True)
    return 0


def cmd_gate_auth_ticket():
    reset()
    st, bad = http("POST", GATEWAY + "/gw/inject_bad_ticket", {"peer_id": "peer_demo"})
    assert st == 200 and bad.get("rejected"), bad
    print("OK gate-auth-ticket", flush=True)
    return 0


def cmd_gate_strict_unavailable():
    reset()
    http("POST", CONTROL + "/v1/admin/accounts/acct_demo/patch", {"kyc_tier": 2})
    http("POST", CONTROL + "/v1/admin/fixtures", {"strict_unavailable": True})
    st, m = match_session("strict1", rematch="strict")
    assert st == 503 and m.get("code") == "strict_unavailable", m
    emit_screen("screen_3_strict_unavailable", ["strict_unavailable"])
    http("POST", CONTROL + "/v1/admin/fixtures", {"strict_unavailable": False})
    print("OK gate-strict-unavailable (Screen 3)", flush=True)
    return 0


def cmd_gate_aup():
    reset()
    http("POST", CONTROL + "/v1/admin/accounts/acct_demo/patch", {"aup_accepted": False})
    st, m = match_session("aup")
    assert st in (403, 400, 401) or (st != 200), m
    http("POST", CONTROL + "/v1/admin/accounts/acct_demo/patch", {"aup_accepted": True})
    print("OK gate-aup (C0)", flush=True)
    return 0


def cmd_gate_cashout():
    reset()
    st, c = http("POST", CONTROL + "/v1/mock/cashout", {"account_id": "acct_demo", "peer_id": "peer_demo"})
    assert st == 403, c
    copy = c.get("user_copy", "")
    assert "Minimum cashout: $25" in copy, c
    forbid_brand(copy)
    print("OK gate-cashout (P6)", flush=True)
    return 0


from a13_topup import bind as _a13_bind, cmd_mock_topup
_a13_bind(
    http=http,
    match_session=match_session,
    start_tunnel=start_tunnel,
    wait_events=wait_events,
    emit_screen=emit_screen,
    load_screens=load_screens,
    forbid_brand=forbid_brand,
    reset=reset,
    CONTROL_URL=CONTROL,
    json_mod=json,
)


COMMANDS = {
    "grace-stop": cmd_grace_stop,
    "assert-grace-ledger": cmd_assert_grace_ledger,
    "gate-denylist": cmd_gate_denylist,
    "gate-freeze": cmd_gate_freeze,
    "gate-peer-ack": cmd_gate_peer_ack,
    "gate-peer-egress": cmd_gate_peer_egress,
    "gate-peer-kill": cmd_gate_peer_kill,
    "gate-auth-ticket": cmd_gate_auth_ticket,
    "gate-strict-unavailable": cmd_gate_strict_unavailable,
    "gate-aup": cmd_gate_aup,
    "gate-cashout": cmd_gate_cashout,
    "mock-topup": cmd_mock_topup,
    "a13-mock-topup": cmd_mock_topup,
}


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print("usage: client-cli <%s>" % "|".join(sorted(COMMANDS)), flush=True)
        return 2
    ensure_stack()
    try:
        return COMMANDS[argv[1]]()
    finally:
        if _managed and os.environ.get("CLI_KEEP_UP") != "1":
            # leave stack up across sequential script calls if CLI_KEEP_UP=1
            pass


if __name__ == "__main__":
    # Keep stack across multiple invocations when parent sets CLI_KEEP_UP and started us;
    # for standalone, we start stack but do not kill (smoke parent cleans). Architect scripts
    # use run_local_asserts.py as parent cleaner.
    try:
        raise SystemExit(main(sys.argv))
    except AssertionError as e:
        print("ASSERT:", e, flush=True)
        raise SystemExit(1)
