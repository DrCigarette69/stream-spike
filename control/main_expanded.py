"""Platform control-plane stub — match/session/grace/denylist/freeze + AUTH_TICKET mint."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import re
import sys
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
_last_assigned: dict[str, float] = {}  # peer_id -> monotonic time of last match (tie-break)
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
            CREATE TABLE IF NOT EXISTS denylist_meta(
              id INTEGER PRIMARY KEY CHECK (id=1),
              version INTEGER NOT NULL DEFAULT 1,
              updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS abuse_cases(
              id TEXT PRIMARY KEY,
              account_id TEXT,
              source TEXT,
              status TEXT,
              actions TEXT,
              stream_ids TEXT,
              created_at REAL
            );
            CREATE TABLE IF NOT EXISTS attribution(
              stream_id TEXT PRIMARY KEY,
              account_id TEXT,
              peer_id TEXT,
              dest_host TEXT,
              dest_port INTEGER,
              bytes_up INTEGER DEFAULT 0,
              bytes_down INTEGER DEFAULT 0,
              geo_tier TEXT,
              frozen_stop INTEGER DEFAULT 0,
              ts REAL
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
        _load_denylist(c)  # seeds denylist + denylist_meta.version
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
    ver = int(seed.get("version", 1))
    c.execute(
        "INSERT INTO denylist_meta(id, version, updated_at) VALUES(1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at",
        (ver, time.time()),
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
        for req in ("session_id", "stream_id", "peer_endpoint_id", "gateway_endpoint_id", "alpn", "exp"):
            if req not in payload:
                return False, f"missing_{req}"
        if payload.get("alpn") != "stream/tunnel/1":
            return False, "bad_alpn"
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        expect = hmac.new(TICKET_SECRET, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return False, "bad_sig"
        if float(payload.get("exp", 0)) < time.time():
            return False, "expired"
        if _iroh_local():
            why = _iroh_verify(payload)
            if why:
                return False, why
        if _iroh_pilot():
            why = _pilot_verify(payload)
            if why:
                return False, why
        return True, "ok"
    except Exception as e:
        return False, str(e)


# ---- A3.3 ticket binding (iroh_local only; other transports unchanged) ----
DEV_GATEWAY_ENDPOINT_ID = "162e075fff299e4c5fba4903ff9f4c9279aeaca5b617c4d9ec0d126dcf00d7a1"
DEFAULT_IROH_GATEWAY_ADDR = "127.0.0.1:9102"
_ENDPOINT_ID_RE = re.compile(r"^[0-9a-f]{64}$")  # iroh EndpointId Display form


def _iroh_local() -> bool:
    return os.environ.get("SPIKE_TRANSPORT", "").strip().lower() == "iroh_local"


def _guard():
    """scripts/spike_private_guard.py (A3.0 Python mirror); same reasons as stream-proto::guard."""
    try:
        import spike_private_guard  # noqa: PLC0415
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import spike_private_guard  # noqa: PLC0415
    return spike_private_guard


def _valid_endpoint_id(v) -> bool:
    return isinstance(v, str) and bool(_ENDPOINT_ID_RE.match(v))


def _iroh_direct_addrs() -> list[str]:
    raw = os.environ.get("SPIKE_IROH_GATEWAY_ADDR")
    if raw is None:
        raw = DEFAULT_IROH_GATEWAY_ADDR
    return [a.strip() for a in raw.split(",") if a.strip()]


def _iroh_bind(peer_endpoint_id) -> tuple[dict | None, str | None, str | None]:
    """Returns (extra_payload, reason, detail). reason=None means OK."""
    if not _valid_endpoint_id(peer_endpoint_id):
        return None, "endpoint_bind_required", str(peer_endpoint_id)
    gw = os.environ.get("SPIKE_GATEWAY_ENDPOINT_ID", "").strip() or DEV_GATEWAY_ENDPOINT_ID
    if not _valid_endpoint_id(gw):
        return None, "gateway_endpoint_invalid", gw
    addrs = _iroh_direct_addrs()
    if not addrs:
        return None, "direct_addrs_required", ""
    try:
        g = _guard()
    except ImportError:  # e.g. control-only Docker image without scripts/: fail closed
        return None, "guard_unavailable", "scripts/spike_private_guard.py"
    try:
        g.check_direct_addrs(addrs)
    except g.GuardError as e:
        return None, e.reason, e.log_line()
    return {"gateway_endpoint_id": gw, "direct_addrs": addrs}, None, None


def _iroh_verify(payload: dict) -> str | None:
    """Extra verify rules for iroh_local tickets (defense in depth; Gateway re-checks the binding)."""
    if not _valid_endpoint_id(payload.get("peer_endpoint_id")):
        return "endpoint_bind_required"
    addrs = payload.get("direct_addrs")
    if not isinstance(addrs, list) or not addrs:
        return "direct_addrs_required"
    try:
        g = _guard()
    except ImportError:
        return "guard_unavailable"
    try:
        g.check_direct_addrs(addrs)
    except g.GuardError as e:
        return e.reason
    return None


# ---- A4.2 iroh_pilot tickets: relay-only via our one relay (SPIKE_RELAY_ALLOW_URL) ----
def _iroh_pilot() -> bool:
    return os.environ.get("SPIKE_TRANSPORT", "").strip().lower() == "iroh_pilot"


def _a4_lane(g):
    """Python mirror takes the lane explicitly. SPIKE_A4_LANE=local only on the on-box a4 lanes."""
    return g.LANE_LOCAL if os.environ.get("SPIKE_A4_LANE", "").strip().lower() == "local" else g.LANE_PILOT


def _pilot_direct_addrs() -> list[str]:
    # Relay-only by default; any configured direct addr still goes through the A3.0 guard.
    raw = os.environ.get("SPIKE_IROH_GATEWAY_ADDR", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def _pilot_bind(peer_endpoint_id) -> tuple[dict | None, str | None, str | None]:
    """Returns (extra_payload, reason, detail). reason=None means OK."""
    if not _valid_endpoint_id(peer_endpoint_id):
        return None, "endpoint_bind_required", str(peer_endpoint_id)
    gw = os.environ.get("SPIKE_GATEWAY_ENDPOINT_ID", "").strip() or DEV_GATEWAY_ENDPOINT_ID
    if not _valid_endpoint_id(gw):
        return None, "gateway_endpoint_invalid", gw
    try:
        g = _guard()
    except ImportError:
        return None, "guard_unavailable", "scripts/spike_private_guard.py"
    addrs = _pilot_direct_addrs()
    try:
        allow = g.relay_allow_from_env(_a4_lane(g))
        if allow is None:
            return None, "relay_config", "SPIKE_RELAY_ALLOW_URL unset"
        relay_url = allow.url()
        g.check_relay_url_with(relay_url, allow)
        g.check_relay_required(addrs, relay_url)
        if addrs:
            g.check_direct_addrs(addrs)
    except g.GuardError as e:
        return None, e.reason, e.log_line()
    return {"gateway_endpoint_id": gw, "direct_addrs": addrs, "relay_url": relay_url}, None, None


def _pilot_verify(payload: dict) -> str | None:
    if not _valid_endpoint_id(payload.get("peer_endpoint_id")):
        return "endpoint_bind_required"
    addrs = payload.get("direct_addrs")
    if not isinstance(addrs, list):
        return "relay_required"
    try:
        g = _guard()
    except ImportError:
        return "guard_unavailable"
    try:
        allow = g.relay_allow_from_env(_a4_lane(g))
        relay_url = payload.get("relay_url")
        g.check_relay_required(addrs, relay_url)
        if not relay_url:
            return "relay_required"
        g.check_relay_url_with(relay_url, allow)
        if addrs:
            g.check_direct_addrs(addrs)
    except g.GuardError as e:
        return e.reason
    return None


def _denylist_version() -> int:
    with _lock:
        c = _conn()
        row = c.execute("SELECT version FROM denylist_meta WHERE id=1").fetchone()
        c.close()
    return int(row["version"]) if row else 1


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
        if path == "/v1/admin/denylist":
            with _lock:
                c = _conn()
                rows = c.execute("SELECT host_pattern, port, reason FROM denylist").fetchall()
                ver = c.execute("SELECT version, updated_at FROM denylist_meta WHERE id=1").fetchone()
                c.close()
            return self._json(
                200,
                {
                    "version": int(ver["version"]) if ver else 1,
                    "updated_at": ver["updated_at"] if ver else None,
                    "entries": [
                        {"host_pattern": r["host_pattern"], "port": r["port"], "reason": r["reason"]}
                        for r in rows
                    ],
                },
            )
        if path.startswith("/v1/attribution/"):
            stream_id = path.split("/v1/attribution/", 1)[1]
            with _lock:
                c = _conn()
                row = c.execute("SELECT * FROM attribution WHERE stream_id=?", (stream_id,)).fetchone()
                c.close()
            if not row:
                return self._json(404, {"error": "not_found"})
            return self._json(200, dict(row))
        if path == "/v1/admin/abuse_cases":
            with _lock:
                c = _conn()
                rows = c.execute("SELECT * FROM abuse_cases ORDER BY created_at DESC").fetchall()
                c.close()
            return self._json(200, {"cases": [dict(r) for r in rows]})
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
                for t in ("quotes", "sessions", "ledger", "abuse_cases", "attribution"):
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

        if path == "/v1/peers/offline":
            # A3.4: Gateway reports a Peer connection gone (iroh_local). Ignored when the peer
            # has since re-enrolled with a different endpoint id (newer connection wins).
            peer_id = body.get("peer_id", "")
            eid = body.get("endpoint_id") or ""
            with _lock:
                c = _conn()
                cur = c.execute("UPDATE peers SET online=0 WHERE peer_id=? AND (?='' OR endpoint_id=?)",
                                (peer_id, eid, eid))
                changed = cur.rowcount
                c.commit()
                c.close()
            if changed:
                _emit("peer.offline", peer_id=peer_id, endpoint_id=eid, reason=body.get("reason", ""))
            return self._json(200, {"ok": True, "offline": bool(changed)})

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
            label = path.split("/")[3]
            return self._force_zero(label)

        if path.startswith("/v1/sessions/") and path.endswith("/settle"):
            label = path.split("/")[3]
            return self._settle(label, body)

        if path.startswith("/v1/admin/accounts/") and path.endswith("/patch"):
            aid = path.split("/")[4]
            with _lock:
                c = _conn()
                if "kyc_tier" in body:
                    c.execute("UPDATE accounts SET kyc_tier=? WHERE id=?", (int(body["kyc_tier"]), aid))
                if "aup_accepted" in body:
                    c.execute("UPDATE accounts SET aup_accepted=? WHERE id=?", (int(bool(body["aup_accepted"])), aid))
                if "balance_usd" in body:
                    c.execute("UPDATE accounts SET balance_usd=? WHERE id=?", (float(body["balance_usd"]), aid))
                c.commit()
                c.close()
            return self._json(200, {"patched": aid})

        if path.startswith("/v1/admin/accounts/") and path.endswith("/freeze"):
            aid = path.split("/")[4]
            case_id = "abuse_" + uuid.uuid4().hex[:10]
            with _lock:
                c = _conn()
                c.execute("UPDATE accounts SET frozen=1 WHERE id=?", (aid,))
                rows = c.execute(
                    "SELECT label, stream_id, peer_id FROM sessions WHERE account_id=? AND status='active'",
                    (aid,),
                ).fetchall()
                stream_ids = []
                for r in rows:
                    stream_ids.append(r["stream_id"])
                    c.execute(
                        "UPDATE sessions SET status='stopped', balance_state='stopped' WHERE stream_id=?",
                        (r["stream_id"],),
                    )
                    # preserve attribution metadata (HARDENING #4)
                    c.execute(
                        """INSERT INTO attribution(stream_id, account_id, peer_id, dest_host, dest_port, geo_tier, frozen_stop, ts)
                           VALUES(?,?,?,?,?,?,1,?)
                           ON CONFLICT(stream_id) DO UPDATE SET frozen_stop=1, ts=excluded.ts""",
                        (r["stream_id"], aid, r["peer_id"], "", 0, "city", time.time()),
                    )
                    _emit(
                        "account.frozen_stop",
                        account_id=aid,
                        stream_id=r["stream_id"],
                        attribution_preserved=True,
                        code="account_frozen",
                    )
                c.execute(
                    "INSERT INTO abuse_cases(id, account_id, source, status, actions, stream_ids, created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        case_id,
                        aid,
                        body.get("source", "admin_freeze"),
                        "open",
                        json.dumps(["freeze", "stop_inflight"]),
                        json.dumps(stream_ids),
                        time.time(),
                    ),
                )
                c.commit()
                c.close()
            return self._json(
                200,
                {
                    "frozen": aid,
                    "stopped": stream_ids,
                    "abuse_case_id": case_id,
                    "attribution_preserved": True,
                },
            )

        if path.startswith("/v1/admin/accounts/") and path.endswith("/unfreeze"):
            aid = path.split("/")[4]
            with _lock:
                c = _conn()
                c.execute("UPDATE accounts SET frozen=0 WHERE id=?", (aid,))
                c.commit()
                c.close()
            _emit("account.unfrozen", account_id=aid)
            return self._json(200, {"unfrozen": aid})

        if path == "/v1/admin/denylist":
            with _lock:
                c = _conn()
                if body.get("entries") is not None:
                    c.execute("DELETE FROM denylist")
                    for e in body["entries"]:
                        ports = e.get("ports") or [e.get("port", 443)]
                        for port in ports:
                            c.execute(
                                "INSERT INTO denylist(host_pattern,port,reason) VALUES(?,?,?)",
                                (e["host_pattern"], int(port), e.get("reason", "")),
                            )
                    # version bump (HARDENING #3)
                    if "version" in body:
                        ver = int(body["version"])
                    else:
                        cur = c.execute("SELECT version FROM denylist_meta WHERE id=1").fetchone()
                        ver = (int(cur["version"]) if cur else 1) + 1
                    c.execute(
                        "INSERT INTO denylist_meta(id, version, updated_at) VALUES(1, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at",
                        (ver, time.time()),
                    )
                    c.commit()
                    c.close()
                    _emit("denylist.updated", version=ver)
                    return self._json(200, {"ok": True, "version": ver})
                c.close()
            return self._json(400, {"error": "entries_required"})

        if path == "/v1/dest/check":
            host = body.get("host", "")
            port = int(body.get("port", 443))
            reason = _host_denied(host, port)
            if reason:
                return self._json(
                    403,
                    {
                        "allowed": False,
                        "code": "dest_denied",
                        "reason": reason,
                        "message": f"Destination blocked ({reason})",
                        "user_copy": "blocked",
                        "denylist_version": _denylist_version(),
                    },
                )
            return self._json(200, {"allowed": True, "denylist_version": _denylist_version()})

        if path == "/v1/usage/flush":
            return self._usage_flush(body)

        if path in ("/v1/tickets/verify", "/v1/admin/verify_ticket"):
            ok, why = verify_ticket(body.get("ticket_json", ""))
            return self._json(200 if ok else 403, {"ok": ok, "reason": why})

        if path == "/v1/mock/cashout":
            aid = body.get("account_id", "acct_demo")
            peer_id = body.get("peer_id", "peer_demo")
            with _lock:
                c = _conn()
                # accrued on peer side stored on demo account field for spike simplicity
                row = c.execute("SELECT accrued_payout_usd FROM accounts WHERE id=?", (aid,)).fetchone()
                # also allow peer-keyed accrual via accounts hack: use peer balance field on same row for spike
                accrued = row["accrued_payout_usd"] if row else 0
                # Prefer peer accrual table-less: read sum of peer_payout ledger
                s = c.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='peer_payout' AND peer_id=?",
                    (peer_id,),
                ).fetchone()[0]
                accrued = max(accrued, s)
                c.close()
            if accrued < 25:
                return self._json(
                    403,
                    {
                        "error": "below_minimum",
                        "min_usd": 25,
                        "accrued": accrued,
                        "user_copy": "Minimum cashout: $25",
                    },
                )
            return self._json(200, {"withdrawn": accrued, "rail": "mock_stripe_connect"})

        if path == "/v1/mock/topup":
            aid = body.get("account_id", "acct_demo")
            try:
                amount = float(body.get("amount_usd", 10.0))
            except (TypeError, ValueError):
                return self._json(400, {"error": "invalid_amount", "code": "invalid_amount"})
            if amount <= 0:
                return self._json(400, {"error": "invalid_amount", "code": "invalid_amount", "message": "amount_usd must be > 0"})
            with _lock:
                c = _conn()
                row = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
                if not row:
                    c.close()
                    return self._json(404, {"error": "account_not_found"})
                c.execute(
                    "UPDATE accounts SET balance_usd=balance_usd+? WHERE id=?",
                    (amount, aid),
                )
                # Do NOT auto-resume stopped sessions / free wind-down — top-up credits only.
                bal = c.execute("SELECT balance_usd FROM accounts WHERE id=?", (aid,)).fetchone()["balance_usd"]
                c.commit()
                c.close()
            user_copy = ["Add funds", "This is a test top-up", "Funds added"]
            body_lines = [
                "Add funds (mock)",
                "This is a test top-up — no real charge.",
                "Funds added. Prepaid balance updated.",
                "Reconnect or create a new session to keep going.",
            ]
            ux = "UX c2_add_funds_mock code=mock_topup"
            ev = _emit(
                "billing.mock_topup",
                code="mock_topup",
                account_id=aid,
                amount_usd=amount,
                balance_usd=bal,
                screen="c2_add_funds_mock",
                rail="mock_stripe",
                user_copy=user_copy,
            )
            return self._json(
                200,
                {
                    "ok": True,
                    "rail": "mock_stripe",
                    "amount_usd": amount,
                    "balance_usd": bal,
                    "code": "mock_topup",
                    "screen": "c2_add_funds_mock",
                    "user_copy": user_copy,
                    "body_lines": body_lines,
                    "ux": ux,
                    "event": ev,
                },
            )

        if path.startswith("/v1/sessions/") and path.count("/") == 3:
            # DELETE alternative via POST end
            pass

        return self._json(404, {"error": "not_found", "path": path})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/v1/sessions/"):
            label = path.split("/v1/sessions/", 1)[1]
            return self._settle(label, {"reason": "client_delete"})
        return self._json(404, {"error": "not_found"})

    def _match(self, body: dict):
        account_id = body.get("account_id", "acct_demo")
        rematch = body.get("rematch_mode", "city")
        geo = body.get("geo") or {"country": "US", "city": "new_orleans"}
        with _lock:
            c = _conn()
            acct = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not acct:
                c.close()
                return self._json(404, {"error": "account_not_found"})
            if not acct["aup_accepted"]:
                c.close()
                return self._json(403, {"error": "aup_required", "code": "aup_required"})
            if acct["frozen"]:
                c.close()
                return self._json(403, {"error": "frozen", "code": "account_frozen"})
            if rematch == "strict" and acct["kyc_tier"] < 2:
                c.close()
                return self._json(403, {"error": "kyc_insufficient", "code": "kyc_insufficient"})
            if rematch == "strict" and geo.get("country") and not geo.get("city"):
                c.close()
                return self._json(400, {"error": "selector_conflict", "code": "selector_conflict"})
            cap = _capacity()
            if rematch == "strict" and (cap["strict_kill"] or cap["strict_eligible_n"] < 25):
                c.close()
                return self._json(
                    503,
                    {
                        "error": "strict_unavailable",
                        "code": "strict_unavailable",
                        "capacity": cap,
                        "user_copy": [
                            "Nearby exits aren’t available here right now",
                            "Switch to City rematch",
                            "Try another city",
                        ],
                    },
                )
            if cap["country_sell_paused"]:
                c.close()
                return self._json(
                    503,
                    {"error": "capacity_country_paused", "code": "capacity_country_paused", "capacity": cap},
                )
            if cap.get("oversubscribed"):
                c.close()
                return self._json(
                    503,
                    {"error": "capacity_oversubscribed", "code": "capacity_oversubscribed", "capacity": cap},
                )
            # Lowest load wins; ties go to the least recently assigned Peer (A3.4 spreads sessions
            # across equal Peers; with one online Peer this is the old behaviour).
            online = c.execute("SELECT * FROM peers WHERE online=1 ORDER BY load ASC, rowid ASC").fetchall()
            peer = min(online, key=lambda r: (r["load"], _last_assigned.get(r["peer_id"], 0.0))) if online else None
            if peer is not None:
                _last_assigned[peer["peer_id"]] = time.monotonic()
            if not peer:
                # allow match against demo peer even if offline for quote; gateway/peer must enroll
                peer = c.execute("SELECT * FROM peers WHERE peer_id='peer_demo'").fetchone()
            price_mult = 1.5 if geo.get("city") else 1.0
            if rematch == "strict":
                price_mult = 2.0
            price = 1.75 * price_mult
            qid = "q_" + uuid.uuid4().hex[:12]
            exclusive = bool(body.get("exclusive"))
            hold = time.time() + 300 if exclusive else None
            c.execute(
                """INSERT INTO quotes(quote_id,account_id,peer_id,endpoint_id,geo_json,rematch_mode,price_mult,price_per_gb,exclusive,hold_expires_at,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    qid,
                    account_id,
                    peer["peer_id"],
                    peer["endpoint_id"],
                    json.dumps(geo),
                    rematch,
                    price_mult,
                    price,
                    int(exclusive),
                    hold,
                    time.time(),
                ),
            )
            c.commit()
            c.close()
        return self._json(
            200,
            {
                "quote_id": qid,
                "peer_id": peer["peer_id"],
                "iroh_endpoint_id": peer["endpoint_id"],
                "assigned_geo": geo,
                "price_mult": price_mult,
                "price_per_gb": price,
                "hold_expires_at": hold,
                "capacity": cap,
                "alpn": "stream/tunnel/1",
            },
        )

    def _session_start(self, body: dict):
        quote_id = body.get("quote_id")
        label = body.get("session_label") or body.get("label") or ("sess_" + uuid.uuid4().hex[:8])
        with _lock:
            c = _conn()
            q = c.execute("SELECT * FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
            if not q:
                c.close()
                return self._json(404, {"error": "quote_not_found"})
            acct = c.execute("SELECT * FROM accounts WHERE id=?", (q["account_id"],)).fetchone()
            if acct["frozen"]:
                c.close()
                return self._json(403, {"error": "frozen", "code": "account_frozen"})
            if acct["balance_usd"] <= 0:
                c.close()
                return self._json(402, {"error": "insufficient_balance"})
            iroh_extra = None
            if _iroh_local() or _iroh_pilot():
                # A3.3: bind the Peer's enrolled (Gateway-authenticated) endpoint ID.
                # A4.2: iroh_pilot also binds our one relay (relay_url), direct_addrs may be empty.
                bind = _pilot_bind if _iroh_pilot() else _iroh_bind
                iroh_extra, why, detail = bind(q["endpoint_id"])
                if why:
                    c.close()
                    _emit("ticket.bind_refused", reason=why, peer_id=q["peer_id"], detail=detail)
                    return self._json(
                        422,
                        {"error": why, "code": "ticket_bind_refused", "reason": why, "detail": detail},
                    )
            sid = "s_" + uuid.uuid4().hex[:12]
            stream_id = "str_" + uuid.uuid4().hex[:12]
            gateway_endpoint_id = body.get("gateway_endpoint_id", "iroh_ep_gateway_spike")
            payload = {
                "session_id": sid,
                "stream_id": stream_id,
                "peer_endpoint_id": q["endpoint_id"],
                "gateway_endpoint_id": gateway_endpoint_id,
                "alpn": "stream/tunnel/1",
                "exp": time.time() + 120,
            }
            if iroh_extra:
                payload.update(iroh_extra)  # gateway_endpoint_id from env only, + guarded direct_addrs
            ticket = _sign_ticket(payload)
            c.execute(
                """INSERT INTO sessions(session_id,label,stream_id,account_id,peer_id,endpoint_id,rematch_mode,price_mult,price_per_gb,expires_at,status,balance_state,ticket_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid,
                    label,
                    stream_id,
                    q["account_id"],
                    q["peer_id"],
                    q["endpoint_id"],
                    q["rematch_mode"],
                    q["price_mult"],
                    q["price_per_gb"],
                    time.time() + 3600,
                    "active",
                    "ok",
                    ticket,
                ),
            )
            c.execute("DELETE FROM quotes WHERE quote_id=?", (quote_id,))
            c.commit()
            c.close()
        _emit("session.started", session_id=sid, stream_id=stream_id, label=label)
        return self._json(
            200,
            {
                "session_id": sid,
                "label": label,
                "stream_id": stream_id,
                "peer_id": q["peer_id"],
                "iroh_endpoint_id": q["endpoint_id"],
                "rematch_mode": q["rematch_mode"],
                "assigned_price_mult": q["price_mult"],
                "expires_at": time.time() + 3600,
                "status": "active",
                "balance_state": "ok",
                "grace": {
                    "max_bytes": GRACE_MAX_BYTES,
                    "max_ms": GRACE_MAX_MS,
                    "peer_paid": True,
                },
                "ticket": json.loads(ticket),
                "ticket_json": ticket,
                "alpn": "stream/tunnel/1",
                "auth_frame": "AUTH_TICKET",
            },
        )

    def _force_zero(self, label: str):
        with _lock:
            c = _conn()
            row = c.execute("SELECT * FROM sessions WHERE label=?", (label,)).fetchone()
            if not row:
                c.close()
                return self._json(404, {"error": "not_found"})
            c.execute("UPDATE accounts SET balance_usd=0 WHERE id=?", (row["account_id"],))
            c.execute(
                "UPDATE sessions SET balance_state='grace', grace_started_at=? WHERE label=?",
                (time.time(), label),
            )
            c.commit()
            c.close()
        ev = _emit(
            "balance.grace_enter",
            code="balance_grace",
            label=label,
            stream_id=row["stream_id"],
            screen="screen_1_grace",
            user_copy=["Balance hit zero", "Finishing this transfer", "Grace left"],
        )
        return self._json(200, {"balance_state": "grace", "event": ev})

    def _usage_flush(self, body: dict):
        stream_id = body["stream_id"]
        add_bytes = int(body.get("bytes", 0))
        with _lock:
            c = _conn()
            row = c.execute("SELECT * FROM sessions WHERE stream_id=?", (stream_id,)).fetchone()
            if not row:
                c.close()
                return self._json(404, {"error": "not_found"})
            if row["status"] != "active":
                c.close()
                return self._json(200, {"ignored": True, "status": row["status"]})
            acct = c.execute("SELECT frozen FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
            if acct and acct["frozen"]:
                c.execute(
                    "UPDATE sessions SET status='stopped', balance_state='stopped' WHERE stream_id=?",
                    (stream_id,),
                )
                c.commit()
                c.close()
                ev = _emit("account.frozen_stop", stream_id=stream_id, code="account_frozen")
                return self._json(200, {"balance_state": "stopped", "stop": True, "frozen": True, "event": ev})
            # optional mid-stream dest recheck (HARDENING #3)
            if body.get("dest_host"):
                reason = _host_denied(body["dest_host"], int(body.get("dest_port", 443)))
                if reason:
                    c.execute(
                        "UPDATE sessions SET status='stopped', balance_state='stopped' WHERE stream_id=?",
                        (stream_id,),
                    )
                    c.commit()
                    c.close()
                    ev = _emit("dest.denied_midstream", stream_id=stream_id, reason=reason, code="dest_denied")
                    return self._json(
                        200,
                        {"balance_state": "stopped", "stop": True, "dest_denied": True, "event": ev, "user_copy": "blocked"},
                    )
            state = row["balance_state"]
            grace_started = row["grace_started_at"]
            if state == "ok":
                c.execute(
                    "UPDATE sessions SET bytes_non_grace=bytes_non_grace+? WHERE stream_id=?",
                    (add_bytes, stream_id),
                )
                # debit lightly for demo: $0.001 per 10KB approx using price
                gb = add_bytes / (1024**3)
                debit = gb * row["price_per_gb"]
                c.execute(
                    "UPDATE accounts SET balance_usd=MAX(0, balance_usd-?) WHERE id=?",
                    (debit, row["account_id"]),
                )
                bal = c.execute(
                    "SELECT balance_usd FROM accounts WHERE id=?", (row["account_id"],)
                ).fetchone()["balance_usd"]
                if bal <= 0 and state == "ok":
                    c.execute(
                        "UPDATE sessions SET balance_state='grace', grace_started_at=? WHERE stream_id=?",
                        (time.time(), stream_id),
                    )
                    c.commit()
                    c.close()
                    ev = _emit(
                        "balance.grace_enter",
                        code="balance_grace",
                        stream_id=stream_id,
                        label=row["label"],
                        screen="screen_1_grace",
                        user_copy=["Balance hit zero", "Finishing this transfer", "Grace left"],
                    )
                    return self._json(200, {"balance_state": "grace", "event": ev})
            elif state == "grace":
                c.execute(
                    "UPDATE sessions SET bytes_grace=bytes_grace+? WHERE stream_id=?",
                    (add_bytes, stream_id),
                )
                row2 = c.execute("SELECT * FROM sessions WHERE stream_id=?", (stream_id,)).fetchone()
                elapsed_ms = (time.time() - (row2["grace_started_at"] or time.time())) * 1000
                if row2["bytes_grace"] >= GRACE_MAX_BYTES or elapsed_ms >= GRACE_MAX_MS:
                    c.execute(
                        "UPDATE sessions SET balance_state='stopped', status='stopped' WHERE stream_id=?",
                        (stream_id,),
                    )
                    c.commit()
                    c.close()
                    ev = _emit(
                        "balance.grace_exhausted",
                        code="balance_exhausted",
                        stream_id=stream_id,
                        label=row["label"],
                        screen="screen_2_exhausted",
                        user_copy=["Session stopped", "Prepaid balance empty", "Add funds"],
                    )
                    self._settle_locked(stream_id)
                    return self._json(
                        200,
                        {"balance_state": "stopped", "stop": True, "event": ev},
                    )
            c.commit()
            c.close()
        return self._json(200, {"ok": True, "balance_state": state})

    def _settle(self, label: str, body: dict):
        with _lock:
            c = _conn()
            row = c.execute("SELECT * FROM sessions WHERE label=?", (label,)).fetchone()
            if not row:
                c.close()
                return self._json(404, {"error": "not_found"})
            stream_id = row["stream_id"]
            c.execute(
                "UPDATE sessions SET status='stopped', balance_state='stopped' WHERE stream_id=?",
                (stream_id,),
            )
            c.commit()
            c.close()
        entries = self._settle_locked(stream_id)
        return self._json(200, {"stream_id": stream_id, "ledger": entries})

    def _settle_locked(self, stream_id: str):
        with _lock:
            c = _conn()
            row = c.execute("SELECT * FROM sessions WHERE stream_id=?", (stream_id,)).fetchone()
            if not row:
                c.close()
                return []
            non_g = row["bytes_non_grace"]
            grace_b = row["bytes_grace"]
            price = row["price_per_gb"]
            debit = (non_g / (1024**3)) * price
            peer_pay = ((non_g + grace_b) / (1024**3)) * (price * 0.25)  # country share scaffold
            # strict working 30% ignored in spike; fine
            absorb = (grace_b / (1024**3)) * price  # platform absorbs buyer side of grace
            margin = debit - peer_pay - 0  # absorb is COGS not customer credit

            def upsert(kind, amount, account_id=None, peer_id=None):
                eid = f"{stream_id}:{kind}"
                try:
                    c.execute(
                        "INSERT INTO ledger(entry_id,stream_id,kind,amount,account_id,peer_id,ts) VALUES(?,?,?,?,?,?,?)",
                        (eid, stream_id, kind, amount, account_id, peer_id, time.time()),
                    )
                except sqlite3.IntegrityError:
                    pass  # idempotent

            upsert("debit", debit, account_id=row["account_id"])
            upsert("peer_payout", peer_pay, peer_id=row["peer_id"])
            upsert("margin", margin, account_id=row["account_id"])
            if grace_b:
                upsert("grace_absorb", absorb, account_id=row["account_id"])
            c.execute(
                "UPDATE accounts SET accrued_payout_usd=accrued_payout_usd+? WHERE id=?",
                (peer_pay, row["account_id"]),
            )
            c.commit()
            rows = c.execute("SELECT * FROM ledger WHERE stream_id=?", (stream_id,)).fetchall()
            c.close()
        _emit("session.settled", stream_id=stream_id)
        return [dict(r) for r in rows]


def main():
    _init_db()
    # copy denylist next to module if fixtures mounted
    fixtures = Path("/fixtures/denylist.seed.json")
    if fixtures.exists():
        os.environ["SPIKE_DENYLIST"] = str(fixtures)
        with _lock:
            c = _conn()
            _load_denylist(c)
            c.close()
    print(f"control listening on {HOST}:{PORT} db={DB_PATH}", flush=True)
    ThreadingHTTPServer((HOST, int(PORT)), Handler).serve_forever()


if __name__ == "__main__":
    main()
