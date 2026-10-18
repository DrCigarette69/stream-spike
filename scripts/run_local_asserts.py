#!/usr/bin/env python3
"""Run spike DoD asserts locally (no Docker). Starts stack once, runs client-cli commands."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spike_ports  # noqa: E402  SPIKE_PORT_BASE + explicit-env-wins resolution
CLI = ROOT / "client-cli" / "main.py"
procs: list[subprocess.Popen] = []
# Last output lines per stack child (stdout+stderr merged), drained by a thread so the pipe never fills.
proc_tails: dict[int, "collections.deque[str]"] = {}

DEFAULT_HEALTH_TIMEOUT_S = 30.0
HEALTH_POLL_S = 0.2
TAIL_LINES = 20


def health_timeout_s():
    """SPIKE_HEALTH_TIMEOUT_S (seconds, > 0); unset/invalid -> 30."""
    try:
        v = float(os.environ.get("SPIKE_HEALTH_TIMEOUT_S", "").strip())
        return v if v > 0 else DEFAULT_HEALTH_TIMEOUT_S
    except ValueError:
        return DEFAULT_HEALTH_TIMEOUT_S


def _drain(p):
    import collections
    import threading

    tail = collections.deque(maxlen=200)
    proc_tails[p.pid] = tail

    def run():
        for line in iter(p.stdout.readline, b""):
            tail.append(line.decode("utf-8", "replace").rstrip("\n"))

    threading.Thread(target=run, daemon=True).start()


def _proc_state(name, p, tail_lines=None):
    """`name: exited rc=N` / `name: still running (pid N)` + last TAIL_LINES output lines."""
    rc = p.poll()
    state = f"{name}: exited rc={rc}" if rc is not None else f"{name}: still running (pid {p.pid})"
    if rc is not None:
        time.sleep(0.2)  # let the drain thread pick up the final lines
    lines = list(tail_lines if tail_lines is not None else proc_tails.get(p.pid, ()))[-TAIL_LINES:]
    body = "\n".join(f"  | {l}" for l in lines) if lines else "  | <no output>"
    return f"{state}; last {len(lines)} output lines:\n{body}"


def wait_health(url, p, name, timeout=None):
    """Poll url every HEALTH_POLL_S until 200 or timeout (SPIKE_HEALTH_TIMEOUT_S, default 30 s).
    Fails fast if the child exits. FAIL message has exit status + the child's last output lines."""
    timeout = health_timeout_s() if timeout is None else timeout
    deadline = time.monotonic() + timeout
    while True:
        if http_ok(url):
            return
        if p.poll() is not None or time.monotonic() >= deadline:
            why = "process exited" if p.poll() is not None else f"no 200 within {timeout:g}s"
            raise SystemExit(f"FAIL failed to start {url} ({why}; SPIKE_HEALTH_TIMEOUT_S={timeout:g})\n"
                             + _proc_state(name, p))
        time.sleep(HEALTH_POLL_S)


def http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def cleanup(*_):
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
    time.sleep(0.3)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


# Hermeticity: the box may export real Stripe TEST credentials. Children (Control, smokes) inherit
# os.environ, so scrub them in every mode; only `a46` with SPIKE_A46_STRIPE_LIVE=1 keeps them
# (opt-in to calling Stripe's test API from the box). Values are never printed.
STRIPE_SECRET_ENVS = ("STRIPE_TEST_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
A46_MODES = ("a46", "stripe-test-topup", "stripe_test_topup")


def scrub_stripe_env(mode):
    """Remove STRIPE_SECRET_ENVS from os.environ unless mode is a46 and SPIKE_A46_STRIPE_LIVE=1.
    Returns True if the env was kept (live opt-in)."""
    if mode in A46_MODES and os.environ.get("SPIKE_A46_STRIPE_LIVE", "").strip() == "1":
        return True
    gone = [k for k in STRIPE_SECRET_ENVS if os.environ.pop(k, None) is not None]
    if gone:
        print(f"stripe env: scrubbed {', '.join(gone)} for this run (Control stays in mock top-up mode)", flush=True)
    return False


def assert_control_mock_topup(control):
    """`all`: prove Control started without a Stripe key (POST /v1/topup/test -> 409 stripe_test_unavailable)."""
    req = urllib.request.Request(control + "/v1/topup/test", data=b'{"account_id":"acct_demo"}',
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            st, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        st, body = e.code, e.read()
    try:
        code = json.loads(body or b"{}").get("code")
    except ValueError:
        code = None
    if st != 409 or code != "stripe_test_unavailable":
        raise SystemExit(f"FAIL control stripe mode: /v1/topup/test -> {st} {code} (want 409 stripe_test_unavailable)")
    print("OK control stripe mode: mock (/v1/topup/test -> 409 stripe_test_unavailable)", flush=True)


# Docker: only ever the local unix socket. A non-unix DOCKER_HOST (e.g. tcp://...:2375, an
# unauthenticated engine) is a hard FAIL, never a fallback; children get DOCKER_HOST pinned to the
# local socket and no DOCKER_CONTEXT. The sudo'd docker (smokes run `sudo env ... docker`) has its
# env reset by sudo, so its context endpoint is checked too (`docker context inspect` reads local
# config only; it doesn't contact any engine).
LOCAL_DOCKER_HOST = "unix:///var/run/docker.sock"


def docker_child_env(tag):
    """FAIL unless Docker resolves to a unix socket for us and for root; return the child env."""
    dh = os.environ.get("DOCKER_HOST", "").strip()
    if dh and not dh.startswith("unix://"):
        raise SystemExit(f"FAIL {tag}: DOCKER_HOST={dh} is not a unix:// socket; refusing to use a remote/TCP "
                         f"Docker engine (unset DOCKER_HOST or point it at {LOCAL_DOCKER_HOST})")
    ctx = os.environ.get("DOCKER_CONTEXT", "").strip()
    if ctx and ctx != "default":
        raise SystemExit(f"FAIL {tag}: DOCKER_CONTEXT={ctx} set; only the default local unix socket is allowed")
    for who, argv in (("user", ["docker"]), ("root", ["sudo", "-n", "docker"])):
        try:
            r = subprocess.run([*argv, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
                               env={**os.environ, "DOCKER_HOST": dh or LOCAL_DOCKER_HOST} if who == "user" else None)
            ep = r.stdout.strip()
        except Exception as e:
            raise SystemExit(f"FAIL {tag}: could not read the {who} docker context ({e})")
        if r.returncode != 0 or not ep.startswith("unix://"):
            raise SystemExit(f"FAIL {tag}: {who} docker endpoint is {ep or '<unknown>'!r}, not a unix:// socket; "
                             "refusing to use a remote/TCP Docker engine")
    env = {k: v for k, v in os.environ.items() if k != "DOCKER_CONTEXT"}
    env["DOCKER_HOST"] = dh or LOCAL_DOCKER_HOST
    print(f"{tag}: docker endpoint {env['DOCKER_HOST']} (user + root contexts unix://)", flush=True)
    return env


def start_stack():
    env = spike_ports.apply(os.environ.copy())  # ports/URLs: setdefault, never overwrite
    db = spike_ports.db_file(ROOT, ".dod.sqlite", env)
    db.unlink(missing_ok=True)
    env.update(
        {
            "SPIKE_DB": str(db),
            "SPIKE_TICKET_SECRET": "dev-only-change-me",
            "SPIKE_ISP_ACK_VERSION": "v1",
            "SPIKE_HOST_TIER": "always_on",
            "SPIKE_PEER_ID": "peer_demo",
            "SPIKE_ENDPOINT_ID": "iroh_ep_demo_001",
            "SPIKE_HEARTBEAT_S": "2",
            "CLI_KEEP_UP": "1",
            "FIXTURES": str(ROOT / "fixtures"),
        }
    )
    # Control always Python; gateway+peer honor SPIKE_IMPL (python|rust).
    from spike_peer_launch import control_cmd, gateway_cmd, peer_cmd

    for argv, cwd in (control_cmd(env), gateway_cmd(env), peer_cmd(env)):
        child = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        procs.append(child)
        _drain(child)
        time.sleep(0.4)
    control, gateway, peer = spike_ports.urls(env)
    pc, pg, pp = procs[-3:]
    print(f"stack: control={control} gateway={gateway} peer={peer}", flush=True)
    for url, child, name in ((control + "/health", pc, "control"), (gateway + "/health", pg, "gateway"),
                             (peer + "/health", pp, "peer")):
        wait_health(url, child, name)
    # wait peer online (same timeout)
    timeout = health_timeout_s()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(gateway + "/gw/peers", timeout=2) as r:
                peers = json.loads(r.read().decode()).get("peers", [])
                if any(p.get("peer_id") == "peer_demo" for p in peers):
                    return env
        except Exception:
            pass
        if pp.poll() is not None or pg.poll() is not None:
            break
        time.sleep(HEALTH_POLL_S)
    raise SystemExit(f"FAIL peer not on gateway within {timeout:g}s (SPIKE_HEALTH_TIMEOUT_S)\n"
                     + _proc_state("peer", pp) + "\n" + _proc_state("gateway", pg))


def run_cli(env, cmd):
    print(f"\n=== client-cli {cmd} ===", flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(CLI), cmd],
        cwd=str(ROOT / "client-cli"),
        env=env,
    )
    if p.returncode != 0:
        raise SystemExit(f"FAIL {cmd}")
    print(f"PASS {cmd}", flush=True)


def run_peer_alpha1():
    """A1.2 Peer CLI walkthrough (starts its own stack)."""
    print("\n=== peer_alpha1_cli (A1.2) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "peer_alpha1_cli.py")], cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit("FAIL peer_alpha1_cli")
    print("PASS peer_alpha1_cli", flush=True)


def run_iroh_loopback():
    """A1.1 Iroh loopback smoke (opt-in; starts its own stack; no public egress)."""
    print("\n=== iroh_loopback_smoke (A1.1) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "iroh_loopback_smoke.py")], cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit("FAIL iroh_loopback_smoke")
    print("PASS iroh_loopback_smoke", flush=True)


def run_a13_mock_topup():
    """A1.3 mock Stripe top-up smoke (starts its own stack)."""
    print("\n=== a13_mock_topup_smoke (A1.3) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "a13_mock_topup_smoke.py")], cwd=str(ROOT))
    if p.returncode != 0:
        raise SystemExit("FAIL a13_mock_topup_smoke")
    print("PASS a13_mock_topup_smoke", flush=True)


def run_a30_private_guard():
    """A3.0 private-address guard: Rust stream-proto::guard tests + Python mirror self-test.
    No stack, no network, no iroh dependency."""
    import shutil

    print("\n=== A3.0 private guard (rust stream-proto::guard) ===", flush=True)
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    cargo = shutil.which("cargo")
    if cargo is None:
        if impl == "rust":
            raise SystemExit("FAIL a30: cargo not found but SPIKE_IMPL=rust")
        print("SKIP a30 rust guard tests: cargo not found (SPIKE_IMPL!=rust)", flush=True)
    else:
        p = subprocess.run(
            [cargo, "test", "-p", "stream-proto", "--locked", "guard"],
            cwd=str(ROOT / "rust"),
        )
        if p.returncode != 0:
            raise SystemExit("FAIL a30 rust guard tests")
        print("PASS a30 rust guard tests", flush=True)

    print("\n=== A3.0 private guard (python spike_private_guard self-test) ===", flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "spike_private_guard.py")],
        cwd=str(ROOT),
    )
    if p.returncode != 0:
        raise SystemExit("FAIL a30 python guard self-test")
    print("PASS a30 python guard self-test", flush=True)
    print("\nA3.0_PRIVATE_GUARD_GREEN", flush=True)


def _run_netns_smoke(tag, title, script, marker, needs=("cargo", "sudo"), strict_env="SPIKE_A3"):
    """Run a sudo-netns A3 smoke (a31/a32/a33) as the current user.

    These smokes build stream-gateway `--features iroh` into rust/target/iroh, then run
    Control + Gateway + a test client inside a throwaway `ip netns` (only `lo`, no default
    route). They call `sudo` themselves for the netns steps and drop back to this user inside,
    so we invoke them as the current user (no root-owned build artifacts) and only pre-check
    that non-interactive sudo works. Their ports live inside the private netns, so they never
    collide with host stacks or SPIKE_PORT_BASE runs; the smokes reset env in the netns.

    Not part of `all` (needs sudo). Skips cleanly when cargo or `sudo -n` is unavailable,
    unless SPIKE_IMPL=rust or SPIKE_A3=1 (then it fails).

    `needs` / `strict_env` let A4 smokes reuse it (a40: needs sudo + docker, strict under SPIKE_A4=1).
    `strict_env` may be a tuple (strict if any is "1"). `marker` may be a tuple of accepted green
    markers (first match wins and is printed in the PASS line), e.g. a44 part 1 / final.

    Returns ("pass", combined_output) or ("skip", ""); failures raise SystemExit."""
    import shutil

    print(f"\n=== {script} ({title}) ===", flush=True)
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    envs = (strict_env,) if isinstance(strict_env, str) else tuple(strict_env)
    hit = [e for e in envs if os.environ.get(e, "").strip() == "1"]
    strict = impl == "rust" or bool(hit)
    why = "SPIKE_IMPL=rust" if impl == "rust" else (f"{hit[0]}=1" if hit else "")
    set_hint = " or ".join(f"{e}=1" for e in envs)

    def unavailable(reason):
        if strict:
            raise SystemExit(f"FAIL {tag}: {reason} but {why}")
        print(f"SKIP {tag} {script}: {reason} (set {set_hint} or SPIKE_IMPL=rust to make this fatal)", flush=True)
        return "skip", ""

    dh = os.environ.get("DOCKER_HOST", "").strip()
    if "docker" in needs and dh and not dh.startswith("unix://"):  # before any SKIP: never silently pass
        raise SystemExit(f"FAIL {tag}: DOCKER_HOST={dh} is not a unix:// socket; refusing to use a remote/TCP "
                         f"Docker engine (unset DOCKER_HOST or point it at {LOCAL_DOCKER_HOST})")
    if "cargo" in needs and shutil.which("cargo") is None:
        return unavailable("cargo not found")
    if "docker" in needs and shutil.which("docker") is None:
        return unavailable("docker not found")
    if shutil.which("sudo") is None:
        return unavailable("sudo not found (needed for ip netns / docker)")
    if os.geteuid() != 0:
        try:
            p = subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=10)
            sudo_ok = p.returncode == 0
        except Exception:
            sudo_ok = False
        if not sudo_ok:
            return unavailable("non-interactive sudo (sudo -n) unavailable")

    child_env = docker_child_env(tag) if "docker" in needs else None
    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / f"{script}.py")],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=child_env,
    )
    sys.stdout.write(p.stdout)
    sys.stdout.flush()
    markers = (marker,) if isinstance(marker, str) else tuple(marker)
    lines = {l.strip() for l in p.stdout.splitlines()}
    matched = next((m for m in markers if m in lines), None)
    green = matched is not None
    if p.returncode != 0 or not green:
        raise SystemExit(f"FAIL {script} (rc={p.returncode}, green_marker={green})")
    print(f"PASS {script}" + (f" (marker: {matched})" if len(markers) > 1 else ""), flush=True)
    return "pass", p.stdout


def run_a40_compose_guard():
    """A4.0 compose Control mints iroh_local tickets (Platform's scripts/a40_compose_guard_smoke.py).
    Needs sudo docker; SKIP without it unless SPIKE_A4=1 / SPIKE_IMPL=rust."""
    return _run_netns_smoke("a40", "A4.0", "a40_compose_guard_smoke", "A4.0_COMPOSE_GUARD_GREEN",
                            needs=("sudo", "docker"), strict_env="SPIKE_A4")


def run_a42_self_relay():
    """A4.2 self-hosted relay smoke (Platform's scripts/a42_self_relay_smoke.py): a42-* netns, no
    default route, relay at http://10.73.0.254:3340/ (dev http), real stream-peer iroh_pilot as the
    Peer. Needs cargo + sudo -n; SKIP without them unless SPIKE_IMPL=rust / SPIKE_A3=1 / SPIKE_A4=1
    (then FAIL). The smoke is single-instance (/tmp/stream-spike-a42.lock) and fails if it's held."""
    return _run_netns_smoke("a42", "A4.2", "a42_self_relay_smoke", "A4.2_SELF_RELAY_GREEN",
                            needs=("cargo", "sudo"), strict_env=("SPIKE_A3", "SPIKE_A4"))


def run_a43_gateway_egress():
    """A4.3 Gateway egress check + kill propagation smoke (Platform's scripts/a43_gateway_egress_smoke.py):
    the a42 relay-only netns + a43-site TLS stand-in 198.51.100.10:443, no default route. Needs cargo +
    sudo -n; SKIP without them unless SPIKE_IMPL=rust / SPIKE_A3=1 / SPIKE_A4=1 (then FAIL). Shares
    /tmp/stream-spike-a42.lock with a42 (fails if another a42/a43 run holds it)."""
    return _run_netns_smoke("a43", "A4.3", "a43_gateway_egress_smoke", "A4.3_GATEWAY_EGRESS_GREEN",
                            needs=("cargo", "sudo"), strict_env=("SPIKE_A3", "SPIKE_A4"))


def run_a46_stripe_test_topup(stripe_live=False):
    """A4.6 Stripe test-mode top-up smoke (Platform's scripts/a46_stripe_test_topup_smoke.py).
    Python only, loopback ports, no sudo / cargo. Lane A (locally signed events, loopback Stripe fake)
    always runs. Lane B (real Stripe TEST API) runs only with SPIKE_A46_STRIPE_LIVE=1 and a key in the
    env; otherwise the key is scrubbed and lane B skips, so the run is network-free. Lane B is never
    required (also not under SPIKE_A4=1)."""
    print("\n=== a46_stripe_test_topup_smoke (A4.6) ===", flush=True)
    print(f"a46: Stripe test API {'opt-in (SPIKE_A46_STRIPE_LIVE=1)' if stripe_live else 'off (key scrubbed; lane B skips)'}",
          flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "a46_stripe_test_topup_smoke.py")],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    out = p.stdout
    for k in STRIPE_SECRET_ENVS:  # belt and braces: the smoke scrubs too
        v = os.environ.get(k, "").strip()
        if v:
            out = out.replace(v, v[:8] + "…REDACTED")
    sys.stdout.write(out)
    sys.stdout.flush()
    lines = [l.strip() for l in out.splitlines()]
    lane_b = next((l.split("A46_LANE_B=", 1)[1] for l in lines if "A46_LANE_B=" in l), "?")
    if p.returncode != 0 or "A4.6_STRIPE_TEST_TOPUP_GREEN" not in lines:
        raise SystemExit(f"FAIL a46_stripe_test_topup_smoke (rc={p.returncode})")
    print(f"PASS a46_stripe_test_topup_smoke (lane B: {lane_b})", flush=True)
    return "pass", out


# A4.4: Peer ships the final marker with part 2; until then part 1 is accepted. Final listed first
# so it wins when both are printed. To require the final marker, drop the PART1 entry.
A44_MARKERS = ("A4.4_PEER_EGRESS_GREEN", "A4.4_PEER_EGRESS_PART1_GREEN")


def run_a44_peer_egress():
    """A4.4 Peer pilot egress smoke (Peer's scripts/a44_peer_egress_smoke.py): netns, no default
    route, TEST-NET-2 stand-in 198.51.100.10:443. Needs cargo + sudo -n; SKIP without them unless
    SPIKE_IMPL=rust / SPIKE_A3=1 / SPIKE_A4=1 (then FAIL)."""
    return _run_netns_smoke("a44", "A4.4", "a44_peer_egress_smoke", A44_MARKERS,
                            needs=("cargo", "sudo"), strict_env=("SPIKE_A3", "SPIKE_A4"))


def run_a41_pilot_guard():
    """A4.1 pilot guard: Rust stream-proto::guard::pilot tests (default + feature a4_local) and the
    Python mirror's pilot self-test. No stack, no network (resolver injected).
    Same skip/fail as a30, and missing cargo is also fatal under SPIKE_A4=1."""
    import shutil

    print("\n=== A4.1 pilot guard (rust stream-proto::guard::pilot) ===", flush=True)
    out_parts: list[str] = []
    rust_ran = False
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    strict = impl == "rust" or os.environ.get("SPIKE_A4", "").strip() == "1"
    cargo = shutil.which("cargo")
    if cargo is None:
        if strict:
            raise SystemExit("FAIL a41: cargo not found but " + ("SPIKE_IMPL=rust" if impl == "rust" else "SPIKE_A4=1"))
        print("SKIP a41 rust pilot guard tests: cargo not found (set SPIKE_A4=1 or SPIKE_IMPL=rust to make this fatal)", flush=True)
    else:
        for extra in ([], ["--features", "a4_local"]):
            p = subprocess.run(
                [cargo, "test", "-p", "stream-proto", "--locked", *extra, "guard::pilot"],
                cwd=str(ROOT / "rust"), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
            sys.stdout.write(p.stdout)
            sys.stdout.flush()
            out_parts.append(p.stdout)
            if p.returncode != 0:
                raise SystemExit(f"FAIL a41 rust pilot guard tests {' '.join(extra)}".rstrip())
        rust_ran = True
        print("PASS a41 rust pilot guard tests (default + a4_local)", flush=True)

    print("\n=== A4.1 pilot guard (python spike_private_guard --pilot) ===", flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "spike_private_guard.py"), "--pilot"],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    sys.stdout.write(p.stdout)
    if p.returncode != 0 or "A4.1_PY_PILOT_SELFTEST_OK" not in p.stdout.split():
        raise SystemExit("FAIL a41 python pilot self-test")
    out_parts.append(p.stdout)
    print("PASS a41 python pilot self-test", flush=True)
    print("\nA4.1_PILOT_GUARD_GREEN", flush=True)
    return ("pass" if rust_ran else "skip"), "".join(out_parts)


def run_a31_gateway_endpoint():
    """A3.1 Gateway iroh_local endpoint smoke (Platform's scripts/a31_gateway_endpoint_smoke.py)."""
    return _run_netns_smoke("a31", "A3.1", "a31_gateway_endpoint_smoke", "A3.1_GATEWAY_ENDPOINT_GREEN")


def run_a32_peer_dial():
    """A3.2 Peer iroh_local dial smoke (Peer's scripts/a32_peer_dial_smoke.py)."""
    return _run_netns_smoke("a32", "A3.2", "a32_peer_dial_smoke", "A3.2_PEER_DIAL_GREEN")


def run_a33_ticket_bind():
    """A3.3 Control ticket binding smoke (Platform's scripts/a33_ticket_bind_smoke.py)."""
    return _run_netns_smoke("a33", "A3.3", "a33_ticket_bind_smoke", "A3.3_TICKET_BIND_GREEN")

A34_LOCK = "/tmp/stream-spike-a34.lock"
A34_NETNS = ("ns-a3-br", "ns-gw", "ns-peer-a", "ns-peer-b")
A34_LOCK_WAIT_S = 120


def run_a34_multinode():
    """A3.4 multi-node netns smoke (Platform's scripts/a34_multinode_smoke.py).

    Single-instance on the box: the smoke uses fixed netns names (ns-gw / ns-peer-a / ns-peer-b /
    ns-a3-br, bridge br-a3) and tears them down at the end. So we take an flock on
    /tmp/stream-spike-a34.lock (wait up to 120 s, then FAIL), and refuse to start if any of those
    namespaces already exist (someone else's run, or leftovers) instead of deleting them."""
    import fcntl

    fd = os.open(A34_LOCK, os.O_RDWR | os.O_CREAT, 0o666)
    deadline = time.time() + A34_LOCK_WAIT_S
    announced = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                os.close(fd)
                raise SystemExit(f"FAIL a34: another a34 run holds {A34_LOCK} (waited {A34_LOCK_WAIT_S}s); "
                                 "a34 is single-instance on this box, retry when it finishes")
            if not announced:
                print(f"a34: waiting for {A34_LOCK} (another a34 run in progress, up to {A34_LOCK_WAIT_S}s)", flush=True)
                announced = True
            time.sleep(1)
    try:
        listed = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True).stdout.split()
        left = [n for n in A34_NETNS if n in listed]
        if left:
            raise SystemExit(f"FAIL a34: netns {', '.join(left)} already exist (another A3.4 run or leftovers). "
                             "Not deleting someone else's topology; if they are yours, run "
                             "`bash scripts/a3_netns_down.sh` and retry")
        return _run_netns_smoke("a34", "A3.4", "a34_multinode_smoke", "A3.4_MULTINODE_GREEN")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


A45_SCRIPT = "a45_peer_package_smoke"
A45_MARKER = "A4.5_PEER_PACKAGE_GREEN"


def run_a45_peer_package():
    """A4.5 Peer pilot package smoke (Peer's scripts/a45_peer_package_smoke.py, marker
    A4.5_PEER_PACKAGE_GREEN): builds rust/crates/stream-peer/Dockerfile.pilot over the local unix
    docker socket (sudo + docker; no host cargo). Returns ("pending", "") if the script is absent."""
    if not (ROOT / "scripts" / f"{A45_SCRIPT}.py").exists():
        print(f"\nPENDING a45 (scripts/{A45_SCRIPT}.py not on main)", flush=True)
        return "pending", ""
    return _run_netns_smoke("a45", "A4.5", A45_SCRIPT, A45_MARKER,
                            needs=("sudo", "docker"), strict_env="SPIKE_A4")


def run_a47_copy_fixtures():
    """A4.7 copy fixtures check (Designer's scripts/a47_copy_fixtures_smoke.py): Python only."""
    print("\n=== a47_copy_fixtures_smoke (A4.7) ===", flush=True)
    p = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "a47_copy_fixtures_smoke.py")],
                       cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    sys.stdout.write(p.stdout)
    sys.stdout.flush()
    if p.returncode != 0 or "A4.7_COPY_FIXTURES_GREEN" not in {l.strip() for l in p.stdout.splitlines()}:
        raise SystemExit(f"FAIL a47_copy_fixtures_smoke (rc={p.returncode})")
    print("PASS a47_copy_fixtures_smoke", flush=True)
    return "pass", p.stdout


# --------------------------------------------------------------------------- A3.5 (a3)
# Refusal evidence grepped from the sub-smoke outputs (each smoke already exercises the
# refusal against the real Rust binaries inside its no-default-route netns).
A3_REFUSAL_GREPS = (
    # (smoke, needle, what it proves)
    ("a31", "OK gateway public listen refused", "Gateway refuses public listen addr"),
    ("a31", "a3_refuse_non_private:8.8.8.8", "Gateway guard reason for public IP"),
    ("a31", "a3_refuse_non_private:0.0.0.0", "Gateway refuses wildcard listen"),
    ("a31", "a3_refuse_relay_refused:https://use1-1.relay.n0.iroh.iroh.link./", "Gateway refuses n0 relay URL"),
    ("a31", "a3_refuse_discovery_refused", "Gateway refuses discovery"),
    ("a31", "REFUSED public_addr a3_refuse_non_private:8.8.8.8", "test client refuses public-IP dial"),
    ("a31", "REFUSED relay_refused", "test client refuses relay URL"),
    ("a32", "OK peer public direct addr refused: A3 iroh_local refused: public_addr", "Peer refuses public-IP dial"),
    ("a32", "OK peer relay URL refused: A3 iroh_local refused: relay_refused", "Peer refuses n0 relay URL"),
    ("a32", "OK peer wildcard bind refused: A3 iroh_local refused: public_addr", "Peer refuses wildcard bind"),
    ("a32", "iroh_local_feature_not_built", "default Peer build refuses iroh_local"),
    ("a33", "error=public_addr detail='a3_refuse_non_private:8.8.8.8'", "Control refuses public direct_addrs"),
    ("a33", "error=allowlist_miss", "Control refuses non-allowlisted private addr"),
    ("a33", "error=endpoint_bind_required", "Control refuses missing/malformed peer endpoint ID"),
    ("a33", "error=direct_addrs_required", "Control refuses empty direct_addrs"),
)
# No default route inside each smoke's netns.
A3_NOROUTE_GREPS = (
    ("a31", "OK netns: no default route"),
    ("a32", "OK netns: no default route"),
    ("a33", "OK netns: no default route"),
)
# UX ordering evidence from a32 (its Proc.expect consumes Peer stderr in order, so these
# OK lines only print if UX_SCREEN p1_isp_ack -> UX_SCREEN p2_consent came before the dial).
A3_UX_ORDER_GREPS = (
    ("a32", "OK P1 ack only: no dial (P2 consent required)", "no dial before P2 consent"),
    ("a32", "OK P1 -> P2 screen IDs before dial; peer iroh_local connected", "UX_SCREEN p1_isp_ack, p2_consent before dial"),
    ("a32", "UX_SCREEN p4_kill", "UX_SCREEN p4_kill on kill-switch"),
    ("a32", "(<2s)", "kill drops the Peer within 2 s"),
)
# A3.4 multi-node evidence (only grepped when a34 ran).
A3_MULTINODE_GREPS = (
    ("a34", "peer implementation: real", "real stream-peer --features iroh_local (not the test client)"),
    ("a34", "OK both peers online over br-a3", "two real Peers in their own netns, own IDs"),
    ("a34", "-> peer_a", "a Control-minted session lands on Peer A"),
    ("a34", "-> peer_b", "a Control-minted session lands on Peer B"),
    ("a34", "peer_a SIGKILLed: gateway dropped it", "kill one Peer -> Gateway drops it, Control marks it offline"),
    ("a34", "post-kill session", "the other Peer still serves"),
)


def _load_screens():
    return json.loads((ROOT / "fixtures" / "screens.json").read_text())


def _a3_ux_inner():
    """Inside a throwaway netns: start the iroh_local Peer (no ack -> never dials), GET
    /peer/consent/p2 and print the Peer's stderr (the `UX P2` block + UX_SCREEN line)."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="a3ux_"))
    peer_bin = ROOT / "rust" / "target" / "iroh" / "debug" / "stream-peer"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SPIKE_IROH_", "SPIKE_P2", "SPIKE_ISP_ACK", "SPIKE_PORT_BASE"))}
    env.update({
        "CONTROL_URL": "http://127.0.0.1:8080", "SPIKE_PEER_ADMIN": "127.0.0.1:9200",
        "SPIKE_PEER_ID": "peer_a3ux", "SPIKE_TRANSPORT": "iroh_local",
        "SPIKE_IROH_KEY_PATH": str(tmp / "peer.key"),
        "SPIKE_GATEWAY_ENDPOINT_ID": "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1",
        "SPIKE_IROH_GATEWAY_ADDR": "10.73.0.1:9102", "SPIKE_IROH_BIND": "10.73.0.1:0",
        "RUST_LOG": "info", "NO_COLOR": "1",
    })
    route = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
    print(f"A3UX_DEFAULT_ROUTE={route or 'none'}", flush=True)
    err = open(tmp / "peer.stderr", "w+")
    p = subprocess.Popen([str(peer_bin)], env=env, stdout=err, stderr=subprocess.STDOUT)
    try:
        timeout = health_timeout_s()
        deadline = time.monotonic() + timeout
        while not http_ok("http://127.0.0.1:9200/health"):
            if p.poll() is not None or time.monotonic() >= deadline:
                err.flush()
                err.seek(0)
                raise SystemExit(f"FAIL a3 ux: iroh_local Peer /health not up within {timeout:g}s "
                                 f"(SPIKE_HEALTH_TIMEOUT_S)\n" + _proc_state("peer", p, err.read().splitlines()))
            time.sleep(HEALTH_POLL_S)
        with urllib.request.urlopen("http://127.0.0.1:9200/peer/consent/p2", timeout=5) as r:
            body = json.loads(r.read().decode())
        print("A3UX_P2_JSON=" + json.dumps(body), flush=True)
        time.sleep(0.5)
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    err.seek(0)
    print("A3UX_STDERR_BEGIN", flush=True)
    sys.stdout.write(err.read())
    print("A3UX_STDERR_END", flush=True)
    return 0


def _a3_ux_copy_check():
    """P2 copy on the iroh_local Peer's real stderr: fragments from fixtures/screens.json
    (`p2_consent.required_copy`) + `forbidden_user_facing_substrings`, nothing hardcoded."""
    print("\n=== A3.5 UX copy (iroh_local Peer stderr, own netns) ===", flush=True)
    peer_bin = ROOT / "rust" / "target" / "iroh" / "debug" / "stream-peer"
    if not peer_bin.exists():
        raise SystemExit(f"FAIL a3: {peer_bin} missing (a32 builds it)")
    ns = f"a3ux-{os.getpid()}"
    user = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
    try:
        for cmd in (["ip", "netns", "add", ns], ["ip", "-n", ns, "link", "set", "lo", "up"],
                    ["ip", "-n", ns, "addr", "add", "10.73.0.1/24", "dev", "lo"]):
            subprocess.run(["sudo", "-n", *cmd], check=True, stdin=subprocess.DEVNULL)
        p = subprocess.run(["sudo", "-n", "ip", "netns", "exec", ns, "sudo", "-n", "-u", user, "env",
                            f"PATH={os.environ.get('PATH', '')}", f"HOME={os.environ.get('HOME', '')}",
                            sys.executable, "-u", str(Path(__file__).resolve()), "_a3_ux_inner"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    finally:
        subprocess.run(["sudo", "-n", "ip", "netns", "del", ns], stdin=subprocess.DEVNULL)
    out = p.stdout
    if p.returncode != 0 or "A3UX_STDERR_BEGIN" not in out:
        raise SystemExit(f"FAIL a3 ux capture rc={p.returncode}: {(out + p.stderr)[-800:]}")
    if "A3UX_DEFAULT_ROUTE=none" not in out:
        raise SystemExit("FAIL a3: ux netns has a default route")
    print("OK ux netns: no default route", flush=True)
    stderr = out.split("A3UX_STDERR_BEGIN", 1)[1].split("A3UX_STDERR_END", 1)[0].splitlines()
    if not any("UX P2" == l.strip() for l in stderr):
        raise SystemExit(f"FAIL a3: no `UX P2` block on Peer stderr: {stderr[-10:]}")
    i = next(n for n, l in enumerate(stderr) if l.strip() == "UX P2")
    j = next((n for n in range(i + 1, len(stderr)) if stderr[n].strip() == "UX_SCREEN p2_consent"), None)
    if j is None:
        raise SystemExit("FAIL a3: `UX_SCREEN p2_consent` does not follow the `UX P2` block")
    block = "\n".join(stderr[i + 1:j])
    print("  [peer] UX P2", flush=True)
    for l in stderr[i + 1:j + 1]:
        print(f"  [peer] {l}", flush=True)
    screens = _load_screens()
    p2 = next(c for c in screens["consent"] if c.get("id") == "p2_consent")
    frags = p2.get("required_copy") or []
    if not frags:
        raise SystemExit("FAIL a3: fixtures/screens.json p2_consent.required_copy is empty")
    for frag in frags:
        if frag not in block:
            raise SystemExit(f"FAIL a3: P2 required_copy {frag!r} (fixtures/screens.json) not on Peer stderr")
        print(f"OK p2_consent required_copy on Peer stderr: {frag!r}", flush=True)
    for bad in screens.get("forbidden_user_facing_substrings", []):
        if bad in block:
            raise SystemExit(f"FAIL a3: forbidden user-facing substring {bad!r} in Peer P2 block")
    print(f"OK P2 block clean of forbidden_user_facing_substrings ({len(screens.get('forbidden_user_facing_substrings', []))} from fixture)", flush=True)


def run_a3_iroh_local():
    """A3.5 / TOM-18: A3.0-A3.4 + refusals + UX greps + banned-word checks -> A3_IROH_LOCAL_GREEN."""
    import shutil

    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    strict = impl == "rust" or os.environ.get("SPIKE_A3", "").strip() == "1"
    pending: list[str] = []

    run_a30_private_guard()
    outs = {}
    for tag, fn in (("a31", run_a31_gateway_endpoint), ("a32", run_a32_peer_dial), ("a33", run_a33_ticket_bind)):
        status, outs[tag] = fn()
        if status != "pass":
            pending.append(f"{tag} skipped")
    if (ROOT / "scripts" / "a34_multinode_smoke.py").exists():
        status, outs["a34"] = run_a34_multinode()
        if status != "pass":
            pending.append("a34 skipped")
    else:
        print("\nPENDING a34 (scripts/a34_multinode_smoke.py not on main)", flush=True)
        pending.append("a34 (scripts/a34_multinode_smoke.py not on main)")

    print("\n=== A3.5 greps over sub-smoke output ===", flush=True)
    for tag, needle, what in A3_REFUSAL_GREPS + A3_UX_ORDER_GREPS + A3_MULTINODE_GREPS:
        if tag not in outs or not outs[tag]:
            continue
        if needle not in outs[tag]:
            raise SystemExit(f"FAIL a3: {tag} output lacks {needle!r} ({what})")
        print(f"OK [{tag}] {what}: {needle!r}", flush=True)
    noroute = A3_NOROUTE_GREPS + ((("a34", "OK no default route (v4/v6) in ns-a3-br, ns-gw, ns-peer-a, ns-peer-b"),)
                                  if outs.get("a34") else ())
    for tag, needle in noroute:
        if outs.get(tag):
            if needle not in outs[tag]:
                raise SystemExit(f"FAIL a3: {tag} output lacks {needle!r}")
            print(f"OK [{tag}] netns has no default route", flush=True)

    if outs.get("a32"):
        _a3_ux_copy_check()

    print("\n=== A3.5 banned-word / UX checks (existing Rust Peer tests, unchanged) ===", flush=True)
    cargo = shutil.which("cargo")
    if cargo is None:
        pending.append("Rust ux tests skipped (no cargo)")
        print("SKIP a3 Rust ux tests: cargo not found", flush=True)
    else:
        p = subprocess.run([cargo, "test", "--locked", "-q", "-p", "stream-peer", "--", "ux::", "iroh_local::"],
                           cwd=str(ROOT / "rust"), stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise SystemExit("FAIL a3: stream-peer ux:: / iroh_local:: tests")
        print("PASS stream-peer ux:: + iroh_local:: tests (fixture drift, forbidden copy, screen order)", flush=True)

    if pending:
        print(f"\nA3_IROH_LOCAL_NOT_GREEN pending: {'; '.join(pending)}", flush=True)
        if strict:
            raise SystemExit(1)
        return 3
    print("\nA3_IROH_LOCAL_GREEN", flush=True)
    return 0


# --------------------------------------------------------------------------- A4.8 (a4)
# Roll-up: A4.0-A4.7 smokes in order + cross-cutting checks. Prints A4_PILOT_GREEN only if every
# step passed with no skip/pending; otherwise A4_PILOT_NOT_GREEN pending: ... (exit 1 under
# SPIKE_A4=1 / SPIKE_IMPL=rust, else 3). A failing check is FAIL (exit 1) immediately.
import re as _re  # noqa: E402



REDIAL_NO_RETRY_REASONS = {"kill", "force_disconnect", "cleared_ack", "consent_withdrawn", "shutdown"}
# Peer 94d9a6e: SIGTERM/SIGINT with a live session logs
# `session_end end_reason=shutdown closed_by=local up_ms=<ms> udp_counters ...` then exits 0.
# It is terminal (no redial in that process) and must be closed_by=local.
REDIAL_LOCAL_ONLY_REASONS = {"shutdown"}
# Peer 4f28b86/4132e68 (iroh_pilot_dial.rs CAP_REFUSALS): a Gateway refusal logs
# `session_end end_reason=auth_reject:<code> closed_by=remote` and the next redial is
# `redial attempt=N redial_in_ms=<cap> after=auth_reject:<code>`; Backoff::jump_to_cap returns
# exactly cap_ms (no jitter), so the lower bound is cap_ms itself.
REDIAL_CAP_REASONS = ("auth_reject:peer_id_in_use", "auth_reject:endpoint_mismatch")
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")


def gateway_replaced_check(text):
    """Gateway half of A4.R over a Gateway log (stream-gateway/src/relay/io.rs, ce80867).
    The Gateway logs no line for a `replaced` close, so a replacement is inferred: a second
    `peer online <peer_id> endpoint=<E2>` with no `peer offline <peer_id>` since the first
    `peer online <peer_id> endpoint=<E1>` (a replaced session never logs `peer offline`).
    E1 != E2 FAILs (one Peer knocked another offline); E1 == E2 is the allowed same-endpoint
    replace. `gateway HTTP on` (startup) clears the table. Returns (fails, counts)."""
    live, fails = {}, []
    counts = {"online": 0, "replaced_same": 0, "replaced_cross": 0, "peer_id_in_use": 0}
    for ln in (text or "").splitlines():
        ln = _ANSI_RE.sub("", ln)
        if "gateway HTTP on " in ln:
            live = {}
            continue
        m = _re.search(r"\bpeer online (\S+) endpoint=(\S*)", ln)
        if m:
            pid, ep = m.group(1), m.group(2)
            counts["online"] += 1
            if pid in live:
                old = live[pid]
                if old and ep and old != ep:
                    counts["replaced_cross"] += 1
                    fails.append(f"gateway: `replaced` across endpoints for {pid}: {old[:16]}… -> {ep[:16]}… "
                                 f"(a different endpoint evicted a live session): {ln.strip()}")
                else:
                    counts["replaced_same"] += 1
            live[pid] = ep
            continue
        m = _re.search(r"\bpeer offline (\S+)", ln)
        if m:
            live.pop(m.group(1), None)
            continue
        if _re.search(r"\bpeer_id_in_use \S+ endpoint=", ln):
            counts["peer_id_in_use"] += 1
    return fails, counts


def peer_reconnect_check(outs_by_tag, base_ms=None, reset_secs=None, cap_ms=None, gateway_log=None):
    """A4.R reconnect contract over captured Peer logs (8f975dc / fa935fa / 75ec884 / 4132e68).
    Returns (status, msgs, counts); status is "ok", "pending" or "fail".
    A Peer process starts at its `peer iroh_pilot bound` line. Within one process:
    every `session_end` carries end_reason= and closed_by=; a `connected gateway=` after a
    session_end needs a `redial attempt=` in between; redial_in_ms >= base/2; attempt numbers
    don't go down unless the
    redial line has reset=1 AND attempt=1 AND the prior session_end up_ms >= reset secs*1000;
    (reset=1 otherwise FAILs); no redial after
    kill/force_disconnect/cleared_ack/consent_withdrawn/shutdown. end_reason=shutdown (graceful
    SIGTERM/SIGINT, Peer 94d9a6e) must be closed_by=local; a log ending in it is fine.
    After end_reason=auth_reject:peer_id_in_use|endpoint_mismatch (closed_by=remote) the next
    redial must be after=<that reason> with redial_in_ms >= SPIKE_REDIAL_CAP_MS (default 300000;
    the Peer's jump_to_cap has no jitter). On a cap redial reset=1 still needs the long up_ms, but
    attempt= is the cap step, not 1.
    gateway_log: Gateway log text; default reads the file at SPIKE_GATEWAY_LOG. If given, a
    `replaced` between two different endpoints FAILs (see gateway_replaced_check)."""
    if base_ms is None:
        base_ms = int(os.environ.get("SPIKE_REDIAL_BASE_MS") or 2000)
    if reset_secs is None:
        reset_secs = float(os.environ.get("SPIKE_REDIAL_RESET_SECS") or 60)
    if cap_ms is None:
        cap_ms = int(os.environ.get("SPIKE_REDIAL_CAP_MS") or 300000)
    cap_ms = max(cap_ms, base_ms)  # Backoff::new: cap_ms.max(base_ms)
    floor = base_ms // 2
    fails, counts = [], {"session_end": 0, "with_reason": 0, "redial": 0, "reconnect": 0, "with_up_ms": 0, "reset": 0,
                         "cap_refusal": 0, "cap_redial": 0}
    for tag, out in outs_by_tag.items():
        ended = None          # last session_end fields in this process, until a reconnect
        redialed = False
        capped = None         # cap-refusal end_reason awaiting its redial line
        last_attempt, last_up = None, None
        for ln in (out or "").splitlines():
            if "peer iroh_pilot bound " in ln:
                ended, redialed, capped, last_attempt, last_up = None, False, None, None, None
            elif "session_end" in ln and "peer iroh_pilot" in ln:
                counts["session_end"] += 1
                er = _re.search(r"\bend_reason=(\S+)", ln)
                cb = _re.search(r"\bclosed_by=(local|remote)\b", ln)
                if er:
                    counts["with_reason"] += 1
                if not (er and cb):
                    fails.append(f"[{tag}] session_end without end_reason=/closed_by=: {ln.strip()}")
                up = _re.search(r"\bup_ms=(\d+)\b", ln)
                if up:
                    counts["with_up_ms"] += 1
                last_up = int(up.group(1)) if up else None
                ended, redialed = (er.group(1) if er else "?"), False
                capped = ended if ended in REDIAL_CAP_REASONS else None
                if ended in REDIAL_LOCAL_ONLY_REASONS and cb and cb.group(1) != "local":
                    fails.append(f"[{tag}] end_reason={ended} must be closed_by=local: {ln.strip()}")
                if capped:
                    counts["cap_refusal"] += 1
                    if cb and cb.group(1) != "remote":
                        fails.append(f"[{tag}] end_reason={capped} must be closed_by=remote: {ln.strip()}")
            elif "peer iroh_pilot redial attempt=" in ln:
                counts["redial"] += 1
                m = _re.search(r"attempt=(\d+) redial_in_ms=(\d+)", ln)
                if not m:
                    fails.append(f"[{tag}] unparsable redial line: {ln.strip()}")
                    continue
                a, ms = int(m.group(1)), int(m.group(2))
                if ended in REDIAL_NO_RETRY_REASONS:
                    fails.append(f"[{tag}] redial after end_reason={ended} (must not redial): {ln.strip()}")
                if ms < floor:
                    fails.append(f"[{tag}] redial_in_ms={ms} < floor {floor} (base {base_ms}): {ln.strip()}")
                aft = _re.search(r"\bafter=(\S+)", ln)
                if capped:
                    counts["cap_redial"] += 1
                    if not aft or aft.group(1) != capped:
                        fails.append(f"[{tag}] redial after end_reason={capped} has after="
                                     f"{aft.group(1) if aft else '<missing>'} (want after={capped}): {ln.strip()}")
                    if ms < cap_ms:
                        fails.append(f"[{tag}] redial_in_ms={ms} after {capped} < cap {cap_ms} "
                                     f"(SPIKE_REDIAL_CAP_MS; must back off straight to the cap, no fast redial): {ln.strip()}")
                reset = _re.search(r"\breset=1\b", ln) is not None
                long_up = last_up is not None and last_up >= reset_secs * 1000
                if reset:
                    counts["reset"] += 1
                    if a != 1 and not capped:
                        fails.append(f"[{tag}] reset=1 with attempt={a} (must be 1): {ln.strip()}")
                    if not long_up:
                        fails.append(f"[{tag}] reset=1 but prior session_end up_ms={last_up} < {reset_secs * 1000:g}: {ln.strip()}")
                elif last_attempt is not None and a < last_attempt:
                    fails.append(f"[{tag}] redial attempt went {last_attempt} -> {a} without reset=1")
                last_attempt, redialed, capped = a, True, None
            elif "peer iroh_pilot connected gateway=" in ln:
                if ended is not None:
                    counts["reconnect"] += 1
                    if not redialed:
                        fails.append(f"[{tag}] reconnect after session_end end_reason={ended} with no redial attempt= line")
                ended, redialed, capped = None, False, None
    info, gfails = [], []
    if gateway_log is None:
        gp = (os.environ.get("SPIKE_GATEWAY_LOG") or "").strip()
        if gp and Path(gp).is_file():
            gateway_log = Path(gp).read_text(errors="replace")
        else:
            info.append("INFO a4 reconnect: no Gateway log (SPIKE_GATEWAY_LOG unset or missing); "
                        "replaced-across-endpoints check skipped")
    if gateway_log is not None:
        gfails, g = gateway_replaced_check(gateway_log)
        info.append(f"INFO a4 reconnect gateway log: online={g['online']} replaced_same_endpoint={g['replaced_same']} "
                    f"replaced_cross_endpoint={g['replaced_cross']} peer_id_in_use={g['peer_id_in_use']}")
    summary = (f"session_end={counts['session_end']} (with end_reason={counts['with_reason']}) "
               f"(with up_ms={counts['with_up_ms']}) redial={counts['redial']} reset={counts['reset']} reconnect={counts['reconnect']} "
               f"cap_refusal={counts['cap_refusal']} cap_redial={counts['cap_redial']} floor_ms={floor} cap_ms={cap_ms}")
    if gfails:  # Gateway clash FAILs whatever the Peer image
        return "fail", [f"FAIL a4 reconnect: {f}" for f in gfails + fails] + [f"  ({summary})"] + info, counts
    if counts["session_end"] and not counts["with_reason"]:
        return "pending", [f"PENDING a4 reconnect: no session_end line has end_reason= (peer image predates fa935fa); {summary}"] + info, counts
    if counts["with_reason"] and not counts["with_up_ms"] and not fails:
        return "pending", [f"PENDING a4 reconnect: no session_end line has up_ms= (peer image predates 75ec884); {summary}"] + info, counts
    if fails:
        return "fail", [f"FAIL a4 reconnect: {f}" for f in fails] + [f"  ({summary})"] + info, counts
    return "ok", [f"OK a4 reconnect contract: {summary}"] + info, counts

def _norm_image_id(v):
    v = (v or "").strip().lower()
    return v[len("sha256:"):] if v.startswith("sha256:") else v


def parse_image_id_pins(want):
    """SPIKE_PEER_IMAGE_ID -> [(name, normalized id)], or raises ValueError.
    Accepts one bare ID (back compat) or a comma-separated list of bare and/or named IDs, e.g.
    `config=sha256:cccb...,manifest=sha256:7591...`. Names are labels only ([a-z0-9_-]+);
    unnamed entries are labelled `id`. Docker 29 / containerd store shows the manifest digest as
    the image ID, so a pin usually lists both the config and the manifest digest."""
    pins = []
    for part in (want or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, val = "id", part
        m = _re.match(r"^([A-Za-z][A-Za-z0-9_-]*)=(.*)$", part)
        if m:
            name, val = m.group(1).lower(), m.group(2).strip()
        v = _norm_image_id(val)
        if not _re.fullmatch(r"[0-9a-f]{12,64}", v):
            raise ValueError(f"SPIKE_PEER_IMAGE_ID entry {part!r} is not a sha256 image ID")
        pins.append((name, v))
    if (want or "").strip() and not pins:
        raise ValueError(f"SPIKE_PEER_IMAGE_ID={want!r} lists no image ID")
    return pins


def a45_image_id_check(out, want=None):
    """a4 roll-up check on a45 output. Returns (ok, msg). `want` = SPIKE_PEER_IMAGE_ID: one bare
    ID or a comma-separated list of named/bare IDs (see parse_image_id_pins). Passes if any
    A45_IMAGE_ID or A45_IMAGE_CONFIG_ID line matches any listed ID (case and `sha256:` ignored)."""
    ids = _re.findall(r"^A45_IMAGE_ID=(\S+)\s*$", out or "", _re.M)
    cfg = _re.findall(r"^A45_IMAGE_CONFIG_ID=(\S+)\s*$", out or "", _re.M)
    want = (want or "").strip()
    if want:
        try:
            pins = parse_image_id_pins(want)
        except ValueError as e:
            return False, f"FAIL a4: {e}"
        if not ids:
            return False, f"FAIL a4: SPIKE_PEER_IMAGE_ID={want} set but a45 printed no A45_IMAGE_ID line"
        got = {_norm_image_id(x) for x in ids + cfg}
        hit = [n for n, v in pins if v in got]
        if not hit:
            shown = ids[-1] + (f" (config {cfg[-1]})" if cfg else "")
            return False, f"FAIL a4: a45 A45_IMAGE_ID={shown} matches none of SPIKE_PEER_IMAGE_ID={want}"
        return True, f"OK a45 image id {ids[-1]} matches SPIKE_PEER_IMAGE_ID ({'/'.join(hit)})"
    return True, f"INFO a45 image id {ids[-1] if ids else '?'}"

A44_FINAL_MARKER = "A4.4_PEER_EGRESS_GREEN"
PEER_UDP_ZERO_LINE = "udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0 recv_v6=0"
INSECURE_CALL_RE = _re.compile(r"insecure_skip_relay_cert_verify\s*\(")

# (smoke, needle, what it proves). Exact substrings of the smokes' own OK/log lines.
A4_REFUSAL_GREPS = (
    # relay: ours only, n0/other refused, relay-only, no fallback (A4.2)
    ("a42", "OK gateway: SPIKE_RELAY_ALLOW_URL unset refused", "Gateway refuses to start without our relay"),
    ("a42", "OK gateway: n0 relay as SPIKE_RELAY_ALLOW_URL refused: rc=2 :: iroh_pilot refused: a4_refuse_relay_config:",
     "n0 host as SPIKE_RELAY_ALLOW_URL -> relay_config (Gateway)"),
    ("a42", "OK gateway: n0 relay URL refused: rc=2 :: iroh_pilot refused: a3_refuse_relay_refused:", "n0 relay URL -> relay_refused (Gateway)"),
    ("a42", "OK gateway: different relay URL refused: rc=2 :: iroh_pilot refused: a3_refuse_relay_refused:", "other relay URL -> relay_refused (Gateway)"),
    ("a42", "OK gateway: non-relay-only path selection refused", "Gateway refuses non-relay-only path selection"),
    ("a42", "OK gateway: discovery refused: rc=2 :: iroh_pilot refused: a3_refuse_discovery_refused", "discovery on -> discovery_refused"),
    ("a42", "OK client: n0 relay refused: REFUSED relay_refused", "client refuses n0 relay"),
    ("a42", "OK client: different relay refused: REFUSED relay_refused", "client refuses look-alike relay (other port)"),
    ("a42", "OK real peer: n0 relay as allow URL refused: peer a4_refuse_relay_config:", "real Peer: n0 allow URL -> relay_config"),
    ("a42", "OK real peer: different relay refused: a3_refuse_relay_refused:", "real Peer: other relay -> relay_refused"),
    ("a42", "OK real peer: pilot build refuses iroh_local: peer a4_refuse_transport_not_pilot:", "pilot build -> transport_not_pilot"),
    ("a42", "no direct fallback", "relay killed -> live conn dropped, no direct fallback"),
    ("a42", "OK relay down: new relay-only dial fails closed", "relay down -> new dial fails closed"),
    ("a42", "answered egress_off (Peer's SPIKE_PUBLIC_EGRESS off", "iroh_pilot with SPIKE_PUBLIC_EGRESS unset refuses OPENs (egress_off)"),
    # Gateway egress + kill switch (A4.3)
    ("a43", "OK default OFF: /gw/start -> 403 egress_off/flag_off refused_at=gateway; nothing forwarded", "default OFF at the Gateway"),
    ("a43", "egress_reason=egress_off detail=local_off", "SPIKE_PUBLIC_EGRESS=0 -> egress_off/local_off"),
    ("a43", "egress_reason=egress_off detail=no_state", "Control unreachable -> egress_off/no_state"),
    ("a43", "-> 403 admin_local_only", "Control switch is local-only"),
    ("a43", "-> 403 egress_not_allowlisted at the gateway; no AUTH_TICKET sent", "off-allowlist host refused AT THE GATEWAY (Peer would allow; never forwarded)"),
    ("a43", "reason=egress_off detail=flag_stale", "stale flag (Control frozen) -> egress_off/flag_stale"),
    ("a43", "reason=egress_allowlist_mismatch", "allowlist version mismatch -> egress_allowlist_mismatch (Gateway)"),
    ("a43", "stream closed egress_budget_exceeded", "byte cap -> egress_budget_exceeded (Gateway authoritative)"),
    ("a43", "next OPEN -> 403 egress_budget_exceeded", "over cap -> new OPENs refused"),
    ("a43", "refused at the real peer: a4_refuse_egress_resolved_non_public:priv.example.com->10.73.0.1",
     "allowlisted name -> private IP refused at the real Peer (egress_resolved_non_public)"),
    ("a43", "OK real peer with a different allowlist -> refused at the peer: egress_allowlist_mismatch", "allowlist mismatch at the real Peer"),
    ("a43", "P8 peer offline_reason=egress_off", "kill switch off -> Peer P8 egress_off"),
    ("a43", "A43_TEARDOWN stopped=0", "teardown: Control SIGSTOP chain resumed + killed, no stopped (T) leftovers"),
    # Peer egress (A4.4)
    ("a44", "a4_refuse_egress_not_allowlisted:site1.pilot.example:80", "off-allowlist dest refused AT THE PEER (egress_not_allowlisted)"),
    ("a44", "a4_refuse_egress_resolved_non_public:site2.pilot.example->169.254.169.254", "metadata IP via allowlisted name refused at the Peer"),
    ("a44", "a4_refuse_egress_stream_limit:2", "third concurrent stream -> egress_stream_limit"),
    ("a44", "corrupt budget -> a4_refuse_egress_budget:state_corrupt", "corrupt byte counter fails closed"),
    ("a44", "restart 2: bytes_today=", "byte budget survives restarts"),
    ("a44", "a4_refuse_egress_pin_mismatch:", "sslip/explicit pin disagreement refused at the Peer"),
    ("a44", "A44_PINNED_OK", "pinned entry connects to the pin"),
    ("a44", "(no DNS)", "pinned entry never asks DNS (Peer)"),
    ("a44", "not retrying", "relay-path watcher trip: closed, no retry"),
    ("a44", "A44_PILOT_E2E_OK", "pilot e2e ran (real Peer DATA pump over the relay + watcher trip)"),
    ("a44", "OK TLS GET /hello via the real Peer DATA pump", "real Peer forwards TLS to the allowlisted pinned site"),
    ("a44", "3rd -> 403 egress_stream_limit", "real Peer: third held stream -> egress_stream_limit"),
    ("a44", "Peer P8 peer offline_reason=egress_off", "real Peer: flag off -> P8 egress_off"),
    ("a44", "no redial in", "watcher trip: one connect, no redial"),
    # Guard unit tests (A4.1) cited by name
    ("a41", "pilot_pinned_resolve_skips_dns ... ok", "pinned/sslip entries never call the resolver (panicking resolver test)"),
    ("a41", "pilot_sslip_allowlist_parse ... ok", "sslip entries must embed a public IP"),
    ("a41", "pilot_egress_dest_check ... ok", "literal-IP OPEN -> egress_not_allowlisted"),
    ("a41", "pilot_resolve_and_pin ... ok", "mixed answer / rebinding stand-in refused"),
    ("a41", "pilot_kill_switch ... ok", "kill switch: stale = off, future ts = stale"),
    ("a41", "A4.1_PY_PILOT_SELFTEST_OK", "Python mirror agrees"),
    # Pilot Peer image (A4.5)
    ("a45", "a45: uid 10001, defaults ok, no presets", "image runs non-root (uid 10001; `id -u` in the container) with no consent presets"),
    ("a45", "no a4_local marker SPIKE_A4_FAKE_UDP_DATAGRAMS", "image binary lacks SPIKE_A4_FAKE_UDP_DATAGRAMS (no a4_local fake-UDP hook)"),
    ("a45", "no insecure_skip_relay_cert_verify", "image binary lacks insecure_skip_relay_cert_verify"),
    ("a45", "a45: no key/budget file in any image layer", "no key / budget file in any image layer"),
    ("a45", "/data empty in image", "/data empty in the image"),
    ("a45", "on-box relay refused (Pilot lane)", "image is the Pilot lane (on-box relay refused)"),
    ("a45", "a45: --network none up, healthy", "image runs --network none, healthy, key 0600 uid 10001"),
    # Stripe (A4.6)
    ("a46", "UX c2_add_funds_test", "test top-up prints UX c2_add_funds_test"),
    ("a46", "Control refused to start", "sk_live_ key -> Control refuses to start"),
)
# Timings: (smoke, regex with one float group, max seconds, what)
A4_TIMING_GREPS = (
    ("a43", r"OK flag off -> held stream closed in ([\d.]+)s", 5.0, "flag off -> open stream closed (Gateway)"),
    ("a43", r"OK real peer: flag off -> stream closed in ([\d.]+)s", 5.0, "flag off -> real Peer stream closed"),
    ("a43", r"closed after ([\d.]+)s \(max age 5 s \+ 1 s poll\)", 6.0, "Control frozen -> stale -> closed (max age 5 s + 1 s poll)"),
    ("a44", r"A44_NETNS_STAND_IN_OK closed_after_ms=(\d+)", 5000.0, "Control silent -> Peer closes stream (ms)"),
    ("a44", r"P8 '[^']*' in ([\d.]+)s", 2.0, "watcher trip -> P8 within 2 s"),
    ("a44", r"OK flag off -> both held streams closed in <= ([\d.]+)s", 5.0, "flag off -> real Peer held streams closed"),
)
# Gateway-side UDP datagram counters stay 0 under RelayOnly.
A4_UDP_ZERO_GREPS = (
    ("a42", "OK relay-only connection (real stream-peer): gateway {", "'udp_recv': 0, 'udp_send': 0"),
    ("a44", "OK no UDP datagrams: gateway iroh_path", "'udp_recv': 0, 'udp_send': 0"),
)
A4_NOROUTE_GREPS = (
    ("a42", "OK a42-gw / a42-relay / a42-peer: no default route (v4/v6)"),
    ("a43", "OK no default route in a42-gw/a42-relay/a42-peer/a43-site"),
    ("a44", "OK netns has no default route"),
    ("a45", "a45: --network none up"),
)


def _a4_strict():
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    return impl == "rust" or os.environ.get("SPIKE_A4", "").strip() == "1"


def _strip_rust_comments(src):
    """Drop // line comments (incl. /// and //!) and nested /* */ block comments; keep string and
    char literals intact so `"//"` inside a string isn't treated as a comment. Newlines kept."""
    out, i, n, depth = [], 0, len(src), 0
    while i < n:
        c, nx = src[i], src[i + 1] if i + 1 < n else ""
        if depth:
            if c == "/" and nx == "*":
                depth += 1; i += 2; continue
            if c == "*" and nx == "/":
                depth -= 1; i += 2; continue
            if c == "\n":
                out.append(c)
            i += 1; continue
        if c == "/" and nx == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nx == "*":
            depth = 1; i += 2; continue
        if c == "r" and _re.match(r'r#*"', src[i:i + 8]) and not (i and (src[i - 1].isalnum() or src[i - 1] == "_")):
            hashes = _re.match(r'r(#*)"', src[i:]).group(1)
            close = '"' + hashes
            j = src.find(close, i + 2 + len(hashes))
            j = n if j < 0 else j + len(close)
            out.append(src[i:j]); i = j; continue
        if c == "'":
            m = _re.match(r"'(?:\\.[^']{0,8}|[^'\\])'", src[i:i + 12])
            if m:  # char literal ('"', '\'', '\u{..}'); otherwise a lifetime
                out.append(m.group(0)); i += len(m.group(0)); continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1; continue
        out.append(c); i += 1
    return "".join(out)


def scan_insecure_calls(roots):
    """[(file, line_no, line)] for INSECURE_CALL_RE matches outside comments."""
    hits = []
    for root in roots:
        for f in sorted(Path(root).rglob("*.rs")):
            code = _strip_rust_comments(f.read_text(errors="replace")).splitlines()
            for k, line in enumerate(code, 1):
                if INSECURE_CALL_RE.search(line):
                    hits.append((str(f), k, line.strip()))
    return hits


def _a4_insecure_selftest():
    import tempfile

    with tempfile.TemporaryDirectory(prefix="a4grep_") as d:
        bad, good = Path(d, "bad"), Path(d, "good")
        bad.mkdir(); good.mkdir()
        (bad / "x.rs").write_text("fn f(b: Builder) -> Builder {\n    b.insecure_skip_relay_cert_verify (true)\n}\n")
        (good / "y.rs").write_text(
            "/// Never calls `insecure_skip_relay_cert_verify(true)`.\n//! insecure_skip_relay_cert_verify(x)\n"
            "// insecure_skip_relay_cert_verify(true);\n  /* insecure_skip_relay_cert_verify(true)\n"
            "     still /* nested */ comment insecure_skip_relay_cert_verify( */\n"
            "fn g() { let s = \"// not a comment\"; let t = \"insecure_skip_relay_cert_verify\"; }\n"
            "fn h<'a>(x: &'a str) -> char { let _r = r#\"a \" // b\"#; '\"' }\n")
        (bad / "z.rs").write_text("fn k() -> char { let q = '\"'; b.insecure_skip_relay_cert_verify(true); q }\n")
        if len(scan_insecure_calls([bad])) != 2:
            raise SystemExit("FAIL a4 insecure grep self-test: real call not detected")
        if scan_insecure_calls([good]):
            raise SystemExit(f"FAIL a4 insecure grep self-test: comment-only mention flagged: {scan_insecure_calls([good])}")
    print("OK insecure grep self-test: real call detected, comment-only mentions ignored", flush=True)


def a4_check_insecure():
    print("\n=== A4.8 relay TLS: no insecure_skip_relay_cert_verify( call in rust/crates/*/src ===", flush=True)
    _a4_insecure_selftest()
    roots = sorted(p for p in (ROOT / "rust" / "crates").glob("*/src") if p.is_dir())
    hits = scan_insecure_calls(roots)
    if hits:
        raise SystemExit("FAIL a4: insecure_skip_relay_cert_verify call(s):\n" + "\n".join(f"  {f}:{k}: {l}" for f, k, l in hits))
    names = ", ".join(r.parent.name for r in roots)
    print(f"OK no insecure_skip_relay_cert_verify( call outside comments in rust/crates/*/src ({names})", flush=True)


def a4_check_default_build(pending):
    """Default (no-feature) builds pull no iroh / iroh-relay and enable only [default]."""
    import shutil

    print("\n=== A4.8 default build: no iroh_pilot / iroh in default features (cargo tree) ===", flush=True)
    cargo = shutil.which("cargo")
    if cargo is None:
        pending.append("default-build check skipped (no cargo)")
        print("SKIP a4 default-build check: cargo not found", flush=True)
        return
    for crate in ("stream-peer", "stream-gateway", "stream-relay"):
        base = [cargo, "tree", "--locked", "-p", crate, "-e", "normal"]
        r = subprocess.run(base + ["--depth", "0", "-f", "{p} [{f}]"], cwd=str(ROOT / "rust"),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        feats = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ""
        if r.returncode != 0 or not feats.endswith("[default]"):
            raise SystemExit(f"FAIL a4: {crate} default features not exactly [default]: {feats or r.stderr.strip()[-300:]}")
        r = subprocess.run(base + ["--prefix", "none"], cwd=str(ROOT / "rust"), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        iroh = [l for l in r.stdout.splitlines() if _re.match(r"^iroh(-relay)? v", l)]
        if r.returncode != 0 or iroh:
            raise SystemExit(f"FAIL a4: {crate} default build pulls {iroh or r.stderr.strip()[-300:]}")
        print(f"OK {crate} default build: features [default], no iroh / iroh-relay in cargo tree", flush=True)


def _free_port_base(width=10):
    import socket

    for base in range(44100, 60000, 100):
        try:
            socks = []
            for port in range(base, base + width):
                s = socket.socket()
                s.bind(("127.0.0.1", port))
                socks.append(s)
            for s in socks:
                s.close()
            return base
        except OSError:
            for s in socks:
                s.close()
    raise SystemExit("FAIL a4: no free SPIKE_PORT_BASE found for the default `all` run")


def a4_check_default_all():
    """Default path unchanged: `all` -> SPIKE_DOD_GREEN with SPIKE_PUBLIC_EGRESS unset."""
    print("\n=== A4.8 default path: run_local_asserts.py all (SPIKE_PUBLIC_EGRESS unset) ===", flush=True)
    env = {k: v for k, v in os.environ.items() if k not in ("SPIKE_PUBLIC_EGRESS",) + STRIPE_SECRET_ENVS}
    env.setdefault("SPIKE_PORT_BASE", str(_free_port_base()))
    p = subprocess.run([sys.executable, "-u", str(Path(__file__).resolve()), "all"], cwd=str(ROOT), env=env,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = {l.strip() for l in p.stdout.splitlines()}
    if p.returncode != 0 or "SPIKE_DOD_GREEN" not in lines:
        raise SystemExit(f"FAIL a4: default `all` not green (rc={p.returncode}):\n{p.stdout[-1500:]}")
    if "OK control stripe mode: mock (/v1/topup/test -> 409 stripe_test_unavailable)" not in lines:
        raise SystemExit("FAIL a4: default `all` did not prove Control mock top-up mode")
    print(f"OK default `all` -> SPIKE_DOD_GREEN (SPIKE_PORT_BASE={env['SPIKE_PORT_BASE']}, "
          "SPIKE_PUBLIC_EGRESS unset, Control in mock top-up mode)", flush=True)


def a4_check_evidence(outs, pending):
    """Refusal / timing / UDP / no-route / screen-order greps over the smoke outputs."""
    print("\n=== A4.8 greps over sub-smoke output ===", flush=True)
    screens = _load_screens()
    p8 = next(c for c in screens["consent"] if c["id"] == "p8_offline")
    for tag, needle, what in A4_REFUSAL_GREPS:
        if not outs.get(tag):
            continue
        if needle not in outs[tag]:
            raise SystemExit(f"FAIL a4: {tag} output lacks {needle!r} ({what})")
        print(f"OK [{tag}] {what}: {needle!r}", flush=True)
    for tag, rx, limit, what in A4_TIMING_GREPS:
        if not outs.get(tag):
            continue
        vals = [float(g) for g in _re.findall(rx, outs[tag]) if g]
        if not vals:
            raise SystemExit(f"FAIL a4: {tag} output lacks timing for {what} (/{rx}/)")
        if max(vals) > limit:
            raise SystemExit(f"FAIL a4: {tag} {what}: {max(vals):g} > {limit:g}")
        print(f"OK [{tag}] {what}: {max(vals):g} <= {limit:g}", flush=True)
    if outs.get("a44"):
        line = p8["reason_lines"]["relay_path_required"]
        if line not in outs["a44"]:
            raise SystemExit("FAIL a4: a44 lacks the exact P8 relay_path_required line from fixtures/screens.json")
        print("OK [a44] watcher trip -> exact P8 relay_path_required line (fixtures/screens.json) on real Peer stderr", flush=True)
    # UDP: Gateway counters 0 under RelayOnly; Peer counters must be real numbers (0) too.
    for tag, anchor, zero in A4_UDP_ZERO_GREPS:
        if not outs.get(tag):
            continue
        hit = [l for l in outs[tag].splitlines() if anchor in l]
        if not hit or not all(zero in l for l in hit):
            raise SystemExit(f"FAIL a4: {tag} gateway UDP counters not 0 under RelayOnly: {hit[:1]}")
        print(f"OK [{tag}] gateway UDP datagram counters 0/0 under RelayOnly", flush=True)
        if not all("'path': 'relay'" in l and "'hole_punch_attempts': 0" in l for l in hit):
            raise SystemExit(f"FAIL a4: {tag} gateway path not relay or hole_punch_attempts != 0: {hit[:1]}")
        print(f"OK [{tag}] gateway path=relay, hole_punch_attempts=0", flush=True)
    # Peer side must log real counters (`udp_counters total=0 ...`); Gateway 0/0 alone isn't enough.
    # `(?<!no )`: a45's evidence line says "no udp_counters_unavailable"; that is not a Peer unavailable line.
    # a44 run B's deliberate watcher trip (fake UDP hook) is a refusal line, not a counter line.
    udp_tags = [t for t in ("a42", "a43", "a44", "a45") if outs.get(t)]
    peer_udp = [m for t in udp_tags for m in _re.findall(r"(?<!no )udp_counters(=unavailable|_unavailable| total=\d+)", outs[t])]
    if udp_tags:
        if any(v.startswith(" total=") and v != " total=0" for v in peer_udp):
            raise SystemExit(f"FAIL a4: Peer UDP datagrams under RelayOnly: {sorted(set(peer_udp))}")
        if any(v.endswith("unavailable") for v in peer_udp):
            pending.append("peer udp_counters unavailable (iroh/metrics)")
            print(f"PENDING [{'/'.join(udp_tags)}] Peer logs udp_counters unavailable (stream-peer lacks "
                  "iroh/metrics); Gateway 0/0 is not enough", flush=True)
        elif not outs.get("a44") or PEER_UDP_ZERO_LINE not in outs["a44"]:
            raise SystemExit(f"FAIL a4: a44 output lacks the Peer-side `{PEER_UDP_ZERO_LINE}` line")
        else:
            print(f"OK [a44] Peer-side `{PEER_UDP_ZERO_LINE}` under RelayOnly "
                  f"({len(peer_udp)} Peer counter lines in {'/'.join(udp_tags)}, all total=0)", flush=True)
    for tag, needle in A4_NOROUTE_GREPS:
        if outs.get(tag):
            if needle not in outs[tag]:
                raise SystemExit(f"FAIL a4: {tag} output lacks {needle!r}")
            print(f"OK [{tag}] netns has no default route: {needle!r}", flush=True)
    # Screen order on the real Peer (a43 phase B): P1 -> P2 -> P9 before the first pilot dial.
    if outs.get("a43"):
        lines = outs["a43"].splitlines()
        def first(s):
            return next((k for k, l in enumerate(lines) if l.strip() == s), None)
        order = [first("[peer] UX_SCREEN p1_isp_ack"), first("[peer] UX_SCREEN p2_consent"),
                 first("[peer] UX_SCREEN p9_allowlist")]
        dial = next((k for k, l in enumerate(lines) if l.strip().startswith("[peer] peer iroh_pilot connected")), None)
        if None in order or dial is None or not (order[0] < order[1] < order[2] < dial):
            raise SystemExit(f"FAIL a4: real Peer screen order not P1 -> P2 -> P9 -> dial (lines {order} dial {dial})")
        print("OK [a43] real Peer: UX_SCREEN p1_isp_ack -> p2_consent -> p9_allowlist before first pilot dial", flush=True)
    if outs.get("a42"):
        if "UX_SCREEN p2_consent" not in outs["a42"] or "UX_SCREEN p9_allowlist" in outs["a42"]:
            raise SystemExit("FAIL a4: a42 (SPIKE_PUBLIC_EGRESS off) should show P1/P2 and no p9_allowlist")
        print("OK [a42] SPIKE_PUBLIC_EGRESS off: P1 -> P2, no p9_allowlist", flush=True)
    if outs.get("a46"):
        c2 = next(c for c in screens["consent"] if c["id"] == "c2_add_funds_test")
        for frag in c2.get("required_copy") or []:
            if frag not in outs["a46"]:
                raise SystemExit(f"FAIL a4: c2_add_funds_test required_copy {frag!r} (fixtures/screens.json) not in a46 output")
        print(f"OK [a46] c2_add_funds_test required_copy from fixtures/screens.json: {c2.get('required_copy')}", flush=True)


def _a4_ux_inner():
    """Inside a throwaway netns (lo + 10.73.0.200 only): start the pilot Peer with no ISP ack (never
    dials), GET /peer/consent/p1, p2, p9, p8; print stderr + the p8 JSON."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="a4ux_"))
    peer_bin = ROOT / "rust" / "target" / "pilot" / "debug" / "stream-peer"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "RUST_LOG": "info", "NO_COLOR": "1",
           "SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_IROH_KEY_PATH": str(tmp / "peer.key"),
           "SPIKE_IROH_BIND": "10.73.0.200:0",
           "SPIKE_GATEWAY_ENDPOINT_ID": "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1",
           "SPIKE_RELAY_ALLOW_URL": "http://10.73.0.254:3340/", "CONTROL_URL": "http://10.73.0.254:8080",
           "SPIKE_PEER_ID": "peer_a4ux", "SPIKE_PEER_ADMIN": "127.0.0.1:9200", "SPIKE_HOST_TIER": "always_on",
           "SPIKE_PUBLIC_EGRESS": "1", "SPIKE_EGRESS_ALLOWLIST": "echo.example.com:443=198.51.100.10"}
    route = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.strip()
    print(f"A4UX_DEFAULT_ROUTE={route or 'none'}", flush=True)
    err = open(tmp / "peer.stderr", "w+")
    p = subprocess.Popen([str(peer_bin)], env=env, stdout=err, stderr=subprocess.STDOUT)
    try:
        wait_deadline = time.monotonic() + health_timeout_s()
        while not http_ok("http://127.0.0.1:9200/health"):
            if p.poll() is not None or time.monotonic() >= wait_deadline:
                err.flush(); err.seek(0)
                raise SystemExit("FAIL a4 ux: pilot Peer /health not up\n" + _proc_state("peer", p, err.read().splitlines()))
            time.sleep(HEALTH_POLL_S)
        for s in ("p1", "p2", "p9"):
            urllib.request.urlopen(f"http://127.0.0.1:9200/peer/consent/{s}", timeout=5).read()
        with urllib.request.urlopen("http://127.0.0.1:9200/peer/consent/p8", timeout=5) as r:
            print("A4UX_P8_JSON=" + json.dumps(json.loads(r.read().decode())), flush=True)
        time.sleep(0.5)
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    err.seek(0)
    print("A4UX_STDERR_BEGIN", flush=True)
    sys.stdout.write(err.read())
    print("A4UX_STDERR_END", flush=True)
    return 0


def _ux_block(lines, header, screen):
    i = next((n for n, l in enumerate(lines) if l.strip() == header), None)
    if i is None:
        return None
    j = next((n for n in range(i + 1, len(lines)) if lines[n].strip() == screen), None)
    if screen is None:
        j = next((n for n in range(i + 1, len(lines)) if lines[n].startswith(("peer ", "UX ", "UX_SCREEN"))), len(lines))
    return None if j is None else "\n".join(lines[i + 1:j])


def a4_ux_copy_check():
    """P1 / P2 / P9 blocks on the pilot Peer's real stderr + P8 copy from its admin API, all checked
    against fixtures/screens.json (required_copy + forbidden_user_facing_substrings), nothing hardcoded."""
    print("\n=== A4.8 UX copy (pilot Peer stderr + P8 API, own netns, no default route) ===", flush=True)
    peer_bin = ROOT / "rust" / "target" / "pilot" / "debug" / "stream-peer"
    if not peer_bin.exists():
        raise SystemExit(f"FAIL a4: {peer_bin} missing (a42 builds it)")
    ns = f"a4ux-{os.getpid()}"
    user = os.environ.get("USER") or subprocess.check_output(["id", "-un"], text=True).strip()
    try:
        for cmd in (["ip", "netns", "add", ns], ["ip", "-n", ns, "link", "set", "lo", "up"],
                    ["ip", "-n", ns, "addr", "add", "10.73.0.200/24", "dev", "lo"]):
            subprocess.run(["sudo", "-n", *cmd], check=True, stdin=subprocess.DEVNULL)
        p = subprocess.run(["sudo", "-n", "ip", "netns", "exec", ns, "sudo", "-n", "-u", user, "env",
                            f"PATH={os.environ.get('PATH', '')}", f"HOME={os.environ.get('HOME', '')}",
                            sys.executable, "-u", str(Path(__file__).resolve()), "_a4_ux_inner"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    finally:
        subprocess.run(["sudo", "-n", "ip", "netns", "del", ns], stdin=subprocess.DEVNULL)
    out = p.stdout
    if p.returncode != 0 or "A4UX_STDERR_BEGIN" not in out:
        raise SystemExit(f"FAIL a4 ux capture rc={p.returncode}: {(out + p.stderr)[-800:]}")
    if "A4UX_DEFAULT_ROUTE=none" not in out:
        raise SystemExit("FAIL a4: ux netns has a default route")
    print("OK [a4ux] netns has no default route", flush=True)
    stderr = out.split("A4UX_STDERR_BEGIN", 1)[1].split("A4UX_STDERR_END", 1)[0].splitlines()
    screens = _load_screens()
    forbidden = screens.get("forbidden_user_facing_substrings", [])
    by_id = {c["id"]: c for c in screens["consent"]}
    blocks = {"p1_isp_ack": _ux_block(stderr, "UX P1", None), "p2_consent": _ux_block(stderr, "UX P2", "UX_SCREEN p2_consent"),
              "p9_allowlist": _ux_block(stderr, "UX P9", "UX_SCREEN p9_allowlist")}
    for sid, block in blocks.items():
        if block is None:
            raise SystemExit(f"FAIL a4: no UX block for {sid} on pilot Peer stderr")
        for frag in by_id[sid].get("required_copy") or []:
            if frag not in block:
                raise SystemExit(f"FAIL a4: {sid} required_copy {frag!r} (fixtures/screens.json) not on Peer stderr")
        bad = [b for b in forbidden if b in block]
        if bad:
            raise SystemExit(f"FAIL a4: forbidden user-facing substring(s) {bad} in Peer {sid} block")
        print(f"OK [a4ux] {sid} required_copy on pilot Peer stderr: {by_id[sid].get('required_copy')}; no forbidden substrings", flush=True)
    p8j = json.loads(next(l for l in out.splitlines() if l.startswith("A4UX_P8_JSON="))[len("A4UX_P8_JSON="):])
    fx = by_id["p8_offline"]
    for k in ("title", "body_lines", "cta"):
        if p8j.get(k) != fx.get(k):
            raise SystemExit(f"FAIL a4: Peer P8 {k} != fixtures/screens.json p8_offline.{k}")
    text = "\n".join([p8j["title"], *p8j["body_lines"], p8j["cta"]])
    for frag in fx.get("required_copy") or []:
        if frag not in text:
            raise SystemExit(f"FAIL a4: p8_offline required_copy {frag!r} missing from Peer P8")
    leaked = [c for c in fx.get("reason_lines", {}) if c in text] + [b for b in forbidden if b in text]
    if leaked:
        raise SystemExit(f"FAIL a4: Peer P8 user-facing text contains {leaked}")
    print("OK [a4ux] Peer P8 title/body/cta == fixtures/screens.json p8_offline; required_copy present; "
          "no raw reason code / forbidden substrings", flush=True)


def _a4_reconnect_selftest():
    """Synthetic cases for peer_reconnect_check / gateway_replaced_check (no smokes)."""
    saved = {k: os.environ.pop(k, None) for k in ("SPIKE_GATEWAY_LOG", "SPIKE_REDIAL_BASE_MS",
                                                    "SPIKE_REDIAL_CAP_MS", "SPIKE_REDIAL_RESET_SECS")}
    P = "peer iroh_pilot "
    B = P + "bound peer_id=peer_92a9d943afd7d7d4 source=endpoint\n"
    C = P + "connected gateway=218c4864 alpn=x via=https://relay\n"
    U = "udp_counters total=0"

    def se(r, by="remote", up=1000):
        return P + f"session_end end_reason={r} closed_by={by} up_ms={up} {U}\n"

    def rd(a, ms, after, reset=False):
        return P + f"redial attempt={a} redial_in_ms={ms}{' reset=1' if reset else ''} after={after}\n"

    PIU, EM = "auth_reject:peer_id_in_use", "auth_reject:endpoint_mismatch"
    GW = "2026-10-18T19:55:35.612679Z  INFO stream_gateway::relay::io: "
    E1, E2 = "7b6748fbd0596e6c" + "a" * 48, "92a9d943afd7d7d4" + "b" * 48
    cases = [
        # (name, logs, kwargs, want status, needle in joined msgs)
        ("legacy ok: remote close -> redial -> reconnect", B + C + se("remote_closed") + rd(1, 1500, "remote_closed") + C, {}, "ok", "OK a4 reconnect"),
        ("legacy pending: no end_reason (pre-fa935fa)", B + C + P + "session_end\n" + C, {}, "pending", "predates fa935fa"),
        ("legacy pending: no up_ms (pre-75ec884)", B + C + P + "session_end end_reason=remote_closed closed_by=remote\n" + rd(1, 1500, "remote_closed") + C, {}, "pending", "predates 75ec884"),
        ("legacy fail: missing closed_by", B + C + P + f"session_end end_reason=remote_closed up_ms=5 {U}\n", {}, "fail", "without end_reason=/closed_by="),
        ("legacy fail: reconnect with no redial", B + C + se("remote_closed") + C, {}, "fail", "no redial attempt= line"),
        ("legacy fail: redial below floor", B + C + se("remote_closed") + rd(1, 999, "remote_closed") + C, {}, "fail", "< floor 1000"),
        ("legacy fail: redial after kill", B + C + se("kill", "local") + rd(1, 1500, "kill"), {}, "fail", "must not redial"),
        ("legacy fail: attempt drop without reset", B + C + se("x") + rd(3, 1500, "x") + C + se("x") + rd(2, 1500, "x"), {}, "fail", "3 -> 2 without reset=1"),
        ("legacy ok: reset=1 attempt=1 after long session", B + C + se("x") + rd(3, 1500, "x") + C + se("x", up=60000) + rd(1, 1500, "x", True) + C, {}, "ok", "reset=1"),
        ("legacy fail: reset=1 after short session", B + C + se("x", up=59999) + rd(1, 1500, "x", True), {}, "fail", "reset=1 but prior session_end up_ms=59999"),
        ("legacy fail: reset=1 with attempt=2", B + C + se("x", up=60000) + rd(2, 1500, "x", True), {}, "fail", "reset=1 with attempt=2"),
        ("legacy ok: new process restarts attempt count", B + C + se("x") + rd(4, 1500, "x") + B + C + se("x") + rd(1, 1500, "x") + C, {}, "ok", "OK a4 reconnect"),
        ("legacy fail: unparsable redial", B + C + se("x") + P + "redial attempt=one\n", {}, "fail", "unparsable redial"),
        ("legacy ok: user stop, no redial", B + C + se("consent_withdrawn", "local") + B + C, {}, "ok", "OK a4 reconnect"),
        # 94d9a6e graceful stop
        ("shutdown ok: log ends in shutdown session_end", B + C + se("shutdown", "local", 90019), {}, "ok", "OK a4 reconnect"),
        ("shutdown ok: blip, reconnect, then graceful stop", B + C + se("read_error", up=170854) + rd(1, 1819, "read_error", True) + C + se("shutdown", "local", 90019), {}, "ok", "reset=1"),
        ("shutdown ok: next process after shutdown", B + C + se("shutdown", "local") + B + C, {}, "ok", "OK a4 reconnect"),
        ("shutdown fail: redial after shutdown", B + C + se("shutdown", "local") + rd(1, 1500, "shutdown"), {}, "fail", "redial after end_reason=shutdown (must not redial)"),
        ("shutdown fail: shutdown closed_by=remote", B + C + se("shutdown", "remote"), {}, "fail", "end_reason=shutdown must be closed_by=local"),
        # 4f28b86 / 4132e68 cap refusals
        ("cap ok: peer_id_in_use -> redial at cap", B + C + se(PIU, up=40) + rd(9, 300000, PIU) + C, {}, "ok", "cap_redial=1"),
        ("cap fail: peer_id_in_use short backoff", B + C + se(PIU, up=40) + rd(1, 2000, PIU) + C, {}, "fail", f"redial_in_ms=2000 after {PIU} < cap 300000"),
        ("cap ok: endpoint_mismatch -> redial at cap", B + C + se(EM, up=12) + rd(9, 300000, EM), {}, "ok", "cap_refusal=1"),
        ("cap fail: endpoint_mismatch short backoff", B + C + se(EM, up=12) + rd(2, 299999, EM), {}, "fail", f"redial_in_ms=299999 after {EM} < cap 300000"),
        ("cap fail: redial after= does not name the refusal", B + C + se(PIU) + rd(9, 300000, "remote_closed"), {}, "fail", f"want after={PIU}"),
        ("cap fail: refusal logged closed_by=local", B + C + se(EM, "local") + rd(9, 300000, EM), {}, "fail", "must be closed_by=remote"),
        ("cap ok: SPIKE_REDIAL_CAP_MS override", B + C + se(PIU) + rd(3, 8000, PIU) + C, {"cap_ms": 8000}, "ok", "cap_ms=8000"),
        ("cap ok: reset=1 on cap redial after long session (attempt = cap step)", B + C + se(PIU, up=61000) + rd(9, 300000, PIU, True), {}, "ok", "reset=1"),
        ("cap ok: normal backoff resumes after the cap redial", B + C + se(PIU) + rd(9, 300000, PIU) + C + se("remote_closed") + rd(10, 150000, "remote_closed") + C, {}, "ok", "cap_redial=1"),
        # Gateway log (ce80867 relay/io.rs lines)
        ("gw fail: replaced across endpoints", B + C, {"gateway_log": GW + f"peer online peer_demo endpoint={E1}\n" + GW + f"peer online peer_demo endpoint={E2}\n"}, "fail", "`replaced` across endpoints for peer_demo"),
        ("gw ok: same-endpoint replaced", B + C, {"gateway_log": GW + f"peer online peer_demo endpoint={E1}\n" + GW + f"peer online peer_demo endpoint={E1}\n"}, "ok", "replaced_same_endpoint=1 replaced_cross_endpoint=0"),
        ("gw ok: offline then a new endpoint claims the free peer_id", B + C, {"gateway_log": GW + f"peer online p endpoint={E1}\n" + GW + "peer offline p\n" + GW + f"peer online p endpoint={E2}\n"}, "ok", "replaced_cross_endpoint=0"),
        ("gw ok: refused peer_id_in_use, two peer_ids both online", B + C, {"gateway_log": GW + f"peer online peer_demo endpoint={E1}\n" + "\x1b[33m WARN\x1b[0m " + f"peer_id_in_use peer_demo endpoint={E2}\n" + GW + f"peer online peer_92a9d943afd7d7d4 endpoint={E2}\n"}, "ok", "online=2 replaced_same_endpoint=0 replaced_cross_endpoint=0 peer_id_in_use=1"),
        ("gw ok: Gateway restart clears the table", B + C, {"gateway_log": GW + f"peer online p endpoint={E1}\n" + GW + "gateway HTTP on 0.0.0.0:8080\n" + GW + f"peer online p endpoint={E2}\n"}, "ok", "replaced_cross_endpoint=0"),
        ("gw fail: cross-endpoint replace FAILs even on a pre-fa935fa Peer image", B + C + P + "session_end\n", {"gateway_log": GW + f"peer online p endpoint={E1}\n" + GW + f"peer online p endpoint={E2}\n"}, "fail", "across endpoints"),
        ("gw skip: no Gateway log -> INFO", B + C, {}, "ok", "replaced-across-endpoints check skipped"),
    ]
    n_ok = 0
    try:
        for name, logs, kw, want, needle in cases:
            st, msgs, _ = peer_reconnect_check({"a44": logs}, **kw)
            text = "\n".join(msgs)
            if st != want or needle not in text:
                raise SystemExit(f"FAIL a4 reconnect selftest [{name}]: got {st}, want {want} with {needle!r}\n{text}")
            n_ok += 1
        # SPIKE_GATEWAY_LOG path input
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(GW + f"peer online p endpoint={E1}\n" + GW + f"peer online p endpoint={E2}\n")
        os.environ["SPIKE_GATEWAY_LOG"] = f.name
        st, msgs, _ = peer_reconnect_check({"a44": B + C})
        os.unlink(f.name)
        if st != "fail" or "across endpoints" not in "\n".join(msgs):
            raise SystemExit(f"FAIL a4 reconnect selftest [SPIKE_GATEWAY_LOG file]: got {st}\n" + "\n".join(msgs))
        n_ok += 1
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print(f"A4_RECONNECT_SELFTEST_OK {n_ok}/{len(cases) + 1}", flush=True)


def _a4_image_id_selftest():
    """Synthetic cases for a45_image_id_check / SPIKE_PEER_IMAGE_ID lists (no docker)."""
    CFG = "sha256:cccbe8a8896b959cc09aaa8383cf13bd5db22d09c7d8111aef3170b043ec725d"
    MAN = "sha256:7591e580" + "0" * 56  # placeholder manifest digest (real one not posted yet)
    OLD = "sha256:5fd94bdb" + "1" * 56
    out_cfg = f"A45_IMAGE_ID={CFG}\nA4.5_PEER_PACKAGE_GREEN\n"                       # classic store
    out_man = f"A45_IMAGE_ID={MAN}\nA45_IMAGE_CONFIG_ID={CFG}\nA4.5_PEER_PACKAGE_GREEN\n"  # containerd store
    out_man_only = f"A45_IMAGE_ID={MAN}\nA4.5_PEER_PACKAGE_GREEN\n"                  # ctr lookup failed
    cases = [
        # (name, a45 out, SPIKE_PEER_IMAGE_ID, want ok, needle)
        ("bare id ok (back compat)", out_cfg, CFG, True, "matches SPIKE_PEER_IMAGE_ID (id)"),
        ("bare id without sha256:, upper case ok", out_cfg, CFG[7:].upper(), True, "(id)"),
        ("bare id mismatch fails", out_cfg, OLD, False, "matches none of"),
        ("bare config id vs containerd A45_IMAGE_CONFIG_ID ok", out_man, CFG, True, "(id)"),
        ("named list: config matches", out_cfg, f"config={CFG},manifest={MAN}", True, "(config)"),
        ("named list: manifest matches A45_IMAGE_ID", out_man_only, f"config={CFG},manifest={MAN}", True, "(manifest)"),
        ("named list: both match", out_man, f"config={CFG}, manifest={MAN}", True, "(config/manifest)"),
        ("bare list ok", out_man_only, f"{CFG},{MAN}", True, "(id)"),
        ("named list: none match fails", out_cfg, f"config={OLD},manifest={MAN}", False, "matches none of"),
        ("list set but no A45_IMAGE_ID line fails", "A4.5_PEER_PACKAGE_GREEN\n", f"config={CFG}", False, "no A45_IMAGE_ID line"),
        ("malformed entry fails", out_cfg, f"config={CFG},manifest=", False, "is not a sha256 image ID"),
        ("only commas fails", out_cfg, ",,", False, "lists no image ID"),
        ("unset -> INFO", out_cfg, "", True, "INFO a45 image id"),
    ]
    for name, out, want, wok, needle in cases:
        ok, msg = a45_image_id_check(out, want)
        if ok != wok or needle not in msg:
            raise SystemExit(f"FAIL a4 image-id selftest [{name}]: got {ok} {msg!r}, want {wok} with {needle!r}")
    print(f"A4_IMAGE_ID_SELFTEST_OK {len(cases)}/{len(cases)}", flush=True)


def _a4_selftest():
    """Offline self-test of the a4 grep helpers (no smokes): insecure grep + comment stripping +
    reconnect contract + a45 image-ID pin lists."""
    _a4_insecure_selftest()
    _a4_reconnect_selftest()
    _a4_image_id_selftest()
    print("A4_SELFTEST_OK", flush=True)
    return 0


def run_a4_pilot():
    """A4.8 / TOM-28: A4.0-A4.7 + cross-cutting checks -> A4_PILOT_GREEN (never with a skip/pending)."""
    import shutil

    strict = _a4_strict()
    pending: list[str] = []
    outs: dict[str, str] = {}

    def note(tag, status):
        if status == "skip":
            pending.append(f"{tag} skipped")
        elif status == "pending":
            pending.append(f"{tag} (scripts/{A45_SCRIPT}.py not on main)" if tag == "a45" else f"{tag} pending")

    for tag, fn in (("a40", run_a40_compose_guard), ("a41", run_a41_pilot_guard),
                    ("a42", run_a42_self_relay), ("a43", run_a43_gateway_egress),
                    ("a44", run_a44_peer_egress), ("a45", run_a45_peer_package),
                    ("a46", run_a46_stripe_test_topup), ("a47", run_a47_copy_fixtures)):
        status, outs[tag] = fn()
        note(tag, status)
        if tag == "a44" and status == "pass":
            final = A44_FINAL_MARKER in {l.strip() for l in outs[tag].splitlines()}
            if not final:
                pending.append(f"a44 final marker {A44_FINAL_MARKER} (only A4.4_PEER_EGRESS_PART1_GREEN)")
                print(f"PENDING a44: a4 needs {A44_FINAL_MARKER}; got PART1 only", flush=True)
        # a45: pilot devices are x86_64, so amd64 is the only shipped peer-pilot image.
        if tag == "a45" and status == "pass":
            ok, msg = a45_image_id_check(outs[tag], os.environ.get("SPIKE_PEER_IMAGE_ID"))
            if not ok:
                raise SystemExit(msg)
            print(msg, flush=True)
        if tag == "a44" and status == "pass":  # a42/a43/a44 Peer logs captured by now
            rs, rmsgs, _ = peer_reconnect_check({t: outs[t] for t in ("a42", "a43", "a44") if outs.get(t)})
            for m_ in rmsgs:
                print(m_, flush=True)
            if rs == "fail":
                raise SystemExit(rmsgs[0])
            if rs == "pending":
                pending.append(rmsgs[0].split("(", 1)[1].split(")", 1)[0])
        if tag == "a46" and status == "pass":
            lane_b = next((l.split("A46_LANE_B=", 1)[1] for l in outs[tag].splitlines() if "A46_LANE_B=" in l), "?")
            print(f"OK a46 green; lane B ({lane_b}) not required", flush=True)

    a4_check_insecure()
    a4_check_default_build(pending)
    a4_check_evidence(outs, pending)
    if outs.get("a42"):
        a4_ux_copy_check()
    else:
        pending.append("UX copy check skipped (needs a42's pilot Peer build + sudo)")

    print("\n=== A4.8 banned-word / UX checks (existing Rust Peer tests, unchanged) ===", flush=True)
    cargo = shutil.which("cargo")
    if cargo is None:
        pending.append("Rust ux tests skipped (no cargo)")
        print("SKIP a4 Rust ux tests: cargo not found", flush=True)
    else:
        p = subprocess.run([cargo, "test", "--locked", "-q", "-p", "stream-peer", "--", "ux::"],
                           cwd=str(ROOT / "rust"), stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise SystemExit("FAIL a4: stream-peer ux:: tests")
        print("PASS stream-peer ux:: tests (fixture drift, forbidden copy, screen order)", flush=True)

    a4_check_default_all()

    if pending:
        print(f"\nA4_PILOT_NOT_GREEN pending: {'; '.join(pending)}", flush=True)
        if strict:
            raise SystemExit(1)
        return 3
    print("\nA4_PILOT_GREEN", flush=True)
    return 0



def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    stripe_live = scrub_stripe_env(mode)
    if mode in ("a11", "iroh-loopback", "iroh_loopback"):
        run_iroh_loopback()
        return 0
    if mode in ("a12", "alpha1-peer", "peer-alpha1"):
        run_peer_alpha1()
        return 0
    if mode in ("a13", "mock-topup", "mock_topup"):
        run_a13_mock_topup()
        return 0
    if mode in ("a30", "private-guard", "private_guard"):
        run_a30_private_guard()
        return 0
    if mode in ("a40", "compose-guard", "compose_guard"):
        run_a40_compose_guard()
        return 0
    if mode in ("a42", "self-relay", "self_relay"):
        run_a42_self_relay()
        return 0
    if mode in A46_MODES:
        run_a46_stripe_test_topup(stripe_live)
        return 0
    if mode in ("a43", "gateway-egress", "gateway_egress"):
        run_a43_gateway_egress()
        return 0
    if mode in ("a44", "peer-egress", "peer_egress"):
        run_a44_peer_egress()
        return 0
    if mode in ("a41", "pilot-guard", "pilot_guard"):
        run_a41_pilot_guard()
        return 0
    if mode in ("a31", "gateway-endpoint", "gateway_endpoint"):
        run_a31_gateway_endpoint()
        return 0
    if mode in ("a32", "peer-dial", "peer_dial"):
        run_a32_peer_dial()
        return 0
    if mode == "_a3_ux_inner":
        return _a3_ux_inner()
    if mode in ("a34", "multinode", "multi-node", "multi_node"):
        if not (ROOT / "scripts" / "a34_multinode_smoke.py").exists():
            print("PENDING a34 (scripts/a34_multinode_smoke.py not on main)", flush=True)
            return 1 if os.environ.get("SPIKE_A3", "").strip() == "1" or os.environ.get("SPIKE_IMPL", "") == "rust" else 3
        run_a34_multinode()
        return 0
    if mode in ("a3", "iroh-local", "iroh_local"):
        return run_a3_iroh_local()
    if mode in ("a45", "peer-package", "peer_package"):
        status, _ = run_a45_peer_package()
        if status == "pending":
            return 1 if _a4_strict() else 3
        return 0
    if mode in ("a47", "copy-fixtures", "copy_fixtures"):
        run_a47_copy_fixtures()
        return 0
    if mode in ("a4", "pilot", "alpha4"):
        return run_a4_pilot()
    if mode == "_a4_ux_inner":
        return _a4_ux_inner()
    if mode == "_a4_selftest":
        return _a4_selftest()
    if mode in ("a33", "ticket-bind", "ticket_bind"):
        run_a33_ticket_bind()
        return 0
    env = start_stack()
    assert_control_mock_topup(spike_ports.urls(env)[0])
    grace = ["grace-stop", "assert-grace-ledger"]
    gates = [
        "gate-denylist",
        "gate-freeze",
        "gate-peer-ack",
        "gate-peer-egress",
        "gate-peer-kill",
        "gate-auth-ticket",
        "gate-strict-unavailable",
        "gate-aup",
        "gate-cashout",
    ]
    if mode == "grace":
        cmds = grace
    elif mode == "gates":
        cmds = gates
    elif mode in ("alpha1", "all+a12"):
        cmds = grace + gates
    else:
        cmds = grace + gates
    for cmd in cmds:
        run_cli(env, cmd)
    print("\nSPIKE_DOD_GREEN", flush=True)
    if mode in ("alpha1", "all+a12"):
        cleanup()
        procs.clear()
        run_iroh_loopback()
        run_peer_alpha1()
        print("\nALPHA1_ASSERT_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        cleanup()
