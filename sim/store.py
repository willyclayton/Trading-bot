"""Persistence backends.

The authoritative record is one JSON snapshot of the whole simulator, so a
restart (or the next serverless invocation) resumes exactly where the last
one stopped, including the Auto flag.

* :class:`SqliteStore` — local development. Plain JSON in a ``state`` table
  plus normalised tables rewritten on every save so history can be browsed
  with ``sqlite3``.
* :class:`RedisStore` — Vercel. Upstash Redis over its REST API (no client
  library; ``urllib`` only), snapshot zlib-compressed. A short-lived ``NX``
  lock serialises writers, because two browser tabs or a cron tick can
  invoke the function concurrently.
* :class:`MemoryStore` — tests.

:func:`from_env` picks the backend: Redis when the Upstash / Vercel KV
environment variables are present, SQLite otherwise.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
import zlib
from contextlib import contextmanager
from pathlib import Path


class Busy(Exception):
    """Another writer holds the lock; the caller should retry later."""


def _encode(snapshot: dict) -> str:
    raw = json.dumps(snapshot, separators=(",", ":")).encode()
    return base64.b64encode(zlib.compress(raw, 6)).decode()


def _decode(blob: str) -> dict:
    return json.loads(zlib.decompress(base64.b64decode(blob)))


class BaseStore:
    shared = False   # True when other processes may write between our load and save

    def load(self) -> dict | None:
        raise NotImplementedError

    def save(self, snapshot: dict) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    @contextmanager
    def lock(self):
        yield

    def close(self) -> None:
        pass


class MemoryStore(BaseStore):
    def __init__(self):
        self.blob: str | None = None

    def load(self):
        return _decode(self.blob) if self.blob else None

    def save(self, snapshot):
        self.blob = _encode(snapshot)

    def reset(self):
        self.blob = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY, symbol TEXT, created_day INTEGER, status TEXT, notional REAL,
  qty REAL, signal_close REAL, stop_price REAL, target_price REAL, decided_by TEXT,
  reject_category TEXT, block_reason TEXT, fill_price REAL, drift_pct REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, client_order_id TEXT UNIQUE, symbol TEXT, side TEXT, qty REAL,
  kind TEXT, status TEXT, submitted_day INTEGER, filled_day INTEGER, filled_price REAL,
  reference_price REAL, fees REAL, slippage REAL);
CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY, book TEXT, symbol TEXT, qty REAL, entry_date TEXT, entry_price REAL,
  exit_date TEXT, exit_price REAL, exit_reason TEXT, gross_pnl REAL, slippage REAL,
  fees REAL, net_pnl REAL, r_multiple REAL, hold_days INTEGER);
CREATE TABLE IF NOT EXISTS equity (
  book TEXT, day INTEGER, date TEXT, equity REAL, cash REAL, hwm REAL, PRIMARY KEY (book, day));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, ts REAL, day INTEGER, date TEXT, event TEXT, detail TEXT);
"""


class SqliteStore(BaseStore):
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    @contextmanager
    def lock(self):
        with self._lock:
            yield

    def load(self) -> dict | None:
        row = self.conn.execute("SELECT value FROM state WHERE key='simulator'").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, snap: dict) -> None:
        with self._lock, self.conn:
            c = self.conn
            c.execute("INSERT OR REPLACE INTO state VALUES ('simulator', ?)", (json.dumps(snap),))
            c.execute("DELETE FROM proposals")
            c.executemany(
                "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(p["id"], p["symbol"], p["created_day"], p["status"], p["notional"], p["qty"],
                  p["signal_close"], p["stop_price"], p["target_price"], p["decided_by"],
                  p["reject_category"], p["block_reason"], p["fill_price"], p["drift_pct"], p["reason"])
                 for p in snap["proposals"]])
            c.execute("DELETE FROM orders")
            c.executemany(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(o["id"], o["client_order_id"], o["symbol"], o["side"], o["qty"], o["kind"],
                  o["status"], o["submitted_day"], o["filled_day"], o["filled_price"],
                  o["reference_price"], o["fees"], o["slippage"]) for o in snap["broker"]["orders"]])
            c.execute("DELETE FROM trades")
            c.executemany(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(t["id"], t["book"], t["symbol"], t["qty"], t["entry_date"], t["entry_price"],
                  t["exit_date"], t["exit_price"], t["exit_reason"], t["gross_pnl"], t["slippage"],
                  t["fees"], t["net_pnl"], t["r_multiple"], t["hold_days"])
                 for book in ("real", "shadow") for t in snap[book]["trades"]])
            c.execute("DELETE FROM equity")
            c.executemany(
                "INSERT INTO equity VALUES (?,?,?,?,?,?)",
                [(book, *row) for book in ("real", "shadow") for row in snap[book]["curve"]])
            c.executemany(
                "INSERT OR IGNORE INTO audit VALUES (?,?,?,?,?,?)",
                [(a["id"], a["ts"], a["day"], a["date"], a["event"], a["detail"]) for a in snap["audit"]])

    def reset(self) -> None:
        with self._lock, self.conn:
            for t in ("state", "proposals", "orders", "trades", "equity", "audit"):
                self.conn.execute(f"DELETE FROM {t}")

    def close(self) -> None:
        self.conn.close()


class RedisStore(BaseStore):
    """Upstash Redis via REST. Works with the variables Vercel injects for
    "Upstash for Redis" (``KV_REST_API_URL`` / ``KV_REST_API_TOKEN``) or
    Upstash's own (``UPSTASH_REDIS_REST_URL`` / ``UPSTASH_REDIS_REST_TOKEN``)."""

    shared = True
    LOCK_MS = 15_000
    _RELEASE = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"

    def __init__(self, url: str, token: str, key: str = "sim:snapshot", timeout: float = 8.0):
        self.url = url.rstrip("/")
        self.token = token
        self.key = key
        self.lock_key = key + ":lock"
        self.timeout = timeout

    def command(self, *args):
        body = json.dumps([str(a) for a in args]).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"redis {args[0]}: HTTP {e.code} {e.read()[:200]!r}") from e
        if isinstance(payload, dict) and "error" in payload:
            raise RuntimeError(f"redis {args[0]}: {payload['error']}")
        return payload["result"]

    def load(self) -> dict | None:
        blob = self.command("GET", self.key)
        return _decode(blob) if blob else None

    def save(self, snapshot: dict) -> None:
        self.command("SET", self.key, _encode(snapshot))

    def reset(self) -> None:
        self.command("DEL", self.key)

    @contextmanager
    def lock(self):
        token = secrets.token_hex(8)
        if self.command("SET", self.lock_key, token, "NX", "PX", self.LOCK_MS) != "OK":
            raise Busy("another request is updating the simulator")
        try:
            yield
        finally:
            self.command("EVAL", self._RELEASE, 1, self.lock_key, token)


def from_env(default_db: str = "sim.db") -> BaseStore:
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if url and token:
        return RedisStore(url, token, key=os.environ.get("SIM_STATE_KEY", "sim:snapshot"))
    return SqliteStore(os.environ.get("SIM_DB", default_db))
