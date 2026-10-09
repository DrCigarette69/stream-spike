//! Stream Gateway (Rust) — Alpha-2 parity with Python gateway/main.py.
//! HTTP admin + fake relay NDJSON + Control metering. SPIKE_IMPL=rust.

mod admin;
mod config;
mod conn;
mod control;
#[cfg(feature = "iroh")]
mod iroh_local;
mod meter;
mod relay;
mod state;

use config::Config;
use state::AppState;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cfg = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    };

    #[allow(unused_mut)]
    let mut st = AppState::new(
        cfg.control_url.clone(),
        cfg.transport.clone(),
        cfg.relay_listen_display(),
    );

    if cfg.is_iroh_local() {
        // A3.1: real iroh endpoint replaces the TCP relay listener.
        #[cfg(feature = "iroh")]
        {
            let icfg = iroh_local::config_from_env().unwrap_or_else(|e| {
                eprintln!("iroh_local refused: {e}");
                std::process::exit(2);
            });
            let ep = iroh_local::bind(&icfg).await.unwrap_or_else(|e| {
                eprintln!("iroh_local refused: {e}");
                std::process::exit(2);
            });
            st.gateway_endpoint_id = Some(ep.id().to_string());
            st.relay_listen = iroh_local::advertised_addr(&ep, &icfg).to_string();
            println!(
                "IROH_LOCAL_READY endpoint_id={} direct_addr={}",
                ep.id(),
                st.relay_listen
            );
            tokio::spawn(iroh_local::run_accept(st.clone(), ep));
        }
    } else {
        let st_relay = st.clone();
        let relay_addr = cfg.relay_listen;
        tokio::spawn(async move {
            if let Err(e) = relay::run_relay(st_relay, relay_addr).await {
                tracing::error!("relay server fatal: {e}");
                std::process::exit(1);
            }
        });
    }

    control::wait_control_healthy(&st).await;

    let app = admin::router(st);
    tracing::info!("gateway HTTP on {}", cfg.proxy_listen);
    let listener = tokio::net::TcpListener::bind(cfg.proxy_listen)
        .await
        .unwrap_or_else(|e| {
            eprintln!("bind gateway HTTP {}: {e}", cfg.proxy_listen);
            std::process::exit(1);
        });
    axum::serve(listener, app).await.expect("serve");
}
