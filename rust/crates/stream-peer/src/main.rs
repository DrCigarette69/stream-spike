//! Stream Peer (Rust) -- Alpha-2 A2.1 parity with Python `peer/main.py`.
//! Stubs only: fake Relay / iroh loopback; no public egress.
mod a4_copy;
mod admin;
mod config;
mod consent_a4;
mod consent;
mod egress;
mod frames;
#[cfg(feature = "iroh_local")]
mod iroh_dial;
mod iroh_key;
mod iroh_local;
mod iroh_ticket;
mod kill;
mod offline;
mod relay;
mod session;
mod state;
mod ticket;
mod ux;

use admin::AppState;
use config::Config;
use relay::spawn_relay_loop;
use state::{PeerState, RelayHandle, SharedState};
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::RwLock;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cfg = Config::from_env();
    let addr: SocketAddr = match config::parse_admin_addr(&cfg.admin_listen) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("peer {e}");
            std::process::exit(2);
        }
    };
    if cfg.isp_ack_version.is_empty() {
        eprintln!("UX P1\n{}", ux::p1_user_facing());
    }

    let state: SharedState = Arc::new(RwLock::new(PeerState::new(cfg.clone())));
    let relay_handle = RelayHandle::default();
    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .expect("http client");

    let transport_slot = kill::TransportSlot::default();
    if config::is_iroh_pilot(&cfg.transport) {
        // A4: gates + P8/P9 copy only; the pilot dial is A4.4.
        iroh_local::spawn_iroh_pilot_refusal(state.clone());
    } else if config::is_iroh_local(&cfg.transport) {
        // A3.2: real iroh dial (part 2) or a clean refusal; admin stays up.
        iroh_local::spawn_iroh_local(
            state.clone(),
            transport_slot.clone(),
            http.clone(),
            relay_handle.clone(),
        );
    } else {
        spawn_relay_loop(state.clone(), relay_handle.clone(), http.clone());
    }

    let app = admin::router(AppState {
        state: state.clone(),
        relay: relay_handle,
        transport: transport_slot,
        http,
    });

    let ack = state.read().await.isp_ack_version.clone();
    eprintln!(
        "peer admin on {} transport={} dial={} ack={:?} hardening=1",
        addr, cfg.transport, cfg.relay_dial, ack
    );
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| panic!("bind peer admin {addr}: {e}"));
    axum::serve(listener, app).await.expect("serve");
}
