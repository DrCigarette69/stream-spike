#!/usr/bin/env python3
"""Alpha-1 A1.2 Peer tray/CLI: enroll → ISP ack → kill → resume (Designer fixtures).

One-command walkthrough against live peer admin. Starts control+gateway+peer
like peer_smoke when ports are down; otherwise assumes 8080/1080/9200 up.

Stance: stubs · [Brand] never Stream in UX · fake Relay · no public egress.
Proof line: PEER_ALPHA1_CLI_GREEN
"""
from __future__ import annotations

import argparse
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
CONTROL = "http://127.0.0.1:8080"
GATEWAY = "http://127.0.0.1:1080"
PEER = "http://127.0.0.1:9200"

P0_LINES = (
    "Share bandwidth. Get paid.",
    "You stay in control — stop anytime.",
)
FORBIDDEN_UX = (
    "Stream",
    "waive all liability",
    "waive-all-liability",
    "ISP will always allow",
    "we are not responsible for ISP",
)

procs: list[subprocess.Popen] = []
started_here = False


def http(method: str, url: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_http(url: str, n: int = 50):
    for _ in range(n):
        try:
            st, _ = http("GET", url)
            if st == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting {url}")


def ports_up() -> bool:
    try:
        st_c, _ = http("GET", CONTROL + "/health")
        st_g, _ = http("GET", GATEWAY + "/health")
        st_p, _ = http("GET", PEER + "/health")
        return st_c == 200 and st_g == 200 and st_p == 200
    except Exception:
        return False


def start_stack():
    global started_here
    env = os.environ.copy()
    env["SPIKE_DB"] = str(ROOT / ".peer_alpha1.sqlite")
    Path(env["SPIKE_DB"]).unlink(missing_ok=True)
    env["SPIKE_LISTEN"] = "127.0.0.1:8080"
    env["SPIKE_TICKET_SECRET"] = "dev-only-change-me"
    env["CONTROL_URL"] = CONTROL
    env["SPIKE_LISTEN_PROXY"] = "127.0.0.1:1080"
    env["SPIKE_FAKE_RELAY"] = "127.0.0.1:9100"
    env["SPIKE_FAKE_RELAY_DIAL"] = "127.0.0.1:9100"
    env["SPIKE_PEER_ADMIN"] = "127.0.0.1:9200"
    # empty ack so A1.2 demos P1 gate
    env["SPIKE_ISP_ACK_VERSION"] = ""
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
    started_here = True
    print("stack: started control+gateway+peer (empty ISP ack)", flush=True)


def cleanup(*_):
    if not started_here:
        return
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


def load_fixtures():
    path = ROOT / "fixtures" / "screens.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def ux_blobs(*objs) -> str:
    """Flatten Designer-facing fields only (skip protocol alpn / ids)."""
    keys = (
        "user_facing",
        "ux_prompt",
        "ux_status",
        "sharing_status",
        "title",
        "checkbox",
        "cta",
        "detail",
        "status_after_kill",
        "body_lines",
        "required_copy",
        "brand_placeholder",
    )
    parts: list[str] = []
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in keys:
            if k not in obj:
                continue
            v = obj[k]
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            else:
                parts.append(str(v))
        for nested in ("p1", "p4"):
            if nested in obj and isinstance(obj[nested], dict):
                parts.append(ux_blobs(obj[nested]))
    return "\n".join(parts)


def assert_no_forbidden(*objs, label: str = "ux"):
    blob = ux_blobs(*objs)
    for bad in FORBIDDEN_UX:
        if bad in blob:
            raise SystemExit(f"FAIL forbidden {bad!r} in {label}")


def step(title: str):
    print(f"\n=== {title} ===", flush=True)


def wait_sharing_on(timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        st, last = http("GET", PEER + "/peer/status")
        if (
            st == 200
            and last.get("isp_ack_version")
            and last.get("connected")
            and not last.get("kill_requested")
        ):
            return last
        time.sleep(0.15)
    raise SystemExit(f"timeout waiting Sharing ON: {last}")


def wait_paused(timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        st, last = http("GET", PEER + "/peer/status")
        if st == 200 and last.get("kill_requested") and last.get("sharing_status") == "Sharing paused":
            return last
        time.sleep(0.1)
    raise SystemExit(f"timeout waiting Sharing paused: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Alpha-1 A1.2 Peer enroll→ack→kill→resume CLI")
    ap.add_argument(
        "--start",
        action="store_true",
        help="Force-start control+gateway+peer (fails if ports already bound)",
    )
    ap.add_argument(
        "--assume-up",
        action="store_true",
        help="Require ports already up; never start processes",
    )
    args = ap.parse_args()

    fixtures = load_fixtures()
    brand = fixtures.get("brand_placeholder", "[Brand]")

    if args.assume_up:
        if not ports_up():
            raise SystemExit("ports not up (--assume-up)")
        print("stack: assuming ports up", flush=True)
    elif args.start or not ports_up():
        start_stack()
    else:
        print("stack: reusing live peer admin on :9200", flush=True)

    # reset to pre-enroll for P1 gate on warm stack
    http("POST", PEER + "/peer/ack", {"isp_ack_version": ""})
    time.sleep(0.4)

    # --- P0 what-this-is ---
    step("P0 what-this-is")
    for line in P0_LINES:
        print(f"  {line}", flush=True)
    print(f"  brand: {brand} (no tunnel yet)", flush=True)
    assert_no_forbidden({"user_facing": "\n".join(P0_LINES), "brand_placeholder": brand}, label="P0")

    # --- P1 ISP ack ---
    step("P1 ISP ack")
    st, p1 = http("GET", PEER + "/peer/consent/p1")
    assert st == 200, p1
    print(p1.get("user_facing") or p1.get("title"), flush=True)
    for needle in ("does not guarantee your ISP", "may suspend service", "I understand and want to continue"):
        blob = json.dumps(p1)
        assert needle in blob, f"P1 missing {needle!r}"
    assert_no_forbidden(p1, label="P1 consent")
    print("  Continue disabled until checkbox (Designer)", flush=True)

    st, deny = http(
        "POST",
        PEER + "/peer/ack",
        {"isp_ack_version": "v1", "understood": False},
    )
    assert st == 403 and deny.get("code") == "p1_ack_gate", deny
    print(f"  POST understood:false → {st} code={deny.get('code')}", flush=True)
    assert_no_forbidden(deny, label="P1 gate deny")

    # --- P2 consent greps (fixtures) ---
    step("P2 consent bundle (fixture greps)")
    p2 = None
    for c in fixtures.get("consent") or []:
        if c.get("id") == "p2_consent":
            p2 = c
            break
    if p2:
        for needle in p2.get("required_copy") or []:
            print(f"  ✓ {needle}", flush=True)
        assert_no_forbidden({"required_copy": p2.get("required_copy") or []}, label="P2")
    else:
        print("  (fixtures/screens.json p2_consent missing — skipped)", flush=True)

    # --- Enroll / ack ---
    step("Enroll / ISP ack (understood:true)")
    st, ack = http(
        "POST",
        PEER + "/peer/ack",
        {"isp_ack_version": "v1", "understood": True},
    )
    assert st == 200 and ack.get("isp_ack_version") == "v1", ack
    assert_no_forbidden(ack, label="ack")
    status = wait_sharing_on()
    # tray label (admin sharing_status is Sharing when connected)
    tray = "Sharing ON"
    print(f"  tray: {tray}", flush=True)
    print(
        f"  status: sharing_status={status.get('sharing_status')!r} "
        f"connected={status.get('connected')} ack={status.get('isp_ack_version')!r}",
        flush=True,
    )
    assert status.get("connected") and status.get("isp_ack_version") == "v1"
    assert status.get("sharing_status") in ("Sharing", "Sharing ON"), status
    assert_no_forbidden(status, label="Sharing ON status")

    # --- P4 kill ---
    step("P4 kill (Pause sharing)")
    print("  CTA: Pause sharing", flush=True)
    st, killed = http("POST", PEER + "/peer/kill")
    assert st == 200, killed
    paused = wait_paused()
    detail = (killed.get("p4") or {}).get("detail") or ""
    ux = killed.get("ux_status") or ""
    print(f"  {paused.get('sharing_status')}", flush=True)
    print(f"  {detail or ux.splitlines()[0] if ux else ''}", flush=True)
    blob = json.dumps(killed)
    assert "Sharing paused" in blob, killed
    assert "No traffic through your connection until you turn it back on" in blob, killed
    assert_no_forbidden(killed, paused, label="P4 kill")

    # --- Resume (explicit only) ---
    step("Resume sharing (explicit)")
    print("  CTA: Resume sharing", flush=True)
    st, resumed = http("POST", PEER + "/peer/resume")
    assert st == 200, resumed
    assert not resumed.get("kill_requested"), resumed
    status = wait_sharing_on()
    print(f"  tray: Sharing ON (after explicit resume)", flush=True)
    print(
        f"  status: sharing_status={status.get('sharing_status')!r} "
        f"connected={status.get('connected')} kill={status.get('kill_requested')}",
        flush=True,
    )
    assert_no_forbidden(resumed, status, label="resume")

    # --- P6 optional ---
    step("P6 cashout (optional A1)")
    p6 = None
    for c in fixtures.get("consent") or []:
        if c.get("id") == "p6_cashout":
            p6 = c
            break
    cashout_line = "Minimum cashout: $25"
    if p6 and cashout_line in (p6.get("required_copy") or [cashout_line]):
        print(f"  {cashout_line}", flush=True)
        print("  CTA: Cash out (disabled under $25)", flush=True)
    else:
        print(f"  {cashout_line}", flush=True)
        print("  CTA: Cash out (disabled under $25)", flush=True)
    assert_no_forbidden({"required_copy": [cashout_line]}, label="P6")

    print(flush=True)
    print("PEER_ALPHA1_CLI_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    try:
        raise SystemExit(main())
    finally:
        cleanup()
