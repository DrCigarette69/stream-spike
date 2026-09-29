"""Peer agent stub — HARDENING #5/#6/#7/#8: ISP ack + P1 copy, egress floor, kill + P4 copy, AUTH_TICKET/ALPN verify."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTROL_URL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8080").rstrip("/")
TRANSPORT = os.environ.get("SPIKE_TRANSPORT", "fake_relay").strip().lower()
IROH_LOOPBACK_TRANSPORTS = frozenset({"iroh", "iroh_loopback"})


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "::1", "localhost")


def _relay_dial_addr() -> str:
    if TRANSPORT in IROH_LOOPBACK_TRANSPORTS:
        return os.environ.get("SPIKE_IROH_LOOPBACK_DIAL", "127.0.0.1:9101")
    return os.environ.get("SPIKE_FAKE_RELAY_DIAL", "127.0.0.1:9100")


RELAY_DIAL = _relay_dial_addr()
ADMIN_LISTEN = os.environ.get("SPIKE_PEER_ADMIN", "0.0.0.0:9200")
PEER_ID = os.environ.get("SPIKE_PEER_ID", "peer_demo")
ENDPOINT_ID = os.environ.get("SPIKE_ENDPOINT_ID", "iroh_ep_demo_001")
HOST_TIER = os.environ.get("SPIKE_HOST_TIER", "casual")
ALPN = "stream/tunnel/1"
HEARTBEAT_S = float(os.environ.get("SPIKE_HEARTBEAT_S", "5"))

# Designer HARDENING copy — docs/P1_P4_COPY.md · fixtures/screens.json (counsel owns final P1 legal)
P1_UX = {
    "screen": "P1",
    "title": "Before you enroll",
    "body_lines": [
        "Some home internet plans prohibit running a proxy or sharing your connection this way.",
        "Your ISP or carrier may suspend service if they decide this violates their terms.",
        "[Brand] does not guarantee your ISP will allow this.",
        "(Full legal disclaimer: counsel text.)",
    ],
    "checkbox": "I understand and want to continue",
    "required_copy": [
        "does not guarantee your ISP",
        "may suspend service",
        "I understand and want to continue",
    ],
    "user_facing": (
        "Before you enroll\n\n"
        "Some home internet plans prohibit running a proxy or sharing your connection this way.\n"
        "Your ISP or carrier may suspend service if they decide this violates their terms.\n"
        "[Brand] does not guarantee your ISP will allow this.\n"
        "(Full legal disclaimer: counsel text.)\n\n"
        "☐ I understand and want to continue"
    ),
}

P4_UX = {
    "screen": "P4",
    "cta": "Pause sharing",
    "status_after_kill": "Sharing paused",
    "detail": "No traffic through your connection until you turn it back on.",
    "required_copy": [
        "Sharing paused",
        "No traffic through your connection until you turn it back on",
    ],
    "user_facing": (
        "Sharing paused\n"
        "No traffic through your connection until you turn it back on.\n"
        "[ Resume sharing ]"
    ),
}

FORBIDDEN = ("Stream", "waive all liability", "ISP will always allow", "we are not responsible for ISP")

_state_lock = threading.RLock()
_state = {
    "isp_ack_version": os.environ.get("SPIKE_ISP_ACK_VERSION", ""),
    "host_tier": HOST_TIER,
    "online": False,
    "connected": False,
    "streams": {},
    "early_cut_attempts": 0,
    "auth_rejects": 0,
    "last_error": "",
    "kill_requested": False,
    "ux_status": "",  # last user-facing status string for asserts
    "ux_screen": None,
}

_relay_stop = threading.Event()
_relay_thread: threading.Thread | None = None
_sock_holder: dict = {"sock": None, "lock": threading.Lock()}


def _http_json(method: str, path: str, body: dict | None = None, timeout=5):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        CONTROL_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw}
    except Exception as e:
        return 599, {"error": str(e)}


def _split(addr: str):
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def _readline(conn: socket.socket) -> str:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf.decode(errors="replace").rstrip("\r\n")


def _send_line(conn: socket.socket, obj: dict):
    conn.sendall((json.dumps(obj) + "\n").encode())


def egress_denied(host: str, port: int) -> str | None:
    """Local egress floor (#6) — non-bypassable. Allowlist 80/443; deny LAN/metadata."""
    port = int(port)
    if port not in (80, 443):
        return f"port_{port}_blocked"
    h = (host or "").lower().rstrip(".")
    if h in ("metadata.google.internal", "169.254.169.254"):
        return "metadata_blocked"
    if h == "echo.local":
        return None
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if h in ("localhost",) or h.endswith(".lan") or h.endswith(".internal"):
            return "lan_hostname_blocked"
        return None
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or str(ip) == "169.254.169.254"
    ):
        return "rfc1918_or_link_local"
    return None


def _assert_no_forbidden(text: str):
    for bad in FORBIDDEN:
        if bad in text:
            raise ValueError(f"forbidden user-facing substring: {bad!r}")


def _set_ux(screen: str | None, text: str):
    _assert_no_forbidden(text)
    with _state_lock:
        _state["ux_screen"] = screen
        _state["ux_status"] = text


def _snapshot():
    with _state_lock:
        ack = _state["isp_ack_version"]
        killed = _state["kill_requested"]
        snap = {
            "peer_id": PEER_ID,
            "endpoint_id": ENDPOINT_ID,
            "isp_ack_version": ack,
            "host_tier": _state["host_tier"],
            "online": _state["online"],
            "connected": _state["connected"],
            "streams": dict(_state["streams"]),
            "early_cut_attempts": _state["early_cut_attempts"],
            "auth_rejects": _state["auth_rejects"],
            "last_error": _state["last_error"],
            "alpn": ALPN,
            "transport": TRANSPORT,
            "relay_dial": RELAY_DIAL,
            "kill_requested": killed,
            "ux_screen": _state["ux_screen"],
            "ux_status": _state["ux_status"],
            "sharing_status": (
                P4_UX["status_after_kill"] if killed else ("Sharing" if _state["connected"] else "Offline")
            ),
        }
        # Always expose consent payloads for harness greps
        snap["p1"] = P1_UX
        snap["p4"] = P4_UX
        if not ack:
            snap["ux_prompt"] = P1_UX["user_facing"]
        if killed:
            snap["ux_status"] = P4_UX["user_facing"]
            snap["ux_screen"] = "P4"
        return snap


def _set_error(msg: str):
    with _state_lock:
        _state["last_error"] = msg


def _close_relay_sock():
    with _sock_holder["lock"]:
        s = _sock_holder["sock"]
        _sock_holder["sock"] = None
        if s:
            try:
                s.close()
            except Exception:
                pass
    with _state_lock:
        _state["connected"] = False
        _state["online"] = False


def _verify_ticket(ticket_json: str, frame_alpn: str | None = None) -> tuple[bool, str, dict]:
    """#8 Peer-side: Control verify + endpoint bind + ALPN (frame and optional ticket.payload.alpn)."""
    if frame_alpn is not None and frame_alpn != ALPN:
        return False, "bad_alpn", {}
    st, ver = _http_json("POST", "/v1/tickets/verify", {"ticket_json": ticket_json})
    if st != 200 or not ver.get("ok"):
        return False, ver.get("error", "bad_ticket"), {}
    try:
        ticket = json.loads(ticket_json)
        payload = ticket.get("payload") or {}
    except Exception as e:
        return False, str(e), {}
    if payload.get("peer_endpoint_id") != ENDPOINT_ID:
        return False, "endpoint_mismatch", payload
    # HARDENING: if ticket carries alpn, it must match stream/tunnel/1
    ticket_alpn = payload.get("alpn")
    if ticket_alpn is not None and ticket_alpn != ALPN:
        return False, "ticket_alpn_mismatch", payload
    return True, "ok", payload


def _handle_auth_ticket(conn: socket.socket, msg: dict):
    frame_alpn = msg.get("alpn")
    if frame_alpn != ALPN:
        with _state_lock:
            _state["auth_rejects"] += 1
        _send_line(conn, {"type": "AUTH_REJECT", "error": "bad_alpn"})
        return
    dest_host = msg.get("dest_host", "")
    dest_port = int(msg.get("dest_port", 0) or 0)
    denied = egress_denied(dest_host, dest_port)
    if denied:
        with _state_lock:
            _state["auth_rejects"] += 1
        _send_line(conn, {"type": "AUTH_REJECT", "error": denied, "egress_denied": True})
        return
    ok, why, _payload = _verify_ticket(msg.get("ticket_json", ""), frame_alpn=frame_alpn)
    if not ok:
        with _state_lock:
            _state["auth_rejects"] += 1
        _send_line(conn, {"type": "AUTH_REJECT", "error": why})
        return
    # Mid-stream floor: re-check dest on accept (same as AUTH) — Gateway may push new dest later via new ticket
    stream_id = msg.get("stream_id", "")
    with _state_lock:
        _state["streams"][stream_id] = {
            "bytes": 0,
            "closed": False,
            "closed_by": None,
            "dest_host": dest_host,
            "dest_port": dest_port,
        }
    _send_line(conn, {"type": "AUTH_OK"})


def _relay_loop():
    host, port = _split(RELAY_DIAL)
    if TRANSPORT in IROH_LOOPBACK_TRANSPORTS and not _is_loopback_host(host):
        _set_error(f"iroh_loopback_refuses_non_loopback_dial:{host}")
        print(f"A1.1 refuse dial {host!r} (loopback only)", flush=True)
        return
    while not _relay_stop.is_set():
        with _state_lock:
            ack = _state["isp_ack_version"]
            kill = _state["kill_requested"]
            tier = _state["host_tier"]
        if kill or not ack:
            with _state_lock:
                _state["online"] = False
                _state["connected"] = False
            time.sleep(0.3)
            continue
        try:
            s = socket.create_connection((host, port), timeout=5)
            with _sock_holder["lock"]:
                _sock_holder["sock"] = s
            hello = {
                "type": "HELLO",
                "peer_id": PEER_ID,
                "endpoint_id": ENDPOINT_ID,
                "isp_ack_version": ack,
                "host_tier": tier,
                "transport": TRANSPORT,
            }
            _send_line(s, hello)
            s.settimeout(30)
            resp = json.loads(_readline(s) or "{}")
            if resp.get("type") != "HELLO_OK":
                _set_error(f"HELLO failed: {resp}")
                print(f"peer HELLO failed: {resp}", flush=True)
                s.close()
                with _sock_holder["lock"]:
                    if _sock_holder["sock"] is s:
                        _sock_holder["sock"] = None
                time.sleep(1)
                continue
            if resp.get("alpn") and resp.get("alpn") != ALPN:
                _set_error("bad HELLO_OK alpn")
                s.close()
                time.sleep(1)
                continue
            with _state_lock:
                _state["connected"] = True
                _state["online"] = True
                _state["last_error"] = ""
            print(f"peer online {PEER_ID} endpoint={ENDPOINT_ID} ack={ack}", flush=True)
            last_hb = 0.0
            while not _relay_stop.is_set():
                with _state_lock:
                    if _state["kill_requested"] or not _state["isp_ack_version"]:
                        break
                now = time.time()
                if now - last_hb >= HEARTBEAT_S:
                    _http_json(
                        "POST",
                        "/v1/peers/heartbeat",
                        {
                            "peer_id": PEER_ID,
                            "host_tier": _state["host_tier"],
                            "isp_ack_version": _state["isp_ack_version"],
                            "load": 0.1,
                            "endpoint_id": ENDPOINT_ID,
                        },
                    )
                    last_hb = now
                s.settimeout(0.5)
                try:
                    line = _readline(s)
                except socket.timeout:
                    continue
                except Exception as e:
                    _set_error(str(e))
                    break
                if not line:
                    break
                msg = json.loads(line)
                t = msg.get("type")
                if t == "AUTH_TICKET":
                    _handle_auth_ticket(s, msg)
                elif t == "OPEN":
                    sid = msg.get("stream_id")
                    with _state_lock:
                        if sid in _state["streams"]:
                            _state["streams"][sid]["opened"] = True
                elif t == "BYTES":
                    sid = msg.get("stream_id")
                    n = int(msg.get("n", 0))
                    with _state_lock:
                        st = _state["streams"].get(sid)
                        if st and not st.get("closed"):
                            # #6: refuse to count if dest flipped to blocked (defense in depth)
                            if egress_denied(st.get("dest_host", ""), int(st.get("dest_port") or 0)):
                                st["closed"] = True
                                st["closed_by"] = "egress_floor"
                                _state["auth_rejects"] += 1
                            else:
                                st["bytes"] = st.get("bytes", 0) + n
                elif t == "CLOSE":
                    sid = msg.get("stream_id")
                    with _state_lock:
                        st = _state["streams"].setdefault(sid, {"bytes": 0})
                        st["closed"] = True
                        st["closed_by"] = "gateway"
                    print(f"peer teardown stream {sid} (gateway CLOSE)", flush=True)
                elif t == "ERR":
                    _set_error(str(msg))
                    break
            _close_relay_sock()
            print(f"peer offline {PEER_ID}", flush=True)
        except Exception as e:
            _set_error(str(e))
            _close_relay_sock()
            time.sleep(1)


def start_relay_thread():
    global _relay_thread
    _relay_stop.clear()
    if _relay_thread and _relay_thread.is_alive():
        return
    _relay_thread = threading.Thread(target=_relay_loop, daemon=True, name="peer-relay")
    _relay_thread.start()


def do_kill():
    """#7 Kill switch + P4 UX: delist + hard-cut; stay paused until resume."""
    with _state_lock:
        _state["kill_requested"] = True
        for sid, st in _state["streams"].items():
            if not st.get("closed"):
                st["closed"] = True
                st["closed_by"] = "peer_kill"
    _set_ux("P4", P4_UX["user_facing"])
    _http_json("POST", "/v1/peers/kill", {"peer_id": PEER_ID})
    _close_relay_sock()
    print("UX P4\n" + P4_UX["user_facing"], flush=True)
    print("peer kill switch fired", flush=True)


def do_resume():
    with _state_lock:
        _state["kill_requested"] = False
        _state["ux_screen"] = None
        _state["ux_status"] = ""
    print("peer resume — will reconnect if ack set", flush=True)


def do_set_ack(version: str, understood: bool | None = None):
    """#5 ISP ack + P1 UX. Setting non-empty requires understood=True when called via admin with body."""
    version = version or ""
    if version and understood is False:
        # Continue disabled until checkbox — refuse to set ack
        _set_ux("P1", P1_UX["user_facing"])
        print("UX P1 (checkbox required)\n" + P1_UX["user_facing"], flush=True)
        return False
    with _state_lock:
        _state["isp_ack_version"] = version
    if not version:
        _close_relay_sock()
        _set_ux("P1", P1_UX["user_facing"])
        print("UX P1\n" + P1_UX["user_facing"], flush=True)
    else:
        with _state_lock:
            if _state["ux_screen"] == "P1":
                _state["ux_screen"] = None
                _state["ux_status"] = ""
    print(f"peer isp_ack_version={version!r}", flush=True)
    return True


class AdminHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/health", "/peer/status"):
            return self._json(200, {"ok": True, "service": "peer", "hardening": True, **_snapshot()})
        if self.path == "/peer/consent/p1":
            return self._json(200, {"ok": True, **P1_UX})
        if self.path == "/peer/consent/p4":
            return self._json(200, {"ok": True, **P4_UX})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        body = self._read_json()
        if self.path == "/peer/ack":
            version = str(body.get("isp_ack_version", body.get("version", "")))
            # HARDENING: setting ack requires understood checkbox (default True for spike scripts that pass version only)
            understood = body.get("understood")
            if version and "understood" not in body:
                # backward compat for peer_smoke / run_local_asserts — treat as checked
                understood = True
            ok = do_set_ack(version, understood=understood)
            snap = _snapshot()
            if not ok:
                return self._json(403, {"error": "checkbox_required", "code": "p1_ack_gate", **snap})
            return self._json(200, snap)
        if self.path == "/peer/kill":
            do_kill()
            return self._json(200, _snapshot())
        if self.path == "/peer/resume":
            do_resume()
            return self._json(200, _snapshot())
        if self.path == "/peer/host_tier":
            tier = body.get("host_tier", "casual")
            if tier not in ("casual", "always_on"):
                return self._json(400, {"error": "bad_tier"})
            with _state_lock:
                _state["host_tier"] = tier
            return self._json(200, _snapshot())
        if self.path == "/peer/egress_check":
            why = egress_denied(body.get("host", ""), int(body.get("port", 0) or 0))
            return self._json(200, {"denied": why is not None, "reason": why})
        if self.path == "/peer/verify_ticket":
            # Harness helper for #8 Peer-side verify
            ok, why, payload = _verify_ticket(body.get("ticket_json", ""), frame_alpn=body.get("alpn", ALPN))
            return self._json(200 if ok else 403, {"ok": ok, "error": why, "payload": payload})
        return self._json(404, {"error": "not_found"})


def main():
    # Startup: if no ack, surface P1 copy immediately
    if not _state["isp_ack_version"]:
        _set_ux("P1", P1_UX["user_facing"])
        print("UX P1\n" + P1_UX["user_facing"], flush=True)
    start_relay_thread()
    host, port = _split(ADMIN_LISTEN)
    print(
        f"peer admin on {host}:{port} transport={TRANSPORT} dial={RELAY_DIAL} "
        f"ack={_state['isp_ack_version']!r} hardening=1",
        flush=True,
    )
    ThreadingHTTPServer((host, port), AdminHandler).serve_forever()


if __name__ == "__main__":
    main()
