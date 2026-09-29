"""Platform control-plane stub — match/session/grace/denylist/freeze + AUTH_TICKET mint."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

LISTEN = os.environ.get("SPIKE_LISTEN", "0.0.0.0:8080")
DB_PATH = os.environ.get("SPIKE_DB", "/tmp/spike.sqlite")
TICKET_SECRET = os.environ.get("SPIKE_TICKET_SECRET", "dev-only-change-me").encode()
DENYLIST_PATH = os.environ.get(
    "SPIKE_DENYLIST",
    str(Path(__file__).resolve().parent / "denylist.seed.json"),
)

GRACE_MAX_BYTES = 5_242_880
GRACE_MAX_MS = 15_000
HOST, PORT = LISTEN.rsplit(":", 1)

_lock = threading.RLock()
_events: list[dict] = []  # append-only for harness
_fixtures = {
    "strict_unavailable": False,
    "country_paused": False,
    "oversubscribed": False,
}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        c = _conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts(
              id TEXT PRIMARY KEY,
              kyc_tier INTEGER NOT NULL DEFAULT 1,
              balance_usd REAL NOT NULL DEFAULT 5.0,
              frozen INTEGER NOT NULL DEFAULT 0,
              aup_accepted INTEGER NOT NULL DEFAULT 1,
              accrued_payout_usd REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS peers(
              peer_id TEXT PRIMARY KEY,
              endpoint_id TEXT NOT NULL,
              country TEXT, city TEXT,
              host_tier TEXT DEFAULT 'casual',
              isp_ack_version TEXT DEFAULT '',
              online INTEGER DEFAULT 0,
              fraud_score REAL DEFAULT 0,
              load REAL DEFAULT 0.1
            );
            CREATE TABLE IF NOT EXISTS quotes(
              quote_id TEXT PRIMARY KEY,
              account_id TEXT, peer_id TEXT, endpoint_id TEXT,
              geo_json TEXT, rematch_mode TEXT, price_mult REAL,
              price_per_gb REAL, exclusive INTEGER, hold_expires_at REAL,
              created_at REAL
            );
            CREATE TABLE IF NOT EXISTS sessions(
              session_id TEXT PRIMARY KEY,
              label TEXT UNIQUE,
              stream_id TEXT UNIQUE,
              account_id TEXT, peer_id TEXT, endpoint_id TEXT,
              rematch_mode TEXT, price_mult REAL, price_per_gb REAL,
              expires_at REAL, status TEXT,
              balance_state TEXT DEFAULT 'ok',
              bytes_non_grace INTEGER DEFAULT 0,
              bytes_grace INTEGER DEFAULT 0,
              grace_started_at REAL,
              ticket_json TEXT
            );
            CREATE TABLE IF NOT EXISTS ledger(
              entry_id TEXT PRIMARY KEY,
              stream_id TEXT, kind TEXT, amount REAL,
              account_id TEXT, peer_id TEXT, ts REAL,
              UNIQUE(stream_id, kind)
            );
            CREATE TABLE IF NOT EXISTS denylist(
              host_pattern TEXT, port INTEGER, reason TEXT,
              PRIMARY KEY(host_pattern, port)
            );
            """
        )
        # seed demo account + peer
        c.execute(
            "INSERT OR IGNORE INTO accounts(id,kyc_tier,balance_usd,frozen,aup_accepted) VALUES(?,?,?,?,?)",
            ("acct_demo", 1, 5.0, 0, 1),
        )
        c.execute(
            "INSERT OR IGNORE INTO peers(peer_id,endpoint_id,country,city,host_tier,isp_ack_version,online) VALUES(?,?,?,?,?,?,?)",
            ("peer_demo", "iroh_ep_demo_001", "US", "new_orleans", "always_on", "", 0),
        )
        c.commit()
        _load_denylist(c)
        c.close()


def _load_denylist(c: sqlite3.Connection) -> None:
    path = Path(DENYLIST_PATH)
    if not path.exists():
        # fallback seed
        seed = {
            "entries": [
                {"host_pattern": "*.bank.example", "ports": [443], "reason": "banking"},
                {"host_pattern": "*.gov.example", "ports": [443], "reason": "gov"},
                {"host_pattern": "mail.example", "ports": [25, 443], "reason": "mail"},
            ]
        }
    else:
        seed = json.loads(path.read_text())
    c.execute("DELETE FROM denylist")
    for e in seed.get("entries", []):
        for port in e.get("ports", [443]):
            c.execute(
                "INSERT OR REPLACE INTO denylist(host_pattern,port,reason) VALUES(?,?,?)",
                (e["host_pattern"], int(port), e.get("reason", "")),
            )
    c.commit()


def _emit(event: str, **payload) -> dict:
    ev = {"event": event, "ts": time.time(), **payload}
    with _lock:
        _events.append(ev)
    return ev


def _capacity() -> dict:
    with _lock:
        c = _conn()
        online = c.execute("SELECT COUNT(*) FROM peers WHERE online=1").fetchone()[0]
        c.close()
        paused = _fixtures["country_paused"]
        strict_unavail = _fixtures["strict_unavailable"]
        over = _fixtures["oversubscribed"]
    country_online = 100 if paused else max(online, 300)
    concurrent = 10
    cap = int(country_online * 0.5)
    strict_n = 10 if strict_unavail else 30
    return {
        "country_online": country_online,
        "country_concurrent": concurrent,
        "country_cap": cap,
        "country_seed_met": country_online >= 300,
        "country_sell_paused": country_online < 150 or paused,
        "strict_eligible_n": strict_n,
        "strict_min_n": 25,
        "strict_assign_success_7d": 0.5 if strict_unavail else 0.95,
        "strict_kill": strict_unavail or strict_n < 25,
        "oversubscribed": over or concurrent > cap,
    }


def _sign_ticket(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(TICKET_SECRET, body, hashlib.sha256).hexdigest()
    return json.dumps({"payload": payload, "sig": sig})


def verify_ticket(ticket_json: str) -> tuple[bool, str]:
    try:
        obj = json.loads(ticket_json)
        payload = obj["payload"]
        sig = obj["sig"]
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        expect = hmac.new(TICKET_SECRET, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return False, "bad_sig"
        if payload.get("exp", 0) < time.time():
            return False, "expired"
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _host_denied(host: str, port: int) -> str | None:
    with _lock:
        c = _conn()
        rows = c.execute("SELECT host_pattern, port, reason FROM denylist").fetchall()
        c.close()
    host = host.lower().rstrip(".")
    for r in rows:
        if int(r["port"]) != int(port):
            continue
        pat = r["host_pattern"].lower()
        if pat.startswith("*."):
            suf = pat[1:]  # .bank.example
            if host.endswith(suf) or host == pat[2:]:
                return r["reason"]
        elif host == pat:
            return r["reason"]
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    def _json(self, code: int, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"ok": True, "service": "control", "stub": False})
        if path == "/v1/events":
            with _lock:
                return self._json(200, {"events": list(_events)})
        if path.startswith("/v1/capacity/"):
            return self._json(200, {"capacity": _capacity()})
        if path.startswith("/v1/sessions/"):
            label = path.split("/v1/sessions/", 1)[1].split("/")[0]
            with _lock:
                c = _conn()
                row = c.execute("SELECT * FROM sessions WHERE label=?", (label,)).fetchone()
                c.close()
            if not row:
                return self._json(404, {"error": "not_found"})
            return self._json(
                200,
                {
                    "session_id": row["session_id"],
                    "label": row["label"],
                    "stream_id": row["stream_id"],
                    "status": row["status"],
                    "balance_state": row["balance_state"],
                    "bytes_non_grace": row["bytes_non_grace"],
                    "bytes_grace": row["bytes_grace"],
                    "rematch_mode": row["rematch_mode"],
                    "peer_id": row["peer_id"],
                    "grace": {
                        "max_bytes": GRACE_MAX_BYTES,
                        "max_ms": GRACE_MAX_MS,
                        "peer_paid": True,
                    },
                },
            )
        if path.startswith("/v1/ledger/"):
            stream_id = path.split("/v1/ledger/", 1)[1]
            with _lock:
                c = _conn()
                rows = c.execute(
                    "SELECT * FROM ledger WHERE stream_id=? ORDER BY ts", (stream_id,)
                ).fetchall()
                c.close()
            return self._json(
                200,
                {
                    "stream_id": stream_id,
                    "entries": [dict(r) for r in rows],
                },
            )
        if path.startswith("/v1/accounts/"):
            aid = path.split("/v1/accounts/", 1)[1]
            with _lock:
                c = _conn()
                row = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
                c.close()
            if not row:
                return self._json(404, {"error": "not_found"})
            return self._json(
                200,
                {
                    "id": row["id"],
                    "balance_usd": row["balance_usd"],
                    "frozen": bool(row["frozen"]),
                    "kyc_tier": row["kyc_tier"],
                    "aup_accepted": bool(row["aup_accepted"]),
                    "accrued_payout_usd": row["accrued_payout_usd"],
                },
            )
        if path == "/v1/admin/verify_ticket_helper":
            return self._json(400, {"error": "POST ticket_json"})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/v1/admin/reset":
            with _lock:
                _events.clear()
                _fixtures["strict_unavailable"] = False
                _fixtures["country_paused"] = False
                _fixtures["oversubscribed"] = False
                c = _conn()
                for t in ("quotes", "sessions", "ledger"):
                    c.execute(f"DELETE FROM {t}")
                c.execute(
                    "UPDATE accounts SET balance_usd=5.0, frozen=0, aup_accepted=1, accrued_payout_usd=0 WHERE id='acct_demo'"
                )
                c.execute("UPDATE peers SET online=0, isp_ack_version='' WHERE peer_id='peer_demo'")
                c.commit()
                c.close()
            return self._json(200, {"ok": True})

        if path == "/v1/admin/fixtures":
            with _lock:
                if "strict_unavailable" in body:
                    _fixtures["strict_unavailable"] = bool(body["strict_unavailable"])
                if "country_paused" in body:
                    _fixtures["country_paused"] = bool(body["country_paused"])
                if "oversubscribed" in body:
                    _fixtures["oversubscribed"] = bool(body["oversubscribed"])
            return self._json(200, {"capacity": _capacity()})

        if path == "/v1/peers/enroll":
            peer_id = body.get("peer_id", "peer_demo")
            endpoint_id = body.get("endpoint_id") or f"iroh_ep_{peer_id}"
            ack = body.get("isp_ack_version", "")
            host_tier = body.get("host_tier", "casual")
            with _lock:
                c = _conn()
                c.execute(
                    """INSERT INTO peers(peer_id,endpoint_id,country,city,host_tier,isp_ack_version,online)
                       VALUES(?,?,?,?,?,?,1)
                       ON CONFLICT(peer_id) DO UPDATE SET
                         endpoint_id=excluded.endpoint_id,
                         host_tier=excluded.host_tier,
                         isp_ack_version=excluded.isp_ack_version,
                         online=1""",
                    (peer_id, endpoint_id, body.get("country", "US"), body.get("city", "new_orleans"), host_tier, ack),
                )
                c.commit()
                c.close()
            return self._json(200, {"peer_id": peer_id, "endpoint_id": endpoint_id, "online": True})

        if path == "/v1/peers/heartbeat":
            peer_id = body.get("peer_id", "peer_demo")
            with _lock:
                c = _conn()
                c.execute(
                    "UPDATE peers SET online=1, load=?, host_tier=COALESCE(?,host_tier), isp_ack_version=COALESCE(?,isp_ack_version) WHERE peer_id=?",
                    (body.get("load", 0.1), body.get("host_tier"), body.get("isp_ack_version"), peer_id),
                )
                c.commit()
                c.close()
            return self._json(200, {"ok": True})

        if path == "/v1/peers/kill":
            peer_id = body.get("peer_id", "peer_demo")
            with _lock:
                c = _conn()
                c.execute("UPDATE peers SET online=0 WHERE peer_id=?", (peer_id,))
                rows = c.execute(
                    "SELECT label, stream_id FROM sessions WHERE peer_id=? AND status='active'",
                    (peer_id,),
                ).fetchall()
                for r in rows:
                    c.execute(
                        "UPDATE sessions SET status='stopped', balance_state='stopped' WHERE stream_id=?",
                        (r["stream_id"],),
                    )
                    _emit("peer.kill", peer_id=peer_id, stream_id=r["stream_id"], label=r["label"])
                c.commit()
                c.close()
            return self._json(200, {"ok": True, "stopped_streams": [r["stream_id"] for r in rows]})

        if path == "/v1/match":
            return self._match(body)

        if path.startswith("/v1/match/") and path.endswith("/release"):
            qid = path.split("/")[3]
            with _lock:
                c = _conn()
                c.execute("DELETE FROM quotes WHERE quote_id=?", (qid,))
                c.commit()
                c.close()
            return self._json(200, {"released": qid})

        if path == "/v1/sessions":
            return self._session_start(body)

        if path.startswith("/v1/sessions/") and path.endswith("/force-zero"):
