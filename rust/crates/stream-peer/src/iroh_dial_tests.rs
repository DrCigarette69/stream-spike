//! iroh_local dial tests (feature `iroh_local`). Real iroh endpoints on
//! 127.0.0.1 with relay/discovery off; run inside a loopback-only netns.
use super::*;
use crate::config::Config;
use crate::state::PeerState;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::RwLock;

const DEV_GW_ID: &str = "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1";

fn peer_state() -> SharedState {
    let mut cfg = Config::from_env();
    cfg.transport = "iroh_local".into();
    cfg.isp_ack_version = "v1".into();
    cfg.p2_consent_preset = true;
    cfg.control_url = "http://127.0.0.1:9".into(); // heartbeats fail fast
    Arc::new(RwLock::new(PeerState::new(cfg)))
}

struct MockGw {
    ep: Endpoint,
    accepts: Arc<AtomicUsize>,
    closed_rx: tokio::sync::mpsc::Receiver<Instant>,
}

/// Mock Gateway: accepts ALPN stream/tunnel/1, reads HELLO on the bi-stream;
/// if `expect_peer` matches the authenticated remote id -> HELLO_OK + CLOSE s1,
/// else AUTH_REJECT endpoint_mismatch. Reports when each connection closes.
async fn mock_gateway(expect_peer: EndpointId) -> MockGw {
    let ep = builder(SecretKey::from_bytes(&[7u8; 32]), "127.0.0.1:0".parse().unwrap(), "[::1]:0".parse().unwrap())
        .alpns(vec![ALPN.as_bytes().to_vec()])
        .bind()
        .await
        .unwrap();
    let accepts = Arc::new(AtomicUsize::new(0));
    let (tx, closed_rx) = tokio::sync::mpsc::channel(8);
    let (ep2, acc) = (ep.clone(), accepts.clone());
    tokio::spawn(async move {
        while let Some(inc) = ep2.accept().await {
            acc.fetch_add(1, Ordering::SeqCst);
            let tx = tx.clone();
            tokio::spawn(async move {
                let conn = inc.await.unwrap();
                let (mut send, recv) = conn.accept_bi().await.unwrap();
                let mut r = BufReader::new(recv);
                let mut hello = String::new();
                r.read_line(&mut hello).await.unwrap();
                assert!(hello.contains("\"HELLO\""), "{hello}");
                let out = if conn.remote_id() == expect_peer {
                    format!("{}\n{}\n", json!({"type": "HELLO_OK", "alpn": ALPN}), json!({"type": "CLOSE", "stream_id": "s1"}))
                } else {
                    format!("{}\n", json!({"type": "AUTH_REJECT", "error": "endpoint_mismatch"}))
                };
                send.write_all(out.as_bytes()).await.unwrap();
                send.flush().await.unwrap();
                let _ = conn.closed().await;
                let _ = tx.send(Instant::now()).await;
            });
        }
    });
    MockGw { ep, accepts, closed_rx }
}

fn target_for(gw: &Endpoint) -> DialTarget {
    let addr = gw.bound_sockets().into_iter().find(|s| s.is_ipv4()).unwrap();
    let port = addr.port();
    DialTarget {
        gateway_endpoint_id: gw.id().to_string(),
        direct_addrs: vec![SocketAddr::from(([127, 0, 0, 1], port))],
    }
}

#[test]
fn endpoint_id_is_deterministic_and_matches_dev_gateway() {
    let k = [42u8; 32];
    assert_eq!(endpoint_id_of(&k), endpoint_id_of(&k));
    assert_ne!(endpoint_id_of(&k), endpoint_id_of(&[43u8; 32]));
    let dev: &[u8; 32] = include_bytes!("../../stream-gateway/dev/gateway_dev.key");
    assert_eq!(endpoint_id_of(dev).to_string(), DEV_GW_ID);
    let s = endpoint_id_of(&k).to_string();
    assert_eq!(EndpointId::from_str(&s).unwrap(), endpoint_id_of(&k));
}

#[test]
fn bind_refuses_public_and_unspecified() {
    assert!(bind_addrs("127.0.0.1:0").is_ok());
    assert_eq!(bind_addrs("8.8.8.8:0").unwrap_err(), guard::REASON_PUBLIC_ADDR);
    assert_eq!(bind_addrs("0.0.0.0:0").unwrap_err(), guard::REASON_PUBLIC_ADDR);
    assert_eq!(bind_addrs("[::1]:0").unwrap_err(), guard::REASON_BAD_ADDR);
}

#[tokio::test]
async fn builder_has_no_relay_no_discovery_private_sockets() {
    let ep = bind_endpoint(SecretKey::from_bytes(&[1u8; 32]), "127.0.0.1:0").await.unwrap();
    assert_eq!(ep.addr().relay_urls().count(), 0);
    assert!(ep.discovery().is_empty());
    let socks = ep.bound_sockets();
    assert!(!socks.is_empty());
    assert!(socks.iter().all(|s| s.ip().is_loopback()), "{socks:?}");
    ep.close().await;
}

#[tokio::test]
async fn dial_session_then_kill_closes_within_2s() {
    let peer_secret = SecretKey::from_bytes(&[9u8; 32]);
    let mut gw = mock_gateway(peer_secret.public()).await;
    let ep = bind_endpoint(peer_secret, "127.0.0.1:0").await.unwrap();
    let state = peer_state();
    let slot = TransportSlot::default();
    let (st, sl, tg, ep2) = (state.clone(), slot.clone(), target_for(&gw.ep), ep.clone());
    let task = tokio::spawn(async move {
        dial_loop(&st, &sl, &reqwest::Client::new(), &RelayHandle::default(), &ep2, &tg).await
    });
    let t0 = Instant::now();
    loop {
        {
            let g = state.read().await;
            if g.connected && g.streams.get("s1").and_then(|s| s.closed_by.clone()).as_deref() == Some("gateway") {
                break;
            }
        }
        assert!(t0.elapsed() < Duration::from_secs(10), "no session: {}", state.read().await.last_error);
        sleep(Duration::from_millis(50)).await;
    }
    assert!(slot.is_active().await);
    // kill switch: same order as /peer/kill
    state.write().await.kill_requested = true;
    let k0 = Instant::now();
    let out = slot.kill("peer_kill").await;
    assert!(out.had_transport);
    assert!(k0.elapsed() < Duration::from_secs(2), "{:?}", k0.elapsed());
    let closed_at = timeout(Duration::from_secs(2), gw.closed_rx.recv()).await.expect("gw saw close").unwrap();
    assert!(closed_at.duration_since(k0) < Duration::from_secs(2));
    sleep(Duration::from_millis(600)).await;
    assert!(!state.read().await.connected);
    assert_eq!(gw.accepts.load(Ordering::SeqCst), 1, "killed peer must not redial");
    task.abort();
    ep.close().await;
    gw.ep.close().await;
}

#[tokio::test]
async fn endpoint_mismatch_closes_and_does_not_retry() {
    let gw = mock_gateway(SecretKey::from_bytes(&[5u8; 32]).public()).await; // expects someone else
    let ep = bind_endpoint(SecretKey::from_bytes(&[6u8; 32]), "127.0.0.1:0").await.unwrap();
    let state = peer_state();
    let slot = TransportSlot::default();
    let r = timeout(
        Duration::from_secs(10),
        dial_loop(&state, &slot, &reqwest::Client::new(), &RelayHandle::default(), &ep, &target_for(&gw.ep)),
    )
    .await
    .expect("dial_loop returned");
    assert_eq!(r.as_deref(), Some("endpoint_mismatch"));
    assert_eq!(state.read().await.last_error, "endpoint_mismatch");
    assert!(!slot.is_active().await);
    sleep(Duration::from_millis(1500)).await;
    assert_eq!(gw.accepts.load(Ordering::SeqCst), 1, "no retry after endpoint_mismatch");
    ep.close().await;
    gw.ep.close().await;
}

#[tokio::test]
async fn wrong_gateway_id_fails_tls() {
    let gw = mock_gateway(SecretKey::from_bytes(&[9u8; 32]).public()).await;
    let ep = bind_endpoint(SecretKey::from_bytes(&[9u8; 32]), "127.0.0.1:0").await.unwrap();
    let mut t = target_for(&gw.ep);
    t.gateway_endpoint_id = DEV_GW_ID.into(); // real gateway key is not this mock's
    assert!(connect(&ep, &t).await.is_err());
    ep.close().await;
    gw.ep.close().await;
}
