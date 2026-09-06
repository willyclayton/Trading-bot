import json
import os
import tempfile
import unittest

from sim.engine import Simulator, Config
from sim.market import WINDOW
from sim.store import SqliteStore


def run_until_pending(sim, limit=400):
    for _ in range(limit):
        pending = [p for p in sim.proposals.values() if p.status == "pending"]
        if pending:
            return pending
        sim.step()
    raise AssertionError("no proposal generated")


def approve_all(sim):
    for p in sim.proposals.values():
        if p.status == "pending":
            sim.approve(p.id, p.token, by="test")


class Cadence(unittest.TestCase):
    def test_no_lookahead_fill_is_next_open(self):
        sim = Simulator(Config(seed=11))
        pending = run_until_pending(sim)
        p = pending[0]
        signal_day = sim.day
        self.assertEqual(p.created_day, signal_day)
        approve_all(sim)
        sim.step()
        self.assertEqual(p.status, "live")
        self.assertEqual(p.fill_day, signal_day + 1)
        bar = sim.market.bar(p.symbol, p.fill_day)
        order = sim.broker.get_by_client_order_id(p.client_order_id)
        self.assertEqual(order.reference_price, bar.open)
        self.assertGreater(order.filled_price, bar.open)   # slippage against us

    def test_undecided_proposal_expires_at_the_open(self):
        sim = Simulator(Config(seed=11))
        pending = run_until_pending(sim)
        sim.step()
        for p in pending:
            self.assertEqual(p.status, "expired")
        self.assertEqual(len(sim.real.positions), 0)

    def test_drift_beyond_max_gap_is_stale(self):
        sim = Simulator(Config(seed=11, max_gap=0.0))
        pending = run_until_pending(sim)
        approve_all(sim)
        sim.step()
        self.assertTrue(all(p.status == "stale" for p in pending))
        self.assertEqual(len(sim.broker.orders), 0)

    def test_risk_is_one_percent_after_resize(self):
        sim = Simulator(Config(seed=11))
        pending = run_until_pending(sim)
        approve_all(sim)
        sim.step()
        for p in pending:
            if p.status != "live":
                continue
            pos = sim.real.positions[p.symbol]
            risk = pos.qty * (pos.entry_price - pos.stop_price)
            self.assertAlmostEqual(risk, p.risk_dollars, delta=0.05)

    def test_exit_orders_never_wait_for_approval(self):
        sim = Simulator(Config(seed=11, max_hold_days=2))
        run_until_pending(sim)
        approve_all(sim)
        sim.step()
        sym = next(iter(sim.real.positions))
        for _ in range(3):
            sim.step()
        self.assertNotIn(sym, sim.real.positions)
        exits = [o for o in sim.broker.orders.values() if o.kind == "exit" and o.symbol == sym]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].status, "filled")
        self.assertEqual(sim.real.trades[-1].exit_reason, "time")


class Invariants(unittest.TestCase):
    def test_cash_never_negative_and_caps_hold(self):
        for seed in (1, 2, 3):
            sim = Simulator(Config(seed=seed))
            sim.set_auto(True)
            for _ in range(250):
                sim.step()
                self.assertGreaterEqual(sim.real.cash, -1e-6, f"seed {seed} day {sim.day}")
                self.assertLessEqual(len(sim.real.positions), sim.config.max_positions)
                for pos in sim.real.positions.values():
                    self.assertLessEqual(pos.qty * pos.entry_price, sim.config.max_position_pct * sim.real.hwm + 1)

    def test_auto_mode_makes_real_equal_shadow(self):
        sim = Simulator(Config(seed=5))
        sim.set_auto(True)
        for _ in range(300):
            sim.step()
        self.assertGreater(len(sim.real.trades), 5)
        self.assertEqual(len(sim.real.trades), len(sim.shadow.trades))
        self.assertAlmostEqual(sim.real.equity(), sim.shadow.equity(), delta=1e-6)
        self.assertTrue(all(p.decided_by == "auto" for p in sim.proposals.values() if p.decided_by))

    def test_paired_cost_test(self):
        """Same paths, costs on vs off: costs-on must never be richer (plan §5 invariant 1)."""
        for seed in (1, 2, 3):
            on = Simulator(Config(seed=seed))
            off = Simulator(Config(seed=seed, slippage_bps=0.0, charge_fees=False))
            on.set_auto(True)
            off.set_auto(True)
            for _ in range(250):
                on.step()
                off.step()
            self.assertGreater(on.real.total_costs, 0)
            self.assertEqual(off.real.total_costs, 0)
            self.assertLess(on.real.equity(), off.real.equity(), f"seed {seed}")

    def test_turnover_scaled_cost_check(self):
        """Realised friction ~ round trips x modelled per-trip cost (plan §5 invariant 3)."""
        sim = Simulator(Config(seed=5))
        sim.set_auto(True)
        for _ in range(300):
            sim.step()
        trades = sim.real.trades
        modelled = sum(2 * t.qty * (t.entry_price + t.exit_price) / 2 * sim.config.slippage_bps / 10_000 + t.fees for t in trades)
        realised = sum(t.costs for t in trades)
        self.assertAlmostEqual(realised, modelled, delta=0.02 * max(realised, 1))

    def test_kill_switch_liquidates_and_halts(self):
        sim = Simulator(Config(seed=5, kill_pct=0.0001))  # trip on the first losing close
        sim.set_auto(True)
        for _ in range(400):
            sim.step()
            if sim.halted:
                break
        self.assertTrue(sim.kill_triggered)
        sim.step()
        sim.step()
        self.assertEqual(len(sim.real.positions), 0)
        after = [p for p in sim.proposals.values() if p.created_day > sim.day - 2 and p.status == "pending"]
        self.assertEqual(after, [])


class RollingWindow(unittest.TestCase):
    def test_snapshot_stays_bounded_on_long_runs(self):
        sim = Simulator(Config(seed=2))
        sim.set_auto(True)
        for _ in range(WINDOW + 150):
            sim.step()
        for sym, bars in sim.market.bars.items():
            self.assertEqual(len(bars), WINDOW)
            self.assertEqual(bars[-1].day, sim.day)
            self.assertEqual(sim.market.bar(sym, sim.day), bars[-1])
            self.assertEqual(sim.market.bar(sym, sim.day - WINDOW + 1), bars[0])
            with self.assertRaises(KeyError):
                sim.market.bar(sym, sim.day - WINDOW)
        restored = Simulator.from_dict(json.loads(json.dumps(sim.to_dict())))
        self.assertEqual(restored.day, sim.day)
        self.assertEqual(restored.date, sim.date)
        restored.step()
        sim.step()
        self.assertEqual(restored.stats(), sim.stats())


class Persistence(unittest.TestCase):
    def test_round_trip_is_exact(self):
        sim = Simulator(Config(seed=9))
        sim.set_auto(True)
        for _ in range(120):
            sim.step()
        restored = Simulator.from_dict(json.loads(json.dumps(sim.to_dict())))
        self.assertTrue(restored.auto)
        self.assertEqual(restored.stats(), sim.stats())
        for _ in range(30):
            sim.step()
            restored.step()
        self.assertEqual(restored.stats(), sim.stats())

    def test_sqlite_store_survives_restart(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            sim = Simulator(Config(seed=9))
            sim.set_auto(True)
            for _ in range(60):
                sim.step()
            store = SqliteStore(path)
            store.save(sim.to_dict())
            store.close()
            store2 = SqliteStore(path)
            restored = Simulator.from_dict(store2.load())
            self.assertTrue(restored.auto)
            self.assertEqual(restored.stats(), sim.stats())
            n_trades = store2.conn.execute("SELECT COUNT(*) FROM trades WHERE book='real'").fetchone()[0]
            self.assertEqual(n_trades, len(sim.real.trades))
            store2.close()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
