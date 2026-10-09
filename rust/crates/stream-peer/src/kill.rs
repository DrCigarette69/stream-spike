//! Kill-switch teardown bounded at 2 s, for any active transport.
//!
//! The fake-relay / iroh_loopback TCP path already closes via
//! `RelayHandle`. Part 2's iroh `Connection` registers here as an
//! [`ActiveTransport`]; `/peer/kill` calls [`TransportSlot::kill`], which
//! awaits a graceful close but never longer than [`KILL_DEADLINE`], then drops
//! the transport (dropping an iroh `Connection`/`Endpoint` aborts it).
#![allow(dead_code)] // part-2 (iroh dial) API; exercised by unit tests now.
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

pub const KILL_DEADLINE: Duration = Duration::from_secs(2);

pub type CloseFut<'a> = Pin<Box<dyn Future<Output = ()> + Send + 'a>>;

pub trait ActiveTransport: Send + Sync {
    /// Graceful close (e.g. `conn.close(0u32.into(), b"peer_kill")` + endpoint close).
    fn close<'a>(&'a self, reason: &'a str) -> CloseFut<'a>;
    fn label(&self) -> &str;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KillOutcome {
    pub had_transport: bool,
    /// true if graceful close missed the deadline and we hard-dropped.
    pub forced: bool,
    pub elapsed: Duration,
}

#[derive(Clone, Default)]
pub struct TransportSlot {
    inner: Arc<Mutex<Option<Box<dyn ActiveTransport>>>>,
}

impl TransportSlot {
    pub async fn set(&self, t: Box<dyn ActiveTransport>) {
        *self.inner.lock().await = Some(t);
    }

    pub async fn is_active(&self) -> bool {
        self.inner.lock().await.is_some()
    }

    pub async fn kill(&self, reason: &str) -> KillOutcome {
        self.kill_within(reason, KILL_DEADLINE).await
    }

    pub async fn kill_within(&self, reason: &str, deadline: Duration) -> KillOutcome {
        let start = Instant::now();
        let taken = self.inner.lock().await.take();
        let Some(t) = taken else {
            return KillOutcome { had_transport: false, forced: false, elapsed: start.elapsed() };
        };
        let forced = tokio::time::timeout(deadline, t.close(reason)).await.is_err();
        if forced {
            eprintln!("peer kill: {} close exceeded {:?}, dropped", t.label(), deadline);
        }
        drop(t);
        KillOutcome { had_transport: true, forced, elapsed: start.elapsed() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};

    struct Mock {
        delay: Duration,
        closed: Arc<AtomicBool>,
    }
    impl ActiveTransport for Mock {
        fn close<'a>(&'a self, _r: &'a str) -> CloseFut<'a> {
            Box::pin(async move {
                tokio::time::sleep(self.delay).await;
                self.closed.store(true, Ordering::SeqCst);
            })
        }
        fn label(&self) -> &str {
            "mock"
        }
    }

    #[tokio::test]
    async fn fast_close_is_graceful() {
        let slot = TransportSlot::default();
        let closed = Arc::new(AtomicBool::new(false));
        slot.set(Box::new(Mock { delay: Duration::from_millis(20), closed: closed.clone() })).await;
        let o = slot.kill("peer_kill").await;
        assert!(o.had_transport && !o.forced);
        assert!(closed.load(Ordering::SeqCst));
        assert!(!slot.is_active().await);
    }

    #[tokio::test]
    async fn hung_close_is_cut_within_2s() {
        let slot = TransportSlot::default();
        let closed = Arc::new(AtomicBool::new(false));
        slot.set(Box::new(Mock { delay: Duration::from_secs(30), closed: closed.clone() })).await;
        let o = slot.kill("peer_kill").await;
        assert!(o.had_transport && o.forced);
        assert!(o.elapsed < KILL_DEADLINE + Duration::from_millis(300), "{:?}", o.elapsed);
        assert!(!closed.load(Ordering::SeqCst));
        assert!(!slot.is_active().await);
    }

    #[tokio::test]
    async fn kill_without_transport_is_noop() {
        let o = TransportSlot::default().kill("peer_kill").await;
        assert!(!o.had_transport);
    }
}
