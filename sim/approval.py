"""Approval state machine (plan §3, §4).

    proposed -> pending -> approved -> submitted -> live -> closed
    terminal: rejected / expired / stale / blocked / failed

Illegal transitions raise. Approval tokens are single-use. The
``client_order_id`` is derived deterministically from the proposal id so a
retried submit can never create a second order (Rule 4).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field, asdict
from typing import Optional


class IllegalTransition(Exception):
    pass


class TokenError(Exception):
    pass


TERMINAL = {"rejected", "expired", "stale", "blocked", "failed", "closed"}

TRANSITIONS = {
    "proposed": {"pending", "blocked"},
    "pending": {"approved", "rejected", "expired"},
    "approved": {"submitted", "stale", "blocked", "expired", "failed"},
    "submitted": {"live", "failed"},
    "live": {"closed"},
}

REJECT_CATEGORIES = ("operational", "data", "discretion")


def client_order_id_for(proposal_id: str) -> str:
    return "tb-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:20]


@dataclass
class Proposal:
    id: str
    symbol: str
    created_day: int              # simulation day index of the signal close
    signal_close: float
    notional: float
    qty: float
    stop_price: float
    target_price: float
    max_hold_days: int
    reason: str
    risk_dollars: float
    r_multiple_target: float
    ttl_day: int                  # expires at the open of this day if undecided
    status: str = "proposed"
    token: Optional[str] = None
    token_used: bool = False
    decided_by: Optional[str] = None
    decided_day: Optional[int] = None
    reject_category: Optional[str] = None
    reject_note: Optional[str] = None
    block_reason: Optional[str] = None
    client_order_id: str = ""
    fill_price: Optional[float] = None
    fill_qty: Optional[float] = None
    fill_day: Optional[int] = None
    drift_pct: Optional[float] = None
    history: list = field(default_factory=list)

    def __post_init__(self):
        if not self.client_order_id:
            self.client_order_id = client_order_id_for(self.id)

    # -- transitions -------------------------------------------------------
    def transition(self, to: str, day: int, note: str = "") -> None:
        allowed = TRANSITIONS.get(self.status, set())
        if to not in allowed:
            raise IllegalTransition(f"{self.id}: {self.status} -> {to}")
        self.history.append({"day": day, "from": self.status, "to": to, "note": note})
        self.status = to

    def make_pending(self, day: int) -> str:
        self.token = secrets.token_urlsafe(16)
        self.token_used = False
        self.transition("pending", day, "awaiting approval")
        return self.token

    def block(self, day: int, reason: str) -> None:
        self.block_reason = reason
        self.transition("blocked", day, reason)

    def approve(self, token: str, day: int, by: str) -> None:
        self._consume(token)
        self.decided_by = by
        self.decided_day = day
        self.transition("approved", day, f"approved by {by}")

    def reject(self, token: str, day: int, by: str, category: str, note: str = "") -> None:
        if category not in REJECT_CATEGORIES:
            raise ValueError(f"unknown reject category {category!r}")
        self._consume(token)
        self.decided_by = by
        self.decided_day = day
        self.reject_category = category
        self.reject_note = note
        self.transition("rejected", day, f"rejected by {by} ({category}) {note}".strip())

    def expire(self, day: int) -> None:
        self.token_used = True
        self.transition("expired", day, "TTL reached with no decision")

    def _consume(self, token: str) -> None:
        if self.status != "pending":
            raise IllegalTransition(f"{self.id}: decision on {self.status} proposal")
        if self.token_used or token != self.token:
            raise TokenError(f"{self.id}: token invalid or already used")
        self.token_used = True

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def to_dict(self, include_token: bool = False) -> dict:
        d = asdict(self)
        if not include_token:
            d.pop("token", None)
        d["has_token"] = self.token is not None and not self.token_used
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        d = dict(d)
        d.pop("has_token", None)
        return cls(**d)
