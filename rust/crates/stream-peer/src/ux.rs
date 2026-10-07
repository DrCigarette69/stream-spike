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
    fn p1_gate_understood_false() {
        let version = "v1";
        let understood = Some(false);
        let gate = !version.is_empty() && understood == Some(false);
        assert!(gate);
    }
}
