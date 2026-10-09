//! A4 P8 "You're offline": system-stopped sharing (gateway close / reject /
//! dial failure / endpoint_mismatch), distinct from the P4 user pause.
//! Printed once per transition: `UX P8` block (plain sentences only) then
//! `UX_SCREEN p8_offline`; the raw code is logged on a separate `peer ...` line.
use crate::a4_copy;
use crate::config;
use crate::iroh_local::print_screen;
use crate::state::{PeerState, SharedState};

pub const SCREEN_P8: &str = "p8_offline";
pub const CONNECTION_LOST: &str = "connection_lost";

/// True when the session ended because of a user action (kill, clear ack,
/// consent withdrawn) -- those are P4 / P1 / P2, never P8.
pub fn user_stopped(g: &PeerState) -> bool {
    let t = &g.cfg.transport;
    let iroh = config::is_iroh_local(t) || config::is_iroh_pilot(t);
    g.kill_requested
        || g.isp_ack_version.is_empty()
        || g.force_disconnect
        || (iroh && !g.p2_consent)
        || (g.cfg.p9_required() && !g.p9_ack)
}

/// Record a system stop; returns the UX block if this is a new transition.
pub fn enter(g: &mut PeerState, code: &str) -> Option<String> {
    if user_stopped(g) || g.p8_reason.as_deref() == Some(code) {
        return None;
    }
    // A pilot egress stop already showing (kill switch off / stale / budget /
    // mismatch) is not replaced by the generic `connection_lost` (no flapping).
    if code == CONNECTION_LOST && g.p8_reason.as_deref().is_some_and(|r| r.starts_with("egress_")) {
        return None;
    }
    g.p8_reason = Some(code.to_string());
    Some(a4_copy::p8_user_facing(code))
}

pub async fn system_offline(state: &SharedState, code: &str) {
    let block = {
        let mut g = state.write().await;
        enter(&mut g, code)
    };
    if let Some(b) = block {
        eprintln!("peer offline_reason={code}");
        eprintln!("UX P8\n{b}");
        print_screen(SCREEN_P8);
    }
}

pub fn clear(g: &mut PeerState) {
    g.p8_reason = None;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;

    fn st() -> PeerState {
        let mut cfg = Config::from_env();
        cfg.transport = "iroh_local".into();
        cfg.isp_ack_version = "v1".into();
        cfg.p2_consent_preset = true;
        cfg.public_egress = false;
        PeerState::new(cfg)
    }

    #[test]
    fn system_stop_enters_once_with_plain_copy() {
        let mut g = st();
        let b = enter(&mut g, "endpoint_mismatch").expect("P8 shown");
        assert!(b.contains("isn't sharing right now") && !b.contains("endpoint_mismatch"));
        assert!(enter(&mut g, "endpoint_mismatch").is_none(), "no repeat for same code");
        assert!(enter(&mut g, CONNECTION_LOST).is_some(), "new code is a new transition");
        clear(&mut g);
        assert!(g.p8_reason.is_none());
    }

    #[test]
    fn user_kill_is_p4_not_p8() {
        let mut g = st();
        g.kill_requested = true;
        assert!(enter(&mut g, CONNECTION_LOST).is_none());
        assert!(g.p8_reason.is_none());
        let mut g = st();
        g.set_isp_ack("");
        assert!(enter(&mut g, CONNECTION_LOST).is_none());
        let mut g = st();
        g.force_disconnect = true;
        assert!(enter(&mut g, CONNECTION_LOST).is_none());
    }

    #[test]
    fn p9_withdrawn_is_user_stop_for_pilot() {
        let mut cfg = Config::from_env();
        cfg.transport = "iroh_pilot".into();
        cfg.public_egress = true;
        cfg.isp_ack_version = "v1".into();
        cfg.p2_consent_preset = true;
        let mut g = PeerState::new(cfg);
        assert!(!g.p9_ack);
        assert!(enter(&mut g, CONNECTION_LOST).is_none(), "P9 not accepted -> user stop, no P8");
        g.set_p9_ack(true).unwrap();
        assert!(enter(&mut g, "relay_path_required").unwrap().contains("expected secure route"));
    }

    #[test]
    fn connection_lost_does_not_replace_specific_reason() {
        let mut g = st();
        assert!(enter(&mut g, "egress_off").is_some());
        assert!(enter(&mut g, CONNECTION_LOST).is_none());
        assert_eq!(g.p8_reason.as_deref(), Some("egress_off"));
        assert!(enter(&mut g, "endpoint_mismatch").is_some(), "specific reasons still replace");
    }

    #[test]
    fn unknown_code_falls_back_to_connection_lost() {
        let mut g = st();
        let b = enter(&mut g, "weird_new_reason").unwrap();
        assert!(!b.contains("weird"));
        assert!(b.contains("No traffic is going through your connection"));
        assert!(b.contains("The connection dropped"));
    }
}
