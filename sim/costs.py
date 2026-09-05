"""Date-indexed cost model (plan §2, Rule 8; RESEARCH.md §3).

Every regulatory fee has an effective date and is rounded *up* to the cent
per fee, mirroring Alpaca's practice. Slippage is charged per side in basis
points of notional and is the dominant term at this account size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

# (effective_from, dollars per $1M of sale notional)
SEC_31_SCHEDULE = [
    (date(2016, 1, 1), 21.80),
    (date(2016, 7, 14), 21.80),
    (date(2017, 4, 3), 23.10),
    (date(2018, 4, 16), 13.00),
    (date(2019, 4, 16), 20.70),
    (date(2020, 2, 18), 22.10),
    (date(2021, 2, 25), 5.10),
    (date(2022, 2, 25), 9.20),
    (date(2023, 2, 27), 8.00),
    (date(2024, 5, 22), 27.80),
    (date(2025, 5, 14), 0.00),
    (date(2026, 4, 4), 20.60),
]

# (effective_from, per share, max per trade)
TAF_SCHEDULE = [
    (date(2016, 1, 1), 0.000119, 5.95),
    (date(2022, 1, 1), 0.000130, 6.49),
    (date(2023, 1, 1), 0.000145, 7.27),
    (date(2024, 1, 1), 0.000166, 8.30),
    (date(2026, 1, 1), 0.000195, 9.79),
    (date(2027, 1, 1), 0.000232, 11.61),
    (date(2028, 1, 1), 0.000240, 12.05),
    (date(2029, 1, 1), 0.000249, 12.50),
]

CAT_PER_SHARE = 0.000003


def _lookup(schedule, on: date):
    chosen = schedule[0]
    for row in schedule:
        if row[0] <= on:
            chosen = row
        else:
            break
    return chosen


def round_up_cent(x: float) -> float:
    if x <= 0:
        return 0.0
    return math.ceil(round(x * 100, 9)) / 100.0


@dataclass(frozen=True)
class FillCosts:
    slippage: float
    sec31: float
    taf: float
    cat: float

    @property
    def fees(self) -> float:
        return round(self.sec31 + self.taf + self.cat, 2)

    @property
    def total(self) -> float:
        return round(self.slippage + self.fees, 4)


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 5.0
    charge_fees: bool = True
    charge_slippage: bool = True

    def sec31_rate(self, on: date) -> float:
        return _lookup(SEC_31_SCHEDULE, on)[1]

    def taf_rate(self, on: date):
        _, per_share, cap = _lookup(TAF_SCHEDULE, on)
        return per_share, cap

    def fill_price(self, reference: float, side: str) -> float:
        """Price actually paid/received after slippage against the reference."""
        if not self.charge_slippage:
            return reference
        adj = reference * self.slippage_bps / 10_000
        return reference + adj if side == "buy" else reference - adj

    def costs(self, on: date, side: str, qty: float, reference: float) -> FillCosts:
        notional = qty * reference
        slippage = notional * self.slippage_bps / 10_000 if self.charge_slippage else 0.0
        if not self.charge_fees:
            return FillCosts(round(slippage, 4), 0.0, 0.0, 0.0)
        cat = round_up_cent(qty * CAT_PER_SHARE)
        if side == "sell":
            sec31 = round_up_cent(notional * self.sec31_rate(on) / 1_000_000)
            per_share, cap = self.taf_rate(on)
            taf = round_up_cent(min(qty * per_share, cap))
        else:
            sec31 = taf = 0.0
        return FillCosts(round(slippage, 4), sec31, taf, cat)

    def paper_equivalent(self) -> "CostModel":
        """Alpaca paper fills at NBBO with no fees; use this when comparing to paper."""
        return CostModel(slippage_bps=0.0, charge_fees=False, charge_slippage=False)
