//! A4.2 relay config (pure, testable without the server feature).
//!
//! Env:
//! - `STREAM_RELAY_MODE`: `dev` (plain http; only `a4_local` builds, only on 10.73.0.254) | `tls`.
//! - `STREAM_RELAY_HTTP_BIND`: `ip:port` (dev default `10.73.0.254:3340`). Never `0.0.0.0`/`[::]`.
//! - `STREAM_RELAY_ALLOW_ENDPOINTS`: comma-separated iroh EndpointIds (64 lowercase hex) allowed to
//!   use the relay; unset = everyone (dev lane only; `tls` mode requires it).
//! - TLS (VPS deploy): `STREAM_RELAY_HOSTNAME`, `STREAM_RELAY_HTTPS_BIND`, `STREAM_RELAY_HTTP_BIND`,
//!   `STREAM_RELAY_PUBLIC_PORT` (port in the advertised URL; default = HTTPS bind port, e.g. 443
//!   when an SNI passthrough forwards 443 to a loopback bind), and a cert source:
//!   - `STREAM_RELAY_TLS_CERT_MODE=acme`: Let's Encrypt via TLS-ALPN-01 on the HTTPS listener
//!     (iroh-relay's built-in ACME). `STREAM_RELAY_ACME_CACHE` (dir, required),
//!     `STREAM_RELAY_ACME_PROD=1` for the production directory (else LE staging),
//!     `STREAM_RELAY_ACME_CONTACT` (optional email).
//!   - `manual` (default): `STREAM_RELAY_TLS_CERT` / `STREAM_RELAY_TLS_KEY` PEM files.
//! QUIC address discovery and metrics listeners are always off.
use std::net::{IpAddr, SocketAddr};

use stream_proto::guard::{self, Lane, LOCAL_RELAY_HOST};

pub const DEV_DEFAULT_BIND: &str = "10.73.0.254:3340";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    /// Plain http on the on-box lane.
    Dev,
    /// TLS on our hostname.
    Tls { hostname: String, https_bind: SocketAddr, public_port: u16, cert: CertSource },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CertSource {
    /// PEM cert chain + key files.
    Files { cert: String, key: String },
    /// Let's Encrypt (TLS-ALPN-01), cached in `cache_dir`.
    Acme { cache_dir: String, prod: bool, contact: Option<String> },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RelayConfig {
    pub mode: Mode,
    pub http_bind: SocketAddr,
    pub allow_endpoints: Option<Vec<String>>,
}

impl RelayConfig {
    /// The URL clients put in `SPIKE_RELAY_ALLOW_URL`.
    pub fn url(&self) -> String {
        match &self.mode {
            Mode::Dev => format!("http://{}/", self.http_bind),
            Mode::Tls { hostname, public_port, .. } => format!("https://{hostname}:{public_port}/"),
        }
    }
}

fn bad_bind(ip: IpAddr) -> bool {
    ip.is_unspecified() || ip.is_multicast()
}

fn valid_endpoint_id(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Env lookup is injected so tests don't touch the process env.
pub fn config_from(get: impl Fn(&str) -> Option<String>, lane: Lane) -> Result<RelayConfig, String> {
    let get = |k: &str| get(k).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());
    let mode = get("STREAM_RELAY_MODE").unwrap_or_else(|| "dev".into()).to_ascii_lowercase();
    let allow_endpoints = match get("STREAM_RELAY_ALLOW_ENDPOINTS") {
        None => None,
        Some(v) => {
            let ids: Vec<String> = v.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
            if ids.is_empty() || !ids.iter().all(|i| valid_endpoint_id(i)) {
                return Err(format!("relay_config: STREAM_RELAY_ALLOW_ENDPOINTS={v:?} (64-hex endpoint ids)"));
            }
            Some(ids)
        }
    };
    match mode.as_str() {
        "dev" => {
            if lane != Lane::Local {
                return Err("relay_config: STREAM_RELAY_MODE=dev (plain http) needs an a4_local build; \
                            the pilot relay must use tls"
                    .into());
            }
            let raw = get("STREAM_RELAY_HTTP_BIND").unwrap_or_else(|| DEV_DEFAULT_BIND.into());
            let http_bind: SocketAddr =
                raw.parse().map_err(|_| format!("relay_config: STREAM_RELAY_HTTP_BIND={raw:?} (ip:port)"))?;
            if http_bind.ip() != IpAddr::V4(LOCAL_RELAY_HOST) || http_bind.port() == 0 {
                return Err(format!(
                    "relay_config: dev relay binds only {LOCAL_RELAY_HOST}:<port> (got {http_bind})"
                ));
            }
            let cfg = RelayConfig { mode: Mode::Dev, http_bind, allow_endpoints };
            // Our own URL must pass the same allowlist parse the Peer/Gateway use.
            guard::RelayAllow::parse(&cfg.url(), lane).map_err(|e| e.log_line())?;
            Ok(cfg)
        }
        "tls" => {
            let need = |k: &str| get(k).ok_or_else(|| format!("relay_config: {k} required for STREAM_RELAY_MODE=tls"));
            let hostname = need("STREAM_RELAY_HOSTNAME")?.to_ascii_lowercase();
            let https_raw = need("STREAM_RELAY_HTTPS_BIND")?;
            let https_bind: SocketAddr = https_raw
                .parse()
                .map_err(|_| format!("relay_config: STREAM_RELAY_HTTPS_BIND={https_raw:?} (ip:port)"))?;
            let http_raw = need("STREAM_RELAY_HTTP_BIND")?;
            let http_bind: SocketAddr = http_raw
                .parse()
                .map_err(|_| format!("relay_config: STREAM_RELAY_HTTP_BIND={http_raw:?} (ip:port)"))?;
            if bad_bind(https_bind.ip()) || bad_bind(http_bind.ip()) {
                return Err("relay_config: bind a specific interface address, never 0.0.0.0/[::]".into());
            }
            let public_port = match get("STREAM_RELAY_PUBLIC_PORT") {
                None => https_bind.port(),
                Some(v) => v
                    .parse::<u16>()
                    .ok()
                    .filter(|p| *p != 0)
                    .ok_or_else(|| format!("relay_config: STREAM_RELAY_PUBLIC_PORT={v:?}"))?,
            };
            let cert = match get("STREAM_RELAY_TLS_CERT_MODE").unwrap_or_else(|| "manual".into()).as_str() {
                "manual" => CertSource::Files { cert: need("STREAM_RELAY_TLS_CERT")?, key: need("STREAM_RELAY_TLS_KEY")? },
                "acme" => CertSource::Acme {
                    cache_dir: need("STREAM_RELAY_ACME_CACHE")?,
                    prod: get("STREAM_RELAY_ACME_PROD").as_deref() == Some("1"),
                    contact: get("STREAM_RELAY_ACME_CONTACT"),
                },
                other => return Err(format!("relay_config: STREAM_RELAY_TLS_CERT_MODE={other:?} (manual|acme)")),
            };
            if allow_endpoints.is_none() {
                return Err("relay_config: tls (pilot) relay requires STREAM_RELAY_ALLOW_ENDPOINTS".into());
            }
            let cfg = RelayConfig { mode: Mode::Tls { hostname, https_bind, public_port, cert }, http_bind, allow_endpoints };
            guard::RelayAllow::parse(&cfg.url(), Lane::Pilot).map_err(|e| e.log_line())?;
            Ok(cfg)
        }
        other => Err(format!("relay_config: STREAM_RELAY_MODE={other:?} (dev|tls)")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env<'a>(kv: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        move |k| kv.iter().find(|(a, _)| *a == k).map(|(_, v)| v.to_string())
    }

    #[test]
    fn dev_only_on_local_lane_and_host() {
        let c = config_from(env(&[]), Lane::Local).unwrap();
        assert_eq!(c.url(), "http://10.73.0.254:3340/");
        assert!(config_from(env(&[]), Lane::Pilot).unwrap_err().contains("a4_local"));
        for b in ["0.0.0.0:3340", "10.73.0.1:3340", "127.0.0.1:3340", "10.73.0.254:0", "nope"] {
            assert!(config_from(env(&[("STREAM_RELAY_HTTP_BIND", b)]), Lane::Local).is_err(), "{b}");
        }
    }

    #[test]
    fn allow_endpoints_parse() {
        let id = "ab".repeat(32);
        let c = config_from(env(&[("STREAM_RELAY_ALLOW_ENDPOINTS", &id)]), Lane::Local).unwrap();
        assert_eq!(c.allow_endpoints, Some(vec![id.clone()]));
        assert!(config_from(env(&[("STREAM_RELAY_ALLOW_ENDPOINTS", "xyz")]), Lane::Local).is_err());
    }

    #[test]
    fn tls_hook_validates() {
        let id = "ab".repeat(32);
        let full = [
            ("STREAM_RELAY_MODE", "tls"),
            ("STREAM_RELAY_HOSTNAME", "relay.example.org"),
            ("STREAM_RELAY_HTTPS_BIND", "203.0.113.5:443"),
            ("STREAM_RELAY_HTTP_BIND", "203.0.113.5:80"),
            ("STREAM_RELAY_TLS_CERT", "/etc/stream-relay/cert.pem"),
            ("STREAM_RELAY_TLS_KEY", "/etc/stream-relay/key.pem"),
            ("STREAM_RELAY_ALLOW_ENDPOINTS", id.as_str()),
        ];
        let c = config_from(env(&full), Lane::Pilot).unwrap();
        assert_eq!(c.url(), "https://relay.example.org:443/");
        let mut n0 = full;
        n0[1] = ("STREAM_RELAY_HOSTNAME", "use1-1.relay.n0.iroh.iroh.link");
        assert!(config_from(env(&n0), Lane::Pilot).unwrap_err().contains("relay_config"));
        let mut wild = full;
        wild[2] = ("STREAM_RELAY_HTTPS_BIND", "0.0.0.0:443");
        assert!(config_from(env(&wild), Lane::Pilot).is_err());
        assert!(config_from(env(&full[..6]), Lane::Pilot).unwrap_err().contains("ALLOW_ENDPOINTS"));
    }

    #[test]
    fn tls_acme_and_public_port() {
        let id = "ab".repeat(32);
        let full = [
            ("STREAM_RELAY_MODE", "tls"),
            ("STREAM_RELAY_HOSTNAME", "relay.93-184-215-14.sslip.io"),
            ("STREAM_RELAY_HTTPS_BIND", "127.0.0.1:8443"),
            ("STREAM_RELAY_HTTP_BIND", "10.0.0.5:80"),
            ("STREAM_RELAY_PUBLIC_PORT", "443"),
            ("STREAM_RELAY_TLS_CERT_MODE", "acme"),
            ("STREAM_RELAY_ACME_CACHE", "/data/acme"),
            ("STREAM_RELAY_ALLOW_ENDPOINTS", id.as_str()),
        ];
        let c = config_from(env(&full), Lane::Pilot).unwrap();
        assert_eq!(c.url(), "https://relay.93-184-215-14.sslip.io:443/");
        assert!(matches!(&c.mode, Mode::Tls { cert: CertSource::Acme { prod: false, .. }, .. }));
        let mut no_cache = full;
        no_cache[6] = ("STREAM_RELAY_ACME_CACHE", "");
        assert!(config_from(env(&no_cache), Lane::Pilot).unwrap_err().contains("ACME_CACHE"));
        let mut bad = full;
        bad[5] = ("STREAM_RELAY_TLS_CERT_MODE", "selfsigned");
        assert!(config_from(env(&bad), Lane::Pilot).is_err());
        let mut port0 = full;
        port0[4] = ("STREAM_RELAY_PUBLIC_PORT", "0");
        assert!(config_from(env(&port0), Lane::Pilot).is_err());
    }
}
