"""A tiny stand-in for the broker's order API.

Models the two behaviours the plan depends on:

* ``client_order_id`` must be unique. A retried submit with the same id
  raises :class:`DuplicateClientOrderId` (Alpaca returns HTTP 422 code
  40010001). The caller must treat that as success-by-lookup (Rule 4).
* Fractional orders are DAY orders, simple order class only (Rule 10).
  Anything else is rejected the way Alpaca rejects it.

Fills happen at the next open, adjusted by the cost model.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date

from .costs import CostModel


class DuplicateClientOrderId(Exception):
    pass


class OrderRejected(Exception):
    pass


@dataclass
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    kind: str                     # "entry" | "exit"
    proposal_id: str | None
    time_in_force: str = "day"
    order_class: str = "simple"
    status: str = "accepted"      # accepted | filled | canceled
    submitted_day: int = 0
    filled_day: int | None = None
    filled_price: float | None = None
    reference_price: float | None = None
    fees: float = 0.0
    slippage: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SimBroker:
    def __init__(self, costs: CostModel):
        self.costs = costs
        self.orders: dict[str, Order] = {}
        self._by_client_id: dict[str, str] = {}
        self._seq = 0

    def submit(self, *, client_order_id: str, symbol: str, side: str, qty: float,
               kind: str, proposal_id: str | None, day: int,
               time_in_force: str = "day", order_class: str = "simple") -> Order:
        if client_order_id in self._by_client_id:
            raise DuplicateClientOrderId("client_order_id must be unique")
        is_fractional = abs(qty - round(qty)) > 1e-9
        if is_fractional and time_in_force != "day":
            raise OrderRejected("Fractional orders must be DAY orders")
        if is_fractional and order_class != "simple":
            raise OrderRejected("Fractional orders do not support bracket/OCO/OTO")
        if qty <= 0:
            raise OrderRejected("qty must be positive")
        self._seq += 1
        o = Order(id=f"ord-{self._seq:06d}", client_order_id=client_order_id, symbol=symbol,
                  side=side, qty=round(qty, 9), kind=kind, proposal_id=proposal_id,
                  time_in_force=time_in_force, order_class=order_class, submitted_day=day)
        self.orders[o.id] = o
        self._by_client_id[client_order_id] = o.id
        return o

    def submit_idempotent(self, **kw) -> tuple[Order, bool]:
        """Submit, or adopt the order that already exists for this client id."""
        try:
            return self.submit(**kw), True
        except DuplicateClientOrderId:
            return self.get_by_client_order_id(kw["client_order_id"]), False

    def get_by_client_order_id(self, cid: str) -> Order:
        oid = self._by_client_id[cid]
        if oid not in self.orders:
            raise KeyError(f"{cid}: order settled and pruned from history")
        return self.orders[oid]

    def open_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status == "accepted"]

    def fill_open_orders(self, day: int, on: date, opens: dict[str, float]) -> list[Order]:
        """DAY orders queued overnight release at the open and fill there."""
        filled = []
        for o in self.open_orders():
            ref = opens[o.symbol]
            c = self.costs.costs(on, o.side, o.qty, ref)
            o.reference_price = ref
            o.filled_price = round(self.costs.fill_price(ref, o.side), 4)
            o.fees = c.fees
            o.slippage = round(c.slippage, 4)
            o.filled_day = day
            o.status = "filled"
            filled.append(o)
        return filled

    def cancel_open(self, day: int, note: str) -> list[Order]:
        out = []
        for o in self.open_orders():
            o.status = "canceled"
            o.filled_day = day
            o.note = note
            out.append(o)
        return out

    MAX_ORDERS = 1000

    def prune(self) -> None:
        """Drop the oldest settled orders beyond the cap; their client ids stay
        reserved so a late retry can never recreate them."""
        settled = [o for o in self.orders.values() if o.status != "accepted"]
        for o in settled[: max(0, len(self.orders) - self.MAX_ORDERS)]:
            del self.orders[o.id]

    def to_dict(self) -> dict:
        return {"seq": self._seq, "orders": [o.to_dict() for o in self.orders.values()],
                "client_ids": sorted(set(self._by_client_id) - {o.client_order_id for o in self.orders.values()})}

    @classmethod
    def from_dict(cls, d: dict, costs: CostModel) -> SimBroker:
        b = cls(costs)
        b._seq = d["seq"]
        for od in d["orders"]:
            o = Order(**od)
            b.orders[o.id] = o
            b._by_client_id[o.client_order_id] = o.id
        for cid in d.get("client_ids", []):
            b._by_client_id.setdefault(cid, "")
        return b
