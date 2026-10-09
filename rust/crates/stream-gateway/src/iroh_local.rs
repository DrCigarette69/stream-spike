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
}

fn env_nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

fn truthy(v: &str) -> bool {
    matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

/// Env → config, all guard checks up front (refuse before anything binds).
pub fn config_from_env() -> Result<IrohConfig, String> {
    let raw = env_nonempty("SPIKE_IROH_LISTEN").unwrap_or_else(|| DEFAULT_LISTEN.into());
    let listen: SocketAddr = raw
        .parse()
        .map_err(|_| format!("a3_refuse_bad_addr:{raw} (SPIKE_IROH_LISTEN must be ip:port)"))?;
    guard::check_ip(listen.ip()).map_err(|e| format!("{} (SPIKE_IROH_LISTEN)", e.log_line()))?;
    guard::check_relay_url(env_nonempty("SPIKE_IROH_RELAY_URL").as_deref())
        .map_err(|e| format!("{} (SPIKE_IROH_RELAY_URL; Alpha-3 runs with relays disabled)", e.log_line()))?;
    guard::check_discovery(env_nonempty("SPIKE_IROH_DISCOVERY").map(|v| truthy(&v)).unwrap_or(false))
        .map_err(|e| format!("{} (SPIKE_IROH_DISCOVERY)", e.log_line()))?;
    let key_path = env_nonempty("SPIKE_GATEWAY_KEY_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_KEY_PATH));
    Ok(IrohConfig {
        listen,
        key_path,
        expect_id: env_nonempty("SPIKE_GATEWAY_ENDPOINT_ID"),
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
    let ep = Endpoint::empty_builder(RelayMode::Disabled)
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
    Ok(ep)
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
        "iroh_local endpoint {} listening on {:?} alpn={ALPN} relay=disabled discovery=off",
        ep.id(),
        ep.bound_sockets()
    );
    while let Some(incoming) = ep.accept().await {
        let st2 = st.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_incoming(st2, incoming).await {
                tracing::info!("iroh_local conn error: {e}");
            }
        });
    }
    tracing::warn!("iroh_local endpoint closed");
}

async fn handle_incoming(
    st: AppState,
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
    tracing::info!("iroh_local peer connected remote={remote}");
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
    if ticket_id == auth {
        Ok(())
    } else {
        Err(REASON_ENDPOINT_MISMATCH)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn id(seed: u8) -> String {
        SecretKey::from_bytes(&[seed; 32]).public().to_string()
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
            let r = config_from_env();
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
