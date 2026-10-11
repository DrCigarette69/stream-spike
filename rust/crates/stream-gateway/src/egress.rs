//! A4.3 (iroh_pilot): public-egress kill switch, per-OPEN second check, DATA pipes, byte cap.
//!
//! `public_egress_effective = SPIKE_PUBLIC_EGRESS==1 AND Control flag on AND fresh (≤ max age)`,
//! plus Control's `allowlist_version` == ours. Polled every 1 s from Control
//! `GET /v1/egress/state`; forwarded to Peers as `EGRESS_STATE` while fresh. Any off state closes
//! every pilot stream (`CLOSE {reason, detail}`) within one watchdog tick (200 ms).
//! Only std + tokio + stream-proto here (no iroh types), so it compiles in every build;
//! it is only *constructed* in iroh_pilot mode.
#![cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use stream_proto::guard::{self, EgressAllow, EgressSwitch, GuardError};
use stream_proto::relay::{Close, Data, EgressState, MAX_DATA_CHUNK};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::{mpsc, watch};

use crate::state::AppState;

pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Why a pilot stream ended: (reason, detail, from_peer).
pub type CloseWhy = (String, Option<String>, bool);

pub struct PilotStream {
    pub peer_id: String,
    /// Authenticated iroh EndpointId of that Peer's connection (DATA/CLOSE must come from it).
    pub endpoint_id: String,
    pub dest: String,
    pub to_client: mpsc::Sender<Vec<u8>>,
    pub close: watch::Sender<Option<CloseWhy>>,
}

pub struct Pilot {
    pub allow: EgressAllow,
    pub version: String,
    pub cap: u64,
    pub switch: Mutex<EgressSwitch>,
    /// Last fresh Control state (flag, version, control ts) — what we forward as EGRESS_STATE.
    pub last: Mutex<Option<(bool, String, f64)>>,
    pub streams: Mutex<HashMap<String, PilotStream>>,
    /// peer_id -> (utc day, bytes) — the authoritative ledger number for the budget.
    pub used: Mutex<HashMap<String, (u64, u64)>>,
    /// peer_id -> utc day on which a stream hit the cap (no new OPENs that day).
    pub exhausted: Mutex<HashMap<String, u64>>,
    /// Closed streams, newest last (metadata only), for /gw/pilot.
    pub closed: Mutex<Vec<Value>>,
}

impl Pilot {
    /// Fails closed: no/invalid allowlist -> `egress_allowlist_config` (Gateway refuses to start).
    pub fn from_env() -> Result<Self, GuardError> {
        let allow = guard::egress_allow_from_env()?;
        let version = allow.version();
        Ok(Self {
            allow,
            version,
            cap: guard::egress_byte_cap_from_env(),
            switch: Mutex::new(EgressSwitch::from_env()),
            last: Mutex::new(None),
            streams: Mutex::new(HashMap::new()),
            used: Mutex::new(HashMap::new()),
            exhausted: Mutex::new(HashMap::new()),
            closed: Mutex::new(Vec::new()),
        })
    }

    pub fn status(&self) -> Result<(), GuardError> {
        self.switch.lock().unwrap().check_open(now_ms(), &self.version)
    }

    /// Per-OPEN second check, before anything reaches the Peer: kill switch → allowlist
    /// (guard API only; the Gateway never resolves the name itself) → budget.
    pub fn check_open(&self, peer_id: &str, host: &str, port: u16) -> Result<String, GuardError> {
        self.status()?;
        let h = guard::check_egress_dest(host, port, &self.allow)?;
        let day = now_ms() / 86_400_000;
        if self.exhausted.lock().unwrap().get(peer_id) == Some(&day) {
            return Err(GuardError { reason: guard::REASON_EGRESS_BUDGET_EXCEEDED, detail: format!("{peer_id} cap={}", self.cap) });
        }
        guard::check_egress_budget(self.used_today(peer_id), 1, self.cap)?;
        Ok(h)
    }

    fn used_today(&self, peer_id: &str) -> u64 {
        let day = now_ms() / 86_400_000;
        match self.used.lock().unwrap().get(peer_id) {
            Some((d, n)) if *d == day => *n,
            _ => 0,
        }
    }

    /// Count `n` bytes; Err once the per-Peer UTC-day cap would be exceeded.
    fn charge(&self, peer_id: &str, n: u64) -> Result<(), GuardError> {
        let day = now_ms() / 86_400_000;
        let mut m = self.used.lock().unwrap();
        let e = m.entry(peer_id.to_string()).or_insert((day, 0));
        if e.0 != day {
            *e = (day, 0);
        }
        if let Err(err) = guard::check_egress_budget(e.1, n, self.cap) {
            self.exhausted.lock().unwrap().insert(peer_id.to_string(), day);
            return Err(err);
        }
        e.1 += n;
        Ok(())
    }

    pub fn snapshot(&self) -> Value {
        let st = self.status();
        json!({
            "public_egress_effective": st.is_ok(),
            "reason": st.as_ref().err().map(|e| e.reason()),
            "reason_detail": st.as_ref().err().map(|e| e.detail.clone()),
            "allowlist_version": self.version,
            "allowlist": self.allow.entries().iter().map(|(h, p)| format!("{h}:{p}")).collect::<Vec<_>>(),
            "control_state": self.last.lock().unwrap().as_ref().map(|(on, v, ts)| json!({"public_egress": on, "allowlist_version": v, "ts": ts})),
            "byte_cap": self.cap,
            "used": self.used.lock().unwrap().iter().map(|(k, v)| (k.clone(), json!(v.1))).collect::<serde_json::Map<_, _>>(),
            "open_streams": self.streams.lock().unwrap().iter().map(|(k, v)| json!({"stream_id": k, "peer_id": v.peer_id, "dest": v.dest})).collect::<Vec<_>>(),
            "closed": self.closed.lock().unwrap().clone(),
        })
    }

    /// Signal one stream to end (idempotent: first reason wins).
    pub fn close_stream(&self, sid: &str, why: CloseWhy) {
        if let Some(s) = self.streams.lock().unwrap().get(sid) {
            s.close.send_if_modified(|cur| {
                if cur.is_none() {
                    *cur = Some(why.clone());
                    true
                } else {
                    false
                }
            });
        }
    }

    pub fn close_all(&self, reason: &str, detail: Option<&str>, only_endpoint: Option<&str>) -> usize {
        let sids: Vec<String> = self
            .streams
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, s)| only_endpoint.map(|e| e == s.endpoint_id).unwrap_or(true))
            .map(|(k, _)| k.clone())
            .collect();
        for sid in &sids {
            self.close_stream(sid, (reason.into(), detail.map(String::from), false));
        }
        sids.len()
    }
}

/// Write one NDJSON frame to a Peer (serialised on the Peer's socket lock).
pub async fn send_frame(st: &AppState, peer_id: &str, v: &impl serde::Serialize) -> Result<(), String> {
    let meta = st.peers.read().await.get(peer_id).cloned().ok_or("peer_offline")?;
    let line = stream_proto::ndjson_line(v).map_err(|e| e.to_string())?;
    let mut g = meta.sock.lock().await;
    g.write_all(&line).await.map_err(|e| e.to_string())?;
    g.flush().await.map_err(|e| e.to_string())
}

/// Poll Control every 1 s; fresh state -> switch.update + EGRESS_STATE to every Peer.
/// Unreachable / non-200 / malformed / skewed ts -> no refresh (goes stale = off).
pub fn spawn_poller(st: AppState, p: Arc<Pilot>) {
    tokio::spawn(async move {
        let http = reqwest::Client::builder().timeout(Duration::from_millis(900)).build().expect("reqwest");
        let url = format!("{}/v1/egress/state", st.control_url.trim_end_matches('/'));
        let max_age = guard::egress_state_max_age_ms_from_env();
        loop {
            let tick = tokio::time::Instant::now();
            if let Some((on, ver, ts)) = fetch(&http, &url).await {
                let now = now_ms();
                let skew = (ts * 1000.0 - now as f64).abs();
                if skew <= max_age as f64 {
                    p.switch.lock().unwrap().update(on, &ver, now);
                    *p.last.lock().unwrap() = Some((on, ver.clone(), ts));
                    let frame = EgressState::new(on, ver, ts);
                    let peers: Vec<String> = st.peers.read().await.keys().cloned().collect();
                    for pid in peers {
                        let (st2, f) = (st.clone(), frame.clone());
                        tokio::spawn(async move {
                            let _ = tokio::time::timeout(Duration::from_secs(1), send_frame(&st2, &pid, &f)).await;
                        });
                    }
                } else {
                    tracing::warn!("egress state ts skew {skew:.0} ms > {max_age}: ignored (stale = off)");
                }
            }
            tokio::time::sleep_until(tick + Duration::from_secs(1)).await;
        }
    });
}

async fn fetch(http: &reqwest::Client, url: &str) -> Option<(bool, String, f64)> {
    let r = http.get(url).send().await.ok()?;
    if !r.status().is_success() {
        return None;
    }
    let v: Value = r.json().await.ok()?;
    Some((v.get("public_egress")?.as_bool()?, v.get("allowlist_version")?.as_str()?.to_string(), v.get("ts")?.as_f64()?))
}

/// Every 200 ms: if egress is not effective, close all pilot streams with its reason/detail.
pub fn spawn_watchdog(p: Arc<Pilot>) {
    tokio::spawn(async move {
        let mut was: Option<String> = None;
        loop {
            let now = p.status().err().map(|e| (e.reason(), e.detail.clone(), e.log_line()));
            let key = now.as_ref().map(|n| n.2.clone());
            if key != was {
                match &now {
                    Some((_, _, line)) => {
                        tracing::info!("egress off: {line}");
                        println!("A43_EGRESS_OFF {line}");
                    }
                    None => println!("A43_EGRESS_ON allowlist_version={}", p.version),
                }
                was = key;
            }
            if let Some((reason, detail, _)) = now {
                // egress_allowlist_mismatch detail carries both hashes; keep it short on the wire.
                let d = if reason == guard::REASON_EGRESS_OFF { Some(detail.as_str()) } else { None };
                let n = p.close_all(reason, d, None);
                if n > 0 {
                    println!("A43_CLOSE_ALL n={n} reason={reason} detail={}", d.unwrap_or("-"));
                }
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    });
}

/// Peer → Gateway frames that are not request/response: DATA, CLOSE. Returns true if handled.
pub fn dispatch_from_peer(p: &Pilot, remote: &str, v: &Value) -> bool {
    let t = v.get("type").and_then(Value::as_str).unwrap_or("");
    let sid = v.get("stream_id").and_then(Value::as_str).unwrap_or("");
    match t {
        "DATA" => {
            let owned = p.streams.lock().unwrap().get(sid).filter(|s| s.endpoint_id == remote).map(|s| s.to_client.clone());
            let raw = serde_json::from_value::<Data>(v.clone()).ok().and_then(|d| d.bytes());
            match (owned, raw) {
                (Some(tx), Some(raw)) => {
                    if tx.try_send(raw).is_err() {
                        p.close_stream(sid, ("gateway_backpressure".into(), None, false));
                    }
                }
                (Some(_), None) => p.close_stream(sid, ("bad_data_frame".into(), None, false)),
                _ => {}
            }
            true
        }
        "CLOSE" => {
            let reason = v.get("reason").and_then(Value::as_str).unwrap_or("peer_closed").to_string();
            let detail = v.get("detail").and_then(Value::as_str).map(String::from);
            let mine = p.streams.lock().unwrap().get(sid).map(|s| s.endpoint_id == remote).unwrap_or(false);
            if mine {
                tracing::info!("peer CLOSE {sid} reason={reason}");
                p.close_stream(sid, (reason, detail, true));
            }
            true
        }
        _ => false,
    }
}

/// Register a pilot stream and bind its loopback client pipe (one TCP client; bytes ↔ DATA).
/// Returns the pipe address. Loopback only: the client surface is never reachable off-host.
pub async fn start_pipe(
    st: AppState,
    p: Arc<Pilot>,
    peer_id: String,
    endpoint_id: String,
    sid: String,
    dest: String,
) -> std::io::Result<std::net::SocketAddr> {
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await?;
    let addr = listener.local_addr()?;
    let (to_client, mut from_peer) = mpsc::channel::<Vec<u8>>(256);
    let (close_tx, mut close_rx) = watch::channel::<Option<CloseWhy>>(None);
    p.streams.lock().unwrap().insert(sid.clone(), PilotStream { peer_id: peer_id.clone(), endpoint_id, dest: dest.clone(), to_client, close: close_tx });
    tokio::spawn(async move {
        let (mut up, mut down) = (0u64, 0u64);
        let accepted = tokio::select! {
            r = tokio::time::timeout(Duration::from_secs(30), listener.accept()) => r.ok().and_then(|r| r.ok()),
            _ = async { let _ = close_rx.wait_for(|c| c.is_some()).await; } => None,
        };
        drop(listener);
        let mut why: CloseWhy = ("client_timeout".into(), None, false);
        if let Some((mut cli, _)) = accepted {
            let mut buf = vec![0u8; MAX_DATA_CHUNK];
            let mut close_wait = close_rx.clone();
            let early = close_rx.borrow().clone();
            why = loop {
                if let Some(w) = &early {
                    break w.clone();
                }
                tokio::select! {
                    r = cli.read(&mut buf) => match r {
                        Ok(0) | Err(_) => break ("client_closed".into(), None, false),
                        Ok(n) => {
                            if let Err(e) = p.charge(&peer_id, n as u64) {
                                break (e.reason().into(), None, false);
                            }
                            up += n as u64;
                            if let Err(e) = send_frame(&st, &peer_id, &Data::new(&sid, &buf[..n])).await {
                                break ("peer_send_failed".into(), Some(e), false);
                            }
                        }
                    },
                    m = from_peer.recv() => match m {
                        None => break ("peer_gone".into(), None, false),
                        Some(raw) => {
                            if let Err(e) = p.charge(&peer_id, raw.len() as u64) {
                                break (e.reason().into(), None, false);
                            }
                            down += raw.len() as u64;
                            if cli.write_all(&raw).await.is_err() {
                                break ("client_closed".into(), None, false);
                            }
                        }
                    },
                    c = close_wait.changed() => {
                        let why = close_rx.borrow().clone();
                        match (c, why) {
                            (_, Some(w)) => break w,
                            (Err(_), None) => break ("closed".into(), None, false),
                            (Ok(()), None) => {}
                        }
                    }
                }
            };
            // Drain site bytes that already arrived before a peer-side close.
            if why.2 {
                while let Ok(raw) = from_peer.try_recv() {
                    down += raw.len() as u64;
                    let _ = cli.write_all(&raw).await;
                }
            }
            let _ = cli.shutdown().await;
        }
        p.streams.lock().unwrap().remove(&sid);
        if !why.2 {
            let f = Close::with_reason(&sid, &why.0, why.1.as_deref());
            let _ = tokio::time::timeout(Duration::from_secs(2), send_frame(&st, &peer_id, &f)).await;
        }
        if let Some(s) = st.streams.write().await.get_mut(&sid) {
            s.stop = true;
        }
        let by = if why.2 { "peer" } else { "gateway" };
        // Metadata only (ALPHA4_PILOT.md): who, when, dest, bytes, reason. Never contents.
        let line = json!({"stream_id": sid, "peer_id": peer_id, "dest": dest, "bytes_up": up, "bytes_down": down,
                          "reason": why.0, "detail": why.1, "closed_by": by, "ts_ms": now_ms()});
        println!("a4_egress_log {line}");
        p.closed.lock().unwrap().push(line);
        let (host, port) = dest.rsplit_once(':').unwrap_or((dest.as_str(), "443"));
        let _ = crate::control::usage_flush(&st, json!({"stream_id": sid, "bytes": up + down, "dest_host": host,
                                                        "dest_port": port.parse::<u16>().unwrap_or(443)})).await;
    });
    Ok(addr)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pilot(cap: u64) -> Pilot {
        let allow = EgressAllow::parse("echo.example.com:443").unwrap();
        Pilot {
            version: allow.version(),
            allow,
            cap,
            switch: Mutex::new(EgressSwitch::new(true, 5000)),
            last: Mutex::new(None),
            streams: Mutex::new(HashMap::new()),
            used: Mutex::new(HashMap::new()),
            exhausted: Mutex::new(HashMap::new()),
            closed: Mutex::new(Vec::new()),
        }
    }

    #[test]
    fn open_order_switch_then_allowlist_then_budget() {
        let p = pilot(100);
        assert_eq!(p.check_open("p", "echo.example.com", 443).unwrap_err().detail, "no_state");
        let v = p.version.clone();
        p.switch.lock().unwrap().update(false, &v, now_ms());
        assert_eq!(p.check_open("p", "echo.example.com", 443).unwrap_err().detail, "flag_off");
        p.switch.lock().unwrap().update(true, "other", now_ms());
        assert_eq!(p.check_open("p", "echo.example.com", 443).unwrap_err().reason, guard::REASON_EGRESS_ALLOWLIST_MISMATCH);
        p.switch.lock().unwrap().update(true, &v, now_ms());
        assert_eq!(p.check_open("p", "x.example.com", 443).unwrap_err().reason, guard::REASON_EGRESS_NOT_ALLOWLISTED);
        assert_eq!(p.check_open("p", "198.51.100.10", 443).unwrap_err().reason, guard::REASON_EGRESS_NOT_ALLOWLISTED);
        assert!(p.check_open("p", "Echo.Example.com.", 443).is_ok());
        assert!(p.charge("p", 60).is_ok());
        assert_eq!(p.charge("p", 60).unwrap_err().reason, guard::REASON_EGRESS_BUDGET_EXCEEDED);
        assert_eq!(p.check_open("p", "echo.example.com", 443).unwrap_err().reason, guard::REASON_EGRESS_BUDGET_EXCEEDED);
        assert!(p.check_open("q", "echo.example.com", 443).is_ok(), "budget is per Peer");
    }

    #[test]
    fn peer_frames_only_from_the_owning_endpoint() {
        let p = pilot(1 << 20);
        let (tx, mut rx) = mpsc::channel(4);
        let (ctx, crx) = watch::channel(None);
        p.streams.lock().unwrap().insert(
            "s1".into(),
            PilotStream { peer_id: "p".into(), endpoint_id: "ep_a".into(), dest: "echo.example.com:443".into(), to_client: tx, close: ctx },
        );
        let data = serde_json::to_value(Data::new("s1", b"hi")).unwrap();
        assert!(dispatch_from_peer(&p, "ep_b", &data));
        assert!(rx.try_recv().is_err(), "other endpoint's DATA dropped");
        dispatch_from_peer(&p, "ep_a", &data);
        assert_eq!(rx.try_recv().unwrap(), b"hi");
        let close = json!({"type": "CLOSE", "stream_id": "s1", "reason": "egress_off"});
        dispatch_from_peer(&p, "ep_b", &close);
        assert!(crx.borrow().is_none());
        dispatch_from_peer(&p, "ep_a", &close);
        assert_eq!(crx.borrow().clone().unwrap(), ("egress_off".to_string(), None, true));
        assert!(!dispatch_from_peer(&p, "ep_a", &json!({"type": "AUTH_OK"})));
    }
}
