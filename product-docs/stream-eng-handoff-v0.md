# Stream — Engineering Handoff Pack (v0)

**Audience:** Developers architecting / designing the system  
**Author:** Spec Keeper (product locks) · adapted from Geo-Tiered Residential Proxy Service handoff pack Rev 2  
**Date:** 2026-09-28 (CT)  
**Status:** Design-stage architecture handoff. Not legal advice. Counsel still owns ISP disclaimer text and compliance copy.

> **Jeff directive (2026-09-28):** Hammer out and hand off. Stop product-choice questionnaires. Jeff authorized Spec Keeper to lock money math without further prompts. Engineers may refine **working assumptions** (§3) with telemetry; do not reopen locked product rules (§2) without a decision-log change.

---

## 0. Status & naming

| Item | Value |
|------|--------|
| **Internal codename** | **Stream** |
| **Public brand** | **TBD** — never use “Stream” in customer UI, ToS, marketing, Peer install screens, invoices, or KYC vendor configs until brand is chosen |
| **Status** | Design-stage handoff for architecture / system design |
| **Settlement** | Prepaid USD customers + per-GB Peer payout (fiat Phase 0); **not** credit-barter |
| **Counsel still needed** | ISP/proxy-ban disclaimer language; attribution-log retention; KYC/data-protection fit US+CA; Peer payout compliance |
| **Identity framing** | Compliant opt-in residential network (paid hosts + scarcity geo + KYC trust) — **not** a “PacketStream-style P2P” clone |

---

## 1. Problem / target user (Stacy)

**Persona:** Buyer needs a **stable residential exit IP** in a **given local area** for multi-hour sessions (account workflows, localized QA, ad verification)—not max-entropy rotating scrape.

**Example:** Stacy reserves New Orleans for ~4 hours. Mid-session rematch **must not** jump across the metro (New Orleans east ≠ west). Soft city-label sticky that silently rematches across the metro is an anti-pattern for `rematch-strict`.

**Sold unit:** Optional session rematch modes — Stacy opts into `rematch-strict`; scrapers who don’t care stay on Phase 0 default `rematch-city`.

**Wedge:** Verified geo integrity + duration SKUs + KYC-gated trust (not a race to $1/GB commodity P2P).

---

## 2. Locked product rules (implement against this checklist)

Engineers can treat every bullet as a product lock unless marked WORKING ASSUMPTION (§3).

### 2.1 Phase 0 scope
- [ ] Phase 0 countries: **US + Canada** only
- [ ] Primary-purpose Peer client only (desktop / Docker) — **no silent SDKs**, no background enrollment in unrelated apps
- [ ] Fiat-only settlement Phase 0 (customer top-up + Peer cashout on approved fiat rails). Crypto Peer payout / DePIN deferred
- [ ] Public brand not chosen — use placeholder brand strings in UI mocks; never ship “Stream” to customers

### 2.2 Settlement
- [ ] Customers: **prepaid USD** balance (cards / wallets / bank; crypto credit only if KYC-gated and later phases allow — Phase 0 fiat-first)
- [ ] Peers: **cash (or approved fiat rail) payout per verified GB** — **not** credit-barter
- [ ] Platform margin = customer debit − Peer payout, ledgered idempotently by `stream_id`
- [ ] Glossary: *customer balance* ≠ *Peer payout* ≠ *service credit* (remedy)

### 2.3 Money math (locked 2026-09-28 — Spec Keeper authorized by Jeff)
- [ ] **Public country rotate list price:** **$1.75/GB**
- [ ] **Peer country payout:** **25%** of public country price on **verified** GB → **$0.4375/GB**
- [ ] **$4.00/GB:** **internal scarce-inventory / planning floor only** — **NOT** the public country list price. Do not expose $4 as country sticker
- [ ] **Pricing formula v1:**
  ```
  price_per_GB = public_base × geo_mult × duration_mult × optional exclusivity_mult
  ```
  - `public_base` (country rotating) = **$1.75**
  - **NO dual-metric $/hr + $/GB in v1** (deferred)
- [ ] **Working geo multipliers** (on country base):
  | Selector / mode | Mult | Notes |
  |-----------------|------|-------|
  | Country | **1.0×** | Public list $1.75 |


### 2.3a Geo / rematch multiplier stacking (**Locked**)
- `rematch-strict` **implies** city-level (or tighter) locality. When the selector requests city (or finer) **and** `rematch-strict`, apply **`strict` mult only (2.0×)** — **do not stack** city 1.5× × strict 2.0×.
- Country + `rematch-strict` is rejected (4xx): strict requires a city-or-tighter target.
- City without strict → **1.5×** only.
- `rematch-city` (default soft rematch) → billed at the **geo mult only** (country 1.0× / city 1.5×). See §2.3b.
- Region SKU: **collapsed in v1** — do not sell a separate region tier; map region selectors to **country** pricing (1.0×) or require city. (Prior pack had region 1.5× colliding with city 1.5×.)

### 2.3b `rematch-city` billing (**Locked**)
- Phase 0 default rematch mode does **not** add the legacy soft-sticky **+0.2×** add-on.
- Buyer pays **geo_mult × duration_mult** (and exclusivity if any) only.
- Soft-sticky **re-rate at delivered geo** still applies if delivered coarser than requested (Locked billing rule).

### 2.3c Service credit on `rematch-strict` hard-fail (**Locked**)
- When strict rematch cannot find an in-bound peer: **service-credit the unused prepaid usage for the remainder of the paid session window at the assignment $/GB rate** (bytes not yet transferred), plus any unused exclusive premium if exclusivity was charged; **do not** forfeit the whole prepaid balance.

### 2.3d Exclusive × `rematch-strict` (**Locked**)
- Exclusive + strict: peer loss → **no** rematch outside exclusivity; session ends (or customer may start a new non-exclusive session). Service-credit unused exclusive premium + unused GB per §2.3c.

### 2.3e Duration multipliers — v1 ladder (inline; Locked scope rotating→24h)

| Duration | Mult | Min KYC | Reliability class | Scope |
|----------|------|---------|-------------------|--------|
| Rotating | 1.00× | T1 | any healthy peer | **v1** |
| 5 min | 1.05× | T1 | any healthy | **v1** |
| 15 min | 1.10× | T1 | any healthy | **v1** |
| 30 min | 1.15× | T1 | any healthy | **v1** |
| 1 h | 1.25× | T1 | any healthy | **v1** |
| 3 h | 1.40× | T2 | above-median uptime | **v1** |
| 6 h | 1.60× | T2 | above-median | **v1** |
| 12 h | 2.00× | T2 | above-median | **v1** |
| 24 h | 2.50× | T2 | top-quartile | **v1** |
| 48 h | 3.50× | T3 | top-quartile, best-effort | **Flagged** — best-effort + auto service credit on peer loss |
| 7 d | 5.00× | T3 | top-decile, best-effort | **Flagged** |

Custom TTLs snap **up** to the next enabled tier. Extensions billed at tier mult for added time. Early DELETE: assignment mult not refunded by default (strict hard-fail uses §2.3c instead).


  | City | **1.5×** | → $2.625/GB |
  | `rematch-strict` | **2.0×** | → $3.50/GB (working); priced/gated |
  | City + ASN | T3-only; calibrate (scaffold historically 4.0× — treat as calibrate, not list) | |
  | Exclusive exit add-on | T3-only; +1.5× scaffold | |
- [ ] Soft-sticky (non-strict) billing: **re-rate remaining bytes at delivered geo** when applicable
- [ ] Exclusive reservation TTL: **5 min** (hold peer on quote/assign; unused hold auto-releases)
- [ ] Peer share on strict: see §3 working assumption (30% of strict $)

### 2.4 Duration ladder v1
- [ ] Public v1 ladder: **rotating → 24 h** (5m, 15m, 30m, 1h, 3h, 6h, 12h, 24h with multipliers from pricing PRD)
- [ ] **48 h / 7 d** behind feature flag until sticky-survival data; when enabled = best-effort + automatic **service credit** on peer loss (no SLA unless later locked)
- [ ] Custom TTL snaps **up** to next enabled tier; KYC over-limit → **4xx reject**, never silent clamp

### 2.5 Rematch modes
- [ ] Modes: `rematch-strict` | `rematch-city` (Phase 0 default) | `hard` (same peer only; peer loss → session error)
- [ ] Phase 0 **default** = `rematch-city`
- [ ] **`rematch-strict` contract:**
  - Rematch only inside: **same city AND (≤15–25 km OR matching H3)**
  - ASN filter is **optional AND** (never OR that widens the bound)
  - Else: **hard-fail + service credit**
  - Minimize mid-session IP hops (many systems flag any hop)
  - Priced/gated (higher KYC + scarcity mult); not network-wide mandate
- [ ] `rematch-city`: soft-sticky within city label (metro jump possible; disclosed)
- [ ] `hard`: same peer only; peer loss → session error

### 2.6 Hosts / Peers
- [ ] Anyone may enroll
- [ ] Inventory tiers: **casual** vs **always-on**; always-on preferred / required for strict-eligible inventory
- [ ] Location verify: GeoIP ensemble **(≥2 providers)** + RTT anchors + ASN
- [ ] Pay by **verified** tier only — never claimed street address
- [ ] **No** street address stored for product geo; **no** payload inspection
- [ ] CGNAT / hosting / VPN / datacenter ASN / geo flap → fraud score; high scores excluded from premium/strict + payouts



### 2.6a Destination attestation vs Stacy (**Locked** Phase 0)
- AUP remains: KYC-gated attested destinations + hard denylist (banking, gov, mail) — not public-web-only.
- Phase 0 attestation is by **use-case category** (e.g. account management / localization QA / ad verification) and/or **fast-path review** (hours–days, not a weeks-long per-domain allowlist) so Stacy-class workflows are not blocked by empty allowlists.
- Per-domain allowlists may be required at higher KYC tiers later; not a Phase 0 gate for attested categories.

### 2.7 Trust / AUP / ISP (product controls — not legal advice)
- [ ] Trust Phase 0 logs: **metadata only** — account, timestamp, dest host:port, bytes, geo tier. Short retention TBD counsel. **No ZK Phase 0**
- [ ] AUP: **KYC-gated attested destinations + hard denylist** (banking, gov, mail) — **NOT** public-web-only
- [ ] ISP: **warn at Peer install** that some AUPs ban proxy/server use; counsel owns disclaimer text — **NOT** “blanket waive all liability”
- [ ] Playbook if ISP terminates Peer: delist IP, pause payouts, preserve attribution if needed (outline §3.10)
- [ ] Counsel sign-off before paid Peer recruitment spend

### 2.8 Access control
- [ ] ASN targeting = **T3-only**
- [ ] Exclusive exit = **T3-only**
- [ ] `rematch-strict` = priced/gated (expect elevated KYC; exact tier gate in Sessions PRD — default assume ≥T2, ASN+exclusive stay T3)
- [ ] Public v1 selector does **not** expose H3 radius as a sold SKU (H3 = internal index)

---

## 3. Working assumptions (engineers may refine with telemetry)

Label these **WORKING ASSUMPTION** in designs/tickets. Not Jeff questionnaires.

| ID | Assumption | Starting value |
|----|------------|----------------|
| W1 | Peer client Phase 0 | Primary-purpose desktop/Docker only; platforms Win/macOS/Linux |
| W2 | US/CA country seed before selling `rematch-city` widely | Target **≥300–500 online peers/country** |
| W3 | `rematch-strict` metro depth | Sell strict only in metros with always-on in-bound depth **N ≥ 20–50** online always-on peers in-bound (**WORKING ASSUMPTION** — tune with telemetry) |
| W4 | Kill criterion for strict SKU in a metro | Disable `rematch-strict` when assignment success **< 80% over 7 days** **OR** online always-on in-bound peers **< threshold (W3)** |
| W5 | Exclusive reservation TTL | **5 min** (already Locked) |
| W6 | Strict peer share | **30% of strict buyer $** as working always-on bonus (from margin sheet). Alternative considered: 25% + separate always-on bonus — **picked 30% of strict $** for v0 handoff; Spec Keeper may normalize later without changing public buyer mult |
| W7 | Region geo mult | Scaffold **1.5×** country (same as city working lock — calibrate; region may collapse to city SKU) |
| W8 | Soft-sticky add-on mult | Scaffold **+0.2×** (from Rev 2); confirm in pricing engine |
| W9 | Attribution retention | Placeholder **90–180 days** metadata — counsel locks before launch |
| W10 | Grace buffer at zero balance | Size TBD; hard stop after small buffer |

---

## 4. System architecture sketch

Adapted from Rev 2 handoff pack §2. Components must implement locked rematch modes, verified-tier payout, and gateway metering.

### 4.1 Components

| Component | Responsibility |
|-----------|----------------|
| **Edge gateway (regional)** | TLS termination; HTTPS / HTTP / SOCKS5 listeners; auth; selector parsing; KYC/policy enforcement; byte metering; price_mult at assignment |
| **Relay tier** | Persistent outbound tunnels from Peers; multiplex customer streams |
| **Peer registry** | `peer_id → {relay, country, region, city, h3, asn, host_tier[casual|always_on], uptime_score, load, last_seen, geo_verified_at, fraud_score}`; indexed by geo node + rematch-strict eligibility |
| **Session store** | Redis-class key/TTL: `(account, label) → {peer_id, geo_level, dur_tier, rematch_mode, exclusive, expires_at, assigned_price_mult}` |
| **Control plane** | Accounts, KYC state, balances/ledger, pricing engine, reseller API, admin, capacity kill switches |
| **Peer client** | Win/macOS/Linux (+ Docker); auto-update; telemetry; local egress policy; ISP warn ack; kill switch |
| **Verification / abuse** | Geo ensemble + RTT anchors; fraud score; abuse case management; denylist |
| **Payments** | Fiat gateway adapters (Phase 0); webhook ingestion; reconciliation; Peer payout rails; (crypto adapters deferred / later phase) |

### 4.2 Request flow

1. Customer connects to gateway; presents credentials (username + auth key with selector suffixes).
2. Gateway authenticates; loads KYC tier, limits, balance; parses selector (**fail-closed** on conflicts / under-spec / over-tier).
3. Policy: geo + duration + rematch mode within tier? Destination host:port allowed (attested + denylist)? Sufficient balance?
4. Session lookup (sticky / labeled) or sample from geo pool (rotating) with filters: health, load, uptime ≥ tier, **geo verified**, host_tier if strict, fraud score OK, rematch bound if rematch mid-session.
5. Gateway asks relay to open stream to Peer; Peer dials `host:port` (DNS on Peer) and splices bytes. Destination TLS end-to-end (no payload inspection).
6. Gateway meters bytes up+down; records assignment with `price_mult` and rematch_mode.
7. On close / flush: usage → ledger; Peer credited from **gateway-measured verified GB only** at tier share.
8. Soft-sticky rematch outside assignment geo: **re-rate remaining bytes** at delivered geo; emit session event; drop exclusivity_mult if exclusivity broken.
9. `rematch-strict` with no eligible peer in bound: **hard-fail + service credit** (no silent metro jump).

### 4.3 Data model sketch

```
accounts(id, type, kyc_tier, status, risk_score, limits_json)
kyc_cases(id, account_id, provider_ref, result, expires_at, reviewer)
peers(peer_id, relay, country, region, city_id, h3, asn, host_tier,
      uptime_score, geo_verified_at, fraud_score, last_seen)
sessions(account_id, label, peer_id, geo_level, dur_tier, rematch_mode,
         exclusive, assigned_price_mult, expires_at)
usage(stream_id, account_id, peer_id, bytes_up, bytes_down, geo_level,
      dur_tier, rematch_mode, price_mult, dest_host, dest_port,
      assigned_geo, delivered_geo, ts)
ledger(entry_id, account_id|peer_id, amount, kind[debit|peer_payout|service_credit|margin],
       ref, stream_id?, ts)
abuse_cases(id, account_id, peer_id?, source, status, actions)
payment_methods(id, account_id, type, provider, fingerprint, verified_owner, status)
payments(id, account_id, method_id, provider_ref, amount, currency, status, risk_flags, ts)
exclusive_holds(peer_id, account_id, expires_at)  -- TTL 5 min
metro_capacity(geo_node, rematch_mode, n_online_eligible, kill_disabled_until?)
```

### 4.4 Metering
- Per-stream counters **at the gateway only** (source of truth for customer debit and Peer payout).
- Batched flush (e.g. 5–10 s) to append-only usage table.
- Balance check on open + periodic mid-stream; hard stop at zero after grace buffer.
- Ledger dual-entry, idempotent by `stream_id`.

### 4.5 Egress policy (both Peer client and gateway)
- Deny loopback, RFC1918, link-local, cloud metadata, Peer’s own LAN.
- Port allowlist (default 80/443 + explicitly approved); block SMTP/25 and abuse-prone ports.
- Per-Peer bandwidth + concurrency caps; back-pressure to gateway.
- Destination hard denylist (banking, gov, mail, known abuse) maintained by T&S.
- Customer destinations: KYC-gated **attested** allowlist / review path — not “public web only.”

### 4.6 Geo model
- Hierarchy: continent → country → region → city (GeoNames ID) → optional ASN; plus **H3** (internal, res TBD in 15–25 km band) for strict bound queries.
- Pool sets per node; join/leave updates ancestors.
- Re-verify daily and on IP change; ensemble ≥2 GeoIP + RTT + ASN.

### 4.7 Security (sketch)
- TLS on all tunnels; mutual auth peer↔relay with per-install keys; signed client updates; secrets in manager; credentials never logged; admin audited; KYC docs restricted storage.

---

## 5. API / selector surface (v1)

Auth: username + auth key; behavior encoded as **suffixes** on the key (Rev 2 pattern). Conflicts / under-spec / KYC over-limit → **4xx + error body** (fail-closed, never silent clamp).

### 5.1 Geo / session suffixes (from Rev 2)

```
KEY                                         rotating; country default from account
KEY_country-US                              country
KEY_country-US_region-TX                    state/province (ISO 3166-2)
KEY_country-US_region-TX_city-<geonames_id> city
KEY_..._asn-<asn>                           ASN — T3-only
KEY_..._session-<label>_ttl-<value>         sticky; value = 5m|15m|30m|1h|3h|6h|12h|24h
                                            (48h|7d feature-flagged)
KEY_..._session-<label>_ttl-1h_renew-1      sliding renewal on new connections
KEY_..._fallback-region                     opt-in coarse fallback; re-rate at delivered geo
KEY_..._exclusive-1                         exclusive exit — T3-only; 5 min hold
```

### 5.2 Rematch mode suffixes (v1 addition)

```
KEY_..._rematch-city      soft city sticky (Phase 0 DEFAULT if omitted)
KEY_..._rematch-strict    locality-bounded rematch (same city AND ≤15–25 km|H3; ASN AND-only)
KEY_..._hard      same as hard-sticky alias
KEY_..._hard       hard-sticky (same peer only) — synonym path; pick one canonical in impl
```

**Rules:**
- If `rematch-strict` and pool cannot satisfy bound at assign or rematch → error + service credit path; **do not** silently fall back to city.
- `exclusive-1` + peer loss → session end (or explicit customer opt-in to non-exclusive); refund unused exclusive premium per pricing PRD.
- Session labels must be non-sensitive.
- H3 radius is **not** a public selector SKU.

### 5.3 Control-plane session APIs (sketch)
- `GET /sessions/{label}` — resolved geo, rematch_mode, stability class, status, assigned vs delivered geo
- `POST /sessions/{label}/extend` — buy more time at tier mult for added duration
- `DELETE /sessions/{label}` — end early; assignment mult not refunded by default (exclusive peer-loss is separate)

Protocols: HTTPS proxy, SOCKS5, legacy HTTP.

---

## 6. KYC tiers summary (T0–T3)

From Rev 2 pack §3.2. Higher capability requires higher assurance.

| Tier | Verification | Unlocks (v1) |
|------|--------------|--------------|
| **T0** (pending) | Verified email, payment method, ToS/AUP acceptance | No proxy access, or tiny capped test if permitted (definition Open) |
| **T1** | Identity (individual) or KYB (business: registry, UBOs, signatory); sanctions/PEP; declared use case | Country-level; rotating and ≤1 h sticky; moderate spend/concurrency; `rematch-city` / hard |
| **T2** | T1 + EDD: use-case review, domain/target list **attestation**, references or history | Region/city; ≤24 h; higher caps; **`rematch-strict`** (priced/gated — confirm gate in Sessions PRD) |
| **T3** | T2 + manual review, contract, ongoing audit rights | **City+ASN**; ≤7 d (when flagged on); **exclusive exits**; reseller status |

**Gating notes:**
- ASN targeting and exclusive exit = **T3-only** (Locked).
- `rematch-strict` priced/gated; default product intent ≥T2 + always-on inventory — implement reject if tier insufficient.
- Re-verify on doc expiry, material change, or risk triggers.
- Screening: sanctions/watchlist continuous; payer identity matches verified party; Phase 0 fiat processors with risk hold on new accounts.

---

## 7. Phase 0 / v1 out of scope

Do **not** build into Phase 0 / public v1 unless decision log reopens:

- Mobile share / Play SDK distribution / silent SDKs
- Dual-metric tariff ($/hr reservation + $/GB)
- Crypto Peer payouts; DePIN / token burn / ZK proofs
- Public brand “Stream” in customer surfaces
- Selling 48 h / 7 d without feature flag + sticky-survival data
- Reseller product (Phase 4)
- Dynamic scarcity pricing at launch (static multipliers first)
- Public H3-radius SKU
- Blanket liability waiver as product lock
- Credit-barter settlement
- Payload inspection / DPI for AUP (metadata + dest controls only Phase 0)

---

## 8. Open for engineering (not product choice)

These are calibration / implementation details — **not** Jeff product questionnaires:

| Topic | Guidance |
|-------|----------|
| Exact H3 resolution inside 15–25 km band | Ops/eng tune; product bound Locked |
| Geo-verify score thresholds / RTT anchor layout | Fail-closed for premium/strict |
| Telemetry schema for sticky survival, assignment success, pool depth | Power W4 kill switches |
| CGNAT rejection heuristics | Exclude from premium/strict + payouts |
| Attestation workflow UX (destination list review) | Align with T2+ AUP |
| Abuse SLA numeric targets | Outline proposes e.g. 24 h triage — confirm with T&S |
| Counsel-owned legal copy | ISP disclaimer, privacy retention, ToS remedies |
| Grace buffer bytes/seconds at zero balance | Spec in Billing PRD |
| Region vs city SKU collapse | If region mult ≈ city, simplify selector |
| Always-on detection signals | Heartbeat, schedule, hardware nudge — for strict eligibility |

---

## 9. Doc index

| Doc | Path |
|-----|------|
| Decision log (living locks) | `/workspace/product-docs/stream-decision-log.md` |
| Spec outline (map) | `/workspace/product-docs/stream-spec-outline-v0.md` |
| Pricing / capacity / payouts PRD | `/workspace/product-docs/stream-prd-pricing-capacity-payouts-v0.md` |
| Margin sheet ($1.75) | `/workspace/market-scout/stream-margin-sheet-1.75-2026-09-28.md` |
| Pricing/capacity comps | `/workspace/market-scout/stream-pricing-capacity-comps-2026-09-28.md` |
| vs Mysterium / PacketStream | `/workspace/market-scout/stream-vs-mysterium-vs-packetstream-2026-09-28.md` |
| Research brief (precedents) | `/workspace/research/p2p-proxy-marketplace-brief.md` |
| Original Rev 2 pack (source concepts) | Agent attachment / prior pack: *Geo-Tiered Residential Proxy Service: Handoff Pack Rev 2* (architecture §2, KYC §3, selector §1.4). This eng handoff supersedes it for locked money + rematch modes. |
| This handoff | `/workspace/product-docs/stream-eng-handoff-v0.md` |
| CHANGELOG | `/workspace/product-docs/CHANGELOG.md` |

---

## 10. Appendix A — Margin sheet (embed)

Source: `/workspace/market-scout/stream-margin-sheet-1.75-2026-09-28.md` (2026-09-28 CT). Working numbers for eng; not marketing copy.

### Locked commercial inputs

| Item | Value |
|------|--------|
| Public country rotate | **$1.75/GB** prepaid USD |
| Peer payout (country) | **25%** of public country on verified GB → **$0.4375/GB** |
| Internal scarce floor | **$4/GB** — planning only; **not** public list |
| City multiplier (working) | **1.5×** country |
| Strict / `rematch-strict` multiplier (working) | **2.0×** country |
| Dual-metric ($/hr + $/GB) | **Deferred** for v1 |
| Settlement | Fiat-only Phase 0 |

### Country unit economics @ $1.75

Assumptions: peer paid on verified GB only; payment processing ~3% of buyer $; opex/support/geo-verify **not** included.

| Line | Amount | Share of buyer $ |
|------|--------|------------------|
| Buyer pays | $1.75 | 100% |
| Peer (25%) | $0.4375 | 25% |
| Payment fees (~3%) | $0.0525 | 3% |
| Platform after peer + fees | **$1.26** | **72%** |

**vs comps:** PacketStream $1 buyer / $0.10 earner (10%). Stream country earner at 25% of $1.75 ≈ **4.4×** PacketStream host rate while staying PacketStream-adjacent on sticker.

### Upgrade ladders (working multipliers)

| SKU | Buyer $/GB | Peer share (working) | Peer $/GB | Fees 3% | Platform after peer+fees |
|-----|------------|----------------------|-----------|---------|--------------------------|
| Country rotate | $1.75 | 25% | $0.44 | $0.053 | ~$1.26 |
| City (1.5×) | $2.63 | 25% of city $ | $0.66 | $0.079 | ~$1.89 |
| Strict (2.0×) | $3.50 | **30%** of strict $ (always-on bonus) | $1.05 | $0.105 | ~$2.35 |

Strict peer share **30%** is the handoff **WORKING ASSUMPTION** (W6) to fund denser always-on inventory.

### Design implications for eng

1. Ledger debits buyer prepaid balance at **assignment** using `public_base × geo_mult × duration_mult × optional exclusivity` (dual-metric out of v1).
2. Peer credits accrue on **verified delivered GB** at tier share; cashout fiat Phase 0.
3. `rematch-strict` inventory is scarcer — metering and kill switches must stop selling strict when concurrent depth fails (W3/W4).
4. Do **not** expose $4 as public country rate.

---

## Acceptance criteria for this handoff

- [x] Architects can start design without asking Jeff product questions (locks in §2; assumptions in §3; eng opens in §8)
- [x] Money locks reflected in decision log + pricing PRD + this document
- [x] No fabricated committed legal advice; ISP = warn + counsel
- [x] Stream never treated as public brand

---

*Spec Keeper — eng handoff v0 · 2026-09-28 CT. Research Desk pressure-test patches applied (multiplier stacking, duration inline, canonical `hard`, service credit, rematch-city billing, Phase 0 attestation).

*Spec Keeper — eng handoff v0 · 2026-09-28 CT. Supersedes Rev 2 pack for locked rematch modes + money math; architecture/KYC/selector adapted from Rev 2.*
