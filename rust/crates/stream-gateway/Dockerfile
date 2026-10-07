# Stream Gateway (Rust) — multi-stage. Build context: rust/ workspace root.
ARG RUST_VERSION=1.85
FROM rust:${RUST_VERSION}-bookworm AS builder
WORKDIR /build
COPY . .
RUN chmod +x pin-msrv-deps.sh restore_cargo_lock.sh 2>/dev/null || true \
  && if [ ! -f Cargo.lock ]; then ./pin-msrv-deps.sh; fi \
  && cargo build --release -p stream-gateway \
  && strip target/release/stream-gateway

FROM debian:bookworm-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/stream-gateway /usr/local/bin/stream-gateway
EXPOSE 1080
CMD ["stream-gateway"]
