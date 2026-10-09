//! A4.4 part 1 tests. Injected resolvers only; real sockets only on loopback
//! (unit) or on TEST-NET-2 inside a no-default-route netns (ignored test, run by
//! `scripts/a44_peer_egress_smoke.py`). Nothing here reaches the internet.
use crate::config::Config;
use crate::egress_state::{apply_state, build_plane_from, watchdog_step};
use crate::pilot_egress::*;
use crate::state::{PeerState, SharedState};
use serde_json::json;
use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use stream_proto::guard::{EgressAllow, EgressSwitch, Lane, Resolver};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::time::{Duration, Instant};

pub(crate) const ALLOW: &str = "site1.pilot.example:443,site2.pilot.example:443";

#[derive(Default)]
pub(crate) struct Fake {
    answers: Mutex<HashMap<String, Vec<Vec<IpAddr>>>>,
    calls: AtomicUsize,
}
impl Fake {
    pub(crate) fn with(pairs: &[(&str, &[&[&str]])]) -> Arc<Self> {
        let f = Fake::default();
        for (h, seq) in pairs {
            let v = seq.iter().map(|a| a.iter().map(|s| s.parse().unwrap()).collect()).collect();
            f.answers.lock().unwrap().insert(h.to_string(), v);
        }
        Arc::new(f)
    }
}
impl Resolver for Fake {
    fn resolve(&self, host: &str, _port: u16) -> Result<Vec<IpAddr>, String> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let mut m = self.answers.lock().unwrap();
        let q = m.get_mut(host).ok_or("nxdomain")?;
        Ok(if q.len() > 1 { q.remove(0) } else { q[0].clone() })
    }
}

pub(crate) fn plane(r: Arc<Fake>, max_age: u64, cap: u64, lane: Lane) -> SharedPlane {
    let allow = EgressAllow::parse(ALLOW).unwrap();
    let mut p = EgressPlane::new(allow, EgressSwitch::new(true, max_age), max_age, cap);
    p.resolver = r;
    p.lane = lane;
    let v = p.version.clone();
    p.update_state(true, &v);
    Arc::new(Mutex::new(p))
}

#[tokio::test]
async fn allowlisted_passes_others_refused_before_dns() {
    let r = Fake::with(&[("site1.pilot.example", &[&["93.184.216.34"]])]);
    let p = plane(r.clone(), 5000, 1 << 20, Lane::Pilot);
    let a = check_and_pin(&p, "s1", "Site1.Pilot.Example.", 443).await.unwrap();
    assert_eq!(a.to_string(), "93.184.216.34:443");
    for (h, port, why) in [
        ("site1.pilot.example", 80, "egress_not_allowlisted"),
        ("other.pilot.example", 443, "egress_not_allowlisted"),
        ("93.184.216.34", 443, "egress_not_allowlisted"),
        ("site1.pilot.example", 8443, "port_8443_blocked"),
        ("169.254.169.254", 443, "metadata_blocked"),
    ] {
        assert_eq!(check_and_pin(&p, "s1", h, port).await.unwrap_err().reason, why, "{h}:{port}");
    }
    assert_eq!(r.calls.load(Ordering::SeqCst), 1, "unlisted names never reach DNS");
}

#[tokio::test]
async fn non_public_answers_and_rebinding_refused() {
    for bad in [&["10.1.2.3"][..], &["169.254.169.254"], &["127.0.0.1"], &["93.184.216.34", "10.0.0.5"], &["100.64.0.1"]] {
        let r = Fake::with(&[("site1.pilot.example", &[bad])]);
        let p = plane(r, 5000, 1 << 20, Lane::Pilot);
        let e = check_and_pin(&p, "s1", "site1.pilot.example", 443).await.unwrap_err();
        assert_eq!(e.reason, "egress_resolved_non_public", "{bad:?}");
        assert!(e.line.starts_with("a4_refuse_egress_resolved_non_public:"), "{}", e.line);
    }
    let r = Fake::with(&[("site2.pilot.example", &[&["93.184.216.34"], &["192.168.1.1"]])]);
    let p = plane(r, 5000, 1 << 20, Lane::Pilot);
    assert!(check_and_pin(&p, "s1", "site2.pilot.example", 443).await.is_ok());
    assert_eq!(check_and_pin(&p, "s1", "site2.pilot.example", 443).await.unwrap_err().reason, "egress_resolved_non_public");
    let p = plane(Fake::with(&[]), 5000, 1 << 20, Lane::Pilot);
    assert_eq!(check_and_pin(&p, "s1", "site1.pilot.example", 443).await.unwrap_err().reason, "egress_resolve_failed");
    // TEST-NET-2 is public only on the a4_local lane.
    let r = Fake::with(&[("site1.pilot.example", &[&["198.51.100.10"]])]);
    assert!(check_and_pin(&plane(r.clone(), 5000, 1, Lane::Pilot), "s1", "site1.pilot.example", 443).await.is_err());
    assert!(check_and_pin(&plane(r, 5000, 1, Lane::Local), "s1", "site1.pilot.example", 443).await.is_ok());
}

#[test]
fn switch_version_budget_and_p8_keys() {
    let allow = EgressAllow::parse(ALLOW).unwrap();
    let mut p = EgressPlane::new(allow.clone(), EgressSwitch::new(true, 5000), 5000, 100);
    let e = p.status_at(0).unwrap_err();
    assert_eq!((e.reason(), p8_key(&e)), ("egress_off", "egress_off"), "no state = off");
    let v = p.version.clone();
    p.switch.update(true, &v, 1000);
    assert!(p.status_at(1000).is_ok());
    let e = p.status_at(7000).unwrap_err();
    assert_eq!((e.reason(), p8_key(&e)), ("egress_off", "egress_state_stale"));
    p.switch.update(false, &v, 7000);
    assert_eq!(p8_key(&p.status_at(7000).unwrap_err()), "egress_off");
    p.switch.update(true, "deadbeef", 8000);
    assert_eq!(p8_key(&p.status_at(8000).unwrap_err()), "egress_allowlist_mismatch");
    p.switch.update(true, &v, 9000);
    assert!(p.add_bytes(100).is_ok());
    assert_eq!(p8_key(&p.status_at(9000).unwrap_err()), "egress_budget_exceeded");
    assert!(p.add_bytes(1).is_err());
    let mut off = EgressPlane::new(allow, EgressSwitch::new(false, 5000), 5000, 100);
    off.switch.update(true, &v, 0);
    assert_eq!(off.status_at(0).unwrap_err().detail, "local_off");
}

#[test]
fn allowlist_version_stable() {
    let a = EgressAllow::parse("b.example.com:443,a.example.com:443").unwrap();
    let b = EgressAllow::parse(" A.Example.COM.:443 , b.example.com:443,a.example.com:443").unwrap();
    let want = "66700a4aa3428355772d718ff5a4a86f03decf5facebfa3725a7d08bac1d6f55";
    assert_eq!(a.version(), want);
    assert_eq!(b.version(), want);
    let mut cfg = Config::from_env();
    cfg.transport = "iroh_pilot".into();
    for bad in ["", "*.example.com:443", "1.2.3.4:443", "example.com", "example.com:80", "10.0.0.0/8:443"] {
        let e = build_plane_from(&cfg, Some(bad), None).unwrap_err();
        assert!(e.starts_with("a4_refuse_egress_allowlist_config:"), "{bad}: {e}");
    }
    assert!(build_plane_from(&cfg, None, None).is_err());
    let p = build_plane_from(&cfg, Some("a.example.com:443,b.example.com:443"), None).unwrap().unwrap();
    assert_eq!(p.lock().unwrap().version, want);
    cfg.transport = "fake_relay".into();
    assert!(build_plane_from(&cfg, Some("garbage"), None).unwrap().is_none(), "Alpha-3 ignores it");
}

pub(crate) async fn echo_server() -> std::net::SocketAddr {
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let a = l.local_addr().unwrap();
    tokio::spawn(async move {
        while let Ok((mut s, _)) = l.accept().await {
            tokio::spawn(async move {
                let (mut r, mut w) = s.split();
                let _ = tokio::io::copy(&mut r, &mut w).await;
            });
        }
    });
    a
}

#[tokio::test]
async fn cap_hit_closes_stream() {
    let p = plane(Fake::with(&[]), 5000, 1000, Lane::Pilot);
    let addr = echo_server().await;
    let (up, rx) = connect_pinned(&p, "s1", "site1.pilot.example", 443, addr).await.unwrap();
    let (down, mut client) = tokio::io::duplex(4096);
    let t = tokio::spawn({ let p = p.clone(); async move { pump(&p, "s1", up, down, rx).await } });
    client.write_all(&[7u8; 600]).await.unwrap();
    let mut buf = [0u8; 600];
    let _ = tokio::time::timeout(Duration::from_secs(2), client.read_exact(&mut buf)).await;
    let end = tokio::time::timeout(Duration::from_secs(2), t).await.unwrap().unwrap();
    assert_eq!(end, Some("egress_budget_exceeded"));
    assert_eq!(p.lock().unwrap().open_streams(), 0);
    assert_eq!(p.lock().unwrap().take_pending_close()[0].1, "egress_budget_exceeded");
}

pub(crate) fn pilot_state(p: &SharedPlane) -> SharedState {
    let mut cfg = Config::from_env();
    cfg.transport = "iroh_pilot".into();
    cfg.public_egress = true;
    cfg.isp_ack_version = "v1".into();
    cfg.p2_consent_preset = true;
    cfg.p9_ack_preset = true;
    let mut st = PeerState::new(cfg);
    st.egress = Some(p.clone());
    Arc::new(tokio::sync::RwLock::new(st))
}

/// Flag off or stale -> watchdog closes the live stream and shows P8.
#[tokio::test]
async fn kill_switch_closes_within_bound_and_p8() {
    for (stale, key) in [(false, "egress_off"), (true, "egress_state_stale")] {
        let p = plane(Fake::with(&[]), 300, 1 << 20, Lane::Pilot);
        let st = pilot_state(&p);
        let addr = echo_server().await;
        let (up, rx) = connect_pinned(&p, "s1", "site1.pilot.example", 443, addr).await.unwrap();
        let (down, _client) = tokio::io::duplex(4096);
        let t = tokio::spawn({ let p = p.clone(); async move { pump(&p, "s1", up, down, rx).await } });
        let flip = Instant::now();
        if !stale {
            let v = p.lock().unwrap().version.clone();
            assert!(apply_state(&p, &json!({"public_egress": false, "allowlist_version": v, "ts": 1})));
        }
        while !t.is_finished() {
            watchdog_step(&st, &p).await;
            tokio::time::sleep(Duration::from_millis(100)).await;
            assert!(flip.elapsed() < Duration::from_secs(5), "not closed within 5 s");
        }
        assert_eq!(t.await.unwrap(), Some("egress_off"));
        assert_eq!(st.read().await.p8_reason.as_deref(), Some(key));
        let v = p.lock().unwrap().version.clone();
        apply_state(&p, &json!({"public_egress": true, "allowlist_version": v}));
        watchdog_step(&st, &p).await;
        assert_eq!(st.read().await.p8_reason, None, "back on clears P8");
    }
}

#[tokio::test]
async fn per_open_refusal_rejects_without_p8() {
    let p = plane(Fake::with(&[]), 5000, 1 << 20, Lane::Pilot);
    let st = pilot_state(&p);
    let http = reqwest::Client::new();
    let mut out: Vec<u8> = Vec::new();
    let msg = json!({"type":"AUTH_TICKET","alpn":stream_proto::ALPN,"dest_host":"evil.example.com","dest_port":443,"stream_id":"s9","ticket_json":"{}"});
    crate::frames::handle_auth_ticket(&st, &http, &mut out, &msg).await;
    let line = String::from_utf8(out).unwrap();
    assert!(line.contains("\"AUTH_REJECT\"") && line.contains("egress_not_allowlisted"), "{line}");
    watchdog_step(&st, &p).await;
    assert_eq!(st.read().await.p8_reason, None);
    assert!(apply_state(&p, &json!({"nope": 1})) == false, "malformed state ignored");
}

/// Netns stand-in (TEST-NET-2, no default route). Run by a44_peer_egress_smoke.py.
#[tokio::test]
#[ignore]
async fn a44_netns_stand_in() {
    let site: std::net::SocketAddr = std::env::var("SPIKE_A44_SITE").expect("SPIKE_A44_SITE").parse().unwrap();
    let l = tokio::net::TcpListener::bind(site).await.expect("bind stand-in site");
    tokio::spawn(async move {
        while let Ok((mut s, _)) = l.accept().await {
            tokio::spawn(async move { let (mut r, mut w) = s.split(); let _ = tokio::io::copy(&mut r, &mut w).await; });
        }
    });
    let ip = site.ip().to_string();
    let r = Fake::with(&[("site1.pilot.example", &[&[ip.as_str()]]), ("site2.pilot.example", &[&["169.254.169.254"]])]);
    let p = plane(r, 1000, 1 << 20, Lane::Local);
    let budget = std::path::PathBuf::from(std::env::var("SPIKE_A44_BUDGET").expect("SPIKE_A44_BUDGET"));
    p.lock().unwrap().attach_store(budget.clone());
    let addr = check_and_pin(&p, "s1", "site1.pilot.example", 443).await.unwrap();
    assert_eq!(addr, site);
    assert_eq!(check_and_pin(&p, "s1", "site2.pilot.example", 443).await.unwrap_err().reason, "egress_resolved_non_public");
    assert_eq!(check_and_pin(&p, "s1", "site1.pilot.example", 80).await.unwrap_err().reason, "egress_not_allowlisted");
    let (up, rx) = connect_pinned(&p, "s1", "site1.pilot.example", 443, addr).await.unwrap();
    let (down, mut client) = tokio::io::duplex(4096);
    let t = tokio::spawn({ let p = p.clone(); async move { pump(&p, "s1", up, down, rx).await } });
    client.write_all(b"hello pilot").await.unwrap();
    let mut buf = [0u8; 11];
    client.read_exact(&mut buf).await.unwrap();
    assert_eq!(&buf, b"hello pilot");
    assert_eq!(p.lock().unwrap().used, 22, "both directions counted");
    // Stream cap (default 2): a second live stream is fine, a third is refused.
    let a2 = check_and_pin(&p, "s2", "site1.pilot.example", 443).await.unwrap();
    let (up2, _rx2) = connect_pinned(&p, "s2", "site1.pilot.example", 443, a2).await.unwrap();
    let e = check_and_pin(&p, "s3", "site1.pilot.example", 443).await.unwrap_err();
    assert_eq!((e.reason.as_str(), e.line.as_str()), ("egress_stream_limit", "a4_refuse_egress_stream_limit:2"));
    drop(up2);
    let st = pilot_state(&p);
    let stop = Instant::now(); // Control "stops": no more updates
    while !t.is_finished() {
        watchdog_step(&st, &p).await;
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(stop.elapsed() <= Duration::from_secs(5));
    assert_eq!(st.read().await.p8_reason.as_deref(), Some("egress_state_stale"));
    let saved: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&budget).unwrap()).unwrap();
    assert_eq!(saved["bytes"], 22, "closed stream flushed the counter");
    eprintln!("A44_NETNS_STAND_IN_OK closed_after_ms={}", stop.elapsed().as_millis());
}
