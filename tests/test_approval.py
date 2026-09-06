import unittest

from sim.approval import Proposal, IllegalTransition, TokenError, client_order_id_for
from sim.broker import SimBroker, DuplicateClientOrderId, OrderRejected
from sim.costs import CostModel


def make(pid="p0001-SPY"):
    return Proposal(id=pid, symbol="SPY", created_day=1, signal_close=500.0, notional=200.0,
                    qty=0.4, stop_price=475.0, target_price=550.0, max_hold_days=10,
                    reason="test", risk_dollars=10.0, r_multiple_target=2.0, ttl_day=2)


class StateMachine(unittest.TestCase):
    def test_happy_path(self):
        p = make()
        tok = p.make_pending(1)
        p.approve(tok, 1, "human")
        p.transition("submitted", 2)
        p.transition("live", 2)
        p.transition("closed", 5)
        self.assertEqual([h["to"] for h in p.history], ["pending", "approved", "submitted", "live", "closed"])

    def test_illegal_transitions_raise(self):
        p = make()
        with self.assertRaises(IllegalTransition):
            p.transition("live", 1)            # proposed -> live
        p.make_pending(1)
        with self.assertRaises(IllegalTransition):
            p.transition("submitted", 1)       # pending -> submitted skips approval

    def test_token_is_single_use(self):
        p = make()
        tok = p.make_pending(1)
        p.approve(tok, 1, "human")
        with self.assertRaises(IllegalTransition):
            p.approve(tok, 1, "human")         # double tap

    def test_wrong_token_rejected(self):
        p = make()
        p.make_pending(1)
        with self.assertRaises(TokenError):
            p.approve("not-the-token", 1, "human")
        self.assertEqual(p.status, "pending")  # still decidable with the right token

    def test_expired_cannot_be_approved(self):
        p = make()
        tok = p.make_pending(1)
        p.expire(2)
        with self.assertRaises(IllegalTransition):
            p.approve(tok, 2, "human")

    def test_reject_requires_category(self):
        p = make()
        tok = p.make_pending(1)
        with self.assertRaises(ValueError):
            p.reject(tok, 1, "human", "vibes")
        p.reject(tok, 1, "human", "discretion", "not today")
        self.assertEqual(p.status, "rejected")
        self.assertTrue(p.is_terminal)

    def test_client_order_id_is_deterministic(self):
        self.assertEqual(client_order_id_for("p0001-SPY"), client_order_id_for("p0001-SPY"))
        self.assertNotEqual(client_order_id_for("p0001-SPY"), client_order_id_for("p0002-SPY"))
        self.assertEqual(make().client_order_id, make().client_order_id)

    def test_list_view_never_leaks_token(self):
        p = make()
        p.make_pending(1)
        self.assertNotIn("token", p.to_dict())
        self.assertIn("token", p.to_dict(include_token=True))
        self.assertEqual(Proposal.from_dict(p.to_dict(include_token=True)).token, p.token)


class Broker(unittest.TestCase):
    def setUp(self):
        self.b = SimBroker(CostModel())

    def test_duplicate_client_order_id_is_422(self):
        kw = dict(client_order_id="tb-abc", symbol="SPY", side="buy", qty=0.4, kind="entry", proposal_id="p", day=1)
        self.b.submit(**kw)
        with self.assertRaises(DuplicateClientOrderId):
            self.b.submit(**kw)

    def test_retry_adopts_existing_order(self):
        kw = dict(client_order_id="tb-abc", symbol="SPY", side="buy", qty=0.4, kind="entry", proposal_id="p", day=1)
        first, created = self.b.submit_idempotent(**kw)
        second, created2 = self.b.submit_idempotent(**kw)
        self.assertTrue(created)
        self.assertFalse(created2)
        self.assertIs(first, second)
        self.assertEqual(len(self.b.orders), 1)

    def test_fractional_must_be_day_and_simple(self):
        kw = dict(client_order_id="a", symbol="SPY", side="buy", qty=0.4, kind="entry", proposal_id="p", day=1)
        with self.assertRaises(OrderRejected):
            self.b.submit(**kw, time_in_force="gtc")
        with self.assertRaises(OrderRejected):
            self.b.submit(**kw, order_class="bracket")


if __name__ == "__main__":
    unittest.main()
