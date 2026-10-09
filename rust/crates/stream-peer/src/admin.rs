//! Peer admin HTTP -- same routes/JSON as Python `AdminHandler`.
use crate::egress::egress_denied;
use crate::relay;
use crate::state::{RelayHandle, SharedState};
use crate::ticket::{control_post, verify_ticket};
use crate::ux::{self, p1_ux, p4_ux};
use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};

#[derive(Clone)]
pub struct AppState {
    pub state: SharedState,
    pub relay: RelayHandle,
    /// A3.2: active non-TCP transport (iroh_local); kill closes it <= 2 s.
    pub transport: crate::kill::TransportSlot,
    pub http: reqwest::Client,
}

pub fn router(app: AppState) -> Router {
    Router::new()
        .route("/health", get(health_or_status))
        .route("/peer/status", get(health_or_status))
        .route("/peer/consent/p1", get(consent_p1))
        .route("/peer/consent/p4", get(consent_p4))
        .route("/peer/consent/p2", get(crate::consent::consent_p2))
        .route("/peer/consent", post(crate::consent::peer_consent))
        .route("/peer/consent/p8", get(crate::consent_a4::consent_p8))
        .route("/peer/screen/p8", get(crate::consent_a4::consent_p8))
        .route("/peer/egress", get(crate::consent_a4::peer_egress))
        .route(
            "/peer/consent/p9",
            get(crate::consent_a4::consent_p9).post(crate::consent_a4::peer_consent_p9),
        )
        .route("/peer/ack", post(peer_ack))
        .route("/peer/kill", post(peer_kill))
        .route("/peer/resume", post(peer_resume))
        .route("/peer/host_tier", post(peer_host_tier))
        .route("/peer/egress_check", post(peer_egress_check))
        .route("/peer/verify_ticket", post(peer_verify_ticket))
        .with_state(app)
}

async fn health_or_status(State(app): State<AppState>) -> Json<Value> {
    let g = app.state.read().await;
    let mut out = g.snapshot();
    if let Some(obj) = out.as_object_mut() {
        obj.insert("ok".into(), json!(true));
        obj.insert("service".into(), json!("peer"));
        obj.insert("hardening".into(), json!(true));
    }
    Json(out)
}

async fn consent_p1() -> Json<Value> {
    let mut v = p1_ux();
    if let Some(obj) = v.as_object_mut() {
        obj.insert("ok".into(), json!(true));
    }
    Json(v)
}

async fn consent_p4() -> Json<Value> {
    let mut v = p4_ux();
    if let Some(obj) = v.as_object_mut() {
        obj.insert("ok".into(), json!(true));
    }
    Json(v)
}

async fn peer_ack(
    State(app): State<AppState>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let version = body
        .get("isp_ack_version")
        .or_else(|| body.get("version"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let understood = if version.is_empty() {
        body.get("understood").and_then(|v| v.as_bool())
    } else if body.get("understood").is_none() {
        // backward compat for peer_smoke -- treat missing as checked
        Some(true)
    } else {
        body.get("understood").and_then(|v| v.as_bool())
    };

    let ok = do_set_ack(&app, &version, understood).await;
    let snap = app.state.read().await.snapshot();
    if !ok {
        let mut out = snap;
        if let Some(obj) = out.as_object_mut() {
            obj.insert("error".into(), json!("checkbox_required"));
            obj.insert("code".into(), json!("p1_ack_gate"));
        }
        return (StatusCode::FORBIDDEN, Json(out));
    }
    (StatusCode::OK, Json(snap))
}

async fn do_set_ack(app: &AppState, version: &str, understood: Option<bool>) -> bool {
    if !version.is_empty() && understood == Some(false) {
        let mut g = app.state.write().await;
        let _ = g.set_ux(Some("P1"), ux::p1_user_facing());
        eprintln!("UX P1 (checkbox required)\n{}", ux::p1_user_facing());
        return false;
    }
    {
        let mut g = app.state.write().await;
        g.set_isp_ack(version);
    }
    if version.is_empty() {
        relay::disconnect_now(&app.state, &app.relay).await;
        let _ = app.transport.kill("isp_ack_cleared").await;
        let mut g = app.state.write().await;
        let _ = g.set_ux(Some("P1"), ux::p1_user_facing());
        eprintln!("UX P1\n{}", ux::p1_user_facing());
    } else {
        let mut g = app.state.write().await;
        if g.ux_screen.as_deref() == Some("P1") {
            g.ux_screen = None;
            g.ux_status.clear();
        }
    }
    eprintln!("peer isp_ack_version={version:?}");
    true
}

async fn peer_kill(State(app): State<AppState>) -> Json<Value> {
    {
        let mut g = app.state.write().await;
        g.kill_requested = true;
        crate::offline::clear(&mut g);
        for st in g.streams.values_mut() {
            if !st.closed {
                st.closed = true;
                st.closed_by = Some("peer_kill".into());
            }
        }
        let _ = g.set_ux(Some("P4"), ux::p4_user_facing());
    }
    let (peer_id, control_url) = {
        let g = app.state.read().await;
        (g.cfg.peer_id.clone(), g.cfg.control_url.clone())
    };
    let _ = control_post(
        &app.http,
        &control_url,
        "/v1/peers/kill",
        json!({ "peer_id": peer_id }),
    )
    .await;
    relay::disconnect_now(&app.state, &app.relay).await;
    let _ = app.transport.kill("peer_kill").await;
    eprintln!("UX P4\n{}", ux::p4_user_facing());
    crate::iroh_local::print_screen(crate::iroh_local::SCREEN_P4);
    eprintln!("peer kill switch fired");
    Json(app.state.read().await.snapshot())
}

async fn peer_resume(State(app): State<AppState>) -> Json<Value> {
    {
        let mut g = app.state.write().await;
        g.kill_requested = false;
        g.ux_screen = None;
        g.ux_status.clear();
        g.force_disconnect = false;
    }
    eprintln!("peer resume -- will reconnect if ack set");
    Json(app.state.read().await.snapshot())
}

async fn peer_host_tier(
    State(app): State<AppState>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let tier = body
        .get("host_tier")
        .and_then(|v| v.as_str())
        .unwrap_or("casual");
    if tier != "casual" && tier != "always_on" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "bad_tier" })),
        );
    }
    {
        let mut g = app.state.write().await;
        g.host_tier = tier.to_string();
    }
    (StatusCode::OK, Json(app.state.read().await.snapshot()))
}

async fn peer_egress_check(Json(body): Json<Value>) -> Json<Value> {
    let host = body
        .get("host")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let port = body
        .get("port")
        .and_then(|v| v.as_u64().or_else(|| v.as_i64().map(|i| i as u64)))
        .unwrap_or(0) as u16;
    let why = egress_denied(&host, port);
    Json(json!({
        "denied": why.is_some(),
        "reason": why,
    }))
}

async fn peer_verify_ticket(
    State(app): State<AppState>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let ticket_json = body
        .get("ticket_json")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let frame_alpn = body
        .get("alpn")
        .and_then(|v| v.as_str())
        .or(Some(stream_proto::ALPN));
    let cfg = app.state.read().await.cfg.clone();
    let ver = verify_ticket(&app.http, &cfg, ticket_json, frame_alpn).await;
    let code = if ver.ok {
        StatusCode::OK
    } else {
        StatusCode::FORBIDDEN
    };
    (
        code,
        Json(json!({
            "ok": ver.ok,
            "error": ver.error,
            "payload": ver.payload,
        })),
    )
}
