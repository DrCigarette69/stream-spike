# A1.3 mock add-funds copy (Designer)

**Owner:** Stream Designer · **With:** Platform Engineer (mock Stripe path)  
**Alpha:** A1.3 — Client quote → match → grace → add-funds mock  
**Stance:** stubs · `[Brand]` · no live Stripe/KYC

Machine greps: [`fixtures/screens.json`](../fixtures/screens.json) → `c2_add_funds_mock`  
Walkthrough: [`ALPHA1_WALKTHROUGH.md`](ALPHA1_WALKTHROUGH.md) §A1.3

---

## Entry

From **Screen 2** (`screen_2_exhausted`) CTA **`Add funds`**, or C2 wallet when balance low.

Screen 2 greps already locked: `Session stopped` · `Prepaid balance empty` · `Add funds`.

---

## Mock checkout (stub lines)

```
Add funds (mock)                              [Brand]

This is a test top-up — no real charge.
Amount  [ $10.00 ▼ ]

[ Cancel ]                    [ Confirm mock top-up → ]
```

## Success

```
Funds added. Prepaid balance updated.
Reconnect or create a new session to keep going.
[ Create session ]     [ View balance ]
```

## Assert greps (`required_copy`)

| Must appear |
|-------------|
| `Add funds` |
| `This is a test top-up` |
| `Funds added` |

## Fail closed

- No user-facing `Stream`
- No `real card charge` / live Stripe promise
- No unlimited free wind-down after top-up

## Platform hook (suggested)

Emit UX lines Platform smoke / `client-cli` can grep, e.g.:

`UX c2_add_funds_mock code=mock_topup`

Mock endpoint may already exist under Control `/v1/mock/...` — Platform owns wiring; Designer owns strings only.

---

## Related

- Wireframes C2 / Screen 2: `/workspace/stream-design/wireframes-core-flows-v0.md`  
- [`P1_P4_COPY.md`](P1_P4_COPY.md) · [`DESIGNER_RUNBOOK.md`](DESIGNER_RUNBOOK.md)
