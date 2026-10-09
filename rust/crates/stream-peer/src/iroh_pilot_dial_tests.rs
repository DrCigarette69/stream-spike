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
    let map = RelayMap::from(u.clone());
    let urls: Vec<RelayUrl> = map.urls();
    assert_eq!(urls, vec![u], "exactly one relay in the map");
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
