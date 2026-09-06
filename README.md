# Trading-bot

Human-in-the-loop swing-trading bot for a $1,000 personal account. The plan is
in [`TRADING BOT PLAN.md`](TRADING%20BOT%20PLAN.md); the fact-check behind it is
in [`RESEARCH.md`](RESEARCH.md).

## Simulator console

A paper-only simulator that runs the plan's daily cadence against synthetic
bars and shows history, trades, orders, proposals, the shadow book and the
audit log in a browser. Python 3.10+, standard library only.

```
api/index.py     Vercel function: all /api/* requests
public/          the console (static HTML/JS/CSS, served as-is)
sim/             engine, approval state machine, cost model, stores, app logic
tests/           unit tests (python -m unittest discover -s tests)
vercel.json      rewrites, function config, daily cron
```

### Run locally

```bash
python -m sim.server                 # open http://127.0.0.1:8000
python -m sim.server --port 9000 --db /tmp/other.db --seed 42
python -m sim.server --no-auto-thread   # behave exactly like the Vercel deployment
python -m unittest discover -s tests
```

State lives in `sim.db` (SQLite). The `trades`, `orders`, `proposals`,
`equity` and `audit` tables can be queried directly.

### Using it

- **Step 1 day / +5 / +20** plays trading days. Proposals appear at the close
  and wait for you until 09:15 the next session, then expire.
- **Approve / Reject** act on the full trade plan. Rejects need a category
  (operational / data / discretion).
- **Auto** approves every proposal without a human and plays one day every
  1.5 s while a tab is open. The switch stays on until you turn it off,
  including across restarts and redeploys. It is a soak test of the
  machinery, not a trading mode (plan Rule 13).
- **Resume entries** clears the −15% drawdown pause after manual review.
- **Reset** wipes the state and starts a fresh $1,000 account.

The P&L shown is on synthetic data and means nothing about any strategy
(plan Rule 7).

## Deploy on Vercel

The console is static and the API is one Python serverless function, so the
project deploys with no build step. Two things differ from running locally,
both forced by serverless: there is no disk and no background process.

1. **Import the repo.** Vercel → Add New → Project → import `Trading-bot`.
   Framework preset *Other*, no build command, leave the output directory
   empty (`public/` is served automatically). Deploy.
2. **Add Redis.** Project → Storage → Create → *Upstash for Redis*
   (Marketplace, free tier is plenty) → connect to the project. This injects
   `KV_REST_API_URL` and `KV_REST_API_TOKEN`; redeploy so the function sees
   them. The header chip should read `store: redis`. Without Redis the
   function falls back to SQLite in `/tmp`, which does not survive between
   invocations — you will see history reset at random.
3. **Auto on Vercel.** There is no server-side loop; the open browser tab
   calls `/api/tick` on the interval, and `vercel.json` adds a daily cron
   that ticks once even with no tab open (Hobby plan: once per day, ±59 min;
   Pro allows per-minute). Concurrent ticks from two tabs are serialised by a
   short Redis lock; the loser gets HTTP 423 and the UI ignores it.
4. **Privacy.** The app has no login. If the URL should not be public,
   enable Deployment Protection (Settings → Deployment Protection → Vercel
   Authentication) so only you can open it.

Optional environment variables: `SIM_SEED` (default 7), `SIM_STATE_KEY`
(Redis key, default `sim:snapshot`).

The snapshot round-trips through Redis on every request; it is kept bounded
(rolling bar window, capped proposal/order/audit history) and compresses to
roughly 100 KB after 300 sessions and 320 KB after 2,000, well inside
Upstash's 1 MB request limit. Trades and the equity curve are never trimmed.
