//! A3.1 helper: print the iroh EndpointId of the DEV-ONLY Gateway key.
//!
//!   cargo run --locked -p stream-gateway --features iroh --example print_dev_endpoint_id [KEY_PATH]
//!
//! KEY_PATH defaults to `crates/stream-gateway/dev/gateway_dev.key` (32 raw bytes).
//! Prints only the public EndpointId, never key material. Offline; no endpoint is bound.
use std::str::FromStr;

fn main() {
    let default = concat!(env!("CARGO_MANIFEST_DIR"), "/dev/gateway_dev.key");
    let path = std::env::args().nth(1).unwrap_or_else(|| default.to_string());
    let raw = std::fs::read(&path).unwrap_or_else(|e| panic!("read {path}: {e}"));
    let bytes: [u8; 32] = raw
        .as_slice()
        .try_into()
        .unwrap_or_else(|_| panic!("{path}: expected 32 raw bytes, got {}", raw.len()));
    let secret = iroh::SecretKey::from_bytes(&bytes);
    let id: iroh::EndpointId = secret.public();
    let s = id.to_string();
    let back = iroh::EndpointId::from_str(&s).expect("EndpointId::from_str round-trip");
    assert_eq!(back, id, "EndpointId round-trip mismatch");
    println!("{s}");
}
