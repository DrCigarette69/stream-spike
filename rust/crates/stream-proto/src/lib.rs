//! Shared Stream tunnel contract (spike / Alpha-2).
pub const ALPN: &str = "stream/tunnel/1";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
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
