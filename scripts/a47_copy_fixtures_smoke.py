#!/usr/bin/env python3
"""A4.7 copy fixtures check (Stream Designer).

Python only, no sudo, no network. Checks that:
- each Rust Peer copy mirror equals its entry in fixtures/screens.json,
- every required_copy phrase appears in that screen's body_lines,
- no user-facing line contains a forbidden substring or a raw reason code.
Prints A4.7_COPY_FIXTURES_GREEN on success, exits 1 on any miss.
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FX = json.loads((ROOT / "fixtures/screens.json").read_text())
MIRROR_DIR = ROOT / "rust/crates/stream-peer/src"
MIRRORS = ["p2_consent", "p8_offline", "p9_allowlist"]
A4_SCREENS = ["p1_isp_ack", "c2_add_funds_test", "p8_offline", "p9_allowlist"]

by_id = {c["id"]: c for c in FX["consent"]}
forbidden = FX["forbidden_user_facing_substrings"]
fails = []

def user_lines(e):
    out = [e.get("title", ""), e.get("cta", ""), e.get("checkbox", "")]
    out += e.get("body_lines", [])
    out += list((e.get("reason_lines") or {}).values())
    out += list((e.get("error_lines") or {}).values())
    return [l for l in out if l]

for sid in MIRRORS:
    f = MIRROR_DIR / f"{sid}.json"
    if not f.exists():
        fails.append(f"missing mirror {f.name}")
    elif json.loads(f.read_text()) != by_id.get(sid):
        fails.append(f"drift {f.name} != fixtures/screens.json[{sid}]")

for sid in A4_SCREENS + MIRRORS:
    e = by_id.get(sid)
    if not e:
        fails.append(f"missing fixture {sid}")
        continue
    lines = user_lines(e)
    for r in e.get("required_copy", []):
        if not any(r in l for l in lines):
            fails.append(f"{sid}: required '{r}' not in user lines")
    codes = list((e.get("reason_lines") or {}).keys())
    for l in lines:
        for bad in forbidden:
            if bad in l:
                fails.append(f"{sid}: forbidden '{bad}' in '{l}'")
        for c in codes:
            if c in l:
                fails.append(f"{sid}: raw code '{c}' shown to user")

if "connection_lost" not in by_id.get("p8_offline", {}).get("reason_lines", {}):
    fails.append("p8_offline: no connection_lost fallback")
if by_id.get("p1_isp_ack", {}).get("legal_status") != "pending_counsel" and not by_id.get("p1_isp_ack", {}).get("counsel_final"):
    fails.append("p1_isp_ack: legal status must be pending_counsel or counsel_final")

for f in fails:
    print("FAIL", f)
if fails:
    sys.exit(1)
print("A4.7_COPY_FIXTURES_GREEN")
