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
