//! A3.1 Gateway iroh endpoint (`SPIKE_TRANSPORT=iroh_local`, `--features iroh`).
//!
//! - Empty builder, `RelayMode::Disabled`, `clear_discovery()`, ALPN `stream/tunnel/1`.
//! - Secret key = fixed DEV-ONLY key file (`SPIKE_GATEWAY_KEY_PATH`).
//! - Binds only to an address passing `stream_proto::guard::check_ip`; relay URL and
//!   discovery are refused via the guard. Every node must still run in a no-default-route
//!   netns: iroh 0.95.1's portmapper has no off switch.
//! - Framing (same as fake_relay): Peer dials, opens ONE bi stream (`open_bi`), sends
//!   NDJSON HELLO; Gateway → HELLO_OK, then AUTH_TICKET per stream; OPEN/BYTES/CLOSE unchanged.
//!   New rule: authenticated remote EndpointId != ticket `peer_endpoint_id` → AUTH_REJECT
//!   `endpoint_mismatch` + close.
//! - A4.2 `SPIKE_TRANSPORT=iroh_pilot` (`--features iroh_pilot`): same endpoint and framing, but
//!   `RelayMode::Custom` with exactly our one relay (`SPIKE_IROH_RELAY_URL`, default
//!   `SPIKE_RELAY_ALLOW_URL`, checked by `guard::check_relay_url_with`), `PathSelection::RelayOnly`,
//!   discovery off. Tickets must carry that `relay_url`. Never skips relay cert verification.
use std::net::{Ipv4Addr, Ipv6Addr, SocketAddr, SocketAddrV4, SocketAddrV6};
use std::path::PathBuf;
use std::str::FromStr;

use iroh::endpoint::{Connection, RecvStream, SendStream};
use iroh::{Endpoint, EndpointId, RelayMode, SecretKey};
use stream_proto::guard;
use stream_proto::ALPN;

use crate::conn::PeerConn;
use crate::state::AppState;

pub const DEFAULT_LISTEN: &str = "127.0.0.1:9102";
pub const DEFAULT_KEY_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/dev/gateway_dev.key");
pub const REASON_ENDPOINT_MISMATCH: &str = "endpoint_mismatch";
pub const REASON_ENDPOINT_BIND_REQUIRED: &str = "endpoint_bind_required";
/// A3.4: a hard-killed Peer is noticed within this (keep-alive 1 s). Env: SPIKE_IROH_IDLE_TIMEOUT_MS.
pub const DEFAULT_IDLE_TIMEOUT_MS: u64 = 6000;

/// One authenticated iroh connection + its single bi stream.
pub struct IrohStream {
    pub conn: Connection,
    pub send: SendStream,
    pub recv: RecvStream,
}

impl IrohStream {
    pub fn is_alive(&self) -> bool {
        self.conn.close_reason().is_none()
    }

    /// Finish our side, give the peer a moment to read the last frame, then close.
    pub async fn close_with(&mut self, reason: &str) {
        let _ = self.send.finish();
        let _ = tokio::time::timeout(std::time::Duration::from_millis(500), self.send.stopped()).await;
        self.conn.close(1u32.into(), reason.as_bytes());
    }
}

#[derive(Clone, Debug)]
pub struct IrohConfig {
    pub listen: SocketAddr,
    pub key_path: PathBuf,
    pub expect_id: Option<String>,
    /// A4.2: Some = iroh_pilot (relay-only via exactly this relay).
    pub pilot: Option<PilotRelay>,
}

#[derive(Clone, Debug)]
pub struct PilotRelay {
    pub relay_url: iroh::RelayUrl,
    pub allow: guard::RelayAllow,
}

/// Endpoint-wide datagram counters (magicsock metrics; live in iroh_pilot builds, where
/// iroh-relay/server turns iroh-metrics on). `ConnectionType` stays `None` under RelayOnly in
/// iroh 0.95.1 (addr_for_send returns early), so these counters are the path evidence.
#[cfg(feature = "iroh_pilot")]
pub fn path_counters(ep: &Endpoint) -> serde_json::Value {
    let m = &ep.metrics().magicsock;
    let udp_send = m.send_ipv4.get() + m.send_ipv6.get();
    let udp_recv = m.recv_data_ipv4.get() + m.recv_data_ipv6.get();
    let relay_send = m.send_relay.get();
    let relay_recv = m.recv_data_relay.get();
    let path = if udp_send + udp_recv > 0 {
        "udp"
    } else if relay_send + relay_recv > 0 {
        "relay"
    } else {
        "none"
    };
    serde_json::json!({"path": path, "relay_send": relay_send, "relay_recv": relay_recv,
                       "udp_send": udp_send, "udp_recv": udp_recv})
}

#[cfg(not(feature = "iroh_pilot"))]
pub fn path_counters(_ep: &Endpoint) -> serde_json::Value {
    serde_json::json!({"path": "unknown"})
}

static ENDPOINT: std::sync::OnceLock<Endpoint> = std::sync::OnceLock::new();

/// For /health: counters of the running endpoint (None before bind).
pub fn health_counters() -> Option<serde_json::Value> {
    ENDPOINT.get().map(path_counters)
}

/// Set once at startup in iroh_pilot mode; tickets are re-checked against it.
static PILOT_ALLOW: std::sync::OnceLock<guard::RelayAllow> = std::sync::OnceLock::new();

/// A4.2 pilot relay config, all guard checks before anything binds.
pub fn pilot_relay_from_env() -> Result<PilotRelay, String> {
    let lane = guard::Lane::build();
    let allow = guard::relay_allow_from_env(lane)
        .map_err(|e| format!("{} (SPIKE_RELAY_ALLOW_URL, lane={lane:?})", e.log_line()))?
        .ok_or_else(|| {
            "a4_refuse_relay_config:SPIKE_RELAY_ALLOW_URL unset (iroh_pilot needs our one self-hosted relay)".to_string()
        })?;
    let raw = env_nonempty("SPIKE_IROH_RELAY_URL").unwrap_or_else(|| allow.url());
    guard::check_relay_url_with(Some(&raw), Some(&allow))
        .map_err(|e| format!("{} (SPIKE_IROH_RELAY_URL must equal SPIKE_RELAY_ALLOW_URL)", e.log_line()))?;
    match env_nonempty("SPIKE_IROH_PATH_SELECTION").map(|v| v.to_ascii_lowercase()) {
        None => {}
        Some(v) if v == "relay_only" => {}
        Some(v) => {
            return Err(format!(
                "a4_refuse_relay_config:SPIKE_IROH_PATH_SELECTION={v} (iroh_pilot is relay_only, no direct paths)"
            ))
        }
    }
    let relay_url = iroh::RelayUrl::from_str(&allow.url())
        .map_err(|e| format!("a4_refuse_relay_config:{raw} ({e})"))?;
    Ok(PilotRelay { relay_url, allow })
}

fn env_nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

fn truthy(v: &str) -> bool {
    matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

/// Env → config, all guard checks up front (refuse before anything binds).
/// `pilot` = A4.2 iroh_pilot (relay-only); otherwise A3 iroh_local (relays refused).
pub fn config_from_env(pilot: bool) -> Result<IrohConfig, String> {
    let raw = env_nonempty("SPIKE_IROH_LISTEN").unwrap_or_else(|| DEFAULT_LISTEN.into());
    let listen: SocketAddr = raw
        .parse()
        .map_err(|_| format!("a3_refuse_bad_addr:{raw} (SPIKE_IROH_LISTEN must be ip:port)"))?;
    guard::check_ip(listen.ip()).map_err(|e| format!("{} (SPIKE_IROH_LISTEN)", e.log_line()))?;
    let pilot = if pilot {
        Some(pilot_relay_from_env()?)
    } else {
        guard::check_relay_url(env_nonempty("SPIKE_IROH_RELAY_URL").as_deref())
            .map_err(|e| format!("{} (SPIKE_IROH_RELAY_URL; Alpha-3 runs with relays disabled)", e.log_line()))?;
        None
    };
    guard::check_discovery(env_nonempty("SPIKE_IROH_DISCOVERY").map(|v| truthy(&v)).unwrap_or(false))
        .map_err(|e| format!("{} (SPIKE_IROH_DISCOVERY)", e.log_line()))?;
    let key_path = env_nonempty("SPIKE_GATEWAY_KEY_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_KEY_PATH));
    Ok(IrohConfig {
        listen,
        key_path,
        expect_id: env_nonempty("SPIKE_GATEWAY_ENDPOINT_ID"),
        pilot,
    })
}

/// Load the 32-byte DEV-ONLY key. Group/world-readable is tolerated (git keeps 0644) with a warning.
pub fn load_key(path: &std::path::Path) -> Result<SecretKey, String> {
    use std::os::unix::fs::PermissionsExt;
    let meta = std::fs::metadata(path).map_err(|e| format!("gateway key {}: {e}", path.display()))?;
    let mode = meta.permissions().mode() & 0o777;
    if mode & 0o077 != 0 {
        tracing::warn!(
            "gateway key {} has mode {:o} (git checkout); tolerated for the DEV-ONLY key, chmod 600 to silence",
            path.display(),
            mode
        );
    }
    let raw = std::fs::read(path).map_err(|e| format!("gateway key {}: {e}", path.display()))?;
    let bytes: [u8; 32] = raw
        .as_slice()
        .try_into()
        .map_err(|_| format!("gateway key {}: expected 32 raw bytes, got {}", path.display(), raw.len()))?;
    Ok(SecretKey::from_bytes(&bytes))
}

/// Bind the endpoint. The configured family binds the guarded listen addr; the other family
/// is pinned to loopback (never the `0.0.0.0`/`[::]` default).
pub async fn bind(cfg: &IrohConfig) -> Result<Endpoint, String> {
    let key = load_key(&cfg.key_path)?;
    let id = key.public().to_string();
    if let Some(want) = &cfg.expect_id {
        if want != &id {
            return Err(format!(
                "SPIKE_GATEWAY_ENDPOINT_ID={want} does not match key {} (derives {id})",
                cfg.key_path.display()
            ));
        }
    }
    let (v4, v6) = match cfg.listen {
        SocketAddr::V4(a) => (a, SocketAddrV6::new(Ipv6Addr::LOCALHOST, 0, 0, 0)),
        SocketAddr::V6(a) => (SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0), a),
    };
    let idle_ms = env_nonempty("SPIKE_IROH_IDLE_TIMEOUT_MS")
        .map(|v| v.parse::<u64>().map_err(|_| format!("SPIKE_IROH_IDLE_TIMEOUT_MS={v:?}: not a number")))
        .transpose()?
        .unwrap_or(DEFAULT_IDLE_TIMEOUT_MS)
        .clamp(1000, 60_000);
    let mut tc = iroh::endpoint::TransportConfig::default();
    tc.keep_alive_interval(Some(std::time::Duration::from_secs(1)));
    tc.max_idle_timeout(Some(
        std::time::Duration::from_millis(idle_ms)
            .try_into()
            .map_err(|e| format!("idle timeout: {e}"))?,
    ));
    let relay_mode = match &cfg.pilot {
        None => RelayMode::Disabled,
        // QUIC address discovery off (quic: None): relay-only never needs our public UDP addr,
        // and it would send UDP probes to the relay's QAD port.
        Some(p) => RelayMode::Custom(iroh::RelayMap::from(iroh::RelayConfig { url: p.relay_url.clone(), quic: None })),
    };
    let builder = Endpoint::empty_builder(relay_mode);
    let builder = match &cfg.pilot {
        None => builder,
        Some(p) => pilot_builder(builder, p)?,
    };
    let ep = builder
        .transport_config(tc)
        .clear_discovery()
        .secret_key(key)
        .alpns(vec![ALPN.as_bytes().to_vec()])
        .bind_addr_v4(v4)
        .bind_addr_v6(v6)
        .bind()
        .await
        .map_err(|e| format!("iroh bind {}: {e}", cfg.listen))?;
    let bound = ep.bound_sockets();
    for sa in &bound {
        if !guard::is_private_addr(sa.ip()) {
            ep.close().await;
            return Err(format!("a3_refuse_non_private:{sa} (bound socket)"));
        }
    }
    if cfg.listen.port() != 0 && !bound.iter().any(|sa| sa.port() == cfg.listen.port()) {
        ep.close().await;
        return Err(format!(
            "iroh could not bind {} (port busy? bound {:?}); refusing random-port fallback",
            cfg.listen, bound
        ));
    }
    if let Some(p) = &cfg.pilot {
        let wait = env_nonempty("SPIKE_IROH_RELAY_WAIT_MS")
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(10_000)
            .clamp(500, 60_000);
        // Fail closed: no home relay connection → refuse to start (there is no direct fallback).
        if tokio::time::timeout(std::time::Duration::from_millis(wait), ep.online()).await.is_err() {
            ep.close().await;
            return Err(format!("a4_refuse_relay_unreachable:{} (no home relay within {wait} ms)", p.relay_url));
        }
        let home = ep.addr().relay_urls().map(|u| u.to_string()).collect::<Vec<_>>();
        if home != [p.relay_url.to_string()] {
            ep.close().await;
            return Err(format!("a4_refuse_relay_refused:{home:?} (home relay != {})", p.relay_url));
        }
        let _ = PILOT_ALLOW.set(p.allow.clone());
    }
    Ok(ep)
}

/// A4.2: relay-only path selection (iroh test-utils API, hence the `iroh_pilot` feature).
/// Deliberately never calls `insecure_skip_relay_cert_verify` (grep-checked by a42).
#[cfg(feature = "iroh_pilot")]
fn pilot_builder(b: iroh::endpoint::Builder, _p: &PilotRelay) -> Result<iroh::endpoint::Builder, String> {
    Ok(b.path_selection(iroh::endpoint::PathSelection::RelayOnly))
}

#[cfg(not(feature = "iroh_pilot"))]
fn pilot_builder(_b: iroh::endpoint::Builder, _p: &PilotRelay) -> Result<iroh::endpoint::Builder, String> {
    Err("iroh_pilot needs --features iroh_pilot (PathSelection::RelayOnly); refusing".into())
}

/// The address Peers should put in `direct_addrs` (listen ip + actually bound port).
pub fn advertised_addr(ep: &Endpoint, cfg: &IrohConfig) -> SocketAddr {
    if cfg.listen.port() != 0 {
        return cfg.listen;
    }
    let port = ep
        .bound_sockets()
        .into_iter()
        .find(|sa| sa.is_ipv4() == cfg.listen.is_ipv4())
        .map(|sa| sa.port())
        .unwrap_or(0);
    SocketAddr::new(cfg.listen.ip(), port)
}

pub async fn run_accept(st: AppState, ep: Endpoint) {
    tracing::info!(
        "iroh endpoint {} listening on {:?} alpn={ALPN} relay={} discovery=off",
        ep.id(),
        ep.bound_sockets(),
        st.relay_url.as_deref().map(|u| format!("{u} (relay_only)")).unwrap_or_else(|| "disabled".into())
    );
    let _ = ENDPOINT.set(ep.clone());
    while let Some(incoming) = ep.accept().await {
        let st2 = st.clone();
        let ep2 = ep.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_incoming(st2, ep2, incoming).await {
                tracing::info!("iroh_local conn error: {e}");
            }
        });
    }
    tracing::warn!("iroh_local endpoint closed");
}

async fn handle_incoming(
    st: AppState,
    ep: Endpoint,
    incoming: iroh::endpoint::Incoming,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let conn = incoming.await?;
    if conn.alpn() != ALPN.as_bytes() {
        conn.close(2u32.into(), b"bad_alpn");
        return Ok(());
    }
    let remote = conn.remote_id();
    let (send, recv) = match tokio::time::timeout(std::time::Duration::from_secs(30), conn.accept_bi()).await {
        Ok(Ok(p)) => p,
        Ok(Err(e)) => return Err(e.into()),
        Err(_) => {
            conn.close(3u32.into(), b"no_stream");
            return Ok(());
        }
    };
    let path = path_counters(&ep);
    tracing::info!("iroh peer connected remote={remote} path={path}");
    println!("IROH_PEER_CONNECTED remote={remote} path={path}");
    let pc = PeerConn::Iroh(IrohStream { conn, send, recv });
    crate::relay::handle_peer_conn(st, pc, Some(remote.to_string())).await
}

/// Ticket binding (A3.3): `peer_endpoint_id` from the ticket (payload or top level) must parse
/// and equal the connection's authenticated remote id.
pub fn check_ticket_binding(auth_id: &str, ticket_json: &str) -> Result<(), &'static str> {
    let v: serde_json::Value = serde_json::from_str(ticket_json).unwrap_or_default();
    let raw = v
        .get("payload")
        .and_then(|p| p.get("peer_endpoint_id"))
        .or_else(|| v.get("peer_endpoint_id"))
        .and_then(|x| x.as_str());
    let Some(raw) = raw else {
        return Err(REASON_ENDPOINT_BIND_REQUIRED);
    };
    let Ok(ticket_id) = EndpointId::from_str(raw.trim()) else {
        return Err(REASON_ENDPOINT_BIND_REQUIRED);
    };
    let Ok(auth) = EndpointId::from_str(auth_id) else {
        return Err(REASON_ENDPOINT_MISMATCH);
    };
    if ticket_id != auth {
        return Err(REASON_ENDPOINT_MISMATCH);
    }
    match PILOT_ALLOW.get() {
        None => Ok(()),
        Some(allow) => check_pilot_ticket(&v, allow),
    }
}

/// A4.2: iroh_pilot tickets must name our relay (`relay_url`); any `direct_addrs` still pass A3.0.
pub fn check_pilot_ticket(v: &serde_json::Value, allow: &guard::RelayAllow) -> Result<(), &'static str> {
    let pl = v.get("payload").unwrap_or(v);
    let relay = pl.get("relay_url").and_then(|x| x.as_str());
    let addrs: Vec<String> = pl
        .get("direct_addrs")
        .and_then(|x| x.as_array())
        .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
        .unwrap_or_default();
    if relay.map(|r| r.trim().is_empty()).unwrap_or(true) {
        return Err(guard::REASON_RELAY_REQUIRED);
    }
    guard::check_relay_url_with(relay, Some(allow)).map_err(|e| e.reason())?;
    for a in &addrs {
        guard::check_direct_addr(a).map_err(|e| e.reason())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(seed: u8) -> String {
        SecretKey::from_bytes(&[seed; 32]).public().to_string()
    }

    #[test]
    fn pilot_ticket_needs_our_relay() {
        let lane = stream_proto::guard::Lane::Local;
        let allow = guard::RelayAllow::parse("http://10.73.0.254:3340/", lane).unwrap();
        let t = |relay: serde_json::Value, addrs: serde_json::Value| {
            serde_json::json!({"payload": {"relay_url": relay, "direct_addrs": addrs}})
        };
        let e = serde_json::json!([]);
        assert_eq!(check_pilot_ticket(&t("http://10.73.0.254:3340/".into(), e.clone()), &allow), Ok(()));
        assert_eq!(check_pilot_ticket(&t("http://10.73.0.254:3340".into(), e.clone()), &allow), Ok(()));
        assert_eq!(check_pilot_ticket(&t(serde_json::Value::Null, e.clone()), &allow), Err("relay_required"));
        assert_eq!(check_pilot_ticket(&t("".into(), e.clone()), &allow), Err("relay_required"));
        for bad in ["http://10.73.0.254:3341/", "https://10.73.0.254:3340/", "https://use1-1.relay.n0.iroh.iroh.link./"] {
            assert_eq!(check_pilot_ticket(&t(bad.into(), e.clone()), &allow), Err("relay_refused"), "{bad}");
        }
        assert_eq!(
            check_pilot_ticket(&t("http://10.73.0.254:3340/".into(), serde_json::json!(["8.8.8.8:9102"])), &allow),
            Err("public_addr")
        );
    }

    /// A4 lock: `iroh_pilot` (iroh/test-utils) makes RelayOnly path selection reachable.
    /// Compile check only; no endpoint is bound and no behavior changes.
    #[cfg(feature = "iroh_pilot")]
    #[test]
    fn iroh_pilot_relay_only_reachable() {
        use iroh::endpoint::PathSelection;
        let _builder = iroh::Endpoint::empty_builder(iroh::RelayMode::Disabled)
            .path_selection(PathSelection::RelayOnly);
        assert_ne!(PathSelection::RelayOnly, PathSelection::All);
    }

    #[test]
    fn binding_ok_mismatch_missing() {
        let a = id(1);
        let b = id(2);
        let t = |pid: &str| format!(r#"{{"payload":{{"peer_endpoint_id":"{pid}"}},"sig":"x"}}"#);
        assert_eq!(check_ticket_binding(&a, &t(&a)), Ok(()));
        assert_eq!(check_ticket_binding(&a, &t(&b)), Err(REASON_ENDPOINT_MISMATCH));
        assert_eq!(check_ticket_binding(&a, r#"{"payload":{},"sig":"x"}"#), Err(REASON_ENDPOINT_BIND_REQUIRED));
        assert_eq!(check_ticket_binding(&a, &t("iroh_ep_demo_001")), Err(REASON_ENDPOINT_BIND_REQUIRED));
    }

    /// iroh bind never falls back to a wildcard: 0.0.0.0 / [::] / garbage are refused up front.
    #[test]
    fn iroh_listen_refuses_wildcard_and_garbage() {
        for (v, want) in [
            ("0.0.0.0:9102", "a3_refuse_non_private:0.0.0.0"),
            ("[::]:9102", "a3_refuse_non_private:::"),
            ("garbage", "a3_refuse_bad_addr:garbage"),
            ("8.8.8.8:9102", "a3_refuse_non_private:8.8.8.8"),
        ] {
            std::env::set_var("SPIKE_IROH_LISTEN", v);
            let r = config_from_env(false);
            std::env::remove_var("SPIKE_IROH_LISTEN");
            let e = r.unwrap_err();
            assert!(e.contains(want), "{v}: {e}");
        }
    }

    #[test]
    fn dev_key_matches_recorded_id() {
        let k = load_key(std::path::Path::new(DEFAULT_KEY_PATH)).unwrap();
        assert_eq!(
            k.public().to_string(),
            "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1"
        );
    }
}
