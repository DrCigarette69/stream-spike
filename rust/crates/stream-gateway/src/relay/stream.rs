//! AUTH_TICKET / OPEN / BYTES / CLOSE on enrolled peer sockets.
use tokio::io::AsyncWriteExt;

use stream_proto::ndjson_line;
use stream_proto::relay::{AuthTicketFrame, Bytes, Close, Open};
use stream_proto::ALPN;

use crate::state::AppState;

use super::io::read_line;

pub async fn drop_peer(st: &AppState, peer_id: &str, reason: &str) {
    let meta = {
        let mut peers = st.peers.write().await;
        peers.remove(peer_id)
    };
    if let Some(meta) = meta {
        meta.dead.store(true, std::sync::atomic::Ordering::SeqCst);
        if let Ok(mut s) = meta.sock.try_lock() {
            s.close_with(reason).await;
        }
        tracing::info!("peer dropped {peer_id} {reason}");
    }
}

/// Send AUTH_TICKET, wait AUTH_OK, send OPEN. Returns (ok, why).
pub async fn open_stream_to_peer(
    st: &AppState,
    peer_id: &str,
    ticket_json: &str,
    stream_id: &str,
    dest_host: &str,
    dest_port: u16,
) -> (bool, String) {
    let meta = {
        let peers = st.peers.read().await;
        peers.get(peer_id).cloned()
    };
    let Some(meta) = meta else {
        return (false, "peer_offline".into());
    };

    let mut g = meta.sock.lock().await;

    // A3.1/A3.3: on iroh_local the ticket must bind the authenticated remote EndpointId.
    if let Some(auth) = meta.auth_endpoint_id.as_deref() {
        if let Err(reason) = ticket_binding(auth, ticket_json) {
            let frame = serde_json::json!({
                "type": "AUTH_REJECT",
                "error": reason,
                "reason": reason,
                "stream_id": stream_id,
            });
            if let Ok(bytes) = ndjson_line(&frame) {
                let _ = g.write_all(&bytes).await;
                let _ = g.flush().await;
            }
            g.close_with(reason).await;
            drop(g);
            drop_peer(st, peer_id, reason).await;
            tracing::info!("AUTH_REJECT {reason} peer={peer_id} remote={auth}");
            return (false, reason.to_string());
        }
    }

    let result = tokio::time::timeout(std::time::Duration::from_secs(10), async {
        let frame = AuthTicketFrame::new(ALPN, ticket_json, stream_id, dest_host, dest_port);
        g.write_all(&ndjson_line(&frame).map_err(|e| e.to_string())?)
            .await
            .map_err(|e| e.to_string())?;

        let line = read_line(&mut *g).await.map_err(|e| e.to_string())?;
        let resp: serde_json::Value =
            serde_json::from_str(line.trim()).unwrap_or_else(|_| serde_json::json!({}));
        let t = resp.get("type").and_then(|v| v.as_str()).unwrap_or("");
        if t != "AUTH_OK" {
            let why = resp
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("auth_rejected")
                .to_string();
            return Ok::<(bool, String), String>((false, why));
        }
        if resp.get("egress_denied").and_then(|v| v.as_bool()).unwrap_or(false) {
            let why = resp
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("egress_denied")
                .to_string();
            return Ok((false, why));
        }
        let open = Open::new(stream_id);
        g.write_all(&ndjson_line(&open).map_err(|e| e.to_string())?)
            .await
            .map_err(|e| e.to_string())?;
        Ok((true, "ok".into()))
    })
    .await;

    match result {
        Ok(Ok(pair)) => pair,
        Ok(Err(e)) => {
            drop(g);
            drop_peer(st, peer_id, &e).await;
            (false, e)
        }
        Err(_) => {
            drop(g);
            drop_peer(st, peer_id, "auth_timeout").await;
            (false, "auth_timeout".into())
        }
    }
}

pub async fn close_stream_on_peer(st: &AppState, peer_id: &str, stream_id: &str) {
    let meta = {
        let peers = st.peers.read().await;
        peers.get(peer_id).cloned()
    };
    let Some(meta) = meta else {
        return;
    };
    let mut g = meta.sock.lock().await;
    let frame = Close::new(stream_id);
    if let Ok(bytes) = ndjson_line(&frame) {
        if let Err(e) = g.write_all(&bytes).await {
            drop(g);
            drop_peer(st, peer_id, &format!("close_failed:{e}")).await;
        }
    }
}

pub async fn send_bytes(st: &AppState, peer_id: &str, stream_id: &str, n: u64) -> Result<(), String> {
    let meta = {
        let peers = st.peers.read().await;
        peers.get(peer_id).cloned()
    };
    let Some(meta) = meta else {
        return Err("peer_offline".into());
    };
    let mut g = meta.sock.lock().await;
    let frame = Bytes::new(stream_id, n);
    g.write_all(&ndjson_line(&frame).map_err(|e| e.to_string())?)
        .await
        .map_err(|e| e.to_string())
}

#[cfg(feature = "iroh")]
fn ticket_binding(auth: &str, ticket_json: &str) -> Result<(), &'static str> {
    crate::iroh_local::check_ticket_binding(auth, ticket_json)
}

#[cfg(not(feature = "iroh"))]
fn ticket_binding(_auth: &str, _ticket_json: &str) -> Result<(), &'static str> {
    // auth_endpoint_id is only ever set by the iroh transport.
    Err("endpoint_mismatch")
}
