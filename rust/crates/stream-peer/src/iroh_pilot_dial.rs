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
use crate::relay_path::{counters_line, require_counters, retry_after, with_test_hook, UdpCounterSource, UdpCounts};
use crate::pilot_target::{check_pilot_target, env_payload, watch_path, PathKind, PilotTarget, RELAY_PATH_REQUIRED};
use crate::session::{self, SessionEnd};
use crate::state::{RelayHandle, SharedState};
use iroh::endpoint::{Connection, ConnectionType, PathSelection, VarInt};
use iroh::{Endpoint, EndpointAddr, EndpointId, RelayConfig, RelayMap, RelayMode, RelayUrl, SecretKey, Watcher};
use std::net::{SocketAddrV4, SocketAddrV6};
use std::str::FromStr;
use stream_proto::guard::{self, RelayAllow};
use stream_proto::ALPN;
use tokio::time::{sleep, timeout, Duration};

/// The single relay URL the endpoint may use (canonical form of the allow URL).
pub fn relay_url_of(allow: &RelayAllow) -> Result<RelayUrl, String> {
    RelayUrl::from_str(&allow.url()).map_err(|_| guard::REASON_RELAY_CONFIG.to_string())
}

/// Our one relay, QUIC address discovery off (`quic: None`): relay-only never needs a
/// public UDP address, so the endpoint has no reason to send a single UDP datagram.
pub fn pilot_relay_map(relay: RelayUrl) -> RelayMap {
    RelayMap::from(RelayConfig { url: relay, quic: None })
}

/// The only builder the pilot Peer uses: one relay (no QAD), no discovery, RelayOnly.
pub fn pilot_builder(secret: SecretKey, relay: RelayUrl, v4: SocketAddrV4, v6: SocketAddrV6) -> iroh::endpoint::Builder {
    Endpoint::empty_builder(RelayMode::Custom(pilot_relay_map(relay)))
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

/// Endpoint UDP counters. iroh 0.95.1 exposes them only through `Endpoint::metrics()`
/// (iroh cargo feature `metrics`, enabled by stream-peer's `iroh_pilot`), endpoint-wide since
/// bind, so a relay-only endpoint must read 0 everywhere:
///   send = `send_ipv4` / `send_ipv6` (bytes, QUIC + disco over UDP);
///   recv = `recv_data_ipv4` / `recv_data_ipv6` (bytes, QUIC only) + `recv_disco_udp`
///   (count of inbound disco packets that arrived over UDP; iroh does not split it by family,
///   so it is folded into `recv_v4` and the logged line keeps its exact format).
/// Disco over the relay (`recv_disco_relay`) is not UDP and is not counted.
pub struct MagicsockUdp(Endpoint);
impl UdpCounterSource for MagicsockUdp {
    fn read(&self) -> Option<UdpCounts> {
        let m = &self.0.metrics().magicsock;
        Some(fold_magicsock(
            m.send_ipv4.get(),
            m.send_ipv6.get(),
            m.recv_data_ipv4.get(),
            m.recv_data_ipv6.get(),
            m.recv_disco_udp.get(),
        ))
    }
}

/// Pure fold of the magicsock counters (see `MagicsockUdp`); inbound UDP disco -> `recv_v4`.
pub fn fold_magicsock(send4: u64, send6: u64, recv_data4: u64, recv_data6: u64, recv_disco_udp: u64) -> UdpCounts {
    UdpCounts {
        send_v4: send4,
        send_v6: send6,
        recv_v4: recv_data4.saturating_add(recv_disco_udp),
        recv_v6: recv_data6,
    }
}

pub fn udp_source(ep: &Endpoint) -> Box<dyn UdpCounterSource> {
    with_test_hook(Box::new(MagicsockUdp(ep.clone())))
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
    let peer_id_src = {
        let mut g = state.write().await;
        g.cfg.endpoint_id = id.to_string();
        let explicit = g.cfg.peer_id_from_env.then(|| g.cfg.peer_id.clone());
        match crate::config::pilot_peer_id(explicit.as_deref(), &id.to_string()) {
            // Hello, heartbeat, kill and ack all read cfg.peer_id, so they stay consistent.
            Ok((pid, src)) => {
                g.cfg.peer_id = pid;
                src
            }
            Err(e) => {
                drop(g);
                return refuse(&state, &e).await;
            }
        }
    };
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
    eprintln!("peer iroh_pilot bound {:?} relay={} path=relay_only discovery=off quic=none", ep.bound_sockets(), target.relay_url);
    eprintln!("peer iroh_pilot peer_id={} source={peer_id_src}", state.read().await.cfg.peer_id);
    let udp = udp_source(&ep);
    eprintln!("peer iroh_pilot {}", counters_line(udp.as_ref()));
    if let Err(e) = require_counters(udp.as_ref(), guard::Lane::build()) {
        ep.close().await;
        refuse(&state, &e).await;
        offline::system_offline(&state, RELAY_PATH_REQUIRED).await;
        return;
    }
    dial_loop(&state, &slot, &http, &handle, &ep, &target, udp.as_ref()).await;
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
    udp: &dyn UdpCounterSource,
) -> Option<String> {
    let gw_id = EndpointId::from_str(&target.gateway_endpoint_id).ok()?;
    let mut backoff = crate::redial::Backoff::from_env();
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
                redial_wait(&mut backoff, "dial_failed").await;
                continue;
            }
        };
        let (send, recv) = match timeout(CONNECT_TIMEOUT, conn.open_bi()).await {
            Ok(Ok(bi)) => bi,
            other => {
                set_error(state, &format!("iroh_open_bi_failed:{other:?}")).await;
                conn.close(VarInt::from_u32(0), b"open_bi_failed");
                redial_wait(&mut backoff, "open_bi_failed").await;
                continue;
            }
        };
        eprintln!("peer iroh_pilot connected gateway={} alpn={ALPN} via={}", conn.remote_id(), target.relay_url);
        eprintln!("peer iroh_pilot path {}", counters_line(udp));
        slot.set(Box::new(IrohConn(conn.clone()))).await;
        let up_since = std::time::Instant::now();
        live_set(conn.clone(), ep.clone(), up_since);
        let mut ct = ep.conn_type(gw_id);
        let get = move || ct.as_mut().map(|w| path_kind(&w.get())).unwrap_or(PathKind::None);
        let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
        let watcher = watch_path(state, slot, get, udp, async move {
            let _ = stop_rx.await;
        });
        let session = session::run(state, handle, http, recv, send);
        tokio::pin!(watcher);
        tokio::pin!(session);
        let end_desc: Option<(String, &'static str)>;
        let end = tokio::select! {
            v = &mut watcher => {
                let _ = (&mut session).await; // transport killed: session ends promptly
                end_desc = Some(if v.is_some() { ("left_relay".to_string(), "local") } else { ("path_watch_ended".to_string(), "local") });
                if v.is_some() { Some(RELAY_PATH_REQUIRED.to_string()) } else { None }
            }
            e = &mut session => {
                let _ = stop_tx.send(());
                end_desc = Some(e.describe());
                match e {
                    SessionEnd::Rejected(why) => Some(why),
                    SessionEnd::Handshake | SessionEnd::Ended(_) => None,
                }
            }
        };
        if live_take().is_none() {
            // Shutdown already logged session_end for this session and is closing it.
            return None;
        }
        let (reason, by) = end_desc.unwrap_or(("unknown".to_string(), "local"));
        let up = up_since.elapsed();
        backoff.session_ended(up);
        let close_p = conn.close_reason().map(|e| close_parts(&e));
        // A refusal can arrive as AUTH_REJECT or as the Gateway's QUIC close reason.
        let capped = cap_refusal(end.as_deref(), close_p.as_ref().map(|(_, t)| t.as_str()));
        let (reason, by) = match capped {
            Some(code) => (format!("auth_reject:{code}"), "remote"),
            None => (reason, by),
        };
        let close = close_suffix(close_p);
        eprintln!("peer iroh_pilot {}", session_end_line(&reason, by, close.as_deref(), up.as_millis() as u64, &counters_line(udp)));
        let _ = slot.kill("session_end").await;
        conn.close(VarInt::from_u32(0), b"session_end");
        mark_offline(state).await;
        if let Some(code) = capped {
            // P8 rule: no auto-retry after peer_id_in_use / endpoint_mismatch; back off to the cap.
            eprintln!("peer iroh_pilot AUTH_REJECT {code}: closed, backing off to cap");
            set_error(state, code).await;
            offline::system_offline(state, code).await;
            let reset = backoff.take_reset();
            let (attempt, ms) = backoff.jump_to_cap();
            eprintln!("peer iroh_pilot {}", redial_line(attempt, ms, reset, &reason));
            sleep(Duration::from_millis(ms)).await;
            continue;
        }
        debug_assert_eq!(retry_after(end.as_deref()), end.is_none());
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
                if user_stop_reason(&reason) {
                    // P4 kill / force_disconnect / P1 cleared / consent withdrawn: no redial; the
                    // dial gate at the loop top holds until the user turns sharing back on.
                    continue;
                }
                redial_wait(&mut backoff, &reason).await;
            }
        }
    }
}

/// The live Gateway session, for the SIGTERM/SIGINT shutdown hook (`shutdown_session`).
struct Live {
    conn: Connection,
    ep: Endpoint,
    since: std::time::Instant,
}

static LIVE: std::sync::Mutex<Option<Live>> = std::sync::Mutex::new(None);

fn live_set(conn: Connection, ep: Endpoint, since: std::time::Instant) {
    *LIVE.lock().unwrap() = Some(Live { conn, ep, since });
}

/// Exactly one of the session loop or the shutdown hook takes (and logs) a session.
fn live_take() -> Option<Live> {
    LIVE.lock().unwrap().take()
}

/// `session_end` line for a local shutdown.
pub(crate) fn shutdown_line(up_ms: u64, counters: &str) -> String {
    session_end_line("shutdown", "local", None, up_ms, counters)
}

/// SIGTERM/SIGINT: if a session is up, log its `session_end end_reason=shutdown closed_by=local`,
/// close it with reason `shutdown` and give the close up to 1 s to go out. No-op otherwise.
pub async fn shutdown_session() {
    let Some(l) = live_take() else { return };
    let up_ms = l.since.elapsed().as_millis() as u64;
    let udp = udp_source(&l.ep);
    eprintln!("peer iroh_pilot {}", shutdown_line(up_ms, &counters_line(udp.as_ref())));
    l.conn.close(VarInt::from_u32(0), b"shutdown");
    let _ = timeout(Duration::from_secs(1), l.ep.close()).await;
}

/// Gateway refusals that back off straight to the cap (P8 keys of the same name).
pub(crate) const CAP_REFUSALS: [&str; 2] = ["peer_id_in_use", "endpoint_mismatch"];

/// The cap refusal named by an AUTH_REJECT reason or the QUIC close reason, if any.
pub(crate) fn cap_refusal(reject: Option<&str>, close_reason: Option<&str>) -> Option<&'static str> {
    [reject, close_reason]
        .into_iter()
        .flatten()
        .find_map(|r| CAP_REFUSALS.into_iter().find(|c| r.trim() == *c))
}

/// User-initiated stops keep their no-redial behaviour (no backoff, gate holds).
pub(crate) fn user_stop_reason(reason: &str) -> bool {
    matches!(reason, "kill" | "force_disconnect" | "cleared_ack" | "consent_withdrawn")
}

/// Every other end: back off (never an immediate redial), logging attempt + redial_in_ms.
async fn redial_wait(backoff: &mut crate::redial::Backoff, after: &str) {
    let reset = backoff.take_reset();
    let (attempt, ms) = backoff.next_delay();
    eprintln!("peer iroh_pilot {}", redial_line(attempt, ms, reset, after));
    sleep(Duration::from_millis(ms)).await;
}

/// (code, reason) for an application close, else (None, debug text) for other errors.
fn close_parts(e: &iroh::endpoint::ConnectionError) -> (Option<u64>, String) {
    match e {
        iroh::endpoint::ConnectionError::ApplicationClosed(a) => {
            (Some(a.error_code.into_inner()), String::from_utf8_lossy(&a.reason).into_owned())
        }
        other => (None, format!("{other}")),
    }
}

/// ` close_code=N close_reason=…` or ` close_error=…` (spaces in text become `_`), None if open.
pub(crate) fn close_suffix(parts: Option<(Option<u64>, String)>) -> Option<String> {
    let (code, text) = parts?;
    let text = if text.is_empty() { "-".to_string() } else { text.split_whitespace().collect::<Vec<_>>().join("_") };
    Some(match code {
        Some(c) => format!("close_code={c} close_reason={text}"),
        None => format!("close_error={text}"),
    })
}

/// One `session_end` line: reason, side, optional QUIC close, then the UDP counters.
pub(crate) fn session_end_line(reason: &str, closed_by: &str, close: Option<&str>, up_ms: u64, counters: &str) -> String {
    match close {
        Some(c) => format!("session_end end_reason={reason} closed_by={closed_by} {c} up_ms={up_ms} {counters}"),
        None => format!("session_end end_reason={reason} closed_by={closed_by} up_ms={up_ms} {counters}"),
    }
}

/// `redial attempt=N redial_in_ms=M[ reset=1] after=<reason>`.
pub(crate) fn redial_line(attempt: u32, ms: u64, reset: bool, after: &str) -> String {
    let r = if reset { " reset=1" } else { "" };
    format!("redial attempt={attempt} redial_in_ms={ms}{r} after={after}")
}

#[cfg(test)]
#[path = "iroh_pilot_dial_tests.rs"]
mod tests;
