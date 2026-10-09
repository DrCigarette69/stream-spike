#!/usr/bin/env python3
"""A3.0 private-address guard — Python mirror of rust/crates/stream-proto/src/guard.rs.

Std lib only (``ipaddress``). Same rules, env var and reason strings as the Rust module;
spec in docs/ALPHA3_IROH.md (Hard rules + A3.3 ticket format).

Public API (Control / A3.3 ``direct_addrs`` validation):
    is_private_addr(ip) -> bool
    parse_allowlist(s) -> list[network] | None        # raises GuardError(allowlist_config)
    allowlist_from_env() -> list[network] | None      # SPIKE_IROH_ALLOW_CIDRS, unset -> default
    check_ip(ip, allow=ENV) -> ip
    check_direct_addr("ip:port", allow=ENV) -> (ip, port)
    check_direct_addrs([...], allow=ENV) -> [(ip, port), ...]   # all-or-nothing
    check_relay_url(url|None) -> None                 # any relay refused in Alpha-3
    check_discovery(enabled) -> None
GuardError has ``.reason`` (one of the REASON_* strings), ``.detail`` and ``.log_line()``.

Run ``python3 scripts/spike_private_guard.py`` for the self-test (prints A3.0_PY_GUARD_SELFTEST_OK).
"""
from __future__ import annotations

import ipaddress
import os

ENV_ALLOW_CIDRS = "SPIKE_IROH_ALLOW_CIDRS"
DEFAULT_ALLOW_CIDRS = "10.73.0.0/24,127.0.0.0/8"

REASON_PUBLIC_ADDR = "public_addr"
REASON_BAD_ADDR = "bad_addr"
REASON_RELAY_REFUSED = "relay_refused"
REASON_DISCOVERY_REFUSED = "discovery_refused"
REASON_ALLOWLIST_MISS = "allowlist_miss"
REASON_ALLOWLIST_CONFIG = "allowlist_config"

PRIVATE_RANGES = tuple(
    ipaddress.ip_network(c)
    for c in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fd00::/8")
)

_ENV = object()  # sentinel: read allowlist from env


class GuardError(ValueError):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail

    def log_line(self) -> str:
        if self.reason in (REASON_PUBLIC_ADDR, REASON_ALLOWLIST_MISS):
            return f"a3_refuse_non_private:{self.detail}"
        return f"a3_refuse_{self.reason}:{self.detail}"


def canonical_ip(ip):
    """IPv4-mapped IPv6 (::ffff:a.b.c.d) -> IPv4; accepts str or ip_address."""
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _in(net, ip) -> bool:
    return net.version == ip.version and ip in net


def is_private_addr(ip) -> bool:
    ip = canonical_ip(ip)
    return any(_in(n, ip) for n in PRIVATE_RANGES)


def _parse_cidr(s: str):
    if not s or s.endswith("/") or ("/" in s and not s.split("/", 1)[1].isdigit()):
        raise GuardError(REASON_ALLOWLIST_CONFIG, s)
    try:
        net = ipaddress.ip_network(s, strict=False)  # host bits masked, like Rust
    except ValueError:
        raise GuardError(REASON_ALLOWLIST_CONFIG, s) from None
    if isinstance(net, ipaddress.IPv6Network) and net.network_address.ipv4_mapped is not None:
        raise GuardError(REASON_ALLOWLIST_CONFIG, s)
    return net


def parse_allowlist(s: str):
    """Comma-separated CIDRs. Empty -> None (no narrowing). Non-private CIDR -> allowlist_config."""
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        return None
    out = []
    for p in parts:
        net = _parse_cidr(p)
        if not any(r.version == net.version and net.subnet_of(r) for r in PRIVATE_RANGES):
            raise GuardError(REASON_ALLOWLIST_CONFIG, p)
        out.append(net)
    return out


def allowlist_from_env():
    v = os.environ.get(ENV_ALLOW_CIDRS)
    return parse_allowlist(DEFAULT_ALLOW_CIDRS if v is None else v)


def check_ip(ip, allow=_ENV):
    if allow is _ENV:
        allow = allowlist_from_env()
    ip = canonical_ip(ip)
    if not is_private_addr(ip):
        raise GuardError(REASON_PUBLIC_ADDR, str(ip))
    if allow is not None and not any(_in(n, ip) for n in allow):
        raise GuardError(REASON_ALLOWLIST_MISS, str(ip))
    return ip


def _parse_sockaddr(s: str):
    t = s.strip()
    if t.startswith("["):
        host, sep, rest = t[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise GuardError(REASON_BAD_ADDR, s)
        port_s = rest[1:]
        try:
            ip = ipaddress.IPv6Address(host)  # Rust SocketAddr: [..] must be IPv6, no zone
        except ValueError:
            raise GuardError(REASON_BAD_ADDR, s) from None
    else:
        host, sep, port_s = t.rpartition(":")
        if not sep:
            raise GuardError(REASON_BAD_ADDR, s)
        try:
            ip = ipaddress.IPv4Address(host)  # bare IPv6 without brackets is bad_addr
        except ValueError:
            raise GuardError(REASON_BAD_ADDR, s) from None
    if not port_s.isdigit() or not port_s.isascii():
        raise GuardError(REASON_BAD_ADDR, s)
    port = int(port_s)
    if port == 0 or port > 65535:
        raise GuardError(REASON_BAD_ADDR, s)
    return ip, port


def check_direct_addr(s: str, allow=_ENV):
    """'ip:port' or '[v6]:port' -> (canonical ip, port). Raises GuardError."""
    if allow is _ENV:
        allow = allowlist_from_env()
    if not isinstance(s, str):
        raise GuardError(REASON_BAD_ADDR, repr(s))
    ip, port = _parse_sockaddr(s)
    return check_ip(ip, allow), port


def check_direct_addrs(addrs, allow=_ENV):
    """All-or-nothing (A3.3 ticket rule): first failing entry raises."""
    if allow is _ENV:
        allow = allowlist_from_env()
    return [check_direct_addr(a, allow) for a in addrs]


def check_relay_url(url) -> None:
    # TODO(A3.6): allow only when A3.6 is on and host is a literal IP passing check_ip.
    if url is None or (isinstance(url, str) and not url.strip()):
        return None
    raise GuardError(REASON_RELAY_REFUSED, str(url))


def check_discovery(enabled: bool) -> None:
    if enabled:
        raise GuardError(REASON_DISCOVERY_REFUSED, "discovery")


def _expect_reason(fn, reason, label):
    try:
        fn()
    except GuardError as e:
        assert e.reason == reason, f"{label}: got {e.reason}, want {reason}"
        return e
    raise AssertionError(f"{label}: expected {reason}, got success")


def selftest() -> int:
    n = 0
    for s in ("127.0.0.1", "127.255.255.255", "10.0.0.0", "10.255.255.255", "10.73.0.11",
              "172.16.0.0", "172.31.255.255", "192.168.0.0", "192.168.255.255", "::1",
              "fd00::", "fd12:3456::1", "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
              "::ffff:10.73.0.1", "::ffff:127.0.0.1", "::ffff:192.168.1.1"):
        assert is_private_addr(s), s; n += 1
    for s in ("0.0.0.0", "::", "1.1.1.1", "8.8.8.8", "9.255.255.255", "11.0.0.0",
              "126.255.255.255", "128.0.0.0", "172.15.255.255", "172.32.0.0",
              "192.167.255.255", "192.169.0.0", "169.254.1.1", "100.64.0.1",
              "100.127.255.255", "224.0.0.1", "255.255.255.255", "fc00::1",
              "fcff::1", "fe00::1", "fe80::1", "::2", "2001:db8::1", "2606:4700::1111",
              "ff02::1", "::ffff:8.8.8.8", "::ffff:169.254.0.1", "::ffff:0.0.0.0",
              "::127.0.0.1", "64:ff9b::a00:1"):
        assert not is_private_addr(s), s; n += 1

    none = None
    assert check_direct_addr("10.73.0.1:4433", none) == (ipaddress.ip_address("10.73.0.1"), 4433); n += 1
    assert check_direct_addr("[fd00::1]:4433", none)[1] == 4433; n += 1
    assert check_direct_addr("[::1]:1", none); n += 1
    assert check_direct_addr("[::ffff:10.0.0.5]:9", none) == (ipaddress.ip_address("10.0.0.5"), 9); n += 1
    for s, r in (("8.8.8.8:53", REASON_PUBLIC_ADDR), ("[2001:db8::1]:443", REASON_PUBLIC_ADDR),
                 ("[::ffff:1.2.3.4]:80", REASON_PUBLIC_ADDR), ("0.0.0.0:4433", REASON_PUBLIC_ADDR),
                 ("10.0.0.1", REASON_BAD_ADDR), ("10.0.0.1:0", REASON_BAD_ADDR),
                 ("10.0.0.1:65536", REASON_BAD_ADDR), ("localhost:4433", REASON_BAD_ADDR),
                 ("fd00::1:4433", REASON_BAD_ADDR), ("", REASON_BAD_ADDR),
                 ("10.0.0.256:1", REASON_BAD_ADDR)):
        _expect_reason(lambda: check_direct_addr(s, none), r, s); n += 1

    good = ["10.73.0.1:4433", "127.0.0.1:4433"]
    assert len(check_direct_addrs(good, none)) == 2; n += 1
    e = _expect_reason(lambda: check_direct_addrs(["10.73.0.1:4433", "1.2.3.4:4433"], none),
                       REASON_PUBLIC_ADDR, "mixed")
    assert e.log_line() == "a3_refuse_non_private:1.2.3.4"; n += 1
    assert check_direct_addrs([], none) == []; n += 1

    allow = parse_allowlist(DEFAULT_ALLOW_CIDRS)
    for s in ("10.73.0.12:4433", "127.0.0.1:4433", "[::ffff:10.73.0.1]:1"):
        assert check_direct_addr(s, allow); n += 1
    e = _expect_reason(lambda: check_direct_addr("10.74.0.1:4433", allow), REASON_ALLOWLIST_MISS, "miss")
    assert e.log_line() == "a3_refuse_non_private:10.74.0.1"; n += 1
    for s, r in (("192.168.1.1:1", REASON_ALLOWLIST_MISS), ("[::1]:1", REASON_ALLOWLIST_MISS),
                 ("8.8.8.8:1", REASON_PUBLIC_ADDR)):
        _expect_reason(lambda: check_direct_addr(s, allow), r, s); n += 1
    v6 = parse_allowlist("fd73::/16")
    assert check_direct_addr("[fd73::5]:1", v6); n += 1
    _expect_reason(lambda: check_direct_addr("[fd74::5]:1", v6), REASON_ALLOWLIST_MISS, "v6 miss"); n += 1
    assert parse_allowlist("") is None and parse_allowlist(" , ") is None; n += 1
    h = parse_allowlist("10.73.0.5/24, 10.9.9.9")
    assert [str(x) for x in h] == ["10.73.0.0/24", "10.9.9.9/32"], h; n += 1
    for s in ("0.0.0.0/0", "8.8.8.0/24", "10.0.0.0/7", "172.16.0.0/11", "192.168.0.0/15",
              "100.64.0.0/10", "fc00::/7", "fc00::/8", "::/0", "::1/127", "10.73.0.0/24,1.1.1.1",
              "10.0.0.0/33", "10.0.0.0/", "10.0.0.0/x", "nonsense", "::ffff:10.0.0.0/104"):
        _expect_reason(lambda: parse_allowlist(s), REASON_ALLOWLIST_CONFIG, s); n += 1
    for s in ("10.0.0.0/8", "172.16.0.0/12", "172.20.0.0/16", "192.168.0.0/16", "::1",
              "fd00::/8", "fd00::/64", "127.0.0.0/8"):
        assert parse_allowlist(s), s; n += 1

    # env handling
    saved = os.environ.get(ENV_ALLOW_CIDRS)
    try:
        os.environ.pop(ENV_ALLOW_CIDRS, None)
        _expect_reason(lambda: check_direct_addr("192.168.1.1:1"), REASON_ALLOWLIST_MISS, "env default"); n += 1
        os.environ[ENV_ALLOW_CIDRS] = ""
        assert check_direct_addr("192.168.1.1:1"); n += 1
        os.environ[ENV_ALLOW_CIDRS] = "0.0.0.0/0"
        _expect_reason(lambda: check_direct_addr("10.0.0.1:1"), REASON_ALLOWLIST_CONFIG, "env bad"); n += 1
    finally:
        if saved is None:
            os.environ.pop(ENV_ALLOW_CIDRS, None)
        else:
            os.environ[ENV_ALLOW_CIDRS] = saved

    assert check_relay_url(None) is None and check_relay_url("") is None; n += 1
    for u in ("https://euw1-1.relay.iroh.network./", "https://use1-1.relay.n0.iroh.iroh.link/",
              "http://10.73.0.254:3340", "http://127.0.0.1:3340"):
        e = _expect_reason(lambda: check_relay_url(u), REASON_RELAY_REFUSED, u)
        assert e.log_line().startswith("a3_refuse_relay_refused:"); n += 1
    check_discovery(False)
    _expect_reason(lambda: check_discovery(True), REASON_DISCOVERY_REFUSED, "discovery"); n += 1
    return n


if __name__ == "__main__":
    count = selftest()
    print(f"spike_private_guard self-test: {count} checks passed")
    print("A3.0_PY_GUARD_SELFTEST_OK")
