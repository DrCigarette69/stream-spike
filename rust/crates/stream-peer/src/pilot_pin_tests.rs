//! A4.1 amendment on the Peer: pinned (`host:443=IP`) and `*.sslip.io` allowlist entries
//! never touch DNS (the resolver panics if called) and connect to exactly the pinned IP;
//! bad / conflicting pins refuse startup (config load) and never reach P8.
use crate::config::Config;
use crate::egress_state::build_plane_from;
use crate::pilot_egress::*;
use std::net::IpAddr;
use std::sync::{Arc, Mutex};
use stream_proto::guard::{EgressAllow, EgressSwitch, Lane, Resolver};

struct NoDns;
impl Resolver for NoDns {
    fn resolve(&self, host: &str, _port: u16) -> Result<Vec<IpAddr>, String> {
        panic!("DNS lookup for pinned entry {host}");
    }
}

fn pinned_plane(list: &str, lane: Lane) -> SharedPlane {
    let allow = EgressAllow::parse_for(list, lane).unwrap();
    let mut p = EgressPlane::new(allow, EgressSwitch::new(true, 5000), 5000, 1 << 20);
    p.resolver = Arc::new(NoDns);
    p.lane = lane;
    let v = p.version.clone();
    p.update_state(true, &v);
    Arc::new(Mutex::new(p))
}

const PINNED: &str = "pinned.pilot.example:443=93.184.216.34,relay.93.184.216.35.sslip.io:443,8-8-4-4.sslip.io:443";

#[tokio::test]
async fn pinned_and_sslip_entries_never_hit_dns() {
    let p = pinned_plane(PINNED, Lane::Pilot);
    for (sid, host, want) in [
        ("s1", "pinned.pilot.example", "93.184.216.34:443"),
        ("s2", "Relay.93.184.216.35.SSLIP.io.", "93.184.216.35:443"),
    ] {
        let a = check_and_pin(&p, sid, host, 443).await.unwrap();
        assert_eq!(a.to_string(), want, "{host}");
    }
    // stream cap 2 reached; release one, the dashed sslip form pins too
    p.lock().unwrap().unreserve("s1");
    assert_eq!(check_and_pin(&p, "s3", "8-8-4-4.sslip.io", 443).await.unwrap().to_string(), "8.8.4.4:443");
    // still exact host:443 only (free a slot first: the stream cap is checked before the list)
    p.lock().unwrap().unreserve("s2");
    assert_eq!(check_and_pin(&p, "s4", "pinned.pilot.example", 80).await.unwrap_err().reason, "egress_not_allowlisted");
}

#[test]
fn version_covers_pins() {
    let v = |s: &str| EgressAllow::parse_for(s, Lane::Pilot).unwrap().version();
    assert_ne!(v("a.pilot.example:443=93.184.216.34"), v("a.pilot.example:443=93.184.216.35"));
    assert_ne!(v("a.pilot.example:443=93.184.216.34"), v("a.pilot.example:443"));
    let mut cfg = Config::from_env();
    cfg.transport = "iroh_pilot".into();
    let p = build_plane_from(&cfg, Some(PINNED), None).unwrap().unwrap();
    assert_eq!(p.lock().unwrap().version, v(PINNED), "Peer reports the pin-covering version");
}

#[test]
fn bad_or_conflicting_pins_refuse_startup() {
    let mut cfg = Config::from_env();
    cfg.transport = "iroh_pilot".into();
    for bad in [
        "a.pilot.example:443=10.0.0.5",
        "a.pilot.example:443=169.254.169.254",
        "a.pilot.example:443=127.0.0.1",
        "10-0-0-5.sslip.io:443",
        "relay.192.168.1.2.sslip.io:443",
        "nothing.sslip.io:443",
        "a.pilot.example:443=not-an-ip",
    ] {
        let e = build_plane_from(&cfg, Some(bad), None).unwrap_err();
        assert!(e.starts_with("a4_refuse_egress_allowlist_config:"), "{bad}: {e}");
    }
    // TEST-NET-2 pin: on-box lane only (pilot builds refuse it at load).
    let e = EgressAllow::parse_for("a.pilot.example:443=198.51.100.10", Lane::Pilot).unwrap_err();
    assert_eq!(e.reason(), "egress_allowlist_config");
    assert!(EgressAllow::parse_for("a.pilot.example:443=198.51.100.10", Lane::Local).is_ok());
    for bad in [
        "relay.93.184.216.34.sslip.io:443=93.184.216.35",
        "a.pilot.example:443=93.184.216.34,a.pilot.example:443=93.184.216.35",
    ] {
        let e = build_plane_from(&cfg, Some(bad), None).unwrap_err();
        assert!(e.starts_with("a4_refuse_egress_pin_mismatch:"), "{bad}: {e}");
    }
}

/// a44 netns: pinned entry -> connect to exactly the pinned TEST-NET-2 stand-in, no DNS.
#[tokio::test]
#[ignore]
async fn a44_netns_pinned() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let site: std::net::SocketAddr = std::env::var("SPIKE_A44_SITE").expect("SPIKE_A44_SITE").parse().unwrap();
    let l = tokio::net::TcpListener::bind(site).await.expect("bind stand-in site");
    let acc = tokio::spawn(async move {
        let (mut s, peer) = l.accept().await.unwrap();
        let mut b = [0u8; 6];
        s.read_exact(&mut b).await.unwrap();
        s.write_all(&b).await.unwrap();
        (peer, s.local_addr().unwrap())
    });
    let p = pinned_plane(&format!("pinned.pilot.example:443={}", site.ip()), Lane::Local);
    let addr = check_and_pin(&p, "s1", "pinned.pilot.example", 443).await.unwrap();
    assert_eq!(addr, site);
    let (up, rx) = connect_pinned(&p, "s1", "pinned.pilot.example", 443, addr).await.unwrap();
    let (down, mut client) = tokio::io::duplex(4096);
    tokio::spawn({ let p = p.clone(); async move { pump(&p, "s1", up, down, rx).await } });
    client.write_all(b"pinned").await.unwrap();
    let mut b = [0u8; 6];
    client.read_exact(&mut b).await.unwrap();
    let (_from, at) = acc.await.unwrap();
    assert_eq!(at, site, "connected to exactly the pinned IP");
    println!("A44_PINNED_OK pinned.pilot.example:443 -> {at} (no DNS)");
}
