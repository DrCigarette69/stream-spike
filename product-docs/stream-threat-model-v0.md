# Stream — Threat model (v0)

**Owner:** Stream Architect · **Control hooks:** Platform Engineer, Peer Engineer · **Consent/warning UX:** Stream Designer  
**Audience:** Stream Dev + Jeff · **Date:** 2026-09-28 (CT)  
**Status:** Design threat model — required before production tunnel code · Platform §11 + Peer §12 + Designer §13 attached · **Not legal advice** (counsel owns disclaimer/ToS language)  
**Aligns:** architecture sketch v0.1 · session sequence v0 · eng handoff · outline §3 · Peer MVP §5

Phase 0 scope: US+CA · primary-purpose Peer app · fiat settlement · metadata-only attribution.

---

## 1. Assets & trust boundaries

| Asset | Why it matters |
|-------|----------------|
| Residential exit IP / ASN reputation | Commodity + liability if abused |
| Customer prepaid balance + Peer accrued payout | Direct financial loss / fraud |
| Session attribution log (metadata only) | Abuse response + LE process |
| Peer device (bandwidth, LAN exposure) | Host safety + ISP termination |
| KYC / identity assurance | Capability gating; chargebacks; sanctions |
| Control-plane keys, Peer device certs, relay mTLS | Network takeover if stolen |

**Trust boundaries:** Client ↔ Gateway · Gateway ↔ Relay · Relay ↔ Peer · Peer ↔ destination · Client/Peer ↔ Control plane (payments, KYC, registry). Gateway is metering/source-of-truth; Peer never trusted for ledger bytes.

---

## 2. Adversaries (who)

1. **Abusive Client** — spam, credential stuffing, scraping bans, malware C2, fraud against third parties via residential IP.  
2. **Fraudulent Peer** — datacenter/VPN/CGNAT disguised as residential; geo spoof; fake always-on; payout farming with no useful inventory.  
3. **Compromised Peer household** — malware on host; attacker pivots via agent; LAN scanning via mis-assigned dial.  
4. **Account takeover (Client or Peer)** — stolen creds drain balance or redirect payouts.  
5. **Reseller / sub-user abuse** — Phase 4 risk; note controls early.  
6. **External attacker** — MITM tunnels, steal device keys, DDoS matching, replay webhooks.  
7. **Insider / support misuse** — over-broad access to KYC or attribution.

---

## 3. Abuse cases (Client egress)

| ID | Case | Impact | Phase 0 control (required) | Primary owner |
|----|------|--------|----------------------------|---------------|
| A1 | Banking / gov / mail / known-abuse destinations | Legal + brand; Peer ISP blowback | **Hard denylist** at Gateway (+ Peer local floor). Fail closed. | Platform (list) · Peer (floor) |
| A2 | Non-attested use (e.g. bulk abuse outside declared category) | AUP breach | KYC-gated **attested categories** + fast-path review; elevate tier for riskier categories | Platform · Designer (attest UX) |
| A3 | High-volume rotating scrape / botnet-like concurrency | IP burn; Peer complaints | Concurrency + spend caps by KYC tier; velocity alerts; auto-throttle | Platform |
| A4 | Credential stuffing / account takeover against third parties | LE / civil risk | Dest denylist + anomaly (many distinct auth hosts); case tooling | Platform · T&S |
| A5 | Malware C2 / phishing kit hosting via Peer | Critical | Port allowlist default **80/443**; block SMTP/25; denylist + rapid Peer delist | Platform · Peer |
| A6 | Mid-session geo spoof expectations (buyer fraud vs Peer) | Chargebacks / support | Soft sticky **re-rate**; strict **hard-fail + credit** — never silent metro jump | Platform · Designer |
| A7 | Chargeback / stolen card top-up | $ loss | Fiat processor risk holds; KYC match payer↔account; grace ≠ negative balance | Platform · Payments |
| A8 | Exclusive / strict inventory hoarding without use | Capacity starve | Exclusive hold TTL **5 min**; unused auto-release | Platform |

**AUP posture (Locked):** KYC-gated attested destinations + hard denylist — **not** public-web-only.

---

## 4. Peer / supply fraud

| ID | Case | Impact | Phase 0 control | Primary owner |
|----|------|--------|-----------------|---------------|
| P1 | Datacenter / hosting / VPN ASN as “residential” | False geo product | ASN class + fraud_score; exclude premium/strict + payouts | Platform (score) · Peer (signals) |
| P2 | Geo spoof / GPS fake / tunnel-from-elsewhere | Broken strict promise | Ensemble ≥2 GeoIP + RTT anchors + ASN; re-verify on IP change / daily; pay **verified tier only** | Platform · Peer (RTT probes) |
| P3 | CGNAT / shared IP farm | Attribution blur; ISP bundles | Fraud score ↑; demote always-on/strict eligibility | Platform |
| P4 | Casual laptop claimed as always-on | Strict hard-fails | Explicit always-on confirm; heartbeat honesty; eligibility demotion | Peer · Platform |
| P5 | Payout farming (loopback / self-traffic) | Drain margin | Gateway meters only; deny Peer LAN/loopback dials; stream heuristics | Gateway · Peer policy |
| P6 | Stolen Peer account → cashout | Theft | Stripe Connect identity; payout to verified Connect account only; min **$25** (friction) | Platform · Peer UX |
| P7 | Malicious Peer inspects / modifies bytes | Privacy / integrity | Product: no Peer DPI **required**; customer TLS e2e; Peer splice-only — **cannot fully stop a rooted host** (disclose residual risk) | Architect (accept) · Designer (copy) |

---

## 5. ISP / ToS exposure (product controls — not legal advice)

| Risk | Product control | Owner |
|------|-----------------|-------|
| Residential AUP bans proxy / commercial resale (Comcast-class etc.) | **Mandatory ISP/proxy-ban warn + ack** before enroll; counsel owns wording — **not** blanket liability waive | Peer · Designer · Counsel |
| ISP terminates Peer service | Playbook: delist IP, pause payouts, preserve attribution if abuse case open; no reinstatement promise | Platform · Peer · Support |
| Paid recruitment before counsel review | **Block** paid Peer ads until counsel signs disclosure + channel plan | Spec Keeper / Jeff gate |
| Thin market / pause matching | Consent line: matching may pause when capacity kill switches fire | Designer · Peer |
| LE / abuse complaint hits Peer IP | Metadata attribution (account, time, dest host:port, bytes, geo tier); short retention TBD counsel; Peer does **not** see buyer identity | Platform |

Residual risk to Peer is **real** and must stay visible in consent — do not soft-pedal in UX.

---

## 6. Control-plane / tunnel attacks

| ID | Case | Control | Owner |
|----|------|---------|-------|
| T1 | Steal Peer device key → rogue exit | Per-install keys; OS keychain; rotate on compromise; mTLS peer↔relay | Peer · Platform |
| T2 | MITM Client↔Gateway or Peer↔Relay | TLS everywhere; pinned/signed updates; no cleartext control | Platform · Peer |
| T3 | Forge Stripe / KYC webhooks | Verify signatures; idempotent ledger; replay window | Platform |
| T4 | Capacity DoS (quote spam) | Auth + rate limit `/v1/match`; exclusive hold TTL | Platform |
| T5 | Over-broad support access to KYC/logs | RBAC + audit; minimize PII in ops tools | Platform |
| T6 | Client dials Peer LAN / metadata | Peer **non-bypassable** egress deny (RFC1918, link-local, `169.254.169.254`, admin ports) | Peer (floor) · Gateway (also deny) |

---

## 7. Required controls map (checklist)

### Platform (enforce in control plane / gateway)

- [x] Hard destination denylist + attested-category gate at assign and mid-stream  
- [x] KYC tier enforcement fail-closed (`kyc_insufficient`)  
- [x] Fraud_score pipeline (ASN class, geo flap, CGNAT, VPN) gating strict/premium + payouts  
- [x] Capacity kill switches + codes (`strict_unavailable`, `capacity_*`)  
- [x] Grace events; Peer paid on grace GB; no negative customer balance  
- [x] Ledger idempotent by `stream_id`; Connect payouts to verified account ≥ $25  
- [x] Attribution log metadata-only; retention placeholder until counsel lock  
- [x] Rate limits, webhook auth, admin RBAC  
- [x] Abuse case object + auto-suspend hooks (Client; later reseller rules)

### Peer (enforce on device)

- [x] ISP warn ack before first tunnel; re-ack on material ToS change  
- [x] Local egress floor (LAN/metadata/ports) even if Gateway buggy  
- [x] Bandwidth / concurrency hard caps; kill switch delist + hard-cut  
- [x] Heartbeat honesty (`host_tier`, load); no early cut on Client grace  
- [x] Signed updater; secrets in OS store; splice-only (no payload features)  
- [x] Cashout CTA gated at $25; Connect onboarding only  

### Designer (consent / warning surfaces)

- [x] Client: AUP + attested use-case; denylist plain language; grace warning → hard-stop; strict unavailable; soft-sticky re-rate disclosure  
- [x] Peer: what-we-do / ISP risk / what-we-don’t / residual rooted-host risk one-liner / thin-market pause / kill switch  
- [x] Never ship “Stream” brand; never claim blanket legal waive  

---

## 8. Severity → response (Phase 0 working SLA)

| Severity | Examples | Response |
|----------|----------|----------|
| **P0** | Active malware C2, LE emergency, mass banking hits | Immediate account + related Peer IP freeze; preserve logs; human on-call |
| **P1** | Confirmed AUP breach, stolen-card ring | Suspend Client ≤1 h triage; delist involved Peers if IP burned |
| **P2** | Geo fraud Peer, velocity anomaly | Demote fraud_score; block strict; payout hold |
| **P3** | Single soft complaint | Ticket; watch; no auto-ban |

Exact on-call roster TBD; tooling must exist before public Peer recruitment.

---

## 9. Explicit non-goals / accepted residual risk

- Cannot prevent a **rooted Peer** from observing destinations they dial (disclose).  
- Cannot make Peer’s ISP contract “safe” via ToS (warn + counsel only).  
- Phase 0: no ZK proofs; no full packet capture for T&S (metadata only).  
- Reseller abuse controls designed now, enforced at Phase 4.

---

## 10. Attach hooks (owners fill)

| Section | Owner | Action |
|---------|-------|--------|
| §11 Platform control hooks | Platform Engineer | **Done** — API/gateway/job map attached |
| §12 Peer control hooks | Peer Engineer | **Done** — daemon/tray enforcement map attached |
| §13 Consent surfaces | Stream Designer | **Done** — screen IDs C0–C3 / 1–3 / P0–P7 attached |

**Green for spike planning:** §11–§13 attached; Platform + Peer report no P0 unowned. Production tunnel code still blocked until a thin spike plan exists and P0 tooling (denylist + freeze + Peer egress/ack/kill) is in that spike’s definition of done.

---

## 11. Platform control hooks — Platform Engineer

**Status:** Attached · aligns control-plane API v0.1 + session sequence §9 · 2026-09-28 CT  
**Surface:** Gateway (enforcement) + Control plane APIs/jobs (policy, ledger, capacity, abuse).

| §7 control | Hook (where enforced) | Event / API / job |
|------------|----------------------|-------------------|
| Hard denylist + attested category | Gateway on assign + every new dest mid-stream; fail closed | Dest host:port check vs denylist + account attestation; reject → stream refuse / mid-stream close. Denylist push: `POST /v1/admin/denylist` (versioned), Gateway pull/hot-reload |
| KYC fail-closed | Control on `POST /v1/match` + Gateway selector parse | Over-tier / conflict → `kyc_insufficient` / `selector_conflict` (4xx). Never silent clamp |
| Fraud_score gate | Registry sample + payout eligibility job | Fields: `asn_class`, `geo_flap`, `cgnat`, `vpn_hosting`, `score`. Score over threshold → exclude strict/premium + hold `peer_payout`. Inputs from Peer heartbeats + GeoIP ensemble |
| Capacity kill switches | Control `metro_capacity` + match path | `GET /v1/capacity/{geo}` · match embed `capacity`. Codes: `strict_unavailable`, `capacity_country_paused`, `capacity_oversubscribed`. Admin: `POST/DELETE /v1/admin/capacity/{geo}/kill` |
| Grace + no negative balance | Gateway meter ↔ Control balance | Emit `balance.grace_enter` / `balance.grace_exhausted`; `balance_state` ok→grace→stopped. Ledger: no customer debit on grace GB; `peer_payout` still posts; absorb on margin |
| Ledger + Connect ≥ $25 | Settle flush + cashout job | Idempotent posts by `stream_id` (debit / peer_payout / service_credit / margin). Cashout: Stripe Connect webhook-verified; withdraw only if accrued ≥ $25 and Connect account verified |
| Attribution metadata-only | Gateway → append-only log on open/flush/close | Fields: account, ts, dest host:port, bytes, geo tier, stream_id — **no payload**. Retention placeholder until counsel; export for abuse/LE case |
| Rate limits + webhook auth + RBAC | Edge + payments + admin | `/v1/match` per-account + IP rate limit (T4). Stripe/KYC webhooks: signature verify + replay window + idempotent `payment_id` (T3). Admin RBAC + audit log on KYC/attribution reads (T5) |
| Abuse case + auto-suspend | Control abuse object | `POST /v1/admin/abuse_cases` · `POST /v1/admin/accounts/{id}/freeze` (P0/P1). Auto-suspend hooks on denylist hits velocity, chargeback flags, sanctions. Related Peer IP delist via registry when IP burned |
| Exclusive hold TTL | Match + release | Exclusive `hold_expires_at` = 5 min; unused → auto `release`; `exclusive_hold_expired` |
| Soft sticky re-rate / strict hard-fail | Rematch + settle | City rematch → re-rate remaining bytes at delivered geo. Strict bound miss → `strict_hard_fail` + `service_credit` (unused window @ assignment rate) |
| ISP-termination playbook | Registry + payouts | Delist IP, pause Peer payouts, preserve attribution if abuse open — triggered by support/admin, not Peer self-serve reinstatement |

**P0 unowned:** none from Platform column — Gateway denylist + freeze endpoints are required before public Peer recruitment (tooling gate with Architect §8).

Also sync: control-plane API error table already lists Designer-facing codes for screens 1–3.

## 12. Peer control hooks — Peer Engineer

**Status:** Attached · aligns peer MVP outline v0.1 + session sequence §10 · 2026-09-28 CT  
**Surface:** Tray UI (consent gates) + Peer daemon (always-on policy) + tunnel worker + policy enforcer + updater.

| §7 control | Hook (where enforced) | Enforcement point |
|------------|----------------------|-------------------|
| ISP warn ack before first tunnel; re-ack on material ToS | Tray enroll gate **P1**; daemon refuses `enroll` / first Relay connect until `isp_ack_version` stored | Persist signed ack version locally; on ToS bump from Control → force re-ack before resume share. Decline → no device key issuance |
| Local egress floor (LAN/metadata/ports) | **Policy enforcer** in-process with tunnel worker — non-bypassable by stream assign | Deny: loopback, RFC1918, link-local, cloud metadata IPv4/IPv6, Peer LAN/admin ports. Default allow **80/443** only until Control pushes allowlist hash. Block SMTP/25 + abuse ports. Dual-enforce with Gateway (T6/A1/A5) |
| Bandwidth / concurrency hard caps | Policy enforcer + tray sliders | Defaults: ~20% uplink, hard ceiling ≤50 Mbps; concurrency default 8 / max 32. User may tighten only. Back-pressure to Relay when saturated |
| Kill switch delist + hard-cut | Tray **P4** one-tap → daemon | Stop accepting new streams; hard-cut active tunnels; registry delist notify Control; stay paused until explicit resume |
| Heartbeat honesty (`host_tier`, load) | Daemon heartbeat loop | Emit `host_tier` only after explicit casual/always-on confirm (**P3**); never auto-flip always-on. Load/online truthful. Fraud/geo demotion → tray **limited eligibility** (Designer) |
| No early cut on Client grace | Tunnel worker | Keep splicing until Relay teardown; treat grace hard-stop as normal close. Local byte estimate ≠ ledger; never claw back |
| Signed updater fail-closed | Updater process | Verify update signatures; bad/missing sig → do not apply. Security-critical pending → force pause share until updated |
| Secrets in OS store | Daemon install / enroll | Device key + tokens in OS keychain/encrypted store only; never log auth material |
| Splice-only (no payload features) | Tunnel worker feature freeze | No DPI, no content cache, no dest URL logging beyond Control already knows. Residual rooted-host risk stays Designer **P2** one-liner (P7 accepted) |
| Cashout CTA ≥ $25 · Connect only | Tray **P6** | Show Connect onboarding; disable Withdraw until accrued ≥ $25; no alternate rails Phase 0 |
| Thin-market / capacity pause status | Tray **P4** + Control eligibility hints | Daemon stays healthy; no pool math on device; surface non-alarmist limited/paused matching |
| ISP-termination playbook (device side) | Tray **P7** + daemon | On support/admin delist signal or user report: stop share, clear local “online”, deep-link support; no reinstatement promise |

**P0 unowned:** none from Peer column — egress floor + kill switch + ISP ack gate are MVP slice-1 blockers before any public Peer binary.

Also sync: session sequence §10 Peer hops; Designer §13 P0–P7 screen IDs.

## 13. Consent / warning surfaces — Stream Designer

**Source:** `/workspace/stream-design/wireframes-core-flows-v0.md` (v0.1). Brand chrome = **[Brand]** only — never “Stream.” Counsel owns final legal ISP/ToS text; product owns plain-language framing. **No** blanket liability waive in any screen.

### Client (§7 Designer → screens)

| Threat / control need | Screen ID | What the surface does |
|-----------------------|-----------|------------------------|
| AUP + attested use-case (A2) | **C0** ToS/AUP accept · **C3** use-case category picker | Must accept AUP before use; Phase 0 category attestation (not weeks-long domain allowlist) |
| Hard denylist plain language (A1) | **C3** denylist note under use-case | “Banking, government, and mail destinations are blocked.” |
| Soft-sticky re-rate disclosure (A6) | **C3** City mode helper · **C5** live requested-vs-delivered + re-rate toast | City may metro-jump; remaining bytes re-rate at delivered geo — never silent |
| Strict fail-closed (A6) | **C3** Strict helper · mid-session **strict_hard_fail** screen | No east↔west silent jump; hard-fail + service credit |
| Grace → hard-stop (A7 adjacent) | **Screen 1** (`balance_grace`) · **Screen 2** (`balance_exhausted`) | Countdown MB+s then stop; Add funds CTA; no “free unlimited wind-down” |
| Strict / capacity thin market | **Screen 3** (`strict_unavailable`) · C3 `capacity_*` states | Unavailable-in-area / paused / at capacity — offer City or other city |
| KYC capability honesty | **C1** · C3 `kyc_insufficient` CTA | Over-tier = upgrade path, never silent clamp |

### Peer (§7 Designer → screens)

| Threat / control need | Screen ID | What the surface does |
|-----------------------|-----------|------------------------|
| What this app does | **P0** | Primary-purpose share; paid for verified traffic; you can stop anytime |
| ISP / proxy-ban risk (mandatory) | **P1** | Required checkbox ack before enroll; warn not waive; counsel text slot |
| What we log / don’t (privacy) | **P2** | Metadata-only list; “we don’t read page contents”; caps + **kill switch** |
| Residual rooted-host risk (P7) | **P2** one-liner (v0.1 add) | “A compromised device can still see destinations it dials — keep your OS updated.” |
| Thin-market / matching may pause | **P2** consent line · **P4** status when limited | Matching may pause when capacity kill switches fire; limited-eligibility tray |
| Always-on / eligibility honesty (P4) | **P3** · **P4** tier | Casual vs always-on; Limited = not Strict-eligible |
| Kill switch always reachable | **P4** Pause sharing | One-tap stop; OFF until enrolled |
| Cashout friction (P6) | **P6** | Stripe Connect only; CTA gated at **$25** |
| ISP terminated playbook | **P7** | Delist / pause payouts / support path; no reinstatement promise |

### Coverage check vs §7 Designer checklist

- [x] Client AUP + attested use-case + denylist language  
- [x] Grace warning → hard-stop  
- [x] Strict unavailable + soft-sticky re-rate disclosure  
- [x] Peer what-we-do / ISP risk / what-we-don’t / residual rooted-host / thin-market pause / kill switch  
- [x] No “Stream” brand · no blanket legal waive  

Wireframes updated in lockstep for P2 residual-risk one-liner if missing.

---

*Stream Architect — threat model v0 · **GREEN for spike planning** · 2026-09-28 CT · not legal advice*
