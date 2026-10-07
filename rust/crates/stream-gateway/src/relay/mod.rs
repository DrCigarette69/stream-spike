//! Fake relay TCP: NDJSON lines (HELLO / AUTH_TICKET / OPEN / BYTES / CLOSE).
mod io;
mod stream;

pub use io::run_relay;
pub use stream::{close_stream_on_peer, drop_peer, open_stream_to_peer, send_bytes};
