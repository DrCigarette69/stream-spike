#!/usr/bin/env python3
"""A4.3 Control egress-state test (no netns, loopback only) -> A4.3_CONTROL_EGRESS_OK.

Runs control/main.py on a free 127.0.0.1 port (several times) and checks:
- GET /v1/egress/state default OFF with allowlist_version = guard EgressAllow version;
- lane: SPIKE_A4_LANE=local accepts a 198.51.100.x (TEST-NET-2 stand-in) pin and yields the same
  version as spike_private_guard.EgressAllow.parse(raw, lane=LANE_LOCAL); without the local lane the
  same allowlist fails closed (version "", public_egress false, error egress_allowlist_config) even
  when the flag is switched on;
- POST /v1/admin/egress (+ alias POST /v1/egress/state): bool required, flag flips, updated_at moves,
  allowlist change -> new version, bad list -> 400; SPIKE_ADMIN_TOKEN enforced when set;
- unset allowlist -> fail closed.
Usage: python3 scripts/a43_control_egress_test.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import spike_private_guard as g  # noqa: E402

STANDIN = "echo.example.com:443=198.51.100.10"


def fail(m):
    print(f"FAIL a43-control: {m}", flush=True)
    raise SystemExit(1)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, url, body=None, headers=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=d, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        x = urllib.request.urlopen(r, timeout=5)
        return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class Control:
    def __init__(self, env):
        self.tmp = tempfile.mkdtemp(prefix="a43_ctl_")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        e = {k: v for k, v in os.environ.items() if not k.startswith("SPIKE_")}
        e.update({"SPIKE_DB": f"{self.tmp}/c.sqlite", "SPIKE_LISTEN": f"127.0.0.1:{self.port}",
                  "FIXTURES": str(ROOT / "fixtures"), **env})
        self.p = subprocess.Popen([sys.executable, str(ROOT / "control" / "main.py")], env=e,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if http("GET", self.url + "/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        fail("control did not start")

    def stop(self):
        self.p.terminate()
        self.p.wait(timeout=5)
        subprocess.run(["rm", "-rf", self.tmp])


def main():
    want = g.EgressAllow.parse(STANDIN, lane=g.LANE_LOCAL).version()
    try:
        g.EgressAllow.parse(STANDIN)
        fail("pilot lane accepted a TEST-NET-2 pin")
    except g.GuardError as e:
        if e.reason != "egress_allowlist_config":
            fail(e)

    c = Control({"SPIKE_A4_LANE": "local", "SPIKE_EGRESS_ALLOWLIST": STANDIN})
    try:
        st, s = http("GET", c.url + "/v1/egress/state")
        if st != 200 or s["public_egress"] is not False or s["allowlist_version"] != want or "error" in s:
            fail(f"local lane default state {st} {s}")
        if s["allowlist"] != ["echo.example.com:443=198.51.100.10"]:
            fail(f"allowlist render {s['allowlist']}")
        print(f"OK local lane: default OFF, stand-in pin accepted, allowlist_version={want[:12]}… == guard LANE_LOCAL", flush=True)
        st, r = http("POST", c.url + "/v1/admin/egress", {"public_egress": "yes"})
        if st != 400 or r.get("error") != "public_egress_bool_required":
            fail(f"non-bool {st} {r}")
        u0 = s["updated_at"]
        st, r = http("POST", c.url + "/v1/admin/egress", {"public_egress": True})
        if st != 200 or r["public_egress"] is not True or r["updated_at"] <= u0:
            fail(f"flip on {st} {r}")
        st, r = http("POST", c.url + "/v1/egress/state", {"public_egress": False})
        if st != 200 or r["public_egress"] is not False:
            fail(f"alias flip off {st} {r}")
        st, r = http("POST", c.url + "/v1/admin/egress", {"public_egress": True, "allowlist": ["10.0.0.1:443"]})
        if st != 400 or r.get("error") != "egress_allowlist_config":
            fail(f"bad allowlist {st} {r}")
        new = [STANDIN, "b.example.com:443"]
        st, r = http("POST", c.url + "/v1/admin/egress", {"public_egress": True, "allowlist": new})
        if st != 200 or r["allowlist_version"] != g.EgressAllow.parse(",".join(new), lane=g.LANE_LOCAL).version():
            fail(f"allowlist change {st} {r}")
        print("OK POST /v1/admin/egress (+alias): bool required, on/off, bad list 400, list change -> new version", flush=True)
    finally:
        c.stop()

    c = Control({"SPIKE_EGRESS_ALLOWLIST": STANDIN})  # pilot lane (no SPIKE_A4_LANE)
    try:
        http("POST", c.url + "/v1/admin/egress", {"public_egress": True})
        st, s = http("GET", c.url + "/v1/egress/state")
        if s["public_egress"] is not False or s["allowlist_version"] != "" or s.get("error") != "egress_allowlist_config":
            fail(f"pilot lane with stand-in pin must fail closed: {s}")
        print("OK pilot lane: same stand-in allowlist fails closed (version '', public_egress false, egress_allowlist_config)", flush=True)
    finally:
        c.stop()

    c = Control({})
    try:
        st, s = http("GET", c.url + "/v1/egress/state")
        if s["public_egress"] is not False or s["allowlist_version"] != "":
            fail(f"unset allowlist {s}")
        print(f"OK unset allowlist: fail closed ({s.get('error')})", flush=True)
    finally:
        c.stop()

    c = Control({"SPIKE_A4_LANE": "local", "SPIKE_EGRESS_ALLOWLIST": STANDIN, "SPIKE_ADMIN_TOKEN": "t0k"})
    try:
        st, r = http("POST", c.url + "/v1/admin/egress", {"public_egress": True})
        st2, r2 = http("POST", c.url + "/v1/admin/egress", {"public_egress": True}, {"X-Admin-Token": "t0k"})
        if st != 403 or r.get("error") != "admin_token_required" or st2 != 200:
            fail(f"admin token {st} {r} {st2} {r2}")
        print("OK SPIKE_ADMIN_TOKEN: missing token 403 admin_token_required, right token 200", flush=True)
    finally:
        c.stop()
    print("A4.3_CONTROL_EGRESS_OK", flush=True)


if __name__ == "__main__":
    main()
