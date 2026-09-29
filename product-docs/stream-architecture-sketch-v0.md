# Stream — One-page architecture sketch (v0.1)

**Audience:** Stream Dev + Jeff · **Author:** Stream Architect · **Updated:** 2026-09-28 (CT)  
**Status:** Design sketch only — no implementation. Aligns with eng handoff + decision log + Stream Dev locks.  
**Codename:** Stream (internal). Public brand TBD — do not ship “Stream” in customer/Peer UI.

---

## What this system is

Compliant opt-in residential proxy: **Clients** buy metered exits by geo/specificity; **Peers** run a primary-purpose app and get paid per **gateway-verified** GB. Not silent SDK P2P. Settlement **locked:** prepaid USD in → per-GB Peer payout out (fiat Phase 0).

---

## Four boxes

### 1. Client (buyer)
- Connects to a **regional edge gateway** (HTTPS / HTTP / SOCKS5).
- Auth = username + key; behavior in **key suffixes** (country/city, session label + TTL, rematch mode, optional ASN/exclusive — fail-closed on conflicts/over-tier).
- Sees sold geo as continent → country → city (+ ASN at T3). **H3 is internal only** — not a public SKU.
- Rematch modes: `rematch-city` (Phase 0 default), `rematch-strict` (locality-bounded; priced/gated), `hard` (same peer only).
- Balance prepaid; usage meters at the gateway; destinations = KYC-attested categories + hard denylist (banking/gov/mail).
- **Zero-balance grace (Locked · Stream Dev):** after balance hits $0, allow **≤5 MB or ≤15 s** (whichever first), then **hard-stop** the stream. Peer is still paid for gateway-verified grace bytes; platform absorbs that sliver as COGS (no negative customer balance in Phase 0).

### 2. Peer agent (host)
- Primary-purpose desktop/Docker client (Win/macOS/Linux). **No silent SDKs.** Phase 0 countries: **US + Canada only**.
- Opens persistent tunnel to a **relay**; receives dial requests; DNS + TCP on the Peer; **no payload inspection**.
- On install: ISP/proxy-ban **warn + acknowledge** (counsel owns copy). Local egress deny (LAN, metadata, abuse ports) + bandwidth/concurrency caps.
- Inventory class: **casual** vs **always-on** (strict-eligible prefers/requires always-on).
- Location: ensemble GeoIP (≥2) + RTT anchors + ASN → **verified tier**; paid only on verified GB. Fraud (CGNAT, DC ASN, flap) excludes premium/strict + payouts. No street address stored.

### 3. Control plane
- Accounts, KYC tiers (T0–T3), prepaid **ledger**, pricing engine, **capacity kill switches**, admin, later reseller API.
- **Payments (Locked · Stream Dev):** Phase 0 customer fiat top-up; Peer cashout via **Stripe Connect (or equivalent Connect-style rail)**; **min cashout $25**. Crypto/DePIN deferred. Counsel still reviews payout compliance copy.
- Ledger kinds: customer debit · Peer payout · service credit (remedy) · margin — idempotent by `stream_id`. Glossary: balance ≠ payout ≠ service credit.
- Owns `metro_capacity` + country pool counters for hard caps and kill switches (see Matching).

### 4. Matching by location specificity
- **Peer registry** indexes peers by country / city / H3 / ASN + host_tier + health/fraud/uptime.
- **Assign path:** gateway parses selector → policy (KYC, balance, dest) → sample healthy geo-verified peers → bind session in store → relay opens stream.
- **Rematch:**
  - `rematch-city`: soft sticky in city label (metro jump possible; disclosed); soft-sticky billing **re-rates remaining bytes at delivered geo**.
  - `rematch-strict`: rematch only **same city AND (≤15–25 km OR matching H3)**; ASN optional **AND**; else **hard-fail + service credit** (never silent east↔west).
  - `hard`: same peer; loss → session error.
- Strict **implies** city-or-tighter; country+strict rejected. Price: country $1.75/GB base; city 1.5×; strict 2.0× (**no stack** city×strict). Peer country share 25% of public country $ on verified GB.

**Phase 0 pool cap + kill switch (Locked · Stream Dev):**
| Knob | Default (rational / eng W2–W4) |
|------|-------------------------------|
| Country seed before wide `rematch-city` sell | **≥300 online peers / country** (US, CA) |
| Hard country pool cap (assignment reject when oversubscribed) | **concurrent sessions ≤ online_peers × 0.5** (headroom; refine with telemetry) |
| Sell `rematch-strict` only if | always-on in-bound peers in bound **N ≥ 25** |
| Kill / disable `rematch-strict` for a metro | assign success **< 80% over 7d** **OR** N < 25 |
| Kill / pause country rotating sell | online peers **< 150** for that country (soft floor; alert Ops earlier at 300) |

**Traffic spine:** Client → Gateway (auth, meter, policy) → Relay ↔ Peer → destination. Gateway is source of truth for bytes.

---

## Locked defaults (was: three opens)

| Topic | Lock | Source |
|-------|------|--------|
| Settlement | Prepaid USD customers + per-GB Peer payout; fiat Phase 0 | Decision log + Stream Dev |
| Phase 0 geos + pool | US+CA only; hard peer-pool cap + kill switches (table above) | Stream Dev + eng W2–W4 |
| Zero-balance grace | ≤5 MB or ≤15 s → hard stop; Peer paid on grace bytes; platform absorbs | Stream Dev + Architect default |
| Peer cashout | Stripe Connect (or equiv); min **$25** | Stream Dev |

---

## Still open (non-blocking for v0 APIs; counsel / brand)

- Public brand name (never ship “Stream” to customers).
- Exact H3 resolution inside 15–25 km band (ops may tune).
- Customer top-up processor vendor (Stripe Checkout/Elements is the natural pair with Connect — Platform may assume unless counsel blocks).
- Attribution-log retention (counsel).
- ISP disclaimer legal copy (counsel).

---

## Out of scope for this sketch
Implementation, public brand strings, counsel copy, crypto rails, reseller detail, 48h/7d sticky (flagged).

*Sources: `stream-spec-outline-v0.md`, `stream-decision-log.md`, `stream-eng-handoff-v0.md`, Stream Dev room locks 2026-09-28.*
