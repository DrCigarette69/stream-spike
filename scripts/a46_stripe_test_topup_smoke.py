#!/usr/bin/env python3
"""A4.6 Stripe test-mode top-up smoke -> A4.6_STRIPE_TEST_TOPUP_GREEN.

Control (control/main.py) on free 127.0.0.1 ports, one process per lane.
  (0) key guard: STRIPE_TEST_SECRET_KEY=sk_live_… -> Control refuses to start (prefix only in the
      log); key unset -> POST /v1/topup/test 409 stripe_test_unavailable, mock top-up unchanged.
  (a) locally signed events lane, no network: a loopback fake of the two Stripe API calls
      (STRIPE_API_BASE=http://127.0.0.1:<port>, fake sk_test_ key) + a locally generated whsec.
      pending (balance unchanged) -> bad / missing / expired signature refused -> amount mismatch
      refused -> currency mismatch refused -> livemode refused -> valid payment_intent.succeeded credits
      once (UX c2_add_funds_test lines) -> replay idempotent -> second event for same intent no credit;
      payment_failed -> failed copy, no credit.
  (b) only if STRIPE_TEST_SECRET_KEY (sk_test_) is set and api.stripe.com answers: a real test-mode
      PaymentIntent confirmed server-side with pm_card_visa; balance unchanged until the succeeded
      event for that real intent is delivered via the local signed lane (Stripe -> box delivery needs
      Jeff's `stripe listen` login; out of scope). Prints A46_LANE_B=ran|skipped:<why>.
The key value is never printed: all output is scrubbed and checked for it.
Usage: python3 scripts/a46_stripe_test_topup_smoke.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_KEY = os.environ.get("STRIPE_TEST_SECRET_KEY", "").strip()
FAKE_KEY = "sk_test_a46_local_lane_" + secrets.token_hex(8)
SECRETS = [k for k in (REAL_KEY, FAKE_KEY) if k]
COPY = ["Add funds", "Test mode", "no real charge", "Funds added"]


def scrub(s: str) -> str:
    for k in SECRETS:
        s = s.replace(k, k[:8] + "…REDACTED")
    return s


def say(m):
    print(scrub(str(m)), flush=True)


def fail(m):
    say(f"FAIL a46: {m}")
    raise SystemExit(1)


def ok(m):
    say(f"OK {m}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, url, body=None, headers=None, raw=None):
    d = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, data=d, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        x = urllib.request.urlopen(r, timeout=30)
        return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class Control:
    def __init__(self, env, expect_start=True):
        self.tmp = tempfile.mkdtemp(prefix="a46_ctl_")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        e = {k: v for k, v in os.environ.items() if not k.startswith(("SPIKE_", "STRIPE_"))}
        e.update({"SPIKE_DB": f"{self.tmp}/c.sqlite", "SPIKE_LISTEN": f"127.0.0.1:{self.port}",
                  "FIXTURES": str(ROOT / "fixtures"), "PYTHONUNBUFFERED": "1", **env})
        self.out = Path(self.tmp) / "out.log"
        self.p = subprocess.Popen([sys.executable, str(ROOT / "control" / "main.py")], env=e,
                                  stdout=open(self.out, "w"), stderr=subprocess.STDOUT)
        if not expect_start:
            self.p.wait(timeout=15)
            return
        for _ in range(100):
            try:
                if http("GET", self.url + "/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        fail(f"control did not start: {self.log()}")

    def log(self):
        return self.out.read_text() if self.out.exists() else ""

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            self.p.wait(timeout=5)
        log = self.log()
        for k in SECRETS:
            if k in log:
                fail("Control output contains the Stripe key")
        subprocess.run(["rm", "-rf", self.tmp])
        return log


def sign(payload: bytes, secret: str, t=None):
    t = int(time.time()) if t is None else t
    v1 = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def event(eid, etype, intent, amount, currency="usd", livemode=False, status="succeeded"):
    return json.dumps({"id": eid, "object": "event", "type": etype, "livemode": livemode, "api_version": "2024-06-20",
                       "created": int(time.time()), "data": {"object": {
                           "id": intent, "object": "payment_intent", "amount": amount, "amount_received": amount if
                           status == "succeeded" else 0, "currency": currency, "livemode": livemode, "status": status,
                           "metadata": {"account_id": "acct_demo"}}}}).encode()


def deliver(c, payload, secret, sig=None):
    return http("POST", c.url + "/v1/stripe/webhook", raw=payload,
                headers={"Stripe-Signature": sig if sig is not None else sign(payload, secret)})


class FakeStripe(BaseHTTPRequestHandler):
    """Loopback stand-in for POST /v1/payment_intents only (lane a)."""
    seen_auth = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = dict(x.split("=", 1) for x in self.rfile.read(n).decode().split("&") if "=" in x)
        FakeStripe.seen_auth.append(self.headers.get("Authorization", "") == "Bearer " + FAKE_KEY)
        if self.path != "/v1/payment_intents":
            self.send_response(404)
            self.end_headers()
            return
        pid = "pi_a46local" + secrets.token_hex(6)
        b = json.dumps({"id": pid, "object": "payment_intent", "amount": int(form["amount"]), "currency": form["currency"],
                        "livemode": False, "status": "requires_payment_method",
                        "client_secret": pid + "_secret_local"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def lane0():
    c = Control({"STRIPE_TEST_SECRET_KEY": "sk_live_" + "x" * 24}, expect_start=False)
    log = c.stop()
    if c.p.returncode != 2 or "a4_refuse_stripe_live_key_refused:sk_live_" not in log or "x" * 24 in log:
        fail(f"live key: rc={c.p.returncode} log={log!r}")
    ok(f"STRIPE_TEST_SECRET_KEY=sk_live_… -> Control refused to start rc=2: {log.strip().splitlines()[-1]}")
    c = Control({"STRIPE_TEST_SECRET_KEY": "pk_test_" + "y" * 24}, expect_start=False)
    log = c.stop()
    if c.p.returncode != 2 or "stripe_live_key_refused" not in log:
        fail(f"publishable key: rc={c.p.returncode} {log!r}")
    ok("publishable pk_test_… key -> refused too (only sk_test_/rk_test_ secret keys)")
    c = Control({})
    try:
        st, r = http("POST", c.url + "/v1/topup/test", {"amount_usd": 10})
        if st != 409 or r.get("error") != "stripe_test_unavailable" or r.get("screen") != "c2_add_funds_mock":
            fail(f"keyless /v1/topup/test {st} {r}")
        st2, m = http("POST", c.url + "/v1/mock/topup", {"amount_usd": 10})
        if st2 != 200 or m.get("screen") != "c2_add_funds_mock" or m.get("balance_usd") != 15.0:
            fail(f"mock topup {st} {m}")
        ok(f"key unset -> /v1/topup/test {st} stripe_test_unavailable (fallback mock); /v1/mock/topup still "
           f"{m['screen']} balance 5.00 -> {m['balance_usd']:.2f}")
    finally:
        c.stop()


def lane_a():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeStripe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    whsec = "whsec_a46local" + secrets.token_hex(16)
    SECRETS.append(whsec)
    c = Control({"STRIPE_TEST_SECRET_KEY": FAKE_KEY, "STRIPE_API_BASE": f"http://127.0.0.1:{srv.server_port}",
                 "STRIPE_WEBHOOK_SECRET": whsec})
    try:
        st, r = http("POST", c.url + "/v1/topup/test", {"account_id": "acct_demo", "amount_usd": 10})
        if st != 200 or r.get("status") != "pending" or r.get("balance_usd") != 5.0 or not all(FakeStripe.seen_auth):
            fail(f"pending {st} {r}")
        if "Funds added" in r["user_copy"] or r["screen"] != "c2_add_funds_test":
            fail(f"pending copy {r}")
        pi = r["intent_id"]
        ok(f"POST /v1/topup/test -> {r['status']} {pi} {r['ux']}; balance still {r['balance_usd']:.2f}; "
           f"copy {r['body_lines']}")

        def bal():
            return http("GET", f"{c.url}/v1/topup/test/{pi}")[1]

        good = event("evt_a46_good", "payment_intent.succeeded", pi, 1000)
        for label, payload, sig, want in (
            ("bad signature", good, sign(good, "whsec_wrong"), "signature_invalid"),
            ("missing signature", good, "", "signature_missing"),
            ("expired signature", good, sign(good, whsec, int(time.time()) - 3600), "signature_expired"),
            ("tampered body", good.replace(b"1000", b"9000"), sign(good, whsec), "signature_invalid"),
        ):
            st, x = deliver(c, payload, whsec, sig)
            if st != 400 or x.get("error") != want:
                fail(f"{label}: {st} {x}")
            ok(f"{label} -> {st} {want}; balance {bal()['balance_usd']:.2f}")
        for label, payload, want in (
            ("amount mismatch", event("evt_a46_amt", "payment_intent.succeeded", pi, 999), "amount_mismatch"),
            ("currency mismatch", event("evt_a46_cur", "payment_intent.succeeded", pi, 1000, "eur"), "amount_mismatch"),
            ("livemode event", event("evt_a46_live", "payment_intent.succeeded", pi, 1000, livemode=True), "livemode_refused"),
            ("unknown intent", event("evt_a46_unk", "payment_intent.succeeded", "pi_nope", 1000), "unknown_intent"),
        ):
            st, x = deliver(c, payload, whsec)
            got = x.get("error") or x.get("result")
            if st != 400 or got != want:
                fail(f"{label}: {st} {x}")
            ok(f"{label} (validly signed) -> {st} {want}; balance {bal()['balance_usd']:.2f}")
        if bal()["balance_usd"] != 5.0 or bal()["status"] != "pending":
            fail(f"credited early: {bal()}")
        st, x = deliver(c, good, whsec)
        if st != 200 or x.get("result") != "credited" or x["topup"]["balance_usd"] != 15.0:
            fail(f"good event {st} {x}")
        ok(f"payment_intent.succeeded (signed, amount/currency match) -> credited; balance 5.00 -> "
           f"{x['topup']['balance_usd']:.2f}; {x['topup']['ux']}")
        st, x = deliver(c, good, whsec)
        st2, x2 = deliver(c, event("evt_a46_again", "payment_intent.succeeded", pi, 1000), whsec)
        if not x.get("duplicate") or x.get("result") != "credited" or x2.get("result") != "not_pending:succeeded" \
                or bal()["balance_usd"] != 15.0:
            fail(f"replay {x} {x2} {bal()}")
        ok(f"replay same event id -> {st} duplicate (result {x['result']}); new event id for the same intent -> "
           f"{x2['result']}; balance stays {bal()['balance_usd']:.2f} (credited once)")
        st, r2 = http("POST", c.url + "/v1/topup/test", {"account_id": "acct_demo", "amount_usd": 7})
        st, x = deliver(c, event("evt_a46_fail", "payment_intent.payment_failed", r2["intent_id"], 700, status="requires_payment_method"), whsec)
        if x.get("result") != "failed" or x["topup"].get("error_line") != \
                "That test payment didn't go through. Your balance hasn't changed." or bal()["balance_usd"] != 15.0:
            fail(f"payment_failed {x}")
        ok(f"payment_intent.payment_failed -> failed, no credit: \"{x['topup']['error_line']}\"")
        time.sleep(0.3)
        log = c.log()
        ux = [l for l in log.splitlines() if l.startswith("UX ")]
        missing = [g for g in COPY if not any(g in l for l in ux)]
        if missing:
            fail(f"UX lines missing {missing}: {ux}")
        say("UX lines (Control stdout):")
        for l in ux:
            say("  " + l)
        ok(f"c2_add_funds_test greps present: {COPY}")
    finally:
        c.stop()
        srv.shutdown()


def lane_b():
    if not REAL_KEY:
        return "skipped:STRIPE_TEST_SECRET_KEY unset"
    if not REAL_KEY.startswith("sk_test_"):
        return "skipped:key is not sk_test_ (refused)"
    try:
        urllib.request.urlopen("https://api.stripe.com/v1/payment_intents", timeout=8)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            return f"skipped:api.stripe.com answered {e.code}"
    except OSError as e:
        return f"skipped:api.stripe.com unreachable ({type(e).__name__})"
    whsec = "whsec_a46local" + secrets.token_hex(16)
    SECRETS.append(whsec)
    c = Control({"STRIPE_TEST_SECRET_KEY": REAL_KEY, "STRIPE_WEBHOOK_SECRET": whsec})
    try:
        if "control stripe: test mode" not in c.log():
            fail(f"startup line: {c.log()}")
        st, r = http("POST", c.url + "/v1/topup/test", {"account_id": "acct_demo", "amount_usd": 5, "confirm_test_card": True})
        if st != 200 or not r.get("intent_id", "").startswith("pi_") or r.get("livemode") is not False:
            fail(f"real test PaymentIntent {st} {json.dumps({k: v for k, v in r.items() if k != 'client_secret'})}")
        pi = r["intent_id"]
        if r.get("stripe_status") != "succeeded" or r["status"] != "pending" or r["balance_usd"] != 5.0:
            fail(f"after confirm: stripe_status={r.get('stripe_status')} status={r['status']} balance={r['balance_usd']}")
        ok(f"real Stripe test mode: PaymentIntent {pi} confirmed server-side with pm_card_visa -> Stripe status "
           f"{r['stripe_status']}; Control status {r['status']}, balance still {r['balance_usd']:.2f} (no webhook yet)")
        st, x = deliver(c, event(f"evt_a46_real_{secrets.token_hex(4)}", "payment_intent.succeeded", pi, 500), whsec)
        if st != 200 or x.get("result") != "credited" or x["topup"]["balance_usd"] != 10.0:
            fail(f"real intent webhook {st} {x}")
        ok(f"succeeded event for the real intent via the local signed lane -> credited; balance 5.00 -> "
           f"{x['topup']['balance_usd']:.2f}")
    finally:
        c.stop()
    return "ran"


def main():
    lane0()
    lane_a()
    b = lane_b()
    say(f"A46_LANE_B={b}")
    print("\nA4.6_STRIPE_TEST_TOPUP_GREEN", flush=True)


if __name__ == "__main__":
    main()
