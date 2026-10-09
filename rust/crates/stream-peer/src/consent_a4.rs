//! A4 admin endpoints: P8 offline screen, P9 allowlist gate.
//! `GET /peer/consent/p8` (alias `/peer/screen/p8`), `GET|POST /peer/consent/p9`.
use crate::a4_copy;
use crate::admin::AppState;
use crate::config;
use crate::frames::mark_offline;
use crate::iroh_local::{print_screen, SCREEN_P9};
use axum::{extract::State, http::StatusCode, response::IntoResponse, Json};
use serde_json::{json, Value};

/// stderr when P9 is shown or accepted: `UX P9`, title, body lines, `UX_SCREEN p9_allowlist`.
pub fn print_p9() {
    eprintln!("UX P9\n{}", a4_copy::p9_user_facing());
    print_screen(SCREEN_P9);
}

pub async fn consent_p8(State(app): State<AppState>) -> Json<Value> {
    let reason = app.state.read().await.p8_reason.clone();
    let mut v = a4_copy::p8_ux(reason.as_deref());
    if let Some(o) = v.as_object_mut() {
        o.insert("ok".into(), json!(true));
    }
    Json(v)
}

fn egress_off() -> (StatusCode, Json<Value>) {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": "p9_not_shown", "code": "p9_not_applicable" })),
    )
}

pub async fn consent_p9(State(app): State<AppState>) -> impl IntoResponse {
    let (on, acked) = {
        let g = app.state.read().await;
        (g.cfg.p9_required(), g.p9_ack)
    };
    if !on {
        return egress_off();
    }
    print_p9();
    let mut v = a4_copy::p9_ux();
    if let Some(o) = v.as_object_mut() {
        o.insert("ok".into(), json!(true));
        o.insert("p9_ack".into(), json!(acked));
    }
    (StatusCode::OK, Json(v))
}

pub async fn peer_consent_p9(State(app): State<AppState>, Json(body): Json<Value>) -> impl IntoResponse {
    let Some(accepted) = body.get("accepted").and_then(|v| v.as_bool()) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "accepted (bool) required", "code": "bad_request" })),
        );
    };
    let res = app.state.write().await.set_p9_ack(accepted);
    match res {
        Err("p9_not_applicable") => return egress_off(),
        Err(code) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({ "error": "p2_consent_required_before_p9", "code": code, "p9_ack": false })),
            )
        }
        Ok(()) => {}
    }
    if accepted {
        print_p9();
        eprintln!("peer p9_ack=true");
    } else {
        let _ = app.transport.kill("p9_withdrawn").await;
        let transport = app.state.read().await.cfg.transport.clone();
        if config::is_iroh_local(&transport) {
            mark_offline(&app.state).await;
        }
        eprintln!("peer p9_ack=false");
    }
    (StatusCode::OK, Json(json!({ "ok": true, "p9_ack": accepted })))
}

/// `GET /peer/egress`: A4.4 pilot egress state (404 outside iroh_pilot).
pub async fn peer_egress(State(app): State<AppState>) -> impl IntoResponse {
    let p = app.state.read().await.egress.clone();
    match p {
        Some(p) => (StatusCode::OK, Json(crate::egress_state::snapshot(&p))),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "pilot_egress_not_active", "code": "transport_not_pilot" })),
        ),
    }
}

#[cfg(test)]
mod tests {
    use crate::config::Config;
    use crate::state::PeerState;

    fn st(egress: bool, ack: &str, p2: bool, p9_preset: bool) -> PeerState {
        let mut cfg = Config::from_env();
        cfg.transport = "iroh_pilot".into();
        cfg.isp_ack_version = ack.into();
        cfg.p2_consent_preset = p2;
        cfg.public_egress = egress;
        cfg.p9_ack_preset = p9_preset;
        PeerState::new(cfg)
    }

    #[test]
    fn p9_requires_p2_and_egress_on() {
        let mut s = st(true, "v1", false, false);
        assert_eq!(s.set_p9_ack(true), Err("p2_consent_required"));
        s.set_p2_consent(true).unwrap();
        assert!(s.set_p9_ack(true).is_ok() && s.p9_ack);
        let mut off = st(false, "v1", true, false);
        assert_eq!(off.set_p9_ack(true), Err("p9_not_applicable"));
        let mut cfg = Config::from_env();
        cfg.transport = "iroh_local".into();
        cfg.public_egress = true;
        cfg.isp_ack_version = "v1".into();
        cfg.p2_consent_preset = true;
        let mut local = PeerState::new(cfg);
        assert_eq!(local.set_p9_ack(true), Err("p9_not_applicable"), "iroh_local never shows P9");
    }

    #[test]
    fn clearing_p1_or_p2_clears_p9() {
        let mut s = st(true, "v1", true, false);
        s.set_p9_ack(true).unwrap();
        s.set_p2_consent(false).unwrap();
        assert!(!s.p9_ack);
        s.set_p2_consent(true).unwrap();
        s.set_p9_ack(true).unwrap();
        s.set_isp_ack("");
        assert!(!s.p9_ack && !s.p2_consent);
    }

    #[test]
    fn p9_preset_only_with_p1_p2_and_egress() {
        assert!(st(true, "v1", true, true).p9_ack);
        assert!(!st(true, "v1", false, true).p9_ack, "needs P2");
        assert!(!st(true, "", true, true).p9_ack, "needs P1");
        assert!(!st(false, "v1", true, true).p9_ack, "egress off: never");
        let mut cfg = Config::from_env();
        cfg.transport = "iroh_local".into();
        cfg.public_egress = true;
        cfg.p9_ack_preset = true;
        cfg.isp_ack_version = "v1".into();
        cfg.p2_consent_preset = true;
        assert!(!PeerState::new(cfg).p9_ack, "iroh_local: P9 never applies");
    }
}
