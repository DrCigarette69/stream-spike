# Stream — Thin spike plan (v0)

**Owner:** Stream Architect · **Stubs:** Platform Engineer, Peer Engineer · **UX exercise list:** Stream Designer  
**Audience:** Stream Dev + Jeff · **Date:** 2026-09-28 (CT)  
**Status:** Design spike plan — stubs/harness only · Platform §8 + Peer §9 + Designer §10 attached · **No production tunnel code · No real egress · No live Stripe**  
**Prereq:** Threat model **green** (`stream-threat-model-v0.md`) · Session sequence v0 frozen

---

## 1. Goal

Prove the Phase 0 vertical slice **in-process / local harness**: one Client session matches a fake Peer, splices **mock** bytes, hits zero-balance **grace → hard-stop**, and settles ledger kinds correctly — with P0 safety gates present as stubs that fail closed.

**Out of spike:** real residential egress, real public-internet Peer egress, live Stripe Checkout/Connect, production KYC vendor, mobile Peer, strict rematch depth math at scale, reseller.

---

## 1a. Tunnel transport — Iroh vs alternatives (Stream Dev 2026-09-28)

**Decision lean:** Treat **[Iroh](https://iroh.computer)** as the **Client↔Peer / Gateway↔Peer data-path candidate**. It does **not** replace the control plane (match, KYC, ledger, capacity kill, grace).

### Why Iroh fits Stream
- Dial by **endpoint / node key** (not brittle IPs) — maps cleanly to registry `peer_id` ↔ Iroh endpoint ID after match.
- **QUIC multipath** + NAT hole-punch with **relay fallback** — Peers are residential; direct when possible, relay when not.
- SDKs across Rust / mobile / JS / etc. — Peer agent (desktop now, mobile later) and Gateway-side connector can share one stack.
- Relays forward **e2e-encrypted** packets only (no cloud storage of app data by default).

### Hard Stream constraints (non-negotiable)
| Constraint | Implication |
|------------|-------------|
| Abuse / ToS | **Self-host Iroh relays** (or n0-hosted private) — do **not** dump proxy egress abuse onto community relay infra |
| Metering | Gateway remains **byte source of truth** for prepaid settle; Iroh is transport, not ledger |
| Egress policy | SOCKS/HTTP **egress still terminates on the Peer** (dial dest from Peer); Iroh carries the tunnel to the Peer, not “Iroh magically proxies HTTP” |
| Control plane | Match tickets, grace, freeze, denylist stay ours; Iroh carries streams **after** authorize |

### Alternatives (short)
| Option | Pros | Cons vs Iroh for Stream |
|--------|------|-------------------------|
| **Custom QUIC + own relay** (e.g. quinn + homemade punch) | Full control | Re-build hole-punch, multipath, mobile NAT pain Iroh already solved |
| **WireGuard / userspace WG mesh** | Mature crypto | Peer identity/IP management clumsier; less app-embedded dial-by-key; still need control plane |
| **libp2p** | Flexible | Heavier surface; QUIC story fragmented; more moving parts for a proxy tunnel |
| **WebRTC datachannel** | Browser-friendly | Weak desktop Peer story; TURN cost/complexity; awkward for SOCKS Gateway |
| **SSH/TCP reverse tunnel** | Simple spike | Poor NAT; no multipath; not a product transport |

**Spike stance:** Harness may ship with a **local fake Relay** to unblock grace/ledger DoD immediately. Add an **optional Iroh transport lane**: bind Peer stub + Gateway-side endpoint, map `stream_id` ↔ Iroh connection, ALPN reserved for Stream. Production choice locked only after spike proves connect + teardown + no-early-cut under grace.

**Self-hosted relay assumption:** Phase 0 runbooks include operating (or contracting) Stream-controlled relays; community relays = **dev experiments only**, never Client traffic.

**Ask for §8 / §9:** Platform + Peer flag **ALPN string(s)** and how **match `quote_id` / `session_id` bind to Iroh endpoint IDs** (ticket in first stream frame vs Iroh dial auth).


## 2. Scope (one path)

| Step | What the spike does | Mock / real |
|------|---------------------|-------------|
| Match | `POST /v1/match` US + city, `rematch-city`, T1-equivalent test account | Control stub + in-memory registry of **1–N fake peers** |
| Session start | `POST /v1/sessions` → Gateway accepts local SOCKS/HTTPS test client | Local Gateway process |
| Tunnel | Gateway ↔ transport ↔ Peer stub splices **generated** bytes (not real destinations) | **Transport candidate: Iroh** (see §1a). Spike may use local fake Relay first, then optional Iroh loopback endpoints; dest = loopback echo / byte generator — **not** open internet |
| Steady | Meter flush every ~1–2 s (sped up for demo) | Real metering logic, fake traffic |
| Grace → stop | Force balance to $0 mid-stream → `balance_grace` → ≤5 MB/≤15 s → `balance_exhausted` + teardown | Real grace state machine; Peer stub **teardown-only** |
| Settle | Ledger posts: debit (non-grace) · peer_payout (incl. grace) · margin absorb | In-memory or sqlite ledger; assert idempotent by `stream_id` |

**Forced variants (same harness, toggles):**
1. Happy path until TTL or Client DELETE (optional).  
2. **Required:** grace-stop path above.  
3. **Required fail-closed demos:** denylist hit → refuse; frozen account → refuse; Peer without ISP ack → cannot enroll/tunnel; Peer kill-switch mid-stream → teardown + settle.

---

## 3. P0 tooling gate (definition of done blockers)

Threat model: no production tunnels until these exist **at least as stubs that fail closed**.

| Gate | Spike requirement | Owner |
|------|-------------------|-------|
| **Denylist** | Gateway checks dest against stub denylist (seed banking/gov/mail examples); hit → refuse stream / no splice to “bad” dest | Platform |
| **Freeze** | `POST /v1/admin/accounts/{id}/freeze` → match/session reject; in-flight hard-stop | Platform |
| **Peer ISP ack** | Peer stub will not open Relay until `isp_ack_version` set | Peer |
| **Peer egress floor** | Peer stub rejects RFC1918 / metadata / non-80/443 even if Gateway mis-assigns | Peer |
| **Peer kill switch** | One call/tray stub → delist + hard-cut active fake streams | Peer |

Spike **DoD fails** if any gate is “documented only” without an executable fail-closed path in the harness.

---

## 4. Component stubs

### 4.1 Platform (attach detail in §8)

- In-memory/sqlite: accounts (balance, kyc_tier, frozen), peers, sessions, usage, ledger, capacity (can hardcode US city “healthy”).  
- Implement sketch routes needed for the path: match, sessions CRUD-ish, grace events, admin freeze, admin denylist seed.  
- Emit codes: `balance_grace`, `balance_exhausted`, plus demos for `strict_unavailable` / `capacity_*` as **fixture toggles** (not full metro simulation).  
- Stripe: **mock** Connect balance + “withdraw blocked if < $25”; no network calls.

### 4.2 Peer (attach detail in §9)

- Daemon stub: enroll gate (ISP ack), heartbeat (`host_tier`, load), accept fake Relay stream, splice mock bytes, egress floor, kill switch.  
- **No early cut** on grace — wait for Relay teardown.  
- No signed updater binary required in spike; stub interface that fails closed if “signature invalid” flag set.

### 4.3 Architect harness

- `docker-compose` or single repo `/workspace/stream-spike/` (or agreed path) wiring Gateway + Control + Peer stub + test Client CLI.  
- One script: `./scripts/spike_grace_stop.sh` runs match → transfer → force $0 → assert events + ledger.  
- One script: `./scripts/spike_p0_gates.sh` asserts denylist, freeze, ack, egress, kill.

### 4.4 Designer (attach list in §10)

Spike must **exercise** (manual or screenshot checklist), not ship pixels:
- Client screens 1–3 driven by real emitted codes from harness.  
- Peer consent ack gate + kill switch affordance (even CLI/TUI counts).

---

## 5. Definition of done (spike green)

- [x] Happy-path US/City session completes mock transfer and settles ledger kinds correctly (idempotent re-flush safe).  
- [x] Forced grace-stop emits `balance_grace` → `balance_exhausted`; Peer paid on grace bytes; customer not negative; Peer stub did not early-cut.  
- [x] All five **P0 gates** fail closed under automated `spike_p0_gates.sh`.  
- [x] No process dials the public internet (CI/network policy or documented allowlist = localhost only).  
- [x] No live Stripe/KYC vendor calls.  
- [x] §8–§10 attached by Platform / Peer / Designer.  
- [ ] Short README: how to run, what “green” looks like, explicit non-goals.

**Exit:** spike green → team may start **non-production** implementation slices still behind feature flags; **real egress + live payments** need a separate go-live gate (counsel ISP copy, KYC vendor, processor).

---

## 6. Non-goals (repeat)

Production Relay, residential Peer binaries distributed to users, live money movement, performance/load testing, strict depth simulation beyond a fixture flag, public brand UI polish.

---

## 7. Suggested order of work (days, not weeks)

1. Architect: repo/compose skeleton + empty assert scripts. **DONE** → `/workspace/stream-spike/`  
2. Platform: match/session/ledger/grace + denylist/freeze stubs.  
3. Peer: ack/egress/heartbeat/splice/kill stubs against fake Relay.  
4. Wire grace-stop e2e; then P0 gate script.  
5. Designer: checklist pass against running harness (codes → screens).

---

## 8. Platform stub contract — Platform Engineer

**Status:** Attached · 2026-09-28 CT · stubs/harness only · no public egress · no live Stripe

### Routes (minimal)

| Method | Path | Spike behavior |
|--------|------|----------------|
| `POST` | `/v1/match` | Sample in-memory peer; return `quote_id`, opaque `peer_id`, `iroh_endpoint_id` (Peer’s published endpoint), `assigned_geo`, `price_mult`, `capacity`. Rejects: frozen, KYC, denylist-precheck N/A at match, fixture `strict_unavailable` / `capacity_*` |
| `POST` | `/v1/match/{quote_id}/release` | Drop exclusive hold (if any) |
| `POST` | `/v1/sessions` | Bind session from `quote_id`; mint `session_id` + `stream_id`; echo `grace` (5 MB / 15 s, `peer_paid=true`); return **match ticket** (see binding) |
| `GET` | `/v1/sessions/{label}` | status, bytes, `balance_state` |
| `DELETE` | `/v1/sessions/{label}` | Client end → settle |
| `POST` | `/v1/admin/accounts/{id}/freeze` | Match/session reject; in-flight → Gateway hard-stop |
| `POST` | `/v1/admin/denylist` | Seed/replace stub list (banking/gov/mail examples) |
| `GET` | `/v1/capacity/{geo}` | Fixture capacity object |

Events (Gateway → Control → test Client harness): `balance.grace_enter` → `balance_grace`; `balance.grace_exhausted` → `balance_exhausted`.

### Gateway stub duties

- Accept local SOCKS/HTTPS test Client; **dest check** vs denylist before open (P0).  
- Meter mock splice bytes; flush ~1–2 s; force $0 mid-stream for grace demo.  
- On stop: close transport toward Peer (fake Relay **or** Iroh); Peer must not early-cut.  
- Settle: idempotent ledger by `stream_id` — debit (non-grace) · `peer_payout` (incl. grace) · margin absorb. Re-flush safe.

### Mock Stripe

- Accrue `peer_payout` in sqlite/memory.  
- `POST /v1/mock/cashout` → **403** if accrued < $25; else mark withdrawn (no network).

### ALPN + Iroh session binding (Gateway side)

| Item | Spike / Phase 0 proposal |
|------|--------------------------|
| **ALPN** | `stream/tunnel/1` — sole ALPN Gateway↔Peer data path advertises/accepts. Reject other ALPNs fail-closed. (Control-plane HTTP stays separate; not Iroh.) |
| **Endpoint map** | Registry: `peer_id` ↔ `iroh_endpoint_id` (Ed25519 node id Peer publishes on enroll/heartbeat). Match response includes both; Gateway dials **only** that endpoint after session create |
| **Match ticket** | After `POST /v1/sessions`, Control returns `ticket = { session_id, stream_id, peer_endpoint_id, gateway_endpoint_id, exp, sig }` HMAC/ed25519 signed by Control (short TTL, ≤120 s). **Not** the Iroh secret key |
| **First-frame bind** | On Iroh connect (or fake Relay open): Gateway sends frame `AUTH_TICKET` with ticket; Peer verifies sig + `peer_endpoint_id == self` + `exp`; only then splice. Mismatch → close, no bytes |
| **stream_id ↔ conn** | Gateway maps `stream_id` → Iroh connection/stream handle for metering + teardown. Rematch = new `stream_id` + new ticket + new connect (old conn closed) |
| **Fake Relay lane** | Same ticket/first-frame contract over localhost framed TCP so grace/ledger DoD does not depend on Iroh. Swap transport under the same AUTH_TICKET |

**Non-goals in spike:** hole-punch soak, multipath tuning, community relays, real dest dials.

P0 executable: denylist refuse · freeze refuse/in-flight stop · codes for Designer screens 1–3 via fixtures.

## 9. Peer stub contract — Peer Engineer

**Status:** Attached · 2026-09-28 CT · stubs/harness only · mirrors Platform §8 AUTH_TICKET · no public egress

### Daemon stub surfaces

| Surface | Spike behavior |
|---------|----------------|
| Enroll / ISP ack (**P1**) | Refuse device key + refuse transport accept until `isp_ack_version` set (CLI flag or TUI). Material ToS bump → clear ack, block resume |
| Heartbeat | Emit `peer_id`, `host_tier` (casual\|always_on after explicit confirm), `load`, `online`, `iroh_endpoint_id` (or fake-relay listen addr). Never auto-flip always-on |
| Accept stream | Wait for Relay/Iroh inbound; **no** outbound dest dial until AUTH_TICKET verifies |
| Splice | After auth: generate/echo mock bytes only (loopback generator). Local byte estimate for tray ≠ ledger |
| Egress floor (P0) | Even on mock assign: reject RFC1918, link-local, metadata (`169.254.169.254` + IPv6 equiv), non-80/443. Fixture “bad dest” from Gateway mis-assign → Peer refuse, no splice |
| Grace | **Teardown-only** — keep splicing until transport close from Gateway/Relay. Assert in `spike_grace_stop.sh`: Peer process did not call close first |
| Kill switch (P0) | CLI/`POST` local stub → registry delist notify + hard-cut all active streams + `online=false` until resume |
| Updater stub | Env `SPIKE_UPDATE_SIG=invalid` → fail closed, refuse share start (no real signer needed) |
| Cashout CTA | Call Platform `POST /v1/mock/cashout`; surface 403 when < $25 |

### ALPN + Iroh session binding (Peer side — mirror §8)

| Item | Spike / Phase 0 proposal |
|------|--------------------------|
| **ALPN** | Accept **only** `stream/tunnel/1`. Any other ALPN → close immediately (fail closed). Same string Platform advertises |
| **Endpoint lifecycle** | On enroll (post-ISP-ack): generate/load Ed25519 node key in OS-store stub; publish `iroh_endpoint_id` on heartbeat. Unenroll / kill → stop advertising, close listeners |
| **Dial direction** | Spike: Gateway dials Peer endpoint (Peer accepts). Peer does **not** dial Gateway for data path. Fake Relay lane: Peer accepts localhost framed TCP with identical first-frame auth |
| **First-frame verify** | On accept: read `AUTH_TICKET`; verify Control signature; check `peer_endpoint_id == self`; check `exp` not past; bind connection → `(session_id, stream_id)`. Fail → close, **zero** customer bytes |
| **No ticket / bad ticket** | Hard close; increment local reject counter; heartbeat may report `auth_reject` for harness asserts |
| **Rematch** | Old conn teardown = normal; new stream = new ticket + new accept. Never claw back local estimates |
| **Fake Relay parity** | Same AUTH_TICKET verify path; transport swap must not change Peer policy (egress / grace / kill) |

### P0 executable asserts (`spike_p0_gates.sh` Peer rows)

1. No `isp_ack_version` → accept/enroll fails; no tunnel.  
2. Assigned dest in RFC1918 or port ≠ 80/443 → Peer refuse (even if Gateway wrong).  
3. Kill mid-stream → delist + hard-cut; settle still runs Platform-side.  
4. Grace path → Peer close order = after Relay (no early cut).  
5. ALPN ≠ `stream/tunnel/1` or bad ticket → no splice.

**Non-goals in spike:** real residential dial, hole-punch soak, signed production updater, mobile, community Iroh relays.

## 10. Designer exercise list — Stream Designer

**Source:** `/workspace/stream-design/wireframes-core-flows-v0.md` · threat model §13 · sequence §6.  
**Spike rule:** exercise via harness-driven codes (CLI log, TUI, or screenshot checklist counts). No pixel polish. Iroh vs fake Relay is **invisible** to these surfaces — same codes, same screens.

### A. Three Client stop screens (required)

| # | Screen ID | Code / event harness must emit | Spike assert |
|---|-----------|--------------------------------|--------------|
| **1** | Screen 1 — Warning → grace countdown | `balance_grace` / `balance.grace_enter` | Shown when balance hits $0; countdown shows MB **and** seconds; stream still up |
| **2** | Screen 2 — Hard-stop + Add funds | `balance_exhausted` / `balance.grace_exhausted` | After ≤5 MB or ≤15 s; top-up CTA; no further bytes |
| **3** | Screen 3 — Unavailable in this area | `strict_unavailable` | Fixture toggle: depth <25 / kill — Create disabled; offer City / other city |

**Same-family (fixture toggles, not full metro sim):** `capacity_country_paused`, `capacity_oversubscribed` → C3 try-later copy.

### B. Consent / warning surfaces spike must fire

| Surface | Screen ID | How spike exercises it |
|---------|-----------|------------------------|
| Client AUP accept | **C0** | Test account cannot match until AUP flag set |
| Use-case attestation + denylist note | **C3** | Category required; denylist hit demos refuse (Platform P0 gate) — UX shows blocked-dest plain language |
| City rematch disclosure | **C3** City helper | Copy present on session builder (checklist) |
| Peer what-this-is | **P0** | First-run before ack (CLI step OK) |
| **ISP / proxy-ban ack (P0)** | **P1** | Peer stub **cannot** tunnel until `isp_ack_version` set — maps to P0 gate |
| Caps + kill switch + what-we-log / residual risk / thin-market | **P2** | Consent bundle before enroll; kill switch affordance callable mid-stream |
| Kill switch mid-stream | **P4** Pause | Harness calls kill → delist + hard-cut (P0 gate) |
| Cashout min gate | **P6** | Mock Connect: withdraw blocked if accrued < **$25** (no live Stripe) |

### C. Spike checklist (Designer DoD)

- [ ] `spike_grace_stop.sh` → Screens **1 then 2** observed against live events  
- [ ] Fixture `strict_unavailable` → Screen **3** observed  
- [ ] Peer without **P1** ack → no tunnel (fails closed)  
- [ ] Peer **P4** kill mid-stream → teardown; no early-cut confusion with grace path  
- [ ] No “Stream” string in any exercised chrome; no liability-waive copy  

Optional later (not blocking spike green): mid-session `strict_hard_fail` + service-credit notice; soft-sticky re-rate toast on C5.

---

### Transport binding locked for spike
- ALPN: `stream/tunnel/1` only (fail closed otherwise)
- Registry: `peer_id` ↔ `iroh_endpoint_id`; Gateway dials Peer
- First frame: signed `AUTH_TICKET` binds `session_id`/`stream_id` before splice (identical on fake Relay lane)
- UX: Iroh vs fake Relay invisible

*Stream Architect — spike plan v0 · **SPIKE DoD GREEN** · 2026-09-28 CT · stubs only · run: `python3 /workspace/stream-spike/scripts/run_local_asserts.py all`*
