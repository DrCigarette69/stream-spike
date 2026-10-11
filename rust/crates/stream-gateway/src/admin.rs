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
        .route("/gw/pilot", get(gw_pilot))
        .route("/gw/inject_bad_ticket", post(gw_inject_bad_ticket))
        .with_state(st)
}

async fn health(State(st): State<AppState>) -> Json<Value> {
    let peers: Vec<String> = {
        let p = st.peers.read().await;
        p.keys().cloned().collect()
    };
    #[allow(unused_mut)]
    let mut v = json!({
        "ok": true,
        "service": "gateway",
        "impl": "rust",
        "peers": peers,
        "alpn": ALPN,
        "transport": st.transport,
        "relay_listen": st.relay_listen,
        "gateway_endpoint_id": st.gateway_endpoint_id,
        "relay_url": st.relay_url,
        "hardening": HARDENING,
    });
    // A4.2: path evidence (datagrams via relay vs UDP) in iroh_pilot only.
    #[cfg(feature = "iroh")]
    if st.relay_url.is_some() {
        if let Some(c) = crate::iroh_local::health_counters() {
            v["iroh_path"] = c;
        }
    }
    if let Some(p) = &st.pilot {
        let s = p.status();
        v["public_egress_effective"] = json!(s.is_ok());
        v["egress_reason"] = json!(s.as_ref().err().map(|e| e.reason()));
        v["egress_detail"] = json!(s.as_ref().err().map(|e| e.detail.clone()));
    }
    Json(v)
}

/// A4.3: kill-switch state, allowlist version, open/closed pilot streams (metadata only).
async fn gw_pilot(State(st): State<AppState>) -> (StatusCode, Json<Value>) {
    match &st.pilot {
        Some(p) => (StatusCode::OK, Json(p.snapshot())),
        None => (StatusCode::NOT_FOUND, Json(json!({"error": "transport_not_pilot"}))),
    }
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
    // A4.3 (iroh_pilot): kill switch -> allowlist -> budget, before anything reaches the Peer.
    if let Some(p) = &st.pilot {
        if let Err(e) = p.check_open(&body.peer_id, &body.dest_host, body.dest_port) {
            let line = e.log_line();
            println!("{line} stream={}", body.stream_id);
            return (
                StatusCode::FORBIDDEN,
                Json(json!({ "error": e.reason(), "code": e.reason(), "detail": e.detail, "refused_at": "gateway" })),
            );
        }
    }

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

    // A4.3: register the DATA pipe before OPEN so an early Peer DATA/CLOSE is never lost.
    let mut pipe_addr = None;
    if let Some(p) = &st.pilot {
        let ep = st.peers.read().await.get(&body.peer_id).and_then(|m| m.auth_endpoint_id.clone());
        let Some(ep) = ep else {
            return (StatusCode::FORBIDDEN, Json(json!({ "error": "peer_offline", "code": "auth_ticket_failed" })));
        };
        let dest = format!("{}:{}", body.dest_host, body.dest_port);
        match crate::egress::start_pipe(st.clone(), p.clone(), body.peer_id.clone(), ep, body.stream_id.clone(), dest).await {
            Ok(a) => pipe_addr = Some(a),
            Err(e) => {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": "pipe_bind_failed", "detail": e.to_string() })))
            }
        }
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
        if let Some(p) = &st.pilot {
            // Peer refused (AUTH_REJECT): end the pipe without sending it a CLOSE.
            p.close_stream(&body.stream_id, (why.clone(), Some("open_refused".into()), true));
        }
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

    // Pilot streams carry real DATA (no synthetic BYTES meter); usage flushes from the pipe.
    if pipe_addr.is_none() {
        meter::spawn_meter(st.clone(), body.stream_id.clone(), body.peer_id.clone());
    }

    let mut out = json!({
        "started": true,
        "stream_id": body.stream_id,
        "denylist_version": chk.get("denylist_version"),
        "alpn": ALPN,
    });
    if let (Some(a), Some(p)) = (pipe_addr, &st.pilot) {
        out["pipe_addr"] = json!(a.to_string());
        out["allowlist_version"] = json!(p.version);
    }
    (StatusCode::OK, Json(out))
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
        let piped = st.pilot.as_ref().map(|p| p.streams.lock().unwrap().contains_key(&sid)).unwrap_or(false);
        if piped {
            // Pilot pipe sends CLOSE {reason: gw_stop} itself and logs the metadata line.
            st.pilot.as_ref().unwrap().close_stream(&sid, ("gw_stop".into(), None, false));
        } else {
            relay::close_stream_on_peer(&st, &pid, &sid).await;
        }
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
