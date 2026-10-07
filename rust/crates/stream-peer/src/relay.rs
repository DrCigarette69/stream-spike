//! Fake-relay / iroh-loopback dial -- newline JSON HELLO / AUTH_TICKET / OPEN / BYTES / CLOSE.
use crate::config;
use crate::frames::{handle_tunnel_msg, mark_offline, read_line, send_line, set_error};
use crate::state::{RelayHandle, SharedState};
use crate::ticket::control_post;
use serde_json::{json, Value};
use stream_proto::ALPN;
use tokio::io::BufReader;
use tokio::net::TcpStream;
use tokio::sync::oneshot;
use tokio::time::{sleep, Duration, Instant};

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
        let (ack, kill, tier, peer_id, endpoint_id, heartbeat_s, control_url) = {
            let g = state.read().await;
            (
                g.isp_ack_version.clone(),
                g.kill_requested,
                g.host_tier.clone(),
                g.cfg.peer_id.clone(),
                g.cfg.endpoint_id.clone(),
                g.cfg.heartbeat_s,
                g.cfg.control_url.clone(),
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
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        let (reader, mut writer) = stream.into_split();
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
            sleep(Duration::from_secs(1)).await;
            continue;
        }

        let resp = match read_line(&mut reader).await {
            Ok(Some(line)) => serde_json::from_str::<Value>(&line).unwrap_or(json!({})),
            _ => {
                set_error(&state, "HELLO read failed").await;
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        if resp.get("type").and_then(|v| v.as_str()) != Some("HELLO_OK") {
            set_error(&state, &format!("HELLO failed: {resp}")).await;
            eprintln!("peer HELLO failed: {resp}");
            sleep(Duration::from_secs(1)).await;
            continue;
        }
        if let Some(a) = resp.get("alpn").and_then(|v| v.as_str()) {
            if a != ALPN {
                set_error(&state, "bad HELLO_OK alpn").await;
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        }

        {
            let mut g = state.write().await;
            g.connected = true;
            g.online = true;
            g.last_error.clear();
            g.force_disconnect = false;
        }
        eprintln!(
            "peer online {peer_id} endpoint={endpoint_id} ack={ack}"
        );

        let (close_tx, mut close_rx) = oneshot::channel::<()>();
        {
            let mut g = handle.close_tx.lock().await;
            *g = Some(close_tx);
        }

        let mut last_hb = Instant::now() - Duration::from_secs(3600);
        let hb = Duration::from_secs_f64(heartbeat_s.max(0.5));

        loop {
            // kill / clear-ack / external close
            {
                let g = state.read().await;
                if g.kill_requested || g.isp_ack_version.is_empty() || g.force_disconnect {
                    break;
                }
            }
            if close_rx.try_recv().is_ok() {
                break;
            }

            if last_hb.elapsed() >= hb {
                let (pid, tier, ack, ep) = {
                    let g = state.read().await;
                    (
                        g.cfg.peer_id.clone(),
                        g.host_tier.clone(),
                        g.cfg.isp_ack_version.clone() if False else g.isp_ack_version.clone(),
                        g.cfg.endpoint_id.clone(),
                    )
                };
                let _ = control_post(
                    &http,
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

            let line = match tokio::time::timeout(Duration::from_millis(200), read_line(&mut reader)).await {
                Ok(Ok(Some(l))) => l,
                Ok(Ok(None)) => break,
                Ok(Err(e)) => {
                    set_error(&state, &e).await;
                    break;
                }
                Err(_) => continue,
            };
            if line.is_empty() {
                break;
            }
            let msg: Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(e) => {
                    set_error(&state, &e.to_string()).await;
                    break;
                }
            };
            if handle_tunnel_msg(&state, &http, &mut writer, &msg).await {
                    break;
                }
        }

        {
            let mut g = handle.close_tx.lock().await;
            *g = None;
        }
        drop(writer);
        mark_offline(&state).await;
        eprintln!("peer offline {peer_id}");
        sleep(Duration::from_millis(200)).await;
    }
}

pub use crate::frames::disconnect_now;
