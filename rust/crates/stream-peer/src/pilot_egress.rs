//! A4.4 part 1: Peer-side pilot egress plane (iroh_pilot). All checks are
//! Architect's A4.1 `stream_proto::guard` (pilot.rs); this file only holds
//! state (switch, budget, open connections) and does the I/O. Per OPEN, doc
//! order: kill switch + allowlist version -> budget -> floor (#6, unchanged) ->
//! `resolve_and_pin` (allowlist before DNS, SSRF on every answer) -> connect to
//! that exact SocketAddr. Refusals log `a4_refuse_<reason>:<detail>`.
use crate::egress::egress_denied;
use crate::egress_budget::{self, Loaded, FLUSH_BYTES, FLUSH_SECS, R_UNREADABLE};
use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use stream_proto::guard::{
    check_egress_budget, resolve_and_pin, EgressAllow, EgressSwitch, GuardError, Lane, Resolver,
    StdResolver, REASON_EGRESS_BUDGET_EXCEEDED, REASON_EGRESS_OFF,
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::watch;
use tokio::time::{timeout, Duration};

pub type SharedPlane = Arc<Mutex<EgressPlane>>;
pub type DynResolver = Arc<dyn Resolver + Send + Sync>;
pub const P8_STALE: &str = "egress_state_stale";
/// No guard name exists for this; Peer-local per-connection reason.
pub const R_STREAM_LIMIT: &str = "egress_stream_limit";
pub const ENV_MAX_STREAMS: &str = "SPIKE_EGRESS_MAX_STREAMS";
pub const DEFAULT_MAX_STREAMS: usize = 2;

/// `SPIKE_EGRESS_MAX_STREAMS`: default 2 (doc), clamp 1..=16, invalid -> 2.
pub fn max_streams_from(v: Option<&str>) -> usize {
    v.and_then(|s| s.trim().parse::<usize>().ok()).map(|n| n.clamp(1, 16)).unwrap_or(DEFAULT_MAX_STREAMS)
}

/// One refused OPEN: stable reason (sent in AUTH_REJECT / CLOSE) + log line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refuse {
    pub reason: String,
    pub line: String,
}

impl From<GuardError> for Refuse {
    fn from(e: GuardError) -> Self {
        Refuse { reason: e.reason().to_string(), line: e.log_line() }
    }
}

/// P8 key for a plane-wide stop: kill switch -> `egress_off`, except detail
/// `flag_stale` -> `egress_state_stale`; mismatch / budget keep their reason.
pub fn p8_key(e: &GuardError) -> &'static str {
    if e.reason() == REASON_EGRESS_OFF && e.detail == "flag_stale" {
        P8_STALE
    } else {
        e.reason()
    }
}

#[derive(Debug)]
struct Conn {
    target: String,
    kill: watch::Sender<Option<&'static str>>,
}

pub struct EgressPlane {
    pub allow: EgressAllow,
    pub version: String,
    pub switch: EgressSwitch,
    pub max_age_ms: u64,
    pub cap: u64,
    pub used: u64,
    day: u64,
    epoch: Instant,
    conns: HashMap<String, Conn>,
    pending_close: Vec<(String, String)>,
    /// `Lane::build()` (Pilot unless stream-proto has `a4_local`); tests inject.
    pub lane: Lane,
    pub resolver: DynResolver,
    pub last_state: Option<(bool, String)>,
    /// `SPIKE_RELAY_ALLOW_URL` (ticket `relay_url` must equal it).
    pub relay_allow: Option<stream_proto::guard::RelayAllow>,
    pub max_streams: usize,
    /// Admitted at AUTH_TICKET, not yet connected (count toward the stream cap).
    reserved: HashSet<String>,
    /// Persistent daily counter file (None = memory only, unit tests).
    pub store: Option<PathBuf>,
    saved_used: u64,
    last_save: Instant,
    /// Counter file unreadable/corrupt/unwritable: fail closed until UTC day changes.
    pub unreadable: Option<&'static str>,
    /// UTC day source (days since epoch); tests inject.
    pub day_fn: fn() -> u64,
}

impl std::fmt::Debug for EgressPlane {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EgressPlane").field("version", &self.version).field("used", &self.used).finish()
    }
}

pub fn utc_day() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() / 86_400).unwrap_or(0)
}

impl EgressPlane {
    pub fn new(allow: EgressAllow, switch: EgressSwitch, max_age_ms: u64, cap: u64) -> Self {
        let version = allow.version();
        Self {
            allow,
            version,
            switch,
            max_age_ms,
            cap,
            used: 0,
            day: utc_day(),
            epoch: Instant::now(),
            conns: HashMap::new(),
            pending_close: Vec::new(),
            lane: Lane::build(),
            resolver: Arc::new(StdResolver),
            last_state: None,
            relay_allow: None,
            max_streams: DEFAULT_MAX_STREAMS,
            reserved: HashSet::new(),
            store: None,
            saved_used: 0,
            last_save: Instant::now(),
            unreadable: None,
            day_fn: utc_day,
        }
    }

    /// Load today's count from the budget file (missing -> 0; bad -> fail closed).
    pub fn attach_store(&mut self, path: PathBuf) {
        self.day = (self.day_fn)();
        match egress_budget::load(&path, self.day) {
            Loaded::Bytes(b) => {
                self.used = b;
                self.saved_used = b;
                self.unreadable = None;
            }
            Loaded::Unreadable(d) => {
                eprintln!("a4_refuse_egress_budget:{d}");
                self.unreadable = Some(d);
            }
        }
        eprintln!("peer egress_budget path={} bytes={} cap={}", path.display(), self.used, self.cap);
        self.store = Some(path);
    }

    /// Persist when >= 1 MB unsaved, >= 5 s since last write with changes, or `force`.
    /// Never overwrites an unreadable file before the day rolls over. A failed
    /// write fails closed (`state_unwritable`).
    pub fn flush(&mut self, force: bool) {
        let Some(path) = self.store.clone() else { return };
        if self.unreadable.is_some() || self.used == self.saved_used && !force {
            return;
        }
        let due = force
            || self.used.saturating_sub(self.saved_used) >= FLUSH_BYTES
            || self.last_save.elapsed().as_secs() >= FLUSH_SECS;
        if !due {
            return;
        }
        match egress_budget::save(&path, self.day, self.used, &self.version) {
            Ok(()) => {
                self.saved_used = self.used;
                self.last_save = Instant::now();
            }
            Err(e) => {
                eprintln!("a4_refuse_egress_budget:state_unwritable:{e}");
                self.unreadable = Some("state_unwritable");
            }
        }
    }

    /// Reserve a stream slot (per-connection; never P8). Ok(true) = new reservation.
    pub fn reserve(&mut self, sid: &str) -> Result<bool, Refuse> {
        if self.reserved.contains(sid) || self.conns.contains_key(sid) {
            return Ok(false);
        }
        if self.conns.len() + self.reserved.len() >= self.max_streams {
            return Err(Refuse { reason: R_STREAM_LIMIT.into(), line: format!("a4_refuse_{R_STREAM_LIMIT}:{}", self.max_streams) });
        }
        self.reserved.insert(sid.to_string());
        Ok(true)
    }

    pub fn unreserve(&mut self, sid: &str) {
        self.reserved.remove(sid);
    }

    pub fn now_ms(&self) -> u64 {
        self.epoch.elapsed().as_millis() as u64
    }

    /// Control `/v1/egress/state` or Gateway `EGRESS_STATE`, received now.
    pub fn update_state(&mut self, flag_on: bool, version: &str) {
        let now = self.now_ms();
        self.switch.update(flag_on, version, now);
        self.last_state = Some((flag_on, version.to_string()));
    }

    fn roll_day(&mut self) {
        let d = (self.day_fn)();
        if d != self.day {
            self.day = d;
            self.used = 0;
            self.saved_used = u64::MAX; // force a fresh file for the new day
            self.unreadable = None;
            self.flush(true);
        }
    }

    /// Plane-wide: kill switch (env + flag + fresh), allowlist version, budget left.
    pub fn status_at(&mut self, now_ms: u64) -> Result<(), GuardError> {
        self.roll_day();
        self.switch.check_open(now_ms, &self.version)?;
        if let Some(d) = self.unreadable {
            return Err(GuardError { reason: R_UNREADABLE, detail: d.to_string() });
        }
        check_egress_budget(self.used, 1, self.cap)
    }

    pub fn status(&mut self) -> Result<(), GuardError> {
        let now = self.now_ms();
        self.status_at(now)
    }

    /// Pre-DNS checks for one OPEN: plane status, then the A3 floor.
    pub fn admit(&mut self, host: &str, port: u16) -> Result<(), Refuse> {
        self.status()?;
        if let Some(r) = egress_denied(host, port) {
            return Err(Refuse { line: format!("a4_refuse_egress_floor:{r}:{host}:{port}"), reason: r });
        }
        Ok(())
    }

    /// Count bytes (either direction) against today's cap.
    pub fn add_bytes(&mut self, n: u64) -> Result<(), GuardError> {
        self.roll_day();
        if let Some(d) = self.unreadable {
            return Err(GuardError { reason: R_UNREADABLE, detail: d.to_string() });
        }
        let r = check_egress_budget(self.used, n, self.cap);
        // Over the cap: count up to the cap so a restart still sees it hit.
        self.used = if r.is_ok() { self.used + n } else { self.cap.max(self.used) };
        self.flush(r.is_err());
        r
    }

    pub fn register(&mut self, sid: &str, target: String) -> watch::Receiver<Option<&'static str>> {
        let (tx, rx) = watch::channel(None);
        self.reserved.remove(sid);
        self.conns.insert(sid.to_string(), Conn { target, kill: tx });
        rx
    }

    pub fn unregister(&mut self, sid: &str) {
        self.conns.remove(sid);
        self.reserved.remove(sid);
        self.flush(true);
    }

    pub fn open_streams(&self) -> usize {
        self.conns.len()
    }

    /// Close every pilot egress connection; queues CLOSE frames for the session.
    pub fn close_all(&mut self, reason: &'static str) -> usize {
        let n = self.conns.len();
        for (sid, c) in self.conns.drain() {
            let _ = c.kill.send(Some(reason));
            eprintln!("peer egress_close {sid} {} reason={reason}", c.target);
            self.pending_close.push((sid, reason.to_string()));
        }
        self.reserved.clear();
        self.flush(true);
        n
    }

    pub fn take_pending_close(&mut self) -> Vec<(String, String)> {
        std::mem::take(&mut self.pending_close)
    }

    /// Queue a Peer-side CLOSE for one stream (e.g. pinned connect refused).
    pub fn queue_close(&mut self, sid: &str, reason: &str) {
        self.pending_close.push((sid.to_string(), reason.to_string()));
    }

    /// Gateway CLOSE / per-stream end: drop one connection, no CLOSE echoed.
    pub fn close_one(&mut self, sid: &str, reason: &'static str) {
        self.reserved.remove(sid);
        if let Some(c) = self.conns.remove(sid) {
            let _ = c.kill.send(Some(reason));
        }
    }

    /// Watchdog step at `now_ms`: any plane-wide stop closes everything now and
    /// returns the P8 key (after the start-up grace, or when streams were closed).
    pub fn tick_at(&mut self, now_ms: u64) -> Option<&'static str> {
        self.flush(false);
        let e = self.status_at(now_ms).err()?;
        let closed = self.close_all(e.reason()) > 0;
        (closed || now_ms >= self.max_age_ms).then(|| p8_key(&e))
    }
}

fn refused(r: Refuse) -> Refuse {
    eprintln!("{}", r.line);
    r
}

/// Status + floor + `resolve_and_pin` (blocking resolver off the runtime).
/// Reserves one of `max_streams` slots for `sid` (released on refusal / close).
pub async fn check_and_pin(plane: &SharedPlane, sid: &str, host: &str, port: u16) -> Result<SocketAddr, Refuse> {
    let (allow, resolver, lane, fresh) = {
        let mut p = plane.lock().unwrap();
        p.admit(host, port).map_err(refused)?;
        let fresh = p.reserve(sid).map_err(refused)?;
        (p.allow.clone(), p.resolver.clone(), p.lane, fresh)
    };
    let res = resolve_checked(host, port, allow, resolver, lane).await;
    if res.is_err() && fresh {
        plane.lock().unwrap().unreserve(sid);
    }
    res
}

async fn resolve_checked(
    host: &str,
    port: u16,
    allow: EgressAllow,
    resolver: DynResolver,
    lane: Lane,
) -> Result<SocketAddr, Refuse> {
    let (h, hp) = (host.to_string(), port);
    let r = tokio::task::spawn_blocking(move || resolve_and_pin(&h, hp, &allow, resolver.as_ref(), lane)).await;
    match r {
        Ok(Ok(addr)) => Ok(addr),
        Ok(Err(e)) => Err(refused(e.into())),
        Err(_) => Err(refused(Refuse { reason: "egress_resolve_failed".into(), line: format!("a4_refuse_egress_resolve_failed:{host}:{port}") })),
    }
}

/// Connect to the pinned address only (no hostname reaches the socket layer).
pub async fn connect_pinned(
    plane: &SharedPlane,
    sid: &str,
    host: &str,
    port: u16,
    addr: SocketAddr,
) -> Result<(TcpStream, watch::Receiver<Option<&'static str>>), Refuse> {
    let admitted = plane.lock().unwrap().admit(host, port);
    if let Err(r) = admitted {
        plane.lock().unwrap().unreserve(sid);
        return Err(refused(r));
    }
    let s = match timeout(Duration::from_secs(5), TcpStream::connect(addr)).await {
        Ok(Ok(s)) => s,
        _ => {
            plane.lock().unwrap().unreserve(sid);
            return Err(refused(Refuse { reason: "egress_connect_failed".into(), line: format!("peer egress_connect_failed:{host}:{port} pinned={addr}") }));
        }
    };
    let rx = plane.lock().unwrap().register(sid, format!("{host}:{port}"));
    eprintln!("peer egress_open {sid} {host}:{port} pinned={addr}");
    Ok((s, rx))
}

/// Copy both ways between the pinned upstream and `down`, counting every byte
/// against the cap. Ends on EOF, a plane-wide close, or the cap.
pub async fn pump<D: AsyncRead + AsyncWrite + Unpin>(
    plane: &SharedPlane,
    sid: &str,
    mut up: TcpStream,
    mut down: D,
    mut kill: watch::Receiver<Option<&'static str>>,
) -> Option<&'static str> {
    let early = *kill.borrow();
    if early.is_some() {
        return early;
    }
    let (mut ur, mut uw) = up.split();
    let (mut a, mut b) = ([0u8; 8192], [0u8; 8192]);
    let end = loop {
        let (n, to_up) = tokio::select! {
            _ = kill.changed() => break *kill.borrow(),
            r = ur.read(&mut a) => (r.unwrap_or(0), false),
            r = down.read(&mut b) => (r.unwrap_or(0), true),
        };
        if n == 0 {
            break None;
        }
        let over = plane.lock().unwrap().add_bytes(n as u64).is_err();
        if over {
            plane.lock().unwrap().close_all(REASON_EGRESS_BUDGET_EXCEEDED);
            break Some(REASON_EGRESS_BUDGET_EXCEEDED);
        }
        let w = if to_up { uw.write_all(&b[..n]).await } else { down.write_all(&a[..n]).await };
        if w.is_err() {
            break None;
        }
    };
    plane.lock().unwrap().unregister(sid);
    end
}

/// Part 1 OPEN (frames carry byte counts, not data yet): connect to the pinned
/// address and hold it, counting upstream bytes, until closed. Part 2 replaces
/// the local sink with the Gateway data path.
pub fn spawn_open(plane: SharedPlane, sid: String, host: String, port: u16, addr: SocketAddr) {
    tokio::spawn(async move {
        match connect_pinned(&plane, &sid, &host, port, addr).await {
            Ok((up, rx)) => {
                let (down, mut sink) = tokio::io::duplex(64 * 1024);
                let drain = tokio::spawn(async move {
                    let mut buf = [0u8; 8192];
                    while matches!(sink.read(&mut buf).await, Ok(n) if n > 0) {}
                });
                let _ = pump(&plane, &sid, up, down, rx).await;
                drain.abort();
            }
            Err(r) => plane.lock().unwrap().queue_close(&sid, &r.reason),
        }
    });
}
