//! Thin Control HTTP client (CONTROL_URL).

use reqwest::StatusCode;
use serde_json::{json, Value};

use crate::state::AppState;

pub async fn http_json(
    st: &AppState,
    method: reqwest::Method,
    path: &str,
    body: Option<Value>,
) -> (u16, Value) {
    let url = format!("{}{}", st.control_url, path);
    let mut req = st.control.request(method.clone(), &url);
    if let Some(b) = body {
        req = req.json(&b);
    }
    match req.send().await {
        Ok(resp) => {
            let code = resp.status().as_u16();
            let v = resp.json::<Value>().await.unwrap_or_else(|_| json!({}));
            (code, v)
        }
        Err(e) => {
            // Mirror Python: network failure → 599
            let status = if e.is_timeout() {
                StatusCode::REQUEST_TIMEOUT.as_u16()
            } else {
                599
            };
            (status, json!({ "error": e.to_string() }))
        }
    }
}

pub async fn enroll_peer(
    st: &AppState,
    peer_id: &str,
    endpoint_id: &str,
    isp_ack_version: &str,
    host_tier: &str,
) {
    let _ = http_json(
        st,
        reqwest::Method::POST,
        "/v1/peers/enroll",
        Some(json!({
            "peer_id": peer_id,
            "endpoint_id": endpoint_id,
            "isp_ack_version": isp_ack_version,
            "host_tier": host_tier,
            "country": "US",
            "city": "new_orleans",
        })),
    )
    .await;
}

/// A3.4: Peer connection gone (iroh_local). Control ignores it if the peer re-enrolled with
/// another endpoint id meanwhile.
pub async fn peer_offline(st: &AppState, peer_id: &str, endpoint_id: &str, reason: &str) {
    let _ = http_json(
        st,
        reqwest::Method::POST,
        "/v1/peers/offline",
        Some(json!({ "peer_id": peer_id, "endpoint_id": endpoint_id, "reason": reason })),
    )
    .await;
}

pub async fn dest_check(st: &AppState, host: &str, port: u16) -> (u16, Value) {
    http_json(
        st,
        reqwest::Method::POST,
        "/v1/dest/check",
        Some(json!({ "host": host, "port": port })),
    )
    .await
}

pub async fn verify_ticket(st: &AppState, ticket_json: &str) -> (u16, Value) {
    http_json(
        st,
        reqwest::Method::POST,
        "/v1/tickets/verify",
        Some(json!({ "ticket_json": ticket_json })),
    )
    .await
}

pub async fn usage_flush(st: &AppState, body: Value) -> (u16, Value) {
    http_json(st, reqwest::Method::POST, "/v1/usage/flush", Some(body)).await
}

pub async fn wait_control_healthy(st: &AppState) {
    for _ in 0..50 {
        let (code, _) = http_json(st, reqwest::Method::GET, "/health", None).await;
        if code == 200 {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }
}
