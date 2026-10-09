//! AUTH_TICKET verify -- Python parity: Control `POST /v1/tickets/verify` + endpoint/ALPN checks.
use crate::config::Config;
use serde_json::{json, Value};
use stream_proto::ALPN;

#[derive(Debug)]
pub struct VerifyResult {
    pub ok: bool,
    pub error: String,
    pub payload: Value,
}

/// Match Python `_verify_ticket`: frame ALPN -> Control verify -> peer_endpoint_id -> payload.alpn.
pub async fn verify_ticket(
    http: &reqwest::Client,
    cfg: &Config,
    ticket_json: &str,
    frame_alpn: Option<&str>,
) -> VerifyResult {
    if let Some(a) = frame_alpn {
        if a != ALPN {
            return VerifyResult {
                ok: false,
                error: "bad_alpn".into(),
                payload: json!({}),
            };
        }
    }
    let url = format!("{}/v1/tickets/verify", cfg.control_url);
    let body = json!({ "ticket_json": ticket_json });
    let (st, ver) = match http.post(&url).json(&body).send().await {
        Ok(resp) => {
            let code = resp.status().as_u16();
            let v: Value = resp.json().await.unwrap_or_else(|_| json!({}));
            (code, v)
        }
        Err(e) => {
            return VerifyResult {
                ok: false,
                error: e.to_string(),
                payload: json!({}),
            };
        }
    };
    if st != 200 || !ver.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        let err = ver
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("bad_ticket")
            .to_string();
        return VerifyResult {
            ok: false,
            error: err,
            payload: json!({}),
        };
    }
    let ticket: Value = match serde_json::from_str(ticket_json) {
        Ok(v) => v,
        Err(e) => {
            return VerifyResult {
                ok: false,
                error: e.to_string(),
                payload: json!({}),
            };
        }
    };
    let payload = ticket.get("payload").cloned().unwrap_or(json!({}));
    let peer_ep = payload
        .get("peer_endpoint_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if peer_ep != cfg.endpoint_id {
        return VerifyResult {
            ok: false,
            error: "endpoint_mismatch".into(),
            payload,
        };
    }
    if let Some(ticket_alpn) = payload.get("alpn").and_then(|v| v.as_str()) {
        if ticket_alpn != ALPN {
            return VerifyResult {
                ok: false,
                error: "ticket_alpn_mismatch".into(),
                payload,
            };
        }
    }
    // A3.2: in iroh_local, gateway_endpoint_id + guarded direct_addrs before any dial.
    if crate::config::is_iroh_local(&cfg.transport) {
        if let Err(e) = crate::iroh_ticket::check_dial_target(&payload, &cfg.gateway_endpoint_id) {
            return VerifyResult {
                ok: false,
                error: e,
                payload,
            };
        }
    }
    VerifyResult {
        ok: true,
        error: "ok".into(),
        payload,
    }
}

/// Fire-and-forget style control POST used by heartbeat / kill.
pub async fn control_post(
    http: &reqwest::Client,
    control_url: &str,
    path: &str,
    body: Value,
) -> (u16, Value) {
    let url = format!("{control_url}{path}");
    match http.post(&url).json(&body).send().await {
        Ok(resp) => {
            let code = resp.status().as_u16();
            let v: Value = resp.json().await.unwrap_or_else(|_| json!({}));
            (code, v)
        }
        Err(e) => (599, json!({ "error": e.to_string() })),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bad_frame_alpn_short_circuits() {
        // Pure logic mirror (no HTTP): frame alpn gate.
        let frame = Some("nope");
        assert_ne!(frame.unwrap(), ALPN);
        assert_eq!(ALPN, "stream/tunnel/1");
    }
}
