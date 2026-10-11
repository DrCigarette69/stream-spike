//! A4.4: DATA pump over the pilot stream (A4.3 frames, Platform e20498d / 9cff11c).
//!
//! Gateway `DATA` (client->site bytes) -> the pinned site socket; site bytes ->
//! `DATA` frames (<= `MAX_DATA_CHUNK` raw bytes) -> Gateway. Site EOF -> `CLOSE`
//! reason `site_closed`; Gateway `CLOSE` drops the socket without echo. The budget
//! counts only bytes actually read from / written to the pinned socket. Plane-wide
//! stops (kill switch off / stale / mismatch / budget) come in through the
//! connection's kill watch (`close_all`, which also queues their CLOSE).
//! Frames for the Gateway go through the session's outbound channel so a stream's
//! last DATA is always written before its `site_closed` CLOSE.
use crate::pilot_egress::{connect_pinned, SharedPlane};
use serde_json::Value;
use std::net::SocketAddr;
use stream_proto::guard::REASON_EGRESS_BUDGET_EXCEEDED;
use stream_proto::relay::{Data, MAX_DATA_CHUNK};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::{mpsc, watch};

pub type Outbound = mpsc::Sender<Value>;
/// Gateway->site chunks buffered per stream (64 x 16 KiB max).
pub const SITE_QUEUE: usize = 64;
pub const SITE_CLOSED: &str = "site_closed";

/// OPEN: register the stream's inbound queue now (DATA may follow OPEN at once),
/// then connect to the pinned address and pump.
pub fn spawn_open(plane: SharedPlane, sid: String, host: String, port: u16, addr: SocketAddr) {
    let (tx, rx) = mpsc::channel(SITE_QUEUE);
    let out = {
        let mut p = plane.lock().unwrap();
        p.site_tx.insert(sid.clone(), tx);
        p.outbound.clone()
    };
    tokio::spawn(async move {
        let Some(out) = out else {
            plane.lock().unwrap().site_tx.remove(&sid);
            plane.lock().unwrap().queue_close(&sid, "peer_offline");
            return;
        };
        match connect_pinned(&plane, &sid, &host, port, addr).await {
            Ok((up, kill)) => {
                let _ = pump_frames(&plane, &sid, up, rx, kill, &out).await;
            }
            Err(r) => {
                plane.lock().unwrap().site_tx.remove(&sid);
                plane.lock().unwrap().queue_close(&sid, &r.reason);
            }
        }
    });
}

/// Gateway `DATA` frame -> that stream's site queue. Unknown stream / bad frame -> false.
pub async fn on_gateway_data(plane: &SharedPlane, msg: &Value) -> bool {
    let Ok(d) = serde_json::from_value::<Data>(msg.clone()) else { return false };
    let Some(raw) = d.bytes() else {
        eprintln!("peer data_frame_invalid {}", d.stream_id);
        return false;
    };
    let tx = plane.lock().unwrap().site_tx.get(&d.stream_id).cloned();
    match tx {
        Some(tx) => tx.send(raw).await.is_ok(),
        None => false,
    }
}

/// Copy between the pinned site socket and the pilot stream. Returns the end reason.
pub async fn pump_frames<S: AsyncRead + AsyncWrite + Unpin>(
    plane: &SharedPlane,
    sid: &str,
    mut site: S,
    mut from_gw: mpsc::Receiver<Vec<u8>>,
    mut kill: watch::Receiver<Option<&'static str>>,
    out: &Outbound,
) -> &'static str {
    let mut buf = vec![0u8; MAX_DATA_CHUNK];
    let early = *kill.borrow();
    let end = if let Some(r) = early {
        r
    } else {
        loop {
            tokio::select! {
                _ = kill.changed() => {
                    let r = *kill.borrow();
                    break r.unwrap_or("closed");
                }
                r = site.read(&mut buf) => match r {
                    Ok(0) | Err(_) => {
                        let _ = out.send(crate::egress_state::close_frame(sid, SITE_CLOSED)).await;
                        break SITE_CLOSED;
                    }
                    Ok(n) => {
                        if !count(plane, n) {
                            break REASON_EGRESS_BUDGET_EXCEEDED;
                        }
                        let f = serde_json::to_value(Data::new(sid, &buf[..n])).unwrap_or(Value::Null);
                        if out.send(f).await.is_err() {
                            break "session_gone";
                        }
                    }
                },
                m = from_gw.recv() => match m {
                    Some(raw) => {
                        if !count(plane, raw.len()) {
                            break REASON_EGRESS_BUDGET_EXCEEDED;
                        }
                        if site.write_all(&raw).await.is_err() {
                            let _ = out.send(crate::egress_state::close_frame(sid, SITE_CLOSED)).await;
                            break SITE_CLOSED;
                        }
                    }
                    None => break "gateway",
                },
            }
        }
    };
    let _ = site.shutdown().await;
    let mut p = plane.lock().unwrap();
    p.site_tx.remove(sid);
    p.unregister(sid);
    eprintln!("peer egress_end {sid} reason={end}");
    end
}

/// Count real pinned-socket bytes; over the cap -> close every stream (queues CLOSEs).
fn count(plane: &SharedPlane, n: usize) -> bool {
    let mut p = plane.lock().unwrap();
    if p.add_bytes(n as u64).is_err() {
        p.close_all(REASON_EGRESS_BUDGET_EXCEEDED);
        return false;
    }
    true
}

#[cfg(test)]
#[path = "pilot_data_tests.rs"]
mod tests;
