# Alpha-1 walkthrough checklist (Designer)

**Owner:** Stream Designer  
**When:** After Alpha-0 closed (#3–#9 green + `SPIKE_DOD_GREEN`)  
**Stance:** stubs · `[Brand]` only · no public egress · mock top-up only · Iroh loopback optional (A1.1)

Machine copy source: [`fixtures/screens.json`](../fixtures/screens.json) · P1/P4 prose: [`P1_P4_COPY.md`](P1_P4_COPY.md)  
Alpha cut: [`ALPHA.md`](ALPHA.md)

---

## A0 confirmation (baseline — already locked)

Before Alpha-1 work, confirm demo/`client-cli` still emit fixture greps:

| Surface | `id` / code | Must appear |
|---------|-------------|-------------|
| Screen 1 | `screen_1_grace` / `balance_grace` | `Balance hit zero` · `Finishing this transfer` · `Grace left` |
| Screen 2 | `screen_2_exhausted` / `balance_exhausted` | `Session stopped` · `Prepaid balance empty` · `Add funds` |
| Screen 3 | `screen_3_strict_unavailable` | `Nearby exits aren’t available here right now` · City escape CTAs |
| P1 | `p1_isp_ack` | `does not guarantee your ISP` · `may suspend service` · `I understand and want to continue` |
| P4 | `p4_kill` | `Sharing paused` · `No traffic through your connection until you turn it back on` |

Fail closed: no user-facing `Stream` · no `waive all liability` / `ISP will always allow`.

**2026-09-29 CT:** Confirmed against `client-cli` (loads `screens.json` → `emit_screen` / P1–P4 gate greps) + Peer HARDENING smoke contract. No fixture shift for A0.

Proof path: `python3 scripts/run_local_asserts.py all` · `./scripts/demo_alpha.sh`

---

## A1.2 — Peer tray / CLI (enroll → ack → kill → resume)

Walk with Peer Engineer. Check each step in order:

1. **P0 what-this-is** — Share bandwidth. Get paid. / control + stop anytime (no tunnel yet).
2. **P1 ISP ack** — verbatim stub in `P1_P4_COPY.md`; Continue disabled until checkbox; `understood:false` → refuse (`p1_ack_gate`).
3. **P2 consent bundle** — caps + kill visible; greps: `do not read page contents` · `Matching may pause` · `compromised device`.
4. **Enroll / ready** — tray shows Sharing ON only after ack + enroll; brand `[Brand]`.
5. **P4 kill** — CTA `Pause sharing` → status `Sharing paused` + detail line; delist + hard-cut.
6. **Resume** — explicit only (`Resume sharing` / `POST /peer/resume`); no auto-resume.
7. **P6 (optional in A1)** — cashout CTA disabled under $25; `Minimum cashout: $25`.

Mark:

- [ ] P1 gate blocks tunnel without ack
- [ ] P4 kill mid-stream shows pause copy
- [ ] Resume explicit
- [ ] Brand/waive asserts pass

---

## A1.3 — Client quote → match → grace → mock add-funds

Walk with Platform Engineer:

1. **C0 AUP** — match refused until accepted.
2. **C3 session builder** — City vs Strict honesty; Strict unavailable → Screen 3 copy + escape CTAs (no silent City fallback).
3. **Quote / match** — prepaid USD language only (no barter credits).
4. **Live → Screen 1** — balance $0 → grace countdown (MB + s).
5. **Screen 2** — hard-stop + `Add funds`.
6. **Mock top-up** — Stripe mock only; after funds, reconnect path clear (no promise of free wind-down).

Mark:

- [ ] Screen 1 then 2 on grace path
- [ ] Screen 3 on strict fixture
- [ ] Mock add-funds CTA copy matches Screen 2
- [ ] No `Stream` in Client UX

---

## A1.1 — Iroh loopback (optional, after A0)

UX-invisible if Relay still fake: same screen IDs / copy. Confirm no new error strings invent brand or waive liability. Designer reviews only if Peer/Platform add new user-facing lines.

---

## A1.4 — Acceptance script (record or live)

One pass (~5–8 min):

1. `./scripts/demo_alpha.sh` → green (A0).
2. Peer enroll → P1 ack → share → kill → resume (A1.2).
3. Client match → force-zero → Screens 1–2 → mock add-funds (A1.3).
4. Toggle `strict_unavailable` → Screen 3.
5. Spot-check forbidden substrings absent.

Ship note: still stubs · counsel owns final P1 legal · Iroh parked until A0 closed per room.

---

## Related

- Wireframes: `/workspace/stream-design/wireframes-core-flows-v0.md`
- [`DESIGNER_RUNBOOK.md`](DESIGNER_RUNBOOK.md) · [`designer-exercise.md`](../fixtures/designer-exercise.md)
