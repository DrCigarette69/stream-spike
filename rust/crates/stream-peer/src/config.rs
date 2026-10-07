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
        }
    }
}

pub fn is_iroh_loopback(transport: &str) -> bool {
    matches!(transport, "iroh" | "iroh_loopback")
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
        // guard used by relay: iroh + non-loopback => refuse
        let host = "8.8.8.8";
        assert!(is_iroh_loopback("iroh_loopback") && !is_loopback_host(host));
    }
}
