"""Platform Gateway stub — denylist, fake Relay, metering, grace stop."""
from __future__ import annotations

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
PROXY_LISTEN = os.environ.get("SPIKE_LISTEN_PROXY", "0.0.0.0:1080")
# A1.1: iroh_loopback uses dedicated loopback port; fake_relay stays on 9100.
ALPN = "stream/tunnel/1"
IROH_LOOPBACK_TRANSPORTS = frozenset({"iroh", "iroh_loopback"})


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "::1", "localhost")


def _relay_listen_addr() -> str:
    if TRANSPORT in IROH_LOOPBACK_TRANSPORTS:
        return os.environ.get("SPIKE_IROH_LOOPBACK", "127.0.0.1:9101")
    return os.environ.get("SPIKE_FAKE_RELAY", "0.0.0.0:9100")


RELAY_LISTEN = _relay_listen_addr()

_peers: dict[str, dict] = {}
_peers_lock = threading.RLock()
_streams: dict[str, dict] = {}
_streams_lock = threading.RLock()


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


def _split(listen: str):
    host, port = listen.rsplit(":", 1)
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


def handle_peer_conn(conn: socket.socket, addr):
    """Peer dials in; after HELLO we hold the socket. Exclusive IO under meta['lock']."""
    dead = threading.Event()
    peer_id = None
    try:
        conn.settimeout(30)
        hello = json.loads(_readline(conn) or "{}")
        if hello.get("type") != "HELLO":
            _send_line(conn, {"type": "ERR", "error": "expected_HELLO"})
            conn.close()
            return
        peer_id = hello.get("peer_id", "peer_demo")
        endpoint_id = hello.get("endpoint_id", "")
        ack = hello.get("isp_ack_version", "")
        host_tier = hello.get("host_tier", "casual")
        if not ack:
            _send_line(conn, {"type": "ERR", "error": "isp_ack_required", "code": "peer_ack_gate"})
            conn.close()
            return
        _http_json(
            "POST",
            "/v1/peers/enroll",
            {
                "peer_id": peer_id,
                "endpoint_id": endpoint_id,
                "isp_ack_version": ack,
                "host_tier": host_tier,
                "country": "US",
                "city": "new_orleans",
            },
        )
        with _peers_lock:
            old = _peers.pop(peer_id, None)
            if old:
                old.get("dead", threading.Event()).set()
                try:
                    old["sock"].close()
                except Exception:
                    pass
            _peers[peer_id] = {
                "sock": conn,
                "endpoint_id": endpoint_id,
                "isp_ack_version": ack,
                "host_tier": host_tier,
                "lock": threading.Lock(),
                "dead": dead,
            }
        _send_line(conn, {"type": "HELLO_OK", "alpn": ALPN, "transport": TRANSPORT})
        print(f"peer online {peer_id} endpoint={endpoint_id}", flush=True)
        # Detect Peer TCP close without racing locked AUTH/BYTES IO:
        # brief peeks only when lock is free.
        while not dead.is_set():
            with _peers_lock:
                meta = _peers.get(peer_id)
            if not meta or meta.get("sock") is not conn:
                break
            acquired = meta["lock"].acquire(blocking=False)
            if acquired:
                try:
                    conn.settimeout(0.3)
                    try:
                        peek = conn.recv(1, socket.MSG_PEEK)
                        if not peek:
                            break
                    except socket.timeout:
                        pass
                    except OSError:
                        break
                finally:
                    meta["lock"].release()
            else:
                time.sleep(0.2)
            dead.wait(0.2)
    except Exception as e:
        print(f"peer conn error {addr}: {e}", flush=True)
    finally:
        with _peers_lock:
            meta = _peers.get(peer_id) if peer_id else None
            if meta and meta.get("sock") is conn:
                _peers.pop(peer_id, None)
                print(f"peer offline {peer_id}", flush=True)
        try:
            conn.close()
        except Exception:
            pass


def relay_server():
    host, port = _split(RELAY_LISTEN)
    if TRANSPORT in IROH_LOOPBACK_TRANSPORTS and not _is_loopback_host(host):
        raise SystemExit(
            f"A1.1 iroh_loopback refuses non-loopback bind {host!r} (no public egress)"
        )
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(50)
    label = "iroh loopback" if TRANSPORT in IROH_LOOPBACK_TRANSPORTS else "fake Relay"
    print(f"{label} listening on {host}:{port} transport={TRANSPORT}", flush=True)
    while True:
        conn, addr = s.accept()
        threading.Thread(target=handle_peer_conn, args=(conn, addr), daemon=True).start()


def _drop_peer(peer_id: str, reason: str = ""):
    with _peers_lock:
        meta = _peers.pop(peer_id, None)
    if meta:
        meta.get("dead", threading.Event()).set()
        try:
            meta["sock"].close()
        except Exception:
            pass
        print(f"peer dropped {peer_id} {reason}", flush=True)


def open_stream_to_peer(peer_id: str, ticket_json: str, stream_id: str, dest_host: str, dest_port: int):
    with _peers_lock:
        meta = _peers.get(peer_id)
    if not meta:
        return False, "peer_offline"
    with meta["lock"]:
        try:
            conn = meta["sock"]
            conn.settimeout(10)
            _send_line(
                conn,
                {
                    "type": "AUTH_TICKET",
                    "alpn": ALPN,
                    "ticket_json": ticket_json,
                    "stream_id": stream_id,
                    "dest_host": dest_host,
                    "dest_port": dest_port,
                },
            )
            resp = json.loads(_readline(conn) or "{}")
            if resp.get("type") != "AUTH_OK":
                return False, resp.get("error", "auth_rejected")
            # egress floor may reject dest
            if resp.get("egress_denied"):
                return False, resp.get("error", "egress_denied")
            _send_line(conn, {"type": "OPEN", "stream_id": stream_id})
            return True, "ok"
        except Exception as e:
            _drop_peer(peer_id, str(e))
            return False, str(e)


def close_stream_on_peer(peer_id: str, stream_id: str):
    with _peers_lock:
        meta = _peers.get(peer_id)
    if not meta:
        return
    with meta["lock"]:
        try:
            _send_line(meta["sock"], {"type": "CLOSE", "stream_id": stream_id})
        except Exception as e:
            _drop_peer(peer_id, f"close_failed:{e}")


def meter_loop(stream_id: str, peer_id: str, chunk=64_000, interval=0.15):
    """HARDENING: mid-stream dest recheck + freeze stop via Control usage flush."""
    ticks = 0
    while True:
        with _streams_lock:
            st = _streams.get(stream_id)
            if not st or st.get("stop"):
                break
            dest_host = st.get("dest_host", "echo.local")
            dest_port = int(st.get("dest_port", 443))
        with _peers_lock:
            meta = _peers.get(peer_id)
        if meta:
            with meta["lock"]:
                try:
                    _send_line(meta["sock"], {"type": "BYTES", "stream_id": stream_id, "n": chunk})
                except Exception as e:
                    _drop_peer(peer_id, f"bytes_failed:{e}")
                    with _streams_lock:
                        if stream_id in _streams:
                            _streams[stream_id]["stop"] = True
                    break
        flush_body = {"stream_id": stream_id, "bytes": chunk}
        # every few ticks, re-send dest so Control can recheck versioned denylist (#3)
        ticks += 1
        if ticks % 3 == 1:
            flush_body["dest_host"] = dest_host
            flush_body["dest_port"] = dest_port
        code, resp = _http_json("POST", "/v1/usage/flush", flush_body)
        if resp.get("event"):
            print(f"EVENT {json.dumps(resp['event'])}", flush=True)
        if resp.get("stop") or resp.get("balance_state") == "stopped" or resp.get("frozen") or resp.get("dest_denied"):
            with _streams_lock:
                if stream_id in _streams:
                    _streams[stream_id]["stop"] = True
            close_stream_on_peer(peer_id, stream_id)
            why = "frozen" if resp.get("frozen") else ("dest_denied" if resp.get("dest_denied") else "stopped")
            print(f"stream stopped {stream_id} reason={why}", flush=True)
            break
        time.sleep(interval)
    close_stream_on_peer(peer_id, stream_id)


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
        if self.path == "/health":
            with _peers_lock:
                peers = list(_peers.keys())
            return self._json(
                200,
                {
                    "ok": True,
                    "service": "gateway",
                    "peers": peers,
                    "alpn": ALPN,
                    "transport": TRANSPORT,
                    "relay_listen": RELAY_LISTEN,
                    "hardening": ["denylist_midstream", "freeze_stop", "ticket_alpn"],
                },
            )
        if self.path == "/gw/peers":
            with _peers_lock:
                return self._json(
                    200,
                    {
                        "peers": [
                            {
                                "peer_id": k,
                                "endpoint_id": v["endpoint_id"],
                                "isp_ack_version": v["isp_ack_version"],
                                "host_tier": v["host_tier"],
                            }
                            for k, v in _peers.items()
                        ]
                    },
                )
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        body = self._read_json()
        if self.path == "/gw/start":
            dest_host = body.get("dest_host", "echo.local")
            dest_port = int(body.get("dest_port", 443))
            code, chk = _http_json("POST", "/v1/dest/check", {"host": dest_host, "port": dest_port})
            if code != 200 or not chk.get("allowed", False):
                return self._json(
                    403,
                    {
                        "error": "dest_denied",
                        "code": "dest_denied",
                        "detail": chk,
                        "user_copy": "blocked",
                        "denylist_version": chk.get("denylist_version"),
                    },
                )
            if body.get("alpn", ALPN) != ALPN:
                return self._json(403, {"error": "bad_alpn", "code": "bad_alpn"})
            ticket_json = body.get("ticket_json", "")
            # HARDENING #8: Control verifies HMAC + required alpn=stream/tunnel/1
            vcode, vresp = _http_json("POST", "/v1/tickets/verify", {"ticket_json": ticket_json})
            if vcode != 200 or not vresp.get("ok"):
                return self._json(
                    403,
                    {
                        "error": vresp.get("reason", "ticket_invalid"),
                        "code": "auth_ticket_failed",
                        "detail": vresp,
                    },
                )
            peer_id = body["peer_id"]
            stream_id = body["stream_id"]
            ok, why = open_stream_to_peer(peer_id, ticket_json, stream_id, dest_host, dest_port)
            if not ok:
                return self._json(403, {"error": why, "code": "auth_ticket_failed"})
            with _streams_lock:
                _streams[stream_id] = {
                    "stop": False,
                    "peer_id": peer_id,
                    "label": body.get("label"),
                    "dest_host": dest_host,
                    "dest_port": dest_port,
                    "denylist_version": chk.get("denylist_version"),
                }
            threading.Thread(target=meter_loop, args=(stream_id, peer_id), daemon=True).start()
            return self._json(
                200,
                {
                    "started": True,
                    "stream_id": stream_id,
                    "denylist_version": chk.get("denylist_version"),
                    "alpn": ALPN,
                },
            )

        if self.path == "/gw/stop":
            stream_id = body.get("stream_id")
            with _streams_lock:
                st = _streams.get(stream_id)
                if st:
                    st["stop"] = True
                    peer_id = st["peer_id"]
                else:
                    peer_id = body.get("peer_id")
            if peer_id and stream_id:
                close_stream_on_peer(peer_id, stream_id)
            return self._json(200, {"stopped": stream_id})

        if self.path == "/gw/inject_bad_ticket":
            peer_id = body.get("peer_id", "peer_demo")
            ok, why = open_stream_to_peer(
                peer_id,
                body.get("ticket_json", '{"payload":{},"sig":"bad"}'),
                body.get("stream_id", "str_bad"),
                "echo.local",
                443,
            )
            if ok:
                return self._json(500, {"error": "should_have_failed"})
            return self._json(200, {"rejected": True, "reason": why})

        return self._json(404, {"error": "not_found"})


def main():
    threading.Thread(target=relay_server, daemon=True).start()
    for _ in range(50):
        code, _ = _http_json("GET", "/health")
        if code == 200:
            break
        time.sleep(0.2)
    host, port = _split(PROXY_LISTEN)
    print(f"gateway HTTP on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), AdminHandler).serve_forever()


if __name__ == "__main__":
    main()
