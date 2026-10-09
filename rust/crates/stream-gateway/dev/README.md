# DEV-ONLY Gateway iroh key — NEVER use in production

`gateway_dev.key` is a fixed, **publicly committed** iroh secret key (32 raw bytes) for the
Alpha-3 `iroh_local` spike on one machine (netns `br-a3`, 10.73.0.0/24, no default route).
Anyone with this repo has it, so it authenticates nothing outside local dev.

- Generated once from `/dev/urandom` (2026-10-08). Do not regenerate casually: the
  EndpointId below is recorded in `rust/README.md` and `docs/ALPHA3_IROH.md`.
- Public EndpointId (`SPIKE_GATEWAY_ENDPOINT_ID`):
  `162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1`
- Re-derive: `cd rust && cargo run --locked -p stream-gateway --features iroh --example print_dev_endpoint_id`
- Git does not keep the 0600 mode; `chmod 600` after checkout if a loader checks perms.
- Production/later alphas: Gateway IDs are issued by Control (per room defaults). Never ship this key.
