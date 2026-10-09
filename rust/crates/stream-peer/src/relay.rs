//! Fake-relay / iroh-loopback dial -- newline JSON HELLO / AUTH_TICKET / OPEN / BYTES / CLOSE.
use crate::config;
use crate::frames::{mark_offline, set_error};
use crate::offline;
use crate::session::{self, SessionEnd};
use crate::state::{RelayHandle, SharedState};
use tokio::net::TcpStream;
use tokio::time::{sleep, Duration};

pub fn spawn_relay_loop(
    state: SharedState,
    handle: RelayHandle,
    http: reqwest::Client,
) {
    tokio::spawn(async move {
        relay_loop(state, handle, http).await;
    });
}

async fn relay_loop(state: SharedState, handle: RelayHandle, http: reqwest::Client) {
    let (transport, dial) = {
        let g = state.read().await;
        (g.cfg.transport.clone(), g.cfg.relay_dial.clone())
    };
    let (host, port) = match config::split_host_port(&dial) {
        Ok(hp) => hp,
        Err(e) => {
            set_error(&state, &e).await;
            return;
        }
    };
    if config::is_iroh_loopback(&transport) && !config::is_loopback_host(&host) {
        let msg = format!("iroh_loopback_refuses_non_loopback_dial:{host}");
        set_error(&state, &msg).await;
        eprintln!("A1.1 refuse dial {host:?} (loopback only)");
        return;
    }

    loop {
        let (ack, kill, peer_id) = {
            let g = state.read().await;
            (
                g.isp_ack_version.clone(),
                g.kill_requested,
                g.cfg.peer_id.clone(),
            )
        };
        if kill || ack.is_empty() {
            {
                let mut g = state.write().await;
                g.online = false;
                g.connected = false;
            }
            sleep(Duration::from_millis(300)).await;
            continue;
        }

        let stream = match TcpStream::connect((host.as_str(), port)).await {
            Ok(s) => s,
            Err(e) => {
                set_error(&state, &e.to_string()).await;
                mark_offline(&state).await;
                offline::system_offline(&state, offline::CONNECTION_LOST).await;
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        let (reader, writer) = stream.into_split();
        match session::run(&state, &handle, &http, reader, writer).await {
            SessionEnd::Handshake => {
                offline::system_offline(&state, offline::CONNECTION_LOST).await;
                sleep(Duration::from_secs(1)).await;
                continue;
            }
            SessionEnd::Ended | SessionEnd::Rejected(_) => {}
        }
        mark_offline(&state).await;
        offline::system_offline(&state, offline::CONNECTION_LOST).await;
        eprintln!("peer offline {peer_id}");
        sleep(Duration::from_millis(200)).await;
    }
}

pub use crate::frames::disconnect_now;
