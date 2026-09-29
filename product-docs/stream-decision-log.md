# Stream — Decision Log

**Owner:** Spec Keeper (maintains table) · **Approver:** Jeff Buzzball for product locks · Counsel for legal  
**Living docs:** [`stream-spec-outline-v0.md`](./stream-spec-outline-v0.md) · [`stream-prd-pricing-capacity-payouts-v0.md`](./stream-prd-pricing-capacity-payouts-v0.md)  
**Rule:** Outline §5 no longer lists open questions inline — update **this** file instead.

Status values: **Locked** | **Open** | **Locked**

| Decision | Status | Owner | Date | Notes |
|----------|--------|-------|------|-------|
| Settlement model = prepaid USD (customers) + per-GB Peer payout (not credit-barter) | **Locked** | Jeff | 2026-09-28 | All living docs follow this. Optional Peer stablecoin payout still needs identity + counsel. |
| Product name **Stream** = **internal only** (not public brand) | **Locked** | Jeff | 2026-09-28 | Living docs keep “Stream” as the internal codename. Public/customer-facing brand name remains Open. |
| Identity framing: compliant opt-in residential network (paid hosts + scarcity geo + KYC trust), not “PacketStream-style P2P” | **Locked** | Spec Keeper / Research Desk | 2026-09-28 | Research Desk critique; outline §0 reframed accordingly. |
| Positioning wedge = verified geo integrity + duration SKUs + KYC-gated trust | **Locked** | Spec Keeper / Research Desk | 2026-09-28 | Outline §1.1. |
| ASN targeting = T3-only | **Locked** | Spec Keeper | 2026-09-28 | Aligns with handoff T3 unlock; stated consistently in outline + pricing PRD. |
| Exclusive exit = T3-only | **Locked** | Spec Keeper | 2026-09-28 | Handoff pack; reinforced in pricing PRD. |
| Sticky ladder v1 scope = rotating → 24 h only; 48 h / 7 d behind feature flag | **Locked** | Research Desk / Spec Keeper | 2026-09-28 | Until sticky-survival data exists. |
| Long-tier (48 h / 7 d) remedy when enabled = best-effort + automatic service credit on peer loss (no SLA unless later locked) | **Locked** | Research Desk / Spec Keeper | 2026-09-28 | Replaces open “credits vs SLA” hanging question with a default proposal. |
| Soft-sticky billing = re-rate remaining bytes at delivered geo | **Locked** | Spec Keeper | 2026-09-28 | Locked with Research Desk package 2026-09-28; pricing PRD §6. |
| Exclusive reservation TTL (proposal: 5 min) + peer-loss refund/service credit of unused exclusive premium | **Locked** | Spec Keeper | 2026-09-28 | Pricing PRD §5.4; outline §4.8 summary. |
| Peer ISP / carrier risk disclosure required at install; playbook if ISP terminates; counsel sign-off before recruitment spend | **Locked** | Research Desk / Counsel | 2026-09-28 | Outline §3.10 — product controls only, **not legal advice**. Comcast-class AUP conflict cited as example. |
| Phase 0 before Foundations: 1–2 launch countries; primary-purpose Peer app only (no silent SDKs); seed N_pool; kill criterion if depth thin | **Locked** | Research Desk / Spec Keeper | 2026-09-28 | Outline §5 Phase 0. |
| Phase 0 launch countries = **US + Canada** | **Locked** | Jeff | 2026-09-28 | Counsel still reviews KYC/data-protection fit per country. Peer payout reachability and recruitment copy must cover both. |
| Phase 0 N_pool seed targets + kill thresholds (US+CA) | **Open** | Jeff / Market Scout | 2026-09-28 | No public concurrent-peer formula. CA thinner than US (vendor inventory displays). Inference: peers ≈ (concurrent ÷ sess/peer) × 3–5; ~100 concurrent country rotating → ~100–500 online peers (wide). |
| Public country rotate list = **$1.75/GB**; `$4.00/GB` = **internal scarce-inventory floor only** (NOT public list) | **Locked** | Spec Keeper (Jeff-authorized 2026-09-28) | 2026-09-28 | Reinterpreted prior $4 lock: public sticker $1.75; $4 planning floor when city/strict inventory scarce. |
| Peer payout (country) = **25%** of public country on verified GB → **$0.4375/GB** | **Locked** | Spec Keeper (Jeff-authorized 2026-09-28) | 2026-09-28 | Not PacketStream 10%. Strict peer share **30% of strict $** = WORKING ASSUMPTION (eng handoff W6). |
| Target platform margin % | **Open** | Jeff | — | Unit-econ scaffold in pricing PRD §4.0. |
| Static vs dynamic scarcity pricing at launch | **Open** | Jeff | — | PRD recommends static first; dynamic post-telemetry / Phase 4. |
| Target jurisdictions (beyond Phase 0) for KYC / data protection | **Open** | Jeff + Counsel | — | Former open Q1. |
| Attribution log retention length + LE policy | **Open** | Counsel | — | Placeholder 90–180 days; needs decision. |
| Hard-sticky only at launch vs soft-sticky too | **Open** | Jeff | — | Soft-sticky billing rule drafted; mode enablement still open. |
| KYC vendor; KYB required for all businesses? | **Open** | Jeff / Spec Keeper | — | Former open Q5. |
| Peer payout methods and geographies; min cashout | **Open** | Jeff + Counsel | — | Former open Q6. |
| Destination denylist scope + owner | **Open** | T&S / Spec Keeper | — | Former open Q7. |
| Peer recruitment channels compatible with explicit consent | **Open** | Jeff / Spec Keeper | — | Constrained by Phase 0: primary-purpose app only; no silent SDKs. |
| Processors, crypto rails, assets, chains | **Open** | Jeff / Payments | — | Former open Q9. |
| Chargeback reserve / hold for new accounts | **Open** | Finance / Jeff | — | Former open Q10. |
| Crypto treasury: hold / fiat / stablecoin | **Open** | Finance / Jeff | — | Former open Q11. |
| Reseller audit sample rate (proposal: 10% of active sub-users quarterly, or 25 accounts min) | **Locked** | Spec Keeper | 2026-09-28 | Outline §3.6. |
| Reseller auto-suspend triggers (proposal: unresolved abuse SLA breach; ≥3 unresolved / 30d; failed KYC audit sample) | **Locked** | Spec Keeper | 2026-09-28 | Outline §3.6. |
| Grace buffer size at zero balance | **Open** | Spec Keeper / Jeff | — | Pricing PRD open #7. |
| T0 tiny capped test definition (bytes, geo, duration, jurisdictions) | **Open** | Spec Keeper / Jeff | — | Outline gap. |
| Public / customer-facing brand name (Stream is internal only) | **Open** | Jeff | — | Do not use “Stream” in customer ToS, marketing, Peer install UI, or KYC vendor configs until brand is chosen. |
| Public v1 selector does **not** expose H3 radius as a sold SKU | **Locked** | Spec Keeper | 2026-09-28 | H3 may be internal index only. |

---

*Spec Keeper — created 2026-09-28 CT. Mark Research Desk recommendations **Locked** until Jeff locks them.*

**2026-09-28 CT:** Jeff locked `base` = **$4.00/GB** (country rotating, US+CA).

**2026-09-28 CT:** Stream Research room (Market Scout sheet + Research Desk order): logged competitive options #3→#5→#1→#2→#4; base $4 holds only if city wedge ships.

## Competitive decision options (Stream Research room — 2026-09-28 CT)

Source: Market Scout sheet `/workspace/market-scout/stream-vs-mysterium-vs-packetstream-2026-09-28.md` + Research Desk pressure-test. **Not locks.** Spec Keeper will queue Jeff prompts in this order:

| Priority | Option | Choices (for Jeff) | Status | Notes from room |
|----------|--------|-------------------|--------|-----------------|
| 1 | **#3 Location SKUs** | Evolving: **city + radius/ASN locality wedge** (Stacy) vs country+city add-on vs country-only until depth | Open | Stacy reframes: locality-bounded rematch, not city-label soft-sticky. |
| 2 | **#5 Phase 0 settlement rails** | Fiat-only Phase 0 vs stablecoin after counsel vs defer crypto | **Locked: fiat-only Phase 0** | Spec Keeper Jeff-authorized 2026-09-28. |
| 3 | **#1 Base band** | Public **$1.75**; $4 internal floor | **Locked** | Spec Keeper Jeff-authorized 2026-09-28. |
| 4 | **#2 Peer share** | Do **not** copy PacketStream 10%; back from margin vs sold base | **Locked: 25% country → $0.4375/GB** | Spec Keeper Jeff-authorized 2026-09-28. |
| 5 | **#4 Residential premium** | One residential base + geo/duration (preferred) vs visible res multiplier vs Peer-only quality bonus | Proposed default: one res base + geo/duration | RD: no visible res multiplier until quality telemetry. |

**vs comps reminder:** vs PacketStream win on city/ASN + duration + trust; lose on $1 simplicity. vs Mysterium win on USD clarity / proxy SKUs / sticky discipline; lose on live scale.

## Target-user product rule — locality-bounded reservation (2026-09-28 CT)

**Persona (Jeff):** Buyer wants a **stable residential IP** in a **given local area**, accessed consistently. Example: Stacy reserves New Orleans for 4 hours; failover must **not** jump across the metro.

**Product rule (capture before multipliers) — Proposed pending Jeff lock:**
1. Sold unit = **locality-bounded reservation**, not “city sticky + soft failover anywhere in the city label.”
2. On peer loss during a reserved session: rematch **only** inside a bound (same H3 cell and/or ≤X km and/or same ASN — exact bound TBD). If no eligible peer: **hard-fail + service credit**.
3. Soft-sticky rematch outside the bound is **out of spec for `rematch-strict` / Stacy sessions**; `rematch-city` may still metro-jump (disclosed).
4. Location SKUs (#3): **`rematch-strict` (locality) is the wedge SKU**; city soft-sticky remains the cheap default; hard = same-peer-only.
5. **Market Scout:** no public vendor found selling locality-bounded rematch SLA; closest is ZIP/ASN+sticky without distance rematch. Wedge is real vs Bright/Oxylabs/SOAX/PacketStream.
6. Dual-metric pricing (reserve **$/hr** + metered **$/GB**) fits Stacy better than pure GB — **Open** (proposal ranges in Jeff’s economic-model note; do not lock 60/40 or $14/session GMV).
7. Phase 0 settlement: keep **fiat**; park DePIN/token burn for later (aligns with Research Desk).
8. **AUP tension to resolve:** proposal’s “public web only” conflicts with account-management use cases in the same pack — Jeff must pick one AUP story before T&S PRD freezes.

| Decision | Status | Owner | Date | Notes |
|----------|--------|-------|------|-------|
| Sold unit includes optional locality-bounded reservation via `rematch-strict` | **Locked** | Jeff | 2026-09-28 | Green light in Stream Research; optional SKU not network-wide mandate. |
| `rematch-strict` bound = same city AND (≤15–25 km OR matching H3); ASN optional AND | **Locked** | Jeff | 2026-09-28 | Jeff + MS fraud-safe locality; tune inside 15–25 without reopening rule. |
| Dual-metric tariff (reserve $/hr + $/GB) vs GB-only | **Deferred v1** (Locked deferral) | Spec Keeper (Jeff-authorized 2026-09-28) | 2026-09-28 | v1 = GB-only formula. Dual-metric stays out of Phase 0/v1. |
| Host split 60/40 + $14/session GMV assumptions | **Open — do not lock** | Jeff / finance | — | RD: utilization not evidenced |
| AUP: KYC-gated attested destinations + hard denylist (banking, gov, mail); **not** public-web-only | **Locked** | Jeff | 2026-09-28 | Stacy persona; Research Desk package. |
| DePIN / token burn / staking for Phase 0 | **Deferred** | Jeff | 2026-09-28 | RD + room: fiat Phase 0; park tokenomics |

### Research Desk three-lock package — **LOCKED** (Jeff “Sounds good” + RD green light, 2026-09-28 CT)
1. **Locked:** optional rematch modes + locality reservation via `rematch-strict` (not network-wide).
2. **Locked:** strict contract = H3 or ≤X km; ASN optional AND (not OR); fail closed on metro-wide jumps. Numeric X still Open.
3. **Locked:** AUP = KYC-gated attested destinations + hard denylist (banking, gov, mail); **not** public-web-only.
Also Locked: Phase 0 default `rematch-city`; strict priced/gated.
Dual-metric: still **deferred**. Exact X km / H3 resolution: **Open** (calibration).

### Locality rematch = optional session SKU (2026-09-28 CT — Jeff question + room)

Jeff: can locality bound be optional / left up to the user? **Yes** (Research Desk + Market Scout).

**Proposed product shape (pending Jeff lock):**
| Mode | Behavior | Phase 0 stance |
|------|----------|----------------|
| `rematch-strict` | Rematch only inside **H3 or ≤X km** (ASN optional **AND**); else hard-fail + service credit | Opt-in; **price/gate** (higher KYC tier + scarcity mult) |
| `rematch-city` | Soft-sticky within city label (metro jump possible) | **Default** for thin pools |
| `hard` | Same peer only; peer loss → session error | Available |

- Stacy selects **strict**; scrapers who don’t care use **city**.
- When strict is selected, bound shape is **defined** (not vague) — still lock H3/≤X km + AND-ASN semantics.
- Market Scout comps: Geonode `strict-on` vs soft; city/ASN opt-in elsewhere; RaxyProxy city/ZIP/ASN often **2×** traffic — pricing/gating strict matches market.
- AUP recommendation unchanged: drop public-web-only; KYC-gated attested destinations + hard denylist.
- Dual-metric still deferred.

| Decision | Status | Owner | Date | Notes |
|----------|--------|-------|------|-------|
| Locality-bound rematch is **optional session mode** (`strict` / `city` / `hard`), not network-wide mandate | **Locked** | Jeff | 2026-09-28 | “Sounds good” + RD green light in Stream Research. |
| Phase 0 default rematch mode = `rematch-city` | **Locked** | Jeff | 2026-09-28 | Thin pool depth. |
| `rematch-strict` priced/gated above city default | **Locked** | Jeff | 2026-09-28 | Multiplier TBD; principle Locked. |


**2026-09-28 CT:** Jeff locked optional rematch modes + strict contract + Stacy AUP package (Stream Research green light).

## Next decision-log queue (Stream Research — 2026-09-28 CT)

Jeff asked what else needs attention. Research Desk order + Market Scout notes. **Not locks.**

| Priority | Topic | Why it matters | Status | Who |
|----------|-------|----------------|--------|-----|
| 1 | **Phase 0 supply** | Primary-purpose Peer app only; always-on hardware nudge for reserved/`strict` sessions; US+CA `N_pool` targets + kill criterion. Without depth, `rematch-strict` always hard-fails. MS: city residential depth thin even on larger nets (~46 US res cities in Mysterium sample); strict needs denser always-on than city. | Open | Jeff + Market Scout (targets) |
| 2 | **ISP/carrier disclosure + counsel** | Warn at install; counsel on disclaimers — **not** blanket liability waive | **Locked posture** (warn+counsel); counsel text Open | Jeff + Counsel |
| 3 | **Unit economics** | Public $1.75; peer 25%; city 1.5×; strict 2.0×; dual-metric deferred; $4 internal floor; fiat Phase 0 | **Locked** (2026-09-28 Spec Keeper Jeff-authorized) | Eng handoff + margin sheet |
| 4 | **Strict numerics** | same city + ≤15–25 km / H3; NOLA east≠west fails closed | **Locked** (band); ops tunes inside band | Jeff / Spec Keeper |
| 5 | **Trust ops** | Metadata-only Phase 0 (no payloads); geo-verify + CGNAT for premium/strict; attestation + abuse SLA still to detail | **Partial Locked** (privacy posture); workflow detail Open | Jeff / T&S |

**Lower priority until 1–5 clear:** exclusivity inventory detail, reseller audits, crypto/Peer payout rails.


**2026-09-28 CT:** Jeff asked remaining gaps; Spec Keeper queued RD 1–5 (+ MS supply/econ notes) as next decision-log queue.

### Plain-language version of queue 1–5 (Research Desk, 2026-09-28 CT)

1. **Phase 0 supply** — Who runs the host app, how we get enough always-on boxes in US/Canada, and when we admit the pool is too thin and stop selling strict sessions. Without that, Stacy’s neighborhood option mostly fails.
2. **ISP / legal honesty** — Tell hosts some home plans ban proxying; decide what we do if ISP cuts them off. Counsel before recruit spend.
3. **Money math** — Buyer prices (country vs city vs strict), host earn, time+data vs data-only. Build peer pay from margin — don’t copy PacketStream’s 10%.
4. **How tight is “strict”** — Actual distance / H3 cell size so NOLA east can’t silently become west. Measure X; don’t leave blank.
5. **Trust ops** — Prove peer location, block bank/gov abuse destinations, real process when something goes wrong.

Start Jeff on **#1**.

## Jeff answers on queue 1–5 + room pressure-test (2026-09-28 CT)

Jeff’s statements → Research Desk / Market Scout refine → Spec Keeper status.

| # | Jeff said | Room refine | Status |
|---|-----------|-------------|--------|
| 1 | Anyone can run a host; verify location while preserving privacy | Anyone may enroll; **tier hosts** (casual vs always-on) so strict isn’t mostly laptops. Verify: ensemble GeoIP + RTT/anchors + ASN; pay/attest **tier** (city/H3); never browse traffic or store street address. | **Locked** (method + tiers principle) |
| 2 | Always warn users; always waive liability | Warn yes. **Blanket waive is NOT a safe lock** — ToS doesn’t stop ISP cutoffs, CFAA/third-party suits, regulators. Status: **warn + counsel** on what can be disclaimed; do not claim waiver solves compliance. | **Locked as warn + counsel — not blanket waive** |
| 3 | Must be profitable, intuitive, competitive | Goals only. Still need public price band, peer share from margin, dual-metric decision. | **Open** (goals noted) |
| 4 | Distance that wouldn’t raise flags where client IP is logged/checked | MS: impossible-travel often ≥~500 km + high speed; same-metro jitter often ignored (~50 km floors). Geo consistency wants same city / ~10–25 km urban. **Strict default Locked against:** same city AND ≤~15–25 km (or matching H3), ASN AND-only; minimize mid-session IP hops. Exact X in 15–25 still calibratable. | **Locked** (15–25 km / same city + H3; ASN AND) |
| 5 | Trust ops while maintaining privacy | Metadata only: who, when, dest host:port, bytes, geo tier — no payloads; short retention; peer sees payout not buyer identity. Skip ZK Phase 0. | **Locked** (metadata-only Phase 0) |

### Locked detail — #1 host verify + tiers
- Enrollment: open (anyone can host).
- Inventory classes: **casual** vs **always-on** (hardware nudge for reserved/strict eligibility).
- Location integrity: ≥2 GeoIP + RTT triangulation + ASN; fraud score excludes premium/strict; pay by **verified tier**, never claimed street address.
- Privacy: no payload inspection; no household street address stored for product geo.

### Locked detail — #4 strict distance (updates prior “X Open”)
- `rematch-strict` bound: **same city AND (≤15–25 km OR matching H3)**; ASN optional **AND** only.
- Fails closed on New Orleans east≠west.
- Prefer minimizing rematch hops (many systems flag any mid-session IP change).
- Exact km within 15–25 / H3 resolution: ops may tune inside this band without reopening the product rule.

### Locked detail — #2 (careful)
- Product: clear ISP/proxy-ban warning at install (outline §3.10).
- Legal: counsel defines disclaimer language; Spec Keeper will **not** document “we always waive all liability” as a product lock.

### #3 money math — **LOCKED** (Jeff authorized Spec Keeper 2026-09-28; no further prompts)
- Public country rotate: **$1.75/GB**
- Peer country: **25%** → **$0.4375/GB** on verified GB
- **$4/GB** = internal scarce floor only (NOT public list)
- City mult **1.5×**; `rematch-strict` mult **2.0×** (working, on country base)
- Dual-metric **deferred v1**; fiat-only Phase 0
- Formula: `price_per_GB = public_base × geo_mult × duration_mult × optional exclusivity_mult`
- See eng handoff `/workspace/product-docs/stream-eng-handoff-v0.md` + margin sheet


**2026-09-28 CT:** Jeff answered queue 1–5; Spec Keeper locked 1/4/5 + warn+counsel on 2; left 3 Open per Research Desk.

## #3 money math — **LOCKED** (2026-09-28 CT)

Jeff authorized Spec Keeper to lock without further prompts (2026-09-28). Eng handoff shipped.

| Step | Decision | Status |
|------|----------|--------|
| 3.1 | Public country rotate = **$1.75/GB** | **Locked** |
| 3.2 | Peer country = **25%** → **$0.4375/GB** verified | **Locked** |
| 3.3 | City **1.5×**; rematch-strict **2.0×**; dual-metric **deferred v1** | **Locked** (working mults) |
| 3.4 | US/CA N_pool + kill criterion | **WORKING ASSUMPTIONS** in eng handoff §3 (W2–W4) — eng may refine with telemetry |
| — | Fiat-only Phase 0 | **Locked** |
| — | $4 = internal scarce floor only (NOT public list) | **Locked** (reinterpreted) |

| Decision | Locked value | Notes |
|----------|--------------|-------|
| Public country rotate $/GB | **$1.75** | PacketStream-adjacent; above $1 for KYC/trust overhead |
| $4.00 prior base | **Internal scarce-inventory / planning floor** — not public country list | City/strict are paid upgrades |
| Peer share on country | **25%** of public country $ → **$0.4375/GB** | From margin on price sold; not 10% of $4 |
| Strict peer share | **30% of strict $** WORKING ASSUMPTION | Eng handoff W6; funds always-on |
| City / strict buyer mult | **1.5× / 2.0×** working | On country base |
| Dual-metric | **Deferred v1** | GB-only formula |
| Phase 0 rails | **Fiat-only** | Crypto peer payout / DePIN deferred |

MS sheet: platform after peer+fees at $1.75/25% ≈ **~$1.26/GB** (~72%) before opex. Full embed: eng handoff §10 / `/workspace/market-scout/stream-margin-sheet-1.75-2026-09-28.md`.

---

**2026-09-28 CT (late):** Jeff authorized Spec Keeper to lock money math without further prompts. Locked: public country **$1.75/GB**; peer country **25% ($0.4375/GB)**; **$4** = internal scarce floor only; city **1.5×**; rematch-strict **2.0×** working; dual-metric **deferred v1**; fiat-only Phase 0. Eng handoff: `/workspace/product-docs/stream-eng-handoff-v0.md`.

## Eng handoff pressure-test patches (Locked 2026-09-28 CT)

Research Desk six fixes — Spec Keeper locked without Jeff ping (authorized hammer-out):
1. **No stack** city×strict — strict 2.0× replaces city when both selected; country+strict rejected.
2. Duration ladder pasted inline in eng handoff §2.3e.
3. Canonical hard mode name: **`hard`** only.
4. Strict hard-fail service credit: unused session-window usage at assignment $/GB + unused exclusive premium; not full balance forfeit.
5. `rematch-city` = geo×duration only — **no** +0.2× soft-sticky add-on on default path.
6. Phase 0 attestation = categories + fast-path review — not weeks-long domain allowlist.
Minor: exclusive×strict peer-loss one-liner; region SKU collapsed in v1.
