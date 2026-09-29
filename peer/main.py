"""Peer agent stub — ISP ack gate, heartbeat, AUTH_TICKET verify, egress floor, teardown-only grace, kill."""
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
RELAY_DIAL = os.environ.get("SPIKE_FAKE_RELAY_DIAL", "127.0.0.1:9100")
ADMIN_LISTEN = os.environ.get("SPIKE_PEER_ADMIN", "0.0.0.0:9200")
PEER_ID = os.environ.get("SPIKE_PEER_ID", "peer_demo")
ENDPOINT_ID = os.environ.get("SPIKE_ENDPOINT_ID", "iroh_ep_demo_001")
HOST_TIER = os.environ.get("SPIKE_HOST_TIER", "casual")
ALPN = "stream/tunnel/1"
HEARTBEAT_S = float(os.environ.get("SPIKE_HEARTBEAT_S", "5"))

_state_lock = threading.RLock()
_state = {
    "isp_ack_version": os.environ.get("SPIKE_ISP_ACK_VERSION", ""),
    "host_tier": HOST_TIER,
    "online": False,
    "connected": False,
    "streams": {},  # stream_id -> {bytes, closed, closed_by}
    "early_cut_attempts": 0,  # Peer-initiated closes before Gateway CLOSE — must stay 0 on grace
    "auth_rejects": 0,
    "last_error": "",
    "kill_requested": False,
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
    """Local egress floor — non-bypassable. Allowlist ports 80/443; deny LAN/metadata."""
    port = int(port)
    if port not in (80, 443):
        return f"port_{port}_blocked"
    h = (host or "").lower().rstrip(".")
    if h in ("metadata.google.internal", "169.254.169.254"):
        return "metadata_blocked"
    # Allow spike mock dest
    if h == "echo.local":
        return None
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # hostname: block obvious LAN names; allow public-looking names for mock
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


def _snapshot():
    with _state_lock:
        return {
            "peer_id": PEER_ID,
            "endpoint_id": ENDPOINT_ID,
            "isp_ack_version": _state["isp_ack_version"],
            "host_tier": _state["host_tier"],
            "online": _state["online"],
            "connected": _state["connected"],
            "streams": dict(_state["streams"]),
            "early_cut_attempts": _state["early_cut_attempts"],
            "auth_rejects": _state["auth_rejects"],
            "last_error": _state["last_error"],
            "alpn": ALPN,
        }


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


def _verify_ticket(ticket_json: str) -> tuple[bool, str, dict]:
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
    return True, "ok", payload


def _handle_auth_ticket(conn: socket.socket, msg: dict):
    if msg.get("alpn") != ALPN:
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
        _send_line(
            conn,
            {"type": "AUTH_REJECT", "error": denied, "egress_denied": True},
        )
        return
    ok, why, _payload = _verify_ticket(msg.get("ticket_json", ""))
    if not ok:
        with _state_lock:
            _state["auth_rejects"] += 1
        _send_line(conn, {"type": "AUTH_REJECT", "error": why})
        return
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
            # Heartbeat + read loop
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
                            st["bytes"] = st.get("bytes", 0) + n
                    # splice mock only — never close early
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
            # exiting read loop
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
    """Kill switch: delist + hard-cut + stay paused until resume."""
    with _state_lock:
        _state["kill_requested"] = True
        for sid, st in _state["streams"].items():
            if not st.get("closed"):
                st["closed"] = True
                st["closed_by"] = "peer_kill"
    _http_json("POST", "/v1/peers/kill", {"peer_id": PEER_ID})
    _close_relay_sock()
    print("peer kill switch fired", flush=True)


def do_resume():
    with _state_lock:
        _state["kill_requested"] = False
    print("peer resume — will reconnect if ack set", flush=True)


def do_set_ack(version: str):
    with _state_lock:
        _state["isp_ack_version"] = version or ""
        if not version:
            # clearing ack forces disconnect
            pass
    if not version:
        _close_relay_sock()
    print(f"peer isp_ack_version={version!r}", flush=True)


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
            return self._json(200, {"ok": True, "service": "peer", **_snapshot()})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        body = self._read_json()
        if self.path == "/peer/ack":
            do_set_ack(str(body.get("isp_ack_version", body.get("version", ""))))
            return self._json(200, _snapshot())
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
            # harness helper: unit-test floor without a live stream
            why = egress_denied(body.get("host", ""), int(body.get("port", 0) or 0))
            return self._json(200, {"denied": why is not None, "reason": why})
        return self._json(404, {"error": "not_found"})


def main():
    start_relay_thread()
    host, port = _split(ADMIN_LISTEN)
    print(
        f"peer admin on {host}:{port} dial={RELAY_DIAL} ack={_state['isp_ack_version']!r}",
        flush=True,
    )
    ThreadingHTTPServer((host, port), AdminHandler).serve_forever()


if __name__ == "__main__":
    main()
