#![cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
//! A4.4 relay-path evidence (iroh-free, unit-tested in every build).
//!
//! Primary signal: the endpoint's UDP datagram counters (send + recv, IPv4 + IPv6).
//! A relay-only pilot endpoint (relay map with `quic: None`, `PathSelection::RelayOnly`)
//! never sends or receives a UDP datagram, so ANY non-zero count is a violation.
//! Secondary: `conn_type` Direct / Mixed (iroh 0.95.1 keeps it `None` under RelayOnly).
//! The counter source is a trait so tests (and the `a4_local`-only hook
//! `SPIKE_A4_FAKE_UDP_DATAGRAMS`) can drive it.
#[cfg(test)]
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(test)]
use std::sync::Arc;
use stream_proto::guard::{self, Lane};

/// Endpoint-wide UDP datagram counts since bind.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct UdpCounts {
    pub send_v4: u64,
    pub send_v6: u64,
    pub recv_v4: u64,
    pub recv_v6: u64,
}

impl UdpCounts {
    pub fn total(&self) -> u64 {
        self.send_v4 + self.send_v6 + self.recv_v4 + self.recv_v6
    }
    pub fn log(&self) -> String {
        format!("send_v4={} send_v6={} recv_v4={} recv_v6={}", self.send_v4, self.send_v6, self.recv_v4, self.recv_v6)
    }
}

pub trait UdpCounterSource: Send + Sync {
    /// `None`: this build cannot read the counters (no evidence either way).
    fn read(&self) -> Option<UdpCounts>;
}

/// The counters are not readable in this build.
pub struct Unavailable;
impl UdpCounterSource for Unavailable {
    fn read(&self) -> Option<UdpCounts> {
        None
    }
}

/// Test source: a shared count reported as IPv4 sends.
#[cfg(test)]
#[derive(Clone, Default)]
pub struct FakeUdp(pub Arc<AtomicU64>);
#[cfg(test)]
impl FakeUdp {
    pub fn new(n: u64) -> Self {
        Self(Arc::new(AtomicU64::new(n)))
    }
    pub fn set(&self, n: u64) {
        self.0.store(n, Ordering::SeqCst)
    }
}
#[cfg(test)]
impl UdpCounterSource for FakeUdp {
    fn read(&self) -> Option<UdpCounts> {
        Some(UdpCounts { send_v4: self.0.load(Ordering::SeqCst), ..Default::default() })
    }
}

/// Real counts plus an injected offset (the `a4_local` hook).
#[cfg(any(test, feature = "a4_local"))]
struct Offset {
    inner: Box<dyn UdpCounterSource>,
    extra: u64,
}
#[cfg(any(test, feature = "a4_local"))]
impl UdpCounterSource for Offset {
    fn read(&self) -> Option<UdpCounts> {
        let mut c = self.inner.read().unwrap_or_default();
        c.send_v4 += self.extra;
        Some(c)
    }
}

/// Apply `SPIKE_A4_FAKE_UDP_DATAGRAMS=<n>` (on-box `a4_local` builds only; the name is not
/// compiled into pilot builds at all).
#[cfg(feature = "a4_local")]
pub fn with_test_hook(src: Box<dyn UdpCounterSource>) -> Box<dyn UdpCounterSource> {
    const ENV_FAKE_UDP: &str = "SPIKE_A4_FAKE_UDP_DATAGRAMS";
    match std::env::var(ENV_FAKE_UDP).ok().map(|v| v.trim().parse::<u64>()) {
        None => src,
        Some(Ok(n)) => {
            eprintln!("peer a4_local hook {ENV_FAKE_UDP}={n}");
            Box::new(Offset { inner: src, extra: n })
        }
        Some(Err(_)) => {
            eprintln!("peer a4_local hook {ENV_FAKE_UDP} invalid; ignored");
            src
        }
    }
}

#[cfg(not(feature = "a4_local"))]
pub fn with_test_hook(src: Box<dyn UdpCounterSource>) -> Box<dyn UdpCounterSource> {
    src
}

#[cfg(test)]
pub fn offset(inner: Box<dyn UdpCounterSource>, extra: u64) -> Box<dyn UdpCounterSource> {
    Box::new(Offset { inner, extra })
}

/// Detail for `a4_refuse_relay_refused:<detail>` when the counters show UDP traffic.
pub fn udp_violation(src: &dyn UdpCounterSource) -> Option<String> {
    src.read().filter(|c| c.total() > 0).map(|c| format!("udp_datagrams:{} ({})", c.total(), c.log()))
}

/// Pilot lane fails closed when the counters cannot be read (no relay-path evidence);
/// on-box `a4_local` lanes go on with the `conn_type` signal only (logged).
pub fn require_counters(src: &dyn UdpCounterSource, lane: Lane) -> Result<(), String> {
    match (src.read(), lane) {
        (Some(_), _) | (None, Lane::Local) => Ok(()),
        (None, Lane::Pilot) => Err(format!("a4_refuse_{}:udp_counters_unavailable", guard::REASON_RELAY_REFUSED)),
    }
}

/// One line for the log / smoke.
pub fn counters_line(src: &dyn UdpCounterSource) -> String {
    match src.read() {
        Some(c) => format!("udp_counters total={} {}", c.total(), c.log()),
        None => "udp_counters=unavailable".into(),
    }
}

/// After a pilot session: only an ordinary drop (no reason) is redialed.
pub fn retry_after(end: Option<&str>) -> bool {
    end.is_none()
}

#[cfg(test)]
#[path = "relay_path_tests.rs"]
mod tests;
