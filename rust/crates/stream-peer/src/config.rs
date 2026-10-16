//! Env config — same knobs as Python `peer/main.py`.
use stream_proto::ALPN;

#[derive(Debug, Clone)]
pub struct Config {
    pub control_url: String,
    pub transport: String,
    pub relay_dial: String,
    pub admin_listen: String,
    pub peer_id: String,
    /// `SPIKE_PEER_ID` was set (non-empty). iroh_pilot derives peer_id from the endpoint otherwise.
    #[cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
    pub peer_id_from_env: bool,
    pub endpoint_id: String,
    pub host_tier: String,
    pub isp_ack_version: String,
    pub heartbeat_s: f64,
    pub alpn: &'static str,
    /// A3.2: `SPIKE_IROH_KEY_PATH` (iroh_local only; read by part 2).
    #[allow(dead_code)]
    pub iroh_key_path: String,
    /// A3.2: `SPIKE_GATEWAY_ENDPOINT_ID` -- ticket `gateway_endpoint_id` must match.
    pub gateway_endpoint_id: String,
    /// A3.2: `SPIKE_P2_CONSENT=1` headless preset (only effective with an ISP ack).
    pub p2_consent_preset: bool,
    /// A4: `SPIKE_PUBLIC_EGRESS` (default off). With `iroh_pilot` => P9 required before dial.
    pub public_egress: bool,
    /// A4: `SPIKE_P9_ACK=1` headless preset (only with P1 + P2 and P9 required).
    pub p9_ack_preset: bool,
}

/// Shared demo id used by the local/test lanes; never valid for iroh_pilot (the Gateway keys
/// sessions by peer_id, so two Peers on it replace each other in a loop).
pub const DEMO_PEER_ID: &str = "peer_demo";

/// iroh_pilot peer_id: explicit `SPIKE_PEER_ID` (source `env`), else `peer_` + first 16 hex of the
/// endpoint ID (source `endpoint`). Explicit `peer_demo` is refused.
pub fn pilot_peer_id(explicit: Option<&str>, endpoint_hex: &str) -> Result<(String, &'static str), String> {
    if let Some(v) = explicit.map(str::trim).filter(|v| !v.is_empty()) {
        if v == DEMO_PEER_ID {
            return Err(format!(
                "SPIKE_PEER_ID={DEMO_PEER_ID} is not allowed with iroh_pilot (shared demo id); unset SPIKE_PEER_ID, it is derived from the endpoint ID"
            ));
        }
        return Ok((v.to_string(), "env"));
    }
    let hex: String = endpoint_hex.chars().take(16).collect();
    if hex.len() < 16 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(format!("cannot derive peer_id from endpoint id {endpoint_hex:?}"));
    }
    Ok((format!("peer_{}", hex.to_ascii_lowercase()), "endpoint"))
}

impl Config {
    /// iroh_pilot startup check (before the key is loaded): refuse an explicit demo id.
    #[cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
    pub fn pilot_peer_id_env_ok(&self) -> Result<(), String> {
        if self.peer_id_from_env {
            pilot_peer_id(Some(&self.peer_id), "0000000000000000").map(|_| ())
        } else {
            Ok(())
        }
    }

    pub fn from_env() -> Self {
        let transport = std::env::var("SPIKE_TRANSPORT")
            .unwrap_or_else(|_| "fake_relay".into())
            .trim()
            .to_ascii_lowercase();
        let relay_dial = if is_iroh_loopback(&transport) {
            std::env::var("SPIKE_IROH_LOOPBACK_DIAL")
                .unwrap_or_else(|_| "127.0.0.1:9101".into())
        } else {
            std::env::var("SPIKE_FAKE_RELAY_DIAL")
                .unwrap_or_else(|_| "127.0.0.1:9100".into())
        };
        Self {
            control_url: std::env::var("CONTROL_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8080".into())
                .trim_end_matches('/')
                .to_string(),
            transport,
            relay_dial,
            admin_listen: std::env::var("SPIKE_PEER_ADMIN")
                .unwrap_or_else(|_| "0.0.0.0:9200".into()),
            peer_id: std::env::var("SPIKE_PEER_ID")
                .ok()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| "peer_demo".into()),
            peer_id_from_env: std::env::var("SPIKE_PEER_ID").map(|v| !v.trim().is_empty()).unwrap_or(false),
            endpoint_id: std::env::var("SPIKE_ENDPOINT_ID")
                .unwrap_or_else(|_| "iroh_ep_demo_001".into()),
            host_tier: std::env::var("SPIKE_HOST_TIER").unwrap_or_else(|_| "casual".into()),
            isp_ack_version: std::env::var("SPIKE_ISP_ACK_VERSION").unwrap_or_default(),
            heartbeat_s: std::env::var("SPIKE_HEARTBEAT_S")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(5.0),
            alpn: ALPN,
            iroh_key_path: crate::iroh_key::key_path_from_env()
                .to_string_lossy()
                .into_owned(),
            gateway_endpoint_id: std::env::var("SPIKE_GATEWAY_ENDPOINT_ID")
                .unwrap_or_default()
                .trim()
                .to_string(),
            p2_consent_preset: matches!(
                std::env::var("SPIKE_P2_CONSENT").unwrap_or_default().trim(),
                "1" | "true" | "yes"
            ),
            // Guard rule: exactly `1` is on (same as the EgressSwitch env bit).
            public_egress: stream_proto::guard::public_egress_env(),
            p9_ack_preset: matches!(
                std::env::var("SPIKE_P9_ACK").unwrap_or_default().trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            ),
        }
    }
}

pub fn is_iroh_loopback(transport: &str) -> bool {
    matches!(transport, "iroh" | "iroh_loopback")
}

/// A4 pilot transport name (ALPHA4_PILOT.md). Recognized for the P9 gate; the
/// pilot dial itself is A4.4 and not built yet, so the Peer refuses it at startup.
pub fn is_iroh_pilot(transport: &str) -> bool {
    transport == "iroh_pilot"
}

impl Config {
    /// P9 is shown/required only for `iroh_pilot` with `SPIKE_PUBLIC_EGRESS=1`.
    pub fn p9_required(&self) -> bool {
        is_iroh_pilot(&self.transport) && self.public_egress
    }
}

/// A3 real-iroh mode (Rust only, opt-in). Distinct from A1.1 `iroh_loopback`.
pub fn is_iroh_local(transport: &str) -> bool {
    transport == "iroh_local"
}

/// `SPIKE_PEER_ADMIN` must be `ip:port`; a bad value is fatal
/// (`admin_addr_invalid:<value>`) instead of silently falling back to 0.0.0.0:9200.
pub fn parse_admin_addr(s: &str) -> Result<std::net::SocketAddr, String> {
    s.trim()
        .parse()
        .map_err(|_| format!("admin_addr_invalid:{s}"))
}

pub fn is_loopback_host(host: &str) -> bool {
    let h = host.trim().to_ascii_lowercase();
    matches!(h.as_str(), "127.0.0.1" | "::1" | "localhost")
}

pub fn split_host_port(addr: &str) -> Result<(String, u16), String> {
    let (host, port) = addr
        .rsplit_once(':')
        .ok_or_else(|| format!("bad_addr:{addr}"))?;
    let port: u16 = port
        .parse()
        .map_err(|_| format!("bad_port:{addr}"))?;
    Ok((host.to_string(), port))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn admin_addr_parse_is_strict() {
        assert_eq!(parse_admin_addr("0.0.0.0:9200").unwrap().port(), 9200);
        assert_eq!(parse_admin_addr("127.0.0.1:18085").unwrap().port(), 18085);
        assert_eq!(parse_admin_addr("localhost:9200").unwrap_err(), "admin_addr_invalid:localhost:9200");
        assert_eq!(parse_admin_addr("9200").unwrap_err(), "admin_addr_invalid:9200");
        assert_eq!(parse_admin_addr("").unwrap_err(), "admin_addr_invalid:");
    }

    #[test]
    fn loopback_dial_hosts() {
        assert!(is_loopback_host("127.0.0.1"));
        assert!(is_loopback_host("localhost"));
        assert!(is_loopback_host("::1"));
        assert!(!is_loopback_host("8.8.8.8"));
        assert!(!is_loopback_host("0.0.0.0"));
    }

    #[test]
    fn iroh_refuses_non_loopback() {
        assert!(is_iroh_loopback("iroh"));
        assert!(is_iroh_loopback("iroh_loopback"));
        assert!(!is_iroh_loopback("fake_relay"));
        assert!(!is_iroh_loopback("iroh_local"));
        assert!(is_iroh_local("iroh_local"));
        assert!(!is_iroh_local("iroh_loopback"));
        // guard used by relay: iroh + non-loopback => refuse
        let host = "8.8.8.8";
        assert!(is_iroh_loopback("iroh_loopback") && !is_loopback_host(host));
    }
}

#[cfg(test)]
mod pilot_peer_id_tests {
    use super::*;

    #[test]
    fn derived_from_endpoint_when_unset() {
        let box_ep = "92a9d943afd7d7d472c4a29a28f60ab50916f00881d9f20aac703fa32d498c09";
        assert_eq!(pilot_peer_id(None, box_ep), Ok(("peer_92a9d943afd7d7d4".to_string(), "endpoint")));
        assert_eq!(pilot_peer_id(Some("  "), box_ep).unwrap().1, "endpoint");
        assert_eq!(pilot_peer_id(None, &box_ep.to_ascii_uppercase()).unwrap().0, "peer_92a9d943afd7d7d4");
    }

    #[test]
    fn explicit_env_wins_but_demo_refused() {
        assert_eq!(pilot_peer_id(Some("peer_lab1"), "ab".repeat(32).as_str()), Ok(("peer_lab1".to_string(), "env")));
        let e = pilot_peer_id(Some("peer_demo"), &"ab".repeat(32)).unwrap_err();
        assert!(e.contains("SPIKE_PEER_ID=peer_demo") && e.contains("iroh_pilot"), "{e}");
    }

    #[test]
    fn bad_endpoint_refused_never_demo() {
        assert!(pilot_peer_id(None, "iroh_ep_demo_001").is_err());
        assert!(pilot_peer_id(None, "abc").is_err());
    }
}
