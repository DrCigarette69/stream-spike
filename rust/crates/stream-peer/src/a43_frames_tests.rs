//! A4.3 part 1 frame shapes (Platform e20498d) as the Peer parses / sends them.
use crate::egress_state::{apply_frame, close_frame};
use crate::pilot_egress_tests::{plane, Fake};
use serde_json::json;
use stream_proto::guard::Lane;

#[test]
fn egress_state_frame_typed() {
    let p = plane(Fake::with(&[]), 5000, 1 << 20, Lane::Pilot);
    let v = p.lock().unwrap().version.clone();
    // Runbook example shape.
    assert!(apply_frame(&p, &json!({"type":"EGRESS_STATE","public_egress":true,"allowlist_version":v,"ts":1791759487.21})));
    assert!(p.lock().unwrap().status().is_ok());
    assert!(apply_frame(&p, &json!({"type":"EGRESS_STATE","public_egress":false,"allowlist_version":v,"ts":1})));
    assert_eq!(p.lock().unwrap().status().unwrap_err().reason(), "egress_off");
    // Missing ts / wrong types / wrong type tag: ignored (no refresh -> goes stale = off).
    for bad in [
        json!({"type":"EGRESS_STATE","public_egress":true,"allowlist_version":v}),
        json!({"type":"EGRESS_STATE","public_egress":"yes","allowlist_version":v,"ts":1}),
        json!({"type":"DATA","public_egress":true,"allowlist_version":v,"ts":1}),
    ] {
        assert!(!apply_frame(&p, &bad), "{bad}");
    }
}

#[test]
fn close_frame_matches_proto() {
    let c = close_frame("s1", "relay_path_required");
    assert_eq!(c, json!({"type":"CLOSE","stream_id":"s1","reason":"relay_path_required"}));
    let back: stream_proto::relay::Close = serde_json::from_value(c).unwrap();
    assert_eq!(back.reason.as_deref(), Some("relay_path_required"));
    // Old A1-A3 CLOSE (no reason) still parses.
    let old: stream_proto::relay::Close = serde_json::from_value(json!({"type":"CLOSE","stream_id":"s1"})).unwrap();
    assert!(old.reason.is_none());
}
