//! One tunnel session over any byte stream (TCP halves for fake_relay /
//! iroh_loopback, an iroh QUIC bi-stream for iroh_local): HELLO -> HELLO_OK ->
//! heartbeat + AUTH_TICKET / OPEN / BYTES / CLOSE until kill / clear-ack /
//! close. Logic moved verbatim from `relay.rs`; only iroh_local adds Gateway
//! `AUTH_REJECT` handling (`endpoint_mismatch` -> caller does not retry) and
//! the P2-withdrawn check.
use crate::config;
use crate::frames::{handle_tunnel_msg, read_line, send_line, set_error};
use crate::state::{RelayHandle, SharedState};
use crate::ticket::control_post;
use serde_json::{json, Value};
use stream_proto::ALPN;
use tokio::io::{AsyncRead, AsyncWrite, BufReader};
use tokio::sync::oneshot;
use tokio::time::{Duration, Instant};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEnd {
    /// HELLO exchange failed; caller backs off 1 s and retries.
    Handshake,
    /// Session ran and ended (kill, clear-ack, close, EOF, ERR).
    Ended,
    /// iroh_local only: Gateway sent AUTH_REJECT (e.g. `endpoint_mismatch`); do not retry.
    Rejected(String),
}

pub(crate) fn gateway_reject(transport: &str, msg: &Value) -> Option<String> {
    if !config::is_iroh_local(transport) && !config::is_iroh_pilot(transport) {
        return None;
    }
    let why = msg
        .get("error")
        .or_else(|| msg.get("reason"))
        .and_then(|v| v.as_str())
        .unwrap_or("auth_reject");
    match msg.get("type").and_then(|v| v.as_str()) {
        Some("AUTH_REJECT") => {}
        // A3.1: HELLO endpoint_id != authenticated id -> ERR endpoint_mismatch
        Some("ERR") if why == "endpoint_mismatch" => {}
        _ => return None,
    }
    Some(why.to_string())
}

pub async fn run<R, W>(
    state: &SharedState,
    handle: &RelayHandle,
    http: &reqwest::Client,
    reader: R,
    mut writer: W,
) -> SessionEnd
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let (ack, tier, peer_id, endpoint_id, heartbeat_s, control_url, transport) = {
        let g = state.read().await;
        (
            g.isp_ack_version.clone(),
            g.host_tier.clone(),
            g.cfg.peer_id.clone(),
            g.cfg.endpoint_id.clone(),
            g.cfg.heartbeat_s,
            g.cfg.control_url.clone(),
            g.cfg.transport.clone(),
        )
    };
    let iroh_local = config::is_iroh_local(&transport) || config::is_iroh_pilot(&transport);
    let mut reader = BufReader::new(reader);

    let hello = json!({
        "type": "HELLO",
        "peer_id": peer_id,
        "endpoint_id": endpoint_id,
        "isp_ack_version": ack,
        "host_tier": tier,
        "transport": transport,
    });
    if send_line(&mut writer, &hello).await.is_err() {
        return SessionEnd::Handshake;
    }

    let resp = match read_line(&mut reader).await {
        Ok(Some(line)) => serde_json::from_str::<Value>(&line).unwrap_or(json!({})),
        _ => {
            set_error(state, "HELLO read failed").await;
            return SessionEnd::Handshake;
        }
    };
    if let Some(why) = gateway_reject(&transport, &resp) {
        return SessionEnd::Rejected(why);
    }
    if resp.get("type").and_then(|v| v.as_str()) != Some("HELLO_OK") {
        set_error(state, &format!("HELLO failed: {resp}")).await;
        eprintln!("peer HELLO failed: {resp}");
        return SessionEnd::Handshake;
    }
    if let Some(a) = resp.get("alpn").and_then(|v| v.as_str()) {
        if a != ALPN {
            set_error(state, "bad HELLO_OK alpn").await;
            return SessionEnd::Handshake;
        }
    }

    {
        let mut g = state.write().await;
        g.connected = true;
        g.online = true;
        g.last_error.clear();
        g.force_disconnect = false;
        crate::offline::clear(&mut g);
    }
    eprintln!("peer online {peer_id} endpoint={endpoint_id} ack={ack}");

    let (close_tx, mut close_rx) = oneshot::channel::<()>();
    {
        let mut g = handle.close_tx.lock().await;
        *g = Some(close_tx);
    }

    let mut last_hb = Instant::now() - Duration::from_secs(3600);
    let hb = Duration::from_secs_f64(heartbeat_s.max(0.5));
    let mut end = SessionEnd::Ended;

    loop {
        {
            let g = state.read().await;
            if g.kill_requested
                || g.isp_ack_version.is_empty()
                || g.force_disconnect
                || (iroh_local && !g.p2_consent)
                || (g.cfg.p9_required() && !g.p9_ack)
            {
                break;
            }
        }
        if close_rx.try_recv().is_ok() {
            break;
        }
        if flush_pilot_closes(state, &mut writer).await.is_err() {
            break;
        }

        if last_hb.elapsed() >= hb {
            let (pid, tier, ack, ep) = {
                let g = state.read().await;
                (
                    g.cfg.peer_id.clone(),
                    g.host_tier.clone(),
                    g.isp_ack_version.clone(),
                    g.cfg.endpoint_id.clone(),
                )
            };
            let _ = control_post(
                http,
                &control_url,
                "/v1/peers/heartbeat",
                json!({
                    "peer_id": pid,
                    "host_tier": tier,
                    "isp_ack_version": ack,
                    "load": 0.1,
                    "endpoint_id": ep,
                }),
            )
            .await;
            last_hb = Instant::now();
        }

        let line = match tokio::time::timeout(Duration::from_millis(200), read_line(&mut reader)).await {
            Ok(Ok(Some(l))) => l,
            Ok(Ok(None)) => break,
            Ok(Err(e)) => {
                set_error(state, &e).await;
                break;
            }
            Err(_) => continue,
        };
        if line.is_empty() {
            break;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                set_error(state, &e.to_string()).await;
                break;
            }
        };
        if let Some(why) = gateway_reject(&transport, &msg) {
            end = SessionEnd::Rejected(why);
            break;
        }
        if handle_tunnel_msg(state, http, &mut writer, &msg).await {
            break;
        }
    }

    {
        let mut g = handle.close_tx.lock().await;
        *g = None;
    }
    drop(writer);
    end
}

/// A4.4: send Peer-side CLOSE frames queued by the pilot egress plane
/// (kill switch / budget / mismatch / refused pinned connect).
pub(crate) async fn flush_pilot_closes<W: AsyncWrite + Unpin>(
    state: &SharedState,
    writer: &mut W,
) -> Result<(), std::io::Error> {
    let Some(p) = state.read().await.egress.clone() else { return Ok(()) };
    let pending = p.lock().unwrap().take_pending_close();
    for (sid, reason) in pending {
        {
            let mut g = state.write().await;
            if let Some(st) = g.streams.get_mut(&sid) {
                st.closed = true;
                st.closed_by = Some(reason.clone());
            }
        }
        send_line(writer, &json!({"type": "CLOSE", "stream_id": sid, "reason": reason})).await?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auth_reject_only_terminal_for_iroh_local() {
        let m = json!({"type": "AUTH_REJECT", "error": "endpoint_mismatch"});
        assert_eq!(gateway_reject("iroh_local", &m).as_deref(), Some("endpoint_mismatch"));
        assert_eq!(gateway_reject("fake_relay", &m), None);
        assert_eq!(gateway_reject("iroh_loopback", &m), None);
        assert_eq!(gateway_reject("iroh_local", &json!({"type": "OPEN"})), None);
        let e = json!({"type": "ERR", "error": "endpoint_mismatch", "code": "endpoint_mismatch"});
        assert_eq!(gateway_reject("iroh_local", &e).as_deref(), Some("endpoint_mismatch"));
        assert_eq!(gateway_reject("iroh_local", &json!({"type": "ERR", "error": "x"})), None);
        assert_eq!(gateway_reject("fake_relay", &e), None);
    }
}
