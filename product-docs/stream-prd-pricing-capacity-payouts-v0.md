# Stream — PRD: Pricing, Capacity & Peer Payouts (v0)

> **Naming:** “Stream” is the **internal** codename only. Public brand TBD — do not use Stream in customer-facing pricing pages or invoices.

**Owner:** Spec Keeper  
**Status:** Draft · Rev align Sep 28, 2026 · **Money math Locked** (Spec Keeper Jeff-authorized 2026-09-28)  
**Depends on:** Stream handoff pack Rev 2 §4; settlement lock (Sep 28, 2026); outline `stream-spec-outline-v0.md`  
**Out of scope:** Legal opinion on money-transmitter / payout compliance (counsel); competitor rate research as committed Stream prices (Research Desk / Market Scout comps are **illustrative only**)

---

## 1. Problem

Customers need predictable, metered pricing that rises with scarce inventory (finer geo, longer sticky sessions). Peers need intelligible per-GB earnings. The platform needs margin and must not oversell thin geo pools.

## 2. Goals

- One customer-visible formula for $/GB at session assignment.
- Clear Peer payout from **gateway-measured, verified** bytes only.
- Capacity guardrails so city/ASN tiers are not sold when pool depth is unsafe.
- Settlement model: **prepaid USD for customers; per-GB payout to Peers** (locked).
- Unit-economics with Locked public country **$1.75/GB** + peer **25%**; **$4** = internal floor only — **no invented prices beyond room locks**.

## 3. Non-goals

- Credit-barter (Peers spending earnings only as in-network proxy credit).
- Anonymous / non-KYC top-ups (including crypto that bypasses KYC).
- Processor selection as fact (Open). Public country **$1.75/GB** and peer country **25%** are Locked; do not invent further list prices beyond room locks.
- Selling 48 h / 7 d sticky at v1 without sticky-survival data (those tiers stay feature-flagged).

## 4. Settlement (locked)

| Side | Mechanism |
|------|-----------|
| Customer | Prepaid **USD-denominated** balance. Funded by cards, wallets, bank rails, or crypto. Credit lands only on KYC-verified accounts. |
| Peer | **Cash (or approved fiat rail) payout per verified GB**, with tier bonuses. **Phase 0 = fiat-only.** Optional stablecoin payout later requires Peer identity verification and counsel sign-off. |
| Platform | Margin = customer debit − Peer payout (per stream), ledgered idempotently by `stream_id`. |

**Glossary (use these terms going forward)**
- **Customer balance** — prepaid USD credit on the account.
- **Peer payout** — money owed/paid to Peers for verified GB.
- **Service credit** — remedial balance for failed long-tier / exclusive sessions, not Peer earnings.

---

## 4.0 Unit economics (scaffold — TBD + Market Scout comps)

**Comps source:** Market Scout `/workspace/market-scout/stream-pricing-capacity-comps-2026-09-28.md` (fetched 2026-09-28 CT). Comps are **not** Stream locks.

| Field | Stream value | Owner |
|-------|--------------|-------|
| Public country rotate `public_base` $/GB | **$1.75/GB Locked** (Spec Keeper Jeff-authorized 2026-09-28) | Spec Keeper |
| Internal scarce floor $/GB | **$4.00/GB Locked** — planning only; **NOT** public list | Spec Keeper |
| Peer payout % (country) | **25% Locked** → **$0.4375/GB** verified | Spec Keeper |
| Peer payout % (strict) | **30% of strict $** WORKING ASSUMPTION (eng handoff W6) | Spec Keeper / eng |
| Target platform margin % | **Open** | Jeff |
| Geo / duration / exclusivity multipliers | Starting tables §5 (calibrate; not list prices) | Spec Keeper / Jeff |
| Intro promo / volume tiers | **Open** | Jeff |

### Competitive bands (facts from Market Scout — US reference; CA same $/GB on most vendors)

| Band | Buyer retail $/GB | Notes |
|------|-------------------|-------|
| Enterprise list / PAYG | ~**$8** list (Bright, Oxylabs PAYG); promo/commit often **$2.50–$5** | Geo often free at retail |
| Mid-market | ~**$2–$5** (Decodo PAYG $4; SOAX Tier-1 $5→$0.85 by plan) | US+CA = SOAX Tier 1 (same rate) |
| P2P flat | **$1.00** (PacketStream; country-only) | Matching earner **$0.10/GB** |

### Peer earn / take-rate (facts)

- PacketStream: buyer $1 / earner $0.10 → **10% earner / ~90% platform** of retail GB (before 3% cashout fee).
- Honeygain: no fixed rate (Apr 2026 help); marketing still implies ~$0.10/GB if credit conversion holds.
- EarnApp: **not $/GB** — US up to $10/IP/mo vs RoW $5 (Canada in RoW).

### N_pool (facts + labeled inference)

- Vendor “M+ IPs” = monthly inventory, **not** concurrent peers. Oxylabs display ~10.4M USA / ~2.0M Canada; Decodo US 7.1M+ / CA 540K+ — **CA thinner**.
- No public “N peers for city coverage” formula found.
- **Inference (not fact):** online peers ≈ (target concurrent ÷ sessions_per_peer) × 3–5; country rotating for ~100 concurrent might need ~100–500 online peers (wide band); city/ASN much thinner.

### Option bands for Jeff to lock (not Spec Keeper recommendations as fact)

| Decision | Option A | Option B | Option C |
|----------|----------|----------|----------|
| `base` (country rotating) | **$1.00** (PacketStream-like flat) | **$4.00** mid (Decodo PAYG / BD promo-ish) | **$8.00** enterprise list |
| Peer payout % | **10%** (only clean public pair) | **5–10%** with geo bonuses TBD | Custom |
| Phase 0 N_pool (US+CA country rotating) | Seed for **~50** concurrent → inference **~150–250** online peers/country | Seed for **~100** concurrent → **~300–500**/country | Jeff-defined |

### Worked examples (Locked public_base $1.75)

**A. US country rotating, 10 GB**

| Line | Value |
|------|-------|
| `public_base` | **$1.75** |
| Customer debit | $1.75 × 10 = **$17.50** |
| Peer (25%) | $0.4375 × 10 = **$4.375** |
| Fees ~3% | ~$0.53 |
| Platform after peer+fees | ~**$12.60** |

**B. US city (1.5×), 1 h sticky (1.25×), 10 GB**

| Line | Value |
|------|-------|
| Customer $/GB | $1.75 × 1.5 × 1.25 = **$3.28125** |
| Customer debit | **$32.81** |
| Peer @ 25% of city $ | $0.65625 × 10 ≈ **$6.56** |

**C. US rematch-strict (2.0×), rotating duration, 10 GB**

| Line | Value |
|------|-------|
| Customer $/GB | $1.75 × 2.0 = **$3.50** |
| Customer debit | **$35.00** |
| Peer @ 30% of strict $ (WORKING) | $1.05 × 10 = **$10.50** |
| Platform after peer+fees (~3%) | ~**$23.45** |

Full ladder: margin sheet / eng handoff Appendix A.




### Public vs internal base — **LOCKED** (2026-09-28 — Spec Keeper Jeff-authorized)

- **Public country rotate:** **$1.75/GB**
- **$4.00** = **internal** scarce-inventory / planning floor — **not** the public country sticker
- Peer country share **25%** of public country $ → **$0.4375/GB** verified (not 10% of $4)
- City mult **1.5×**; `rematch-strict` mult **2.0×** (working, on country base)
- Dual-metric **deferred v1**; fiat-only Phase 0
- Strict peer share **30% of strict $** = WORKING ASSUMPTION

Source: Market Scout margin sheet `/workspace/market-scout/stream-margin-sheet-1.75-2026-09-28.md` + eng handoff.

## 4.1 Locality-bounded reservations & dual-metric

**Stacy rule (Locked):** `rematch-strict` sessions promise same city AND ≤15–25 km (or matching H3); ASN AND-only — rematch inside bound or hard-fail+credit. Phase 0 default `rematch-city`; `rematch-strict` opt-in, priced/gated at **2.0×** country; AUP = attested destinations + hard denylist (not public-web-only).

**Tariff shape v1 (Locked deferral):** GB-only formula — **no dual-metric $/hr + $/GB in v1**. Dual-metric deferred.

**Do not lock without evidence:** 60/40 host split, $14/session GMV, 1,000 reserved nodes/day (Research Desk flagged).

**AUP:** Locked — KYC-gated attested destinations + hard denylist (banking, gov, mail); not public-web-only.


## 5. Customer price formula

```
price_per_GB = public_base × geo_mult × duration_mult × (optional exclusivity_mult)
```

- `public_base` (country rotating) = **$1.75/GB Locked**. Internal scarce floor **$4/GB** is planning-only — never the public country sticker.
- **No dual-metric $/hr in v1.**
- Applied at **assignment** (consumes scarce inventory), subject to soft-sticky re-rate rule in §6.
- Bytes metered continuously at the gateway (up + down).
- Insufficient balance: hard stop mid-stream after a small grace buffer (size TBD).

### 5.1 Geo / rematch multipliers (Locked working values on country base)

| Geo / mode | Mult | Buyer $/GB @ $1.75 base | Min KYC | Status |
|------------|------|-------------------------|---------|--------|
| Continent / country | **1.0×** | **$1.75** | T1 | **Locked** public list |
| Region | 1.5× | $2.625 | T2 | WORKING scaffold (may calibrate) |
| City | **1.5×** | **$2.625** | T2 | **Locked** working |
| `rematch-strict` | **2.0×** | **$3.50** | ≥T2 gated | **Locked** working |
| City + ASN | 4.0× scaffold | calibrate | **T3-only** | Calibrate; not list lock |
| Exclusive exit (add-on) | +1.5× scaffold | — | **T3-only** — §5.4 | Scaffold |
| Soft-sticky (add-on) | +0.2× scaffold | — | per sticky tier — §6 | Scaffold |

**Do not expose $4 as public country rate.** $4 = internal scarce-inventory floor for planning when city/strict inventory is thin.

### 5.2 Duration multipliers — v1 ladder (rotating → 24 h)

| Duration | Mult | Min KYC | Reliability class | v1 status |
|----------|------|---------|-------------------|-----------|
| Rotating | 1.00× | T1 | any healthy peer | **v1** |
| 5 min | 1.05× | T1 | any healthy | **v1** |
| 15 min | 1.10× | T1 | any healthy | **v1** |
| 30 min | 1.15× | T1 | any healthy | **v1** |
| 1 h | 1.25× | T1 | any healthy | **v1** |
| 3 h | 1.40× | T2 | above-median uptime | **v1** |
| 6 h | 1.60× | T2 | above-median | **v1** |
| 12 h | 2.00× | T2 | above-median | **v1** |
| 24 h | 2.50× | T2 | top-quartile | **v1** |

Custom TTLs snap **up** to the next tier on the **enabled** ladder. Extensions billed at the tier multiplier for added time. Early DELETE: assignment multiplier **not** refunded by default (exclusive peer-loss is a separate refund rule — §5.4).

### 5.2b Flagged / post-v1 duration tiers (48 h, 7 d)

Behind a **feature flag** until sticky-survival telemetry exists. Not sold in public v1 selector.

| Duration | Mult | Min KYC | Reliability class | Default remedy when enabled |
|----------|------|---------|-------------------|----------------------------|
| 48 h | 3.50× | T3 | top-quartile, best-effort | **best-effort + automatic service credit on peer loss** |
| 7 d | 5.00× | T3 | top-decile, best-effort | **best-effort + automatic service credit on peer loss** |

No SLA for these long tiers at enablement unless Jeff later locks one. Remedy = automatic **service credit** (not Peer clawback unless fraud).

### 5.3 Dynamic scarcity (optional, post-launch candidate)

```
geo_mult = 1 + α · ln(N_ref / N_pool)
```

Floored at 1.0, capped. `N_pool` = online, verified peers matching the selector. Publish live availability. Hold price quote for the session at assignment.

**Launch default recommendation:** static multipliers (§5.1–5.2) until pool telemetry exists; revisit dynamic in Phase 4.

### 5.4 Exclusivity inventory rules

Exclusive exit is **T3-only** and consumes scarce single-tenant peer inventory.

| Rule | Spec (starting proposal — Jeff can edit) |
|------|------------------------------------------|
| **Reservation TTL** | On quote/assign of exclusive, hold the peer for **5 minutes** (**Locked** starting TTL; revise if ops needs otherwise) awaiting session open; unused hold auto-releases. Exact TTL TBD. |
| **Peer loss mid-session** | Exclusive cannot silently reassign to another peer. Session ends (or customer opts into non-exclusive soft-sticky if offered). **Refund / service credit** the unused exclusive premium and unused prepaid usage per ToS (proposal: full unused exclusive add-on + unused GB at assignment rate). |
| **Interaction with capacity guardrails** | Exclusive seats ⊆ reserved share of healthy `N_pool` at the geo node. Cap concurrent exclusive assignments so remaining non-exclusive headroom stays above alert threshold. Auto-disable exclusive SKU when exclusive-eligible pool < threshold. |
| **Soft-sticky + exclusive** | If exclusivity breaks, do **not** charge exclusivity_mult on re-rated bytes (§6). |

Architecture must define reservation store keys and peer single-tenant lock (outline §2 gap closed by this product rule; implement in Sessions PRD).

## 6. Soft-sticky / fallback billing rule (proposal)

Until Jeff overrides (pending confirm widget / decision log):

1. **Hard-sticky:** price held at assignment geo+duration; peer loss → session error; no reassignment.
2. **Soft-sticky / fallback:** if reassigned or delivered at coarser geo, **re-rate remaining bytes** at the delivered geo multiplier; emit a session event; do not charge exclusivity if exclusivity was broken.
3. Customer dashboard shows assigned vs delivered geo when they differ.

***Locked 2026-09-28** (Jeff): soft-sticky/fallback re-rates at delivered geo.*

## 7. Peer payouts

> **Locked (2026-09-28):** Country peer share **25%** of public country price on verified GB → **$0.4375/GB**. Do not import PacketStream’s 10%. Phase 0 payout rails = **fiat-only**.

- Pay per **verified** GB only (verified geo tier, never claimed).
- Country: **25%** of buyer country $ (= $0.4375/GB at $1.75).
- City: **25%** of city buyer $ (WORKING — same % of price sold).
- Strict: **30% of strict buyer $** WORKING ASSUMPTION (eng handoff W6) to fund always-on depth.
- Fraud clawbacks; minimum cashout; payout geos/methods TBD (decision log) — fiat Phase 0.
- Bytes for payout = gateway-measured only (same counters as customer debit).

## 8. Billing rules (customer)

- Prepaid USD balance; KYC tier caps spend and concurrency.
- Card/bank: risk hold + staged limits on new accounts.
- Crypto: credit after confirmations + wallet screening; refunds only to originating address after review.
- Resellers: same top-up methods; invoices; sub-user balances.
- Tax/VAT per jurisdiction (finance + counsel).
- Refund / unused-balance policy → ToS (not specified here).
- Exclusive peer-loss and flagged long-tier peer-loss → **service credit** per §5.2b / §5.4.

## 9. Capacity guardrails

- Cap sales per geo node vs `N_pool`.
- Reserve share for existing customers **and** exclusive inventory (§5.4).
- Alert below headroom.
- Auto-disable sub-city (and tighter) tiers when depth < threshold.
- Auto-disable exclusive SKU when exclusive-eligible pool < threshold.
- Rule of thumb from pack: peers needed ≈ (target concurrent sessions ÷ per-peer concurrency) × 3–5 per geo node.
- Phase 0 seed `N_pool` targets must clear country-level rotating headroom before city/ASN claims (see outline Phase 0).

## 10. Metrics

- Revenue $/GB by geo and duration tier
- Peer payout $/GB and payout ratio (vs TBD target)
- Assignment success rate and sticky survival by tier (gate for enabling 48 h / 7 d)
- Pool depth and time-below-threshold by geo node
- Exclusive reservation abandon rate and peer-loss credit rate
- Ledger reconciliation drift (meter vs ledger)

## 11. Open decisions (also tracked in decision log)

1. ~~**`base` USD/GB**~~ → **Locked:** public country **$1.75/GB**; **$4** = internal floor only. Intro promo still Open.  
2. ~~**Peer payout % (country)**~~ → **Locked 25% ($0.4375/GB)**. Strict 30% = WORKING. Target platform margin % still Open.  
3. **Static vs dynamic pricing at launch** — this PRD recommends static first.  
4. **Enable 48 h / 7 d** only after sticky-survival data; remedy default = best-effort + auto service credit.  
5. ~~Exclusive reservation TTL~~ → **Locked 5 min**; peer-loss refund detail still refineable.  
6. ~~Confirm or reject §6 soft-sticky re-rate rule.~~ **Locked** — re-rate at delivered geo.  
7. Grace buffer size at zero balance.  
8. Peer payout methods, geos, min cashout (with counsel) — fiat Phase 0.
9. ~~Dual-metric~~ → **Deferred v1**.

## 12. Acceptance criteria (for an implementable v1)

- [ ] Gateway applies formula at assignment and records `price_mult` on every usage row.
- [ ] Soft-sticky re-rate (if confirmed) adjusts remaining bytes and records assigned vs delivered geo.
- [ ] Customer balance debits and Peer payouts are dual-entry, idempotent by `stream_id`.
- [ ] KYC tier rejects geo/duration above unlock (4xx, no silent clamp); ASN and exclusivity require T3.
- [ ] Sub-city / exclusive tiers disable when pool depth < threshold.
- [ ] Public v1 selector offers sticky **rotating → 24 h only**; 48 h / 7 d gated by feature flag.
- [ ] Exclusive reservation TTL and peer-loss service-credit path implemented.
- [ ] Dashboard shows estimated $/GB before connect and assigned vs delivered geo when they differ.
- [ ] No Peer payout on unverified or fraud-scored-excluded traffic.
- [ ] No customer-facing “Stream” brand; publish public country **$1.75/GB** only under chosen public brand. Never publish $4 as country list.

---

*v0 Rev align Sep 28, 2026 — Spec Keeper. Money math Locked 2026-09-28 (Jeff-authorized): public $1.75; peer 25%; $4 internal floor; city 1.5×; strict 2.0×; dual-metric deferred; fiat Phase 0. ASN/exclusive scaffolds remain calibrate-only.*
