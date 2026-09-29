# Stream — Phase 0 session sequence (v0)

**Owner:** Stream Architect · **Attach hops:** Platform Engineer, Peer Engineer · **UX map:** Stream Designer  
**Audience:** Stream Dev + Jeff · **Date:** 2026-09-28 (CT)  
**Status:** Shared design sequence — no production code · Platform + Peer hops confirmed  
**Aligns:** architecture sketch v0.1 · control-plane API v0.1 · peer MVP outline v0.1 · wireframes v0.1

Happy path + the three Client stop screens. Actors: **Client** · **Gateway** · **Control plane** · **Relay** · **Peer**.

---

## 0. Preconditions

| Check | Who | Fail → |
|-------|-----|--------|
| Account KYC ≥ selector needs | Control | `kyc_insufficient` |
| Selector parse (no country+strict, no H3 SKU) | Gateway / Control | `selector_conflict` |
| Prepaid balance > 0 (headroom beyond grace preferred) | Control | soft warn in UX; open still allowed if > 0 |
| Country sell not paused (`online ≥ 150`) | Control capacity | `capacity_country_paused` |
| Concurrent < `online × 0.5` | Control capacity | `capacity_oversubscribed` |
| If `rematch-strict`: always-on in-bound **N ≥ 25** and not `strict_kill` | Control capacity | `strict_unavailable` |
| Peer online, geo_verified, fraud OK, host_tier eligible | Registry | no-match → same as unavailable / retry |

---

## 1. Match

```
Client                Control                 Registry              Peer
  |-- POST /v1/match -->|                       |                     |
  |                     |-- sample eligible --->|                     |
  |                     |<-- peer_id + geo -----|                     |
  |<-- quote + capacity-|                       |                     |
```

**Platform hop:** `POST /v1/match` → `quote_id`, opaque `peer_id`, `assigned_geo`, `price_mult`, `price_per_gb`, `capacity`, exclusive `hold_expires_at` (5 min if exclusive).  
**Peer hop:** none yet (still heartbeat only).  
**Designer:** C3 inventory states from `capacity` / `strict_unavailable` before Create.

---

## 2. Session start → tunnel

```
Client          Control           Gateway            Relay              Peer
  |-- POST /v1/sessions -->|        |                  |                 |
  |<-- session + grace ----|        |                  |                 |
  |-- connect proxy ------|-------->|-- open stream -->|-- dial cmd ---->|
  |                       |         |                  |                 |-- dial host:port
  |<======== bytes =======|<=======|<==== splice =====|<==== splice ====|
```

**Platform hop:** `POST /v1/sessions` from `quote_id`; echo `grace` (`max_bytes=5_242_880`, `max_ms=15000`, `peer_paid=true`). Session store bind `(account, label) → peer, rematch_mode, price_mult, expires_at`.  
**Gateway:** auth key+suffixes, policy (dest attested + denylist), start byte counters (`stream_id`).  
**Peer hop:** tunnel worker accepts stream; local egress floor; splice only; local byte estimate for tray **≠** ledger.  
**Settle note:** no ledger post yet — metering batches mid-stream.

---

## 3. Steady state (optional rematch)

| Event | Behavior |
|-------|----------|
| Peer healthy | bytes flow; Gateway flushes usage every ~5–10 s to Control |
| Soft sticky (`rematch-city`) peer loss | Control `POST …/rematch` inside city label; may change delivered geo → **re-rate remaining bytes** at delivered geo |
| Strict peer loss | rematch only same city AND (≤15–25 km | H3); else **`strict_hard_fail`** → tear down + **service_credit** unused window |
| Hard peer loss | session error; no rematch |
| Exclusive + peer loss | no out-of-exclusivity rematch; credit unused exclusive premium + unused GB |

**Peer:** on rematch, old stream tears down via relay; new dial is a new stream — treat as normal teardown/start. Never claw back local estimates.

---

## 4. Grace → stop (zero balance)

```
Gateway                         Control                         Client UX
  |-- balance hits $0 --------->|-- balance.grace_enter ------->| balance_grace
  |   balance_state=grace       |                               | (warning + countdown)
  |-- grace MB or 15s hit ----->|-- balance.grace_exhausted --->| balance_exhausted
  |   hard-stop stream          |   balance_state=stopped       | (hard-stop screen)
  |-- close to Relay ---------->|                               |
Relay -- teardown -------------> Peer (keep splicing until close; no early cut)
```

**Rules (Locked):** whichever of **5 MB / 15 s** first after $0 → hard-stop.  
**Ledger on stop:** customer **no debit** for grace bytes; **`peer_payout`** still posts on gateway-verified grace GB; absorb on margin/COGS. No negative customer balance Phase 0.  
**Peer:** relay teardown = normal; Peer paid; do not cut early to “save” buyer.

---

## 5. Settle (end of stream / session)

Triggered by: grace exhaust, TTL expiry, Client `DELETE`, strict_hard_fail, exclusive loss, Peer kill-switch, or clean Client close.

```
Gateway -- final usage flush --> Control
Control:
  1. Idempotent ledger by stream_id
     - debit customer for non-grace metered GB × assigned (or re-rated) price_mult
     - peer_payout for all verified GB including grace
     - service_credit if strict_hard_fail / exclusive peer-loss rules apply
     - margin = debit − peer_payout − credits (− grace absorb)
  2. Session status → stopped / ended
  3. Release peer slot; update metro_capacity counters
Peer cashout (async, not per-stream): Stripe Connect withdraw when accrued ≥ $25
```

---

## 6. Client stop screens ↔ error codes

| # | Screen (Designer) | Code / event | When |
|---|-------------------|--------------|------|
| 1 | Warning → grace countdown | `balance_grace` / `balance.grace_enter` | Balance hit $0; stream still up |
| 2 | Hard-stop / top-up CTA | `balance_exhausted` / `balance.grace_exhausted` | Grace MB or 15 s hit |
| 3 | Unavailable in this area | `strict_unavailable` | Match-time depth < 25 or `strict_kill` |

Also map (not the “three,” but same family): `capacity_country_paused`, `capacity_oversubscribed` → try-later / country unavailable; `strict_hard_fail` → mid-session stop + credit notice.

---

## 7. Sequence checklist (owners) — **all green** (2026-09-28 CT)

| Step | Architect | Platform | Peer | Designer |
|------|-----------|----------|------|----------|
| Match + capacity rejects | ✅ rules | ✅ `/v1/match` + codes (§9) | ✅ heartbeat honesty (§10) | ✅ C3 inventory copy |
| Tunnel splice | ✅ spine | ✅ session bind + gateway meter (§9) | ✅ dial + egress + no early cut (§10) | ✅ live session chrome |
| Grace → stop | ✅ 5 MB / 15 s lock | ✅ events + ledger absorb (§9) | ✅ teardown only (§10) | ✅ screens 1–2 |
| Strict unavailable | ✅ N≥25 / kill | ✅ `strict_unavailable` (§9) | ✅ limited-eligibility tray (§10) | ✅ screen 3 |
| Settle | ✅ ledger kinds | ✅ idempotent posts (§9) | — | ✅ receipt / credit UX |
| Cashout | ✅ min $25 | ✅ Connect rail (§9) | ✅ CTA gate ≥$25 (§10) | ✅ P6 copy |

Phase 0 vertical-slice sequence doc is **complete** for design handoff. No production code yet.

---

## 8. Out of scope here

Production code, public brand strings, counsel ISP copy, crypto, mobile Peer, 48h/7d sticky.

*Stream Architect — session sequence v0 · 2026-09-28 CT*

---

## 9. Platform hop contracts (confirmed)

**Owner:** Platform Engineer · **Status:** Confirmed against control-plane API v0.1 · 2026-09-28 CT

| Hop | Contract |
|-----|----------|
| Match | `POST /v1/match` returns quote + `capacity`; rejects with `kyc_insufficient` / `selector_conflict` / `capacity_*` / `strict_unavailable` before any Peer work |
| Session start | `POST /v1/sessions` binds store, echoes `grace` (5 MB / 15 s, `peer_paid=true`); Gateway owns `stream_id` + meters |
| Usage flush | Gateway → Control every ~5–10 s; mid-stream balance check; enter grace at $0 |
| Grace events | Emit `balance.grace_enter` then `balance.grace_exhausted`; set `balance_state` ok→grace→stopped; instruct Gateway hard-stop → Relay teardown (Peer does not cut early) |
| Rematch | `POST /v1/sessions/{label}/rematch`; city soft re-rate; strict bound miss → `strict_hard_fail` + `service_credit` |
| Settle | Idempotent ledger by `stream_id`: debit (non-grace) · `peer_payout` (all verified GB incl. grace) · `service_credit` · margin (incl. grace absorb). Release peer slot + update `metro_capacity` |
| Cashout | Async Stripe Connect when accrued ≥ $25 — not per-stream |

Gateway closes the stream to Relay on stop; Peer hop = teardown only. Designer screens 1–3 map 1:1 to §6 codes — no Platform change needed.

Checklist rows for Platform (match, tunnel, grace, strict unavailable, settle, cashout): **green**.

---

## 10. Peer hop contracts (confirmed)

**Owner:** Peer Engineer · **Status:** Confirmed against peer MVP outline v0.1 · 2026-09-28 CT

| Hop | Contract |
|-----|----------|
| Pre-match | Heartbeat only: `host_tier` (casual|always_on), load, online, geo probe responses. No dial until Relay stream open. |
| Tunnel | Accept Relay dial cmd → local egress floor → DNS+TCP to `host:port` → splice. Tray byte estimate **≠** ledger. |
| Rematch | Old stream: Relay teardown = normal close. New stream = new dial. Never claw back local estimates. |
| Grace / Client hard-stop | **Teardown only** — keep splicing until Relay closes. No Peer-side early cut to “save” buyer. Grace GB still payable (Platform posts `peer_payout`). |
| Peer kill-switch / pause | Delist from registry; hard-cut active streams; notify Control. Triggers settle path like any peer loss for that stream’s rematch mode. |
| Capacity / strict kill | Peer does not enforce pool math. If Control marks limited eligibility, tray shows non-alarmist status (Designer chrome). |
| Cashout CTA | Surface Connect onboarding + gate Withdraw until accrued ≥ **$25**; Platform owns rail. |

Checklist rows for Peer (match heartbeat, tunnel, grace teardown-only, strict limited-eligibility, cashout gate): **green**.
