//! Peer connection carried by the relay protocol: TCP (fake_relay / iroh_loopback) or,
//! with `--features iroh`, one iroh bi stream (A3.1 `iroh_local`). Same NDJSON either way.
use std::pin::Pin;
use std::task::{Context, Poll};
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio::net::TcpStream;

pub enum PeerConn {
    Tcp(TcpStream),
    #[cfg(feature = "iroh")]
    Iroh(crate::iroh_local::IrohStream),
}

impl PeerConn {
    /// Liveness probe for the enroll loop. `Some(false)` = gone, `Some(true)`/`None` = keep.
    pub async fn probe_alive(&self) -> bool {
        match self {
            PeerConn::Tcp(s) => {
                let mut buf = [0u8; 1];
                match tokio::time::timeout(std::time::Duration::from_millis(300), s.peek(&mut buf)).await {
                    Ok(Ok(0)) => false,
                    Ok(Ok(_)) => true,
                    Ok(Err(_)) => false,
                    Err(_) => true,
                }
            }
            #[cfg(feature = "iroh")]
            PeerConn::Iroh(s) => s.is_alive(),
        }
    }

    /// Hard close with a reason (iroh: QUIC CONNECTION_CLOSE carrying `reason`).
    pub async fn close_with(&mut self, reason: &str) {
        use tokio::io::AsyncWriteExt;
        match self {
            PeerConn::Tcp(s) => {
                let _ = s.shutdown().await;
            }
            #[cfg(feature = "iroh")]
            PeerConn::Iroh(s) => s.close_with(reason).await,
        }
        let _ = reason;
    }
}

impl AsyncRead for PeerConn {
    fn poll_read(self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &mut ReadBuf<'_>) -> Poll<std::io::Result<()>> {
        match self.get_mut() {
            PeerConn::Tcp(s) => Pin::new(s).poll_read(cx, buf),
            #[cfg(feature = "iroh")]
            PeerConn::Iroh(s) => Pin::new(&mut s.recv).poll_read(cx, buf),
        }
    }
}

impl AsyncWrite for PeerConn {
    fn poll_write(self: Pin<&mut Self>, cx: &mut Context<'_>, buf: &[u8]) -> Poll<std::io::Result<usize>> {
        match self.get_mut() {
            PeerConn::Tcp(s) => Pin::new(s).poll_write(cx, buf),
            #[cfg(feature = "iroh")]
            PeerConn::Iroh(s) => Pin::new(&mut s.send).poll_write(cx, buf).map_err(std::io::Error::other),
        }
    }
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        match self.get_mut() {
            PeerConn::Tcp(s) => Pin::new(s).poll_flush(cx),
            #[cfg(feature = "iroh")]
            PeerConn::Iroh(s) => Pin::new(&mut s.send).poll_flush(cx),
        }
    }
    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        match self.get_mut() {
            PeerConn::Tcp(s) => Pin::new(s).poll_shutdown(cx),
            #[cfg(feature = "iroh")]
            PeerConn::Iroh(s) => Pin::new(&mut s.send).poll_shutdown(cx),
        }
    }
}
