"""The simulator: one object that runs the plan's daily cadence.

One call to :meth:`Simulator.step` advances one trading day:

    open  of day t+1   TTL sweep -> approved proposals: risk gate AGAIN ->
                       drift check (resize or STALE) -> idempotent DAY order
                       -> broker fills entries and queued exits at the open
    close of day t+1   mark positions -> exit rules -> exit orders for next
                       open -> drawdown / kill checks -> new signals ->
                       validate -> risk gate -> PENDING proposals
    evening            human (or Auto) decides on PENDING proposals

The shadow book takes every proposal as if approved at the modelled fill,
so the gap between it and the real book is the cost of human filtering
(plan §3, "Human filtering is a bias").
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

from .approval import Proposal, IllegalTransition, TokenError, REJECT_CATEGORIES
from .broker import SimBroker, Order
from .costs import CostModel
from .market import Market, UNIVERSE


@dataclass
class Config:
    seed: int = 7
    starting_equity: float = 1000.0
    risk_pct: float = 0.01
    stop_pct: float = 0.05
    target_r: float = 2.0
    max_hold_days: int = 10
    max_position_pct: float = 0.25
    max_positions: int = 4
    max_entries_per_day: int = 2
    max_gap: float = 0.02
    min_rr: float = 1.5
    drawdown_pause_pct: float = 0.15
    kill_pct: float = 0.30
    slippage_bps: float = 5.0
    charge_fees: bool = True
    sma_len: int = 10
    dip_pct: float = 0.02
    lookback_low: int = 5
    auto_interval_s: float = 1.5

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_day: int
    entry_date: str
    stop_price: float
    target_price: float
    max_hold_days: int
    proposal_id: str
    entry_ref: float                    # printed open; entry_price includes slippage
    entry_fees: float
    exit_pending: Optional[str] = None   # reason, once an exit order is queued
    last_close: float = 0.0

    def market_value(self) -> float:
        return self.qty * self.last_close

    def unrealized(self) -> float:
        return self.qty * (self.last_close - self.entry_price) - self.entry_fees

    def to_dict(self) -> dict:
        d = asdict(self)
        d["market_value"] = round(self.market_value(), 2)
        d["unrealized"] = round(self.unrealized(), 2)
        d["unrealized_pct"] = round((self.last_close / self.entry_price - 1) * 100, 2) if self.entry_price else 0.0
        return d


@dataclass
class Trade:
    id: str
    symbol: str
    proposal_id: str
    qty: float
    entry_day: int
    entry_date: str
    entry_price: float
    exit_day: int
    exit_date: str
    exit_price: float
    exit_reason: str
    gross_pnl: float        # on printed opens, before friction
    slippage: float
    fees: float
    costs: float            # slippage + fees
    net_pnl: float          # what the account actually made
    r_multiple: float
    hold_days: int
    book: str = "real"

    def to_dict(self) -> dict:
        return asdict(self)


class Portfolio:
    """Cash + positions + closed trades + equity curve. One per book."""

    def __init__(self, name: str, cash: float):
        self.name = name
        self.cash = cash
        self.start_equity = cash
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.curve: list[dict] = []
        self.hwm = cash
        self.total_costs = 0.0

    def equity(self) -> float:
        return self.cash + sum(p.market_value() for p in self.positions.values())

    def mark(self, closes: dict[str, float]) -> None:
        for p in self.positions.values():
            p.last_close = closes[p.symbol]

    def open_position(self, prop: Proposal, qty: float, ref: float, price: float, fees: float,
                      day: int, date_s: str) -> Position:
        """``ref`` is the printed open, ``price`` the fill after slippage."""
        self.cash -= qty * price + fees
        self.total_costs += fees + qty * (price - ref)
        # Stop/target travel with the actual fill so risk stays 1% after drift.
        stop = round(price * (prop.stop_price / prop.signal_close), 4)
        target = round(price * (prop.target_price / prop.signal_close), 4)
        pos = Position(symbol=prop.symbol, qty=qty, entry_price=price, entry_day=day,
                       entry_date=date_s, stop_price=stop, target_price=target,
                       max_hold_days=prop.max_hold_days, proposal_id=prop.id,
                       entry_ref=ref, entry_fees=fees, last_close=price)
        self.positions[prop.symbol] = pos
        return pos

    def close_position(self, sym: str, ref: float, price: float, fees: float, day: int,
                       date_s: str, reason: str, risk_dollars: float) -> Trade:
        p = self.positions.pop(sym)
        self.cash += p.qty * price - fees
        slippage = p.qty * (p.entry_price - p.entry_ref) + p.qty * (ref - price)
        all_fees = p.entry_fees + fees
        self.total_costs += fees + p.qty * (ref - price)
        gross = p.qty * (ref - p.entry_ref)
        net = p.qty * (price - p.entry_price) - all_fees
        t = Trade(id=str(uuid.uuid4())[:8], symbol=sym, proposal_id=p.proposal_id, qty=p.qty,
                  entry_day=p.entry_day, entry_date=p.entry_date, entry_price=p.entry_price,
                  exit_day=day, exit_date=date_s, exit_price=price, exit_reason=reason,
                  gross_pnl=round(gross, 2), slippage=round(slippage, 2), fees=round(all_fees, 2),
                  costs=round(slippage + all_fees, 2), net_pnl=round(net, 2),
                  r_multiple=round(net / risk_dollars, 2) if risk_dollars else 0.0,
                  hold_days=day - p.entry_day, book=self.name)
        self.trades.append(t)
        return t

    def record_curve(self, day: int, date_s: str) -> None:
        eq = self.equity()
        self.hwm = max(self.hwm, eq)
        self.curve.append({"day": day, "date": date_s, "equity": round(eq, 2),
                           "cash": round(self.cash, 2), "hwm": round(self.hwm, 2)})

    def stats(self) -> dict:
        eq = self.equity()
        n = len(self.trades)
        wins = [t for t in self.trades if t.net_pnl > 0]
        return {
            "equity": round(eq, 2),
            "cash": round(self.cash, 2),
            "invested": round(eq - self.cash, 2),
            "hwm": round(self.hwm, 2),
            "drawdown_pct": round((eq / self.hwm - 1) * 100, 2) if self.hwm else 0.0,
            "pnl": round(eq - self.start_equity, 2),
            "pnl_pct": round((eq / self.start_equity - 1) * 100, 2),
            "n_trades": n,
            "win_rate": round(len(wins) / n * 100, 1) if n else None,
            "avg_r": round(sum(t.r_multiple for t in self.trades) / n, 2) if n else None,
            "total_r": round(sum(t.r_multiple for t in self.trades), 2),
            "total_costs": round(self.total_costs, 2),
            "open_positions": len(self.positions),
        }

    def to_dict(self) -> dict:
        return {"name": self.name, "cash": self.cash, "start_equity": self.start_equity,
                "positions": {s: asdict(p) for s, p in self.positions.items()},
                "trades": [t.to_dict() for t in self.trades], "curve": self.curve,
                "hwm": self.hwm, "total_costs": self.total_costs}

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        p = cls(d["name"], d["cash"])
        p.start_equity = d["start_equity"]
        p.positions = {s: Position(**pd) for s, pd in d["positions"].items()}
        p.trades = [Trade(**t) for t in d["trades"]]
        p.curve = d["curve"]
        p.hwm = d["hwm"]
        p.total_costs = d["total_costs"]
        return p


class Simulator:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        c = self.config
        self.costs = CostModel(slippage_bps=c.slippage_bps, charge_fees=c.charge_fees)
        self.market = Market(seed=c.seed)
        self.broker = SimBroker(self.costs)
        self.real = Portfolio("real", c.starting_equity)
        self.shadow = Portfolio("shadow", c.starting_equity)
        self.proposals: dict[str, Proposal] = {}
        self.shadow_queue: list[str] = []      # proposal ids the shadow book fills next open
        self.exit_queue: dict[str, tuple[str, str]] = {}   # sym -> (order_id, reason) for real
        self.shadow_exits: dict[str, str] = {}             # sym -> reason
        self.audit: list[dict] = []
        self.auto = False
        self.paused = False
        self.halted = False
        self.kill_triggered = False
        self.heartbeat_day: Optional[int] = None
        self.steps = 0
        self.created_at = time.time()
        self._seq = 0
        self.log("boot", f"simulator created, seed={c.seed}, equity=${c.starting_equity:,.0f}")
        self._mark_all()
        self.real.record_curve(self.day, self.date)
        self.shadow.record_curve(self.day, self.date)

    # -- helpers -----------------------------------------------------------
    @property
    def day(self) -> int:
        return self.market.day

    @property
    def date(self) -> str:
        return self.market.date_for(self.day).isoformat()

    def log(self, event: str, detail: str, **extra) -> None:
        self._seq += 1
        row = {"id": self._seq, "ts": time.time(), "day": self.day, "date": self.date,
               "event": event, "detail": detail}
        row.update(extra)
        self.audit.append(row)
        # The SQLite audit table is append-only and keeps everything; the
        # in-memory tail is bounded so the snapshot stays small on long soaks.
        if len(self.audit) > 6000:
            del self.audit[:1000]

    def _closes(self) -> dict[str, float]:
        return {s: self.market.last(s).close for s, *_ in UNIVERSE}

    def _opens(self) -> dict[str, float]:
        return {s: self.market.last(s).open for s, *_ in UNIVERSE}

    def _mark_all(self) -> None:
        closes = self._closes()
        self.real.mark(closes)
        self.shadow.mark(closes)

    COMMITTED = ("pending", "approved", "submitted")

    def _committed(self, exclude: Optional[str] = None) -> list[Proposal]:
        return [p for p in self.proposals.values() if p.status in self.COMMITTED and p.id != exclude]

    def _committed_notional(self, exclude: Optional[str] = None) -> float:
        return sum(p.notional for p in self._committed(exclude))

    def _active_symbols(self) -> set[str]:
        s = set(self.real.positions)
        s |= {p.symbol for p in self.proposals.values() if p.status in ("pending", "approved", "submitted")}
        return s

    # -- risk gate ---------------------------------------------------------
    def risk_gate(self, prop: Proposal, book: Portfolio, entries_today: int, at: str) -> Optional[str]:
        """Return a block reason, or None if the proposal passes."""
        c = self.config
        eq = book.equity()
        if self.halted:
            return "kill switch active: no new entries"
        if self.paused and at == "close":
            return "drawdown pause active: no new proposals until manual review"
        if self.paused and at == "open":
            return "drawdown pause active at submit"
        committed = self._committed(exclude=prop.id)
        if len(book.positions) + len(committed) >= c.max_positions:
            return f"max concurrent positions ({c.max_positions}) reached"
        if entries_today >= c.max_entries_per_day:
            return f"max entries per day ({c.max_entries_per_day}) reached"
        if prop.notional > c.max_position_pct * eq + 1e-6:
            return f"notional ${prop.notional:.2f} > {c.max_position_pct:.0%} of equity"
        reserved = sum(p.notional for p in committed)
        if prop.notional + reserved > book.cash + 1e-6:
            return f"insufficient cash: need ${prop.notional:.2f}, have ${book.cash - reserved:.2f} uncommitted"
        rr = (prop.target_price - prop.signal_close) / (prop.signal_close - prop.stop_price)
        if rr < c.min_rr:
            return f"reward:risk {rr:.2f} < {c.min_rr}"
        return None

    @staticmethod
    def validate(prop: Proposal) -> Optional[str]:
        if prop.symbol not in {s for s, *_ in UNIVERSE}:
            return "symbol not in universe"
        if not (prop.stop_price < prop.signal_close < prop.target_price):
            return "inverted stop/target"
        if prop.qty <= 0 or prop.notional <= 0:
            return "non-positive size"
        return None

    # -- strategy ----------------------------------------------------------
    def signals(self) -> list[dict]:
        """Hand-written long-only mean-reversion rule on completed daily bars."""
        c = self.config
        out = []
        for sym, *_ in UNIVERSE:
            closes = self.market.closes(sym, c.sma_len)
            if len(closes) < c.sma_len:
                continue
            sma = sum(closes) / len(closes)
            close = closes[-1]
            recent = self.market.closes(sym, c.lookback_low)
            dip = close / sma - 1
            if dip <= -c.dip_pct and close <= min(recent):
                out.append({"symbol": sym, "close": close, "sma": sma,
                            "reason": f"close {dip * 100:.1f}% below {c.sma_len}d SMA and a {c.lookback_low}-day low"})
        return out

    def _build_proposal(self, sig: dict) -> Proposal:
        c = self.config
        risk = round(self.real.equity() * c.risk_pct, 2)
        close = sig["close"]
        stop = round(close * (1 - c.stop_pct), 4)
        target = round(close * (1 + c.stop_pct * c.target_r), 4)
        qty = risk / (close - stop)
        notional = qty * close
        cap = c.max_position_pct * self.real.equity()
        if notional > cap:
            notional = cap
            qty = notional / close
        return Proposal(id=f"p{self.day:04d}-{sig['symbol']}", symbol=sig["symbol"],
                        created_day=self.day, signal_close=close, notional=round(notional, 2),
                        qty=round(qty, 9), stop_price=stop, target_price=target,
                        max_hold_days=c.max_hold_days, reason=sig["reason"], risk_dollars=risk,
                        r_multiple_target=c.target_r, ttl_day=self.day + 1)

    # -- the daily step ------------------------------------------------------
    def step(self) -> dict:
        summary = {"fills": 0, "exits": 0, "proposals": 0, "expired": 0, "stale": 0, "blocked": 0}
        new_day = self.market.advance()
        on = self.market.date_for(new_day)
        opens = self._opens()

        # 09:15 — TTL sweep
        for p in self.proposals.values():
            if p.status == "pending" and p.ttl_day <= new_day:
                p.expire(new_day)
                summary["expired"] += 1
                self.log("expired", f"{p.id} {p.symbol}: no decision by 09:15", proposal_id=p.id)

        # 09:15 — approved proposals: risk gate again, drift check, submit
        entries_today = 0
        reserved = 0.0
        for p in [p for p in self.proposals.values() if p.status == "approved"]:
            reason = self.risk_gate(p, self.real, entries_today, at="open")
            if reason:
                p.block(new_day, reason)
                summary["blocked"] += 1
                self.log("blocked", f"{p.id} {p.symbol} at submit: {reason}", proposal_id=p.id)
                continue
            open_px = opens[p.symbol]
            gap = open_px / p.signal_close - 1
            p.drift_pct = round(gap * 100, 3)
            if abs(gap) > self.config.max_gap:
                p.transition("stale", new_day, f"gap {gap:+.2%} beyond max_gap")
                summary["stale"] += 1
                self.log("stale", f"{p.id} {p.symbol}: open {open_px:.2f} vs signal {p.signal_close:.2f} ({gap:+.2%})", proposal_id=p.id)
                continue
            qty = self._resize(p, open_px, self.real, reserved)
            if qty * open_px < 1.0:
                p.block(new_day, "resized below $1 minimum notional")
                summary["blocked"] += 1
                continue
            order, created = self.broker.submit_idempotent(
                client_order_id=p.client_order_id, symbol=p.symbol, side="buy", qty=qty,
                kind="entry", proposal_id=p.id, day=new_day)
            p.transition("submitted", new_day, f"DAY market {order.id} ({'new' if created else 'adopted'})")
            entries_today += 1
            reserved += qty * open_px
            self.log("submitted", f"{p.id} {p.symbol}: buy {qty:.4f} @ open, {order.client_order_id}", proposal_id=p.id, order_id=order.id)

        # 09:30 — fills
        filled = self.broker.fill_open_orders(new_day, on, opens)
        for o in filled:
            self._apply_fill(o, new_day)
            summary["fills"] += 1
            if o.kind == "exit":
                summary["exits"] += 1

        # shadow book: fill everything that was pending at yesterday's close
        self._shadow_open(new_day, on, opens)

        # 16:00 — close: mark, exits, risk checks, new signals
        self._mark_all()
        closes = self._closes()
        self._evaluate_exits(new_day, closes)
        self._check_drawdown_and_kill(new_day)
        if not self.halted and not self.paused:
            summary["proposals"] = self._propose(new_day)
        self.real.record_curve(new_day, self.date)
        self.shadow.record_curve(new_day, self.date)
        self.heartbeat_day = new_day
        self.steps += 1
        self.log("heartbeat", f"16:20 job complete: {summary}")

        if self.auto:
            self.auto_decide()
        return summary

    def _resize(self, p: Proposal, open_px: float, book: Portfolio, reserved: float = 0.0) -> float:
        """Keep risk = 1% at the actual open; respect cash and position caps."""
        c = self.config
        stop_dist = open_px * c.stop_pct
        qty = p.risk_dollars / stop_dist
        # Leave headroom for slippage and fees so the buy cannot overdraw cash.
        available = max(book.cash - reserved - 0.10, 0) / (1 + c.slippage_bps / 10_000)
        notional = min(qty * open_px, c.max_position_pct * book.equity(), available)
        return round(notional / open_px, 9)

    def _apply_fill(self, o: Order, day: int) -> None:
        if o.kind == "entry":
            p = self.proposals[o.proposal_id]
            p.transition("live", day, f"filled {o.qty:.4f} @ {o.filled_price}")
            p.fill_price, p.fill_qty, p.fill_day = o.filled_price, o.qty, day
            self.real.open_position(p, o.qty, o.reference_price, o.filled_price, o.fees, day, self.date)
            self.log("fill", f"BUY {o.qty:.4f} {o.symbol} @ {o.filled_price:.2f} (ref {o.reference_price:.2f}, fees ${o.fees:.2f})", order_id=o.id, proposal_id=p.id)
        else:
            sym = o.symbol
            _, reason = self.exit_queue.pop(sym, (None, "exit"))
            pos = self.real.positions.get(sym)
            if pos is None:
                return
            prop = self.proposals.get(pos.proposal_id)
            risk = prop.risk_dollars if prop else self.config.starting_equity * self.config.risk_pct
            t = self.real.close_position(sym, o.reference_price, o.filled_price, o.fees, day, self.date, reason, risk)
            if prop and prop.status == "live":
                prop.transition("closed", day, f"{reason}: {t.net_pnl:+.2f} ({t.r_multiple:+.2f}R)")
            self.log("fill", f"SELL {o.qty:.4f} {sym} @ {o.filled_price:.2f} [{reason}] net {t.net_pnl:+.2f} ({t.r_multiple:+.2f}R)", order_id=o.id, trade_id=t.id)

    def _evaluate_exits(self, day: int, closes: dict[str, float]) -> None:
        for book, queue in ((self.real, self.exit_queue), (self.shadow, self.shadow_exits)):
            for sym, pos in list(book.positions.items()):
                if sym in queue:
                    continue
                close = closes[sym]
                reason = None
                if self.kill_triggered:
                    reason = "kill"
                elif close <= pos.stop_price:
                    reason = "stop"
                elif close >= pos.target_price:
                    reason = "target"
                elif day - pos.entry_day >= pos.max_hold_days:
                    reason = "time"
                if not reason:
                    continue
                pos.exit_pending = reason
                if book is self.real:
                    cid = f"{pos.proposal_id}-exit"
                    order, _ = self.broker.submit_idempotent(
                        client_order_id=cid, symbol=sym, side="sell", qty=pos.qty,
                        kind="exit", proposal_id=pos.proposal_id, day=day)
                    queue[sym] = (order.id, reason)
                    self.log("exit_queued", f"{sym} {reason} at close {close:.2f} -> sell {pos.qty:.4f} next open (no approval needed)", order_id=order.id)
                else:
                    queue[sym] = reason

    def _check_drawdown_and_kill(self, day: int) -> None:
        c = self.config
        eq = self.real.equity()
        if not self.kill_triggered and eq <= c.starting_equity * (1 - c.kill_pct):
            self.kill_triggered = True
            self.halted = True
            self.log("KILL", f"equity ${eq:.2f} <= {1 - c.kill_pct:.0%} of start: liquidate and halt")
            for p in self.proposals.values():
                if p.status == "pending":
                    p.token_used = True
                    p.transition("expired", day, "kill switch")
                elif p.status == "approved":
                    p.block(day, "kill switch")
            self._evaluate_exits(day, self._closes())
        hwm_floor = self.real.hwm * (1 - c.drawdown_pause_pct)
        if not self.paused and not self.halted and eq < hwm_floor:
            self.paused = True
            self.log("PAUSE", f"equity ${eq:.2f} is {c.drawdown_pause_pct:.0%} below high-water ${self.real.hwm:.2f}: no new entries until manual review")

    def _propose(self, day: int) -> int:
        n = 0
        active = self._active_symbols()
        entries = 0
        for sig in self.signals():
            if sig["symbol"] in active:
                continue
            p = self._build_proposal(sig)
            if p.id in self.proposals:
                continue
            self.proposals[p.id] = p
            bad = self.validate(p)
            if bad:
                p.block(day, f"validation: {bad}")
                self.log("blocked", f"{p.id}: {bad}", proposal_id=p.id)
                continue
            reason = self.risk_gate(p, self.real, entries, at="close")
            if reason:
                p.block(day, reason)
                self.log("blocked", f"{p.id} {p.symbol}: {reason}", proposal_id=p.id)
                continue
            p.make_pending(day)
            self.shadow_queue.append(p.id)
            entries += 1
            n += 1
            self.log("proposal", f"{p.id} {p.symbol}: buy ${p.notional:.0f} ({p.qty:.4f}) stop {p.stop_price:.2f} target {p.target_price:.2f}; {p.reason}; TTL 09:15 next day", proposal_id=p.id)
        return n

    def _shadow_open(self, day: int, on, opens: dict[str, float]) -> None:
        """Mirror the 09:15 gate for the shadow book: positions being sold this
        morning still count, and their proceeds are not yet spendable."""
        ids, self.shadow_queue = self.shadow_queue, []
        planned: list[tuple[Proposal, float, float]] = []
        reserved = 0.0
        held = len(self.shadow.positions)
        for pid in ids:
            p = self.proposals[pid]
            if p.symbol in self.shadow.positions:
                continue
            if held + len(planned) >= self.config.max_positions or len(planned) >= self.config.max_entries_per_day:
                continue
            ref = opens[p.symbol]
            if abs(ref / p.signal_close - 1) > self.config.max_gap:
                continue
            qty = self._resize(p, ref, self.shadow, reserved)
            if qty * ref < 1.0:
                continue
            planned.append((p, qty, ref))
            reserved += qty * ref
        for p, qty, ref in planned:
            c = self.costs.costs(on, "buy", qty, ref)
            price = round(self.costs.fill_price(ref, "buy"), 4)
            self.shadow.open_position(p, qty, ref, price, c.fees, day, self.date)
        for sym, reason in list(self.shadow_exits.items()):
            pos = self.shadow.positions.get(sym)
            if not pos:
                continue
            ref = opens[sym]
            c = self.costs.costs(on, "sell", pos.qty, ref)
            price = round(self.costs.fill_price(ref, "sell"), 4)
            prop = self.proposals.get(pos.proposal_id)
            risk = prop.risk_dollars if prop else self.config.starting_equity * self.config.risk_pct
            self.shadow.close_position(sym, ref, price, c.fees, day, self.date, reason, risk)
        self.shadow_exits.clear()

    # -- decisions -----------------------------------------------------------
    def approve(self, proposal_id: str, token: str, by: str = "human") -> Proposal:
        p = self._pending(proposal_id)
        p.approve(token, self.day, by)
        self.log("approved", f"{p.id} {p.symbol} approved by {by}", proposal_id=p.id)
        return p

    def reject(self, proposal_id: str, token: str, category: str, note: str = "", by: str = "human") -> Proposal:
        p = self._pending(proposal_id)
        p.reject(token, self.day, by, category, note)
        self.log("rejected", f"{p.id} {p.symbol} rejected by {by} [{category}] {note}".rstrip(), proposal_id=p.id)
        return p

    def _pending(self, proposal_id: str) -> Proposal:
        p = self.proposals.get(proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        return p

    def auto_decide(self) -> int:
        """Auto mode: approve every pending proposal on behalf of the operator."""
        n = 0
        for p in list(self.proposals.values()):
            if p.status == "pending" and p.token and not p.token_used:
                try:
                    self.approve(p.id, p.token, by="auto")
                    n += 1
                except (IllegalTransition, TokenError):
                    pass
        return n

    def set_auto(self, on: bool) -> None:
        if on == self.auto:
            return
        self.auto = on
        self.log("auto", "AUTO mode ON: every proposal is approved without a human; the real book now equals the shadow book" if on else "AUTO mode OFF: proposals wait for a human")
        if on:
            self.auto_decide()

    def resume_entries(self) -> None:
        if self.paused:
            self.paused = False
            self.log("RESUME", "drawdown pause cleared after manual review")

    def stats(self) -> dict:
        decided = [p for p in self.proposals.values() if p.decided_by is not None]
        rejects = [p for p in decided if p.status == "rejected"]
        discretionary = [p for p in rejects if p.reject_category == "discretion"]
        return {
            "day": self.day, "date": self.date, "steps": self.steps,
            "auto": self.auto, "paused": self.paused, "halted": self.halted,
            "kill_triggered": self.kill_triggered, "heartbeat_day": self.heartbeat_day,
            "pending": sum(1 for p in self.proposals.values() if p.status == "pending"),
            "proposals_total": len(self.proposals),
            "rejects": len(rejects), "discretionary_rejects": len(discretionary),
            "discretionary_reject_rate": round(len(discretionary) / len(decided) * 100, 1) if decided else 0.0,
            "real": self.real.stats(), "shadow": self.shadow.stats(),
        }

    # -- persistence ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "config": self.config.to_dict(),
            "market": self.market.to_dict(),
            "broker": self.broker.to_dict(),
            "real": self.real.to_dict(),
            "shadow": self.shadow.to_dict(),
            "proposals": [p.to_dict(include_token=True) for p in self.proposals.values()],
            "shadow_queue": self.shadow_queue,
            "exit_queue": self.exit_queue,
            "shadow_exits": self.shadow_exits,
            "audit": self.audit,
            "auto": self.auto, "paused": self.paused, "halted": self.halted,
            "kill_triggered": self.kill_triggered, "heartbeat_day": self.heartbeat_day,
            "steps": self.steps, "created_at": self.created_at, "seq": self._seq,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Simulator":
        s = cls.__new__(cls)
        s.config = Config(**d["config"])
        s.costs = CostModel(slippage_bps=s.config.slippage_bps, charge_fees=s.config.charge_fees)
        s.market = Market.from_dict(d["market"])
        s.broker = SimBroker.from_dict(d["broker"], s.costs)
        s.real = Portfolio.from_dict(d["real"])
        s.shadow = Portfolio.from_dict(d["shadow"])
        s.proposals = {p["id"]: Proposal.from_dict(p) for p in d["proposals"]}
        s.shadow_queue = d["shadow_queue"]
        s.exit_queue = {k: tuple(v) for k, v in d["exit_queue"].items()}
        s.shadow_exits = d["shadow_exits"]
        s.audit = d["audit"]
        s.auto = d["auto"]
        s.paused = d["paused"]
        s.halted = d["halted"]
        s.kill_triggered = d["kill_triggered"]
        s.heartbeat_day = d["heartbeat_day"]
        s.steps = d["steps"]
        s.created_at = d["created_at"]
        s._seq = d["seq"]
        return s
