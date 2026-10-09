//! Alpha-4 Peer copy: P8 "You're offline" and P9 "Pilot: limited sharing".
//! Verbatim copies of the `p8_offline` / `p9_allowlist` entries of
//! `fixtures/screens.json` (Designer, fd19e0d), vendored like `p2_consent.json`
//! because the Docker build context is `rust/`. Drift tests below.
use serde_json::{json, Value};
use std::sync::LazyLock;

const P8_JSON: &str = include_str!("p8_offline.json");
const P9_JSON: &str = include_str!("p9_allowlist.json");

fn strs(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

static P8: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(P8_JSON).expect("p8_offline.json"));
static P9: LazyLock<Value> = LazyLock::new(|| serde_json::from_str(P9_JSON).expect("p9_allowlist.json"));

/// Plain sentence for a system stop reason, from the fixture's `reason_lines`.
/// Unknown codes fall back to `connection_lost` (fixture rule). Dial failures,
/// gateway close and stream end use `connection_lost` directly.
pub fn p8_reason_line(code: &str) -> Option<String> {
    let rl = &P8["reason_lines"];
    rl.get(code)
        .or_else(|| rl.get("connection_lost"))
        .and_then(|v| v.as_str())
        .map(String::from)
}

/// stderr/user-facing P8 block: title, body lines, reason sentence (if any), button.
/// Never contains the raw reason code.
pub fn p8_user_facing(code: &str) -> String {
    let mut lines = vec![P8["title"].as_str().unwrap_or_default().to_string()];
    lines.extend(strs(&P8["body_lines"]));
    if let Some(l) = p8_reason_line(code) {
        lines.push(l);
    }
    lines.push(P8["cta"].as_str().unwrap_or_default().to_string());
    lines.join("\n")
}

/// `GET /peer/consent/p8` body. `reason_code` is machine-only (never shown).
pub fn p8_ux(active: Option<&str>) -> Value {
    json!({
        "screen": "P8",
        "id": "p8_offline",
        "title": P8["title"],
        "body_lines": P8["body_lines"],
        "button": P8["cta"],
        "cta": P8["cta"],
        "required_copy": P8["required_copy"],
        "offline": active.is_some(),
        "reason_line": active.and_then(p8_reason_line),
        "reason_code": active,
        "user_facing": active.map(p8_user_facing),
    })
}

pub fn p9_user_facing() -> String {
    let mut lines = vec![P9["title"].as_str().unwrap_or_default().to_string()];
    lines.extend(strs(&P9["body_lines"]));
    lines.join("\n")
}

pub fn p9_ux() -> Value {
    json!({
        "screen": "P9",
        "id": "p9_allowlist",
        "title": P9["title"],
        "body_lines": P9["body_lines"],
        "required_copy": P9["required_copy"],
        "user_facing": p9_user_facing(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(id: &str) -> (Value, Vec<String>) {
        let fx: Value = serde_json::from_str(include_str!("../../../../fixtures/screens.json")).unwrap();
        let e = fx["consent"].as_array().unwrap().iter().find(|c| c["id"] == id).unwrap().clone();
        let mut bad: Vec<String> = crate::ux::FORBIDDEN.iter().map(|s| s.to_string()).collect();
        bad.extend(strs(&fx["forbidden_user_facing_substrings"]));
        (e, bad)
    }

    #[test]
    fn p8_matches_fixture_and_is_clean() {
        let (e, bad) = fixture("p8_offline");
        assert_eq!(*P8, e, "src/p8_offline.json drifted from fixtures/screens.json");
        let body = strs(&e["body_lines"]).join("\n");
        for frag in strs(&e["required_copy"]) {
            assert!(body.contains(&frag), "{frag}");
        }
        let codes: Vec<String> = e["reason_lines"].as_object().unwrap().keys().cloned().chain(["something_new".to_string()]).collect();
        for code in &codes {
            let code = code.as_str();
            let ux = p8_user_facing(code);
            assert!(!ux.contains(code), "raw code {code} in UX");
            assert!(!ux.contains('_'), "raw code-ish text in UX: {ux}");
            for b in &bad {
                assert!(!ux.contains(b.as_str()), "{b}");
            }
            for frag in strs(&e["required_copy"]) {
                assert!(ux.contains(&frag));
            }
        }
        assert!(p8_user_facing("endpoint_mismatch").contains("doesn't match how it was set up"));
        assert!(p8_user_facing("connection_lost").contains("The connection dropped"));
        assert_eq!(p8_reason_line("something_new"), p8_reason_line("connection_lost"), "unknown -> connection_lost");
        for (code, line) in e["reason_lines"].as_object().unwrap() {
            assert_eq!(p8_reason_line(code).as_deref(), line.as_str(), "{code}");
        }
        assert!(p8_user_facing("relay_path_required").contains("expected secure route"));
        let blob = p8_ux(Some("endpoint_mismatch")).to_string();
        for b in &bad {
            assert!(!blob.contains(b.as_str()), "{b}");
        }
    }

    #[test]
    fn p9_matches_fixture_and_is_clean() {
        let (e, bad) = fixture("p9_allowlist");
        assert_eq!(*P9, e, "src/p9_allowlist.json drifted from fixtures/screens.json");
        let body = strs(&e["body_lines"]).join("\n");
        for frag in strs(&e["required_copy"]) {
            assert!(body.contains(&frag), "{frag}");
            assert!(p9_user_facing().contains(&frag));
        }
        let blob = format!("{}{}", p9_ux(), p9_user_facing());
        for b in &bad {
            assert!(!blob.contains(b.as_str()), "{b}");
        }
        assert!(!blob.contains("echo") && !blob.contains("http"), "P9 must not name test sites");
    }
}
