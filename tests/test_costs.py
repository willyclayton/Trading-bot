import unittest
from datetime import date

from sim.costs import CostModel, round_up_cent


class RoundUp(unittest.TestCase):
    def test_rounds_up_to_cent(self):
        self.assertEqual(round_up_cent(0.0041), 0.01)
        self.assertEqual(round_up_cent(0.0100), 0.01)
        self.assertEqual(round_up_cent(0.0101), 0.02)
        self.assertEqual(round_up_cent(0.0), 0.0)


class Rates(unittest.TestCase):
    def setUp(self):
        self.cm = CostModel(slippage_bps=5.0)

    def test_sec31_has_effective_dates(self):
        self.assertEqual(self.cm.sec31_rate(date(2025, 6, 1)), 0.0)      # zero window
        self.assertEqual(self.cm.sec31_rate(date(2026, 4, 3)), 0.0)
        self.assertEqual(self.cm.sec31_rate(date(2026, 4, 4)), 20.60)
        self.assertEqual(self.cm.sec31_rate(date(2024, 6, 1)), 27.80)

    def test_taf_schedule_by_year(self):
        self.assertEqual(self.cm.taf_rate(date(2026, 6, 1)), (0.000195, 9.79))
        self.assertEqual(self.cm.taf_rate(date(2027, 1, 1)), (0.000232, 11.61))
        self.assertEqual(self.cm.taf_rate(date(2025, 12, 31)), (0.000166, 8.30))

    def test_small_sale_fees_are_rounding_dominated(self):
        # $200 sale of 0.35 sh: true fees ~ $0.004 + $0.00007, billed $0.02
        c = self.cm.costs(date(2026, 9, 1), "sell", 0.35, 571.43)
        self.assertEqual(c.sec31, 0.01)
        self.assertEqual(c.taf, 0.01)
        self.assertEqual(c.cat, 0.01)
        self.assertEqual(c.fees, 0.03)

    def test_buy_pays_only_cat(self):
        c = self.cm.costs(date(2026, 9, 1), "buy", 0.35, 571.43)
        self.assertEqual(c.sec31, 0.0)
        self.assertEqual(c.taf, 0.0)
        self.assertEqual(c.cat, 0.01)

    def test_taf_cap(self):
        c = self.cm.costs(date(2026, 9, 1), "sell", 1_000_000, 1.0)
        self.assertEqual(c.taf, 9.79)

    def test_slippage_direction(self):
        self.assertGreater(self.cm.fill_price(100.0, "buy"), 100.0)
        self.assertLess(self.cm.fill_price(100.0, "sell"), 100.0)
        self.assertAlmostEqual(self.cm.fill_price(100.0, "buy"), 100.05)

    def test_paper_equivalent_is_frictionless(self):
        pm = self.cm.paper_equivalent()
        c = pm.costs(date(2026, 9, 1), "sell", 10, 100.0)
        self.assertEqual(c.total, 0.0)
        self.assertEqual(pm.fill_price(100.0, "buy"), 100.0)


if __name__ == "__main__":
    unittest.main()
