//! HTTP admin on SPIKE_LISTEN_PROXY — /health, /gw/*.

use axum::{
    extract::State,
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use stream_proto::{ALPN, HARDENING};

use crate::config::Config;
use crate::control;
use crate::meter;
use crate::relay;
use crate::state::{AppState, StreamMeta};

pub fn router(st: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/gw/peers", get(gw_peers))
        .route("/gw/start", post(gw_start))
        .route("/gw/stop", post(gw_stop))
        .route("/gw/inject_bad_ticket", post(gw_inject_bad_ticket))
        .with_state(st)
}

async fn health(State(st): State<AppState>) -> Json<Value> {
    let peers: Vec<String> = {
        let p = st.peers.read().await;
        p.keys().cloned().collect()
    };
    Json(json!({
        "ok": true,
        "service": "gateway",
        "impl": "rust",
        "peers": peers,
        "alpn": ALPN,
        "transport": st.transport,
        "relay_listen": st.relay_listen,
        "hardening": HARDENING,
    }))
}

async fn gw_peers(State(st): State<AppState>) -> Json<Value> {
    let peers = {
        let p = st.peers.read().await;
        p.iter()
            .map(|(k, v)| {
                json!({
                    "peer_id": k,
                    "endpoint_id": v.endpoint_id,
                    "isp_ack_version": v.isp_ack_version,
                    "host_tier": v.host_tier,
                })
            })
            .collect::<Vec<_>>()
    };
    Json(json!({ "peers": peers }))
}

#[derive(Debug, Deserialize)]
struct StartBody {
    #[serde(default = "default_dest_host")]
    dest_host: String,
    #[serde(default = "default_dest_port")]
    dest_port: u16,
    #[serde(default)]
    alpn: Option<String>,
    #[serde(default)]
    ticket_json: String,
    peer_id: String,
    stream_id: String,
    #[serde(default)]
    label: Option<String>,
}

fn default_dest_host() -> String {
    "echo.local".into()
}
fn default_dest_port() -> u16 {
    443
}

async fn gw_start(
    State(st): State<AppState>,
    Json(body): Json<StartBody>,
) -> (StatusCode, Json<Value>) {
    let (code, chk) = control::dest_check(&st, &body.dest_host, body.dest_port).await;
    if code != 200 || !chk.get("allowed").and_then(|v| v.as_bool()).unwrap_or(false) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "dest_denied",
                "code": "dest_denied",
                "detail": chk,
                "user_copy": "blocked",
                "denylist_version": chk.get("denylist_version"),
            })),
        );
    }

    let alpn = body.alpn.as_deref().unwrap_or(ALPN);
    if alpn != ALPN {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "bad_alpn", "code": "bad_alpn" })),
        );
    }

    let (vcode, vresp) = control::verify_ticket(&st, &body.ticket_json).await;
    if vcode != 200 || !vresp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": vresp.get("reason").and_then(|v| v.as_str()).unwrap_or("ticket_invalid"),
                "code": "auth_ticket_failed",
                "detail": vresp,
            })),
        );
    }

    let (ok, why) = relay::open_stream_to_peer(
        &st,
        &body.peer_id,
        &body.ticket_json,
        &body.stream_id,
        &body.dest_host,
        body.dest_port,
    )
    .await;
    if !ok {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": why, "code": "auth_ticket_failed" })),
        );
    }

    {
        let mut streams = st.streams.write().await;
        streams.insert(
            body.stream_id.clone(),
            StreamMeta {
                stop: false,
                peer_id: body.peer_id.clone(),
                label: body.label.clone(),
                dest_host: body.dest_host.clone(),
                dest_port: body.dest_port,
                denylist_version: chk.get("denylist_version").cloned(),
            },
        );
    }

    meter::spawn_meter(st.clone(), body.stream_id.clone(), body.peer_id.clone());

    (
        StatusCode::OK,
        Json(json!({
            "started": true,
            "stream_id": body.stream_id,
            "denylist_version": chk.get("denylist_version"),
            "alpn": ALPN,
        })),
    )
}

#[derive(Debug, Deserialize)]
struct StopBody {
    stream_id: Option<String>,
    peer_id: Option<String>,
}

async fn gw_stop(State(st): State<AppState>, Json(body): Json<StopBody>) -> Json<Value> {
    let stream_id = body.stream_id.clone();
    let peer_id = {
        let mut streams = st.streams.write().await;
        if let Some(ref sid) = stream_id {
            if let Some(s) = streams.get_mut(sid) {
                s.stop = true;
                Some(s.peer_id.clone())
            } else {
                body.peer_id.clone()
            }
        } else {
            body.peer_id.clone()
        }
    };
    if let (Some(pid), Some(sid)) = (peer_id, stream_id.clone()) {
        relay::close_stream_on_peer(&st, &pid, &sid).await;
    }
    Json(json!({ "stopped": stream_id }))
}

#[derive(Debug, Deserialize)]
struct InjectBody {
    #[serde(default = "default_peer")]
    peer_id: String,
    #[serde(default = "default_bad_ticket")]
    ticket_json: String,
    #[serde(default = "default_bad_stream")]
    stream_id: String,
}

fn default_peer() -> String {
    "peer_demo".into()
}
fn default_bad_ticket() -> String {
    r#"{"payload":{},"sig":"bad"}"#.into()
}
fn default_bad_stream() -> String {
    "str_bad".into()
}

async fn gw_inject_bad_ticket(
    State(st): State<AppState>,
    Json(body): Json<InjectBody>,
) -> (StatusCode, Json<Value>) {
    let (ok, why) = relay::open_stream_to_peer(
        &st,
        &body.peer_id,
        &body.ticket_json,
        &body.stream_id,
        "echo.local",
        443,
    )
    .await;
    if ok {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": "should_have_failed" })),
        );
    }
    (
        StatusCode::OK,
        Json(json!({ "rejected": true, "reason": why })),
    )
}

#[allow(dead_code)]
pub fn bind_addr(cfg: &Config) -> std::net::SocketAddr {
    cfg.proxy_listen
}
