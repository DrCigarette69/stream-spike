//! Stream Gateway (Rust) — Alpha-2 scaffold. Health + denylist stub; relay next.
use axum::{routing::get, Json, Router};
use serde_json::{json, Value};
use std::net::SocketAddr;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let listen = std::env::var("SPIKE_LISTEN_PROXY").unwrap_or_else(|_| "127.0.0.1:1080".into());
    let app = Router::new().route("/health", get(health));
    let addr: SocketAddr = listen.parse().expect("SPIKE_LISTEN_PROXY");
    tracing::info!("stream-gateway listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind gateway");
    axum::serve(listener, app).await.expect("serve");
}

async fn health() -> Json<Value> {
    Json(json!({ "ok": true, "impl": "rust", "component": "gateway" }))
}
