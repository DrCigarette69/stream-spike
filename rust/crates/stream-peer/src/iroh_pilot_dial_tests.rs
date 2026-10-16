//! A4.4 part 2 builder tests (feature `iroh_pilot`). Endpoints bind on loopback
//! with the on-box relay URL 10.73.0.254 (never reachable here; no DNS, no internet).
//! Run inside a no-default-route netns.
use super::*;
use stream_proto::guard::Lane;

fn allow(url: &str, lane: Lane) -> RelayAllow {
    RelayAllow::parse(url, lane).unwrap()
}

#[test]
fn relay_url_is_the_single_allowed_one() {
    let a = allow("https://relay.pilot.example/", Lane::Pilot);
    let u = relay_url_of(&a).unwrap();
    // iroh writes DNS names as FQDN (trailing dot); the guard normalises it away.
    assert!(u.as_str().starts_with("https://relay.pilot.example"), "{u}");
    guard::check_relay_url_with(Some(u.as_str()), Some(&a)).unwrap();
    assert!(guard::check_relay_url_with(Some("https://relay.pilot.example:8443/"), Some(&a)).is_err());
    let map = pilot_relay_map(u.clone());
    let urls: Vec<RelayUrl> = map.urls();
    assert_eq!(urls, vec![u.clone()], "exactly one relay in the map");
    assert!(map.get(&u).unwrap().quic.is_none(), "QUIC address discovery off (quic: None)");
}

#[tokio::test]
async fn pilot_endpoint_relay_only_no_discovery_private_bind() {
    // On-box lane relay URL: private, never reachable from the test netns (no DNS, no internet).
    let a = allow("http://10.73.0.254:3340/", Lane::Local);
    let ep = bind_pilot(SecretKey::from_bytes(&[7u8; 32]), &a, "127.0.0.1:0").await.unwrap();
    assert!(ep.discovery().is_empty(), "discovery must be off");
    assert!(ep.bound_sockets().iter().all(|s| guard::is_private_addr(s.ip())));
    // Any relay the endpoint reports can only be ours.
    for u in ep.addr().relay_urls() {
        assert_eq!(u.as_str(), "http://10.73.0.254:3340/");
    }
    ep.close().await;
    // A public bind address is refused before binding.
    assert!(bind_pilot(SecretKey::from_bytes(&[7u8; 32]), &a, "8.8.8.8:0").await.is_err());
}

#[test]
fn conn_type_mapping() {
    let r: RelayUrl = "https://relay.pilot.example/".parse().unwrap();
    let a: std::net::SocketAddr = "203.0.113.5:4433".parse().unwrap();
    assert_eq!(path_kind(&ConnectionType::Relay(r.clone())), PathKind::Relay);
    assert_eq!(path_kind(&ConnectionType::None), PathKind::None);
    assert_eq!(path_kind(&ConnectionType::Direct(a)), PathKind::Direct(a.to_string()));
    assert_eq!(path_kind(&ConnectionType::Mixed(a, r)), PathKind::Direct(a.to_string()));
}

#[tokio::test]
async fn pilot_udp_source_reads_iroh_metrics() {
    let a = allow("http://10.73.0.254:3340/", Lane::Local);
    let ep = bind_pilot(SecretKey::from_bytes(&[9u8; 32]), &a, "127.0.0.1:0").await.unwrap();
    let src = udp_source(&ep);
    #[cfg(not(feature = "a4_local"))]
    {
        // iroh/metrics is on under iroh_pilot: a freshly bound relay-only endpoint reads 0, not unavailable.
        assert_eq!(src.read(), Some(Default::default()));
        assert!(require_counters(src.as_ref(), Lane::Pilot).is_ok(), "pilot lane has counters");
        assert_eq!(counters_line(src.as_ref()), "udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0 recv_v6=0");
    }
    let _ = src;
    ep.close().await;
}

#[test]
fn inbound_udp_disco_counts_and_trips() {
    use crate::relay_path::{counters_line, udp_violation, UdpCounterSource, UdpCounts};
    struct Fixed(UdpCounts);
    impl UdpCounterSource for Fixed {
        fn read(&self) -> Option<UdpCounts> {
            Some(self.0)
        }
    }
    let zero = Fixed(fold_magicsock(0, 0, 0, 0, 0));
    assert_eq!(counters_line(&zero), "udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0 recv_v6=0");
    assert!(udp_violation(&zero).is_none());
    // Only an inbound disco packet over UDP (no QUIC data, nothing sent): still trips.
    let disco = Fixed(fold_magicsock(0, 0, 0, 0, 1));
    assert_eq!(counters_line(&disco), "udp_counters total=1 send_v4=0 send_v6=0 recv_v4=1 recv_v6=0");
    assert!(udp_violation(&disco).unwrap().starts_with("udp_datagrams:1 "), "{:?}", udp_violation(&disco));
    let both = fold_magicsock(10, 20, 30, 40, 2);
    assert_eq!((both.send_v4, both.send_v6, both.recv_v4, both.recv_v6, both.total()), (10, 20, 32, 40, 102));
    assert_eq!(fold_magicsock(0, 0, u64::MAX, 0, 5).recv_v4, u64::MAX, "saturating");
}

#[test]
fn session_end_line_carries_reason_side_close_and_counters() {
    let udp = "udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0 recv_v6=0";
    let close = close_suffix(Some((Some(7), "egress off".into())));
    assert_eq!(close.as_deref(), Some("close_code=7 close_reason=egress_off"));
    assert_eq!(
        session_end_line("remote_closed", "remote", close.as_deref(), udp),
        format!("session_end end_reason=remote_closed closed_by=remote close_code=7 close_reason=egress_off {udp}")
    );
    assert_eq!(close_suffix(Some((Some(0), String::new()))).as_deref(), Some("close_code=0 close_reason=-"));
    assert_eq!(close_suffix(Some((None, "timed out".into()))).as_deref(), Some("close_error=timed_out"));
    assert_eq!(close_suffix(None), None);
    assert_eq!(session_end_line("kill", "local", None, udp), format!("session_end end_reason=kill closed_by=local {udp}"));
}

#[test]
fn user_stops_do_not_redial_everything_else_backs_off() {
    for r in ["kill", "force_disconnect", "cleared_ack", "consent_withdrawn"] {
        assert!(user_stop_reason(r), "{r}");
    }
    for r in ["remote_closed", "tunnel_msg:ERR", "read_error", "handshake", "unknown", "path_watch_ended"] {
        assert!(!user_stop_reason(r), "{r}");
    }
}
