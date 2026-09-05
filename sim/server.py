"""HTTP server for the simulator console.

    python -m sim.server [--port 8000] [--db sim.db] [--seed 7]

Standard library only. State lives in SQLite and survives restarts,
including the Auto flag: if Auto was on when the process stopped, it is on
when it comes back.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .approval import IllegalTransition, TokenError, REJECT_CATEGORIES
from .engine import Simulator, Config
from .market import UNIVERSE
from .store import Store

STATIC = Path(__file__).parent / "static"
MAX_ROWS = 400


class App:
    def __init__(self, db_path: str, seed: int):
        self.store = Store(db_path)
        self.lock = threading.RLock()
        self.default_seed = seed
        snap = self.store.load()
        self.sim = Simulator.from_dict(snap) if snap else Simulator(Config(seed=seed))
        if not snap:
            self.store.save(self.sim)
        self._stop = threading.Event()
        self.auto_thread = threading.Thread(target=self._auto_loop, name="auto", daemon=True)
        self.auto_thread.start()

    # -- auto mode -----------------------------------------------------------
    def _auto_loop(self) -> None:
        while not self._stop.is_set():
            interval = self.sim.config.auto_interval_s
            if self.sim.auto:
                with self.lock:
                    self.sim.step()
                    self.store.save(self.sim)
            self._stop.wait(interval)

    def shutdown(self) -> None:
        self._stop.set()

    # -- API -----------------------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            s = self.sim
            props = sorted(s.proposals.values(), key=lambda p: (p.created_day, p.id), reverse=True)
            pending = [p.to_dict(include_token=True) for p in props if p.status == "pending"]
            orders = sorted(s.broker.orders.values(), key=lambda o: o.id, reverse=True)
            quotes = []
            for sym, *_ in UNIVERSE:
                bars = s.market.bars[sym]
                last, prev = bars[-1], bars[-2]
                quotes.append({"symbol": sym, "close": last.close, "open": last.open,
                               "change_pct": round((last.close / prev.close - 1) * 100, 2),
                               "spark": [b.close for b in bars[-40:]]})
            return {
                "stats": s.stats(),
                "config": s.config.to_dict(),
                "server_time": time.time(),
                "quotes": quotes,
                "positions": [p.to_dict() for p in s.real.positions.values()],
                "shadow_positions": [p.to_dict() for p in s.shadow.positions.values()],
                "pending": pending,
                "proposals": [p.to_dict() for p in props[:MAX_ROWS]],
                "orders": [o.to_dict() for o in orders[:MAX_ROWS]],
                "trades": [t.to_dict() for t in reversed(s.real.trades)][:MAX_ROWS],
                "shadow_trades": [t.to_dict() for t in reversed(s.shadow.trades)][:MAX_ROWS],
                "curve": _decimate(s.real.curve),
                "shadow_curve": _decimate(s.shadow.curve),
                "audit": list(reversed(s.audit[-MAX_ROWS:])),
                "reject_categories": list(REJECT_CATEGORIES),
            }

    def act(self, action: str, body: dict) -> dict:
        with self.lock:
            s = self.sim
            if action == "step":
                n = max(1, min(int(body.get("days", 1)), 500))
                out = [s.step() for _ in range(n)]
                result = {"stepped": n, "last": out[-1]}
            elif action == "approve":
                p = s.approve(body["id"], body["token"], by=body.get("by", "human"))
                result = {"proposal": p.to_dict()}
            elif action == "reject":
                p = s.reject(body["id"], body["token"], body.get("category", "discretion"),
                             body.get("note", ""), by=body.get("by", "human"))
                result = {"proposal": p.to_dict()}
            elif action == "auto":
                s.set_auto(bool(body.get("on")))
                result = {"auto": s.auto}
            elif action == "resume":
                s.resume_entries()
                result = {"paused": s.paused}
            elif action == "reset":
                seed = int(body.get("seed", self.default_seed))
                keep_auto = s.auto
                self.store.reset()
                self.sim = s = Simulator(Config(seed=seed))
                if keep_auto:
                    s.set_auto(True)
                result = {"reset": True, "seed": seed}
            else:
                raise KeyError(action)
            self.store.save(s)
            return result


def _decimate(curve: list[dict], limit: int = 800) -> list[dict]:
    if len(curve) <= limit:
        return curve
    stride = len(curve) / limit
    picked = [curve[int(i * stride)] for i in range(limit)]
    if picked[-1] is not curve[-1]:
        picked.append(curve[-1])
    return picked


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TradingBotSim/0.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, payload, ctype="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send(200, app.state())
            if path == "/":
                path = "/index.html"
            file = (STATIC / path.lstrip("/")).resolve()
            if STATIC.resolve() in file.parents and file.is_file():
                ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                return self._send(200, file.read_bytes(), ctype)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON"})
            action = path[len("/api/"):]
            try:
                result = app.act(action, body)
            except KeyError as e:
                return self._send(404, {"error": f"unknown action or id: {e}"})
            except (TokenError, IllegalTransition) as e:
                return self._send(409, {"error": str(e)})
            except (ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            self._send(200, result)

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trading-bot simulator console")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default="sim.db")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    app = App(args.db, args.seed)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"simulator console on http://{args.host}:{args.port}  (db: {args.db}, auto={'ON' if app.sim.auto else 'off'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        app.store.save(app.sim)
        app.store.close()


if __name__ == "__main__":
    main()
