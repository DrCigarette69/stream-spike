#!/usr/bin/env python3
"""A4.4 part 1: Peer pilot egress against a TEST-NET-2 stand-in, no internet.

Builds the stream-peer unit-test binary as the current user, then runs the
ignored `a44_netns_stand_in` test inside a throwaway netns that has only `lo`
(carrying the extra stand-in address 198.51.100.10/32 (stand-in test site, doc
"Local stand-ins")) and NO default route. The test uses an injected resolver and
the a4_local lane (TEST-NET-2 public) without needing the cargo feature.
Checks: allowlisted name -> pinned 198.51.100.10:443 + echo through the pump
(bytes counted both ways); metadata answer -> egress_resolved_non_public; other
port -> egress_not_allowlisted; Control silent -> stale -> stream closed <= 5 s
and P8 `egress_state_stale`.

`sudo -n` only for netns setup/exec. If cargo or sudo -n is unavailable: SKIP +
exit 0, unless SPIKE_IMPL=rust or SPIKE_A4=1 (then FAIL). Prints
A4.4_PEER_EGRESS_PART1_GREEN.
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUST = os.path.join(ROOT, "rust")
SITE = "198.51.100.10:443"


def fail(msg: str):
    print(f"FAIL a44 peer egress smoke: {msg}", flush=True)
    sys.exit(1)


def preflight() -> bool:
    strict = os.environ.get("SPIKE_IMPL", "").strip() == "rust" or os.environ.get("SPIKE_A4", "").strip() == "1"

    def unavailable(reason: str) -> bool:
        if strict:
            fail(reason)
        print(f"SKIP a44 peer egress smoke: {reason} (set SPIKE_A4=1 or SPIKE_IMPL=rust to make this fatal)", flush=True)
        return False

    if not shutil.which("cargo"):
        return unavailable("cargo not found")
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        return unavailable("passwordless sudo -n unavailable")
    return True


def build_test_bin() -> str:
    out = subprocess.run(
        ["cargo", "test", "--locked", "-p", "stream-peer", "--no-run", "--message-format=json"],
        cwd=RUST, capture_output=True, text=True,
    )
    if out.returncode != 0:
        fail("cargo test --no-run failed:\n" + out.stderr[-2000:])
    for line in out.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and m.get("executable") and m.get("profile", {}).get("test"):
            if m["target"]["name"] == "stream-peer":
                return m["executable"]
    fail("test executable not found")


def sh(*args, check=True, **kw):
    r = subprocess.run(list(args), capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        fail(f"{' '.join(args)}: {r.stderr.strip()}")
    return r


def main():
    if not preflight():
        return
    exe = build_test_bin()
    ns = f"pe-a44-{os.getpid()}"
    nx = ["sudo", "-n", "ip", "netns", "exec", ns]
    try:
        sh("sudo", "-n", "ip", "netns", "add", ns)
        sh(*nx, "ip", "link", "set", "lo", "up")
        # Stand-in site address on lo (no dummy module on every box); no routes added.
        sh(*nx, "ip", "addr", "add", "198.51.100.10/32", "dev", "lo")
        sh(*nx, "sysctl", "-qw", "net.ipv4.ip_unprivileged_port_start=0")
        routes = sh(*nx, "ip", "route", "show", "default").stdout + sh(*nx, "ip", "-6", "route", "show", "default").stdout
        if routes.strip():
            fail(f"netns has a default route: {routes.strip()}")
        print("OK netns has no default route", flush=True)
        user = os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(
            nx + ["sudo", "-n", "-u", user, "env", f"SPIKE_A44_SITE={SITE}", exe,
                  "pilot_egress_tests::a44_netns_stand_in", "--ignored", "--exact", "--nocapture"],
            capture_output=True, text=True, timeout=120,
        )
        out = r.stdout + r.stderr
        for l in out.splitlines():
            if l.startswith(("a4_refuse_", "peer egress_", "A44_", "UX_SCREEN", "test ")):
                print("  " + l, flush=True)
        if r.returncode != 0 or "A44_NETNS_STAND_IN_OK" not in out or "1 passed" not in out:
            fail("stand-in test failed:\n" + out[-3000:])
        print("A4.4_PEER_EGRESS_PART1_GREEN", flush=True)
    finally:
        subprocess.run(["sudo", "-n", "ip", "netns", "del", ns], capture_output=True)


if __name__ == "__main__":
    main()
