"""A1.3 mock-topup command helpers for client-cli."""
from __future__ import annotations

# Bound from client-cli.main at import time via bind()
_http = _match_session = _start_tunnel = _wait_events = None
_emit_screen = _load_screens = _forbid_brand = _reset = None
CONTROL = None
json = None


def bind(*, http, match_session, start_tunnel, wait_events, emit_screen, load_screens, forbid_brand, reset, CONTROL_URL, json_mod):
    global _http, _match_session, _start_tunnel, _wait_events
    global _emit_screen, _load_screens, _forbid_brand, _reset, CONTROL, json
    _http = http
    _match_session = match_session
    _start_tunnel = start_tunnel
    _wait_events = wait_events
    _emit_screen = emit_screen
    _load_screens = load_screens
    _forbid_brand = forbid_brand
    _reset = reset
    CONTROL = CONTROL_URL
    json = json_mod


def cmd_mock_topup():
    """A1.3 — quote → match → grace → Add funds mock → reconnect with new session."""
    _reset()
    st, m = _match_session("topup_cli")
    assert st == 200, m
    st, s = _http("POST", CONTROL + "/v1/sessions", {"quote_id": m["quote_id"], "label": "topup_cli"})
    assert st == 200, s
    st, g = _start_tunnel(s, "topup_cli")
    assert st == 200, g

    st, z = _http("POST", CONTROL + "/v1/sessions/topup_cli/force-zero")
    assert st == 200 and z.get("balance_state") == "grace", z
    _emit_screen("screen_1_grace", ["balance_grace"])

    events = _wait_events(
        lambda evs: any(
            e.get("code") == "balance_exhausted" or e.get("event") == "balance.grace_exhausted" for e in evs
        )
    )
    _emit_screen("screen_2_exhausted", ["balance_exhausted"])
    assert any(
        e.get("code") == "balance_exhausted" or e.get("event") == "balance.grace_exhausted" for e in events
    )

    st, top = _http("POST", CONTROL + "/v1/mock/topup", {"account_id": "acct_demo", "amount_usd": 10.0})
    assert st == 200 and top.get("ok") is True, top
    assert top.get("code") == "mock_topup", top
    assert top.get("rail") == "mock_stripe", top
    assert top.get("screen") == "c2_add_funds_mock", top

    screens = _load_screens()
    c2 = next(c for c in screens.get("consent", []) if c.get("id") == "c2_add_funds_mock")
    blob = " ".join(
        [
            json.dumps(top.get("user_copy") or []),
            json.dumps(top.get("body_lines") or []),
            top.get("ux") or "",
            json.dumps(top),
        ]
    )
    for line in c2.get("required_copy", []):
        _forbid_brand(line)
        assert line in blob, (line, blob[:300])
        print(f"  COPY: {line}", flush=True)
    for line in top.get("user_copy") or []:
        _forbid_brand(line)
    for line in top.get("body_lines") or []:
        _forbid_brand(line)
    _forbid_brand(top.get("ux") or "")
    for bad in ("real card charge", "real charge will", "live Stripe"):
        assert bad.lower() not in blob.lower(), bad

    print("UX c2_add_funds_mock code=mock_topup", flush=True)
    assert "UX c2_add_funds_mock code=mock_topup" in (top.get("ux") or "")

    st, acct = _http("GET", CONTROL + "/v1/accounts/acct_demo")
    assert st == 200 and acct.get("balance_usd", 0) > 0, acct

    st, m2 = _match_session("topup_cli_re")
    assert st == 200, m2
    st, s2 = _http("POST", CONTROL + "/v1/sessions", {"quote_id": m2["quote_id"], "label": "topup_cli_re"})
    assert st == 200, s2
    assert s2.get("stream_id") != s.get("stream_id"), (s2, s)
    st, old = _http("GET", CONTROL + "/v1/sessions/topup_cli")
    if st == 200:
        assert (
            old.get("status") in ("stopped", None)
            or old.get("balance_state") == "stopped"
            or old.get("status") == "stopped"
        ), old

    print("OK mock-topup / A1.3 path", flush=True)
    return 0
