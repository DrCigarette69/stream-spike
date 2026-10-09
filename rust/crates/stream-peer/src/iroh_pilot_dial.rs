//! A4.4 part 2: relay-only `iroh_pilot` dial (cargo feature `iroh_pilot`).
//!
//! Endpoint: empty builder with `RelayMode::Custom` holding exactly our one
//! relay (`SPIKE_RELAY_ALLOW_URL`, checked by `stream_proto::guard`),
//! `clear_discovery()`, `PathSelection::RelayOnly` (no hole punching), the
//! Peer key, guarded private binds (A3.2 `bind_addrs`). Relay TLS is always
//! verified (no skip-verify option is ever set; a unit test greps for it).
//! Dial: P1 -> P2 -> P9 gates -> `EndpointAddr(gateway id).with_relay_url`
//! (no direct addrs) -> one bi-stream -> `session::run`. A watcher on
//! `conn_type(gateway)` treats any direct / mixed path as a violation.
use crate::frames::{mark_offline, set_error};
use crate::iroh_dial::{bind_addrs, endpoint_id_of, IrohConn, CONNECT_TIMEOUT};
use crate::iroh_key;
use crate::iroh_local::pre_dial_a4;
use crate::kill::TransportSlot;
use crate::offline::{self, CONNECTION_LOST};
use crate::pilot_target::{check_pilot_target, env_payload, watch_path, PathKind, PilotTarget, RELAY_PATH_REQUIRED};
use crate::session::{self, SessionEnd};
use crate::state::{RelayHandle, SharedState};
use iroh::endpoint::{Connection, ConnectionType, PathSelection, VarInt};
use iroh::{Endpoint, EndpointAddr, EndpointId, RelayMap, RelayMode, RelayUrl, SecretKey, Watcher};
use std::net::{SocketAddrV4, SocketAddrV6};
use std::str::FromStr;
use stream_proto::guard::{self, RelayAllow};
use stream_proto::ALPN;
use tokio::time::{sleep, timeout, Duration};

/// The single relay URL the endpoint may use (canonical form of the allow URL).
pub fn relay_url_of(allow: &RelayAllow) -> Result<RelayUrl, String> {
    RelayUrl::from_str(&allow.url()).map_err(|_| guard::REASON_RELAY_CONFIG.to_string())
}

/// The only builder the pilot Peer uses: one relay, no discovery, RelayOnly.
pub fn pilot_builder(secret: SecretKey, relay: RelayUrl, v4: SocketAddrV4, v6: SocketAddrV6) -> iroh::endpoint::Builder {
    Endpoint::empty_builder(RelayMode::Custom(RelayMap::from(relay)))
        .clear_discovery()
        .path_selection(PathSelection::RelayOnly)
        .secret_key(secret)
        .bind_addr_v4(v4)
        .bind_addr_v6(v6)
}

pub async fn bind_pilot(secret: SecretKey, allow: &RelayAllow, bind: &str) -> Result<Endpoint, String> {
    guard::check_discovery(false).map_err(|e| e.reason().to_string())?;
    let relay = relay_url_of(allow)?;
    guard::check_relay_url_with(Some(relay.as_str()), Some(allow)).map_err(|e| e.log_line())?;
    let (v4, v6) = bind_addrs(bind)?;
    let ep = pilot_builder(secret, relay, v4, v6).bind().await.map_err(|e| format!("iroh_bind_failed:{e}"))?;
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

pub fn path_kind(ct: &ConnectionType) -> PathKind {
    match ct {
        ConnectionType::Relay(_) => PathKind::Relay,
        ConnectionType::Direct(a) | ConnectionType::Mixed(a, _) => PathKind::Direct(a.to_string()),
        ConnectionType::None => PathKind::None,
    }
}

pub async fn connect(ep: &Endpoint, t: &PilotTarget) -> Result<Connection, String> {
    let id = EndpointId::from_str(&t.gateway_endpoint_id).map_err(|_| crate::iroh_ticket::GATEWAY_MISMATCH.to_string())?;
    let relay = RelayUrl::from_str(&t.relay_url).map_err(|_| guard::REASON_RELAY_CONFIG.to_string())?;
    let addr = EndpointAddr::new(id).with_relay_url(relay);
    match timeout(CONNECT_TIMEOUT * 2, ep.connect(addr, ALPN.as_bytes())).await {
        Ok(Ok(c)) => Ok(c),
        Ok(Err(e)) => Err(format!("iroh_connect_failed:{e}")),
        Err(_) => Err("iroh_connect_timeout".into()),
    }
}

async fn refuse(state: &SharedState, reason: &str) {
    eprintln!("A4 iroh_pilot refused: {reason}");
    mark_offline(state).await;
    set_error(state, reason).await;
}

pub async fn run(state: SharedState, slot: TransportSlot, http: reqwest::Client, handle: RelayHandle, allow: RelayAllow) {
    let (key_path, gw) = {
        let g = state.read().await;
        (g.cfg.iroh_key_path.clone(), g.cfg.gateway_endpoint_id.clone())
    };
    let key = match iroh_key::load_or_create(std::path::Path::new(&key_path)) {
        Ok(k) => k,
        Err(e) => return refuse(&state, &e).await,
    };
    let id = endpoint_id_of(key.bytes());
    eprintln!("peer iroh_endpoint_id={id}");
    state.write().await.cfg.endpoint_id = id.to_string();
    let cidrs = match guard::allowlist_from_env() {
        Ok(c) => c,
        Err(e) => return refuse(&state, &e.log_line()).await,
    };
    let target = match check_pilot_target(&env_payload(&gw, &allow), &gw, &allow, cidrs.as_deref()) {
        Ok(t) => t,
        Err(e) => return refuse(&state, &e).await,
    };
    let bind = std::env::var(crate::iroh_dial::ENV_BIND).unwrap_or_else(|_| crate::iroh_dial::DEFAULT_BIND.into());
    let ep = match bind_pilot(SecretKey::from_bytes(key.bytes()), &allow, &bind).await {
        Ok(ep) => ep,
        Err(e) => return refuse(&state, &e).await,
    };
    eprintln!("peer iroh_pilot bound {:?} relay={} path=relay_only discovery=off", ep.bound_sockets(), target.relay_url);
    dial_loop(&state, &slot, &http, &handle, &ep, &target).await;
    ep.close().await;
}

/// Returns the terminal reason (Gateway reject or relay path violation); no retry after it.
pub async fn dial_loop(
    state: &SharedState,
    slot: &TransportSlot,
    http: &reqwest::Client,
    handle: &RelayHandle,
    ep: &Endpoint,
    target: &PilotTarget,
) -> Option<String> {
    let gw_id = EndpointId::from_str(&target.gateway_endpoint_id).ok()?;
    loop {
        let (ack, p2, p9_req, p9, killed) = {
            let g = state.read().await;
            (g.isp_ack_version.clone(), g.p2_consent, g.cfg.p9_required(), g.p9_ack, g.kill_requested)
        };
        if crate::iroh_local::dial_gate_a4(&ack, p2, p9_req, p9, killed).is_err() {
            mark_offline(state).await;
            sleep(Duration::from_millis(300)).await;
            continue;
        }
        let _ = pre_dial_a4(&ack, p2, p9_req, p9, killed);
        let conn = match connect(ep, target).await {
            Ok(c) => c,
            Err(e) => {
                eprintln!("peer iroh_pilot dial failed: {e}");
                set_error(state, &e).await;
                mark_offline(state).await;
                offline::system_offline(state, CONNECTION_LOST).await;
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        let (send, recv) = match timeout(CONNECT_TIMEOUT, conn.open_bi()).await {
            Ok(Ok(bi)) => bi,
            other => {
                set_error(state, &format!("iroh_open_bi_failed:{other:?}")).await;
                conn.close(VarInt::from_u32(0), b"open_bi_failed");
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        eprintln!("peer iroh_pilot connected gateway={} alpn={ALPN} via={}", conn.remote_id(), target.relay_url);
        slot.set(Box::new(IrohConn(conn.clone()))).await;
        let mut ct = ep.conn_type(gw_id);
        let get = move || ct.as_mut().map(|w| path_kind(&w.get())).unwrap_or(PathKind::None);
        let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
        let watcher = watch_path(state, slot, get, async move {
            let _ = stop_rx.await;
        });
        let session = session::run(state, handle, http, recv, send);
        tokio::pin!(watcher);
        tokio::pin!(session);
        let end = tokio::select! {
            v = &mut watcher => {
                let _ = (&mut session).await; // transport killed: session ends promptly
                if v.is_some() { Some(RELAY_PATH_REQUIRED.to_string()) } else { None }
            }
            e = &mut session => {
                let _ = stop_tx.send(());
                match e {
                    SessionEnd::Rejected(why) => Some(why),
                    SessionEnd::Handshake | SessionEnd::Ended => None,
                }
            }
        };
        let _ = slot.kill("session_end").await;
        conn.close(VarInt::from_u32(0), b"session_end");
        mark_offline(state).await;
        match end {
            Some(why) if why == RELAY_PATH_REQUIRED => {
                eprintln!("peer iroh_pilot left relay path: closed, not retrying");
                set_error(state, RELAY_PATH_REQUIRED).await;
                return Some(why);
            }
            Some(why) => {
                eprintln!("peer iroh_pilot AUTH_REJECT {why}: closed, not retrying");
                set_error(state, &why).await;
                offline::system_offline(state, &why).await;
                return Some(why);
            }
            None => {
                offline::system_offline(state, CONNECTION_LOST).await;
                sleep(Duration::from_secs(1)).await;
            }
        }
    }
}

#[cfg(test)]
#[path = "iroh_pilot_dial_tests.rs"]
mod tests;
