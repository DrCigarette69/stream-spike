//! AUTH_TICKET + newline JSON frame I/O helpers.
use crate::egress::egress_denied;
use crate::state::{RelayHandle, SharedState, StreamInfo};
use crate::ticket::verify_ticket;
use serde_json::{json, Value};
use stream_proto::ALPN;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

pub(crate) async fn handle_auth_ticket<W: AsyncWrite + Unpin>(
    state: &SharedState,
    http: &reqwest::Client,
    writer: &mut W,
    msg: &Value,
) {
    let frame_alpn = msg.get("alpn").and_then(|v| v.as_str());
    if frame_alpn != Some(ALPN) {
        {
            let mut g = state.write().await;
            g.auth_rejects += 1;
        }
        let _ = send_line(writer, &json!({"type":"AUTH_REJECT","error":"bad_alpn"})).await;
        return;
    }
    let dest_host = msg
        .get("dest_host")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let dest_port = msg
        .get("dest_port")
        .and_then(|v| v.as_u64().or_else(|| v.as_i64().map(|i| i as u64)))
        .unwrap_or(0) as u16;
    if let Some(denied) = egress_denied(&dest_host, dest_port) {
        {
            let mut g = state.write().await;
            g.auth_rejects += 1;
        }
        let _ = send_line(
            writer,
            &json!({"type":"AUTH_REJECT","error":denied,"egress_denied":true}),
        )
        .await;
        return;
    }
    // A4.4: iroh_pilot plane -- switch, version, budget, floor, allowlist,
    // resolve-and-pin. Per-OPEN refusals reject this stream only (no P8).
    let plane = state.read().await.egress.clone();
    let mut pinned = None;
    if let Some(p) = &plane {
        let sid = msg.get("stream_id").and_then(|v| v.as_str()).unwrap_or("");
        match crate::pilot_egress::check_and_pin(p, sid, &dest_host, dest_port).await {
            Ok(a) => pinned = Some(a),
            Err(r) => {
                state.write().await.auth_rejects += 1;
                let _ = send_line(writer, &json!({"type":"AUTH_REJECT","error":r.reason,"egress_refused":true})).await;
                return;
            }
        }
    }
    let ticket_json = msg
        .get("ticket_json")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let cfg = {
        let g = state.read().await;
        g.cfg.clone()
    };
    let ver = verify_ticket(http, &cfg, ticket_json, frame_alpn).await;
    if !ver.ok {
        {
            let mut g = state.write().await;
            g.auth_rejects += 1;
        }
        let _ = send_line(
            writer,
            &json!({"type":"AUTH_REJECT","error":ver.error}),
        )
        .await;
        return;
    }
    let stream_id = msg
        .get("stream_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    {
        let mut g = state.write().await;
        g.streams.insert(
            stream_id,
            StreamInfo {
                bytes: 0,
                closed: false,
                closed_by: None,
                dest_host,
                dest_port,
                opened: false,
                pinned,
            },
        );
    }
    let _ = send_line(writer, &json!({"type":"AUTH_OK"})).await;
}

pub(crate) async fn send_line<W: AsyncWrite + Unpin>(
    writer: &mut W,
    obj: &Value,
) -> Result<(), std::io::Error> {
    let mut line = serde_json::to_vec(obj).unwrap_or_default();
    line.push(b'\n');
    writer.write_all(&line).await?;
    writer.flush().await?;
    Ok(())
}

pub(crate) async fn read_line<R: AsyncRead + Unpin>(
    reader: &mut BufReader<R>,
) -> Result<Option<String>, String> {
    let mut buf = String::new();
    match reader.read_line(&mut buf).await {
        Ok(0) => Ok(None),
        Ok(_) => Ok(Some(buf.trim_end_matches(['\r', '\n']).to_string())),
        Err(e) => Err(e.to_string()),
    }
}


pub(crate) async fn handle_tunnel_msg<W: AsyncWrite + Unpin>(
    state: &SharedState,
    http: &reqwest::Client,
    writer: &mut W,
    msg: &Value,
) -> bool {
    // returns true if relay loop should break
    let t = msg.get("type").and_then(|v| v.as_str()).unwrap_or("");
    match t {
        "AUTH_TICKET" => {
            handle_auth_ticket(state, http, writer, msg).await;
        }
        "OPEN" => {
            if let Some(sid) = msg.get("stream_id").and_then(|v| v.as_str()) {
                let mut g = state.write().await;
                let plane = g.egress.clone();
                if let Some(st) = g.streams.get_mut(sid) {
                    st.opened = true;
                    if let (Some(p), Some(addr)) = (plane, st.pinned) {
                        crate::pilot_egress::spawn_open(p, sid.to_string(), st.dest_host.clone(), st.dest_port, addr);
                    }
                } else if let Some(p) = plane {
                    // OPEN without a checked AUTH_TICKET never dials in pilot mode.
                    p.lock().unwrap().queue_close(sid, "egress_not_allowlisted");
                }
            }
        }
        "EGRESS_STATE" => {
            let plane = state.read().await.egress.clone();
            if let Some(p) = plane {
                crate::egress_state::apply_state(&p, msg);
            }
        }
        "BYTES" => {
            let sid = msg
                .get("stream_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let n = msg.get("n").and_then(|v| v.as_u64()).unwrap_or(0);
            // A4.4: the pilot budget counts only real bytes on the pinned socket
            // (pilot_egress::pump), never Gateway-reported `n` (no double count).
            let mut g = state.write().await;
            if let Some(st) = g.streams.get_mut(&sid) {
                if !st.closed {
                    if crate::egress::egress_denied(&st.dest_host, st.dest_port).is_some() {
                        st.closed = true;
                        st.closed_by = Some("egress_floor".into());
                        g.auth_rejects += 1;
                    } else {
                        st.bytes = st.bytes.saturating_add(n);
                    }
                }
            }
        }
        "CLOSE" => {
            let sid = msg
                .get("stream_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            {
                let mut g = state.write().await;
                let st = g.streams.entry(sid.clone()).or_insert_with(|| StreamInfo {
                    bytes: 0,
                    ..Default::default()
                });
                st.closed = true;
                st.closed_by = Some("gateway".into());
                if let Some(p) = g.egress.clone() {
                    p.lock().unwrap().close_one(&sid, "gateway");
                }
            }
            eprintln!("peer teardown stream {sid} (gateway CLOSE)");
        }
        "ERR" => {
            set_error(state, &msg.to_string()).await;
            return true;
        }
        _ => {}
    }
    false
}

pub(crate) async fn set_error(state: &SharedState, msg: &str) {
    let mut g = state.write().await;
    g.last_error = msg.to_string();
}

pub(crate) async fn mark_offline(state: &SharedState) {
    let mut g = state.write().await;
    g.connected = false;
    g.online = false;
}


pub async fn disconnect_now(state: &SharedState, handle: &RelayHandle) {
    {
        let mut g = state.write().await;
        g.connected = false;
        g.online = false;
        g.force_disconnect = true;
    }
    handle.request_close().await;
}
