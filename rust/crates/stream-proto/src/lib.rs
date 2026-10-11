//! Shared Stream tunnel contract (spike / Alpha-2).
//! ALPN + AUTH_TICKET helpers + fake-relay NDJSON frame shapes for Peer/Gateway.

use serde::{Deserialize, Serialize};

/// A3.0 private-address guard (Alpha-3 `iroh_local`), std only.
pub mod guard;

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

    /// `{"type":"CLOSE","stream_id":..}`; A4.3 adds optional `reason` / `detail` (either
    /// direction; Peer→Gateway e.g. `egress_off`, Gateway→Peer e.g. `egress_off` + `flag_off`).
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Close {
        #[serde(rename = "type")]
        pub type_: String,
        pub stream_id: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        pub reason: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        pub detail: Option<String>,
    }

    impl Close {
        pub fn new(stream_id: impl Into<String>) -> Self {
            Self {
                type_: "CLOSE".into(),
                stream_id: stream_id.into(),
                reason: None,
                detail: None,
            }
        }

        pub fn with_reason(stream_id: impl Into<String>, reason: &str, detail: Option<&str>) -> Self {
            Self {
                reason: Some(reason.into()),
                detail: detail.map(String::from),
                ..Self::new(stream_id)
            }
        }
    }

    /// A4.3 data frame (iroh_pilot): `{"type":"DATA","stream_id":..,"b64":"<standard base64>"}`.
    /// Gateway→Peer = client→site bytes, Peer→Gateway = site→client bytes. At most
    /// [`MAX_DATA_CHUNK`] raw bytes per frame. `BYTES` stays the byte-count frame (A1–A3).
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct Data {
        #[serde(rename = "type")]
        pub type_: String,
        pub stream_id: String,
        pub b64: String,
    }

    pub const MAX_DATA_CHUNK: usize = 16 * 1024;

    impl Data {
        pub fn new(stream_id: impl Into<String>, raw: &[u8]) -> Self {
            Self { type_: "DATA".into(), stream_id: stream_id.into(), b64: crate::b64::encode(raw) }
        }

        pub fn bytes(&self) -> Option<Vec<u8>> {
            crate::b64::decode(&self.b64).filter(|b| b.len() <= MAX_DATA_CHUNK)
        }
    }

    /// A4.3 kill-switch frame, Gateway→Peer every ~1 s while Control's state is fresh:
    /// `{"type":"EGRESS_STATE","public_egress":bool,"allowlist_version":"<sha256>","ts":<unix s>}`
    /// (same fields as Control `GET /v1/egress/state`). Not sent when Control is stale/unreachable.
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct EgressState {
        #[serde(rename = "type")]
        pub type_: String,
        pub public_egress: bool,
        pub allowlist_version: String,
        pub ts: f64,
    }

    impl EgressState {
        pub fn new(public_egress: bool, allowlist_version: impl Into<String>, ts: f64) -> Self {
            Self { type_: "EGRESS_STATE".into(), public_egress, allowlist_version: allowlist_version.into(), ts }
        }
    }
}

/// Std-only standard base64 (RFC 4648, padded) for the A4.3 `DATA` frame.
pub mod b64 {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    pub fn encode(raw: &[u8]) -> String {
        let mut out = String::with_capacity(raw.len().div_ceil(3) * 4);
        for c in raw.chunks(3) {
            let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
            out.push(A[(n >> 18) as usize & 63] as char);
            out.push(A[(n >> 12) as usize & 63] as char);
            out.push(if c.len() > 1 { A[(n >> 6) as usize & 63] as char } else { '=' });
            out.push(if c.len() > 2 { A[n as usize & 63] as char } else { '=' });
        }
        out
    }

    fn val(c: u8) -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        } as u32)
    }

    /// Strict: padded length, valid alphabet; None otherwise.
    pub fn decode(s: &str) -> Option<Vec<u8>> {
        let b = s.as_bytes();
        if b.len() % 4 != 0 {
            return None;
        }
        let mut out = Vec::with_capacity(b.len() / 4 * 3);
        for (i, q) in b.chunks(4).enumerate() {
            let last = i == b.len() / 4 - 1;
            let pad = q.iter().rev().take_while(|&&c| c == b'=').count();
            if pad > 2 || (pad > 0 && !last) {
                return None;
            }
            let mut n = 0u32;
            for (j, &c) in q.iter().enumerate() {
                let v = if j >= 4 - pad { 0 } else { val(c)? };
                n = n << 6 | v;
            }
            out.push((n >> 16) as u8);
            if pad < 2 {
                out.push((n >> 8) as u8);
            }
            if pad < 1 {
                out.push(n as u8);
            }
        }
        Some(out)
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn roundtrip_and_vectors() {
            for (raw, enc) in [("", ""), ("f", "Zg=="), ("fo", "Zm8="), ("foo", "Zm9v"), ("foobar", "Zm9vYmFy")] {
                assert_eq!(encode(raw.as_bytes()), enc);
                assert_eq!(decode(enc).unwrap(), raw.as_bytes());
            }
            let all: Vec<u8> = (0..=255u8).collect();
            assert_eq!(decode(&encode(&all)).unwrap(), all);
            for bad in ["Zg=", "Z===", "Zg==Zg==", "Zm9!", "@@@@"] {
                assert!(decode(bad).is_none(), "{bad}");
            }
        }
    }
}
