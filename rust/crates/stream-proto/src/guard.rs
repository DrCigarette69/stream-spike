//! A3.0 private-address guard (Alpha-3, `iroh_local`). std only, no iroh dependency.
//!
//! Spec: `docs/ALPHA3_IROH.md` (Hard rules + A3.3 ticket format). Python mirror:
//! `scripts/spike_private_guard.py` (same rules, env var and reason strings).
//!
//! Rules:
//! - Private = `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `::1`, `fd00::/8`.
//!   Everything else is refused (incl. `0.0.0.0`, `::`, `169.254/16`, `100.64/10`,
//!   `fc00::/8` outside `fd00::/8`, multicast, broadcast). IPv4-mapped IPv6
//!   (`::ffff:a.b.c.d`) is judged by the IPv4 rules.
//! - Narrowing: `SPIKE_IROH_ALLOW_CIDRS` (comma-separated CIDRs). An address must be
//!   private **and** inside one of them. Unset → doc default `10.73.0.0/24,127.0.0.0/8`.
//!   Set to empty → no narrowing (private ranges only). A listed CIDR that is not fully
//!   inside a private range (or does not parse) is a config error (`allowlist_config`).
//! - Relay URLs are refused in Alpha-3 (`relay_refused`); discovery on is refused
//!   (`discovery_refused`).
//!
//! Stable reason strings: see the `REASON_*` constants.
//!
//! Alpha-4 (A4.1) additions live in `guard::pilot` (re-exported); see `docs/ALPHA4_PILOT.md`.

use std::fmt;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};

/// A4.1 pilot guard (Alpha-4 `iroh_pilot`): relay allowlist, egress allowlist + SSRF check,
/// kill switch. Re-exported here so callers use `stream_proto::guard::*`.
pub mod pilot;
pub use pilot::*;

pub const ENV_ALLOW_CIDRS: &str = "SPIKE_IROH_ALLOW_CIDRS";
pub const DEFAULT_ALLOW_CIDRS: &str = "10.73.0.0/24,127.0.0.0/8";

pub const REASON_PUBLIC_ADDR: &str = "public_addr";
pub const REASON_BAD_ADDR: &str = "bad_addr";
pub const REASON_RELAY_REFUSED: &str = "relay_refused";
pub const REASON_DISCOVERY_REFUSED: &str = "discovery_refused";
pub const REASON_ALLOWLIST_MISS: &str = "allowlist_miss";
pub const REASON_ALLOWLIST_CONFIG: &str = "allowlist_config";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuardError {
    /// One of the `REASON_*` constants.
    pub reason: &'static str,
    /// The offending input (address, URL, CIDR, ...).
    pub detail: String,
}

impl GuardError {
    fn new(reason: &'static str, detail: impl Into<String>) -> Self {
        Self { reason, detail: detail.into() }
    }

    pub fn reason(&self) -> &'static str {
        self.reason
    }

    /// Log line per ALPHA3_IROH.md: `a3_refuse_non_private:<addr>` for public /
    /// allowlist misses, `a3_refuse_<reason>:<detail>` otherwise. A4 reasons
    /// (`pilot::A4_REASONS`, ALPHA4_PILOT.md) log as `a4_refuse_<reason>:<detail>`.
    pub fn log_line(&self) -> String {
        if pilot::A4_REASONS.contains(&self.reason) {
            return format!("a4_refuse_{}:{}", self.reason, self.detail);
        }
        match self.reason {
            REASON_PUBLIC_ADDR | REASON_ALLOWLIST_MISS => {
                format!("a3_refuse_non_private:{}", self.detail)
            }
            r => format!("a3_refuse_{}:{}", r, self.detail),
        }
    }
}

impl fmt::Display for GuardError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.reason, self.detail)
    }
}

impl std::error::Error for GuardError {}

/// IPv4-mapped IPv6 (`::ffff:a.b.c.d`) → IPv4; everything else unchanged.
pub fn canonical_ip(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => IpAddr::V6(v6),
        },
        v4 => v4,
    }
}

/// A parsed CIDR (host bits masked off).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cidr {
    net: IpAddr,
    prefix: u8,
}

fn mask_v4(a: Ipv4Addr, prefix: u8) -> u32 {
    let bits = u32::from(a);
    if prefix == 0 { 0 } else { bits & (u32::MAX << (32 - prefix as u32)) }
}

fn mask_v6(a: Ipv6Addr, prefix: u8) -> u128 {
    let bits = u128::from(a);
    if prefix == 0 { 0 } else { bits & (u128::MAX << (128 - prefix as u32)) }
}

impl Cidr {
    /// `"10.73.0.0/24"`, `"fd00::/8"`; a bare IP means a host route (/32 or /128).
    /// IPv4-mapped IPv6 CIDRs are not accepted (write the IPv4 form).
    pub fn parse(s: &str) -> Result<Cidr, GuardError> {
        let s = s.trim();
        let bad = || GuardError::new(REASON_ALLOWLIST_CONFIG, s.to_string());
        let (ip_s, pfx_s) = match s.split_once('/') {
            Some((a, b)) => (a, Some(b)),
            None => (s, None),
        };
        let ip: IpAddr = ip_s.parse().map_err(|_| bad())?;
        if let IpAddr::V6(v6) = ip {
            if v6.to_ipv4_mapped().is_some() {
                return Err(bad());
            }
        }
        let max = if ip.is_ipv4() { 32 } else { 128 };
        let prefix: u8 = match pfx_s {
            Some(p) if !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()) => {
                p.parse().map_err(|_| bad())?
            }
            Some(_) => return Err(bad()),
            None => max,
        };
        if prefix > max {
            return Err(bad());
        }
        let net = match ip {
            IpAddr::V4(a) => IpAddr::V4(Ipv4Addr::from(mask_v4(a, prefix))),
            IpAddr::V6(a) => IpAddr::V6(Ipv6Addr::from(mask_v6(a, prefix))),
        };
        Ok(Cidr { net, prefix })
    }

    const fn v4(a: u8, b: u8, c: u8, d: u8, prefix: u8) -> Cidr {
        Cidr { net: IpAddr::V4(Ipv4Addr::new(a, b, c, d)), prefix }
    }

    pub fn prefix(&self) -> u8 {
        self.prefix
    }

    pub fn network(&self) -> IpAddr {
        self.net
    }

    /// Does this CIDR contain `ip` (IPv4-mapped IPv6 is matched as IPv4)?
    pub fn contains(&self, ip: IpAddr) -> bool {
        match (self.net, canonical_ip(ip)) {
            (IpAddr::V4(n), IpAddr::V4(a)) => mask_v4(a, self.prefix) == u32::from(n),
            (IpAddr::V6(n), IpAddr::V6(a)) => mask_v6(a, self.prefix) == u128::from(n),
            _ => false,
        }
    }

    /// Is `other` entirely inside `self`?
    pub fn covers(&self, other: &Cidr) -> bool {
        other.prefix >= self.prefix && self.contains(other.net)
    }
}

impl fmt::Display for Cidr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}/{}", self.net, self.prefix)
    }
}

/// The only ranges Alpha-3 may bind/dial/advertise.
pub fn private_ranges() -> [Cidr; 6] {
    [
        Cidr::v4(127, 0, 0, 0, 8),
        Cidr::v4(10, 0, 0, 0, 8),
        Cidr::v4(172, 16, 0, 0, 12),
        Cidr::v4(192, 168, 0, 0, 16),
        Cidr { net: IpAddr::V6(Ipv6Addr::LOCALHOST), prefix: 128 },
        Cidr { net: IpAddr::V6(Ipv6Addr::new(0xfd00, 0, 0, 0, 0, 0, 0, 0)), prefix: 8 },
    ]
}

/// True only for the A3.0 private ranges (IPv4-mapped IPv6 judged as IPv4).
pub fn is_private_addr(ip: IpAddr) -> bool {
    let ip = canonical_ip(ip);
    private_ranges().iter().any(|c| c.contains(ip))
}

/// Parse a comma-separated allowlist. Empty / whitespace-only → `None` (no narrowing).
/// Any CIDR that does not parse or is not fully inside a private range → `allowlist_config`.
pub fn parse_allowlist(s: &str) -> Result<Option<Vec<Cidr>>, GuardError> {
    let parts: Vec<&str> = s.split(',').map(str::trim).filter(|p| !p.is_empty()).collect();
    if parts.is_empty() {
        return Ok(None);
    }
    let ranges = private_ranges();
    let mut out = Vec::with_capacity(parts.len());
    for p in parts {
        let c = Cidr::parse(p)?;
        if !ranges.iter().any(|r| r.covers(&c)) {
            return Err(GuardError::new(REASON_ALLOWLIST_CONFIG, p.to_string()));
        }
        out.push(c);
    }
    Ok(Some(out))
}

/// Allowlist from `SPIKE_IROH_ALLOW_CIDRS` (unset → `DEFAULT_ALLOW_CIDRS`).
pub fn allowlist_from_env() -> Result<Option<Vec<Cidr>>, GuardError> {
    match std::env::var(ENV_ALLOW_CIDRS) {
        Ok(v) => parse_allowlist(&v),
        Err(std::env::VarError::NotPresent) => parse_allowlist(DEFAULT_ALLOW_CIDRS),
        Err(std::env::VarError::NotUnicode(_)) => {
            Err(GuardError::new(REASON_ALLOWLIST_CONFIG, "<non-utf8>"))
        }
    }
}

/// Check one IP against the private rules + optional allowlist. Returns the canonical IP.
pub fn check_ip_with(ip: IpAddr, allow: Option<&[Cidr]>) -> Result<IpAddr, GuardError> {
    let ip = canonical_ip(ip);
    if !is_private_addr(ip) {
        return Err(GuardError::new(REASON_PUBLIC_ADDR, ip.to_string()));
    }
    if let Some(list) = allow {
        if !list.iter().any(|c| c.contains(ip)) {
            return Err(GuardError::new(REASON_ALLOWLIST_MISS, ip.to_string()));
        }
    }
    Ok(ip)
}

/// `check_ip_with` using the env allowlist (use for bind addresses; port 0 is fine there).
pub fn check_ip(ip: IpAddr) -> Result<IpAddr, GuardError> {
    let allow = allowlist_from_env()?;
    check_ip_with(ip, allow.as_deref())
}

/// Check one `"ip:port"` (`"[v6]:port"` for IPv6) with an explicit allowlist.
/// Port 0, hostnames and missing ports are `bad_addr`.
pub fn check_direct_addr_with(s: &str, allow: Option<&[Cidr]>) -> Result<SocketAddr, GuardError> {
    let sa: SocketAddr = s
        .trim()
        .parse()
        .map_err(|_| GuardError::new(REASON_BAD_ADDR, s.to_string()))?;
    if sa.port() == 0 {
        return Err(GuardError::new(REASON_BAD_ADDR, s.to_string()));
    }
    let ip = check_ip_with(sa.ip(), allow)?;
    Ok(SocketAddr::new(ip, sa.port()))
}

/// Check one `"ip:port"` using `SPIKE_IROH_ALLOW_CIDRS`.
pub fn check_direct_addr(s: &str) -> Result<SocketAddr, GuardError> {
    let allow = allowlist_from_env()?;
    check_direct_addr_with(s, allow.as_deref())
}

/// All-or-nothing: the first failing entry fails the whole list (A3.3 ticket rule).
pub fn check_direct_addrs_with(
    addrs: &[String],
    allow: Option<&[Cidr]>,
) -> Result<Vec<SocketAddr>, GuardError> {
    addrs.iter().map(|a| check_direct_addr_with(a, allow)).collect()
}

/// `check_direct_addrs_with` using `SPIKE_IROH_ALLOW_CIDRS`.
pub fn check_direct_addrs(addrs: &[String]) -> Result<Vec<SocketAddr>, GuardError> {
    let allow = allowlist_from_env()?;
    check_direct_addrs_with(addrs, allow.as_deref())
}

/// Alpha-3: any relay URL is refused; `None` (relay disabled) passes.
// TODO(A3.6): when the optional local iroh-relay lands, allow a relay URL only if A3.6 is
// enabled *and* its host is a literal IP that passes `check_ip` (e.g. 10.73.0.254).
// n0/community relay hosts stay refused.
pub fn check_relay_url(url: Option<&str>) -> Result<(), GuardError> {
    match url {
        None => Ok(()),
        Some(u) if u.trim().is_empty() => Ok(()),
        Some(u) => Err(GuardError::new(REASON_RELAY_REFUSED, u.to_string())),
    }
}

/// Alpha-3: DNS/pkarr/mDNS discovery must be off.
pub fn check_discovery(enabled: bool) -> Result<(), GuardError> {
    if enabled {
        Err(GuardError::new(REASON_DISCOVERY_REFUSED, "discovery"))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn guard_private_allow_edges() {
        for s in [
            "127.0.0.1", "127.255.255.255", "10.0.0.0", "10.255.255.255", "10.73.0.11",
            "172.16.0.0", "172.31.255.255", "192.168.0.0", "192.168.255.255", "::1",
            "fd00::", "fd12:3456::1", "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
            "::ffff:10.73.0.1", "::ffff:127.0.0.1", "::ffff:192.168.1.1",
        ] {
            assert!(is_private_addr(ip(s)), "{s} should be private");
        }
    }

    #[test]
    fn guard_private_deny_edges() {
        for s in [
            "0.0.0.0", "::", "1.1.1.1", "8.8.8.8", "9.255.255.255", "11.0.0.0",
            "126.255.255.255", "128.0.0.0", "172.15.255.255", "172.32.0.0",
            "192.167.255.255", "192.169.0.0", "169.254.1.1", "100.64.0.1",
            "100.127.255.255", "224.0.0.1", "255.255.255.255", "fc00::1",
            "fcff::1", "fe00::1", "fe80::1", "::2", "2001:db8::1", "2606:4700::1111",
            "ff02::1", "::ffff:8.8.8.8", "::ffff:169.254.0.1", "::ffff:0.0.0.0",
            "::127.0.0.1", "64:ff9b::a00:1",
        ] {
            assert!(!is_private_addr(ip(s)), "{s} should NOT be private");
        }
    }

    #[test]
    fn guard_direct_addr_parse_and_reasons() {
        let none: Option<&[Cidr]> = None;
        assert_eq!(
            check_direct_addr_with("10.73.0.1:4433", none).unwrap(),
            "10.73.0.1:4433".parse::<SocketAddr>().unwrap()
        );
        assert!(check_direct_addr_with("[fd00::1]:4433", none).is_ok());
        assert!(check_direct_addr_with("[::1]:1", none).is_ok());
        // mapped v6 canonicalises to v4
        assert_eq!(
            check_direct_addr_with("[::ffff:10.0.0.5]:9", none).unwrap(),
            "10.0.0.5:9".parse::<SocketAddr>().unwrap()
        );
        for (s, r) in [
            ("8.8.8.8:53", REASON_PUBLIC_ADDR),
            ("[2001:db8::1]:443", REASON_PUBLIC_ADDR),
            ("[::ffff:1.2.3.4]:80", REASON_PUBLIC_ADDR),
            ("0.0.0.0:4433", REASON_PUBLIC_ADDR),
            ("10.0.0.1", REASON_BAD_ADDR),
            ("10.0.0.1:0", REASON_BAD_ADDR),
            ("10.0.0.1:65536", REASON_BAD_ADDR),
            ("localhost:4433", REASON_BAD_ADDR),
            ("fd00::1:4433", REASON_BAD_ADDR),
            ("", REASON_BAD_ADDR),
            ("10.0.0.256:1", REASON_BAD_ADDR),
        ] {
            assert_eq!(check_direct_addr_with(s, none).unwrap_err().reason, r, "{s}");
        }
    }

    #[test]
    fn guard_direct_addrs_all_or_nothing() {
        let none: Option<&[Cidr]> = None;
        let good = vec!["10.73.0.1:4433".to_string(), "127.0.0.1:4433".to_string()];
        assert_eq!(check_direct_addrs_with(&good, none).unwrap().len(), 2);
        let mixed = vec!["10.73.0.1:4433".to_string(), "1.2.3.4:4433".to_string()];
        let e = check_direct_addrs_with(&mixed, none).unwrap_err();
        assert_eq!(e.reason, REASON_PUBLIC_ADDR);
        assert_eq!(e.log_line(), "a3_refuse_non_private:1.2.3.4");
        assert!(check_direct_addrs_with(&[], none).unwrap().is_empty());
    }

    #[test]
    fn guard_allowlist_narrowing() {
        let allow = parse_allowlist(DEFAULT_ALLOW_CIDRS).unwrap().unwrap();
        let a = Some(allow.as_slice());
        assert!(check_direct_addr_with("10.73.0.12:4433", a).is_ok());
        assert!(check_direct_addr_with("127.0.0.1:4433", a).is_ok());
        assert!(check_direct_addr_with("[::ffff:10.73.0.1]:1", a).is_ok());
        let e = check_direct_addr_with("10.74.0.1:4433", a).unwrap_err();
        assert_eq!(e.reason, REASON_ALLOWLIST_MISS);
        assert_eq!(e.log_line(), "a3_refuse_non_private:10.74.0.1");
        assert_eq!(check_direct_addr_with("192.168.1.1:1", a).unwrap_err().reason, REASON_ALLOWLIST_MISS);
        assert_eq!(check_direct_addr_with("[::1]:1", a).unwrap_err().reason, REASON_ALLOWLIST_MISS);
        // public still public_addr even when narrowed
        assert_eq!(check_direct_addr_with("8.8.8.8:1", a).unwrap_err().reason, REASON_PUBLIC_ADDR);
        // v6 allowlist
        let v6 = parse_allowlist("fd73::/16").unwrap().unwrap();
        assert!(check_direct_addr_with("[fd73::5]:1", Some(&v6)).is_ok());
        assert_eq!(check_direct_addr_with("[fd74::5]:1", Some(&v6)).unwrap_err().reason, REASON_ALLOWLIST_MISS);
        // empty → no narrowing
        assert_eq!(parse_allowlist("").unwrap(), None);
        assert_eq!(parse_allowlist(" , ").unwrap(), None);
        // host bits masked, bare IP = host route
        let h = parse_allowlist("10.73.0.5/24, 10.9.9.9").unwrap().unwrap();
        assert_eq!(h[0].to_string(), "10.73.0.0/24");
        assert_eq!(h[1].to_string(), "10.9.9.9/32");
    }

    #[test]
    fn guard_allowlist_config_errors() {
        for s in [
            "0.0.0.0/0", "8.8.8.0/24", "10.0.0.0/7", "172.16.0.0/11", "192.168.0.0/15",
            "100.64.0.0/10", "fc00::/7", "fc00::/8", "::/0", "::1/127", "10.73.0.0/24,1.1.1.1",
            "10.0.0.0/33", "10.0.0.0/", "10.0.0.0/x", "nonsense", "::ffff:10.0.0.0/104",
        ] {
            assert_eq!(parse_allowlist(s).unwrap_err().reason, REASON_ALLOWLIST_CONFIG, "{s}");
        }
        for s in ["10.0.0.0/8", "172.16.0.0/12", "172.20.0.0/16", "192.168.0.0/16", "::1", "fd00::/8", "fd00::/64", "127.0.0.0/8"] {
            assert!(parse_allowlist(s).unwrap().is_some(), "{s}");
        }
    }

    /// Only test that touches the process env (keeps parallel tests race-free).
    #[test]
    fn guard_env_allowlist() {
        let saved = std::env::var(ENV_ALLOW_CIDRS).ok();
        std::env::remove_var(ENV_ALLOW_CIDRS);
        assert_eq!(check_direct_addr("192.168.1.1:1").unwrap_err().reason, REASON_ALLOWLIST_MISS);
        assert!(check_direct_addr("10.73.0.1:1").is_ok());
        std::env::set_var(ENV_ALLOW_CIDRS, "");
        assert!(check_direct_addr("192.168.1.1:1").is_ok());
        assert!(check_ip(ip("172.16.0.1")).is_ok());
        std::env::set_var(ENV_ALLOW_CIDRS, "0.0.0.0/0");
        assert_eq!(check_direct_addr("10.0.0.1:1").unwrap_err().reason, REASON_ALLOWLIST_CONFIG);
        assert_eq!(
            check_direct_addrs(&["10.0.0.1:1".to_string()]).unwrap_err().reason,
            REASON_ALLOWLIST_CONFIG
        );
        match saved {
            Some(v) => std::env::set_var(ENV_ALLOW_CIDRS, v),
            None => std::env::remove_var(ENV_ALLOW_CIDRS),
        }
    }

    #[test]
    fn guard_relay_and_discovery() {
        assert!(check_relay_url(None).is_ok());
        assert!(check_relay_url(Some("")).is_ok());
        for u in [
            "https://euw1-1.relay.iroh.network./",
            "https://use1-1.relay.n0.iroh.iroh.link/",
            "http://10.73.0.254:3340",
            "http://127.0.0.1:3340",
        ] {
            let e = check_relay_url(Some(u)).unwrap_err();
            assert_eq!(e.reason, REASON_RELAY_REFUSED);
            assert!(e.log_line().starts_with("a3_refuse_relay_refused:"));
        }
        assert!(check_discovery(false).is_ok());
        assert_eq!(check_discovery(true).unwrap_err().reason, REASON_DISCOVERY_REFUSED);
    }
}
