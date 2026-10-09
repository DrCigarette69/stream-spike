//! A3.1 smoke test client: a minimal iroh "peer" following the Peer↔Gateway framing contract.
//! NOT the real Peer (that is stream-peer, A3.2). Random key per run; never touches relays.
//!
//!   a31_test_client --gateway-id <ID> --direct-addr <ip:port> [--peer-id P] [--relay-url URL]
//!                   [--hello-endpoint-id ID] [--bind IP]   (local v4 bind, default 127.0.0.1;
//!                   must be on the gateway's subnet for multi-node, e.g. 10.73.0.11; guard-checked)
//!
//! Contract: dial gateway (ALPN stream/tunnel/1), open ONE bi stream, send NDJSON HELLO;
//! expect HELLO_OK; answer AUTH_TICKET with AUTH_OK; print every frame as `RX {json}`.
//! Stdout markers: CLIENT_ID, REFUSED <reason>, DIALED, RX ..., CONN_CLOSED <reason>.
//!
//! A4.2 relay-only mode (`--features iroh_pilot`; stand-in for the Peer's pilot dial):
//!   a31_test_client --gateway-id <ID> --pilot-relay <URL> --relay-allow <URL> [--bind IP] [--peer-id P]
//!   RelayMode::Custom(<URL>) + PathSelection::RelayOnly, no direct addr in the dial, discovery off.
//!   URL must pass guard::RelayAllow::parse(--relay-allow, Lane::build()) + check_relay_url_with.
//!   Extra markers: RELAY_ONLINE, RELAY_UNREACHABLE, CONNECT_FAILED, PATH <conn_type>.
use std::io::Write;
use std::net::{Ipv4Addr, Ipv6Addr, SocketAddrV4, SocketAddrV6};
use std::str::FromStr;

use iroh::{Endpoint, EndpointAddr, EndpointId, RelayMode, SecretKey};
use stream_proto::guard;
use stream_proto::relay::Hello;
use stream_proto::{ndjson_line, ALPN};
use tokio::io::{AsyncBufReadExt, BufReader};

fn arg(args: &[String], k: &str) -> Option<String> {
    args.iter().position(|a| a == k).and_then(|i| args.get(i + 1).cloned())
}

fn out(s: impl AsRef<str>) {
    println!("{}", s.as_ref());
    let _ = std::io::stdout().flush();
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let gw = arg(&args, "--gateway-id").expect("--gateway-id");
    let pilot_relay = arg(&args, "--pilot-relay");
    let direct = match (&pilot_relay, arg(&args, "--direct-addr")) {
        (_, Some(d)) => d,
        (Some(_), None) => String::new(),
        (None, None) => panic!("--direct-addr"),
    };
    let peer_id = arg(&args, "--peer-id").unwrap_or_else(|| "peer_a31".into());
    if let Some(url) = pilot_relay {
        return pilot::run(&args, &gw, &url, peer_id).await;
    }

    // Guard first: nothing binds or dials unless addr + relay pass A3.0.
    if let Err(e) = guard::check_relay_url(arg(&args, "--relay-url").as_deref()) {
        out(format!("REFUSED {} {}", e.reason(), e.log_line()));
        std::process::exit(3);
    }
    let addr = match guard::check_direct_addr(&direct) {
        Ok(a) => a,
        Err(e) => {
            out(format!("REFUSED {} {}", e.reason(), e.log_line()));
            std::process::exit(3);
        }
    };
    let gw_id = EndpointId::from_str(&gw).expect("gateway id parses");
    let bind_ip: Ipv4Addr = arg(&args, "--bind")
        .map(|b| b.parse().expect("--bind must be an IPv4 address"))
        .unwrap_or(Ipv4Addr::LOCALHOST);
    if let Err(e) = guard::check_ip(bind_ip.into()) {
        out(format!("REFUSED {} {}", e.reason(), e.log_line()));
        std::process::exit(3);
    }

    let mut seed = [0u8; 32];
    std::io::Read::read_exact(&mut std::fs::File::open("/dev/urandom").unwrap(), &mut seed).unwrap();
    let key = SecretKey::from_bytes(&seed);
    let my_id = key.public().to_string();
    out(format!("CLIENT_ID {my_id}"));
    let hello_id = arg(&args, "--hello-endpoint-id").unwrap_or_else(|| my_id.clone());

    let ep = Endpoint::empty_builder(RelayMode::Disabled)
        .clear_discovery()
        .secret_key(key)
        .bind_addr_v4(SocketAddrV4::new(bind_ip, 0))
        .bind_addr_v6(SocketAddrV6::new(Ipv6Addr::LOCALHOST, 0, 0, 0))
        .bind()
        .await
        .expect("bind client");
    let conn = ep
        .connect(EndpointAddr::new(gw_id).with_ip_addr(addr), ALPN.as_bytes())
        .await
        .expect("connect gateway");
    out(format!("DIALED gateway={} addr={addr}", conn.remote_id()));
    let (mut send, recv) = conn.open_bi().await.expect("open_bi");
    let hello = Hello::new(peer_id, hello_id, "v1", "always_on");
    send.write_all(&ndjson_line(&hello).unwrap()).await.expect("send HELLO");

    let mut lines = BufReader::new(recv).lines();
    loop {
        tokio::select! {
            l = lines.next_line() => match l {
                Ok(Some(line)) => {
                    out(format!("RX {line}"));
                    let v: serde_json::Value = serde_json::from_str(&line).unwrap_or_default();
                    if v.get("type").and_then(|t| t.as_str()) == Some("AUTH_TICKET") {
                        let ok = serde_json::json!({"type": "AUTH_OK"});
                        let _ = send.write_all(&ndjson_line(&ok).unwrap()).await;
                    }
                }
                Ok(None) => { out("STREAM_EOF"); break; }
                Err(e) => { out(format!("STREAM_ERR {e}")); break; }
            },
            r = conn.closed() => { out(format!("CONN_CLOSED {r}")); break; }
        }
    }
    let r = tokio::time::timeout(std::time::Duration::from_secs(2), conn.closed()).await;
    if let Ok(r) = r {
        out(format!("CONN_CLOSED {r}"));
    }
    ep.close().await;
}

/// Speak the Peer↔Gateway NDJSON contract on an open connection (pilot mode).
#[cfg(feature = "iroh_pilot")]
async fn speak(ep: &Endpoint, conn: iroh::endpoint::Connection, peer_id: String, hello_id: String) {
    let gw_id = conn.remote_id();
    // conn_type stays None under RelayOnly in iroh 0.95.1; datagram counters are the evidence.
    let path = || {
        use iroh::Watcher;
        let m = &ep.metrics().magicsock;
        let udp = m.send_ipv4.get() + m.send_ipv6.get() + m.recv_data_ipv4.get() + m.recv_data_ipv6.get();
        let relay = m.send_relay.get() + m.recv_data_relay.get();
        let kind = if udp > 0 { "udp" } else if relay > 0 { "relay" } else { "none" };
        let ct = ep.conn_type(gw_id).map(|mut w| w.get().to_string()).unwrap_or_else(|| "unknown".into());
        format!("{kind} relay_datagrams={relay} udp_datagrams={udp} conn_type={ct}")
    };
    let (mut send, recv) = conn.open_bi().await.expect("open_bi");
    let hello = Hello::new(peer_id, hello_id, "v1", "always_on");
    send.write_all(&ndjson_line(&hello).unwrap()).await.expect("send HELLO");
    let mut lines = BufReader::new(recv).lines();
    loop {
        tokio::select! {
            l = lines.next_line() => match l {
                Ok(Some(line)) => {
                    out(format!("RX {line}"));
                    let v: serde_json::Value = serde_json::from_str(&line).unwrap_or_default();
                    match v.get("type").and_then(|t| t.as_str()) {
                        Some("AUTH_TICKET") => {
                            let ok = serde_json::json!({"type": "AUTH_OK"});
                            let _ = send.write_all(&ndjson_line(&ok).unwrap()).await;
                            out(format!("PATH {}", path()));
                        }
                        Some("HELLO_OK") => out(format!("PATH {}", path())),
                        _ => {}
                    }
                }
                Ok(None) => { out("STREAM_EOF"); break; }
                Err(e) => { out(format!("STREAM_ERR {e}")); break; }
            },
            r = conn.closed() => { out(format!("CONN_CLOSED {r}")); break; }
        }
    }
}

#[cfg(feature = "iroh_pilot")]
mod pilot {
    use super::*;
    use iroh::endpoint::PathSelection;
    use iroh::{RelayMap, RelayUrl};

    pub async fn run(args: &[String], gw: &str, url: &str, peer_id: String) {
        let lane = guard::Lane::build();
        let allow_raw = arg(args, "--relay-allow").unwrap_or_default();
        let allow = match guard::RelayAllow::parse(&allow_raw, lane) {
            Ok(a) => a,
            Err(e) => {
                out(format!("REFUSED {} {}", e.reason(), e.log_line()));
                std::process::exit(3);
            }
        };
        if let Err(e) = guard::check_relay_url_with(Some(url), Some(&allow)) {
            out(format!("REFUSED {} {}", e.reason(), e.log_line()));
            std::process::exit(3);
        }
        let relay = RelayUrl::from_str(&allow.url()).expect("relay url");
        let gw_id = EndpointId::from_str(gw).expect("gateway id parses");
        let bind_ip: Ipv4Addr = arg(args, "--bind")
            .map(|b| b.parse().expect("--bind must be an IPv4 address"))
            .unwrap_or(Ipv4Addr::LOCALHOST);
        if let Err(e) = guard::check_ip(bind_ip.into()) {
            out(format!("REFUSED {} {}", e.reason(), e.log_line()));
            std::process::exit(3);
        }
        let mut seed = [0u8; 32];
        std::io::Read::read_exact(&mut std::fs::File::open("/dev/urandom").unwrap(), &mut seed).unwrap();
        let key = SecretKey::from_bytes(&seed);
        let my_id = key.public().to_string();
        out(format!("CLIENT_ID {my_id}"));
        let relay_cfg = iroh::RelayConfig { url: relay.clone(), quic: None }; // no QAD UDP probes
        let ep = Endpoint::empty_builder(RelayMode::Custom(RelayMap::from(relay_cfg)))
            .path_selection(PathSelection::RelayOnly)
            .clear_discovery()
            .secret_key(key)
            .alpns(vec![])
            .bind_addr_v4(SocketAddrV4::new(bind_ip, 0))
            .bind_addr_v6(SocketAddrV6::new(Ipv6Addr::LOCALHOST, 0, 0, 0))
            .bind()
            .await
            .expect("bind client");
        let wait = std::time::Duration::from_secs(15);
        if tokio::time::timeout(wait, ep.online()).await.is_err() {
            out(format!("RELAY_UNREACHABLE {relay}"));
            ep.close().await;
            std::process::exit(4);
        }
        out(format!("RELAY_ONLINE {relay}"));
        // Relay-only dial: the address carries no IP, only our relay.
        let target = EndpointAddr::new(gw_id).with_relay_url(relay.clone());
        let conn = match tokio::time::timeout(wait, ep.connect(target, ALPN.as_bytes())).await {
            Ok(Ok(c)) => c,
            Ok(Err(e)) => {
                out(format!("CONNECT_FAILED {e}"));
                ep.close().await;
                std::process::exit(5);
            }
            Err(_) => {
                out("CONNECT_FAILED timeout");
                ep.close().await;
                std::process::exit(5);
            }
        };
        out(format!("DIALED gateway={} relay={relay}", conn.remote_id()));
        speak(&ep, conn, peer_id, my_id).await;
        ep.close().await;
    }
}

#[cfg(not(feature = "iroh_pilot"))]
mod pilot {
    pub async fn run(_args: &[String], _gw: &str, _url: &str, _peer_id: String) {
        super::out("REFUSED transport_not_pilot (build with --features iroh_pilot for --pilot-relay)");
        std::process::exit(3);
    }
}
