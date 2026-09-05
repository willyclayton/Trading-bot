"""SQLite persistence (plan §3: "State in SQLite").

The authoritative record is a JSON snapshot of the whole simulator in the
``state`` table, so a restart resumes exactly where it stopped, including
the Auto flag. Normalised tables are rewritten on every save so history can
be browsed with plain ``sqlite3`` as well as through the UI.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

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


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def load(self) -> dict | None:
        row = self.conn.execute("SELECT value FROM state WHERE key='simulator'").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, sim) -> None:
        snap = sim.to_dict()
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
                [(book, e["day"], e["date"], e["equity"], e["cash"], e["hwm"])
                 for book in ("real", "shadow") for e in snap[book]["curve"]])
            c.executemany(
                "INSERT OR IGNORE INTO audit VALUES (?,?,?,?,?,?)",
                [(a["id"], a["ts"], a["day"], a["date"], a["event"], a["detail"]) for a in snap["audit"]])

    def reset(self) -> None:
        with self._lock, self.conn:
            for t in ("state", "proposals", "orders", "trades", "equity", "audit"):
                self.conn.execute(f"DELETE FROM {t}")

    def close(self) -> None:
        self.conn.close()
