//! Local egress floor (#6) -- non-bypassable. Parity with Python `egress_denied`.
use std::net::IpAddr;

/// Returns `Some(reason)` when dest must be refused; `None` when allowed.
pub fn egress_denied(host: &str, port: u16) -> Option<String> {
    if port != 80 && port != 443 {
        return Some(format!("port_{port}_blocked"));
    }
    let h = host.trim().trim_end_matches('.').to_ascii_lowercase();
    if h == "metadata.google.internal" || h == "169.254.169.254" {
        return Some("metadata_blocked".into());
    }
    if h == "echo.local" {
        return None;
    }
    if let Ok(ip) = h.parse::<IpAddr>() {
        if ip_blocked(ip) {
            return Some("rfc1918_or_link_local".into());
        }
        return None;
    }
    if h == "localhost" || h.ends_with(".lan") || h.ends_with(".internal") {
        return Some("lan_hostname_blocked".into());
    }
    None
}

fn ip_blocked(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            v4.is_private()
                || v4.is_loopback()
                || v4.is_link_local()
                || v4.is_broadcast()
                || v4.is_multicast()
                || v4.is_unspecified()
                || {
                    let o = v4.octets();
                    o[0] == 0 || o[0] >= 240
                }
        }
        IpAddr::V6(v6) => {
            v6.is_loopback()
                || v6.is_multicast()
                || v6.is_unspecified()
                || {
                    let o = v6.octets();
                    (o[0] & 0xfe) == 0xfc || (o[0] == 0xfe && (o[1] & 0xc0) == 0x80)
                }
                || v6
                    .to_ipv4_mapped()
                    .map(IpAddr::V4)
                    .map(ip_blocked)
                    .unwrap_or(false)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn denies_rfc1918() {
        assert_eq!(
            egress_denied("10.0.0.1", 443).as_deref(),
            Some("rfc1918_or_link_local")
        );
        assert_eq!(
            egress_denied("192.168.1.50", 443).as_deref(),
            Some("rfc1918_or_link_local")
        );
        assert_eq!(
            egress_denied("172.16.0.1", 80).as_deref(),
            Some("rfc1918_or_link_local")
        );
    }

    #[test]
    fn denies_link_local_and_metadata() {
        assert_eq!(
            egress_denied("169.254.169.254", 80).as_deref(),
            Some("metadata_blocked")
        );
        assert_eq!(
            egress_denied("metadata.google.internal", 443).as_deref(),
            Some("metadata_blocked")
        );
        assert_eq!(
            egress_denied("169.254.1.1", 443).as_deref(),
            Some("rfc1918_or_link_local")
        );
    }

    #[test]
    fn denies_non_http_ports() {
        assert_eq!(
            egress_denied("echo.local", 22).as_deref(),
            Some("port_22_blocked")
        );
        assert_eq!(
            egress_denied("1.1.1.1", 8080).as_deref(),
            Some("port_8080_blocked")
        );
    }

    #[test]
    fn allows_echo_and_public() {
        assert!(egress_denied("echo.local", 443).is_none());
        assert!(egress_denied("echo.local", 80).is_none());
        assert!(egress_denied("1.1.1.1", 443).is_none());
        assert!(egress_denied("8.8.8.8", 80).is_none());
    }

    #[test]
    fn denies_lan_hostnames_and_loopback() {
        assert_eq!(
            egress_denied("localhost", 443).as_deref(),
            Some("lan_hostname_blocked")
        );
        assert_eq!(
            egress_denied("printer.lan", 443).as_deref(),
            Some("lan_hostname_blocked")
        );
        assert_eq!(
            egress_denied("foo.internal", 443).as_deref(),
            Some("lan_hostname_blocked")
        );
        assert_eq!(
            egress_denied("127.0.0.1", 443).as_deref(),
            Some("rfc1918_or_link_local")
        );
        assert_eq!(
            egress_denied("::1", 443).as_deref(),
            Some("rfc1918_or_link_local")
        );
    }
}
