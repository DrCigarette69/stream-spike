//! Env parity with Python `gateway/main.py`.

use std::net::SocketAddr;

#[allow(dead_code)]
pub const ALPN: &str = stream_proto::ALPN;

const IROH_LOOPBACK: &[&str] = &["iroh", "iroh_loopback"];

#[derive(Clone, Debug)]
pub struct Config {
    pub control_url: String,
    pub transport: String,
    pub proxy_listen: SocketAddr,
    pub relay_listen: SocketAddr,
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        let control_url = std::env::var("CONTROL_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8080".into())
            .trim_end_matches('/')
            .to_string();
        let transport = std::env::var("SPIKE_TRANSPORT")
            .unwrap_or_else(|_| "fake_relay".into())
            .trim()
            .to_ascii_lowercase();

        let proxy_raw =
            std::env::var("SPIKE_LISTEN_PROXY").unwrap_or_else(|_| "127.0.0.1:1080".into());
        let proxy_listen: SocketAddr = proxy_raw
            .parse()
            .map_err(|e| format!("SPIKE_LISTEN_PROXY={proxy_raw:?}: {e}"))?;

        let relay_raw = if is_iroh_loopback(&transport) {
            std::env::var("SPIKE_IROH_LOOPBACK").unwrap_or_else(|_| "127.0.0.1:9101".into())
        } else {
            std::env::var("SPIKE_FAKE_RELAY").unwrap_or_else(|_| "127.0.0.1:9100".into())
        };
        let relay_listen: SocketAddr = relay_raw
            .parse()
            .map_err(|e| format!("relay listen {relay_raw:?}: {e}"))?;

        if is_iroh_loopback(&transport) && !is_loopback_host(relay_listen.ip()) {
            return Err(format!(
                "A1.1 iroh_loopback refuses non-loopback bind {:?} (no public egress)",
                relay_listen.ip()
            ));
        }

        Ok(Self {
            control_url,
            transport,
            proxy_listen,
            relay_listen,
        })
    }

    #[allow(dead_code)]
    pub fn is_iroh_loopback(&self) -> bool {
        is_iroh_loopback(&self.transport)
    }

    pub fn relay_listen_display(&self) -> String {
        self.relay_listen.to_string()
    }
}

fn is_iroh_loopback(transport: &str) -> bool {
    IROH_LOOPBACK.contains(&transport)
}

fn is_loopback_host(ip: std::net::IpAddr) -> bool {
    match ip {
        std::net::IpAddr::V4(v4) => v4.is_loopback(),
        std::net::IpAddr::V6(v6) => v6.is_loopback(),
    }
}
