//! A3.2 part 2: real `iroh_local` dial (cargo feature `iroh_local`).
//!
//! Endpoint: empty builder, `RelayMode::Disabled`, `clear_discovery()`, Peer
//! secret key from `iroh_key`, IPv4 bind guarded by `stream_proto::guard`
//! (`SPIKE_IROH_BIND`, default `127.0.0.1:0`), IPv6 pinned to `[::1]:0`.
//! Dial: `pre_dial` (P1+P2+not killed) -> `check_dial_target` -> connect to
//! `EndpointAddr(gateway id, direct addrs)` on ALPN `stream/tunnel/1` -> one
//! bi-stream -> `session::run` (same NDJSON as fake_relay). The connection is
//! registered in `TransportSlot` so kill / P2 withdrawal closes it <= 2 s.
//! Gateway `AUTH_REJECT` (e.g. `endpoint_mismatch`) -> log, close, no retry.
use crate::frames::{mark_offline, set_error};
use crate::iroh_key;
use crate::iroh_local::pre_dial;
use crate::iroh_ticket::{check_dial_target, DialTarget, GATEWAY_MISMATCH};
use crate::kill::{ActiveTransport, CloseFut, TransportSlot};
use crate::session::{self, SessionEnd};
use crate::state::{RelayHandle, SharedState};
use iroh::endpoint::{Connection, VarInt};
use iroh::{Endpoint, EndpointAddr, EndpointId, RelayMode, SecretKey, TransportAddr};
use serde_json::json;
use std::net::{IpAddr, Ipv6Addr, SocketAddr, SocketAddrV4, SocketAddrV6};
use std::str::FromStr;
use stream_proto::{guard, ALPN};
use tokio::time::{sleep, timeout, Duration};

pub const ENV_BIND: &str = "SPIKE_IROH_BIND";
pub const ENV_GATEWAY_ADDR: &str = "SPIKE_IROH_GATEWAY_ADDR";
pub const DEFAULT_BIND: &str = "127.0.0.1:0";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

pub fn endpoint_id_of(bytes: &[u8; 32]) -> EndpointId {
    SecretKey::from_bytes(bytes).public()
}

/// Guarded bind addresses: v4 from `bind` (must pass the A3.0 guard), v6 `[::1]:0`.
pub fn bind_addrs(bind: &str) -> Result<(SocketAddrV4, SocketAddrV6), String> {
    let sa: SocketAddr = bind
        .trim()
        .parse()
        .map_err(|_| guard::REASON_BAD_ADDR.to_string())?;
    let SocketAddr::V4(v4) = sa else {
        return Err(guard::REASON_BAD_ADDR.to_string());
    };
    guard::check_ip(IpAddr::V4(*v4.ip())).map_err(|e| {
        eprintln!("{}", e.log_line());
        e.reason().to_string()
    })?;
    Ok((v4, SocketAddrV6::new(Ipv6Addr::LOCALHOST, 0, 0, 0)))
}

/// The only builder the Peer uses: no relay, no discovery, private binds.
pub fn builder(secret: SecretKey, v4: SocketAddrV4, v6: SocketAddrV6) -> iroh::endpoint::Builder {
    Endpoint::empty_builder(RelayMode::Disabled)
        .clear_discovery()
        .secret_key(secret)
        .bind_addr_v4(v4)
        .bind_addr_v6(v6)
}

/// Bind and verify: no relay URL, no discovery service, only private sockets.
pub async fn bind_endpoint(secret: SecretKey, bind: &str) -> Result<Endpoint, String> {
    guard::check_relay_url(None).map_err(|e| e.reason().to_string())?;
    guard::check_discovery(false).map_err(|e| e.reason().to_string())?;
    let (v4, v6) = bind_addrs(bind)?;
    let ep = builder(secret, v4, v6)
        .bind()
        .await
        .map_err(|e| format!("iroh_bind_failed:{e}"))?;
    if ep.addr().relay_urls().next().is_some() {
        ep.close().await;
        return Err(guard::REASON_RELAY_REFUSED.into());
    }
    if !ep.discovery().is_empty() {
        ep.close().await;
        return Err(guard::REASON_DISCOVERY_REFUSED.into());
    }
    if let Some(bad) = ep.bound_sockets().into_iter().find(|s| !guard::is_private_addr(s.ip())) {
        ep.close().await;
        return Err(format!("{}:{bad}", guard::REASON_PUBLIC_ADDR));
    }
    Ok(ep)
}

pub async fn connect(ep: &Endpoint, target: &DialTarget) -> Result<Connection, String> {
    let id = EndpointId::from_str(&target.gateway_endpoint_id).map_err(|_| GATEWAY_MISMATCH.to_string())?;
    let addr = EndpointAddr::from_parts(id, target.direct_addrs.iter().map(|a| TransportAddr::Ip(*a)));
    match timeout(CONNECT_TIMEOUT, ep.connect(addr, ALPN.as_bytes())).await {
        Ok(Ok(c)) => Ok(c),
        Ok(Err(e)) => Err(format!("iroh_connect_failed:{e}")),
        Err(_) => Err("iroh_connect_timeout".into()),
    }
}

pub struct IrohConn(pub Connection);

impl ActiveTransport for IrohConn {
    fn close<'a>(&'a self, reason: &'a str) -> CloseFut<'a> {
        Box::pin(async move {
            self.0.close(VarInt::from_u32(0), reason.as_bytes());
            let _ = self.0.closed().await;
        })
    }
    fn label(&self) -> &str {
        "iroh_local"
    }
}

fn env_target_payload(gateway_id: &str) -> serde_json::Value {
    let addrs: Vec<String> = std::env::var(ENV_GATEWAY_ADDR)
        .unwrap_or_default()
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    json!({ "gateway_endpoint_id": gateway_id, "direct_addrs": addrs })
}

async fn refuse(state: &SharedState, reason: &str) {
    eprintln!("A3 iroh_local refused: {reason}");
    mark_offline(state).await;
    set_error(state, reason).await;
}

/// Startup from env, then the dial loop. Returns only on refusal / reject.
pub async fn run(state: SharedState, slot: TransportSlot, http: reqwest::Client, handle: RelayHandle) {
    let (key_path, gw) = {
        let g = state.read().await;
        (g.cfg.iroh_key_path.clone(), g.cfg.gateway_endpoint_id.clone())
    };
    let key = match iroh_key::load_or_create(std::path::Path::new(&key_path)) {
        Ok(k) => k,
        Err(e) => return refuse(&state, &e).await,
    };
    let secret = SecretKey::from_bytes(key.bytes());
    let id = endpoint_id_of(key.bytes());
    eprintln!("peer iroh_endpoint_id={id}");
    state.write().await.cfg.endpoint_id = id.to_string();
    let target = match check_dial_target(&env_target_payload(&gw), &gw) {
        Ok(t) => t,
        Err(e) => return refuse(&state, &e).await,
    };
    let bind = std::env::var(ENV_BIND).unwrap_or_else(|_| DEFAULT_BIND.into());
    let ep = match bind_endpoint(secret, &bind).await {
        Ok(ep) => ep,
        Err(e) => return refuse(&state, &e).await,
    };
    eprintln!("peer iroh_local bound {:?} relay=disabled discovery=off", ep.bound_sockets());
    dial_loop(&state, &slot, &http, &handle, &ep, &target).await;
    ep.close().await;
}

/// Returns `Some(reason)` when the Gateway rejected us (no retry).
pub async fn dial_loop(
    state: &SharedState,
    slot: &TransportSlot,
    http: &reqwest::Client,
    handle: &RelayHandle,
    ep: &Endpoint,
    target: &DialTarget,
) -> Option<String> {
    loop {
        let (ack, p2, killed, peer_id) = {
            let g = state.read().await;
            (g.isp_ack_version.clone(), g.p2_consent, g.kill_requested, g.cfg.peer_id.clone())
        };
        if crate::iroh_local::dial_gate(&ack, p2, killed).is_err() {
            mark_offline(state).await;
            sleep(Duration::from_millis(300)).await;
            continue;
        }
        let _ = pre_dial(&ack, p2, killed);
        let conn = match connect(ep, target).await {
            Ok(c) => c,
            Err(e) => {
                eprintln!("peer iroh_local dial failed: {e}");
                set_error(state, &e).await;
                mark_offline(state).await;
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        let (send, recv) = match timeout(CONNECT_TIMEOUT, conn.open_bi()).await {
            Ok(Ok(bi)) => bi,
            other => {
                let e = format!("iroh_open_bi_failed:{other:?}");
                set_error(state, &e).await;
                conn.close(VarInt::from_u32(0), b"open_bi_failed");
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        eprintln!("peer iroh_local connected gateway={} alpn={ALPN}", conn.remote_id());
        slot.set(Box::new(IrohConn(conn.clone()))).await;
        let end = session::run(state, handle, http, recv, send).await;
        let _ = slot.kill("session_end").await;
        conn.close(VarInt::from_u32(0), b"session_end");
        mark_offline(state).await;
        match end {
            SessionEnd::Rejected(why) => {
                eprintln!("peer iroh_local AUTH_REJECT {why}: closed, not retrying");
                set_error(state, &why).await;
                return Some(why);
            }
            SessionEnd::Handshake => sleep(Duration::from_secs(1)).await,
            SessionEnd::Ended => {
                eprintln!("peer offline {peer_id}");
                sleep(Duration::from_millis(200)).await;
            }
        }
    }
}

#[cfg(test)]
#[path = "iroh_dial_tests.rs"]
mod tests;
