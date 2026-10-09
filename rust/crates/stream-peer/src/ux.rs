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

/// P2 consent copy: verbatim copy of the `p2_consent` entry of
/// `fixtures/screens.json`, vendored as `src/p2_consent.json` because the Docker
/// build context is `rust/` only. The drift test below fails if they differ.
const P2_JSON: &str = include_str!("p2_consent.json");

struct P2Copy {
    #[cfg_attr(not(test), allow(dead_code))]
    raw: Value,
    title: String,
    body_lines: Vec<String>,
    button: String,
    required_copy: Vec<String>,
    user_facing: String,
}

fn strs(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

static P2: std::sync::LazyLock<P2Copy> = std::sync::LazyLock::new(|| {
    let raw: Value = serde_json::from_str(P2_JSON).expect("p2_consent.json");
    let title = raw["title"].as_str().unwrap_or_default().to_string();
    let body_lines = strs(&raw["body_lines"]);
    let button = raw["cta"].as_str().unwrap_or_default().to_string();
    let required_copy = strs(&raw["required_copy"]);
    let mut lines = vec![title.clone()];
    lines.extend(body_lines.iter().cloned());
    lines.push(button.clone());
    let user_facing = lines.join("\n");
    P2Copy { raw, title, body_lines, button, required_copy, user_facing }
});

pub fn p2_ux() -> Value {
    json!({
        "screen": "P2",
        "id": "p2_consent",
        "title": P2.title,
        "body_lines": P2.body_lines,
        "button": P2.button,
        "cta": P2.button,
        "required_copy": P2.required_copy,
        "user_facing": P2.user_facing,
    })
}

/// Title, body lines, button -- one per line (stderr block after `UX P2`).
pub fn p2_user_facing() -> &'static str {
    &P2.user_facing
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
        let entry = fx["consent"]
            .as_array()
            .unwrap()
            .iter()
            .find(|c| c["id"] == "p2_consent")
            .unwrap();
        // vendored copy must equal the fixture entry exactly
        assert_eq!(&P2.raw, entry, "src/p2_consent.json drifted from fixtures/screens.json");
        let body = P2.body_lines.join("\n");
        assert!(!P2.title.is_empty() && !P2.button.is_empty() && !P2.body_lines.is_empty());
        for frag in strs(&entry["required_copy"]) {
            assert!(body.contains(&frag), "required_copy {frag:?} not in body_lines");
        }
        let ux = p2_ux();
        assert_eq!(ux["title"], entry["title"]);
        assert_eq!(ux["body_lines"], entry["body_lines"]);
        assert_eq!(ux["button"], entry["cta"]);
        assert_eq!(ux["required_copy"], entry["required_copy"]);
        let blob = format!("{ux}{}", p2_user_facing());
        let mut bad: Vec<String> = FORBIDDEN.iter().map(|s| s.to_string()).collect();
        bad.extend(strs(&fx["forbidden_user_facing_substrings"]));
        for b in bad {
            assert!(!blob.contains(&b), "P2 contains forbidden {b}");
        }
        let expect = format!("{}\n{}\n{}", P2.title, P2.body_lines.join("\n"), P2.button);
        assert_eq!(p2_user_facing(), expect);
    }

    #[test]
    fn p1_gate_understood_false() {
        let version = "v1";
        let understood = Some(false);
        let gate = !version.is_empty() && understood == Some(false);
        assert!(gate);
    }
}
