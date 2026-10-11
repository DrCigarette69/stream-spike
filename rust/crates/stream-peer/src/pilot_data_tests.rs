//! DATA pump unit tests: a duplex stands in for the pinned site socket, a channel for
//! the pilot stream. No network.
use super::*;
use crate::pilot_egress_tests::{plane, Fake};
use serde_json::json;
use stream_proto::guard::Lane;
use tokio::time::{timeout, Duration};

fn setup(cap: u64) -> (SharedPlane, mpsc::Sender<Vec<u8>>, mpsc::Receiver<Value>, Outbound, watch::Receiver<Option<&'static str>>, mpsc::Receiver<Vec<u8>>) {
    let p = plane(Fake::with(&[]), 5000, cap, Lane::Pilot);
    let (out_tx, out_rx) = mpsc::channel(256);
    let (gw_tx, gw_rx) = mpsc::channel(SITE_QUEUE);
    p.lock().unwrap().site_tx.insert("s1".into(), gw_tx.clone());
    p.lock().unwrap().outbound = Some(out_tx.clone());
    let kill = p.lock().unwrap().register("s1", "site1.pilot.example:443".into());
    (p, gw_tx, out_rx, out_tx, kill, gw_rx)
}

fn data_bytes(v: &Value) -> Vec<u8> {
    serde_json::from_value::<Data>(v.clone()).unwrap().bytes().unwrap()
}

#[tokio::test]
async fn data_both_ways_chunked_then_site_closed() {
    let (p, gw_tx, mut out_rx, out, kill, gw_rx) = setup(10 << 20);
    let (site, mut far) = tokio::io::duplex(1 << 20);
    let t = tokio::spawn({ let p = p.clone(); async move { pump_frames(&p, "s1", site, gw_rx, kill, &out).await } });
    // Gateway DATA frame -> site
    assert!(on_gateway_data(&p, &serde_json::to_value(Data::new("s1", b"GET / HTTP/1.1\r\n\r\n")).unwrap()).await);
    let mut got = [0u8; 18];
    far.read_exact(&mut got).await.unwrap();
    assert_eq!(&got, b"GET / HTTP/1.1\r\n\r\n");
    // site -> DATA frames, each <= 16 KiB raw
    let body: Vec<u8> = (0..40_000u32).map(|i| (i % 251) as u8).collect();
    far.write_all(&body).await.unwrap();
    far.shutdown().await.unwrap();
    let mut back = Vec::new();
    loop {
        let f = timeout(Duration::from_secs(2), out_rx.recv()).await.unwrap().unwrap();
        match f["type"].as_str().unwrap() {
            "DATA" => {
                let b = data_bytes(&f);
                assert!(b.len() <= MAX_DATA_CHUNK);
                back.extend(b);
            }
            "CLOSE" => {
                assert_eq!(f, json!({"type":"CLOSE","stream_id":"s1","reason":"site_closed"}));
                break;
            }
            other => panic!("{other}"),
        }
    }
    assert_eq!(back, body, "site bytes arrive in order before site_closed");
    assert_eq!(t.await.unwrap(), SITE_CLOSED);
    assert_eq!(p.lock().unwrap().used, 18 + 40_000, "only real site-socket bytes counted");
    assert_eq!(p.lock().unwrap().open_streams(), 0);
    drop(gw_tx);
}

#[tokio::test]
async fn gateway_close_drops_socket_without_echo() {
    let (p, _gw_tx, mut out_rx, out, kill, gw_rx) = setup(1 << 20);
    let (site, mut far) = tokio::io::duplex(4096);
    let t = tokio::spawn({ let p = p.clone(); async move { pump_frames(&p, "s1", site, gw_rx, kill, &out).await } });
    p.lock().unwrap().close_one("s1", "gateway");
    assert_eq!(timeout(Duration::from_secs(2), t).await.unwrap().unwrap(), "gateway");
    let mut b = [0u8; 1];
    assert_eq!(far.read(&mut b).await.unwrap(), 0, "site socket closed");
    assert!(out_rx.try_recv().is_err(), "no CLOSE echoed to the Gateway");
    assert!(!on_gateway_data(&p, &serde_json::to_value(Data::new("s1", b"x")).unwrap()).await);
}

#[tokio::test]
async fn plane_wide_stop_and_budget_close() {
    // flag off / stale / mismatch all arrive as close_all(<reason>)
    let (p, _gw_tx, _out_rx, out, kill, gw_rx) = setup(1 << 20);
    let (site, _far) = tokio::io::duplex(4096);
    let t = tokio::spawn({ let p = p.clone(); async move { pump_frames(&p, "s1", site, gw_rx, kill, &out).await } });
    tokio::time::sleep(Duration::from_millis(50)).await;
    p.lock().unwrap().close_all("egress_off");
    assert_eq!(timeout(Duration::from_secs(5), t).await.unwrap().unwrap(), "egress_off");
    assert_eq!(p.lock().unwrap().take_pending_close(), vec![("s1".to_string(), "egress_off".to_string())]);
    // byte cap: 100 bytes allowed, site sends 200
    let (p, _gw_tx, _out_rx, out, kill, gw_rx) = setup(100);
    let (site, mut far) = tokio::io::duplex(4096);
    let t = tokio::spawn({ let p = p.clone(); async move { pump_frames(&p, "s1", site, gw_rx, kill, &out).await } });
    far.write_all(&[7u8; 200]).await.unwrap();
    assert_eq!(timeout(Duration::from_secs(2), t).await.unwrap().unwrap(), REASON_EGRESS_BUDGET_EXCEEDED);
    assert_eq!(p.lock().unwrap().take_pending_close()[0].1, "egress_budget_exceeded");
}

#[tokio::test]
async fn bad_data_frames_ignored() {
    let (p, ..) = setup(1 << 20);
    assert!(!on_gateway_data(&p, &json!({"type":"DATA","stream_id":"s1","b64":"Zg="})).await);
    assert!(!on_gateway_data(&p, &json!({"type":"DATA","stream_id":"nope","b64":"Zg=="})).await);
    let big = stream_proto::b64::encode(&vec![0u8; MAX_DATA_CHUNK + 1]);
    assert!(!on_gateway_data(&p, &json!({"type":"DATA","stream_id":"s1","b64":big})).await, "> 16 KiB refused");
}

#[tokio::test]
async fn line_reader_is_cancel_safe() {
    use crate::frames::LineReader;
    let (a, mut b) = tokio::io::duplex(1 << 16);
    let mut lr = LineReader::new(tokio::io::BufReader::new(a));
    let line = format!("{{\"type\":\"DATA\",\"b64\":\"{}\"}}", "A".repeat(30_000));
    b.write_all(&line.as_bytes()[..10_000]).await.unwrap();
    // A competing branch wins while the line is half read ...
    tokio::select! { _ = lr.next_line() => panic!("no full line yet"), _ = tokio::time::sleep(Duration::from_millis(50)) => {} }
    b.write_all(&line.as_bytes()[10_000..]).await.unwrap();
    b.write_all(b"\n").await.unwrap();
    // ... and nothing was lost.
    assert_eq!(lr.next_line().await.unwrap().unwrap(), line);
}
