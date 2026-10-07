# Stream Peer (Rust) — multi-stage. Build context: rust/ workspace root.
ARG RUST_VERSION=1.85
FROM rust:${RUST_VERSION}-bookworm AS builder
WORKDIR /build
COPY . .
RUN chmod +x pin-msrv-deps.sh restore_cargo_lock.sh 2>/dev/null || true \
  && if [ ! -f Cargo.lock ]; then ./pin-msrv-deps.sh; fi \
  && cargo build --release -p stream-peer \
  && strip target/release/stream-peer

FROM debian:bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/stream-peer /usr/local/bin/stream-peer
EXPOSE 9200
CMD ["stream-peer"]
