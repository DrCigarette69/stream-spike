//! Metering loop: BYTES to peer + POST /v1/usage/flush; honor stop/frozen/dest_denied.

use serde_json::json;

use crate::control;
use crate::relay;
use crate::state::AppState;

pub fn spawn_meter(st: AppState, stream_id: String, peer_id: String) {
    tokio::spawn(async move {
        meter_loop(st, stream_id, peer_id).await;
    });
}

async fn meter_loop(st: AppState, stream_id: String, peer_id: String) {
    let chunk: u64 = 64_000;
    let interval = std::time::Duration::from_millis(150);
    let mut ticks: u64 = 0;

    loop {
        let (stop, dest_host, dest_port) = {
            let streams = st.streams.read().await;
            match streams.get(&stream_id) {
                Some(s) if !s.stop => (false, s.dest_host.clone(), s.dest_port),
                _ => (true, String::new(), 0u16),
            }
        };
        if stop {
            break;
        }

        if let Err(e) = relay::send_bytes(&st, &peer_id, &stream_id, chunk).await {
            relay::drop_peer(&st, &peer_id, &format!("bytes_failed:{e}")).await;
            let mut streams = st.streams.write().await;
            if let Some(s) = streams.get_mut(&stream_id) {
                s.stop = true;
            }
            break;
        }

        ticks += 1;
        let mut flush_body = json!({ "stream_id": stream_id, "bytes": chunk });
        if ticks % 3 == 1 {
            flush_body["dest_host"] = json!(dest_host);
            flush_body["dest_port"] = json!(dest_port);
        }

        let (_code, resp) = control::usage_flush(&st, flush_body).await;
        if let Some(ev) = resp.get("event") {
            tracing::info!("EVENT {}", ev);
        }
        let should_stop = resp.get("stop").and_then(|v| v.as_bool()).unwrap_or(false)
            || resp.get("balance_state").and_then(|v| v.as_str()) == Some("stopped")
            || resp.get("frozen").and_then(|v| v.as_bool()).unwrap_or(false)
            || resp.get("dest_denied").and_then(|v| v.as_bool()).unwrap_or(false);

        if should_stop {
            {
                let mut streams = st.streams.write().await;
                if let Some(s) = streams.get_mut(&stream_id) {
                    s.stop = true;
                }
            }
            relay::close_stream_on_peer(&st, &peer_id, &stream_id).await;
            let why = if resp.get("frozen").and_then(|v| v.as_bool()).unwrap_or(false) {
                "frozen"
            } else if resp.get("dest_denied").and_then(|v| v.as_bool()).unwrap_or(false) {
                "dest_denied"
            } else {
                "stopped"
            };
            tracing::info!("stream stopped {stream_id} reason={why}");
            break;
        }

        tokio::time::sleep(interval).await;
    }

    relay::close_stream_on_peer(&st, &peer_id, &stream_id).await;
}
