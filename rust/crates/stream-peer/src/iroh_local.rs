//! A3.2 `SPIKE_TRANSPORT=iroh_local` scaffolding (part 1, no iroh crate yet).
//!
//! Without the `iroh_local` cargo feature the Peer refuses this transport
//! cleanly: `last_error = "iroh_local_feature_not_built"`, no dial, admin
//! `/health` stays up. Ordering contract for the real dial (part 2):
//! P1 ISP ack + P2 consent + not killed -> print `UX_SCREEN p1_isp_ack`,
//! `UX_SCREEN p2_consent` -> check ticket (`iroh_ticket`) -> dial ->
//! register connection in `kill::TransportSlot` (kill closes it <= 2 s and
//! prints `UX_SCREEN p4_kill`).
#![allow(dead_code)] // part-2 (iroh dial) API; exercised by unit tests now.
use crate::frames::{mark_offline, set_error};
use crate::kill::TransportSlot;
use crate::state::SharedState;

pub const FEATURE_NOT_BUILT: &str = "iroh_local_feature_not_built";
/// A4: `iroh_pilot` is recognized but its dial (A4.4) is not built yet.
pub const PILOT_NOT_BUILT: &str = "iroh_pilot_not_built";

pub const SCREEN_P1: &str = "p1_isp_ack";
pub const SCREEN_P2: &str = "p2_consent";
pub const SCREEN_P4: &str = "p4_kill";
pub const SCREEN_P9: &str = "p9_allowlist";

/// Exact log line the A3.5 asserts grep (stderr, one per line).
pub fn screen_line(id: &str) -> String {
    format!("UX_SCREEN {id}")
}

pub fn print_screen(id: &str) {
    eprintln!("{}", screen_line(id));
}

pub fn feature_built() -> bool {
    cfg!(feature = "iroh_local")
}

/// Gate checked immediately before every iroh_local dial attempt.
pub fn dial_gate(isp_ack_version: &str, p2_consent: bool, killed: bool) -> Result<(), &'static str> {
    if killed {
        return Err("peer_killed");
    }
    if isp_ack_version.is_empty() {
        return Err("p1_isp_ack_required");
    }
    if !p2_consent {
        return Err("p2_consent_required");
    }
    Ok(())
}

/// A4 gate: as `dial_gate`, plus P9 when `p9_required` (iroh_pilot + public egress).
pub fn dial_gate_a4(
    isp_ack_version: &str,
    p2_consent: bool,
    public_egress: bool,
    p9_ack: bool,
    killed: bool,
) -> Result<(), &'static str> {
    dial_gate(isp_ack_version, p2_consent, killed)?;
    if public_egress && !p9_ack {
        return Err("p9_allowlist_required");
    }
    Ok(())
}

/// A4 pre-dial: gate + `UX_SCREEN p1_isp_ack`, `p2_consent` (+ `p9_allowlist` when P9 required).
pub fn pre_dial_a4(
    isp_ack_version: &str,
    p2_consent: bool,
    public_egress: bool,
    p9_ack: bool,
    killed: bool,
) -> Result<Vec<String>, &'static str> {
    dial_gate_a4(isp_ack_version, p2_consent, public_egress, p9_ack, killed)?;
    let mut lines = vec![screen_line(SCREEN_P1), screen_line(SCREEN_P2)];
    if public_egress {
        lines.push(screen_line(SCREEN_P9));
    }
    for l in &lines {
        eprintln!("{l}");
    }
    Ok(lines)
}

/// Gate + screen-ID prints, in order. Returns the lines printed (for tests).
pub fn pre_dial(isp_ack_version: &str, p2_consent: bool, killed: bool) -> Result<Vec<String>, &'static str> {
    dial_gate(isp_ack_version, p2_consent, killed)?;
    let lines = vec![screen_line(SCREEN_P1), screen_line(SCREEN_P2)];
    for l in &lines {
        eprintln!("{l}");
    }
    Ok(lines)
}

/// Startup check for the transport; Err is the reason to surface.
pub fn startup_check() -> Result<(), &'static str> {
    if !feature_built() {
        return Err(FEATURE_NOT_BUILT);
    }
    Ok(())
}

/// `SPIKE_TRANSPORT=iroh_pilot`: refuse cleanly (admin stays up) until A4.4.
pub fn spawn_iroh_pilot_refusal(state: SharedState) {
    tokio::spawn(async move {
        eprintln!("A4 iroh_pilot refused: {PILOT_NOT_BUILT}");
        mark_offline(&state).await;
        set_error(&state, PILOT_NOT_BUILT).await;
    });
}

pub fn spawn_iroh_local(
    state: SharedState,
    slot: TransportSlot,
    http: reqwest::Client,
    handle: crate::state::RelayHandle,
) {
    tokio::spawn(async move {
        if let Err(reason) = startup_check() {
            eprintln!("A3 iroh_local refused: {reason}");
            mark_offline(&state).await;
            set_error(&state, reason).await;
            return;
        }
        #[cfg(feature = "iroh_local")]
        crate::iroh_dial::run(state, slot, http, handle).await;
        #[cfg(not(feature = "iroh_local"))]
        let _ = (slot, http, handle);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_without_feature() {
        if !feature_built() {
            assert_eq!(startup_check(), Err(FEATURE_NOT_BUILT));
        }
    }

    #[test]
    fn gate_order() {
        assert_eq!(dial_gate("", true, false), Err("p1_isp_ack_required"));
        assert_eq!(dial_gate("v1", false, false), Err("p2_consent_required"));
        assert_eq!(dial_gate("v1", true, true), Err("peer_killed"));
        assert!(dial_gate("v1", true, false).is_ok());
    }

    #[test]
    fn pre_dial_prints_p1_then_p2() {
        assert!(pre_dial("", true, false).is_err());
        let l = pre_dial("v1", true, false).unwrap();
        assert_eq!(l, vec!["UX_SCREEN p1_isp_ack", "UX_SCREEN p2_consent"]);
    }

    #[test]
    fn a4_p9_gate_only_when_egress_on() {
        assert!(dial_gate_a4("v1", true, false, false, false).is_ok(), "egress off: P9 never required");
        assert_eq!(dial_gate_a4("v1", true, true, false, false), Err("p9_allowlist_required"));
        assert_eq!(dial_gate_a4("v1", false, true, true, false), Err("p2_consent_required"));
        assert!(dial_gate_a4("v1", true, true, true, false).is_ok());
        assert_eq!(pre_dial_a4("v1", true, true, false, false), Err("p9_allowlist_required"));
        let l = pre_dial_a4("v1", true, true, true, false).unwrap();
        assert_eq!(l, vec!["UX_SCREEN p1_isp_ack", "UX_SCREEN p2_consent", "UX_SCREEN p9_allowlist"]);
        let l = pre_dial_a4("v1", true, false, false, false).unwrap();
        assert_eq!(l, vec!["UX_SCREEN p1_isp_ack", "UX_SCREEN p2_consent"]);
    }

    #[test]
    fn screen_lines_have_no_forbidden_copy() {
        for id in [SCREEN_P1, SCREEN_P2, SCREEN_P4, SCREEN_P9] {
            crate::ux::assert_no_forbidden(&screen_line(id)).unwrap();
        }
        crate::ux::assert_no_forbidden(FEATURE_NOT_BUILT).unwrap();
    }
}
