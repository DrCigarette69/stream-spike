//! A4.4 part 1: pilot egress config, Control flag freshness (poller + the
//! Gateway's `EGRESS_STATE` frame, same shape) and the kill-switch watchdog.
use crate::config::{self, Config};
use crate::pilot_egress::{EgressPlane, SharedPlane};
use stream_proto::guard::{
    egress_byte_cap_from_env, egress_state_max_age_ms_from_env, EgressAllow, EgressSwitch,
    ENV_EGRESS_ALLOWLIST,
};
use crate::state::SharedState;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use tokio::time::Duration;

pub const ENV_STATE_URL: &str = "SPIKE_EGRESS_STATE_URL";
const P8_EGRESS: [&str; 5] = [
    "egress_off",
    "egress_state_stale",
    "egress_budget_exceeded",
    "egress_budget_unreadable",
    "egress_allowlist_mismatch",
];

/// Startup config check. `Ok(None)` outside iroh_pilot (Alpha-3 floor only).
/// Any error refuses start with the guard's log line
/// (`a4_refuse_egress_allowlist_config:<detail>`). Outside iroh_pilot the env is
/// ignored (Alpha-3 unchanged).
pub fn build_plane_from(
    cfg: &Config,
    allowlist: Option<&str>,
    budget: Option<std::path::PathBuf>,
) -> Result<Option<SharedPlane>, String> {
    if !config::is_iroh_pilot(&cfg.transport) {
        return Ok(None);
    }
    let allow = EgressAllow::parse(allowlist.unwrap_or("")).map_err(|e| e.log_line())?;
    let max_age = egress_state_max_age_ms_from_env();
    let switch = EgressSwitch::new(cfg.public_egress, max_age);
    let mut plane = EgressPlane::new(allow, switch, max_age, egress_byte_cap_from_env());
    plane.max_streams = crate::pilot_egress::max_streams_from(
        std::env::var(crate::pilot_egress::ENV_MAX_STREAMS).ok().as_deref(),
    );
    plane.relay_allow = stream_proto::guard::relay_allow_from_env(stream_proto::guard::Lane::build())
        .map_err(|e| e.log_line())?;
    if let Some(b) = budget {
        plane.attach_store(b);
    }
    Ok(Some(Arc::new(Mutex::new(plane))))
}

pub fn build_plane(cfg: &Config) -> Result<Option<SharedPlane>, String> {
    build_plane_from(
        cfg,
        std::env::var(ENV_EGRESS_ALLOWLIST).ok().as_deref(),
        Some(crate::egress_budget::budget_path(&cfg.iroh_key_path)),
    )
}

pub fn state_url(cfg: &Config) -> String {
    std::env::var(ENV_STATE_URL).unwrap_or_else(|_| format!("{}/v1/egress/state", cfg.control_url))
}

/// Apply `{"public_egress": bool, "allowlist_version": "<sha256>", "ts": ..}`.
/// Malformed -> ignored (no refresh, so it goes stale = off). Returns accepted.
pub fn apply_state(plane: &SharedPlane, v: &Value) -> bool {
    let Some(on) = v.get("public_egress").and_then(Value::as_bool) else { return false };
    let ver = v.get("allowlist_version").and_then(Value::as_str).unwrap_or("");
    plane.lock().unwrap().update_state(on, ver);
    true
}

pub fn snapshot(plane: &SharedPlane) -> Value {
    let mut p = plane.lock().unwrap();
    let status = p.status();
    json!({
        "allowlist_version": p.version,
        "allowlist_entries": p.allow.entries().len(),
        "control_state": p.last_state.as_ref().map(|(on, v)| json!({"public_egress": on, "allowlist_version": v})),
        "max_age_ms": p.max_age_ms,
        "public_egress_effective": status.is_ok(),
        "reason": status.as_ref().err().map(|e| e.reason()),
        "reason_detail": status.as_ref().err().map(|e| e.detail.clone()),
        "bytes_today": p.used,
        "byte_cap": p.cap,
        "open_streams": p.open_streams(),
        "max_streams": p.max_streams,
        "budget_path": p.store.as_ref().map(|s| s.display().to_string()),
        "budget_state": p.unreadable.unwrap_or("ok"),
    })
}

/// Poll Control every 1 s; unreachable / non-200 / malformed = no refresh.
pub fn spawn_poller(plane: SharedPlane, http: reqwest::Client, url: String) {
    tokio::spawn(async move {
        loop {
            let r = http.get(&url).timeout(Duration::from_millis(900)).send().await;
            if let Ok(resp) = r {
                if resp.status().is_success() {
                    if let Ok(v) = resp.json::<Value>().await {
                        apply_state(&plane, &v);
                    }
                }
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    });
}

/// One watchdog step: close everything on off / stale / mismatch / budget and
/// show P8 with that key; clear an egress P8 once public egress is back.
pub async fn watchdog_step(state: &SharedState, plane: &SharedPlane) {
    // P4 kill / P1 cleared / P2 or P9 withdrawn: close pilot egress too (no P8).
    if crate::offline::user_stopped(&*state.read().await) {
        plane.lock().unwrap().close_all("user_stop");
        return;
    }
    let (reason, back_on) = {
        let mut p = plane.lock().unwrap();
        let now = p.now_ms();
        let r = p.tick_at(now);
        (r, p.status_at(now).is_ok())
    };
    if let Some(r) = reason {
        crate::offline::system_offline(state, r).await;
    } else if back_on {
        let mut g = state.write().await;
        if g.p8_reason.as_deref().is_some_and(|c| P8_EGRESS.contains(&c)) {
            crate::offline::clear(&mut g);
            eprintln!("peer egress_on");
        }
    }
}

pub fn spawn_watchdog(state: SharedState, plane: SharedPlane) {
    tokio::spawn(async move {
        loop {
            watchdog_step(&state, &plane).await;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    });
}
