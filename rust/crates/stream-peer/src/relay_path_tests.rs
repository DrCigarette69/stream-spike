//! A4.4 UDP-counter relay-path watcher: fake counter source (no iroh, no network).
use super::*;
use crate::kill::{ActiveTransport, CloseFut, TransportSlot};
use crate::pilot_egress_tests::{pilot_state, plane, Fake};
use crate::pilot_target::{watch_path, PathKind, RELAY_PATH_REQUIRED};
use std::sync::atomic::AtomicBool;
use std::time::Duration;

struct Conn(Arc<AtomicBool>);
impl ActiveTransport for Conn {
    fn close<'a>(&'a self, _r: &'a str) -> CloseFut<'a> {
        Box::pin(async move { self.0.store(true, Ordering::SeqCst) })
    }
    fn label(&self) -> &str {
        "mock_pilot"
    }
}

async fn setup() -> (crate::pilot_egress::SharedPlane, crate::state::SharedState, TransportSlot, Arc<AtomicBool>) {
    let p = plane(Fake::with(&[]), 5000, 1 << 20, Lane::Pilot);
    let st = pilot_state(&p);
    let closed = Arc::new(AtomicBool::new(false));
    let slot = TransportSlot::default();
    slot.set(Box::new(Conn(closed.clone()))).await;
    (p, st, slot, closed)
}

#[test]
fn counts_sum_send_recv_v4_v6() {
    let c = UdpCounts { send_v4: 1, send_v6: 2, recv_v4: 3, recv_v6: 4 };
    assert_eq!(c.total(), 10);
    assert_eq!(udp_violation(&FakeUdp::new(0)), None);
    assert!(udp_violation(&FakeUdp::new(1)).unwrap().starts_with("udp_datagrams:1 "));
    struct V6Recv;
    impl UdpCounterSource for V6Recv {
        fn read(&self) -> Option<UdpCounts> {
            Some(UdpCounts { recv_v6: 1, ..Default::default() })
        }
    }
    assert!(udp_violation(&V6Recv).unwrap().contains("recv_v6=1"));
    assert_eq!(udp_violation(&Unavailable), None);
    assert_eq!(counters_line(&Unavailable), "udp_counters=unavailable");
    assert_eq!(counters_line(&FakeUdp::new(0)), "udp_counters total=0 send_v4=0 send_v6=0 recv_v4=0 recv_v6=0");
}

#[tokio::test]
async fn zero_datagrams_stays_up() {
    let (p, st, slot, closed) = setup().await;
    let _rx = p.lock().unwrap().register("s1", "site1.pilot.example:443".into());
    let v = watch_path(&st, &slot, || PathKind::None, &FakeUdp::new(0), tokio::time::sleep(Duration::from_millis(400))).await;
    assert_eq!(v, None);
    assert!(!closed.load(Ordering::SeqCst));
    assert_eq!(p.lock().unwrap().open_streams(), 1);
    assert_eq!(st.read().await.p8_reason, None);
}

#[tokio::test]
async fn one_datagram_trips_once_within_2s() {
    let (p, st, slot, closed) = setup().await;
    let _rx = p.lock().unwrap().register("s1", "site1.pilot.example:443".into());
    let udp = FakeUdp::new(0);
    let u2 = udp.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(250)).await;
        u2.set(1);
    });
    let t0 = tokio::time::Instant::now();
    // conn_type keeps saying None (as iroh 0.95.1 does under RelayOnly): counters alone trip it.
    let v = watch_path(&st, &slot, || PathKind::None, &udp, std::future::pending()).await;
    assert!(v.as_deref().unwrap().starts_with("udp_datagrams:1 "), "{v:?}");
    assert!(t0.elapsed() < Duration::from_secs(2), "kill <= 2 s");
    assert!(closed.load(Ordering::SeqCst), "transport closed");
    assert_eq!(p.lock().unwrap().open_streams(), 0);
    assert_eq!(p.lock().unwrap().take_pending_close()[0].1, RELAY_PATH_REQUIRED);
    let mut g = st.write().await;
    assert_eq!(g.p8_reason.as_deref(), Some(RELAY_PATH_REQUIRED));
    // Shown once: the same reason again prints nothing, connection_lost does not replace it.
    assert_eq!(crate::offline::enter(&mut g, RELAY_PATH_REQUIRED), None);
    drop(g);
    // ... and the dial loop does not retry after it.
    assert!(!retry_after(Some(RELAY_PATH_REQUIRED)));
    assert!(!retry_after(Some("gateway_mismatch")));
    assert!(retry_after(None));
}

#[tokio::test]
async fn conn_type_direct_is_secondary_signal() {
    let (_p, st, slot, closed) = setup().await;
    let v = watch_path(&st, &slot, || PathKind::Direct("10.73.0.1:9102".into()), &FakeUdp::new(0), std::future::pending()).await;
    assert_eq!(v.as_deref(), Some("direct_path:10.73.0.1:9102"));
    assert!(closed.load(Ordering::SeqCst));
}

#[test]
fn pilot_fails_closed_without_counters() {
    assert!(require_counters(&Unavailable, Lane::Pilot).unwrap_err().ends_with("udp_counters_unavailable"));
    assert!(require_counters(&Unavailable, Lane::Local).is_ok());
    assert!(require_counters(&FakeUdp::new(0), Lane::Pilot).is_ok());
}

#[test]
fn offset_source_adds_to_real_counts() {
    assert_eq!(offset(Box::new(Unavailable), 3).read().unwrap().total(), 3);
    assert_eq!(offset(Box::new(FakeUdp::new(2)), 1).read().unwrap().total(), 3);
}

#[cfg(not(feature = "a4_local"))]
#[test]
fn hook_is_inert_without_a4_local() {
    assert_eq!(with_test_hook(Box::new(Unavailable)).read(), None);
}
