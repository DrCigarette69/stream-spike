//! A4.4: persistent daily egress byte counter (iroh_pilot). File next to the
//! iroh key (`<dir of SPIKE_IROH_KEY_PATH>/.peer_egress_budget`, override
//! `SPIKE_EGRESS_BUDGET_PATH`), JSON `{"utc_day":"YYYY-MM-DD","bytes":n,
//! "allowlist_version":"<hex>"}`, mode 0600, written atomically (temp file in
//! the same dir, fsync, rename, fsync dir). Missing -> 0. Unreadable / corrupt /
//! group- or world-accessible -> fail closed for the rest of the UTC day
//! (`egress_budget_unreadable`). Another day in the file -> reset to 0.
use serde_json::{json, Value};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub const ENV_BUDGET_PATH: &str = "SPIKE_EGRESS_BUDGET_PATH";
pub const FILE_NAME: &str = ".peer_egress_budget";
pub const R_UNREADABLE: &str = "egress_budget_unreadable";
/// Flush when this many bytes are unsaved, or after `FLUSH_SECS`.
pub const FLUSH_BYTES: u64 = 1 << 20;
pub const FLUSH_SECS: u64 = 5;

pub fn budget_path(key_path: &str) -> PathBuf {
    if let Ok(p) = std::env::var(ENV_BUDGET_PATH) {
        if !p.trim().is_empty() {
            return PathBuf::from(p.trim());
        }
    }
    let k = Path::new(key_path);
    k.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new(".")).join(FILE_NAME)
}

/// Days since the Unix epoch -> `YYYY-MM-DD` (proleptic Gregorian, UTC).
pub fn day_str(days: u64) -> String {
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Loaded {
    /// Bytes already used today (0 when missing or from another day).
    Bytes(u64),
    /// Fail closed; detail is `state_corrupt` or `state_unreadable`.
    Unreadable(&'static str),
}

pub fn load(path: &Path, today: u64) -> Loaded {
    let meta = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Loaded::Bytes(0),
        Err(_) => return Loaded::Unreadable("state_unreadable"),
    };
    if !meta.file_type().is_file() || meta.permissions().mode() & 0o077 != 0 {
        return Loaded::Unreadable("state_unreadable");
    }
    let Ok(text) = fs::read_to_string(path) else { return Loaded::Unreadable("state_unreadable") };
    let Ok(v) = serde_json::from_str::<Value>(&text) else { return Loaded::Unreadable("state_corrupt") };
    let (Some(day), Some(bytes)) = (v.get("utc_day").and_then(Value::as_str), v.get("bytes").and_then(Value::as_u64))
    else {
        return Loaded::Unreadable("state_corrupt");
    };
    let today_s = day_str(today);
    if day.len() != 10 || !day.is_ascii() {
        return Loaded::Unreadable("state_corrupt");
    }
    match day.cmp(today_s.as_str()) {
        std::cmp::Ordering::Equal => Loaded::Bytes(bytes),
        std::cmp::Ordering::Less => Loaded::Bytes(0),
        // A future day (clock moved back): keep the count, never reset early.
        std::cmp::Ordering::Greater => Loaded::Bytes(bytes),
    }
}

/// Atomic write: `<path>.tmp.<pid>` (0600) -> fsync -> rename -> fsync dir.
pub fn save(path: &Path, today: u64, bytes: u64, version: &str) -> std::io::Result<()> {
    let dir = path.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new("."));
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| FILE_NAME.into());
    let tmp = dir.join(format!("{name}.tmp.{}", std::process::id()));
    let body = json!({"utc_day": day_str(today), "bytes": bytes, "allowlist_version": version}).to_string();
    let res = (|| {
        let mut f = OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp)?;
        f.set_permissions(fs::Permissions::from_mode(0o600))?;
        f.write_all(body.as_bytes())?;
        f.sync_all()?;
        fs::rename(&tmp, path)?;
        if let Ok(d) = fs::File::open(dir) {
            let _ = d.sync_all();
        }
        Ok(())
    })();
    if res.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    res
}
