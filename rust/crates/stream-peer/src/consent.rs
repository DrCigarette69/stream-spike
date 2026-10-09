//! A3.2 P2 consent gate: its own step after the P1 ISP ack, before any
//! iroh_local dial. fake_relay / iroh_loopback are not gated by P2.
use crate::admin::AppState;
use crate::config;
use crate::frames::mark_offline;
use crate::iroh_local::{print_screen, SCREEN_P2};
use crate::ux::{self, p2_ux};
use axum::{extract::State, http::StatusCode, response::IntoResponse, Json};
use serde_json::{json, Value};

/// stderr: `UX P2` + copy block, then `UX_SCREEN p2_consent`.
pub fn print_p2() {
    eprintln!("UX P2\n{}", ux::p2_user_facing());
    print_screen(SCREEN_P2);
}

/// `GET /peer/consent/p2` -- same shape as p1/p4.
pub async fn consent_p2() -> Json<Value> {
    print_p2();
    let mut v = p2_ux();
    if let Some(obj) = v.as_object_mut() {
        obj.insert("ok".into(), json!(true));
    }
    Json(v)
}

/// `POST /peer/consent {"accepted": bool}`.
pub async fn peer_consent(
    State(app): State<AppState>,
    Json(body): Json<Value>,
) -> impl IntoResponse {
    let Some(accepted) = body.get("accepted").and_then(|v| v.as_bool()) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "accepted (bool) required", "code": "bad_request" })),
        );
    };
    let res = app.state.write().await.set_p2_consent(accepted);
    if let Err(code) = res {
        let mut out = app.state.read().await.snapshot();
        if let Some(obj) = out.as_object_mut() {
            obj.insert("error".into(), json!("isp_ack_required_before_consent"));
            obj.insert("code".into(), json!(code));
            obj.insert("p2_consent".into(), json!(false));
        }
        return (StatusCode::FORBIDDEN, Json(out));
    }
    if accepted {
        print_p2();
        eprintln!("peer p2_consent=true");
    } else {
        let _ = app.transport.kill("p2_consent_withdrawn").await;
        let transport = app.state.read().await.cfg.transport.clone();
        if config::is_iroh_local(&transport) {
            mark_offline(&app.state).await;
        }
        eprintln!("peer p2_consent=false");
    }
    let mut out = app.state.read().await.snapshot();
    if let Some(obj) = out.as_object_mut() {
        obj.insert("ok".into(), json!(true));
        obj.insert("p2_consent".into(), json!(accepted));
    }
    (StatusCode::OK, Json(out))
}

#[cfg(test)]
mod tests {
    use crate::config::Config;
    use crate::iroh_local::pre_dial;
    use crate::state::PeerState;

    fn st(ack: &str, preset: bool) -> PeerState {
        let mut cfg = Config::from_env();
        cfg.isp_ack_version = ack.into();
        cfg.p2_consent_preset = preset;
        PeerState::new(cfg)
    }

    #[test]
    fn p2_without_p1_refused() {
        let mut s = st("", false);
        assert_eq!(s.set_p2_consent(true), Err("p1_ack_required"));
        assert!(!s.p2_consent);
        s.set_isp_ack("v1");
        assert!(s.set_p2_consent(true).is_ok());
        assert!(s.p2_consent);
    }

    #[test]
    fn clearing_p1_clears_p2() {
        let mut s = st("v1", false);
        s.set_p2_consent(true).unwrap();
        s.set_isp_ack("v2");
        assert!(s.p2_consent, "re-ack keeps P2");
        s.set_isp_ack("");
        assert!(!s.p2_consent);
    }

    #[test]
    fn withdraw_clears() {
        let mut s = st("v1", false);
        s.set_p2_consent(true).unwrap();
        s.set_p2_consent(false).unwrap();
        assert!(!s.p2_consent);
    }

    #[test]
    fn preset_ack_no_longer_implies_p2() {
        assert!(!st("v1", false).p2_consent);
        assert!(st("v1", true).p2_consent);
        assert!(!st("", true).p2_consent, "preset without P1 is ignored");
    }

    #[test]
    fn pre_dial_refuses_without_p2() {
        let s = st("v1", false);
        assert_eq!(
            pre_dial(&s.isp_ack_version, s.p2_consent, s.kill_requested),
            Err("p2_consent_required")
        );
    }
}
