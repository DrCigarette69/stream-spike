//! AUTH_TICKET + newline JSON frame I/O helpers.
use crate::egress::egress_denied;
use crate::state::{RelayHandle, SharedState, StreamInfo};
use crate::ticket::verify_ticket;
use serde_json::{json, Value};
use stream_proto::ALPN;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub(crate) async fn handle_auth_ticket(
    state: &SharedState,
    http: &reqwest::Client,
    writer: &mut tokio::net::tcp::OwnedWriteHalf,
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
            },
        );
    }
    let _ = send_line(writer, &json!({"type":"AUTH_OK"})).await;
}

pub(crate) async fn send_line(
    writer: &mut tokio::net::tcp::OwnedWriteHalf,
    obj: &Value,
) -> Result<(), std::io::Error> {
    let mut line = serde_json::to_vec(obj).unwrap_or_default();
    line.push(b'\n');
    writer.write_all(&line).await?;
    writer.flush().await?;
    Ok(())
}

pub(crate) async fn read_line(
    reader: &mut BufReader<tokio::net::tcp::OwnedReadHalf>,
) -> Result<Option<String>, String> {
    let mut buf = String::new();
    match reader.read_line(&mut buf).await {
        Ok(0) => Ok(None),
        Ok(_) => Ok(Some(buf.trim_end_matches(['\r', '\n']).to_string())),
        Err(e) => Err(e.to_string()),
    }
}


pub(crate) async fn handle_tunnel_msg(
    state: &SharedState,
    http: &reqwest::Client,
    writer: &mut tokio::net::tcp::OwnedWriteHalf,
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
                if let Some(st) = g.streams.get_mut(sid) {
                    st.opened = true;
                }
            }
        }
        "BYTES" => {
            let sid = msg
                .get("stream_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let n = msg.get("n").and_then(|v| v.as_u64()).unwrap_or(0);
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
