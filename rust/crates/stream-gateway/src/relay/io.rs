//! Fake relay accept loop + HELLO enroll.
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::Mutex;

use stream_proto::ndjson_line;
use stream_proto::relay::{ErrFrame, HelloOk};
use stream_proto::ALPN;

use crate::conn::PeerConn;
use crate::control;
use crate::state::{AppState, PeerMeta};

pub(crate) async fn read_line<S: AsyncRead + Unpin>(stream: &mut S) -> std::io::Result<String> {
    let mut buf = Vec::with_capacity(256);
    loop {
        let mut b = [0u8; 1];
        let n = stream.read(&mut b).await?;
        if n == 0 {
            break;
        }
        if b[0] == b'\n' {
            break;
        }
        if b[0] != b'\r' {
            buf.push(b[0]);
        }
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

pub async fn run_relay(st: AppState, listen: std::net::SocketAddr) -> std::io::Result<()> {
    let listener = TcpListener::bind(listen).await?;
    let label = if st.transport == "iroh" || st.transport == "iroh_loopback" {
        "iroh loopback"
    } else {
        "fake Relay"
    };
    tracing::info!("{label} listening on {listen} transport={}", st.transport);
    loop {
        let (conn, addr) = listener.accept().await?;
        let st2 = st.clone();
        tokio::spawn(async move {
            conn.set_nodelay(true).ok();
            if let Err(e) = handle_peer_conn(st2, PeerConn::Tcp(conn), None).await {
                tracing::debug!("peer conn error {addr}: {e}");
            }
        });
    }
}

/// Relay session on one peer connection. `auth_endpoint_id` is the transport-authenticated
/// peer id (iroh_local); `None` for TCP transports (unchanged A1/A2 behaviour).
pub(crate) async fn handle_peer_conn(
    st: AppState,
    mut conn: PeerConn,
    auth_endpoint_id: Option<String>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let line = match tokio::time::timeout(std::time::Duration::from_secs(30), read_line(&mut conn)).await {
        Ok(Ok(l)) if !l.is_empty() => l,
        Ok(Ok(_)) | Err(_) => return Ok(()),
        Ok(Err(e)) => return Err(e.into()),
    };
    let hello: serde_json::Value = serde_json::from_str(line.trim()).unwrap_or_else(|_| serde_json::json!({}));

    if hello.get("type").and_then(|v| v.as_str()) != Some("HELLO") {
        let frame = ErrFrame::new("expected_HELLO", None);
        conn.write_all(&ndjson_line(&frame)?).await?;
        return Ok(());
    }

    let peer_id = hello
        .get("peer_id")
        .and_then(|v| v.as_str())
        .unwrap_or("peer_demo")
        .to_string();
    let mut endpoint_id = hello
        .get("endpoint_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if let Some(auth) = &auth_endpoint_id {
        // iroh_local: the authenticated id is authoritative; a different self-declared id is refused.
        if !endpoint_id.is_empty() && &endpoint_id != auth {
            let frame = ErrFrame::new("endpoint_mismatch", Some("endpoint_mismatch"));
            conn.write_all(&ndjson_line(&frame)?).await?;
            conn.close_with("endpoint_mismatch").await;
            return Ok(());
        }
        endpoint_id = auth.clone();
    }
    let ack = hello
        .get("isp_ack_version")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let host_tier = hello
        .get("host_tier")
        .and_then(|v| v.as_str())
        .unwrap_or("casual")
        .to_string();

    if ack.is_empty() {
        let frame = ErrFrame::new("isp_ack_required", Some("peer_ack_gate"));
        conn.write_all(&ndjson_line(&frame)?).await?;
        return Ok(());
    }

    control::enroll_peer(&st, &peer_id, &endpoint_id, &ack, &host_tier).await;

    let dead = Arc::new(AtomicBool::new(false));
    let sock = Arc::new(Mutex::new(conn));

    {
        let mut peers = st.peers.write().await;
        if let Some(old) = peers.remove(&peer_id) {
            old.dead.store(true, Ordering::SeqCst);
            if let Ok(mut s) = old.sock.try_lock() {
                s.close_with("replaced").await;
            }
        }
        peers.insert(
            peer_id.clone(),
            PeerMeta {
                sock: sock.clone(),
                endpoint_id: endpoint_id.clone(),
                auth_endpoint_id: auth_endpoint_id.clone(),
                isp_ack_version: ack,
                host_tier,
                dead: dead.clone(),
            },
        );
    }

    {
        let mut g = sock.lock().await;
        let ok = HelloOk::new(ALPN, &st.transport);
        g.write_all(&ndjson_line(&ok)?).await?;
    }
    tracing::info!("peer online {peer_id} endpoint={endpoint_id}");

    while !dead.load(Ordering::SeqCst) {
        {
            let peers = st.peers.read().await;
            let meta = peers.get(&peer_id);
            if meta.map(|m| !Arc::ptr_eq(&m.sock, &sock)).unwrap_or(true) {
                break;
            }
        }
        if let Ok(g) = sock.try_lock() {
            if !g.probe_alive().await {
                break;
            }
        } else {
            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }

    {
        let mut peers = st.peers.write().await;
        if let Some(meta) = peers.get(&peer_id) {
            if Arc::ptr_eq(&meta.sock, &sock) {
                peers.remove(&peer_id);
                tracing::info!("peer offline {peer_id}");
            }
        }
    }
    Ok(())
}
