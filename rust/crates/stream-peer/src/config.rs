//! Env config — same knobs as Python `peer/main.py`.
use stream_proto::ALPN;

#[derive(Debug, Clone)]
pub struct Config {
    pub control_url: String,
    pub transport: String,
    pub relay_dial: String,
    pub admin_listen: String,
    pub peer_id: String,
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
}

impl Config {
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
            peer_id: std::env::var("SPIKE_PEER_ID").unwrap_or_else(|_| "peer_demo".into()),
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
        }
    }
}

pub fn is_iroh_loopback(transport: &str) -> bool {
    matches!(transport, "iroh" | "iroh_loopback")
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
