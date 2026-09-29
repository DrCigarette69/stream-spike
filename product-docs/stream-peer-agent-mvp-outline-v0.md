# Stream — Peer Agent MVP Outline (v0.1)

**Owner:** Peer Engineer  
**Audience:** Stream Dev + Jeff · Spec Keeper  
**Date:** 2026-09-28 (CT)  
**Status:** Design outline only — no production code  
**Scope:** Phase 0 primary-purpose Peer client (desktop / Docker). **Not** mobile / silent SDK (Phase 0 out of scope).

Aligned to: eng handoff + architecture sketch v0.1 + control-plane API v0.1 + Stream Dev locks (grace, pool cap, Stripe Connect $25).

---

## 1. What MVP is

A primary-purpose install that turns a consented home/office machine into an always-reachable residential exit: enroll → verify geo → open relay tunnel → share bandwidth under hard caps → cash payout on **gateway-verified** GB.

**In:** Win / macOS / Linux native + Docker; explicit consent; ISP warn ack; share/bandwidth UI; tunnel + local egress policy; kill switch; casual vs always-on signal; auto-update; minimal telemetry.

**Out (Phase 0):** Mobile share, Play/App Store SDKs, silent enrollment in unrelated apps, payload inspection, street-address storage, crypto Peer payout, public brand “Stream” in UI (placeholder brand only).


---

## 1a. Stream Dev locks folded in (v0.1)

| Lock | Peer-agent implication |
|------|------------------------|
| Zero-balance Client grace ≤5 MB / ≤15 s then hard-stop | Peer keeps splicing until relay tears down; **Peer still paid** on gateway-verified grace bytes (platform absorbs). No Peer-side “cut early to save buyer.” |
| Country pool hard cap + kill switches | Peer does **not** enforce pool math. Heartbeat must expose accurate `host_tier` (casual | always_on), load, online so Platform capacity object stays honest. Tray may show “network busy / limited matching” if control returns eligibility hints — copy with Designer. |
| Strict only if ≥25 always-on in-bound; kill if N<25 or <80% assign/7d | Always-on confirm remains explicit. When demoted/ineligible for strict inventory, show **limited eligibility** (Designer owns chrome). |
| Peer cashout = Stripe Connect (or equiv); **min $25** | Payout glance + cashout CTA: Connect onboarding link; gate withdraw button until balance ≥ $25; never invent alternate rails in Phase 0. |
| Consent + ISP warn | Unchanged mandatory ack; add one-line that capacity/matching may pause in thin markets (no guarantee of continuous assigns). |


---

## 2. Processes (runtime shape)

Treat these as logical processes (one binary may host several):

| Process | Role |
|---------|------|
| **UI / tray** | Consent screens, share controls, status, payout glance, quit / pause |
| **Peer daemon** | Persistent; survives UI close if user opted always-on; owns tunnel lifecycle |
| **Tunnel worker** | Mutual-TLS to relay; multiplex customer streams; DNS on Peer; byte splice only |
| **Policy enforcer** | Local egress denylist + port allowlist + LAN/metadata blocks; concurrency + bandwidth caps |
| **Telemetry / heartbeat** | Health, load, host_tier signal, last_seen; **no** payload content |
| **Updater** | Signed updates only; fail closed if signature bad |
| **Kill switch** | One control: stop accepting new streams + tear down active tunnels + notify registry |

**Enrollment flow (happy path)**  
1. Install → placeholder brand ToS + AUP + **ISP/proxy-ban warning** (must ack) → account link / device key.  
2. Daemon starts → mutual auth with control/relay → geo verify (ensemble driven from control plane; Peer supplies RTT probes as asked).  
3. User sets share mode (paused / casual / always-on), bandwidth & concurrency caps.  
4. Registry marks peer online; eligible for matching once geo_verified + fraud_score OK.  
5. On assign: relay opens stream → Peer dials `host:port` → meters locally for UX only; **ledger truth = gateway**.

**Failure paths MVP must handle**  
- ISP warn declined → do not enroll.  
- Geo flap / CGNAT / VPN / datacenter ASN signals → demote or exclude from premium/strict (control plane); Peer shows “limited eligibility”.  
- Control-plane capacity pause / oversubscribe / strict kill → keep daemon healthy; stop expecting assigns; surface non-alarmist status (Designer).  
- Client grace→hard-stop on a stream → relay closes; Peer treats as normal teardown; do not claw back local byte estimate.  
- Kill switch / pause → immediate delist from new assigns; hard-cut active streams; notify registry.  
- Update pending critical → optional soft nag; security-critical → force pause until updated (working assumption).

---

## 3. Hard safety limits (ship with defaults; user may tighten, not loosen past floor)

### 3.1 Network egress (local, non-bypassable by customer traffic)

- Deny: loopback, RFC1918, link-local, cloud metadata (`169.254.169.254` and IPv6 equivalents), Peer’s own LAN/gateway admin ports.  
- Default port allowlist: **80 / 443** only until control plane pushes approved extras; block SMTP/25 and known abuse ports.  
- No raw sockets / no peer-initiated arbitrary listen for customer use beyond tunnel.  
- Destination hard denylist (banking, gov, mail, abuse) enforced **at gateway**; Peer still applies local floor so a buggy assign can’t punch LAN.

### 3.2 Bandwidth & device

| Limit | MVP default (WORKING — tune with telemetry) | Notes |
|-------|-----------------------------------------------|-------|
| Upload share cap | 20% of measured uplink, hard ceiling **≤50 Mbps** | User slider 5–50%; cannot exceed ceiling |
| Download share cap | Match upload or separate; default = upload | Customer traffic is bidirectional through Peer |
| Concurrent streams | **8** default, hard max **32** | Back-pressure to gateway when saturated |
| CPU / nice | Cap worker CPU ~25% when interactive session detected | Always-on schedule can raise when idle |
| Disk | Telemetry + logs only; rotate; no content cache of customer bytes | |
| Schedule | Optional quiet hours (no new streams) | Always-on eligibility needs awake window length (Architect/Platform) |

### 3.3 Identity & secrets

- Per-install device key; never log auth material.  
- Credentials / payout tokens only in OS keychain / encrypted store.  
- Auto-update signatures required.

### 3.4 Privacy floor (product lock)

- **No** payload inspection / DPI.  
- **No** household street address stored for product geo.  
- Telemetry = metadata: peer_id, health, load, host_tier, bytes (local estimate), errors — not destination URLs beyond what control already knows for the stream.

---

## 4. Consent UX hooks (must exist before first tunnel)

Screens / moments (placeholder brand strings — never ship “Stream” to customers):

1. **What this app does** — You share unused residential bandwidth for pay; traffic exits via your IP; you can pause anytime.  
2. **ISP / carrier ToS risk** — Explicit warning that some residential AUPs ban proxy/server use; Peer must acknowledge before enroll. Counsel owns final copy.  
3. **What we don’t do** — We don’t read your files or customer page content; we don’t store your street address for matching.  
4. **Share controls** — Pause / casual / always-on; bandwidth %; concurrent streams; quiet hours. Defaults conservative.  
5. **Payout glance** — Verified GB estimate + **final pay = gateway-verified**; Stripe Connect cashout; **Withdraw** disabled until ≥ **$25**; show pending vs available.  
6. **Kill switch** — Always one tap from tray; confirm destructive only if streams active.  
7. **Uninstall / revoke** — Stops daemon, deletes local keys option, requests control-plane delist.

**Always-on nudge (eligibility, not dark pattern)**  
Explain that longer sticky / `rematch-strict` inventory prefers always-on; never auto-flip tier without confirm.

---

## 5. ISP / ToS risks the agent must surface

| Risk | Product behavior |
|------|------------------|
| Residential AUP bans proxy/commercial resale (e.g. Comcast-class) | **Warn at install**; mandatory ack; counsel text — **not** “we waive all liability” |
| ISP terminates service | In-app + support playbook: delist IP, pause payouts, preserve attribution if abuse needs it; no promise of reinstatement |
| CGNAT / business class / hosting ASN | Show eligibility impact; don’t promise premium/strict payout |
| ToS or disclosure update | Re-ack before continuing share if material change |
| Recruitment | No paid Peer ads until counsel signs off disclosure + channel plan |

Peer Engineer owns **when/where** these appear in the app. Counsel owns **wording**. Spec Keeper owns living-doc locks (§3.10 outline / decision log).

---

## 6. Interfaces to Platform / Architect (MVP contracts)

Peer client does **not** own control-plane APIs. Needs from Platform/Architect:

- Enroll + device cert issuance  
- Heartbeat / registry fields: `host_tier`, load, geo_verified_at, fraud_score feedback  
- Relay endpoints + stream assign/teardown  
- Push of port allowlist / denylist hashes  
- Payout summary read model (verified GB, pending cashout)  
- Delist + kill acknowledgment

---

## 7. Suggested build slices

1. Daemon + tray + pause/kill + local egress floor (no money yet)  
2. Consent + ISP ack + enroll/device key  
3. Relay tunnel + concurrency/bandwidth caps  
4. Always-on / casual signal + heartbeat  
5. Signed updater + re-ack on material ToS  
6. Payout glance (read-only)

---

## 8. Open for eng (not Jeff product questions)

- Exact default Mbps / % / concurrency (start with table in §3.2; refine with telemetry)  
- Interactive-session detection signals  
- Critical-update force-pause policy  
- Docker networking caveats (host network vs userspace tunnel)  
- macOS/Windows notarization + tray permissions

---

*Peer Engineer — MVP outline v0.1 · 2026-09-28 CT · Stream Dev locks (grace / pool / Connect $25)*
