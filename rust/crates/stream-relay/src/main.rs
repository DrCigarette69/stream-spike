//! A4.2 `stream-relay`: our self-hosted iroh relay (iroh-relay 0.95.1 server, pinned lock).
//! See `src/lib.rs` for env. Dev mode = plain http on 10.73.0.254 (a4_local builds only).
use iroh_relay::server::{AccessConfig, Access, Limits, RelayConfig as SrvRelay, Server, ServerConfig};
use stream_relay::{config_from, Mode};

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
    let lane = stream_proto::guard::Lane::build();
    let cfg = match config_from(|k| std::env::var(k).ok(), lane) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("stream-relay refused: {e}");
            std::process::exit(2);
        }
    };
    if let Mode::Tls { hostname, .. } = &cfg.mode {
        eprintln!(
            "stream-relay refused: relay_tls_todo: tls mode for {hostname} is a config hook only; the VPS \
             deploy (host, domain, certs) is pending Jeff (docs/PLATFORM_RUNBOOK.md A4.2)"
        );
        std::process::exit(2);
    }
    let access = match cfg.allow_endpoints.clone() {
        None => AccessConfig::Everyone,
        Some(ids) => AccessConfig::Restricted(Box::new(move |id| {
            let ok = ids.iter().any(|a| *a == id.to_string());
            Box::pin(async move { if ok { Access::Allow } else { Access::Deny } })
        })),
    };
    let server_cfg: ServerConfig<(), ()> = ServerConfig {
        relay: Some(SrvRelay {
            http_bind_addr: cfg.http_bind,
            tls: None,
            limits: Limits::default(),
            key_cache_capacity: None,
            access,
        }),
        quic: None,
        metrics_addr: None,
    };
    let server = match Server::spawn(server_cfg).await {
        Ok(s) => s,
        Err(e) => {
            eprintln!("stream-relay refused: bind {}: {e}", cfg.http_bind);
            std::process::exit(2);
        }
    };
    println!(
        "STREAM_RELAY_READY url={} http={:?} quic=off mode=dev access={}",
        cfg.url(),
        server.http_addr(),
        if cfg.allow_endpoints.is_some() { "restricted" } else { "everyone" }
    );
    let _ = tokio::signal::ctrl_c().await;
    let _ = server.shutdown().await;
}
