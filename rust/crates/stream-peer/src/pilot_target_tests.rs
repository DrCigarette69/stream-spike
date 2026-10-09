//! A4.4 part 2 tests that need no iroh: startup checks, relay refusals,
//! pilot dial target + ticket relay checks, mock path watcher -> P8.
use crate::kill::{ActiveTransport, CloseFut, TransportSlot};
use crate::pilot_egress_tests::{pilot_state, plane, Fake};
use crate::pilot_target::*;
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use stream_proto::guard::{Lane, RelayAllow};

const GW: &str = "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1";

fn ours() -> RelayAllow {
    RelayAllow::parse("https://relay.pilot.example/", Lane::Pilot).unwrap()
}

#[test]
fn relay_allow_refusals() {
    for bad in [
        "https://use1-1.relay.iroh.network/",
        "https://relay.n0.computer/",
        "http://10.73.0.254:3340/",
        "https://10.0.0.5/",
        "https://192.168.1.2/",
        "https://relay.pilot.example/,https://other.example/",
        "ftp://relay.pilot.example/",
    ] {
        let e = RelayAllow::parse(bad, Lane::Pilot).unwrap_err();
        assert_eq!(e.reason(), "relay_config", "{bad}");
    }
    assert!(RelayAllow::parse("http://10.73.0.254:3340/", Lane::Local).is_ok(), "a4_local lane only");
    assert!(RelayAllow::parse("http://10.73.0.253:3340/", Lane::Local).is_err());
    assert!(startup_checks("iroh_local", Lane::Pilot).unwrap_err().starts_with("a4_refuse_transport_not_pilot:iroh_local"));
    assert!(startup_checks("fake_relay", Lane::Pilot).unwrap_err().contains("transport_not_pilot"));
    if std::env::var("SPIKE_RELAY_ALLOW_URL").is_err() {
        assert_eq!(startup_checks("iroh_pilot", Lane::Pilot).unwrap_err(), "a4_refuse_relay_config:<unset>");
    }
}

#[test]
fn pilot_target_checks() {
    let a = ours();
    let t = check_pilot_target(&json!({"gateway_endpoint_id": GW, "relay_url": "https://RELAY.pilot.example:443/"}), GW, &a, None).unwrap();
    assert_eq!(t.relay_url, "https://relay.pilot.example:443/");
    assert!(t.direct_addrs.is_empty());
    let r = |p: serde_json::Value| check_pilot_target(&p, GW, &a, None).unwrap_err();
    assert_eq!(r(json!({"gateway_endpoint_id": "other", "relay_url": a.url()})), "gateway_mismatch");
    assert_eq!(r(json!({"gateway_endpoint_id": GW})), "relay_required");
    assert_eq!(r(json!({"gateway_endpoint_id": GW, "direct_addrs": []})), "relay_required");
    for other in ["https://use1-1.relay.iroh.network/", "https://relay.pilot.example:8443/", "http://relay.pilot.example/", "https://evil.example/"] {
        assert_eq!(r(json!({"gateway_endpoint_id": GW, "relay_url": other})), "relay_refused", "{other}");
    }
    assert_eq!(r(json!({"gateway_endpoint_id": GW, "relay_url": a.url(), "direct_addrs": ["8.8.8.8:4433"]})), "public_addr");
    let cidrs = stream_proto::guard::parse_allowlist("10.73.0.0/24").unwrap();
    let ok = check_pilot_target(&json!({"gateway_endpoint_id": GW, "relay_url": a.url(), "direct_addrs": ["10.73.0.2:9102"]}), GW, &a, cidrs.as_deref()).unwrap();
    assert_eq!(ok.direct_addrs.len(), 1, "checked, but RelayOnly never dials them");
}

#[test]
fn ticket_relay_checks() {
    let a = ours();
    assert!(check_ticket_relay(r#"{"payload":{"relay_url":"https://relay.pilot.example/"}}"#, Some(&a)).is_ok());
    assert!(check_ticket_relay(r#"{"payload":{"peer_endpoint_id":"x"}}"#, Some(&a)).is_ok());
    assert_eq!(check_ticket_relay(r#"{"relay_url":"https://relay.iroh.network/"}"#, Some(&a)).unwrap_err(), "relay_refused");
    assert_eq!(check_ticket_relay(r#"{"payload":{"relay_url":"https://relay.pilot.example/"}}"#, None).unwrap_err(), "relay_refused");
    assert_eq!(check_ticket_relay(r#"{"payload":{"direct_addrs":[]}}"#, Some(&a)).unwrap_err(), "relay_required");
}

struct Conn(Arc<AtomicBool>);
impl ActiveTransport for Conn {
    fn close<'a>(&'a self, _r: &'a str) -> CloseFut<'a> {
        Box::pin(async move { self.0.store(true, Ordering::SeqCst) })
    }
    fn label(&self) -> &str {
        "mock_pilot"
    }
}

#[tokio::test]
async fn mock_watcher_direct_path_closes_all_and_p8() {
    let p = plane(Fake::with(&[]), 5000, 1 << 20, Lane::Pilot);
    let st = pilot_state(&p);
    let closed = Arc::new(AtomicBool::new(false));
    let slot = TransportSlot::default();
    slot.set(Box::new(Conn(closed.clone()))).await;
    let _rx = p.lock().unwrap().register("s1", "site1.pilot.example:443".into());
    let mut seq = vec![PathKind::None, PathKind::Relay, PathKind::Relay, PathKind::Direct("203.0.113.5:4433".into())].into_iter();
    let t0 = tokio::time::Instant::now();
    let v = watch_path(&st, &slot, move || seq.next().unwrap_or(PathKind::Relay), std::future::pending()).await;
    assert_eq!(v.as_deref(), Some("203.0.113.5:4433"));
    assert!(t0.elapsed() < std::time::Duration::from_secs(2), "kill <= 2 s");
    assert!(closed.load(Ordering::SeqCst), "transport closed");
    assert_eq!(p.lock().unwrap().open_streams(), 0);
    assert_eq!(p.lock().unwrap().take_pending_close()[0].1, "relay_path_required");
    assert_eq!(st.read().await.p8_reason.as_deref(), Some("relay_path_required"));
    assert!(crate::a4_copy::p8_reason_line("relay_path_required").unwrap().contains("expected secure route"));
    // Relay-only all along: watcher just stops.
    let st2 = pilot_state(&p);
    let v = watch_path(&st2, &slot, || PathKind::Relay, tokio::time::sleep(std::time::Duration::from_millis(300))).await;
    assert_eq!(v, None);
    assert_eq!(st2.read().await.p8_reason, None);
}

#[test]
fn never_skips_relay_cert_verification() {
    let needle = concat!("insecure_skip", "_relay_cert_verify");
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut seen = 0;
    for e in std::fs::read_dir(&dir).unwrap() {
        let p = e.unwrap().path();
        if p.extension().and_then(|x| x.to_str()) == Some("rs") {
            seen += 1;
            assert!(!std::fs::read_to_string(&p).unwrap().contains(needle), "{} uses {needle}", p.display());
        }
    }
    assert!(seen > 20);
}
