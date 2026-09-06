"""Synthetic daily bars for a fixed ETF-like universe.

Deterministic per seed. Each symbol follows a drifting random walk with a
mild mean-reverting component and an intraday range, so the strategy and
the exit rules have something to react to. This data exists to exercise the
*operations* (proposals, fills, exits, costs, reconciliation). Any P&L it
produces is meaningless (plan Rule 7).

Only the most recent ``WINDOW`` bars per symbol are kept: the strategy needs
a few dozen, the UI sparkline forty, and the snapshot has to stay small
enough to round-trip through a key-value store on every request.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, astuple
from datetime import date, timedelta

UNIVERSE = [
    # symbol, start price, annual vol, annual drift
    ("SPY", 560.0, 0.16, 0.08),
    ("QQQ", 480.0, 0.22, 0.10),
    ("IWM", 210.0, 0.24, 0.05),
    ("XLF", 45.0, 0.20, 0.06),
    ("XLE", 90.0, 0.28, 0.03),
    ("TLT", 92.0, 0.15, 0.00),
    ("GLD", 245.0, 0.14, 0.05),
    ("EFA", 82.0, 0.17, 0.04),
]

WINDOW = 120


@dataclass(frozen=True)
class Bar:
    day: int
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class Market:
    def __init__(self, seed: int, start: date = date(2026, 1, 2), warmup: int = 60):
        self.seed = seed
        self.start = start
        self.rng = random.Random(seed)
        self.bars: dict[str, list[Bar]] = {s: [] for s, *_ in UNIVERSE}
        self.day = -1
        self._anchor = {s: p for s, p, *_ in UNIVERSE}
        self._dates: list[date] = []
        for _ in range(warmup):
            self.advance()

    def date_for(self, day: int) -> date:
        while len(self._dates) <= day:
            d = self._dates[-1] + timedelta(days=1) if self._dates else self.start
            while d.weekday() >= 5:
                d += timedelta(days=1)
            self._dates.append(d)
        return self._dates[day]

    def advance(self) -> int:
        """Generate the next completed daily bar for every symbol."""
        day = self.day + 1
        date_s = self.date_for(day).isoformat()
        common_shock = self.rng.gauss(0, 1)
        for sym, start_px, vol, drift in UNIVERSE:
            prev = self.bars[sym][-1].close if self.bars[sym] else start_px
            dvol = vol / math.sqrt(252)
            ddrift = drift / 252
            anchor = self._anchor[sym] * (1 + ddrift) ** day
            pull = 0.02 * math.log(anchor / prev)
            r = ddrift + pull + dvol * (0.6 * common_shock + 0.8 * self.rng.gauss(0, 1))
            open_ = prev * (1 + self.rng.gauss(0, dvol * 0.35))
            close = prev * math.exp(r)
            spread = abs(self.rng.gauss(0, dvol * 0.8)) * prev
            high = max(open_, close) + spread * self.rng.random()
            low = min(open_, close) - spread * self.rng.random()
            vol_shares = int(abs(self.rng.gauss(30_000_000, 8_000_000)))
            bars = self.bars[sym]
            bars.append(Bar(day, date_s, round(open_, 4), round(high, 4), round(low, 4),
                            round(close, 4), vol_shares))
            if len(bars) > WINDOW:
                del bars[: len(bars) - WINDOW]
        self.day = day
        return day

    def bar(self, sym: str, day: int) -> Bar:
        bars = self.bars[sym]
        idx = day - bars[-1].day + len(bars) - 1
        if idx < 0:
            raise KeyError(f"{sym} day {day} has left the window")
        return bars[idx]

    def closes(self, sym: str, n: int) -> list[float]:
        return [b.close for b in self.bars[sym][-n:]]

    def last(self, sym: str) -> Bar:
        return self.bars[sym][-1]

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "start": self.start.isoformat(),
            "day": self.day,
            "rng": self.rng.getstate(),
            "bars": {s: [list(astuple(b)) for b in bs] for s, bs in self.bars.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> Market:
        m = cls.__new__(cls)
        m.seed = d["seed"]
        m.start = date.fromisoformat(d["start"])
        m.day = d["day"]
        m.rng = random.Random()
        state = d["rng"]
        m.rng.setstate((state[0], tuple(state[1]), state[2]))
        m._anchor = {s: p for s, p, *_ in UNIVERSE}
        m._dates = []
        m.bars = {s: [Bar(*row) for row in rows] for s, rows in d["bars"].items()}
        return m
