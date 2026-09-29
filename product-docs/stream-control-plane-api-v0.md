# Stream — Control-plane API sketch (v0.1)

**Audience:** Stream Dev + Jeff · **Author:** Platform Engineer · **Updated:** 2026-09-28 (CT)  
**Status:** Design sketch only — no production code. Aligns with architecture sketch v0.1 + eng handoff.  
**Codename:** Stream (internal). Public brand TBD.

Gateway owns live proxy auth + byte metering. These APIs are control-plane quote / assign / lifecycle + capacity signals.

---

## Matching

### `POST /v1/match`
Request: `account_id`, `geo` (country | region | city_id + optional asn), `dur_tier`, `rematch_mode` (`city`|`strict`|`hard`, default `city`), `exclusive?`, `session_label?`

Response: `quote_id`, `peer_id` (opaque), `assigned_geo`, `price_mult`, `price_per_gb`, `hold_expires_at` (5 min if exclusive), `capacity` (see below)

Rejects (4xx, fail-closed): KYC under-tier, country+strict, balance too low for open, pool oversubscribed, kill on.

### `POST /v1/match/{quote_id}/release`
Drop unused exclusive hold.

---

## Sessions

### `POST /v1/sessions`
Start from `quote_id` (or inline match if non-exclusive).

→ `session_id`, `label`, `stream_id`, `peer_id`, `rematch_mode`, `assigned_price_mult`, `expires_at`, `status`, `grace` policy echo

### `GET /v1/sessions/{label}`
`status`, assigned vs delivered geo, rematch_mode, bytes so far, `expires_at`, `balance_state` (`ok`|`grace`|`stopped`)

### `POST /v1/sessions/{label}/extend`
`{dur_tier}` → new `expires_at` + add'l mult for added time

### `DELETE /v1/sessions/{label}`
End early (assignment mult not refunded by default; strict hard-fail / exclusive peer-loss → service-credit path)

### `POST /v1/sessions/{label}/rematch`
On peer loss / control-plane trigger; same rematch bounds as product lock

---

## Grace hooks (Locked · Stream Dev + Architect defaults)

Echoed on session create / GET; gateway enforces mid-stream.

| Field | Value |
|-------|--------|
| `grace.max_bytes` | **5_242_880** (5 MB) |
| `grace.max_ms` | **15_000** (15 s) |
| `grace.stop_rule` | whichever first after prepaid balance hits $0 |
| `grace.peer_paid` | **true** — Peer credited on gateway-verified grace bytes; platform absorbs (no negative customer balance Phase 0) |

**Gateway events → control plane (and Client UX):**
1. `balance.grace_enter` — balance hit $0; grace window open  
2. `balance.grace_exhausted` — MB or time hit → hard-stop stream  
3. Session `balance_state`: `ok` → `grace` → `stopped`

Ledger: no customer debit for grace bytes; Peer payout still posts on verified grace GB; margin line may show absorb.

---

## Kill-switch / capacity hooks (Locked · Stream Dev + Architect defaults)

### Response shape (on match + capacity reads)
```
capacity: {
  country_online,
  country_concurrent,
  country_cap,          // floor(online × 0.5)
  country_seed_met,     // online ≥ 300
  country_sell_paused,  // online < 150 → reject rotating sell
  strict_eligible_n,    // always-on in-bound in rematch bound
  strict_min_n,         // 25
  strict_assign_success_7d,
  strict_kill           // true if success < 80% / 7d OR N < 25
}
```

### Thresholds

| Knob | Default |
|------|---------|
| Country seed (wide `rematch-city`) | ≥ **300** online / country (US, CA) |
| Hard concurrent cap | concurrent ≤ **online × 0.5** |
| Sell `rematch-strict` | always-on in-bound **N ≥ 25** |
| Kill strict (metro) | assign success **< 80% / 7d** OR **N < 25** |
| Pause country rotating | online **< 150** |

### `GET /v1/capacity/{geo_node}`
Ops / Client “unavailable” checks without allocating a quote. Same `capacity` object.

### Admin (internal)
`POST /v1/admin/capacity/{geo_node}/kill` — force `strict_kill` or country pause with `until`  
`DELETE /v1/admin/capacity/{geo_node}/kill` — clear manual kill

---

## Error codes (for Client UX mapping)

| Code | HTTP | Client screen / meaning |
|------|------|-------------------------|
| `balance_grace` | 200 + event | Warning → grace countdown (Designer screen 1) |
| `balance_exhausted` | 402 / stream close | Hard-stop after grace (Designer screen 2) |
| `capacity_country_paused` | 503 | Country rotating unavailable (online < 150) |
| `capacity_oversubscribed` | 503 | Concurrent > 0.5× online |
| `strict_unavailable` | 503 | Depth < 25 or kill on — “unavailable in this area” (Designer screen 3) |
| `strict_hard_fail` | 409 | Mid-session rematch bound miss → service credit |
| `kyc_insufficient` | 403 | Over-tier selector |
| `selector_conflict` | 400 | Fail-closed parse / country+strict |
| `exclusive_hold_expired` | 410 | Quote hold past 5 min |

---

## Payments assumptions (Phase 0)

- Customer top-up: fiat (Stripe Checkout/Elements natural pair — confirm with counsel)  
- Peer cashout: **Stripe Connect (or equiv)**; min **$25**  
- Ledger still posts `peer_payout` on verified GB before cashout; cashout job drains accrued ≥ $25  

---

## Ledger note (unchanged kinds)

Kinds: `debit` | `peer_payout` | `service_credit` | `margin` — idempotent by `stream_id`.  
Grace byte absorb may appear as margin/COGS, not negative customer balance.

---

*Platform Engineer — control-plane API v0.1 · 2026-09-28 CT*
