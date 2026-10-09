//! Shared peer runtime state (snapshot keys match Python `/peer/status`).
use crate::config::Config;
use crate::ux::{self, p1_ux, p4_ux};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{Mutex, RwLock};

#[derive(Debug, Clone, Default)]
pub struct StreamInfo {
    pub bytes: u64,
    pub closed: bool,
    pub closed_by: Option<String>,
    pub dest_host: String,
    pub dest_port: u16,
    pub opened: bool,
}

#[derive(Debug)]
pub struct PeerState {
    pub cfg: Config,
    pub isp_ack_version: String,
    pub host_tier: String,
    pub online: bool,
    pub connected: bool,
    pub streams: HashMap<String, StreamInfo>,
    pub early_cut_attempts: u64,
    pub auth_rejects: u64,
    pub last_error: String,
    pub kill_requested: bool,
    pub ux_status: String,
    pub ux_screen: Option<String>,
    /// When set, relay loop should drop the current socket (kill / clear-ack).
    pub force_disconnect: bool,
    /// A3.2: P2 consent accepted -- its own gate after P1 (`POST /peer/consent`
    /// or `SPIKE_P2_CONSENT=1` + ack). Only gates iroh_local dials.
    pub p2_consent: bool,
}

impl PeerState {
    pub fn new(cfg: Config) -> Self {
        let ack = cfg.isp_ack_version.clone();
        let mut st = Self {
            host_tier: cfg.host_tier.clone(),
            isp_ack_version: ack.clone(),
            cfg,
            online: false,
            connected: false,
            streams: HashMap::new(),
            early_cut_attempts: 0,
            auth_rejects: 0,
            last_error: String::new(),
            kill_requested: false,
            ux_status: String::new(),
            ux_screen: None,
            force_disconnect: false,
            p2_consent: false,
        };
        if ack.is_empty() {
            let _ = st.set_ux(Some("P1"), ux::p1_user_facing());
        } else if st.cfg.p2_consent_preset {
            st.p2_consent = true;
        }
        st
    }

    /// Set / clear the ISP ack. Clearing P1 always clears P2.
    pub fn set_isp_ack(&mut self, version: &str) {
        self.isp_ack_version = version.to_string();
        if version.is_empty() {
            self.p2_consent = false;
        }
    }

    /// P2 accept / withdraw. Accept requires P1 first.
    pub fn set_p2_consent(&mut self, accepted: bool) -> Result<(), &'static str> {
        if accepted && self.isp_ack_version.is_empty() {
            return Err("p1_ack_required");
        }
        self.p2_consent = accepted;
        Ok(())
    }

    pub fn set_ux(&mut self, screen: Option<&str>, text: &str) -> Result<(), String> {
        ux::assert_no_forbidden(text)?;
        self.ux_screen = screen.map(|s| s.to_string());
        self.ux_status = text.to_string();
        Ok(())
    }

    pub fn snapshot(&self) -> Value {
        let killed = self.kill_requested;
        let mut streams = Map::new();
        for (k, v) in &self.streams {
            streams.insert(
                k.clone(),
                json!({
                    "bytes": v.bytes,
                    "closed": v.closed,
                    "closed_by": v.closed_by,
                    "dest_host": v.dest_host,
                    "dest_port": v.dest_port,
                    "opened": v.opened,
                }),
            );
        }
        let sharing_status = if killed {
            ux::p4_status_after_kill().to_string()
        } else if self.connected {
            "Sharing".to_string()
        } else {
            "Offline".to_string()
        };
        let mut snap = json!({
            "peer_id": self.cfg.peer_id,
            "endpoint_id": self.cfg.endpoint_id,
            "isp_ack_version": self.isp_ack_version,
            "host_tier": self.host_tier,
            "online": self.online,
            "connected": self.connected,
            "streams": Value::Object(streams),
            "early_cut_attempts": self.early_cut_attempts,
            "auth_rejects": self.auth_rejects,
            "last_error": self.last_error,
            "alpn": self.cfg.alpn,
            "transport": self.cfg.transport,
            "relay_dial": self.cfg.relay_dial,
            "kill_requested": killed,
            "ux_screen": self.ux_screen,
            "ux_status": self.ux_status,
            "sharing_status": sharing_status,
            "p1": p1_ux(),
            "p4": p4_ux(),
        });
        if self.isp_ack_version.is_empty() {
            snap["ux_prompt"] = json!(ux::p1_user_facing());
        }
        if killed {
            snap["ux_status"] = json!(ux::p4_user_facing());
            snap["ux_screen"] = json!("P4");
        }
        snap
    }
}

pub type SharedState = Arc<RwLock<PeerState>>;

/// Holder for the live relay TCP half-close signal.
#[derive(Clone, Default)]
pub struct RelayHandle {
    pub close_tx: Arc<Mutex<Option<tokio::sync::oneshot::Sender<()>>>>,
}

impl RelayHandle {
    pub async fn request_close(&self) {
        let mut g = self.close_tx.lock().await;
        if let Some(tx) = g.take() {
            let _ = tx.send(());
        }
    }
}
