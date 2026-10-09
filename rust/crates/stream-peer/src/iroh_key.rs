//! A3.2 persistent per-peer iroh secret key (32 raw bytes).
//!
//! Path: `SPIKE_IROH_KEY_PATH` (default `./.peer_iroh_key`). Created once from
//! `/dev/urandom` with mode 0600; reloaded unchanged on restart; a file that is
//! group/world accessible or not exactly 32 bytes is refused. Part 2 feeds
//! [`IrohKey::bytes`] into `iroh::SecretKey::from_bytes` and derives the
//! endpoint ID from it (never from env).
#![allow(dead_code)] // part-2 (iroh dial) API; exercised by unit tests now.
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub const DEFAULT_KEY_PATH: &str = "./.peer_iroh_key";
pub const KEY_LEN: usize = 32;

pub struct IrohKey {
    bytes: [u8; KEY_LEN],
    pub path: PathBuf,
    pub created: bool,
}

impl std::fmt::Debug for IrohKey {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // never print key material
        f.debug_struct("IrohKey")
            .field("path", &self.path)
            .field("created", &self.created)
            .finish()
    }
}

impl IrohKey {
    pub fn bytes(&self) -> &[u8; KEY_LEN] {
        &self.bytes
    }
}

pub fn key_path_from_env() -> PathBuf {
    std::env::var("SPIKE_IROH_KEY_PATH")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_KEY_PATH))
}

fn random_key() -> Result<[u8; KEY_LEN], String> {
    let mut buf = [0u8; KEY_LEN];
    File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .map_err(|e| format!("iroh_key_rng:{e}"))?;
    if buf.iter().all(|b| *b == 0) {
        return Err("iroh_key_rng:all_zero".into());
    }
    Ok(buf)
}

fn load(path: &Path) -> Result<[u8; KEY_LEN], String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("iroh_key_read:{e}"))?;
    if !meta.is_file() {
        return Err("iroh_key_not_file".into());
    }
    let mode = meta.permissions().mode() & 0o777;
    if mode & 0o077 != 0 {
        return Err(format!("iroh_key_insecure_perms:{mode:o}"));
    }
    let data = std::fs::read(path).map_err(|e| format!("iroh_key_read:{e}"))?;
    if data.len() != KEY_LEN {
        return Err(format!("iroh_key_bad_len:{}", data.len()));
    }
    let mut out = [0u8; KEY_LEN];
    out.copy_from_slice(&data);
    Ok(out)
}

/// Load the key at `path`, creating it (0600, exclusive) if it does not exist.
pub fn load_or_create(path: &Path) -> Result<IrohKey, String> {
    if path.exists() {
        return Ok(IrohKey { bytes: load(path)?, path: path.to_path_buf(), created: false });
    }
    let bytes = random_key()?;
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("iroh_key_create:{e}"))?;
    // umask can only remove bits; enforce 0600 explicitly anyway.
    f.set_permissions(std::fs::Permissions::from_mode(0o600))
        .map_err(|e| format!("iroh_key_create:{e}"))?;
    f.write_all(&bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| format!("iroh_key_create:{e}"))?;
    Ok(IrohKey { bytes, path: path.to_path_buf(), created: true })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("a3key-{}-{name}", std::process::id()));
        let _ = std::fs::remove_file(&d);
        d
    }

    #[test]
    fn create_then_reload_same_bytes_0600() {
        let p = tmp("rt");
        let k1 = load_or_create(&p).unwrap();
        assert!(k1.created);
        let mode = std::fs::metadata(&p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        let k2 = load_or_create(&p).unwrap();
        assert!(!k2.created);
        assert_eq!(k1.bytes(), k2.bytes());
        std::fs::remove_file(&p).unwrap();
    }

    #[test]
    fn refuses_group_or_world_readable() {
        let p = tmp("perm");
        load_or_create(&p).unwrap();
        for m in [0o644, 0o640, 0o604] {
            std::fs::set_permissions(&p, std::fs::Permissions::from_mode(m)).unwrap();
            let e = load_or_create(&p).unwrap_err();
            assert!(e.starts_with("iroh_key_insecure_perms"), "{m:o}: {e}");
        }
        std::fs::remove_file(&p).unwrap();
    }

    #[test]
    fn refuses_wrong_length() {
        let p = tmp("len");
        std::fs::write(&p, b"short").unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(load_or_create(&p).unwrap_err().starts_with("iroh_key_bad_len"));
        std::fs::remove_file(&p).unwrap();
    }
}
