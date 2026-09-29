# Stream — Product Spec Outline (tightened)

**Source:** Geo-Tiered Residential Proxy Service handoff pack (Rev 2); Research Desk brief + critique sync (Sep 28, 2026)  
**Role of this doc:** Living map of the product spec — section purpose + gaps to fill. Not a rewrite of the handoff pack.  
**Status:** design-stage · Settlement locked (USD prepaid + Peer per-GB payout) · Spec Keeper owns updates · Research Desk owns critiques/research · counsel owns legal  
**Identity (v1 framing):** Stream is a **compliant opt-in residential network** with paid hosts, scarcity-priced geo, and KYC-gated trust — **not** a “PacketStream-style P2P” clone. Jeff’s hard requirements: (1) client geo/specificity, (2) hosting incentives, (3) remain compliant/legal.

---

## How to read this
Each section below is what the living spec should contain. One line = purpose. Gaps and contradictions are listed for Jeff to resolve before Spec Keeper expands that section into a full PRD. Open decisions live in [`stream-decision-log.md`](./stream-decision-log.md) — do not re-list them here.

---

### 0. Product identity
**Purpose:** Name, one-liner, and what Stream is / is not at v1.

- Product name in docs: **Stream** (public/brand name pending Jeff confirm if different from working name).
- One-liner: prepaid, metered residential proxy via **explicitly consenting, paid Peers**; customers pay more for finer geo and longer sticky sessions; mandatory customer KYC.
- Positioning wedge (see §1.1): **verified geo integrity + duration SKUs + KYC-gated trust**.
- Non-goals (v1): mobile/ISP targeting, unblocker/CAPTCHA, browser APIs, anonymous/non-KYC purchase (crypto still requires KYC), silent SDKs / background proxy enrollment.

**Gaps**
- **Locked:** Stream = internal codename only. Public brand still Open (Contradiction D / decision log).

---

### 1. Product overview

### Target user & sold unit (Stacy rule)
**Purpose:** Define who we optimize for and what “sticky” actually promises.

- **Target user:** needs a stable residential exit in a **specific local area** for a multi-hour window (account workflows, localized QA, ad verification)—not max-entropy rotating scrape.
- **Sold unit (Locked 2026-09-28):** optional **session rematch modes** — `rematch-strict` (same city AND ≤15–25 km or matching H3; ASN AND-only; else hard-fail+credit), `rematch-city` (soft city sticky; Phase 0 default), `hard` (same peer only). Stacy opts into strict; scrapers stay on city.
- **Anti-pattern:** soft-sticky that stays “New Orleans” but jumps east↔west across the metro.
- **Hosts:** anyone may enroll; inventory **tiered** casual vs always-on; location verify via GeoIP ensemble + RTT + ASN (privacy: no payloads, no street address).
- **Trust Phase 0:** metadata-only attribution (account, time, dest host:port, bytes, geo tier).
- Open: exact bound, dual-metric ($/hr + $/GB) vs GB-only, AUP story (public-web-only vs account management).

**Purpose:** Who uses Stream, what they can do, and the selector/API surface customers see.

#### 1.1 Competitive positioning wedge
Stream differentiates on three product promises competitors under-deliver together:
1. **Verified geo integrity** — ensemble GeoIP + active verification; Peers paid by *verified* tier, never claimed; fail-closed on spoof/flap.
2. **Duration SKUs** — explicit sticky ladder (v1: rotating → 24 h) with reliability classes and clear hard/soft failure policy.
3. **KYC-gated trust** — capability (geo precision, session length, spend, concurrency) unlocks only with assurance tier; ASN and exclusivity are **T3-only**.

Not a race to PacketStream-style $1/GB commodity P2P. Compliance and consent quality are product features, not afterthoughts.

Subsections to keep:
1. What it is (§1.1 wedge above)  
2. Users (Customers, Peers, Resellers, Internal)  
3. Core capabilities table (geo, sessions, failure policy, protocols, auth, billing, access control)  
4. Selector syntax  
5. Non-goals  

**Notes on selector / geo (polish)**
- Public v1 selector exposes continent / country / region / city / optional ASN — **it does not imply H3 radius** as a customer-facing targeting mode. H3 may exist internally for indexing; do not document H3 as a sold SKU in v1.
- ASN targeting = **T3-only** (consistent with KYC unlock table).
- Duration ladder **v1** = rotating → 24 h. 48 h and 7 d stay behind a feature flag until sticky-survival data exists (see pricing PRD).

**Gaps**
- Customer use-case list is illustrative; need an allowlisted / reviewable use-case taxonomy tied to KYC tiers.
- Reseller product surface is named but thin (API shape, sub-user UX, invoicing) — expand when Phase 4 is in scope or sooner if sales needs it.

---

### 2. Technical architecture
**Purpose:** How traffic, identity, money, and geo integrity actually work so product promises are implementable.

Subsections to keep:
1. Components (gateway, relay, registry, session store, control plane, peer client, abuse, payments)  
2. Request flow  
3. Geo model and index  
4. Location integrity  
5. Sessions  
6. Metering and ledger  
7. Egress policy (peer + gateway)  
8. Data model sketch  
9. Scale and availability  
10. Security  
11. Payments (cards/wallets/bank + crypto)  

**Gaps**
- Soft-sticky “billed at the delivered level” — **drafted proposal kept** (re-rate at delivered geo); **Locked** 2026-09-28: re-rate remaining bytes at delivered geo (pricing PRD §6).
- Grace buffer at zero balance: size, customer notification, Peer payout behavior on hard stop.
- Peer client platforms listed; install/update/telemetry privacy policy still needs a product section (what is collected, retention).

---

### 3. KYC, compliance & trust and safety
**Purpose:** Who may use what capability, how harm is bounded, and what Peers consent to — **product controls only; not legal advice.** Counsel reviews before launch.

Subsections to keep:
1. Principles  
2. Onboarding tiers T0–T3 (**ASN + exclusive exit + reseller = T3-only**)  
3. Screening  
4. AUP summary  
5. Ongoing monitoring  
6. Reseller obligations (see concrete starting proposals below)  
7. Peer consent, privacy, safety  
8. Abuse handling and legal process  
9. Compliance checklist (for counsel)  
10. **Peer ISP / carrier risk** (new — product disclosure + playbook; not legal advice)

#### 3.6 Reseller obligations — starting proposals (Jeff can edit)
- **Audit sample rate (proposal):** audit **10%** of each reseller’s active sub-users **quarterly** (or 25 accounts, whichever is larger), plus any sub-user tied to an abuse case.
- **Auto-suspend triggers (proposal):** auto-suspend reseller wholesale when (a) an abuse SLA triage window (e.g. 24 h) expires **unresolved** on a critical case, or (b) ≥**3** unresolved abuse cases accumulate in a rolling 30 days, or (c) sub-user KYC/attribution records fail audit sample.
- Flow-down terms remain: resellers KYC sub-users to ≥T1 equivalent, maintain audit records, liable for sub-user misuse; credentials map to separate accounts for attribution.

#### 3.10 Peer ISP / carrier risk (product controls — not legal advice)
Some residential AUPs (e.g. **Comcast/Xfinity**) explicitly ban proxy servers / commercial resale of residential service. Product requirements:
1. **Install acknowledgment:** Peer client install flow must disclose that the Peer’s ISP/carrier ToS may prohibit proxy/server use; Peer must acknowledge before enrollment. Copy owned by product; counsel reviews language.
2. **Playbook if ISP terminates Peer:** support path to delist peer IP, pause payouts, preserve attribution for abuse if needed, and communicate next steps to the Peer (no guarantee of ISP reinstatement).
3. **Counsel sign-off before recruitment spend:** no paid Peer acquisition campaign in a jurisdiction/ISP class until counsel reviews disclosure + recruitment channel plan.
4. This section is **product risk control**, not an opinion on whether any Peer’s use is lawful under their ISP contract.

**Gaps**
- T0 “tiny capped test if permitted” is undefined (bytes, geo, duration, jurisdictions).
- Attribution-log retention “90–180 days typical” is a placeholder — needs a decision + counsel.
- Destination denylist ownership and change process — tracked in decision log.

---

### 4. Pricing & capacity
**Purpose:** How customer price and Peer payout are calculated, and how thin pools do not get oversold. Detail lives in [`stream-prd-pricing-capacity-payouts-v0.md`](./stream-prd-pricing-capacity-payouts-v0.md).

Subsections to keep:
1. Formula: `base × geo_mult × duration_mult × (optional exclusivity_mult)`  
2. Starting multipliers (geo + duration ladders) — **v1 duration = rotating → 24 h**; 48 h / 7 d flagged  
3. Dynamic scarcity option  
4. Peer payouts  
5. Billing rules  
6. Capacity guardrails  
7. Unit economics scaffold (TBD fields — no committed Stream list prices)  
8. Exclusivity inventory rules (summary below; detail in pricing PRD)

#### 4.8 Exclusivity inventory rules (summary)
- **Reservation TTL:** exclusive-exit holds a peer for a short quote/reservation window (exact TTL TBD; starting proposal in pricing PRD) before session start; unused reservation releases inventory.
- **Peer-loss refund:** if the exclusive peer is lost mid-session and cannot be replaced under exclusivity semantics, customer receives a **service credit / refund** for unused exclusive premium (and session ends or downgrades per mode — see pricing PRD).
- **Capacity guardrails:** exclusive inventory counts against `N_pool` headroom; cannot oversell exclusive seats beyond reserved share of healthy peers at that geo node.
- Soft-sticky + exclusivity: do not charge exclusivity if exclusivity was broken (aligns with soft-sticky re-rate proposal).

**Gaps**
- `base` USD/GB, Peer payout %, target platform margin — **TBD; Jeff must lock** (scaffold only in pricing PRD; comps are illustrative).
- Static vs dynamic pricing at launch — open (PRD recommends static first).

---

### 5. Delivery plan
**Purpose:** Phased build order, team sketch, risks. Decisions that block locking live docs → [`stream-decision-log.md`](./stream-decision-log.md).

#### Phase 0 — Seed countries & consent channel (BEFORE Foundations)

**Countries (Locked 2026-09-28):** United States and Canada. Counsel reviews KYC/data-protection fit; no expansion beyond these until Phase 0 kill criteria clear.

**Purpose:** prove compliant supply depth before building the full gateway stack for global claims.

- Launch in **1–2 countries** only (candidates TBD — Jeff + counsel; Research Desk recommends consent-friendly, payout-reachable geos).
- **Consent-compatible Peer channel only:** primary-purpose Peer app (desktop/Docker); **no silent SDKs**, no background enrollment in unrelated apps.
- Seed **`N_pool` targets** per launch geo node (numeric targets TBD with Market Scout / capacity rule of thumb).
- **Kill criterion:** if pool depth stays thin past the Phase 0 window (depth < threshold for country-level rotating at target concurrency × headroom), pause geo expansion and revisit recruitment/channel — do not proceed to city/ASN claims on vapor inventory.

#### Phases 1–4 (release backbone)
1. **Foundations:** gateway, relay, peer client, registry, country-level rotating, ledger, basic KYC T1, first card/wallet processor.  
2. **Geo depth:** region/city index, geo verification, pricing engine, GET /sessions, dashboard, crypto + wallet screening.  
3. **Sessions and trust:** duration ladder **rotating → 24 h** with reliability classes and extend API; second processor; uptime scoring; hard/soft sticky; T2/T3; attribution log; abuse tooling. (48 h / 7 d remain flagged.)  
4. **Reseller and scale:** reseller API, multi-region cells, dynamic pricing candidate, audits.

Keep phases 1–4 until Jeff reorders them. Phase 0 is a hard prerequisite for honest geo claims.

**Open decisions** — see [`stream-decision-log.md`](./stream-decision-log.md). Do not maintain a parallel hanging list here.

---

## Contradictions & tensions to resolve

**A. Settlement model — LOCKED (Jeff, Sep 28, 2026)**  
- Customers: **prepaid USD** balance (cards, wallets, bank, crypto — all KYC-gated).  
- Peers: **paid per verified GB** (existing payout rails; optional stablecoin payout needs identity verification + counsel).  
- Credit-barter is **out of scope** for Stream unless Jeff reopens it. All living docs follow this.

**B. “Credits” word collision**  
- Pack uses “credit” for customer prepaid balance and for failure remediation on long tiers.  
- Peer side is payout, not the same credit. Glossary should split: *customer balance*, *Peer payout*, *service credit (remedy)*.

**C. Price at assignment vs delivered geo — drafted proposal pending Jeff confirm**  
- Soft-sticky / fallback: **re-rate remaining bytes at delivered geo** (pricing PRD §6). Hard-sticky: price held at assignment; peer loss → session error.  
- Confirm widget pending Jeff; until overridden, living docs keep the re-rate rule.

**D. Product name / pack title — PARTIAL LOCK**  
- Living docs use **Stream** as the **internal** codename only (Jeff 2026-09-28).  
- Public/customer-facing brand remains **Open**. Pack title “Geo-Tiered Residential Proxy Service” is descriptive, not the brand.

---

## Proposed living doc set
1. This outline (map) — **done / Rev align Sep 28, 2026**  
2. [`stream-decision-log.md`](./stream-decision-log.md) — **done**  
3. PRD: Pricing, capacity & Peer payouts — **done (v0, unit-econ scaffold)**  
4. Glossary (resolve B)  
5. PRD: Sessions & selector  
6. PRD: KYC tiers & access control  
7. PRD: Peer client, consent & egress policy  
8. PRD: Payments & ledger  
9. PRD: Trust & abuse (product controls)  

---

*Spec Keeper — outline aligned to Research Desk critique (Sep 28, 2026). Research Desk Proposed package **Locked** by Jeff 2026-09-28. Remaining Open rows are numerics/vendors/jurisdictions.*
