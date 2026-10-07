//! Shared Stream tunnel contract (spike / Alpha-2).
//! ALPN + AUTH_TICKET helpers + fake-relay NDJSON frame shapes for Peer/Gateway.

use serde::{Deserialize, Serialize};

pub const ALPN: &str = "stream/tunnel/1";

/// Hardening features advertised by Gateway `/health`.
pub const HARDENING: &[&str] = &["denylist_midstream", "freeze_stop", "ticket_alpn"];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthTicket {
    pub session_id: String,
    pub stream_id: String,
    pub peer_id: String,
    /// HMAC hex from Control mint (verified by Peer/Gateway).
    pub mac: String,
}

impl AuthTicket {
    pub fn to_first_frame(&self) -> Result<Vec<u8>, serde_json::Error> {
        let mut v = serde_json::to_vec(self)?;
        v.push(b'\n');
        Ok(v)
    }
}

/// Encode any JSON-serializable value as a single NDJSON line (trailing `\n`).
pub fn ndjson_line<T: Serialize>(value: &T) -> Result<Vec<u8>, serde_json::Error> {
    let mut v = serde_json::to_vec(value)?;
    v.push(b'\n');
    Ok(v)
}

/// Fake-relay NDJSON frames (Peer \u2194 Gateway). See docs/FAKE_RELAY_PROTOCOL.md.
pub mod relay {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Hello {
        #[serde(rename = "type")]
        pub type_: String,
        pub peer_id: String,
        #[serde(default)]
        pub endpoint_id: String,
        #[serde(default)]
        pub isp_ack_version: String,
        #[serde(default = "default_host_tier")]
        pub host_tier: String,
    }

    fn default_host_tier() -> String {
        "casual".into()
    }

    impl Hello {
        pub fn new(
            peer_id: impl Into<String>,
            endpoint_id: impl Into<String>,
            isp_ack_version: impl Into<String>,
            host_tier: impl Into<String>,
        ) -> Self {
            Self {
                type_: "HELLO".into(),
                peer_id: peer_id.into(),
                endpoint_id: endpoint_id.into(),
                isp_ack_version: isp_ack_version.into(),
                host_tier: host_tier.into(),
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct HelloOk {
        #[serde(rename = "type")]
        pub type_: String,
        pub alpn: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub transport: Option<String>,
    }

    impl HelloOk {
        pub fn new(alpn: impl Into<String>, transport: impl Into<String>) -> Self {
            Self {
                type_: "HELLO_OK".into(),
                alpn: alpn.into(),
                transport: Some(transport.into()),
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct ErrFrame {
        #[serde(rename = "type")]
        pub type_: String,
        pub error: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub code: Option<String>,
    }

    impl ErrFrame {
        pub fn new(error: impl Into<String>, code: Option<&str>) -> Self {
            Self {
                type_: "ERR".into(),
                error: error.into(),
                code: code.map(|s| s.to_string()),
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct AuthTicketFrame {
        #[serde(rename = "type")]
        pub type_: String,
        pub alpn: String,
        pub ticket_json: String,
        pub stream_id: String,
        pub dest_host: String,
        pub dest_port: u16,
    }

    impl AuthTicketFrame {
        pub fn new(
            alpn: impl Into<String>,
            ticket_json: impl Into<String>,
            stream_id: impl Into<String>,
            dest_host: impl Into<String>,
            dest_port: u16,
        ) -> Self {
            Self {
                type_: "AUTH_TICKET".into(),
                alpn: alpn.into(),
                ticket_json: ticket_json.into(),
                stream_id: stream_id.into(),
                dest_host: dest_host.into(),
                dest_port,
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct AuthOk {
        #[serde(rename = "type")]
        pub type_: String,
        #[serde(default)]
        pub egress_denied: bool,
        #[serde(default)]
        pub error: Option<String>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Open {
        #[serde(rename = "type")]
        pub type_: String,
        pub stream_id: String,
    }

    impl Open {
        pub fn new(stream_id: impl Into<String>) -> Self {
            Self {
                type_: "OPEN".into(),
                stream_id: stream_id.into(),
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Bytes {
        #[serde(rename = "type")]
        pub type_: String,
        pub stream_id: String,
        pub n: u64,
    }

    impl Bytes {
        pub fn new(stream_id: impl Into<String>, n: u64) -> Self {
            Self {
                type_: "BYTES".into(),
                stream_id: stream_id.into(),
                n,
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Close {
        #[serde(rename = "type")]
        pub type_: String,
        pub stream_id: String,
    }

    impl Close {
        pub fn new(stream_id: impl Into<String>) -> Self {
            Self {
                type_: "CLOSE".into(),
                stream_id: stream_id.into(),
            }
        }
    }
}
