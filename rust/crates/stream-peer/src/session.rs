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
    /// Session ran and ended (kill, clear-ack, close, EOF, ERR); why is in the reason.
    Ended(EndReason),
    /// iroh_local only: Gateway sent AUTH_REJECT (e.g. `endpoint_mismatch`); do not retry.
    Rejected(String),
}

/// Why a running session ended (logged at `session_end` as `end_reason=` / `closed_by=`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EndReason {
    Kill,
    ClearedAck,
    ForceDisconnect,
    ConsentWithdrawn,
    CloseRequested,
    WriteError,
    ReadError,
    RemoteClosed,
    EmptyLine,
    BadFrame,
    TunnelMsg(String),
}

#[cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
impl EndReason {
    pub fn label(&self) -> String {
        match self {
            EndReason::Kill => "kill".into(),
            EndReason::ClearedAck => "cleared_ack".into(),
            EndReason::ForceDisconnect => "force_disconnect".into(),
            EndReason::ConsentWithdrawn => "consent_withdrawn".into(),
            EndReason::CloseRequested => "close_requested".into(),
            EndReason::WriteError => "write_error".into(),
            EndReason::ReadError => "read_error".into(),
            EndReason::RemoteClosed => "remote_closed".into(),
            EndReason::EmptyLine => "empty_line".into(),
            EndReason::BadFrame => "bad_frame".into(),
            EndReason::TunnelMsg(t) => format!("tunnel_msg:{t}"),
        }
    }
    pub fn closed_by(&self) -> &'static str {
        match self {
            EndReason::Kill
            | EndReason::ClearedAck
            | EndReason::ForceDisconnect
            | EndReason::ConsentWithdrawn
            | EndReason::CloseRequested
            | EndReason::BadFrame => "local",
            EndReason::WriteError
            | EndReason::ReadError
            | EndReason::RemoteClosed
            | EndReason::EmptyLine
            | EndReason::TunnelMsg(_) => "remote",
        }
    }
}

#[cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
impl SessionEnd {
    /// (`end_reason`, `closed_by`) for the `session_end` log line.
    pub fn describe(&self) -> (String, &'static str) {
        match self {
            SessionEnd::Handshake => ("handshake".into(), "local"),
            SessionEnd::Rejected(why) => (format!("auth_reject:{why}"), "remote"),
            SessionEnd::Ended(r) => (r.label(), r.closed_by()),
        }
    }
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
        // A4.5 Gateway (ce80867): ERR {"error":"peer_id_in_use","code":"peer_id_in_use"}, then
        // QUIC close(1, "peer_id_in_use"). The frame usually arrives before the close.
        Some("ERR") if why == "peer_id_in_use" || msg.get("code").and_then(|v| v.as_str()) == Some("peer_id_in_use") => {
            return Some("peer_id_in_use".to_string());
        }
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

    // A4.4: pilot DATA/CLOSE frames from the egress tasks, written in order here.
    let (out_tx, mut out_rx) = tokio::sync::mpsc::channel::<Value>(64);
    let plane = state.read().await.egress.clone();
    if let Some(p) = &plane {
        p.lock().unwrap().outbound = Some(out_tx.clone());
    }
    let mut lines = crate::frames::LineReader::new(reader);
    let mut last_hb = Instant::now() - Duration::from_secs(3600);
    let hb = Duration::from_secs_f64(heartbeat_s.max(0.5));
    let end: SessionEnd;

    loop {
        {
            let g = state.read().await;
            let stop = if g.kill_requested {
                Some(EndReason::Kill)
            } else if g.isp_ack_version.is_empty() {
                Some(EndReason::ClearedAck)
            } else if g.force_disconnect {
                Some(EndReason::ForceDisconnect)
            } else if (iroh_local && !g.p2_consent) || (g.cfg.p9_required() && !g.p9_ack) {
                Some(EndReason::ConsentWithdrawn)
            } else {
                None
            };
            if let Some(r) = stop {
                end = SessionEnd::Ended(r);
                break;
            }
        }
        if close_rx.try_recv().is_ok() {
            end = SessionEnd::Ended(EndReason::CloseRequested);
            break;
        }
        if flush_pilot_closes(state, &mut writer).await.is_err() {
            end = SessionEnd::Ended(EndReason::WriteError);
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

        let line = tokio::select! {
            r = lines.next_line() => match r {
                Ok(Some(l)) => l,
                Ok(None) => {
                    end = SessionEnd::Ended(EndReason::RemoteClosed);
                    break;
                }
                Err(e) => {
                    set_error(state, &e).await;
                    end = SessionEnd::Ended(EndReason::ReadError);
                    break;
                }
            },
            Some(f) = out_rx.recv() => {
                if send_line(&mut writer, &f).await.is_err() {
                    end = SessionEnd::Ended(EndReason::WriteError);
                    break;
                }
                // Drain whatever else is queued (bulk DATA) before the next read.
                let mut gone = false;
                while let Ok(f) = out_rx.try_recv() {
                    if send_line(&mut writer, &f).await.is_err() {
                        gone = true;
                        break;
                    }
                }
                if gone {
                    end = SessionEnd::Ended(EndReason::WriteError);
                    break;
                }
                continue;
            }
            _ = tokio::time::sleep(Duration::from_millis(200)) => continue,
        };
        if line.is_empty() {
            end = SessionEnd::Ended(EndReason::EmptyLine);
            break;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                set_error(state, &e.to_string()).await;
                end = SessionEnd::Ended(EndReason::BadFrame);
                break;
            }
        };
        if let Some(why) = gateway_reject(&transport, &msg) {
            end = SessionEnd::Rejected(why);
            break;
        }
        if handle_tunnel_msg(state, http, &mut writer, &msg).await {
            let t = msg.get("type").and_then(|v| v.as_str()).unwrap_or("?");
            end = SessionEnd::Ended(EndReason::TunnelMsg(t.to_string()));
            break;
        }
    }

    {
        let mut g = handle.close_tx.lock().await;
        *g = None;
    }
    if let Some(p) = &plane {
        // Session gone: its streams cannot reach the Gateway any more.
        let mut p = p.lock().unwrap();
        p.outbound = None;
        p.close_all("connection_lost");
        let _ = p.take_pending_close();
    }
    drop(out_tx);
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
        send_line(writer, &crate::egress_state::close_frame(&sid, &reason)).await?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{EndReason, SessionEnd};

    #[test]
    fn end_reason_labels_and_side() {
        let cases = [
            (SessionEnd::Ended(EndReason::Kill), "kill", "local"),
            (SessionEnd::Ended(EndReason::ForceDisconnect), "force_disconnect", "local"),
            (SessionEnd::Ended(EndReason::CloseRequested), "close_requested", "local"),
            (SessionEnd::Ended(EndReason::BadFrame), "bad_frame", "local"),
            (SessionEnd::Ended(EndReason::ReadError), "read_error", "remote"),
            (SessionEnd::Ended(EndReason::RemoteClosed), "remote_closed", "remote"),
            (SessionEnd::Ended(EndReason::TunnelMsg("ERR".into())), "tunnel_msg:ERR", "remote"),
            (SessionEnd::Rejected("endpoint_mismatch".into()), "auth_reject:endpoint_mismatch", "remote"),
            (SessionEnd::Handshake, "handshake", "local"),
        ];
        for (e, label, side) in cases {
            assert_eq!(e.describe(), (label.to_string(), side), "{e:?}");
        }
    }

    use super::*;

    #[test]
    fn err_peer_id_in_use_wire_form_is_a_reject() {
        let m = json!({"type": "ERR", "error": "peer_id_in_use", "code": "peer_id_in_use"});
        assert_eq!(gateway_reject("iroh_pilot", &m).as_deref(), Some("peer_id_in_use"));
        let m = json!({"type": "ERR", "error": "x", "code": "peer_id_in_use"});
        assert_eq!(gateway_reject("iroh_pilot", &m).as_deref(), Some("peer_id_in_use"));
        assert_eq!(gateway_reject("iroh_pilot", &json!({"type": "ERR", "error": "other"})), None);
        assert_eq!(gateway_reject("fake_relay", &json!({"type": "ERR", "error": "peer_id_in_use"})), None);
    }

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
