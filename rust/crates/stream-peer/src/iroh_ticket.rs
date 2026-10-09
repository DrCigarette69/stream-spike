//! A3.2 Peer-side checks on the A3.3 ticket fields before any iroh_local dial.
//!
//! `peer_endpoint_id` is already compared by `ticket::verify_ticket`
//! (`endpoint_mismatch`). Here, using the shared A3.0 `stream_proto::guard`:
//! - `gateway_endpoint_id` must equal `SPIKE_GATEWAY_ENDPOINT_ID` (else `gateway_mismatch`);
//! - `direct_addrs` must be a non-empty list (else `direct_addrs_required`);
//! - every entry must pass the guard (reason passed through unchanged:
//!   `public_addr`, `allowlist_miss`, `bad_addr`, ...); an entry that is a URL,
//!   or a `relay_url` field, is `relay_refused`.
//! Refusals log the guard's `log_line()` (`a3_refuse_non_private:<addr>` etc.).
use serde_json::Value;
use std::net::SocketAddr;
use stream_proto::guard::{self, Cidr, GuardError};

pub const GATEWAY_MISMATCH: &str = "gateway_mismatch";
pub const DIRECT_ADDRS_REQUIRED: &str = "direct_addrs_required";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DialTarget {
    pub gateway_endpoint_id: String,
    pub direct_addrs: Vec<SocketAddr>,
}

fn refuse(e: GuardError) -> String {
    eprintln!("{}", e.log_line());
    e.reason().to_string()
}

/// Production entry point: allowlist from `SPIKE_IROH_ALLOW_CIDRS`
/// (unset -> `10.73.0.0/24,127.0.0.0/8`).
pub fn check_dial_target(payload: &Value, expected_gateway: &str) -> Result<DialTarget, String> {
    let allow = guard::allowlist_from_env().map_err(refuse)?;
    check_dial_target_with(payload, expected_gateway, allow.as_deref())
}

/// `expected_gateway` empty => `gateway_mismatch`: with no relay or discovery
/// the Peer must know exactly which Gateway it dials.
pub fn check_dial_target_with(
    payload: &Value,
    expected_gateway: &str,
    allow: Option<&[Cidr]>,
) -> Result<DialTarget, String> {
    let gw = payload
        .get("gateway_endpoint_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    let want = expected_gateway.trim();
    if gw.is_empty() || want.is_empty() || gw != want {
        return Err(GATEWAY_MISMATCH.into());
    }
    let relay = payload.get("relay_url").and_then(|v| v.as_str());
    guard::check_relay_url(relay).map_err(refuse)?;
    let raw = match payload.get("direct_addrs") {
        Some(Value::Array(a)) if !a.is_empty() => a,
        Some(Value::Array(_)) | None | Some(Value::Null) => {
            return Err(DIRECT_ADDRS_REQUIRED.into())
        }
        Some(_) => return Err(guard::REASON_BAD_ADDR.into()),
    };
    let mut entries = Vec::with_capacity(raw.len());
    for v in raw {
        let s = v.as_str().ok_or_else(|| guard::REASON_BAD_ADDR.to_string())?;
        if s.contains("://") {
            guard::check_relay_url(Some(s)).map_err(refuse)?;
        }
        entries.push(s.to_string());
    }
    let direct_addrs = guard::check_direct_addrs_with(&entries, allow).map_err(refuse)?;
    Ok(DialTarget { gateway_endpoint_id: gw.to_string(), direct_addrs })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const GW: &str = "gwdevkey0001";

    fn default_allow() -> Option<Vec<Cidr>> {
        guard::parse_allowlist(guard::DEFAULT_ALLOW_CIDRS).unwrap()
    }

    fn check(p: &Value, gw: &str) -> Result<DialTarget, String> {
        check_dial_target_with(p, gw, default_allow().as_deref())
    }

    #[test]
    fn good_ticket_passes() {
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["10.73.0.1:4433", "127.0.0.1:4433"]});
        assert_eq!(check(&p, GW).unwrap().direct_addrs.len(), 2);
    }

    #[test]
    fn gateway_missing_or_mismatch() {
        let p = json!({"direct_addrs": ["10.73.0.1:4433"]});
        assert_eq!(check(&p, GW).unwrap_err(), GATEWAY_MISMATCH);
        let p = json!({"gateway_endpoint_id": "other", "direct_addrs": ["10.73.0.1:4433"]});
        assert_eq!(check(&p, GW).unwrap_err(), GATEWAY_MISMATCH);
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["10.73.0.1:4433"]});
        assert_eq!(check(&p, "").unwrap_err(), GATEWAY_MISMATCH);
    }

    #[test]
    fn public_ip_and_mixed_list_rejected() {
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["8.8.8.8:4433"]});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_PUBLIC_ADDR);
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["10.73.0.1:4433", "8.8.8.8:4433"]});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_PUBLIC_ADDR);
    }

    #[test]
    fn default_allowlist_narrows_private() {
        // 192.168/16 and ::1 are private but outside the default allowlist.
        for a in ["192.168.1.2:4433", "[::1]:4433"] {
            let p = json!({"gateway_endpoint_id": GW, "direct_addrs": [a]});
            assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_ALLOWLIST_MISS, "{a}");
        }
        // With no narrowing (env set to empty) they pass.
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["192.168.1.2:4433", "[::1]:4433"]});
        assert!(check_dial_target_with(&p, GW, None).is_ok());
    }

    #[test]
    fn relay_url_rejected() {
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["https://euw1-1.relay.iroh.network./"]});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_RELAY_REFUSED);
        let p = json!({"gateway_endpoint_id": GW, "relay_url": "https://x", "direct_addrs": ["10.73.0.1:1"]});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_RELAY_REFUSED);
    }

    #[test]
    fn empty_missing_or_malformed_addrs() {
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": []});
        assert_eq!(check(&p, GW).unwrap_err(), DIRECT_ADDRS_REQUIRED);
        let p = json!({"gateway_endpoint_id": GW});
        assert_eq!(check(&p, GW).unwrap_err(), DIRECT_ADDRS_REQUIRED);
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": "10.73.0.1:1"});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_BAD_ADDR);
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": [5]});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_BAD_ADDR);
        let p = json!({"gateway_endpoint_id": GW, "direct_addrs": ["10.73.0.1:0"]});
        assert_eq!(check(&p, GW).unwrap_err(), guard::REASON_BAD_ADDR);
    }
}
