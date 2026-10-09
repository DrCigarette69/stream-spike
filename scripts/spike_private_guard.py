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

A4.1 pilot guard (docs/ALPHA4_PILOT.md; mirror of rust stream_proto::guard::pilot). Alpha-3 functions
above are unchanged. ``lane`` is LANE_PILOT (default) or LANE_LOCAL (on-box a4 lanes only; the Rust
side gates it behind cargo feature ``a4_local``, here the caller passes it explicitly):
    is_public_egress_ip(ip, lane) -> bool
    normalize_hostname(s) -> str | None
    RelayAllow.parse(url, lane) / relay_allow_from_env(lane)       # relay_config
    check_relay_url_with(url|None, allow|None) -> None              # relay_refused
    check_relay_required(direct_addrs, relay_url) -> None           # relay_required
    EgressAllow.parse(s) / egress_allow_from_env()                  # egress_allowlist_config; .version()
    check_egress_dest(host, port, allow) -> host                    # egress_not_allowlisted
    check_egress_resolved(host, ips, lane) -> ip                    # egress_resolved_non_public / _resolve_failed
    resolve_and_pin(host, port, allow, resolver, lane) -> (ip, port) # resolver(host, port) -> [ip]
    EgressSwitch(env_on, max_age_ms).update/check/is_on/check_open  # egress_off / egress_allowlist_mismatch
    check_allowlist_version, check_egress_budget, check_transport_pilot, check_stripe_test_key

Run ``python3 scripts/spike_private_guard.py`` for the self-tests (prints A3.0_PY_GUARD_SELFTEST_OK
and A4.1_PY_PILOT_SELFTEST_OK).
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
        if self.reason in A4_REASONS:
            return f"a4_refuse_{self.reason}:{self.detail}"
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


# ============================================================== A4.1 pilot guard
import hashlib as _hashlib

ENV_RELAY_ALLOW_URL = "SPIKE_RELAY_ALLOW_URL"
ENV_EGRESS_ALLOWLIST = "SPIKE_EGRESS_ALLOWLIST"
ENV_PUBLIC_EGRESS = "SPIKE_PUBLIC_EGRESS"
ENV_EGRESS_STATE_MAX_AGE_MS = "SPIKE_EGRESS_STATE_MAX_AGE_MS"
ENV_EGRESS_BYTE_CAP = "SPIKE_EGRESS_BYTE_CAP"
TRANSPORT_PILOT = "iroh_pilot"
DEFAULT_EGRESS_STATE_MAX_AGE_MS = 5000
MIN_EGRESS_STATE_MAX_AGE_MS = 100
DEFAULT_EGRESS_BYTE_CAP = 50_000_000
ALLOWED_EGRESS_PORTS = (443,)
LOCAL_RELAY_HOST = ipaddress.IPv4Address("10.73.0.254")
LANE_PILOT = "pilot"
LANE_LOCAL = "local"

REASON_RELAY_CONFIG = "relay_config"
REASON_RELAY_REQUIRED = "relay_required"
REASON_EGRESS_ALLOWLIST_CONFIG = "egress_allowlist_config"
REASON_EGRESS_NOT_ALLOWLISTED = "egress_not_allowlisted"
REASON_EGRESS_RESOLVED_NON_PUBLIC = "egress_resolved_non_public"
REASON_EGRESS_RESOLVE_FAILED = "egress_resolve_failed"
REASON_EGRESS_OFF = "egress_off"
REASON_EGRESS_ALLOWLIST_MISMATCH = "egress_allowlist_mismatch"
REASON_EGRESS_BUDGET_EXCEEDED = "egress_budget_exceeded"
REASON_TRANSPORT_NOT_PILOT = "transport_not_pilot"
REASON_STRIPE_LIVE_KEY_REFUSED = "stripe_live_key_refused"
A4_REASONS = (
    REASON_RELAY_CONFIG, REASON_RELAY_REQUIRED, REASON_EGRESS_ALLOWLIST_CONFIG,
    REASON_EGRESS_NOT_ALLOWLISTED, REASON_EGRESS_RESOLVED_NON_PUBLIC, REASON_EGRESS_RESOLVE_FAILED,
    REASON_EGRESS_OFF, REASON_EGRESS_ALLOWLIST_MISMATCH, REASON_EGRESS_BUDGET_EXCEEDED,
    REASON_TRANSPORT_NOT_PILOT, REASON_STRIPE_LIVE_KEY_REFUSED,
)

LOCAL_TEST_SITE_CIDR = ipaddress.ip_network("198.51.100.0/24")
_NON_PUBLIC_V4 = tuple(ipaddress.ip_network(c) for c in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32"))
_NON_PUBLIC_V6 = tuple(ipaddress.ip_network(c) for c in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20", "2001:10::/28", "2001:20::/28"))
_GLOBAL_UNICAST_V6 = ipaddress.ip_network("2000::/3")
_REFUSED_NAME_SUFFIXES = ("localhost", "local", "lan", "internal", "home.arpa", "arpa", "onion", "test", "invalid")


def is_public_egress_ip(ip, lane=LANE_PILOT) -> bool:
    ip = canonical_ip(ip)
    if lane == LANE_LOCAL and _in(LOCAL_TEST_SITE_CIDR, ip):
        return True
    if ip.version == 4:
        return not any(_in(n, ip) for n in _NON_PUBLIC_V4)
    return _in(_GLOBAL_UNICAST_V6, ip) and not any(_in(n, ip) for n in _NON_PUBLIC_V6)


def _is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def normalize_hostname(s):
    t = s.strip()
    if t.endswith("."):
        t = t[:-1]
    if not t or len(t) > 253 or not _is_ascii(t):
        return None
    h = t.lower()
    labels = h.split(".")
    if len(labels) < 2:
        return None
    ok = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    for lab in labels:
        if not lab or len(lab) > 63 or lab[0] == "-" or lab[-1] == "-" or not set(lab) <= ok:
            return None
    tld = labels[-1]
    if tld.isdigit() or (tld.startswith("0x") and all(c in "0123456789abcdef" for c in tld[2:])):
        return None
    return h


def _is_ip_literal(s) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _has_suffix(h, suf):
    return h == suf or h.endswith("." + suf)


def _is_refused_local_name(h):
    return any(_has_suffix(h, s) for s in _REFUSED_NAME_SUFFIXES)


def is_n0_relay_host(h) -> bool:
    return _has_suffix(h, "iroh.network") or _has_suffix(h, "iroh.link") or "n0" in h.split(".")


def _parse_relay_parts(s):
    """-> (scheme, host, port) or None. host is a normalised name or an ip_address."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    if any(c in t for c in "?#@ \\"):
        return None
    scheme_s, sep, rest = t.partition("://")
    if not sep:
        return None
    scheme = scheme_s.lower()
    if scheme not in ("https", "http"):
        return None
    i = rest.find("/")
    auth, path = (rest, "") if i < 0 else (rest[:i], rest[i:])
    if path not in ("", "/") or not auth:
        return None
    bracket = auth.startswith("[")
    if bracket:
        h, sep, after = auth[1:].partition("]")
        if not sep:
            return None
        if after == "":
            port_s = None
        elif after.startswith(":"):
            port_s = after[1:]
        else:
            return None
        host_s = h
    else:
        host_s, sep, port_s = auth.rpartition(":")
        if not sep:
            host_s, port_s = auth, None
    if port_s is None:
        port = 443 if scheme == "https" else 80
    else:
        if not port_s or len(port_s) > 5 or not port_s.isdigit() or not _is_ascii(port_s):
            return None
        port = int(port_s)
        if port == 0 or port > 65535:
            return None
    if bracket:
        try:
            host = canonical_ip(ipaddress.IPv6Address(host_s))
        except ValueError:
            return None
    else:
        try:
            host = ipaddress.IPv4Address(host_s)
        except ValueError:
            host = normalize_hostname(host_s)
            if host is None:
                return None
    return scheme, host, port


class RelayAllow:
    def __init__(self, parts):
        self.parts = parts

    def __eq__(self, other):
        return isinstance(other, RelayAllow) and self.parts == other.parts

    @classmethod
    def parse(cls, s, lane=LANE_PILOT):
        detail = s.strip() if isinstance(s, str) else repr(s)
        if not isinstance(s, str) or "," in s:
            raise GuardError(REASON_RELAY_CONFIG, detail)
        parts = _parse_relay_parts(s)
        if parts is None:
            raise GuardError(REASON_RELAY_CONFIG, detail)
        scheme, host, _port = parts
        if lane == LANE_LOCAL and scheme == "http" and host == LOCAL_RELAY_HOST:
            return cls(parts)
        if scheme != "https":
            raise GuardError(REASON_RELAY_CONFIG, detail)
        if isinstance(host, str):
            if is_n0_relay_host(host) or _is_refused_local_name(host):
                raise GuardError(REASON_RELAY_CONFIG, detail)
        elif not is_public_egress_ip(host, LANE_PILOT):
            raise GuardError(REASON_RELAY_CONFIG, detail)
        return cls(parts)

    def url(self) -> str:
        scheme, host, port = self.parts
        h = f"[{host}]" if isinstance(host, ipaddress.IPv6Address) else str(host)
        return f"{scheme}://{h}:{port}/"


def relay_allow_from_env(lane=LANE_PILOT):
    v = os.environ.get(ENV_RELAY_ALLOW_URL)
    if v is None or not v.strip():
        return None
    return RelayAllow.parse(v, lane)


def check_relay_url_with(url, allow=None) -> None:
    if url is None or (isinstance(url, str) and not url.strip()):
        return None
    if allow is None:
        raise GuardError(REASON_RELAY_REFUSED, str(url))
    parts = _parse_relay_parts(url)
    if parts is None or parts != allow.parts:
        raise GuardError(REASON_RELAY_REFUSED, str(url))
    return None


def check_relay_required(direct_addrs, relay_url) -> None:
    has_relay = isinstance(relay_url, str) and bool(relay_url.strip())
    if not direct_addrs and not has_relay:
        raise GuardError(REASON_RELAY_REQUIRED, "no direct_addrs and no relay_url")


class EgressAllow:
    def __init__(self, entries):
        self._entries = entries

    def __eq__(self, other):
        return isinstance(other, EgressAllow) and self._entries == other._entries

    @classmethod
    def parse(cls, s):
        parts = [p.strip() for p in (s or "").split(",") if p.strip()]
        if not parts:
            raise GuardError(REASON_EGRESS_ALLOWLIST_CONFIG, "<empty>")
        out = set()
        for p in parts:
            def bad():
                return GuardError(REASON_EGRESS_ALLOWLIST_CONFIG, p)
            if any(c in p for c in "*[]/@"):
                raise bad()
            h, sep, port_s = p.rpartition(":")
            if not sep or ":" in h:
                raise bad()
            if _is_ip_literal(h):
                raise bad()
            if not port_s or len(port_s) > 5 or not port_s.isdigit() or not _is_ascii(port_s):
                raise bad()
            port = int(port_s)
            if port not in ALLOWED_EGRESS_PORTS:
                raise bad()
            host = normalize_hostname(h)
            if host is None or _is_refused_local_name(host):
                raise bad()
            out.add((host, port))
        return cls(sorted(out))

    def entries(self):
        return list(self._entries)

    def canonical(self) -> str:
        return "".join(f"{h}:{p}\n" for h, p in self._entries)

    def version(self) -> str:
        return _hashlib.sha256(self.canonical().encode()).hexdigest()

    def contains(self, host, port) -> bool:
        return (host, port) in self._entries


def egress_allow_from_env():
    v = os.environ.get(ENV_EGRESS_ALLOWLIST)
    if v is None:
        raise GuardError(REASON_EGRESS_ALLOWLIST_CONFIG, "<unset>")
    return EgressAllow.parse(v)


def check_egress_dest(host, port, allow):
    t = host.strip() if isinstance(host, str) else ""
    detail = f"{t}:{port}"
    bare = t[1:-1] if t.startswith("[") and t.endswith("]") else t
    if _is_ip_literal(bare):
        raise GuardError(REASON_EGRESS_NOT_ALLOWLISTED, detail)
    h = normalize_hostname(t)
    if h is None or not allow.contains(h, port):
        raise GuardError(REASON_EGRESS_NOT_ALLOWLISTED, detail)
    return h


def check_egress_resolved(host, ips, lane=LANE_PILOT):
    if not ips:
        raise GuardError(REASON_EGRESS_RESOLVE_FAILED, host)
    canon = [canonical_ip(i) for i in ips]
    for ip in canon:
        if not is_public_egress_ip(ip, lane):
            raise GuardError(REASON_EGRESS_RESOLVED_NON_PUBLIC, f"{host}->{ip}")
    return canon[0]


def resolve_and_pin(host, port, allow, resolver, lane=LANE_PILOT):
    """resolver(host, port) -> list of IPs (raise on failure). Connect to the returned (ip, port) only."""
    h = check_egress_dest(host, port, allow)
    try:
        ips = list(resolver(h, port))
    except Exception:
        raise GuardError(REASON_EGRESS_RESOLVE_FAILED, f"{h}:{port}") from None
    return check_egress_resolved(h, ips, lane), port


def std_resolver(host, port):
    import socket
    return [ipaddress.ip_address(ai[4][0].split("%")[0]) for ai in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def public_egress_env() -> bool:
    return os.environ.get(ENV_PUBLIC_EGRESS, "").strip() == "1"


def egress_state_max_age_ms_from(v):
    try:
        n = int(v.strip()) if v is not None else None
    except ValueError:
        n = None
    if n is None or n < 0:
        return DEFAULT_EGRESS_STATE_MAX_AGE_MS
    return max(MIN_EGRESS_STATE_MAX_AGE_MS, min(n, DEFAULT_EGRESS_STATE_MAX_AGE_MS))


def egress_state_max_age_ms_from_env():
    return egress_state_max_age_ms_from(os.environ.get(ENV_EGRESS_STATE_MAX_AGE_MS))


class EgressSwitch:
    """public_egress_effective = env_on AND Control flag on AND fresh. Caller-supplied monotonic ms."""

    def __init__(self, env_on: bool, max_age_ms: int = DEFAULT_EGRESS_STATE_MAX_AGE_MS):
        self.env_on = bool(env_on)
        self.max_age_ms = max_age_ms
        self.flag = None  # (flag_on, allowlist_version, received_at_ms)

    @classmethod
    def from_env(cls):
        return cls(public_egress_env(), egress_state_max_age_ms_from_env())

    def update(self, flag_on: bool, allowlist_version: str, now_ms: int) -> None:
        self.flag = (bool(flag_on), str(allowlist_version), now_ms)

    def check(self, now_ms: int) -> None:
        if not self.env_on:
            raise GuardError(REASON_EGRESS_OFF, "local_off")
        if self.flag is None:
            raise GuardError(REASON_EGRESS_OFF, "no_state")
        on, _v, at = self.flag
        if now_ms < at or now_ms - at > self.max_age_ms:
            raise GuardError(REASON_EGRESS_OFF, "flag_stale")
        if not on:
            raise GuardError(REASON_EGRESS_OFF, "flag_off")

    def is_on(self, now_ms: int) -> bool:
        try:
            self.check(now_ms)
            return True
        except GuardError:
            return False

    def check_open(self, now_ms: int, local_version: str) -> None:
        self.check(now_ms)
        check_allowlist_version(local_version, self.flag[1] if self.flag else "")


def check_allowlist_version(local, remote) -> None:
    l, r = (local or "").strip(), (remote or "").strip()
    if not l or l.lower() != r.lower():
        raise GuardError(REASON_EGRESS_ALLOWLIST_MISMATCH, f"local={l} remote={r}")


def egress_byte_cap_from(v):
    try:
        n = int(v.strip()) if v is not None else None
    except ValueError:
        n = None
    return DEFAULT_EGRESS_BYTE_CAP if n is None or n < 0 else n


def egress_byte_cap_from_env():
    return egress_byte_cap_from(os.environ.get(ENV_EGRESS_BYTE_CAP))


_U64_MAX = 2**64 - 1


def check_egress_budget(used, adding, cap) -> None:
    total = used + adding
    if total > _U64_MAX or total > cap:  # u64 overflow fails closed, like Rust
        raise GuardError(REASON_EGRESS_BUDGET_EXCEEDED, f"used={used} add={adding} cap={cap}")


def check_transport_pilot(transport) -> None:
    t = (transport or "").strip()
    if t != TRANSPORT_PILOT:
        raise GuardError(REASON_TRANSPORT_NOT_PILOT, t)


def check_stripe_test_key(key) -> None:
    """STRIPE_TEST_SECRET_KEY must be sk_test_/rk_test_. Detail is the prefix only, never the key."""
    k = (key or "").strip()
    if (k.startswith("sk_test_") or k.startswith("rk_test_")) and len(k) > 8:
        return None
    idx = [i for i, c in enumerate(k) if c == "_"]
    prefix = k[: idx[1] + 1] if len(idx) >= 2 else "<redacted>"
    raise GuardError(REASON_STRIPE_LIVE_KEY_REFUSED, prefix)


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


def selftest_pilot() -> int:
    """A4.1 tables, same cases as rust guard/pilot_tests.rs. No network (injected resolver)."""
    n = 0
    ip = ipaddress.ip_address
    ours = "https://relay.stream-pilot.example.org/"
    assert _hashlib.sha256(b"abc").hexdigest().startswith("ba7816bf"); n += 1

    for s in ("1.1.1.1", "8.8.8.8", "93.184.215.14", "11.0.0.1", "100.63.255.255", "100.128.0.0",
              "169.253.255.255", "172.32.0.1", "192.0.1.1", "198.17.255.255", "198.20.0.0",
              "223.255.255.255", "2606:4700::1111", "2a00:1450:4001::1", "::ffff:8.8.8.8", "2001:200::1"):
        assert is_public_egress_ip(s), s; n += 1
    for s in ("0.0.0.0", "0.1.2.3", "10.0.0.1", "10.73.0.254", "100.64.0.1", "100.127.255.255",
              "127.0.0.1", "127.255.255.255", "169.254.169.254", "169.254.0.1", "172.16.0.1",
              "172.31.255.255", "192.0.0.8", "192.0.2.1", "192.88.99.1", "192.168.1.1", "198.18.0.1",
              "198.19.255.255", "198.51.100.10", "203.0.113.5", "224.0.0.1", "239.255.255.255",
              "240.0.0.1", "255.255.255.255", "::", "::1", "::127.0.0.1", "::ffff:10.0.0.1",
              "::ffff:169.254.169.254", "::ffff:127.0.0.1", "64:ff9b::a9fe:a9fe", "64:ff9b::808:808",
              "64:ff9b:1::1", "100::1", "fc00::1", "fd00::1", "fe80::1", "fec0::1", "ff02::1",
              "2001::1", "2001:db8::1", "2001:10::1", "2001:20::1", "2002:a00:1::1", "3fff::1",
              "4000::1", "1000::1"):
        assert not is_public_egress_ip(s), s; n += 1
    assert is_public_egress_ip("198.51.100.10", LANE_LOCAL) and is_public_egress_ip("::ffff:198.51.100.10", LANE_LOCAL); n += 1
    for s in ("10.73.0.254", "127.0.0.1", "169.254.169.254", "192.0.2.1", "203.0.113.1"):
        assert not is_public_egress_ip(s, LANE_LOCAL), s; n += 1

    assert normalize_hostname(" Echo.Example.ORG. ") == "echo.example.org"; n += 1
    assert normalize_hostname("xn--bcher-kva.example") == "xn--bcher-kva.example"; n += 1
    for s in ("", "localhost", "example", "bücher.example", "a..b", ".a.b", "-a.b", "a-.b", "a_b.com",
              "127.1", "1.2.3.4", "10.0.0.1", "1.0x7f", "a.b/c", "a.b:443", "a" * 64, "*.example.org"):
        assert normalize_hostname(s) is None, s; n += 1

    a = RelayAllow.parse(ours)
    assert a.url() == "https://relay.stream-pilot.example.org:443/"; n += 1
    assert RelayAllow.parse("HTTPS://Relay.Stream-Pilot.Example.ORG.:443") == a; n += 1
    assert RelayAllow.parse("https://1.2.3.4:8443/").url() == "https://1.2.3.4:8443/"; n += 1
    assert RelayAllow.parse("https://[2606:4700::1]/").url() == "https://[2606:4700::1]:443/"; n += 1
    for s in ("", "relay.example.org", "ftp://relay.example.org/", "http://relay.example.org/",
              "https://euw1-1.relay.iroh.network./", "https://use1-1.relay.n0.iroh.iroh.link/",
              "https://iroh.link/", "https://relay.n0.example.org/", "https://localhost/",
              "https://relay.lan/", "https://relay.internal/", "https://10.73.0.254/", "https://127.0.0.1/",
              "https://169.254.169.254/", "https://[::1]/", "https://[fd00::1]/", "https://198.51.100.1/",
              "https://u:p@relay.example.org/", "https://relay.example.org/path", "https://relay.example.org/?x=1",
              "https://relay.example.org/#f", "https://relay.example.org:0/", "https://relay.example.org:65536/",
              "https://relay.example.org:/", "https://relay.example.org:x/",
              "https://a.example.org/,https://b.example.org/", "http://10.73.0.254:3340/"):
        e = _expect_reason(lambda: RelayAllow.parse(s), REASON_RELAY_CONFIG, s)
        assert e.log_line().startswith("a4_refuse_relay_config:"); n += 1
    assert RelayAllow.parse("http://10.73.0.254:3340", LANE_LOCAL).url() == "http://10.73.0.254:3340/"; n += 1
    for s in ("http://10.73.0.253:3340/", "http://127.0.0.1:3340/", "http://relay.example.org/", "https://10.73.0.254/"):
        _expect_reason(lambda: RelayAllow.parse(s, LANE_LOCAL), REASON_RELAY_CONFIG, s); n += 1

    for u in (ours, "https://relay.stream-pilot.example.org", "https://RELAY.stream-pilot.example.org.:443/"):
        check_relay_url_with(u, a); n += 1
    check_relay_url_with(None, a); check_relay_url_with("  ", a); n += 1
    for u in ("https://euw1-1.relay.iroh.network./", "https://use1-1.relay.n0.iroh.iroh.link/",
              "https://other-relay.example.org/", "https://relay.stream-pilot.example.org:8443/",
              "http://relay.stream-pilot.example.org/", "https://relay.stream-pilot.example.org.evil.com/",
              "https://relay.stream-pilot.example.org/x", "https://user@relay.stream-pilot.example.org/",
              "https://relay.stream-pilot.example.org/?a", "garbage", "http://10.73.0.254:3340"):
        e = _expect_reason(lambda: check_relay_url_with(u, a), REASON_RELAY_REFUSED, u)
        assert e.log_line().startswith("a3_refuse_relay_refused:"); n += 1
    for u in (ours, "http://10.73.0.254:3340"):
        _expect_reason(lambda: check_relay_url_with(u, None), REASON_RELAY_REFUSED, u)
        _expect_reason(lambda: check_relay_url(u), REASON_RELAY_REFUSED, u); n += 1
    loc = RelayAllow.parse("http://10.73.0.254:3340/", LANE_LOCAL)
    check_relay_url_with("http://10.73.0.254:3340", loc); n += 1
    _expect_reason(lambda: check_relay_url_with("http://10.73.0.254:3341", loc), REASON_RELAY_REFUSED, "local port"); n += 1

    check_relay_required([], ours); check_relay_required(["10.73.0.1:1"], None); n += 1
    for r in (None, "", "  "):
        e = _expect_reason(lambda: check_relay_required([], r), REASON_RELAY_REQUIRED, repr(r))
        assert e.log_line().startswith("a4_refuse_relay_required:"); n += 1

    al = EgressAllow.parse(" Echo.Example.org.:443 , example.com:443,echo.example.org:443")
    assert al.entries() == [("echo.example.org", 443), ("example.com", 443)]; n += 1
    assert al.canonical() == "echo.example.org:443\nexample.com:443\n"; n += 1
    assert al.version() == _hashlib.sha256(b"echo.example.org:443\nexample.com:443\n").hexdigest(); n += 1
    assert al.version() == EgressAllow.parse("example.com:443,ECHO.example.org:443").version(); n += 1
    assert al.version() != EgressAllow.parse("example.com:443").version(); n += 1
    for s in ("", " , ", "example.com", "example.com:", "example.com:80", "example.com:8443", "example.com:0",
              "example.com:65536", "example.com:44x", "*.example.com:443", "*:443", "1.2.3.4:443",
              "198.51.100.10:443", "[2606:4700::1]:443", "2606:4700::1:443", "127.1:443", "localhost:443",
              "printer.lan:443", "metadata.google.internal:443", "a.local:443", "x.home.arpa:443",
              "bücher.example:443", "https://example.com:443", "example.com/x:443", "u@example.com:443",
              "example.com:443,1.1.1.1:443", "intranet:443"):
        e = _expect_reason(lambda: EgressAllow.parse(s), REASON_EGRESS_ALLOWLIST_CONFIG, s)
        assert e.log_line().startswith("a4_refuse_egress_allowlist_config:"); n += 1

    d = EgressAllow.parse("echo.example.org:443,example.com:443")
    assert check_egress_dest("echo.example.org", 443, d) == "echo.example.org"; n += 1
    assert check_egress_dest("ECHO.example.org.", 443, d) == "echo.example.org"; n += 1
    for h, p in (("echo.example.org", 80), ("echo.example.org", 8443), ("evil.example.org", 443),
                 ("example.com.evil.net", 443), ("sub.example.com", 443), ("93.184.215.14", 443),
                 ("[2606:4700::1]", 443), ("2606:4700::1", 443), ("169.254.169.254", 443), ("127.1", 443),
                 ("", 443), ("bücher.example", 443), ("localhost", 443)):
        e = _expect_reason(lambda: check_egress_dest(h, p, d), REASON_EGRESS_NOT_ALLOWLISTED, f"{h}:{p}")
        assert e.log_line().startswith("a4_refuse_egress_not_allowlisted:"); n += 1

    h = "echo.example.org"
    assert check_egress_resolved(h, [ip("1.2.3.4"), ip("2606:4700::1")]) == ip("1.2.3.4"); n += 1
    assert check_egress_resolved(h, [ip("::ffff:1.2.3.4")]) == ip("1.2.3.4"); n += 1
    _expect_reason(lambda: check_egress_resolved(h, []), REASON_EGRESS_RESOLVE_FAILED, "empty"); n += 1
    for bad in (["10.0.0.1"], ["127.0.0.1"], ["169.254.169.254"], ["100.64.0.1"], ["fe80::1"],
                ["64:ff9b::a9fe:a9fe"], ["::ffff:192.168.0.1"], ["1.2.3.4", "10.0.0.1"], ["198.51.100.10"]):
        e = _expect_reason(lambda: check_egress_resolved(h, [ip(x) for x in bad]), REASON_EGRESS_RESOLVED_NON_PUBLIC, str(bad))
        assert e.log_line().startswith("a4_refuse_egress_resolved_non_public:echo.example.org->"); n += 1
    assert check_egress_resolved(h, [ip("198.51.100.10")], LANE_LOCAL) == ip("198.51.100.10"); n += 1
    _expect_reason(lambda: check_egress_resolved(h, [ip("169.254.169.254")], LANE_LOCAL),
                   REASON_EGRESS_RESOLVED_NON_PUBLIC, "local meta"); n += 1

    pa = EgressAllow.parse("echo.example.org:443,meta.example.org:443,gone.example.org:443,empty.example.org:443")
    calls = []
    answers = {"echo.example.org": ["93.184.215.14", "2606:4700::6810"], "meta.example.org": ["169.254.169.254"],
               "empty.example.org": []}

    def fake(host, port):
        calls.append(host)
        if host not in answers:
            raise OSError("NXDOMAIN")
        return [ip(x) for x in answers[host]]
    assert resolve_and_pin("Echo.example.org", 443, pa, fake) == (ip("93.184.215.14"), 443); n += 1
    c0 = len(calls)
    _expect_reason(lambda: resolve_and_pin("evil.example.org", 443, pa, fake), REASON_EGRESS_NOT_ALLOWLISTED, "off"); n += 1
    _expect_reason(lambda: resolve_and_pin("8.8.8.8", 443, pa, fake), REASON_EGRESS_NOT_ALLOWLISTED, "ip"); n += 1
    assert len(calls) == c0, "no DNS for off-allowlist"; n += 1
    _expect_reason(lambda: resolve_and_pin("meta.example.org", 443, pa, fake), REASON_EGRESS_RESOLVED_NON_PUBLIC, "meta"); n += 1
    _expect_reason(lambda: resolve_and_pin("gone.example.org", 443, pa, fake), REASON_EGRESS_RESOLVE_FAILED, "gone"); n += 1
    _expect_reason(lambda: resolve_and_pin("empty.example.org", 443, pa, fake), REASON_EGRESS_RESOLVE_FAILED, "empty"); n += 1
    seq = iter([["93.184.215.14"], ["127.0.0.1"]])  # DNS rebinding stand-in
    rebind = lambda host, port: [ip(x) for x in next(seq)]
    assert resolve_and_pin("echo.example.org", 443, pa, rebind); n += 1
    _expect_reason(lambda: resolve_and_pin("echo.example.org", 443, pa, rebind), REASON_EGRESS_RESOLVED_NON_PUBLIC, "rebind"); n += 1
    local = lambda host, port: [ip("198.51.100.10")]
    assert resolve_and_pin("echo.example.org", 443, pa, local, LANE_LOCAL) == (ip("198.51.100.10"), 443); n += 1
    _expect_reason(lambda: resolve_and_pin("echo.example.org", 443, pa, local), REASON_EGRESS_RESOLVED_NON_PUBLIC, "testnet pilot"); n += 1

    v = EgressAllow.parse("echo.example.org:443").version()
    det = lambda sw, t: _expect_reason(lambda: sw.check(t), REASON_EGRESS_OFF, "sw").detail
    off = EgressSwitch(False, 5000)
    assert det(off, 0) == "local_off"; off.update(True, v, 0); assert det(off, 1) == "local_off"; n += 1
    sw = EgressSwitch(True, 5000)
    assert det(sw, 0) == "no_state"; n += 1
    sw.update(True, v, 1000)
    assert sw.is_on(1000) and sw.is_on(6000); n += 1
    assert det(sw, 6001) == "flag_stale" and det(sw, 999) == "flag_stale"; n += 1
    sw.update(False, v, 7000); assert det(sw, 7000) == "flag_off"; n += 1
    sw.update(True, v, 8000); sw.check_open(8500, v); n += 1
    e = _expect_reason(lambda: sw.check_open(8500, _hashlib.sha256(b"other").hexdigest()), REASON_EGRESS_ALLOWLIST_MISMATCH, "mm")
    assert e.log_line().startswith("a4_refuse_egress_allowlist_mismatch:"); n += 1
    _expect_reason(lambda: sw.check_open(20000, v), REASON_EGRESS_OFF, "stale open"); n += 1
    check_allowlist_version(v, v.upper()); n += 1
    _expect_reason(lambda: check_allowlist_version("", ""), REASON_EGRESS_ALLOWLIST_MISMATCH, "empty"); n += 1
    assert [egress_state_max_age_ms_from(x) for x in (None, "x", "60000", "2000", "0")] == [5000, 5000, 5000, 2000, 100]; n += 1

    check_egress_budget(0, 50_000_000, DEFAULT_EGRESS_BYTE_CAP); n += 1
    e = _expect_reason(lambda: check_egress_budget(49_999_999, 2, DEFAULT_EGRESS_BYTE_CAP), REASON_EGRESS_BUDGET_EXCEEDED, "cap")
    assert e.log_line().startswith("a4_refuse_egress_budget_exceeded:"); n += 1
    _expect_reason(lambda: check_egress_budget(_U64_MAX, 1, _U64_MAX), REASON_EGRESS_BUDGET_EXCEEDED, "overflow"); n += 1
    _expect_reason(lambda: check_egress_budget(0, 1, 0), REASON_EGRESS_BUDGET_EXCEEDED, "zero cap"); n += 1
    assert [egress_byte_cap_from(x) for x in (None, "bad", "1000")] == [50_000_000, 50_000_000, 1000]; n += 1
    check_transport_pilot("iroh_pilot"); n += 1
    for t in ("", "fake_relay", "iroh_loopback", "iroh_local", "IROH_PILOT"):
        _expect_reason(lambda: check_transport_pilot(t), REASON_TRANSPORT_NOT_PILOT, t); n += 1
    check_stripe_test_key("sk_test_51abcDEF"); check_stripe_test_key("rk_test_51abcDEF"); n += 1
    for k, dd in (("sk_live_51SECRETSECRET", "sk_live_"), ("rk_live_51SECRET", "rk_live_"), ("pk_test_51x", "pk_test_"),
                  ("sk_test_", "sk_test_"), ("", "<redacted>"), ("whatever", "<redacted>")):
        e = _expect_reason(lambda: check_stripe_test_key(k), REASON_STRIPE_LIVE_KEY_REFUSED, k)
        assert e.detail == dd and "SECRET" not in e.log_line(), k; n += 1

    keys = (ENV_RELAY_ALLOW_URL, ENV_EGRESS_ALLOWLIST, ENV_PUBLIC_EGRESS, ENV_EGRESS_STATE_MAX_AGE_MS, ENV_EGRESS_BYTE_CAP)
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        assert relay_allow_from_env() is None; n += 1
        _expect_reason(egress_allow_from_env, REASON_EGRESS_ALLOWLIST_CONFIG, "env unset"); n += 1
        assert not public_egress_env() and not EgressSwitch.from_env().is_on(0); n += 1
        assert egress_byte_cap_from_env() == DEFAULT_EGRESS_BYTE_CAP and egress_state_max_age_ms_from_env() == 5000; n += 1
        os.environ[ENV_RELAY_ALLOW_URL] = ours
        assert relay_allow_from_env() is not None; n += 1
        os.environ[ENV_RELAY_ALLOW_URL] = "https://euw1-1.relay.iroh.network./"
        _expect_reason(relay_allow_from_env, REASON_RELAY_CONFIG, "env n0"); n += 1
        os.environ[ENV_EGRESS_ALLOWLIST] = "echo.example.org:443"
        assert len(egress_allow_from_env().entries()) == 1; n += 1
        for val, on in (("1", True), (" 1 ", True), ("0", False), ("true", False), ("yes", False), ("", False)):
            os.environ[ENV_PUBLIC_EGRESS] = val
            assert public_egress_env() == on, val; n += 1
        os.environ[ENV_PUBLIC_EGRESS] = "1"
        os.environ[ENV_EGRESS_STATE_MAX_AGE_MS] = "1000"
        sw = EgressSwitch.from_env(); sw.update(True, "v", 0)
        assert sw.is_on(1000) and not sw.is_on(1001); n += 1
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
    return n


if __name__ == "__main__":
    import sys as _sys
    if "--pilot" not in _sys.argv[1:]:
        count = selftest()
        print(f"spike_private_guard self-test: {count} checks passed")
        print("A3.0_PY_GUARD_SELFTEST_OK")
    pcount = selftest_pilot()
    print(f"spike_private_guard A4.1 pilot self-test: {pcount} checks passed")
    print("A4.1_PY_PILOT_SELFTEST_OK")
