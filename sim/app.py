"""Request-level application logic, shared by the local server and the
Vercel function.

Every call loads the simulator from the store, mutates it, saves it. On a
single-process store (SQLite) the loaded object is cached; on a shared store
(Redis, many serverless instances) it is re-read under a lock every time.

Auto mode is tick-driven: :meth:`App.tick` advances one day *if* Auto is on
and otherwise does nothing. The browser calls it on an interval while the
switch is on; a daily cron can call it too. Nothing has to stay running.
"""
from __future__ import annotations

import time

from .approval import REJECT_CATEGORIES, IllegalTransition, TokenError
from .engine import Config, Simulator
from .market import UNIVERSE
from .store import BaseStore, Busy

MAX_ROWS = 400


class App:
    def __init__(self, store: BaseStore, seed: int = 7):
        self.store = store
        self.default_seed = seed
        self._cache: Simulator | None = None
        self.backend = type(store).__name__.removesuffix("Store").lower()

    # -- load / save ---------------------------------------------------------
    def _load(self) -> Simulator:
        if self._cache is not None and not self.store.shared:
            return self._cache
        snap = self.store.load()
        if snap:
            sim = Simulator.from_dict(snap)
        else:
            sim = Simulator(Config(seed=self.default_seed))
            self.store.save(sim.to_dict())
        if not self.store.shared:
            self._cache = sim
        return sim

    def _save(self, sim: Simulator) -> None:
        self.store.save(sim.to_dict())

    # -- operations ----------------------------------------------------------
    def state(self) -> dict:
        with self.store.lock():
            return build_state(self._load(), self.backend)

    def tick(self) -> dict:
        """One Auto heartbeat: step a day if Auto is on. Safe to call any time."""
        with self.store.lock():
            sim = self._load()
            ticked = False
            if sim.auto and not sim.halted:
                sim.step()
                self._save(sim)
                ticked = True
            out = build_state(sim, self.backend)
            out["ticked"] = ticked
            return out

    def act(self, action: str, body: dict) -> dict:
        with self.store.lock():
            sim = self._load()
            if action == "step":
                n = max(1, min(int(body.get("days", 1)), 500))
                last = None
                for _ in range(n):
                    last = sim.step()
                result = {"stepped": n, "last": last}
            elif action == "approve":
                p = sim.approve(body["id"], body["token"], by=body.get("by", "human"))
                result = {"proposal": p.to_dict()}
            elif action == "reject":
                p = sim.reject(body["id"], body["token"], body.get("category", "discretion"),
                               body.get("note", ""), by=body.get("by", "human"))
                result = {"proposal": p.to_dict()}
            elif action == "auto":
                sim.set_auto(bool(body.get("on")))
                result = {"auto": sim.auto}
            elif action == "resume":
                sim.resume_entries()
                result = {"paused": sim.paused}
            elif action == "reset":
                seed = int(body.get("seed", self.default_seed))
                keep_auto = sim.auto
                self.store.reset()
                self._cache = None
                sim = Simulator(Config(seed=seed))
                if keep_auto:
                    sim.set_auto(True)
                if not self.store.shared:
                    self._cache = sim
                result = {"reset": True, "seed": seed}
            else:
                raise KeyError(action)
            self._save(sim)
            result["state"] = build_state(sim, self.backend)
            return result

    # -- HTTP-agnostic routing -------------------------------------------------
    def handle(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        """Route an API request. Returns (status, payload). Used by both servers."""
        body = body or {}
        if not path.startswith("/api/"):
            return 404, {"error": "not found"}
        action = path[len("/api/"):].strip("/")
        try:
            if action == "state" and method == "GET":
                return 200, self.state()
            if action == "tick" and method in ("GET", "POST"):
                return 200, self.tick()
            if method != "POST":
                return 405, {"error": "method not allowed"}
            return 200, self.act(action, body)
        except Busy as e:
            return 423, {"error": str(e)}
        except KeyError as e:
            return 404, {"error": f"unknown action or id: {e}"}
        except (TokenError, IllegalTransition) as e:
            return 409, {"error": str(e)}
        except (ValueError, TypeError) as e:
            return 400, {"error": str(e)}


def build_state(s: Simulator, backend: str = "") -> dict:
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
        "backend": backend,
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


def _decimate(curve: list[dict], limit: int = 800) -> list[dict]:
    if len(curve) <= limit:
        return curve
    stride = len(curve) / limit
    picked = [curve[int(i * stride)] for i in range(limit)]
    if picked[-1] is not curve[-1]:
        picked.append(curve[-1])
    return picked
