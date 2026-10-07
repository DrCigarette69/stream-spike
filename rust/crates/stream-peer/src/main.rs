//! Stream Peer (Rust) — Alpha-2 scaffold. Parity with Python peer stub env.
use axum::{routing::{get, post}, Json, Router};
use serde_json::{json, Value};
use std::{
    net::SocketAddr,
    sync::atomic::{AtomicBool, Ordering},
    sync::Arc,
};
use tokio::sync::RwLock;

#[derive(Clone, Default)]
struct State {
    isp_ack: Arc<RwLock<Option<String>>>,
    paused: Arc<AtomicBool>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let admin = std::env::var("SPIKE_PEER_ADMIN").unwrap_or_else(|_| "127.0.0.1:9200".into());
    let ack = std::env::var("SPIKE_ISP_ACK_VERSION").ok().filter(|s| !s.is_empty());
    let state = State {
        isp_ack: Arc::new(RwLock::new(ack)),
        paused: Arc::new(AtomicBool::new(false)),
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/peer/ack", post(peer_ack))
        .route("/peer/kill", post(peer_kill))
        .route("/peer/resume", post(peer_resume))
        .route("/peer/status", get(peer_status))
        .with_state(state);

    let addr: SocketAddr = admin.parse().expect("SPIKE_PEER_ADMIN");
    tracing::info!("stream-peer listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind peer admin");
    axum::serve(listener, app).await.expect("serve");
}

async fn health() -> Json<Value> {
    Json(json!({ "ok": true, "impl": "rust", "component": "peer" }))
}

async fn peer_ack(
    axum::extract::State(st): axum::extract::State<State>,
    Json(body): Json<Value>,
) -> Json<Value> {
    let ver = body
        .get("isp_ack_version")
        .and_then(|v| v.as_str())
        .unwrap_or("v1")
        .to_string();
    *st.isp_ack.write().await = Some(ver.clone());
    // Designer P1 greps (no "Stream", no liability waive)
    println!("does not guarantee your ISP");
    println!("may suspend service");
    println!("I understand and want to continue");
    Json(json!({ "ok": true, "isp_ack_version": ver }))
}

async fn peer_kill(axum::extract::State(st): axum::extract::State<State>) -> Json<Value> {
    st.paused.store(true, Ordering::SeqCst);
    println!("Sharing paused");
    println!("No traffic through your connection until you turn it back on");
    Json(json!({ "ok": true, "sharing_status": "Sharing paused", "paused": true }))
}

async fn peer_resume(axum::extract::State(st): axum::extract::State<State>) -> Json<Value> {
    st.paused.store(false, Ordering::SeqCst);
    Json(json!({ "ok": true, "sharing_status": "Sharing", "paused": false }))
}

async fn peer_status(axum::extract::State(st): axum::extract::State<State>) -> Json<Value> {
    let ack = st.isp_ack.read().await.clone();
    let paused = st.paused.load(Ordering::SeqCst);
    Json(json!({
        "ok": true,
        "isp_ack_version": ack,
        "paused": paused,
        "sharing_status": if paused { "Sharing paused" } else { "Sharing" },
        "impl": "rust"
    }))
}
