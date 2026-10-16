//! A4.2 `stream-relay`: our self-hosted iroh relay (iroh-relay 0.95.1 server, pinned lock).
//! See `src/lib.rs` for env. Dev mode = plain http on 10.73.0.254 (a4_local builds only).
//! TLS mode = the VPS pilot relay (manual PEM files or Let's Encrypt TLS-ALPN-01).
use iroh_relay::server::{
    AccessConfig, Access, CertConfig, Limits, RelayConfig as SrvRelay, Server, ServerConfig, TlsConfig,
};
use stream_relay::{config_from, CertSource, Mode};

type Tls = TlsConfig<std::io::Error, std::io::Error>;

fn load_tls(hostname: &str, cert: &CertSource) -> Result<(CertConfig<std::io::Error, std::io::Error>, rustls::ServerConfig), String> {
    let builder = rustls::ServerConfig::builder_with_provider(std::sync::Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map_err(|e| format!("tls: {e}"))?
        .with_no_client_auth();
    match cert {
        CertSource::Files { cert, key } => {
            let certs: Vec<_> = rustls_pemfile::certs(&mut std::io::BufReader::new(
                std::fs::File::open(cert).map_err(|e| format!("relay_tls_cert: {cert}: {e}"))?,
            ))
            .collect::<Result<_, _>>()
            .map_err(|e| format!("relay_tls_cert: {cert}: {e}"))?;
            let key = rustls_pemfile::private_key(&mut std::io::BufReader::new(
                std::fs::File::open(key).map_err(|e| format!("relay_tls_key: {e}"))?,
            ))
            .map_err(|e| format!("relay_tls_key: {e}"))?
            .ok_or("relay_tls_key: no private key in file")?;
            let sc = builder.with_single_cert(certs.clone(), key).map_err(|e| format!("relay_tls: {e}"))?;
            Ok((CertConfig::Manual { certs }, sc))
        }
        CertSource::Acme { cache_dir, prod, contact } => {
            let mut acme = tokio_rustls_acme::AcmeConfig::new(vec![hostname.to_string()])
                .cache_option(Some(tokio_rustls_acme::caches::DirCache::new(cache_dir.clone())))
                .directory_lets_encrypt(*prod);
            if let Some(c) = contact {
                acme = acme.contact([format!("mailto:{c}")]);
            }
            let state = acme.state();
            let sc = builder.with_cert_resolver(state.resolver().clone());
            Ok((CertConfig::LetsEncrypt { state }, sc))
        }
    }
}

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
    let (tls, mode_label): (Option<Tls>, String) = match &cfg.mode {
        Mode::Dev => (None, "dev".into()),
        Mode::Tls { hostname, https_bind, cert, .. } => match load_tls(hostname, cert) {
            Ok((cert_cfg, server_config)) => (
                Some(TlsConfig {
                    https_bind_addr: *https_bind,
                    // QUIC (address discovery) stays off; the field is required but unused.
                    quic_bind_addr: *https_bind,
                    cert: cert_cfg,
                    server_config,
                }),
                match cert {
                    CertSource::Files { .. } => "tls cert=files".into(),
                    CertSource::Acme { prod, .. } => {
                        format!("tls cert=acme directory={}", if *prod { "letsencrypt" } else { "letsencrypt-staging" })
                    }
                },
            ),
            Err(e) => {
                eprintln!("stream-relay refused: {e}");
                std::process::exit(2);
            }
        },
    };
    let access = match cfg.allow_endpoints.clone() {
        None => AccessConfig::Everyone,
        Some(ids) => AccessConfig::Restricted(Box::new(move |id| {
            let ok = ids.iter().any(|a| *a == id.to_string());
            Box::pin(async move { if ok { Access::Allow } else { Access::Deny } })
        })),
    };
    let server_cfg: ServerConfig<std::io::Error, std::io::Error> = ServerConfig {
        relay: Some(SrvRelay {
            http_bind_addr: cfg.http_bind,
            tls,
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
        "STREAM_RELAY_READY url={} http={:?} https={:?} quic=off mode={} access={}",
        cfg.url(),
        server.http_addr(),
        server.https_addr(),
        mode_label,
        match &cfg.allow_endpoints {
            Some(v) => format!("restricted n={}", v.len()),
            None => "everyone".into(),
        }
    );
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("sigterm");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = term.recv() => {}
    }
    let _ = server.shutdown().await;
}
