#!/usr/bin/env python3
"""A4.3 stand-in "public" site for the netns lane (TEST-NET-2 198.51.100.10:443, TLS, self-signed).

Runs only inside the a43-site netns (no default route). Minimal HTTP/1.1 over TLS:
  GET /hello      -> 200 "a43-stand-in hello from 198.51.100.10"
  GET /big?n=N    -> 200 N deterministic bytes (sha256 printed in X-Sha256)
  GET /hold       -> 200 headers, then holds the connection open (kill-switch tests)
Logs per connection: SITE_TCP_ACCEPT <ip:port>, SITE_ACCEPT <peer ip:port> / SITE_REQ <path> / SITE_DONE <path> <n>.
Usage: a43_standin_site.py <cert.pem> <key.pem> [bind=198.51.100.10] [port=443]
"""
import hashlib
import socket
import ssl
import sys
import threading
import time


def body_for(n):
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(str(i).encode()).digest()
        i += 1
    return bytes(out[:n])


def handle(ctx, c, addr):
    cs = c
    try:
        print(f"SITE_TCP_ACCEPT {addr[0]}:{addr[1]}", flush=True)
        c.settimeout(60)
        try:
            cs = ctx.wrap_socket(c, server_side=True)
        except (ssl.SSLError, OSError) as e:
            print(f"SITE_TLS_FAIL {addr[0]}:{addr[1]} {e}", flush=True)
            return
        print(f"SITE_ACCEPT {addr[0]}:{addr[1]} tls={cs.version()}", flush=True)
        cs.settimeout(30)
        req = b""
        while b"\r\n\r\n" not in req and len(req) < 8192:
            d = cs.recv(4096)
            if not d:
                return
            req += d
        line = req.split(b"\r\n", 1)[0].decode(errors="replace")
        path = line.split(" ")[1] if line.count(" ") >= 2 else "/"
        print(f"SITE_REQ {path}", flush=True)
        if path.startswith("/hold"):
            cs.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhold\n\r\n")
            cs.settimeout(None)
            while cs.recv(4096):
                pass
            print(f"SITE_DONE {path} closed_by_far_side", flush=True)
            return
        if path.startswith("/big"):
            n = int(path.split("n=", 1)[1]) if "n=" in path else 200_000
            b = body_for(n)
        else:
            b = b"a43-stand-in hello from 198.51.100.10\n"
        hdr = (f"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {len(b)}\r\n"
               f"X-Sha256: {hashlib.sha256(b).hexdigest()}\r\nConnection: close\r\n\r\n").encode()
        cs.sendall(hdr + b)
        print(f"SITE_DONE {path} {len(b)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"SITE_ERR {type(e).__name__}: {e}", flush=True)
    finally:
        try:
            cs.close()
        except OSError:
            pass


def main():
    cert, key = sys.argv[1], sys.argv[2]
    bind = sys.argv[3] if len(sys.argv) > 3 else "198.51.100.10"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 443
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((bind, port))
    ls.listen(16)
    print(f"SITE_READY {bind}:{port}", flush=True)
    while True:
        c, a = ls.accept()
        threading.Thread(target=handle, args=(ctx, c, a), daemon=True).start()


if __name__ == "__main__":
    main()
