//! A4.1 pilot guard (Alpha-4 `iroh_pilot`). std only, no new dependencies.
//!
//! Spec: `docs/ALPHA4_PILOT.md` ("Guard changes (A4.1)"). Python mirror:
//! `scripts/spike_private_guard.py` (same rules, env vars and reason strings).
//!
//! Nothing here changes Alpha-3 behaviour: `check_relay_url`, `check_direct_addr*`,
//! `check_discovery` and their reasons are untouched. This module only adds:
//!
//! 1. Relay allowlist: `RelayAllow` (exactly one relay, `SPIKE_RELAY_ALLOW_URL`) and
//!    `check_relay_url_with`. Exact match after normalisation; anything else `relay_refused`.
//!    A bad / n0 / non-public allow URL is `relay_config`. `check_relay_required`.
//! 2. Egress allowlist: `EgressAllow` (`SPIKE_EGRESS_ALLOWLIST`, exact `host:443`, no IPs or
//!    wildcards), `check_egress_dest`, `check_egress_resolved` (SSRF: every resolved IP must
//!    be public), `resolve_and_pin` with an injected `Resolver`, `version()` (sha256).
//! 3. Kill switch: `EgressSwitch` (`SPIKE_PUBLIC_EGRESS` AND Control flag AND fresh
//!    (`SPIKE_EGRESS_STATE_MAX_AGE_MS`, default/max 5000)) → `egress_off`;
//!    `check_allowlist_version` → `egress_allowlist_mismatch`; byte cap
//!    (`SPIKE_EGRESS_BYTE_CAP`) → `egress_budget_exceeded`.
//! Plus `check_transport_pilot` (`transport_not_pilot`) and `check_stripe_test_key`
//! (`stripe_live_key_refused`).
//!
//! Lanes: `Lane::Pilot` (real devices) and `Lane::Local` (on-box a4 lanes). `Lane::build()`
//! is `Local` only with the stream-proto cargo feature `a4_local`. Local additionally allows
//! exactly: relay `http://10.73.0.254:<port>/`, and resolved test-site IPs in
//! `198.51.100.0/24` (TEST-NET-2). Private / metadata ranges stay refused in both lanes.

use super::{canonical_ip, Cidr, GuardError};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};

pub const ENV_RELAY_ALLOW_URL: &str = "SPIKE_RELAY_ALLOW_URL";
pub const ENV_EGRESS_ALLOWLIST: &str = "SPIKE_EGRESS_ALLOWLIST";
pub const ENV_PUBLIC_EGRESS: &str = "SPIKE_PUBLIC_EGRESS";
pub const ENV_EGRESS_STATE_MAX_AGE_MS: &str = "SPIKE_EGRESS_STATE_MAX_AGE_MS";
pub const ENV_EGRESS_BYTE_CAP: &str = "SPIKE_EGRESS_BYTE_CAP";

pub const TRANSPORT_PILOT: &str = "iroh_pilot";
/// Default and maximum staleness of the Control egress flag. Env can only narrow it.
pub const DEFAULT_EGRESS_STATE_MAX_AGE_MS: u64 = 5000;
pub const MIN_EGRESS_STATE_MAX_AGE_MS: u64 = 100;
/// Pilot default: 50 MB per Peer per UTC day.
pub const DEFAULT_EGRESS_BYTE_CAP: u64 = 50_000_000;
/// Ports an allowlist entry may name (doc: 443; 80 only if a slice asks for it).
pub const ALLOWED_EGRESS_PORTS: &[u16] = &[443];
/// The only relay host the on-box `Local` lane accepts over plain http.
pub const LOCAL_RELAY_HOST: Ipv4Addr = Ipv4Addr::new(10, 73, 0, 254);

pub const REASON_RELAY_CONFIG: &str = "relay_config";
pub const REASON_RELAY_REQUIRED: &str = "relay_required";
pub const REASON_EGRESS_ALLOWLIST_CONFIG: &str = "egress_allowlist_config";
pub const REASON_EGRESS_NOT_ALLOWLISTED: &str = "egress_not_allowlisted";
pub const REASON_EGRESS_RESOLVED_NON_PUBLIC: &str = "egress_resolved_non_public";
pub const REASON_EGRESS_RESOLVE_FAILED: &str = "egress_resolve_failed";
pub const REASON_EGRESS_OFF: &str = "egress_off";
pub const REASON_EGRESS_ALLOWLIST_MISMATCH: &str = "egress_allowlist_mismatch";
pub const REASON_EGRESS_BUDGET_EXCEEDED: &str = "egress_budget_exceeded";
pub const REASON_TRANSPORT_NOT_PILOT: &str = "transport_not_pilot";
pub const REASON_STRIPE_LIVE_KEY_REFUSED: &str = "stripe_live_key_refused";

/// Reasons whose log line is `a4_refuse_<reason>:<detail>`.
pub const A4_REASONS: &[&str] = &[
    REASON_RELAY_CONFIG,
    REASON_RELAY_REQUIRED,
    REASON_EGRESS_ALLOWLIST_CONFIG,
    REASON_EGRESS_NOT_ALLOWLISTED,
    REASON_EGRESS_RESOLVED_NON_PUBLIC,
    REASON_EGRESS_RESOLVE_FAILED,
    REASON_EGRESS_OFF,
    REASON_EGRESS_ALLOWLIST_MISMATCH,
    REASON_EGRESS_BUDGET_EXCEEDED,
    REASON_TRANSPORT_NOT_PILOT,
    REASON_STRIPE_LIVE_KEY_REFUSED,
];

fn err(reason: &'static str, detail: impl Into<String>) -> GuardError {
    GuardError::new(reason, detail)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lane {
    /// Real devices. No exceptions.
    Pilot,
    /// On-box a4 lanes (cargo feature `a4_local`): local relay + TEST-NET-2 test sites.
    Local,
}

impl Lane {
    /// `Local` only when stream-proto is built with feature `a4_local`.
    pub const fn build() -> Lane {
        if cfg!(feature = "a4_local") { Lane::Local } else { Lane::Pilot }
    }
}

// ---------------------------------------------------------------- public-IP (SSRF) check

const fn c4(a: u8, b: u8, c: u8, d: u8, p: u8) -> Cidr {
    Cidr { net: IpAddr::V4(Ipv4Addr::new(a, b, c, d)), prefix: p }
}
#[allow(clippy::too_many_arguments)]
const fn c6(a: u16, b: u16, c: u16, d: u16, e: u16, f: u16, g: u16, h: u16, p: u8) -> Cidr {
    Cidr { net: IpAddr::V6(Ipv6Addr::new(a, b, c, d, e, f, g, h)), prefix: p }
}

/// TEST-NET-2, the only "public" stand-in the `Local` lane accepts.
pub const LOCAL_TEST_SITE_CIDR: Cidr = c4(198, 51, 100, 0, 24);

/// IPv4 never valid as an egress destination.
const NON_PUBLIC_V4: [Cidr; 16] = [
    c4(0, 0, 0, 0, 8),
    c4(10, 0, 0, 0, 8),
    c4(100, 64, 0, 0, 10), // CGNAT
    c4(127, 0, 0, 0, 8),
    c4(169, 254, 0, 0, 16), // link-local incl. metadata 169.254.169.254
    c4(172, 16, 0, 0, 12),
    c4(192, 0, 0, 0, 24),
    c4(192, 0, 2, 0, 24), // TEST-NET-1
    c4(192, 88, 99, 0, 24),
    c4(192, 168, 0, 0, 16),
    c4(198, 18, 0, 0, 15),
    c4(198, 51, 100, 0, 24), // TEST-NET-2 (Local lane exception)
    c4(203, 0, 113, 0, 24),  // TEST-NET-3
    c4(224, 0, 0, 0, 4),     // multicast
    c4(240, 0, 0, 0, 4),     // reserved + broadcast
    c4(255, 255, 255, 255, 32),
];

/// IPv6 inside 2000::/3 that is still not a valid egress destination.
const NON_PUBLIC_V6: [Cidr; 6] = [
    c6(0x2001, 0, 0, 0, 0, 0, 0, 0, 23),      // IETF protocol assignments (incl. Teredo)
    c6(0x2001, 0x0db8, 0, 0, 0, 0, 0, 0, 32), // documentation
    c6(0x2002, 0, 0, 0, 0, 0, 0, 0, 16),      // 6to4 (can embed private v4)
    c6(0x3fff, 0, 0, 0, 0, 0, 0, 0, 20),      // documentation (RFC 9637)
    c6(0x2001, 0x0010, 0, 0, 0, 0, 0, 0, 28), // ORCHID
    c6(0x2001, 0x0020, 0, 0, 0, 0, 0, 0, 28), // ORCHIDv2
];
const GLOBAL_UNICAST_V6: Cidr = c6(0x2000, 0, 0, 0, 0, 0, 0, 0, 3);

/// Is `ip` a public unicast address we may egress to? IPv4-mapped IPv6 judged as IPv4.
/// IPv6 must be in 2000::/3 (so `::`, `::1`, `::/96`, NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`,
/// `100::/64`, `fc00::/7`, `fe80::/10`, `fec0::/10`, `ff00::/8` are all refused) and outside
/// the reserved blocks above.
pub fn is_public_egress_ip(ip: IpAddr, lane: Lane) -> bool {
    let ip = canonical_ip(ip);
    if lane == Lane::Local && LOCAL_TEST_SITE_CIDR.contains(ip) {
        return true;
    }
    match ip {
        IpAddr::V4(_) => !NON_PUBLIC_V4.iter().any(|c| c.contains(ip)),
        IpAddr::V6(_) => GLOBAL_UNICAST_V6.contains(ip) && !NON_PUBLIC_V6.iter().any(|c| c.contains(ip)),
    }
}

// ---------------------------------------------------------------- hostnames

const REFUSED_NAME_SUFFIXES: &[&str] =
    &["localhost", "local", "lan", "internal", "home.arpa", "arpa", "onion", "test", "invalid"];

/// Lowercase, strip one trailing dot, require a plausible ASCII DNS name with at least one dot,
/// a TLD that isn't numeric / hex (blocks `127.1`, `1.0x7f` IP-ish forms). Non-ASCII → `None`
/// (allowlist entries must be in punycode `xn--` form).
pub fn normalize_hostname(s: &str) -> Option<String> {
    let t = s.trim();
    let t = t.strip_suffix('.').unwrap_or(t);
    if t.is_empty() || t.len() > 253 || !t.is_ascii() {
        return None;
    }
    let h = t.to_ascii_lowercase();
    let labels: Vec<&str> = h.split('.').collect();
    if labels.len() < 2 {
        return None;
    }
    for l in &labels {
        if l.is_empty()
            || l.len() > 63
            || l.starts_with('-')
            || l.ends_with('-')
            || !l.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        {
            return None;
        }
    }
    let tld = labels[labels.len() - 1];
    if tld.bytes().all(|b| b.is_ascii_digit())
        || (tld.starts_with("0x") && tld[2..].bytes().all(|b| b.is_ascii_hexdigit()))
    {
        return None;
    }
    Some(h)
}

fn has_suffix(h: &str, suf: &str) -> bool {
    h == suf || h.ends_with(&format!(".{suf}"))
}

fn is_refused_local_name(h: &str) -> bool {
    REFUSED_NAME_SUFFIXES.iter().any(|s| has_suffix(h, s))
}

/// n0 / community relay hosts (never allowed as our relay).
pub fn is_n0_relay_host(h: &str) -> bool {
    has_suffix(h, "iroh.network") || has_suffix(h, "iroh.link") || h.split('.').any(|l| l == "n0")
}

// ---------------------------------------------------------------- 1. relay allowlist

#[derive(Debug, Clone, PartialEq, Eq)]
enum RelayHost {
    Name(String),
    Ip(IpAddr),
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RelayParts {
    scheme: &'static str,
    host: RelayHost,
    port: u16,
}

impl RelayParts {
    fn canonical(&self) -> String {
        let h = match &self.host {
            RelayHost::Name(n) => n.clone(),
            RelayHost::Ip(IpAddr::V6(v6)) => format!("[{v6}]"),
            RelayHost::Ip(ip) => ip.to_string(),
        };
        format!("{}://{}:{}/", self.scheme, h, self.port)
    }
}

/// Mechanical parse: `scheme://host[:port][/]`. No userinfo, query, fragment, or path
/// other than `/`. Scheme `https` or `http`; default ports 443 / 80. `None` if malformed.
fn parse_relay_parts(s: &str) -> Option<RelayParts> {
    let t = s.trim();
    if t.contains(['?', '#', '@', ' ', '\\']) {
        return None;
    }
    let (scheme_s, rest) = t.split_once("://")?;
    let scheme = match scheme_s.to_ascii_lowercase().as_str() {
        "https" => "https",
        "http" => "http",
        _ => return None,
    };
    let (auth, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, ""),
    };
    if !(path.is_empty() || path == "/") || auth.is_empty() {
        return None;
    }
    let (host_s, port_s) = if let Some(r) = auth.strip_prefix('[') {
        let (h, after) = r.split_once(']')?;
        let p = match after {
            "" => None,
            a => Some(a.strip_prefix(':')?),
        };
        (h, p)
    } else {
        match auth.rsplit_once(':') {
            Some((h, p)) => (h, Some(p)),
            None => (auth, None),
        }
    };
    let port = match port_s {
        None => if scheme == "https" { 443 } else { 80 },
        Some(p) if !p.is_empty() && p.len() <= 5 && p.bytes().all(|b| b.is_ascii_digit()) => {
            let n: u32 = p.parse().ok()?;
            if n == 0 || n > 65535 {
                return None;
            }
            n as u16
        }
        Some(_) => return None,
    };
    let host = if auth.starts_with('[') {
        let v6: Ipv6Addr = host_s.parse().ok()?;
        RelayHost::Ip(canonical_ip(IpAddr::V6(v6)))
    } else if let Ok(v4) = host_s.parse::<Ipv4Addr>() {
        RelayHost::Ip(IpAddr::V4(v4))
    } else {
        RelayHost::Name(normalize_hostname(host_s)?)
    };
    Some(RelayParts { scheme, host, port })
}

/// The one relay we accept (`SPIKE_RELAY_ALLOW_URL`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RelayAllow {
    parts: RelayParts,
}

impl RelayAllow {
    /// Parse + policy. `relay_config` when: malformed, more than one URL, n0/community host,
    /// local-only name (`localhost`, `*.lan`, ...), literal IP that isn't public, or `http`
    /// (the `Local` lane allows `http` only for host `10.73.0.254`).
    pub fn parse(s: &str, lane: Lane) -> Result<RelayAllow, GuardError> {
        let bad = || err(REASON_RELAY_CONFIG, s.trim().to_string());
        if s.contains(',') {
            return Err(bad());
        }
        let parts = parse_relay_parts(s).ok_or_else(bad)?;
        let local_http = lane == Lane::Local
            && parts.scheme == "http"
            && parts.host == RelayHost::Ip(IpAddr::V4(LOCAL_RELAY_HOST));
        if local_http {
            return Ok(RelayAllow { parts });
        }
        if parts.scheme != "https" {
            return Err(bad());
        }
        match &parts.host {
            RelayHost::Name(n) => {
                if is_n0_relay_host(n) || is_refused_local_name(n) {
                    return Err(bad());
                }
            }
            RelayHost::Ip(ip) => {
                if !is_public_egress_ip(*ip, Lane::Pilot) {
                    return Err(bad());
                }
            }
        }
        Ok(RelayAllow { parts })
    }

    /// Canonical form, e.g. `https://relay.example.org:443/`.
    pub fn url(&self) -> String {
        self.parts.canonical()
    }
}

/// `SPIKE_RELAY_ALLOW_URL`: unset/empty → `None` (no relay allowed); else `RelayAllow::parse`.
pub fn relay_allow_from_env(lane: Lane) -> Result<Option<RelayAllow>, GuardError> {
    match std::env::var(ENV_RELAY_ALLOW_URL) {
        Ok(v) if v.trim().is_empty() => Ok(None),
        Ok(v) => RelayAllow::parse(&v, lane).map(Some),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(err(REASON_RELAY_CONFIG, "<non-utf8>")),
    }
}

/// `None`/empty URL passes (no relay). Otherwise the URL must equal `allow` after
/// normalisation; no allow, malformed, or any other relay (n0 included) → `relay_refused`.
/// `check_relay_url(url)` == `check_relay_url_with(url, None)` (Alpha-3: refuse all).
pub fn check_relay_url_with(url: Option<&str>, allow: Option<&RelayAllow>) -> Result<(), GuardError> {
    let u = match url {
        None => return Ok(()),
        Some(u) if u.trim().is_empty() => return Ok(()),
        Some(u) => u,
    };
    let refused = || err(super::REASON_RELAY_REFUSED, u.to_string());
    let allow = allow.ok_or_else(refused)?;
    let parts = parse_relay_parts(u).ok_or_else(refused)?;
    if parts == allow.parts { Ok(()) } else { Err(refused()) }
}

/// `iroh_pilot` tickets: empty `direct_addrs` is fine only with a relay URL.
pub fn check_relay_required(direct_addrs: &[String], relay_url: Option<&str>) -> Result<(), GuardError> {
    let has_relay = relay_url.map(|u| !u.trim().is_empty()).unwrap_or(false);
    if direct_addrs.is_empty() && !has_relay {
        Err(err(REASON_RELAY_REQUIRED, "no direct_addrs and no relay_url"))
    } else {
        Ok(())
    }
}

// ---------------------------------------------------------------- 2. egress allowlist

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EgressAllow {
    /// Sorted, deduplicated `(host, port)`.
    entries: Vec<(String, u16)>,
}

impl EgressAllow {
    /// Comma-separated `host:port`. Empty, IP literal, wildcard, bad name, local-only name,
    /// or port not in `ALLOWED_EGRESS_PORTS` → `egress_allowlist_config`.
    pub fn parse(s: &str) -> Result<EgressAllow, GuardError> {
        let parts: Vec<&str> = s.split(',').map(str::trim).filter(|p| !p.is_empty()).collect();
        if parts.is_empty() {
            return Err(err(REASON_EGRESS_ALLOWLIST_CONFIG, "<empty>"));
        }
        let mut entries = Vec::with_capacity(parts.len());
        for p in parts {
            let bad = || err(REASON_EGRESS_ALLOWLIST_CONFIG, p.to_string());
            if p.contains(['*', '[', ']', '/', '@']) {
                return Err(bad());
            }
            let (h, port_s) = p.rsplit_once(':').ok_or_else(bad)?;
            if h.contains(':') || h.parse::<IpAddr>().is_ok() {
                return Err(bad());
            }
            if port_s.is_empty() || port_s.len() > 5 || !port_s.bytes().all(|b| b.is_ascii_digit()) {
                return Err(bad());
            }
            let port: u32 = port_s.parse().map_err(|_| bad())?;
            if port > 65535 || !ALLOWED_EGRESS_PORTS.contains(&(port as u16)) {
                return Err(bad());
            }
            let host = normalize_hostname(h).ok_or_else(bad)?;
            if is_refused_local_name(&host) {
                return Err(bad());
            }
            entries.push((host, port as u16));
        }
        entries.sort();
        entries.dedup();
        Ok(EgressAllow { entries })
    }

    pub fn entries(&self) -> &[(String, u16)] {
        &self.entries
    }

    /// Canonical text: one `host:port\n` per entry, sorted.
    pub fn canonical(&self) -> String {
        self.entries.iter().map(|(h, p)| format!("{h}:{p}\n")).collect()
    }

    /// `allowlist_version`: lowercase hex sha256 of `canonical()`.
    pub fn version(&self) -> String {
        sha256_hex(self.canonical().as_bytes())
    }

    pub fn contains(&self, host: &str, port: u16) -> bool {
        self.entries.iter().any(|(h, p)| h == host && *p == port)
    }
}

/// `SPIKE_EGRESS_ALLOWLIST` (required in pilot mode: unset/empty → `egress_allowlist_config`).
pub fn egress_allow_from_env() -> Result<EgressAllow, GuardError> {
    match std::env::var(ENV_EGRESS_ALLOWLIST) {
        Ok(v) => EgressAllow::parse(&v),
        Err(std::env::VarError::NotPresent) => Err(err(REASON_EGRESS_ALLOWLIST_CONFIG, "<unset>")),
        Err(std::env::VarError::NotUnicode(_)) => Err(err(REASON_EGRESS_ALLOWLIST_CONFIG, "<non-utf8>")),
    }
}

/// OPEN destination check. Literal IPs (any form), non-ASCII, malformed or unlisted
/// host:port → `egress_not_allowlisted`. Returns the normalised host.
pub fn check_egress_dest(host: &str, port: u16, allow: &EgressAllow) -> Result<String, GuardError> {
    let refused = || err(REASON_EGRESS_NOT_ALLOWLISTED, format!("{}:{}", host.trim(), port));
    let t = host.trim();
    let bare = t.strip_prefix('[').and_then(|r| r.strip_suffix(']')).unwrap_or(t);
    if bare.parse::<IpAddr>().is_ok() {
        return Err(refused());
    }
    let h = normalize_hostname(t).ok_or_else(refused)?;
    if allow.contains(&h, port) { Ok(h) } else { Err(refused()) }
}

/// SSRF check on a DNS answer. Empty → `egress_resolve_failed`; any non-public address
/// (one bad answer fails all) → `egress_resolved_non_public`. Returns the address to pin
/// (first answer, canonicalised).
pub fn check_egress_resolved(host: &str, ips: &[IpAddr], lane: Lane) -> Result<IpAddr, GuardError> {
    if ips.is_empty() {
        return Err(err(REASON_EGRESS_RESOLVE_FAILED, host.to_string()));
    }
    for ip in ips {
        if !is_public_egress_ip(*ip, lane) {
            return Err(err(REASON_EGRESS_RESOLVED_NON_PUBLIC, format!("{host}->{}", canonical_ip(*ip))));
        }
    }
    Ok(canonical_ip(ips[0]))
}

/// DNS lookup, injected so tests never touch the network.
pub trait Resolver {
    fn resolve(&self, host: &str, port: u16) -> Result<Vec<IpAddr>, String>;
}

/// `std::net::ToSocketAddrs` (blocking; system resolver). Not used by tests.
pub struct StdResolver;

impl Resolver for StdResolver {
    fn resolve(&self, host: &str, port: u16) -> Result<Vec<IpAddr>, String> {
        use std::net::ToSocketAddrs;
        (host, port)
            .to_socket_addrs()
            .map(|it| it.map(|sa| sa.ip()).collect())
            .map_err(|e| e.to_string())
    }
}

/// Full check for one OPEN: allowlist → resolve once → SSRF check → the `SocketAddr` to
/// connect to. Callers must connect to exactly this address (no second lookup), which is
/// the DNS-rebinding guard. Kill switch / floor checks come before this (see doc order).
pub fn resolve_and_pin(
    host: &str,
    port: u16,
    allow: &EgressAllow,
    resolver: &dyn Resolver,
    lane: Lane,
) -> Result<SocketAddr, GuardError> {
    let h = check_egress_dest(host, port, allow)?;
    let ips = resolver
        .resolve(&h, port)
        .map_err(|_| err(REASON_EGRESS_RESOLVE_FAILED, format!("{h}:{port}")))?;
    let ip = check_egress_resolved(&h, &ips, lane)?;
    Ok(SocketAddr::new(ip, port))
}

// ---------------------------------------------------------------- 3. kill switch

/// `SPIKE_PUBLIC_EGRESS`: exactly `1` is on; unset, `0` or anything else is off.
pub fn public_egress_env() -> bool {
    std::env::var(ENV_PUBLIC_EGRESS).map(|v| v.trim() == "1").unwrap_or(false)
}

/// Clamp to [100, 5000] ms; unset/invalid → 5000. Env can only make staleness stricter.
pub fn egress_state_max_age_ms_from(v: Option<&str>) -> u64 {
    match v.and_then(|s| s.trim().parse::<u64>().ok()) {
        Some(n) => n.clamp(MIN_EGRESS_STATE_MAX_AGE_MS, DEFAULT_EGRESS_STATE_MAX_AGE_MS),
        None => DEFAULT_EGRESS_STATE_MAX_AGE_MS,
    }
}

pub fn egress_state_max_age_ms_from_env() -> u64 {
    egress_state_max_age_ms_from(std::env::var(ENV_EGRESS_STATE_MAX_AGE_MS).ok().as_deref())
}

/// `public_egress_effective = env_on AND control flag on AND flag fresh`.
/// Timestamps are caller-supplied monotonic milliseconds (pure, testable).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EgressSwitch {
    env_on: bool,
    max_age_ms: u64,
    /// (flag_on, allowlist_version, received_at_ms)
    flag: Option<(bool, String, u64)>,
}

impl EgressSwitch {
    pub fn new(env_on: bool, max_age_ms: u64) -> Self {
        Self { env_on, max_age_ms, flag: None }
    }

    /// From `SPIKE_PUBLIC_EGRESS` + `SPIKE_EGRESS_STATE_MAX_AGE_MS`.
    pub fn from_env() -> Self {
        Self::new(public_egress_env(), egress_state_max_age_ms_from_env())
    }

    /// Record Control's `/v1/egress/state` (or an `EGRESS_STATE` frame) received at `now_ms`.
    pub fn update(&mut self, flag_on: bool, allowlist_version: &str, now_ms: u64) {
        self.flag = Some((flag_on, allowlist_version.to_string(), now_ms));
    }

    /// Ok, or `egress_off` with detail `local_off` / `no_state` / `flag_off` / `flag_stale`.
    /// A timestamp in the future also counts as stale (fail closed).
    pub fn check(&self, now_ms: u64) -> Result<(), GuardError> {
        if !self.env_on {
            return Err(err(REASON_EGRESS_OFF, "local_off"));
        }
        let (on, _, at) = self.flag.as_ref().ok_or_else(|| err(REASON_EGRESS_OFF, "no_state"))?;
        if now_ms < *at || now_ms - at > self.max_age_ms {
            return Err(err(REASON_EGRESS_OFF, "flag_stale"));
        }
        if !on {
            return Err(err(REASON_EGRESS_OFF, "flag_off"));
        }
        Ok(())
    }

    pub fn is_on(&self, now_ms: u64) -> bool {
        self.check(now_ms).is_ok()
    }

    /// `check` plus the allowlist version Control announced must equal ours.
    pub fn check_open(&self, now_ms: u64, local_version: &str) -> Result<(), GuardError> {
        self.check(now_ms)?;
        let remote = self.flag.as_ref().map(|f| f.1.as_str()).unwrap_or("");
        check_allowlist_version(local_version, remote)
    }
}

/// Gateway and Peer must agree on the allowlist → else `egress_allowlist_mismatch`.
pub fn check_allowlist_version(local: &str, remote: &str) -> Result<(), GuardError> {
    let (l, r) = (local.trim(), remote.trim());
    if l.is_empty() || !l.eq_ignore_ascii_case(r) {
        Err(err(REASON_EGRESS_ALLOWLIST_MISMATCH, format!("local={l} remote={r}")))
    } else {
        Ok(())
    }
}

/// `SPIKE_EGRESS_BYTE_CAP` (bytes per Peer per UTC day): unset/invalid → 50 MB. `0` = no egress.
pub fn egress_byte_cap_from(v: Option<&str>) -> u64 {
    v.and_then(|s| s.trim().parse::<u64>().ok()).unwrap_or(DEFAULT_EGRESS_BYTE_CAP)
}

pub fn egress_byte_cap_from_env() -> u64 {
    egress_byte_cap_from(std::env::var(ENV_EGRESS_BYTE_CAP).ok().as_deref())
}

/// `used + adding > cap` (or overflow) → `egress_budget_exceeded`.
pub fn check_egress_budget(used: u64, adding: u64, cap: u64) -> Result<(), GuardError> {
    let over = match used.checked_add(adding) {
        Some(total) => total > cap,
        None => true, // overflow fails closed
    };
    if over {
        Err(err(REASON_EGRESS_BUDGET_EXCEEDED, format!("used={used} add={adding} cap={cap}")))
    } else {
        Ok(())
    }
}

// ---------------------------------------------------------------- startup checks

/// Pilot builds start only with `SPIKE_TRANSPORT=iroh_pilot`.
pub fn check_transport_pilot(transport: &str) -> Result<(), GuardError> {
    if transport.trim() == TRANSPORT_PILOT {
        Ok(())
    } else {
        Err(err(REASON_TRANSPORT_NOT_PILOT, transport.trim().to_string()))
    }
}

/// `STRIPE_TEST_SECRET_KEY` must be `sk_test_…` / `rk_test_…`. The detail never contains
/// the key, only its prefix up to the second `_` (e.g. `sk_live_`).
pub fn check_stripe_test_key(key: &str) -> Result<(), GuardError> {
    let k = key.trim();
    if (k.starts_with("sk_test_") || k.starts_with("rk_test_")) && k.len() > 8 {
        return Ok(());
    }
    let prefix: String = match k.match_indices('_').nth(1) {
        Some((i, _)) => k[..=i].to_string(),
        None => "<redacted>".to_string(),
    };
    Err(err(REASON_STRIPE_LIVE_KEY_REFUSED, prefix))
}

// ---------------------------------------------------------------- sha256 (std only)

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/// Lowercase hex SHA-256 (FIPS 180-4). Small, std only; used for `allowlist_version`.
pub fn sha256_hex(data: &[u8]) -> String {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let mut msg = data.to_vec();
    let bitlen = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bitlen.to_be_bytes());
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([chunk[4 * i], chunk[4 * i + 1], chunk[4 * i + 2], chunk[4 * i + 3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *x = x.wrapping_add(y);
        }
    }
    h.iter().map(|x| format!("{x:08x}")).collect()
}

#[cfg(test)]
#[path = "pilot_tests.rs"]
mod tests;
