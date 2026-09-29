# P1 / P4 stub copy — HARDENING (#5 / #7)

**Owner:** Stream Designer  
**Consumers:** Peer Engineer (CLI / admin emit), assert greps via `fixtures/screens.json`  
**Still stubs only** — counsel owns final legal text on P1; no pixel UI

Machine source of truth: [`fixtures/screens.json`](../fixtures/screens.json) → `consent` ids `p1_isp_ack`, `p4_kill`.

---

## P1 — ISP / carrier ack ([#5](https://github.com/DrCigarette69/stream-spike/issues/5))

**Gate:** `isp_ack_version` non-empty before HELLO / enroll / tunnel.  
**Tone:** warn, **not** waive.

### Stub lines (Peer may print verbatim)

```
Before you enroll

Some home internet plans prohibit running a proxy or sharing your connection this way.
Your ISP or carrier may suspend service if they decide this violates their terms.
[Brand] does not guarantee your ISP will allow this.
(Full legal disclaimer: counsel text.)

☐ I understand and want to continue
[ Back ]   [ Continue → ]   ← Continue disabled until ☐
```

### Assert greps (`required_copy`)

| Must appear |
|-------------|
| `does not guarantee your ISP` |
| `may suspend service` |
| `I understand and want to continue` |

### Fail closed

User-facing output must **not** contain: `Stream`, `waive all liability`, `ISP will always allow`, `we are not responsible for ISP`.

### Spike proof

Clear ack → Peer disconnects / cannot tunnel (`gate-peer-ack` · `POST /peer/ack` with empty clears).

---

## P4 — Kill switch ([#7](https://github.com/DrCigarette69/stream-spike/issues/7))

**Gate:** one-tap / `POST /peer/kill` → delist + hard-cut; stay paused until explicit resume.

### Stub lines

**CTA (while sharing):** `Pause sharing`

**After kill:**

```
Sharing paused
No traffic through your connection until you turn it back on.
[ Resume sharing ]   ← explicit only; no auto-resume
```

### Assert greps (`required_copy`)

| Must appear |
|-------------|
| `Sharing paused` |
| `No traffic through your connection until you turn it back on` |

### Spike proof

`gate-peer-kill` · `/peer/kill` → offline + delist; status string includes `Sharing paused`.

---

## Related

- Wireframes: `/workspace/stream-design/wireframes-core-flows-v0.md` §2.1 P1 · §2.2 P4 · pause snippet §2.3  
- [`DESIGNER_RUNBOOK.md`](DESIGNER_RUNBOOK.md) · [`PEER_RUNBOOK.md`](PEER_RUNBOOK.md)
