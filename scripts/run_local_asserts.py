#!/usr/bin/env python3
"""Run spike DoD asserts locally (no Docker). Starts stack once, runs client-cli commands."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spike_ports  # noqa: E402  SPIKE_PORT_BASE + explicit-env-wins resolution
CLI = ROOT / "client-cli" / "main.py"
procs: list[subprocess.Popen] = []


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
        procs.append(
            subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        )
        time.sleep(0.4)
    control, gateway, peer = spike_ports.urls(env)
    print(f"stack: control={control} gateway={gateway} peer={peer}", flush=True)
    for url in (control + "/health", gateway + "/health", peer + "/health"):
        for _ in range(50):
            if http_ok(url):
                break
            time.sleep(0.1)
        else:
            raise SystemExit(f"failed to start {url}")
    # wait peer online
    import urllib.error
    for _ in range(50):
        try:
            with urllib.request.urlopen(gateway + "/gw/peers", timeout=2) as r:
                peers = json.loads(r.read().decode()).get("peers", [])
                if any(p.get("peer_id") == "peer_demo" for p in peers):
                    return env
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit("peer not on gateway")


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

    Returns ("pass", combined_output) or ("skip", ""); failures raise SystemExit."""
    import shutil

    print(f"\n=== {script} ({title}) ===", flush=True)
    impl = os.environ.get("SPIKE_IMPL", "python").strip().lower() or "python"
    strict = impl == "rust" or os.environ.get(strict_env, "").strip() == "1"
    why = "SPIKE_IMPL=rust" if impl == "rust" else f"{strict_env}=1"

    def unavailable(reason):
        if strict:
            raise SystemExit(f"FAIL {tag}: {reason} but {why}")
        print(f"SKIP {tag} {script}: {reason} (set {strict_env}=1 or SPIKE_IMPL=rust to make this fatal)", flush=True)
        return "skip", ""

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

    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / f"{script}.py")],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    sys.stdout.write(p.stdout)
    sys.stdout.flush()
    green = any(l.strip() == marker for l in p.stdout.splitlines())
    if p.returncode != 0 or not green:
        raise SystemExit(f"FAIL {script} (rc={p.returncode}, green_marker={green})")
    print(f"PASS {script}", flush=True)
    return "pass", p.stdout


def run_a40_compose_guard():
    """A4.0 compose Control mints iroh_local tickets (Platform's scripts/a40_compose_guard_smoke.py).
    Needs sudo docker; SKIP without it unless SPIKE_A4=1 / SPIKE_IMPL=rust."""
    return _run_netns_smoke("a40", "A4.0", "a40_compose_guard_smoke", "A4.0_COMPOSE_GUARD_GREEN",
                            needs=("sudo", "docker"), strict_env="SPIKE_A4")


def run_a41_pilot_guard():
    """A4.1 pilot guard: Rust stream-proto::guard::pilot tests (default + feature a4_local) and the
    Python mirror's pilot self-test. No stack, no network (resolver injected).
    Same skip/fail as a30, and missing cargo is also fatal under SPIKE_A4=1."""
    import shutil

    print("\n=== A4.1 pilot guard (rust stream-proto::guard::pilot) ===", flush=True)
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
                cwd=str(ROOT / "rust"), stdin=subprocess.DEVNULL,
            )
            if p.returncode != 0:
                raise SystemExit(f"FAIL a41 rust pilot guard tests {' '.join(extra)}".rstrip())
        print("PASS a41 rust pilot guard tests (default + a4_local)", flush=True)

    print("\n=== A4.1 pilot guard (python spike_private_guard --pilot) ===", flush=True)
    p = subprocess.run(
        [sys.executable, "-u", str(ROOT / "scripts" / "spike_private_guard.py"), "--pilot"],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    sys.stdout.write(p.stdout)
    if p.returncode != 0 or "A4.1_PY_PILOT_SELFTEST_OK" not in p.stdout.split():
        raise SystemExit("FAIL a41 python pilot self-test")
    print("PASS a41 python pilot self-test", flush=True)
    print("\nA4.1_PILOT_GUARD_GREEN", flush=True)


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
        for _ in range(100):
            if http_ok("http://127.0.0.1:9200/health"):
                break
            time.sleep(0.1)
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


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
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
    if mode in ("a33", "ticket-bind", "ticket_bind"):
        run_a33_ticket_bind()
        return 0
    env = start_stack()
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
