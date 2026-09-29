# P2P Residential / Mobile Proxy Marketplace — Product Outline (v0)

**Owner:** Spec Keeper (product docs)  
**Status:** Draft for Jeff’s review — not committed decisions  
**Inputs:** Stated product idea + Research Desk brief (`/workspace/research/p2p-proxy-marketplace-brief.md`)  
**Out of scope here:** Market sizing, legal opinion, competitor critique (Research Desk)

---

## 1. One-liner

A two-sided marketplace where people share home or mobile bandwidth as proxy exits, earn **in-network credits**, and spend those credits to use proxies themselves — with **location specificity** driving price.

---

## 2. Who it’s for

| Role | Goal | Success looks like |
|---|---|---|
| **Provider** | Opt in to share bandwidth; earn credits without cash complexity | Clear consent, kill switch, predictable credit rate, no surprise ISP pain |
| **Client** | Buy / redeem access to residential or mobile exits at a chosen geo | Reliable sessions, honest geo labeling, simple pricing |
| **Platform** | Match supply ↔ demand; keep credits solvent; keep abuse contained | Liquid geos, trusted brand, enforceable traffic policy |

---

## 3. Core product loop

1. Provider installs a **primary-purpose** share app (or agent), consents in plain language, sets caps (Wi‑Fi only, daily GB, schedule).
2. Platform verifies exit quality (IP type, geo/ASN, reputation signals) and lists inventory.
3. Client requests a session: protocol + **geo specificity** (country → region → city → ZIP/ASN) + stickiness.
4. Credits move: client spends → provider earns (minus platform take, TBD).
5. Either side can stop: provider kill switch; client session end; platform policy kill.

---

## 4. Credit economy (product shape — open knobs)

**Working assumption from the idea:** Credits are the primary settlement between peers (barter), not cash payouts like PacketStream/Honeygain.

| Decision | Options to pick | Why it matters |
|---|---|---|
| Redemption | In-network only vs. cash-out vs. hybrid | Cold-start & regulatory surface |
| Issuance | Fixed $/GB-equivalent vs. demand-weighted geo rates | Avoid EarnApp-style silent devaluation |
| Platform take | Fixed % vs. spread on geo tiers | Provider trust + runway |
| Anti-abuse | Wash-trade detection, unique-device rules, CGNAT demotion | Credit inflation & fake residential |

*Spec Keeper will lock these into a dedicated Credits PRD once Jeff picks a direction.*

---

## 5. Location-specificity pricing

**Idea:** More specific location request → higher cost.

**Product sketch (not locked):**
- Base rate for country-level residential
- Multipliers (or auction) when inventory is scarce at city / ZIP / ASN
- Mobile SKU separate from residential (usually premium)
- Client UI shows **estimated cost before connect**, and whether the claimed geo is verified vs. best-effort

**Open:** Flat GB + free targeting (Bright Data–style marketing) vs. true scarcity multipliers — needs an inventory oracle Jeff trusts.

---

## 6. Trust & safety (product requirements, not legal advice)

Must-have product surfaces Research Desk’s brief implies:
- Explicit, auditable **consent** + kill switch + traffic-class controls
- Client **KYC / allowlisting** before high-risk use
- Real-time **traffic policy** (block obvious abuse classes; document what you *don’t* inspect, e.g. TLS payload)
- Provider-facing disclosure that **some ISP AUPs forbid proxy servers** on residential plans
- Sourcing transparency for enterprise buyers (how this IP was enrolled)

*Legal review stays with Research Desk / counsel; this outline only names product controls to specify.*

---

## 7. MVP slice (proposal)

**In:**
- Desktop provider agent (outbound-only; no inbound ports)
- Credit ledger (earn ↔ spend), country-level targeting
- Client API + simple dashboard
- Basic abuse: rate limits, blocklists, session logs retained for dispute

**Out of first slice:**
- Mobile share app / Play distribution
- City/ZIP/ASN scarcity auction
- Cash off-ramp
- SDK-in-other-apps distribution

---

## 8. Doc set Spec Keeper will maintain

1. **This outline** → living product thesis  
2. **PRD: Marketplace & matching**  
3. **PRD: Credits & pricing**  
4. **PRD: Provider agent & consent UX**  
5. **PRD: Client API & session model**  
6. **PRD: Trust, KYC, traffic policy** (product controls only)  
7. **Glossary** (residential vs mobile, sticky session, CGNAT, ASN, etc.)

---

## 9. Decisions needed from Jeff (blocks deepening)

1. Credits: in-network only, cash-out, or hybrid?  
2. Distribution: primary-purpose app only for v1, or SDK later?  
3. First geos to seed?  
4. Pricing: scarcity multipliers vs. flat GB + free targeting?  
5. Which PRD to draft first?

---

*v0 — revise freely. Spec Keeper updates this when product choices change.*
