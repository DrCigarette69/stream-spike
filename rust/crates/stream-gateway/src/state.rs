//! Shared peer + stream registries.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{Mutex, RwLock};

#[derive(Clone)]
pub struct PeerMeta {
    pub sock: Arc<Mutex<crate::conn::PeerConn>>,
    /// A3.1: authenticated iroh remote EndpointId (None on TCP transports).
    pub auth_endpoint_id: Option<String>,
    pub endpoint_id: String,
    pub isp_ack_version: String,
    pub host_tier: String,
    pub dead: Arc<std::sync::atomic::AtomicBool>,
}

#[derive(Clone, Debug)]
#[allow(dead_code)]
pub struct StreamMeta {
    pub stop: bool,
    pub peer_id: String,
    pub label: Option<String>,
    pub dest_host: String,
    pub dest_port: u16,
    pub denylist_version: Option<serde_json::Value>,
}

#[derive(Clone, Default)]
pub struct AppState {
    pub peers: Arc<RwLock<HashMap<String, PeerMeta>>>,
    pub streams: Arc<RwLock<HashMap<String, StreamMeta>>>,
    pub control: reqwest::Client,
    pub control_url: String,
    pub transport: String,
    pub relay_listen: String,
    /// A3.1: this Gateway's iroh EndpointId (iroh_local only).
    pub gateway_endpoint_id: Option<String>,
}

impl AppState {
    pub fn new(control_url: String, transport: String, relay_listen: String) -> Self {
        Self {
            peers: Arc::new(RwLock::new(HashMap::new())),
            streams: Arc::new(RwLock::new(HashMap::new())),
            control: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(5))
                .build()
                .expect("reqwest client"),
            control_url,
            transport,
            relay_listen,
            gateway_endpoint_id: None,
        }
    }
}
