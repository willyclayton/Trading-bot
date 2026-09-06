# Trading-bot

Human-in-the-loop swing-trading bot for a $1,000 personal account. The plan is
in [`TRADING BOT PLAN.md`](TRADING%20BOT%20PLAN.md); the fact-check behind it is
in [`RESEARCH.md`](RESEARCH.md).

## Simulator console

A paper-only simulator that runs the plan's daily cadence against synthetic
bars and shows history, trades, orders, proposals, the shadow book and the
audit log in a browser. Python 3.10+, standard library only.

```bash
python -m sim.server                 # open http://127.0.0.1:8000
python -m sim.server --port 9000 --db /tmp/other.db --seed 42
python -m unittest discover -s tests
```

- **Step 1 day / +5 / +20** plays trading days. Proposals appear at the close
  and wait for you until 09:15 the next session, then expire.
- **Approve / Reject** act on the full trade plan. Rejects need a category
  (operational / data / discretion).
- **Auto** approves every proposal without a human and keeps advancing one day
  every 1.5 s. It stays on until you switch it off, including across restarts.
  It is a soak test of the machinery, not a trading mode (plan Rule 13).
- **Resume entries** clears the −15% drawdown pause after manual review.
- **Reset** wipes `sim.db` and starts a fresh $1,000 account.

State lives in `sim.db` (SQLite). The `trades`, `orders`, `proposals`,
`equity` and `audit` tables can be queried directly.

The P&L shown is on synthetic data and means nothing about any strategy
(plan Rule 7).
