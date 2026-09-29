# Peer runbook — local assert + smoke

**Owner:** Peer Engineer · **Repo:** https://github.com/DrCigarette69/stream-spike  
**Scope:** `peer/` stub · fake Relay data path · no production tunnels · no real residential egress

## Prerequisites

- Python 3.11+ (stdlib only)
- Control + gateway already runnable (or use smokes that start them)
- Ports: Peer admin `9200` · dials fake Relay `9100` · talks to control `8080`
- From repo root

## Full DoD (preferred)

Same harness as Platform — Peer process is started automatically:

```bash
python3 scripts/run_local_asserts.py all
# expect: SPIKE_DOD_GREEN
```

Peer-owned P0 rows inside gates: ISP ack · egress floor · kill · AUTH_TICKET reject.

## Peer-only smoke

Starts control + gateway + **real** `peer/main.py` (not the mini Peer inside platform_smoke):

```bash
python3 scripts/peer_smoke.py
# expect: PEER_SMOKE_GREEN
```

Covers:

| Check | Expect |
|-------|--------|
| Egress unit | `10.0.0.1:443` / `echo.local:22` denied; `echo.local:443` allowed |
| Grace teardown-only | force-zero → exhaust; `early_cut_attempts == 0`; stream `closed_by=gateway` |
| Egress on AUTH | `/gw/start` to `192.168.1.50` → 403 |
| Bad ticket | `/gw/inject_bad_ticket` → rejected |
| ISP ack gate | clear ack → Peer disconnects; Gateway delists |
| Kill switch | `/peer/kill` → offline + delist |

## Manual Peer bring-up

```bash
# control + gateway first (see PLATFORM_RUNBOOK.md), then:

export CONTROL_URL=http://127.0.0.1:8080
export SPIKE_FAKE_RELAY_DIAL=127.0.0.1:9100
export SPIKE_PEER_ADMIN=127.0.0.1:9200
export SPIKE_ISP_ACK_VERSION=v1          # empty = fail closed (no HELLO)
export SPIKE_HOST_TIER=always_on
export SPIKE_PEER_ID=peer_demo
export SPIKE_ENDPOINT_ID=iroh_ep_demo_001
python3 -u peer/main.py &

curl -s http://127.0.0.1:9200/peer/status | python3 -m json.tool
curl -s http://127.0.0.1:1080/gw/peers | python3 -m json.tool
```

## Peer admin HTTP (spike)

| Method | Path | Role |
|--------|------|------|
| `GET` | `/peer/status` or `/health` | Ack, online, streams, `early_cut_attempts`, auth rejects |
| `POST` | `/peer/ack` | `{"isp_ack_version":"v1"}` — empty string clears ack + disconnects |
| `POST` | `/peer/kill` | Delist via control + hard-cut + stay paused |
| `POST` | `/peer/resume` | Clear kill flag (reconnects if ack set) |
| `POST` | `/peer/host_tier` | `casual` \| `always_on` (explicit only) |
| `POST` | `/peer/egress_check` | Unit-test floor: `{"host","port"}` → `{denied, reason}` |

## Fake Relay contract (Peer side)

See [`FAKE_RELAY_PROTOCOL.md`](FAKE_RELAY_PROTOCOL.md). Peer **dials** Gateway `:9100`.

1. `HELLO` with non-empty `isp_ack_version` + `endpoint_id`
2. `HELLO_OK` / ALPN `stream/tunnel/1`
3. On `AUTH_TICKET`: verify ALPN → egress floor → `POST /v1/tickets/verify` → `peer_endpoint_id == self` → `AUTH_OK` or `AUTH_REJECT`
4. `OPEN` / `BYTES` — splice mock only; **never** close early on grace
5. `CLOSE` from Gateway — teardown-only; set `closed_by=gateway`

## Mapping to Phase 0 issues

| Issue | Peer hook |
|-------|-----------|
| [#5](https://github.com/DrCigarette69/stream-spike/issues/5) ISP ack | `SPIKE_ISP_ACK_VERSION` / `POST /peer/ack` before tunnel |
| [#6](https://github.com/DrCigarette69/stream-spike/issues/6) egress | `egress_denied` on AUTH; `/peer/egress_check` |
| [#7](https://github.com/DrCigarette69/stream-spike/issues/7) kill | `POST /peer/kill` + control `/v1/peers/kill` |
| [#8](https://github.com/DrCigarette69/stream-spike/issues/8) AUTH_TICKET/ALPN | First-frame verify; reject bad ALPN/ticket |
| [#9](https://github.com/DrCigarette69/stream-spike/issues/9) grace | Teardown-only; `early_cut_attempts` must stay 0 |

## Failure tips

| Symptom | Check |
|---------|-------|
| Not on `/gw/peers` | Ack empty, kill set, wrong `SPIKE_FAKE_RELAY_DIAL`, gateway down |
| `auth_ticket_failed` | Endpoint ID ≠ ticket `peer_endpoint_id`; secret mismatch; ALPN |
| `egress_denied` | Dest RFC1918 / metadata / port ≠ 80\|443 (expected for gate tests) |
| Grace early-cut assert | Peer must not close before Gateway `CLOSE` |
| Stale peer after kill | Gateway TCP-close delist; nudge with `/gw/stop` or wait peek loop |

## Alpha-1 A1.2 — Peer tray / CLI walkthrough

One-command Designer walkthrough against live peer admin (enroll → ISP ack → kill → resume). Uses fake Relay only; no Iroh / no public egress.

```bash
python3 scripts/peer_alpha1_cli.py
# expect: PEER_ALPHA1_CLI_GREEN
# reuses :8080/:1080/:9200 if up; else starts stack like peer_smoke (empty ISP ack)
```

Covers A1.2 checklist from [`ALPHA1_WALKTHROUGH.md`](ALPHA1_WALKTHROUGH.md): P0 what-this-is · P1 gate (`understood:false` → `p1_ack_gate`) · P2 fixture greps · Sharing ON after ack · P4 kill / explicit resume · optional P6 `$25` cashout stub. Brand `[Brand]` · fail closed on `Stream` / waive strings in UX blobs.

See also thin status note: [`PEER_ALPHA1.md`](PEER_ALPHA1.md).

## Non-goals

Production Peer binaries, real dest dials, signed updater, mobile, community Iroh relays.

## HARDENING (#5 / #6 / #7 / #8)

Still stubs — no production tunnels.

| Issue | Behavior |
|-------|----------|
| #5 P1 | `GET /peer/consent/p1` + status `ux_prompt` emit Designer strings; `POST /peer/ack` with `understood:false` → 403 `p1_ack_gate` |
| #6 | Egress floor on AUTH + BYTES defense; `/peer/egress_check` |
| #7 P4 | `POST /peer/kill` sets `ux_status` / `sharing_status` to **Sharing paused** + detail line |
| #8 | Frame ALPN + ticket verify + optional `payload.alpn`; `POST /peer/verify_ticket` helper |

```bash
python3 scripts/peer_smoke.py
# expect: PEER_SMOKE_GREEN and PEER_HARDENING_GREEN
```

Copy source: [`P1_P4_COPY.md`](P1_P4_COPY.md) · counsel owns final P1 legal.
