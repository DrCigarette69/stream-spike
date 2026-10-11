//! A4.1 pilot guard table tests (no network: resolver is injected).
use super::*;
use crate::guard::{check_relay_url, REASON_RELAY_REFUSED};
use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};

fn ip(s: &str) -> IpAddr {
    s.parse().unwrap()
}

const OURS: &str = "https://relay.stream-pilot.example.org/";

#[test]
fn pilot_sha256_vectors() {
    assert_eq!(sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    assert_eq!(
        sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
    );
    let a = vec![b'a'; 1000];
    assert_eq!(sha256_hex(&a), "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3");
}

#[test]
fn pilot_public_egress_ip_table() {
    for s in [
        "1.1.1.1", "8.8.8.8", "93.184.215.14", "11.0.0.1", "100.63.255.255", "100.128.0.0",
        "169.253.255.255", "172.32.0.1", "192.0.1.1", "198.17.255.255", "198.20.0.0",
        "223.255.255.255", "2606:4700::1111", "2a00:1450:4001::1", "::ffff:8.8.8.8", "2001:200::1",
    ] {
        assert!(is_public_egress_ip(ip(s), Lane::Pilot), "{s} should be public");
    }
    for s in [
        "0.0.0.0", "0.1.2.3", "10.0.0.1", "10.73.0.254", "100.64.0.1", "100.127.255.255",
        "127.0.0.1", "127.255.255.255", "169.254.169.254", "169.254.0.1", "172.16.0.1",
        "172.31.255.255", "192.0.0.8", "192.0.2.1", "192.88.99.1", "192.168.1.1", "198.18.0.1",
        "198.19.255.255", "198.51.100.10", "203.0.113.5", "224.0.0.1", "239.255.255.255",
        "240.0.0.1", "255.255.255.255", "::", "::1", "::127.0.0.1", "::ffff:10.0.0.1",
        "::ffff:169.254.169.254", "::ffff:127.0.0.1", "64:ff9b::a9fe:a9fe", "64:ff9b::808:808",
        "64:ff9b:1::1", "100::1", "fc00::1", "fd00::1", "fe80::1", "fec0::1", "ff02::1",
        "2001::1", "2001:db8::1", "2001:10::1", "2001:20::1", "2002:a00:1::1", "3fff::1",
        "4000::1", "1000::1",
    ] {
        assert!(!is_public_egress_ip(ip(s), Lane::Pilot), "{s} should NOT be public");
    }
    // Local lane: only TEST-NET-2 is added; private / metadata stay refused.
    assert!(is_public_egress_ip(ip("198.51.100.10"), Lane::Local));
    assert!(is_public_egress_ip(ip("::ffff:198.51.100.10"), Lane::Local));
    for s in ["10.73.0.254", "127.0.0.1", "169.254.169.254", "192.0.2.1", "203.0.113.1"] {
        assert!(!is_public_egress_ip(ip(s), Lane::Local), "{s} local");
    }
}

#[test]
fn pilot_hostname_normalisation() {
    assert_eq!(normalize_hostname(" Echo.Example.ORG. ").as_deref(), Some("echo.example.org"));
    assert_eq!(normalize_hostname("xn--bcher-kva.example").as_deref(), Some("xn--bcher-kva.example"));
    for s in [
        "", "localhost", "example", "bücher.example", "a..b", ".a.b", "-a.b", "a-.b", "a_b.com",
        "127.1", "1.2.3.4", "10.0.0.1", "1.0x7f", "a.b/c", "a.b:443", &"a".repeat(64), "*.example.org",
    ] {
        assert!(normalize_hostname(s).is_none(), "{s:?} should not normalise");
    }
}

#[test]
fn pilot_relay_allow_parse_and_config_errors() {
    let a = RelayAllow::parse(OURS, Lane::Pilot).unwrap();
    assert_eq!(a.url(), "https://relay.stream-pilot.example.org:443/");
    assert_eq!(RelayAllow::parse("HTTPS://Relay.Stream-Pilot.Example.ORG.:443", Lane::Pilot).unwrap(), a);
    assert_eq!(RelayAllow::parse("https://1.2.3.4:8443/", Lane::Pilot).unwrap().url(), "https://1.2.3.4:8443/");
    assert_eq!(RelayAllow::parse("https://[2606:4700::1]/", Lane::Pilot).unwrap().url(), "https://[2606:4700::1]:443/");
    for s in [
        "", "relay.example.org", "ftp://relay.example.org/", "http://relay.example.org/",
        "https://euw1-1.relay.iroh.network./", "https://use1-1.relay.n0.iroh.iroh.link/",
        "https://iroh.link/", "https://relay.n0.example.org/", "https://localhost/",
        "https://relay.lan/", "https://relay.internal/", "https://10.73.0.254/", "https://127.0.0.1/",
        "https://169.254.169.254/", "https://[::1]/", "https://[fd00::1]/", "https://198.51.100.1/",
        "https://u:p@relay.example.org/", "https://relay.example.org/path", "https://relay.example.org/?x=1",
        "https://relay.example.org/#f", "https://relay.example.org:0/", "https://relay.example.org:65536/",
        "https://relay.example.org:/", "https://relay.example.org:x/", "https://a.example.org/,https://b.example.org/",
        "http://10.73.0.254:3340/", // http needs the Local lane
    ] {
        let e = RelayAllow::parse(s, Lane::Pilot).unwrap_err();
        assert_eq!(e.reason, REASON_RELAY_CONFIG, "{s:?}");
        assert!(e.log_line().starts_with("a4_refuse_relay_config:"), "{s:?}");
    }
    // Local lane: exactly http://10.73.0.254:<port>/ extra.
    assert_eq!(RelayAllow::parse("http://10.73.0.254:3340", Lane::Local).unwrap().url(), "http://10.73.0.254:3340/");
    for s in ["http://10.73.0.253:3340/", "http://127.0.0.1:3340/", "http://relay.example.org/", "https://10.73.0.254/"] {
        assert_eq!(RelayAllow::parse(s, Lane::Local).unwrap_err().reason, REASON_RELAY_CONFIG, "{s}");
    }
}

#[test]
fn pilot_check_relay_url_with_exact_match() {
    let a = RelayAllow::parse(OURS, Lane::Pilot).unwrap();
    for u in [OURS, "https://relay.stream-pilot.example.org", "https://RELAY.stream-pilot.example.org.:443/"] {
        assert!(check_relay_url_with(Some(u), Some(&a)).is_ok(), "{u}");
    }
    assert!(check_relay_url_with(None, Some(&a)).is_ok());
    assert!(check_relay_url_with(Some("  "), Some(&a)).is_ok());
    for u in [
        "https://euw1-1.relay.iroh.network./", "https://use1-1.relay.n0.iroh.iroh.link/",
        "https://other-relay.example.org/", "https://relay.stream-pilot.example.org:8443/",
        "http://relay.stream-pilot.example.org/", "https://relay.stream-pilot.example.org.evil.com/",
        "https://relay.stream-pilot.example.org/x", "https://user@relay.stream-pilot.example.org/",
        "https://relay.stream-pilot.example.org/?a", "garbage", "http://10.73.0.254:3340",
    ] {
        let e = check_relay_url_with(Some(u), Some(&a)).unwrap_err();
        assert_eq!(e.reason, REASON_RELAY_REFUSED, "{u}");
        assert!(e.log_line().starts_with("a3_refuse_relay_refused:"));
    }
    // No allow → refuse all (== Alpha-3 check_relay_url, which is unchanged).
    for u in [OURS, "http://10.73.0.254:3340"] {
        assert_eq!(check_relay_url_with(Some(u), None).unwrap_err().reason, REASON_RELAY_REFUSED);
        assert_eq!(check_relay_url(Some(u)).unwrap_err().reason, REASON_RELAY_REFUSED);
    }
    let l = RelayAllow::parse("http://10.73.0.254:3340/", Lane::Local).unwrap();
    assert!(check_relay_url_with(Some("http://10.73.0.254:3340"), Some(&l)).is_ok());
    assert!(check_relay_url_with(Some("http://10.73.0.254:3341"), Some(&l)).is_err());
}

#[test]
fn pilot_relay_required() {
    assert!(check_relay_required(&[], Some(OURS)).is_ok());
    assert!(check_relay_required(&["10.73.0.1:1".into()], None).is_ok());
    for r in [None, Some(""), Some("  ")] {
        let e = check_relay_required(&[], r).unwrap_err();
        assert_eq!(e.reason, REASON_RELAY_REQUIRED);
        assert!(e.log_line().starts_with("a4_refuse_relay_required:"));
    }
}

#[test]
fn pilot_egress_allowlist_parse() {
    let a = EgressAllow::parse(" Echo.Example.org.:443 , example.com:443,echo.example.org:443").unwrap();
    assert_eq!(a.entries(), &[("echo.example.org".to_string(), 443), ("example.com".to_string(), 443)]);
    assert_eq!(a.canonical(), "echo.example.org:443\nexample.com:443\n");
    assert_eq!(a.version(), sha256_hex(b"echo.example.org:443\nexample.com:443\n"));
    // order / case / dup independent version
    let b = EgressAllow::parse("example.com:443,ECHO.example.org:443").unwrap();
    assert_eq!(a.version(), b.version());
    assert_ne!(a.version(), EgressAllow::parse("example.com:443").unwrap().version());
    for s in [
        "", " , ", "example.com", "example.com:", "example.com:80", "example.com:8443", "example.com:0",
        "example.com:65536", "example.com:44x", "*.example.com:443", "*:443", "1.2.3.4:443",
        "198.51.100.10:443", "[2606:4700::1]:443", "2606:4700::1:443", "127.1:443", "localhost:443",
        "printer.lan:443", "metadata.google.internal:443", "a.local:443", "x.home.arpa:443",
        "bücher.example:443", "https://example.com:443", "example.com/x:443", "u@example.com:443",
        "example.com:443,1.1.1.1:443", "intranet:443",
    ] {
        let e = EgressAllow::parse(s).unwrap_err();
        assert_eq!(e.reason, REASON_EGRESS_ALLOWLIST_CONFIG, "{s:?}");
        assert!(e.log_line().starts_with("a4_refuse_egress_allowlist_config:"));
    }
}

#[test]
fn pilot_egress_dest_check() {
    let a = EgressAllow::parse("echo.example.org:443,example.com:443").unwrap();
    assert_eq!(check_egress_dest("echo.example.org", 443, &a).unwrap(), "echo.example.org");
    assert_eq!(check_egress_dest("ECHO.example.org.", 443, &a).unwrap(), "echo.example.org");
    for (h, p) in [
        ("echo.example.org", 80), ("echo.example.org", 8443), ("evil.example.org", 443),
        ("example.com.evil.net", 443), ("sub.example.com", 443), ("93.184.215.14", 443),
        ("[2606:4700::1]", 443), ("2606:4700::1", 443), ("169.254.169.254", 443), ("127.1", 443),
        ("", 443), ("bücher.example", 443), ("localhost", 443),
    ] {
        let e = check_egress_dest(h, p, &a).unwrap_err();
        assert_eq!(e.reason, REASON_EGRESS_NOT_ALLOWLISTED, "{h}:{p}");
        assert!(e.log_line().starts_with("a4_refuse_egress_not_allowlisted:"));
    }
}

#[test]
fn pilot_egress_resolved_check() {
    let h = "echo.example.org";
    assert_eq!(check_egress_resolved(h, &[ip("1.2.3.4"), ip("2606:4700::1")], Lane::Pilot).unwrap(), ip("1.2.3.4"));
    assert_eq!(check_egress_resolved(h, &[ip("::ffff:1.2.3.4")], Lane::Pilot).unwrap(), ip("1.2.3.4"));
    assert_eq!(check_egress_resolved(h, &[], Lane::Pilot).unwrap_err().reason, REASON_EGRESS_RESOLVE_FAILED);
    for bad in [
        vec![ip("10.0.0.1")], vec![ip("127.0.0.1")], vec![ip("169.254.169.254")], vec![ip("100.64.0.1")],
        vec![ip("fe80::1")], vec![ip("64:ff9b::a9fe:a9fe")], vec![ip("::ffff:192.168.0.1")],
        vec![ip("1.2.3.4"), ip("10.0.0.1")], // mixed answer fails whole
        vec![ip("198.51.100.10")],          // TEST-NET-2 is not public in Pilot
    ] {
        let e = check_egress_resolved(h, &bad, Lane::Pilot).unwrap_err();
        assert_eq!(e.reason, REASON_EGRESS_RESOLVED_NON_PUBLIC, "{bad:?}");
        assert!(e.log_line().starts_with("a4_refuse_egress_resolved_non_public:echo.example.org->"));
    }
    assert_eq!(check_egress_resolved(h, &[ip("198.51.100.10")], Lane::Local).unwrap(), ip("198.51.100.10"));
    assert_eq!(
        check_egress_resolved(h, &[ip("169.254.169.254")], Lane::Local).unwrap_err().reason,
        REASON_EGRESS_RESOLVED_NON_PUBLIC
    );
}

/// Injected resolver: fixed answers; `rebind` returns its second list from the 2nd call on.
struct FakeResolver {
    answers: HashMap<String, Vec<IpAddr>>,
    rebind: Option<(String, Vec<IpAddr>)>,
    calls: AtomicUsize,
}

impl FakeResolver {
    fn new(pairs: &[(&str, &[&str])]) -> Self {
        Self {
            answers: pairs.iter().map(|(h, ips)| (h.to_string(), ips.iter().map(|s| ip(s)).collect())).collect(),
            rebind: None,
            calls: AtomicUsize::new(0),
        }
    }
}

impl Resolver for FakeResolver {
    fn resolve(&self, host: &str, _port: u16) -> Result<Vec<IpAddr>, String> {
        let n = self.calls.fetch_add(1, Ordering::SeqCst);
        if let Some((h, second)) = &self.rebind {
            if h == host && n >= 1 {
                return Ok(second.clone());
            }
        }
        self.answers.get(host).cloned().ok_or_else(|| "NXDOMAIN".to_string())
    }
}

#[test]
fn pilot_resolve_and_pin() {
    let a = EgressAllow::parse("echo.example.org:443,meta.example.org:443,gone.example.org:443,empty.example.org:443").unwrap();
    let r = FakeResolver::new(&[
        ("echo.example.org", &["93.184.215.14", "2606:4700::6810"]),
        ("meta.example.org", &["169.254.169.254"]),
        ("empty.example.org", &[]),
    ]);
    assert_eq!(resolve_and_pin("Echo.example.org", 443, &a, &r, Lane::Pilot).unwrap(), "93.184.215.14:443".parse().unwrap());
    let calls = r.calls.load(Ordering::SeqCst);
    // off-allowlist: refused before any lookup
    assert_eq!(resolve_and_pin("evil.example.org", 443, &a, &r, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_NOT_ALLOWLISTED);
    assert_eq!(resolve_and_pin("8.8.8.8", 443, &a, &r, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_NOT_ALLOWLISTED);
    assert_eq!(r.calls.load(Ordering::SeqCst), calls, "no DNS for off-allowlist");
    assert_eq!(resolve_and_pin("meta.example.org", 443, &a, &r, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_RESOLVED_NON_PUBLIC);
    assert_eq!(resolve_and_pin("gone.example.org", 443, &a, &r, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_RESOLVE_FAILED);
    assert_eq!(resolve_and_pin("empty.example.org", 443, &a, &r, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_RESOLVE_FAILED);

    // DNS rebinding stand-in: first answer public, second private → second OPEN refused.
    let mut rb = FakeResolver::new(&[("echo.example.org", &["93.184.215.14"])]);
    rb.rebind = Some(("echo.example.org".into(), vec![ip("127.0.0.1")]));
    assert!(resolve_and_pin("echo.example.org", 443, &a, &rb, Lane::Pilot).is_ok());
    assert_eq!(resolve_and_pin("echo.example.org", 443, &a, &rb, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_RESOLVED_NON_PUBLIC);

    // Local lane stand-in site on TEST-NET-2.
    let l = FakeResolver::new(&[("echo.example.org", &["198.51.100.10"])]);
    assert_eq!(resolve_and_pin("echo.example.org", 443, &a, &l, Lane::Local).unwrap(), "198.51.100.10:443".parse().unwrap());
    assert_eq!(resolve_and_pin("echo.example.org", 443, &a, &l, Lane::Pilot).unwrap_err().reason, REASON_EGRESS_RESOLVED_NON_PUBLIC);
}

#[test]
fn pilot_kill_switch() {
    let v = EgressAllow::parse("echo.example.org:443").unwrap().version();
    let detail = |s: &EgressSwitch, t| s.check(t).unwrap_err().detail;

    let mut off = EgressSwitch::new(false, 5000);
    assert_eq!(detail(&off, 0), "local_off");
    off.update(true, &v, 0);
    assert_eq!(detail(&off, 1), "local_off"); // env off wins even when Control says on
    assert_eq!(off.check(1).unwrap_err().reason, REASON_EGRESS_OFF);

    let mut s = EgressSwitch::new(true, 5000);
    assert_eq!(detail(&s, 0), "no_state");
    s.update(true, &v, 1_000);
    assert!(s.is_on(1_000) && s.is_on(6_000));
    assert_eq!(detail(&s, 6_001), "flag_stale");
    assert_eq!(detail(&s, 999), "flag_stale"); // future timestamp fails closed
    s.update(false, &v, 7_000);
    assert_eq!(detail(&s, 7_000), "flag_off");
    s.update(true, &v, 8_000);
    assert!(s.check_open(8_500, &v).is_ok());
    let e = s.check_open(8_500, &sha256_hex(b"other")).unwrap_err();
    assert_eq!(e.reason, REASON_EGRESS_ALLOWLIST_MISMATCH);
    assert!(e.log_line().starts_with("a4_refuse_egress_allowlist_mismatch:"));
    assert_eq!(s.check_open(20_000, &v).unwrap_err().reason, REASON_EGRESS_OFF);
    assert!(s.check(8_500).is_ok());

    assert!(check_allowlist_version(&v, &v.to_uppercase()).is_ok());
    assert_eq!(check_allowlist_version("", "").unwrap_err().reason, REASON_EGRESS_ALLOWLIST_MISMATCH);

    // max age: unset/invalid → 5000, clamp to [100, 5000] (env can only narrow)
    assert_eq!(egress_state_max_age_ms_from(None), 5000);
    assert_eq!(egress_state_max_age_ms_from(Some("x")), 5000);
    assert_eq!(egress_state_max_age_ms_from(Some("60000")), 5000);
    assert_eq!(egress_state_max_age_ms_from(Some("2000")), 2000);
    assert_eq!(egress_state_max_age_ms_from(Some("0")), 100);
}

#[test]
fn pilot_budget_transport_stripe() {
    assert!(check_egress_budget(0, 50_000_000, DEFAULT_EGRESS_BYTE_CAP).is_ok());
    let e = check_egress_budget(49_999_999, 2, DEFAULT_EGRESS_BYTE_CAP).unwrap_err();
    assert_eq!(e.reason, REASON_EGRESS_BUDGET_EXCEEDED);
    assert!(e.log_line().starts_with("a4_refuse_egress_budget_exceeded:"));
    assert_eq!(check_egress_budget(u64::MAX, 1, u64::MAX).unwrap_err().reason, REASON_EGRESS_BUDGET_EXCEEDED);
    assert_eq!(check_egress_budget(0, 1, 0).unwrap_err().reason, REASON_EGRESS_BUDGET_EXCEEDED);
    assert_eq!(egress_byte_cap_from(None), 50_000_000);
    assert_eq!(egress_byte_cap_from(Some("bad")), 50_000_000);
    assert_eq!(egress_byte_cap_from(Some("1000")), 1000);

    assert!(check_transport_pilot("iroh_pilot").is_ok());
    for t in ["", "fake_relay", "iroh_loopback", "iroh_local", "IROH_PILOT"] {
        assert_eq!(check_transport_pilot(t).unwrap_err().reason, REASON_TRANSPORT_NOT_PILOT, "{t}");
    }

    assert!(check_stripe_test_key("sk_test_51abcDEF").is_ok());
    assert!(check_stripe_test_key("rk_test_51abcDEF").is_ok());
    for (k, d) in [
        ("sk_live_51SECRETSECRET", "sk_live_"), ("rk_live_51SECRET", "rk_live_"), ("pk_test_51x", "pk_test_"),
        ("sk_test_", "sk_test_"), ("", "<redacted>"), ("whatever", "<redacted>"),
    ] {
        let e = check_stripe_test_key(k).unwrap_err();
        assert_eq!(e.reason, REASON_STRIPE_LIVE_KEY_REFUSED, "{k}");
        assert_eq!(e.detail, d);
        assert!(!e.log_line().contains("SECRET"));
    }
}

/// Only pilot test touching the process env (vars unused by the A3 env test).
#[test]
fn pilot_env() {
    let keys = [ENV_RELAY_ALLOW_URL, ENV_EGRESS_ALLOWLIST, ENV_PUBLIC_EGRESS, ENV_EGRESS_STATE_MAX_AGE_MS, ENV_EGRESS_BYTE_CAP];
    let saved: Vec<_> = keys.iter().map(|k| std::env::var(k).ok()).collect();
    for k in keys {
        std::env::remove_var(k);
    }
    assert_eq!(relay_allow_from_env(Lane::Pilot).unwrap(), None);
    assert_eq!(egress_allow_from_env().unwrap_err().reason, REASON_EGRESS_ALLOWLIST_CONFIG);
    assert!(!public_egress_env());
    assert!(!EgressSwitch::from_env().is_on(0));
    assert_eq!(egress_byte_cap_from_env(), DEFAULT_EGRESS_BYTE_CAP);
    assert_eq!(egress_state_max_age_ms_from_env(), 5000);

    std::env::set_var(ENV_RELAY_ALLOW_URL, OURS);
    assert!(relay_allow_from_env(Lane::Pilot).unwrap().is_some());
    std::env::set_var(ENV_RELAY_ALLOW_URL, "https://euw1-1.relay.iroh.network./");
    assert_eq!(relay_allow_from_env(Lane::Pilot).unwrap_err().reason, REASON_RELAY_CONFIG);
    std::env::set_var(ENV_EGRESS_ALLOWLIST, "echo.example.org:443");
    assert_eq!(egress_allow_from_env().unwrap().entries().len(), 1);
    for (v, on) in [("1", true), (" 1 ", true), ("0", false), ("true", false), ("yes", false), ("", false)] {
        std::env::set_var(ENV_PUBLIC_EGRESS, v);
        assert_eq!(public_egress_env(), on, "{v:?}");
    }
    std::env::set_var(ENV_PUBLIC_EGRESS, "1");
    std::env::set_var(ENV_EGRESS_STATE_MAX_AGE_MS, "1000");
    let mut s = EgressSwitch::from_env();
    s.update(true, "v", 0);
    assert!(s.is_on(1000) && !s.is_on(1001));

    for (k, v) in keys.iter().zip(saved) {
        match v {
            Some(v) => std::env::set_var(k, v),
            None => std::env::remove_var(k),
        }
    }
}

#[test]
fn pilot_lane_build() {
    assert_eq!(Lane::build(), if cfg!(feature = "a4_local") { Lane::Local } else { Lane::Pilot });
}

// ---------------------------------------------------------------- A4.1 amendment: sslip.io / pins

/// Resolver that must never be called (pinned entries skip DNS).
struct PanicResolver;

impl Resolver for PanicResolver {
    fn resolve(&self, host: &str, _port: u16) -> Result<Vec<IpAddr>, String> {
        panic!("resolver called for pinned host {host}");
    }
}

#[test]
fn pilot_sslip_embedded_ipv4() {
    let v4 = |s: &str| s.parse::<Ipv4Addr>().unwrap();
    assert!(is_sslip_host("sslip.io") && is_sslip_host("a.sslip.io") && !is_sslip_host("notsslip.io"));
    for (h, want) in [
        ("relay.93.184.215.14.sslip.io", "93.184.215.14"),
        ("93.184.215.14.sslip.io", "93.184.215.14"),
        ("93-184-215-14.sslip.io", "93.184.215.14"),
        ("echo.93-184-215-14.sslip.io", "93.184.215.14"),
        ("x.1.93.184.215.14.sslip.io", "93.184.215.14"), // the four labels immediately before
        ("203.0.113.7.sslip.io", "203.0.113.7"),         // parsed; policy rejects it later
        ("0.0.0.0.sslip.io", "0.0.0.0"),
    ] {
        assert_eq!(sslip_embedded_ipv4(h), Some(v4(want)), "{h}");
    }
    for h in [
        "sslip.io", "relay.sslip.io", "1.2.3.sslip.io", "1-2-3.sslip.io", "1-2-3-4-5.sslip.io",
        "relay-93-184-215-14.sslip.io", "93.184.215.256.sslip.io", "93.184.215.014.sslip.io",
        "93-184-215-014.sslip.io", "93.184.215.14.sslip.io.evil.com", "93.184.215.14.example.org",
        "93.184.215.14.xsslip.io",
    ] {
        assert_eq!(sslip_embedded_ipv4(h), None, "{h}");
    }
}

#[test]
fn pilot_sslip_allowlist_parse() {
    let a = EgressAllow::parse_for("Echo.93.184.215.14.SSLIP.io.:443, relay.8-8-8-8.sslip.io:443", Lane::Pilot).unwrap();
    assert_eq!(a.pin("echo.93.184.215.14.sslip.io", 443), Some(ip("93.184.215.14")));
    assert_eq!(a.pin("relay.8-8-8-8.sslip.io", 443), Some(ip("8.8.8.8")));
    assert_eq!(
        a.canonical(),
        "echo.93.184.215.14.sslip.io:443=93.184.215.14\nrelay.8-8-8-8.sslip.io:443=8.8.8.8\n"
    );
    // Local lane: TEST-NET-2 embedded IP allowed, refused in Pilot.
    assert!(EgressAllow::parse_for("echo.198.51.100.10.sslip.io:443", Lane::Local).is_ok());
    for (s, lane) in [
        ("echo.198.51.100.10.sslip.io:443", Lane::Pilot),
        ("sslip.io:443", Lane::Pilot),
        ("relay.sslip.io:443", Lane::Pilot),
        ("echo.203.0.113.7.sslip.io:443", Lane::Pilot), // TEST-NET-3
        ("echo.203.0.113.7.sslip.io:443", Lane::Local),
        ("echo.10.0.0.1.sslip.io:443", Lane::Local),
        ("echo.127.0.0.1.sslip.io:443", Lane::Pilot),
        ("meta.169-254-169-254.sslip.io:443", Lane::Local),
        ("echo.192.168.1.1.sslip.io:443", Lane::Pilot),
        ("echo.1-2-3.sslip.io:443", Lane::Pilot),
        ("echo.93.184.215.14.sslip.io:80", Lane::Pilot),
    ] {
        let e = EgressAllow::parse_for(s, lane).unwrap_err();
        assert_eq!(e.reason, REASON_EGRESS_ALLOWLIST_CONFIG, "{s:?} {lane:?}");
    }
}

#[test]
fn pilot_explicit_pin_parse() {
    let a = EgressAllow::parse_for("echo.example.org:443=93.184.215.14,v6.example.org:443=[2606:4700::1],plain.example.org:443", Lane::Pilot).unwrap();
    assert_eq!(a.pin("echo.example.org", 443), Some(ip("93.184.215.14")));
    assert_eq!(a.pin("v6.example.org", 443), Some(ip("2606:4700::1")));
    assert_eq!(a.pin("plain.example.org", 443), None);
    assert_eq!(
        a.canonical(),
        "echo.example.org:443=93.184.215.14\nplain.example.org:443\nv6.example.org:443=2606:4700::1\n"
    );
    // bare v6 and v4-mapped pins canonicalise
    let b = EgressAllow::parse_for("v6.example.org:443=2606:4700::1,m.example.org:443=::ffff:8.8.8.8", Lane::Pilot).unwrap();
    assert_eq!(b.pin("v6.example.org", 443), Some(ip("2606:4700::1")));
    assert_eq!(b.pin("m.example.org", 443), Some(ip("8.8.8.8")));
    // same sslip pin given explicitly is fine; duplicate identical entries dedup
    let c = EgressAllow::parse_for("echo.8.8.8.8.sslip.io:443=8.8.8.8,echo.8.8.8.8.sslip.io:443", Lane::Pilot).unwrap();
    assert_eq!(c.entries().len(), 1);
    for s in [
        "echo.example.org:443=", "echo.example.org:443=nope", "echo.example.org:443=10.0.0.1",
        "echo.example.org:443=169.254.169.254", "echo.example.org:443=198.51.100.10",
        "echo.example.org:443=fe80::1%eth0", "echo.example.org:443=1.2.3.4:443", "1.2.3.4:443=1.2.3.4",
        "*.example.org:443=8.8.8.8",
    ] {
        let e = EgressAllow::parse_for(s, Lane::Pilot).unwrap_err();
        assert_eq!(e.reason, REASON_EGRESS_ALLOWLIST_CONFIG, "{s:?}");
    }
    assert!(EgressAllow::parse_for("echo.example.org:443=198.51.100.10", Lane::Local).is_ok());
    for s in [
        "echo.8.8.8.8.sslip.io:443=1.1.1.1",
        "echo.example.org:443=8.8.8.8,echo.example.org:443=1.1.1.1",
        "echo.example.org:443=8.8.8.8,echo.example.org:443",
    ] {
        let e = EgressAllow::parse_for(s, Lane::Pilot).unwrap_err();
        assert_eq!(e.reason, REASON_EGRESS_PIN_MISMATCH, "{s:?}");
        assert!(e.log_line().starts_with("a4_refuse_egress_pin_mismatch:"));
    }
}

#[test]
fn pilot_allowlist_version_covers_pins() {
    let plain = EgressAllow::parse_for("echo.example.org:443", Lane::Pilot).unwrap();
    let p1 = EgressAllow::parse_for("echo.example.org:443=8.8.8.8", Lane::Pilot).unwrap();
    let p2 = EgressAllow::parse_for("echo.example.org:443=1.1.1.1", Lane::Pilot).unwrap();
    assert_eq!(plain.version(), sha256_hex(b"echo.example.org:443\n"), "unpinned hash unchanged");
    assert_eq!(p1.version(), sha256_hex(b"echo.example.org:443=8.8.8.8\n"));
    assert_ne!(plain.version(), p1.version());
    assert_ne!(p1.version(), p2.version());
    let s = EgressAllow::parse_for("echo.8.8.8.8.sslip.io:443", Lane::Pilot).unwrap();
    assert_eq!(s.version(), sha256_hex(b"echo.8.8.8.8.sslip.io:443=8.8.8.8\n"));
}

#[test]
fn pilot_pinned_resolve_skips_dns() {
    let a = EgressAllow::parse_for(
        "echo.93.184.215.14.sslip.io:443,relay.8-8-8-8.sslip.io:443,pinned.example.org:443=1.1.1.1",
        Lane::Pilot,
    )
    .unwrap();
    // Resolver that panics if called: pinned entries never reach it.
    assert_eq!(
        resolve_and_pin("ECHO.93.184.215.14.sslip.io.", 443, &a, &PanicResolver, Lane::Pilot).unwrap(),
        "93.184.215.14:443".parse().unwrap()
    );
    assert_eq!(
        resolve_and_pin("relay.8-8-8-8.sslip.io", 443, &a, &PanicResolver, Lane::Pilot).unwrap(),
        "8.8.8.8:443".parse().unwrap()
    );
    assert_eq!(
        resolve_and_pin("pinned.example.org", 443, &a, &PanicResolver, Lane::Pilot).unwrap(),
        "1.1.1.1:443".parse().unwrap()
    );
    // Allowlist still applies first (no DNS either way).
    assert_eq!(
        resolve_and_pin("echo.1.1.1.1.sslip.io", 443, &a, &PanicResolver, Lane::Pilot).unwrap_err().reason,
        REASON_EGRESS_NOT_ALLOWLISTED
    );
    // A hijacked / lying resolver answering something else changes nothing: still the embedded IP.
    let liar = FakeResolver::new(&[
        ("echo.93.184.215.14.sslip.io", &["169.254.169.254"]),
        ("relay.8-8-8-8.sslip.io", &["2.2.2.2"]),
        ("pinned.example.org", &["127.0.0.1"]),
    ]);
    assert_eq!(
        resolve_and_pin("echo.93.184.215.14.sslip.io", 443, &a, &liar, Lane::Pilot).unwrap(),
        "93.184.215.14:443".parse().unwrap()
    );
    assert_eq!(
        resolve_and_pin("relay.8-8-8-8.sslip.io", 443, &a, &liar, Lane::Pilot).unwrap(),
        "8.8.8.8:443".parse().unwrap()
    );
    assert_eq!(
        resolve_and_pin("pinned.example.org", 443, &a, &liar, Lane::Pilot).unwrap(),
        "1.1.1.1:443".parse().unwrap()
    );
    assert_eq!(liar.calls.load(Ordering::SeqCst), 0);
    // Pin parsed in Local lane (TEST-NET-2) is re-checked against the OPEN lane.
    let l = EgressAllow::parse_for("echo.198.51.100.10.sslip.io:443", Lane::Local).unwrap();
    assert_eq!(
        resolve_and_pin("echo.198.51.100.10.sslip.io", 443, &l, &PanicResolver, Lane::Local).unwrap(),
        "198.51.100.10:443".parse().unwrap()
    );
    assert_eq!(
        resolve_and_pin("echo.198.51.100.10.sslip.io", 443, &l, &PanicResolver, Lane::Pilot).unwrap_err().reason,
        REASON_EGRESS_RESOLVED_NON_PUBLIC
    );
}
