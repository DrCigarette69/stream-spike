//! Fake relay TCP: NDJSON lines (HELLO / AUTH_TICKET / OPEN / BYTES / CLOSE).
mod io;
mod stream;

pub use io::run_relay;
#[cfg(feature = "iroh")]
pub(crate) use io::handle_peer_conn;
pub use stream::{close_stream_on_peer, drop_peer, open_stream_to_peer, send_bytes};
