# Stream Gateway (Rust) — multi-stage. Build context: rust/ workspace root.
#   docker build -f crates/stream-gateway/Dockerfile -t stream-gateway:rust .
ARG RUST_VERSION=1.85
FROM rust:${RUST_VERSION}-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY pin-msrv-deps.sh restore_cargo_lock.sh ./
COPY crates ./crates
RUN cargo build --release -p stream-gateway \
  && strip target/release/stream-gateway

FROM debian:bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/stream-gateway /usr/local/bin/stream-gateway
EXPOSE 1080
CMD ["stream-gateway"]
