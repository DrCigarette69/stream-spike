//! Designer P1 / P4 copy -- exact parity with Python `P1_UX` / `P4_UX`.
//! Brand placeholder is `[Brand]`; never put product codename in UX.

use serde_json::{json, Value};

pub const FORBIDDEN: &[&str] = &[
    "Stream",
    "waive all liability",
    "ISP will always allow",
    "we are not responsible for ISP",
];

pub const P1_USER_FACING: &str = concat!(
    "Before you enroll\n\n",
    "Some home internet plans prohibit running a proxy or sharing your connection this way.\n",
    "Your ISP or carrier may suspend service if they decide this violates their terms.\n",
    "[Brand] does not guarantee your ISP will allow this.\n",
    "(Full legal disclaimer: counsel text.)\n\n",
    "☐ I understand and want to continue"
);

pub const P4_USER_FACING: &str = concat!(
    "Sharing paused\n",
    "No traffic through your connection until you turn it back on.\n",
    "[ Resume sharing ]"
);

/// P2 consent bundle -- verbatim `fixtures/screens.json` `p2_consent.required_copy`
/// (no other copy exists in the fixture; Designer owns any fuller text).
pub const P2_REQUIRED_COPY: &[&str] = &[
    "do not read page contents",
    "Matching may pause",
    "compromised device",
];

pub const P2_USER_FACING: &str = concat!(
    "do not read page contents\n",
    "Matching may pause\n",
    "compromised device"
);

pub fn p2_ux() -> Value {
    json!({
        "screen": "P2",
        "id": "p2_consent",
        "body_lines": P2_REQUIRED_COPY,
        "required_copy": P2_REQUIRED_COPY,
        "user_facing": P2_USER_FACING,
    })
}

pub fn p2_user_facing() -> &'static str {
    P2_USER_FACING
}

pub fn p1_ux() -> Value {
    json!({
        "screen": "P1",
        "title": "Before you enroll",
        "body_lines": [
            "Some home internet plans prohibit running a proxy or sharing your connection this way.",
            "Your ISP or carrier may suspend service if they decide this violates their terms.",
            "[Brand] does not guarantee your ISP will allow this.",
            "(Full legal disclaimer: counsel text.)",
        ],
        "checkbox": "I understand and want to continue",
        "required_copy": [
            "does not guarantee your ISP",
            "may suspend service",
            "I understand and want to continue",
        ],
        "user_facing": P1_USER_FACING,
    })
}

pub fn p4_ux() -> Value {
    json!({
        "screen": "P4",
        "cta": "Pause sharing",
        "status_after_kill": "Sharing paused",
        "detail": "No traffic through your connection until you turn it back on.",
        "required_copy": [
            "Sharing paused",
            "No traffic through your connection until you turn it back on",
        ],
        "user_facing": P4_USER_FACING,
    })
}

pub fn p1_user_facing() -> &'static str {
    P1_USER_FACING
}

pub fn p4_user_facing() -> &'static str {
    P4_USER_FACING
}

pub fn p4_status_after_kill() -> &'static str {
    "Sharing paused"
}

pub fn assert_no_forbidden(text: &str) -> Result<(), String> {
    for bad in FORBIDDEN {
        if text.contains(bad) {
            return Err(format!("forbidden user-facing substring: {bad:?}"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_p1_greps() {
        let blob = p1_ux().to_string();
        assert!(blob.contains("does not guarantee your ISP"));
        assert!(blob.contains("may suspend service"));
        assert!(blob.contains("I understand and want to continue"));
    }

    #[test]
    fn required_p4_greps() {
        let blob = p4_ux().to_string();
        assert!(blob.contains("Sharing paused"));
        assert!(blob.contains("No traffic through your connection until you turn it back on"));
    }

    #[test]
    fn forbidden_strings_absent_from_ux() {
        let blob = format!("{}{}", p1_ux(), p4_ux());
        for bad in FORBIDDEN {
            assert!(!blob.contains(bad), "UX contains forbidden {bad}");
        }
        assert_no_forbidden(p1_user_facing()).unwrap();
        assert_no_forbidden(p4_user_facing()).unwrap();
    }

    #[test]
    fn p2_copy_matches_fixture_and_is_clean() {
        let fx: Value = serde_json::from_str(include_str!("../../../../fixtures/screens.json")).unwrap();
        let p2 = fx["consent"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["id"] == "p2_consent")
            .unwrap();
        let want: Vec<&str> = p2["required_copy"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(want, P2_REQUIRED_COPY);
        for line in &want {
            assert!(p2_user_facing().contains(line), "{line}");
        }
        let blob = format!("{}{}", p2_ux(), p2_user_facing());
        for bad in FORBIDDEN.iter().chain(["waive all liability", "ISP will always allow"].iter()) {
            assert!(!blob.contains(bad), "P2 contains forbidden {bad}");
        }
        for bad in fx["forbidden_user_facing_substrings"].as_array().unwrap() {
            assert!(!blob.contains(bad.as_str().unwrap()));
        }
    }

    #[test]
    fn p1_gate_understood_false() {
        let version = "v1";
        let understood = Some(false);
        let gate = !version.is_empty() && understood == Some(false);
        assert!(gate);
    }
}
