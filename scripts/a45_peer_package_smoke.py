#!/usr/bin/env python3
"""A4.5 pilot Peer package smoke -> A4.5_PEER_PACKAGE_GREEN.

Builds rust/crates/stream-peer/Dockerfile.pilot with `sudo -n docker build` and checks
the image (amd64 only for now):
  * runs as uid 10001; defaults SPIKE_TRANSPORT=iroh_pilot, key + budget in /data,
    admin 127.0.0.1:9200; no consent presets baked in (user goes through P1/P2/P9)
  * iroh_pilot behavior: refuses start without SPIKE_RELAY_ALLOW_URL (a4_refuse_relay_config)
  * iroh metrics on: the Peer logs `udp_counters total=0 ...` (all four 0) after bind and never
    `udp_counters_unavailable`
  * no a4_local lane: the on-box relay 10.73.0.254 is refused (Lane is one cfg in stream-proto,
    so this also covers the TEST-NET-2 exceptions), and the a4_local-only marker string
    `SPIKE_A4_FAKE_UDP_DATAGRAMS` is absent from the binary; no `insecure_skip_relay_cert_verify`
  * no key in image layers: no peer_iroh_key / peer_egress_budget file in any layer, /data empty
  * --network none: admin stays up (HEALTHCHECK healthy, /health ok), key 0600 uid 10001 in
    the /data volume, same endpoint ID after a container restart, budget file 0600
Never reaches the public internet at test time (all runs are --network none; the build itself
pulls crates like the compose images do). Cleans up its containers + volume; keeps the image
image only if asked (see below).
Image selection (a45 never moves the shared `peer-pilot:amd64` tag):
  * SPIKE_PEER_IMAGE=<ref or sha256 ID>: check that already-loaded image, no build. Requires
    SPIKE_PEER_IMAGE_ID (the pin it must match).
  * otherwise build to SPIKE_A45_TAG (default temporary `peer-pilot:a45-<pid>`), and remove that
    tag at the end unless SPIKE_A45_KEEP=1. SPIKE_A45_TAG=peer-pilot:amd64 is refused.
Prints A45_IMAGE_ID=<id>. If SPIKE_PEER_IMAGE_ID is set, FAILs unless the image is that ID
(or, on Docker 29 containerd store, its config digest is).
SKIP (exit 0) when docker or `sudo -n` is unavailable, unless SPIKE_A4=1 (then FAIL).
Docker is only ever talked to over the local unix socket (`-H unix:///var/run/docker.sock`);
the smoke refuses to run when DOCKER_HOST points anywhere else (e.g. a tcp:// engine).
Admin calls inside the container use the image's `peer-admin` (bash /dev/tcp, no curl).
"""
import json, os, re, shutil, subprocess, sys, tarfile, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_TAG = "peer-pilot:amd64"
PREBUILT = os.environ.get("SPIKE_PEER_IMAGE", "").strip()
TAG = PREBUILT or os.environ.get("SPIKE_A45_TAG", "").strip() or f"peer-pilot:a45-{os.getpid()}"
KEEP = bool(PREBUILT) or os.environ.get("SPIKE_A45_KEEP", "").strip() == "1"
# Optional pin: approved image ID (sha256:...). When set, a45 FAILs unless the built/loaded image
# is that ID (directly, or as the config digest under Docker 29's containerd store).
WANT_ID = os.environ.get("SPIKE_PEER_IMAGE_ID", "").strip()
DOCKER_SOCK = "unix:///var/run/docker.sock"
DOCKER = ["sudo", "-n", "docker", "-H", DOCKER_SOCK]
NAME = f"a45-peer-{os.getpid()}"
VOL = f"a45-data-{os.getpid()}"
GW = "a" * 64
GOOD_ENV = ["-e", "SPIKE_RELAY_ALLOW_URL=https://relay.pilot.example/",
            "-e", f"SPIKE_GATEWAY_ENDPOINT_ID={GW}",
            "-e", "SPIKE_EGRESS_ALLOWLIST=api.pilot.example:443"]


def fail(msg):
    print(f"FAIL a45 peer package: {msg}", flush=True)
    sys.exit(1)


def preflight():
    dh = os.environ.get("DOCKER_HOST", "")
    if dh and not dh.startswith("unix://"):
        fail(f"DOCKER_HOST={dh!r} is not a unix:// socket; refusing (only the local engine is ours)")
    strict = os.environ.get("SPIKE_A4", "").strip() == "1"
    def unavailable(reason):
        if strict:
            fail(f"{reason} (SPIKE_A4=1)")
        print(f"SKIP a45 peer package smoke: {reason} (set SPIKE_A4=1 to make this fatal)", flush=True)
        return False
    if not shutil.which("docker"):
        return unavailable("docker not installed")
    r = subprocess.run(DOCKER + ["version", "--format", "{{.Server.Version}}"], capture_output=True, text=True)
    if r.returncode != 0:
        return unavailable(f"`sudo -n docker` unavailable: {r.stderr.strip()[:200]}")
    return True


def d(*args, check=True, timeout=120):
    r = subprocess.run(DOCKER + list(args), capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        fail(f"docker {' '.join(args[:3])} rc={r.returncode}: {r.stderr.strip()[-400:]}")
    return r


def run_once(*extra, cmd=(), timeout=30):
    """Foreground run, --network none; returns (rc, stdout+stderr)."""
    r = d("run", "--rm", "--network", "none", *extra, TAG, *cmd, check=False, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def wait_log(pattern, deadline_s=20):
    end = time.time() + deadline_s
    while time.time() < end:
        logs = d("logs", NAME, check=False)
        m = re.findall(pattern, logs.stdout + logs.stderr)
        if m:
            return m
        time.sleep(0.5)
    return []


def check_layers():
    with tempfile.TemporaryDirectory(prefix="a45-save-") as tmp:
        tar_path = os.path.join(tmp, "img.tar")
        with open(tar_path, "wb") as f:
            r = subprocess.run(DOCKER + ["save", TAG], stdout=f, stderr=subprocess.PIPE, timeout=300)
        if r.returncode != 0:
            fail(f"docker save: {r.stderr.decode()[-300:]}")
        bad = []
        with tarfile.open(tar_path) as outer:
            for m in outer.getmembers():
                if not m.isfile():
                    continue
                fo = outer.extractfile(m)
                try:
                    with tarfile.open(fileobj=fo) as layer:
                        for n in layer.getnames():
                            base = n.rsplit("/", 1)[-1]
                            if base in ("peer_iroh_key", "peer_egress_budget", ".peer_egress_budget") or base.startswith(".peer_iroh_key"):
                                bad.append(n)
                except tarfile.ReadError:
                    continue  # manifest/config json
    if bad:
        fail(f"key/budget file baked into image layers: {bad}")
    hist = d("history", "--no-trunc", "--format", "{{.CreatedBy}}", TAG).stdout
    if re.search(r"(COPY|ADD)[^\n]*peer_iroh_key", hist):
        fail("image history copies a key file")
    print("a45: no key/budget file in any image layer", flush=True)


def check_image_id(shown):
    print(f"A45_IMAGE_ID={shown}", flush=True)
    if not WANT_ID:
        return
    if shown == WANT_ID:
        print(f"a45: image ID matches SPIKE_PEER_IMAGE_ID", flush=True)
        return
    # Docker 29+ containerd store shows the manifest digest; the approved ID is the config digest.
    r = subprocess.run(["sudo", "-n", "ctr", "-n", "moby", "content", "get", shown],
                       capture_output=True, text=True, timeout=30)
    m = re.search(r'"config":\{[^}]*"digest":"(sha256:[0-9a-f]{64})"', r.stdout or "")
    if m:
        print(f"A45_IMAGE_CONFIG_ID={m.group(1)}", flush=True)
        if m.group(1) == WANT_ID:
            print("a45: image config ID matches SPIKE_PEER_IMAGE_ID", flush=True)
            return
    fail(f"image ID {shown} != SPIKE_PEER_IMAGE_ID {WANT_ID}")


def main():
    if not preflight():
        return 0
    if PREBUILT:
        if not WANT_ID:
            fail("SPIKE_PEER_IMAGE set without SPIKE_PEER_IMAGE_ID; refusing to check an unpinned image")
        d("image", "inspect", TAG)
        print(f"a45: checking pre-loaded image {TAG} (no build)", flush=True)
        return checks()
    if TAG == SHARED_TAG:
        fail(f"refusing to build to the shared tag {SHARED_TAG}; use SPIKE_A45_TAG=<other> or SPIKE_PEER_IMAGE")
    t0 = time.time()
    try:
        d("build", "-q", "-f", "rust/crates/stream-peer/Dockerfile.pilot", "-t", TAG, "rust/", timeout=3600)
        print(f"a45: built {TAG} in {time.time() - t0:.0f}s", flush=True)
        return checks()
    finally:
        if not KEEP:
            d("rmi", TAG, check=False)  # untags only; layers stay in cache
            print(f"a45: removed temporary tag {TAG} (SPIKE_A45_KEEP=1 to keep)", flush=True)


def checks():

    info = json.loads(d("image", "inspect", TAG).stdout)[0]
    check_image_id(info["Id"])
    cfg = info["Config"]
    env = dict(e.split("=", 1) for e in cfg.get("Env") or [])
    if cfg.get("User") != "10001:10001":
        fail(f"image User={cfg.get('User')!r}")
    want = {"SPIKE_TRANSPORT": "iroh_pilot", "SPIKE_IROH_KEY_PATH": "/data/peer_iroh_key",
            "SPIKE_EGRESS_BUDGET_PATH": "/data/peer_egress_budget", "SPIKE_PEER_ADMIN": "127.0.0.1:9200"}
    for k, v in want.items():
        if env.get(k) != v:
            fail(f"default {k}={env.get(k)!r}, want {v!r}")
    presets = [k for k in env if k in ("SPIKE_ISP_ACK_VERSION", "SPIKE_P2_CONSENT", "SPIKE_P9_ACK", "SPIKE_PUBLIC_EGRESS")]
    if presets:
        fail(f"consent/egress presets baked in: {presets}")
    if "/data" not in (cfg.get("Volumes") or {}):
        fail("no VOLUME /data")
    if not (cfg.get("Healthcheck") or {}).get("Test"):
        fail("no HEALTHCHECK")
    if info.get("Architecture") != "amd64":
        fail(f"arch={info.get('Architecture')}")
    rc, out = run_once("--entrypoint", "id", cmd=("-u",))
    if rc != 0 or out.strip() != "10001":
        fail(f"id -u -> {out.strip()!r}")
    print("a45: uid 10001, defaults ok, no presets, VOLUME /data, HEALTHCHECK", flush=True)

    rc, out = run_once()
    if rc == 0 or "a4_refuse_relay_config:<unset>" not in out:
        fail(f"no SPIKE_RELAY_ALLOW_URL: rc={rc} out={out[-300:]!r}")
    rc, out = run_once("-e", "SPIKE_RELAY_ALLOW_URL=http://10.73.0.254:3340/")
    if rc == 0 or "a4_refuse_relay_config:http://10.73.0.254:3340/" not in out:
        fail(f"a4_local relay lane accepted (rc={rc}): {out[-300:]!r}")
    rc, out = run_once("--entrypoint", "sh", cmd=("-c",
                       "grep -c -a insecure_skip_relay_cert_verify /usr/local/bin/stream-peer; "
                       "grep -c -a SPIKE_A4_FAKE_UDP_DATAGRAMS /usr/local/bin/stream-peer; ls -A /data | wc -l"))
    counts = out.split()
    if counts[:3] != ["0", "0", "0"]:
        fail(f"binary/data probe (insecure_skip_relay_cert_verify, SPIKE_A4_FAKE_UDP_DATAGRAMS, /data files) = {counts}")
    print("a45: refuses without relay URL; on-box relay refused (Pilot lane); no a4_local marker "
          "SPIKE_A4_FAKE_UDP_DATAGRAMS; no insecure_skip_relay_cert_verify; /data empty in image", flush=True)

    check_layers()

    try:
        d("volume", "create", VOL)
        d("run", "-d", "--name", NAME, "--network", "none", "-v", f"{VOL}:/data", *GOOD_ENV, TAG)
        ids = wait_log(r"peer iroh_endpoint_id=([0-9a-f]{64})")
        if not ids:
            fail("no endpoint id logged: " + d("logs", NAME, check=False).stderr[-400:])
        if not wait_log(r"peer admin on 127\.0\.0\.1:9200 transport=iroh_pilot"):
            fail("admin not on 127.0.0.1:9200")
        id1 = ids[0]
        health = ""
        for _ in range(60):
            health = d("inspect", "-f", "{{.State.Health.Status}}", NAME).stdout.strip()
            if health == "healthy":
                break
            time.sleep(0.5)
        if health != "healthy":
            fail(f"HEALTHCHECK status={health}")
        h = json.loads(d("exec", NAME, "peer-admin", "GET", "/health").stdout)
        if not h.get("ok") or h.get("transport") != "iroh_pilot" or h.get("ux_screen") != "P1" or h.get("online"):
            fail(f"/health unexpected: ok={h.get('ok')} transport={h.get('transport')} screen={h.get('ux_screen')} online={h.get('online')}")
        # Metrics on in the pilot binary (iroh/metrics, c219b65): after bind the Peer logs real
        # endpoint UDP counters, all 0 (relay-only, --network none), never `unavailable`.
        udp = wait_log(r"peer iroh_pilot (udp_counters[^\n]*)")
        time.sleep(2)
        logs = d("logs", NAME, check=False)
        alll = logs.stdout + logs.stderr
        if not udp or udp[0].strip() != "udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0 recv_v6=0":
            fail(f"pilot binary UDP counter line = {udp[:1]} (want all four 0 from iroh metrics)")
        if "udp_counters_unavailable" in alll or "udp_counters=unavailable" in alll or "relay_path_required" in alll:
            fail("pilot binary reports udp_counters unavailable / relay_path_required:\n" + alll[-600:])
        print(f"a45: iroh metrics on: '{udp[0].strip()}', Peer counters present", flush=True)
        k = d("exec", NAME, "peer-admin", "POST", "/peer/kill", check=False)
        hk = json.loads(d("exec", NAME, "peer-admin", "GET", "/health").stdout)
        if k.returncode != 0 or not hk.get("kill_requested"):
            fail(f"peer-admin POST /peer/kill: rc={k.returncode} kill_requested={hk.get('kill_requested')}")
        d("exec", NAME, "peer-admin", "POST", "/peer/resume")
        print("a45: peer-admin GET /health, POST /peer/kill + /peer/resume work inside the container", flush=True)
        st = d("exec", NAME, "stat", "-c", "%a %u %s", "/data/peer_iroh_key").stdout.split()
        if st != ["600", "10001", "32"]:
            fail(f"key file stat={st}")
        print(f"a45: --network none up, healthy, /health ok (P1 first), key 0600 uid 10001, id={id1[:12]}…", flush=True)

        d("restart", "-t", "10", NAME, timeout=60)
        time.sleep(1)
        ids = wait_log(r"peer iroh_endpoint_id=([0-9a-f]{64})")
        if len(ids) < 2 or ids[-1] != id1 or len(set(ids)) != 1:
            fail(f"endpoint id changed across restart: {ids}")
        d("stop", "-t", "10", NAME, timeout=60)
        code = d("inspect", "-f", "{{.State.ExitCode}}", NAME).stdout.strip()
        rc, out = run_once("-v", f"{VOL}:/data", "--entrypoint", "stat", cmd=("-c", "%n %a %u",
                           "/data/peer_iroh_key", "/data/peer_egress_budget"))
        lines = sorted(out.strip().splitlines())
        if lines != ["/data/peer_egress_budget 600 10001", "/data/peer_iroh_key 600 10001"]:
            fail(f"volume files after stop: {lines}")
        print(f"a45: same endpoint id after restart; stop exit={code}; budget + key 0600 in volume", flush=True)
    finally:
        d("rm", "-f", NAME, check=False)
        d("volume", "rm", "-f", VOL, check=False)

    digest = info["Id"]
    size_mb = info["Size"] / 1e6
    print(f"a45: image {TAG} id={digest} size={size_mb:.1f}MB arch={info.get('Architecture')}", flush=True)
    print("A4.5_PEER_PACKAGE_GREEN", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
