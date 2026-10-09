#![cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
//! A4.4 part 2 (iroh-free half, compiled in every build so it is unit-tested
//! without the `iroh_pilot` feature): pilot startup checks, the relay-only dial
//! target, ticket relay checks, and the direct-path watcher logic. The iroh
//! endpoint itself lives in `iroh_pilot_dial.rs` (feature `iroh_pilot`).
use crate::iroh_ticket::GATEWAY_MISMATCH;
use crate::kill::TransportSlot;
use crate::state::SharedState;
use serde_json::Value;
use std::future::Future;
use std::net::SocketAddr;
use stream_proto::guard::{
    self, check_relay_required, check_relay_url_with, check_transport_pilot, relay_allow_from_env, Cidr,
    GuardError, Lane, RelayAllow, REASON_RELAY_CONFIG,
};
use tokio::time::Duration;

/// P8 key + CLOSE reason when the connection leaves the relay path.
pub const RELAY_PATH_REQUIRED: &str = "relay_path_required";
/// Optional override of the Gateway's relay URL for the dial (default: the allowed URL).
pub const ENV_GATEWAY_RELAY_URL: &str = "SPIKE_IROH_GATEWAY_RELAY_URL";
pub const WATCH_INTERVAL: Duration = Duration::from_millis(100);

/// Pilot-build startup: `SPIKE_TRANSPORT` must be `iroh_pilot` and
/// `SPIKE_RELAY_ALLOW_URL` must name exactly one acceptable relay. Err = log line.
pub fn startup_checks(transport: &str, lane: Lane) -> Result<RelayAllow, String> {
    check_transport_pilot(transport).map_err(|e| e.log_line())?;
    match relay_allow_from_env(lane) {
        Ok(Some(a)) => Ok(a),
        Ok(None) => Err(GuardError { reason: REASON_RELAY_CONFIG, detail: "<unset>".into() }.log_line()),
        Err(e) => Err(e.log_line()),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PilotTarget {
    pub gateway_endpoint_id: String,
    /// Canonical relay URL we dial through (always equals the allowed relay).
    pub relay_url: String,
    /// Checked by the A3.0 private guard but never dialed (RelayOnly).
    pub direct_addrs: Vec<SocketAddr>,
}

fn refuse(e: GuardError) -> String {
    eprintln!("{}", e.log_line());
    e.reason().to_string()
}

/// Gateway id must match; `relay_url` (default: the allowed one) must equal
/// the allowed relay; empty `direct_addrs` needs a relay (`relay_required`);
/// any `direct_addrs` entry still passes the private guard.
pub fn check_pilot_target(
    payload: &Value,
    expected_gateway: &str,
    allow: &RelayAllow,
    cidrs: Option<&[Cidr]>,
) -> Result<PilotTarget, String> {
    let gw = payload.get("gateway_endpoint_id").and_then(Value::as_str).unwrap_or("").trim();
    if gw.is_empty() || gw != expected_gateway.trim() {
        return Err(GATEWAY_MISMATCH.into());
    }
    let relay = payload.get("relay_url").and_then(Value::as_str).map(str::trim).filter(|s| !s.is_empty());
    let addrs: Vec<String> = match payload.get("direct_addrs") {
        None | Some(Value::Null) => vec![],
        Some(Value::Array(a)) => a.iter().map(|v| v.as_str().map(String::from).ok_or(guard::REASON_BAD_ADDR)).collect::<Result<_, _>>()?,
        Some(_) => return Err(guard::REASON_BAD_ADDR.into()),
    };
    check_relay_required(&addrs, relay).map_err(refuse)?;
    let relay = relay.ok_or_else(|| refuse(GuardError { reason: guard::REASON_RELAY_REQUIRED, detail: "relay_url".into() }))?;
    check_relay_url_with(Some(relay), Some(allow)).map_err(refuse)?;
    let direct_addrs = if addrs.is_empty() {
        vec![]
    } else {
        guard::check_direct_addrs_with(&addrs, cidrs).map_err(refuse)?
    };
    Ok(PilotTarget { gateway_endpoint_id: gw.to_string(), relay_url: allow.url(), direct_addrs })
}

/// Dial payload from env: `SPIKE_GATEWAY_ENDPOINT_ID`, optional
/// `SPIKE_IROH_GATEWAY_ADDR`, relay = `SPIKE_IROH_GATEWAY_RELAY_URL` or the allowed URL.
pub fn env_payload(gateway_id: &str, allow: &RelayAllow) -> Value {
    let addrs: Vec<String> = std::env::var("SPIKE_IROH_GATEWAY_ADDR")
        .unwrap_or_default()
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    let relay = std::env::var(ENV_GATEWAY_RELAY_URL).unwrap_or_else(|_| allow.url());
    serde_json::json!({"gateway_endpoint_id": gateway_id, "direct_addrs": addrs, "relay_url": relay})
}

/// AUTH_TICKET in pilot mode: a ticket `relay_url` (top level or `payload`)
/// must be our relay; empty `direct_addrs` needs one.
pub fn check_ticket_relay(ticket_json: &str, allow: Option<&RelayAllow>) -> Result<(), String> {
    let v: Value = serde_json::from_str(ticket_json).unwrap_or(Value::Null);
    let pick = |k: &str| v.get(k).or_else(|| v.get("payload").and_then(|p| p.get(k))).cloned();
    let relay = pick("relay_url").and_then(|r| r.as_str().map(String::from));
    check_relay_url_with(relay.as_deref(), allow).map_err(refuse)?;
    if let Some(Value::Array(a)) = pick("direct_addrs") {
        let addrs: Vec<String> = a.iter().filter_map(|x| x.as_str().map(String::from)).collect();
        check_relay_required(&addrs, relay.as_deref()).map_err(refuse)?;
    }
    Ok(())
}

/// iroh-free view of `iroh::endpoint::ConnectionType`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathKind {
    Relay,
    /// Direct or mixed (a UDP path exists): detail = the UDP address.
    Direct(String),
    None,
}

/// Poll `get` every `WATCH_INTERVAL` until a direct path shows up; then close
/// all pilot egress, kill the transport (<= 2 s), log
/// `a4_refuse_relay_refused:direct_path:<addr>` and show P8 `relay_path_required`.
/// Returns the offending address. Runs until `stop` resolves otherwise.
pub async fn watch_path<G, S>(state: &SharedState, slot: &TransportSlot, mut get: G, stop: S) -> Option<String>
where
    G: FnMut() -> PathKind,
    S: Future<Output = ()>,
{
    tokio::pin!(stop);
    loop {
        if let PathKind::Direct(addr) = get() {
            eprintln!("a4_refuse_{}:direct_path:{addr}", guard::REASON_RELAY_REFUSED);
            if let Some(p) = state.read().await.egress.clone() {
                p.lock().unwrap().close_all(RELAY_PATH_REQUIRED);
            }
            let _ = slot.kill(RELAY_PATH_REQUIRED).await;
            crate::offline::system_offline(state, RELAY_PATH_REQUIRED).await;
            return Some(addr);
        }
        tokio::select! {
            _ = &mut stop => return None,
            _ = tokio::time::sleep(WATCH_INTERVAL) => {}
        }
    }
}
