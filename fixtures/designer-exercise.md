# Designer exercise checklist (spike)

**Owner:** Stream Designer  
**Wireframes:** `/workspace/stream-design/wireframes-core-flows-v0.md`  
**Machine map:** `fixtures/screens.json` + `fixtures/codes.json`  
**P1/P4 HARDENING copy:** `docs/P1_P4_COPY.md` (#5 / #7)  
**Rule:** Harness emits real codes; CLI/TUI may print `SCREEN <id>`; mark when observed. Iroh vs fake Relay is invisible to UX.

Brand chrome = **`[Brand]`** only. Assert scripts must **fail** if output contains the substring `Stream` in user-facing lines, or liability-waive phrases (`waive all liability`, `ISP will always allow`, `we are not responsible for ISP`).

---

## A. Client stop screens (required — grace path + fixture)

### Screen 1 — Warning → grace countdown
|| |
|--|--|
| **ID** | `screen_1_grace` |
| **Code / event** | `balance_grace` / `balance.grace_enter` |
| **When** | Balance hit $0; stream still up |
| **Fixture copy (must appear)** | `Balance hit zero` · `Finishing this transfer` · grace shows both time and MB (e.g. `Grace left`) |
| **Must not** | Imply unlimited free wind-down |
| **Harness** | `spike_grace_stop.sh` |

- [ ] Observed against live `balance_grace`

### Screen 2 — Hard-stop + Add funds
|| |
|--|--|
| **ID** | `screen_2_exhausted` |
| **Code / event** | `balance_exhausted` / `balance.grace_exhausted` |
| **When** | ≤5 MB or ≤15 s grace hit; stream closed |
| **Fixture copy** | `Session stopped` · `Prepaid balance empty` · `Add funds` |
| **Must not** | Promise continued traffic without top-up |
| **Harness** | `spike_grace_stop.sh` (after Screen 1) |

- [ ] Observed against live `balance_exhausted`

### Screen 3 — Unavailable in this area
|| |
|--|--|
| **ID** | `screen_3_strict_unavailable` |
| **Code / event** | `strict_unavailable` |
| **When** | Fixture: depth <25 or `strict_kill` before Create |
| **Fixture copy** | `Nearby exits aren’t available here right now` · escape CTAs `Switch to City rematch` / `Try another city` |
| **Harness** | `spike_p0_gates.sh` or control fixture toggle |

- [ ] Observed against live `strict_unavailable`

**Same-family (optional toggles):**
- `capacity_country_paused` → copy contains `Country exits paused`
- `capacity_oversubscribed` → copy contains `at capacity`

---

## B. Consent / P0 surfaces

| Screen | ID | Fixture copy / assert | Harness |
|--------|-----|----------------------|---------|
| **C0** AUP | `c0_aup` | Match refused until `aup_accepted=true`; copy may include `Accept terms` | p0 / match precheck |
| **C3** attest + denylist | `c3_attest_denylist` | On denylist refuse: plain language includes `blocked` (banking/gov/mail) | `spike_p0_gates.sh` denylist |
| **C3** City helper | `c3_city_disclosure` | Checklist: City mode discloses metro jump possible | manual / CLI help text |
| **P0** what-this-is | `p0_what` | First-run before ack | peer enroll CLI |
| **P1** ISP ack (P0 gate) | `p1_isp_ack` | No tunnel without ack. Grep: `does not guarantee your ISP` · `may suspend service` · `I understand and want to continue`. Warn not waive. See `docs/P1_P4_COPY.md` | `spike_p0_gates.sh` / `gate-peer-ack` |
| **P2** consent bundle | `p2_consent` | Caps + kill + `do not read page contents` + thin-market pause + residual compromised-device line | peer enroll |
| **P4** kill | `p4_kill` | Mid-stream kill → teardown; greps: `Sharing paused` · `No traffic through your connection until you turn it back on`. Explicit resume only. See `docs/P1_P4_COPY.md` | `spike_p0_gates.sh` / `gate-peer-kill` |
| **P6** cashout | `p6_cashout` | Mock withdraw blocked if accrued < **$25**; copy `Minimum cashout: $25` | control mock Connect |

Checklist:

- [ ] **C0** AUP gate
- [ ] **C3** denylist refuse copy
- [ ] **P1** no-tunnel without ISP ack (+ HARDENING copy strings)
- [ ] **P2** consent bundle strings present
- [ ] **P4** kill mid-stream (+ HARDENING pause copy)
- [ ] **P6** <$25 cashout blocked
- [ ] No `Stream` in user-facing output · no liability-waive copy

---

## C. Pass criteria for Designer column

Spike Designer DoD is green when:

1. `spike_grace_stop.sh` prints/observes **Screen 1 then Screen 2** in order.  
2. Fixture emits **Screen 3** once.  
3. P0 consent rows **P1**, **P4**, denylist (**C3**), and **P6** <$25 marked.  
4. Brand/waive asserts pass.

CLI may emit lines like: `UX screen_1_grace code=balance_grace` for script greps — see `screens.json`.
