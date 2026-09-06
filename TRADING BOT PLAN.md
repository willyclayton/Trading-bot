# Trading Bot — Project Plan

Self-contained handoff doc. Everything decided so far, the constraints that drive
it, and the guardrails that must not be removed.

**Revision 2 (2026-09-05).** Every factual claim in revision 1 was checked
against primary sources (regulator releases, Alpaca's own docs and staff, fee
schedules, the cited papers). Three premises were wrong and the plan has been
re-based on the corrected facts. The full verification ledger, with sources, is
in `RESEARCH.md`. Summary of what changed:

1. **Alpaca has no individual cash accounts.** Every Alpaca account is a margin
   account; under $2,000 equity it is "limited margin" at 1x buying power.
   Unsettled proceeds are usable immediately and good-faith violations do not
   apply. §2 is rewritten around what the account actually is.
2. **Fractional shares and bracket orders are mutually exclusive at Alpaca.**
   Fractional orders are market/limit/stop/stop-limit, `time_in_force=day`
   only. No bracket, OCO or OTO. Since fractional is mandatory at this account
   size, exits must be bot-managed. §3 is redesigned accordingly — and the
   redesign turns out to match the backtest engine better than brackets did.
3. **The null gate as written breaks on real data.** Random long-only entries on
   2016–2026 US equity data are *profitable* on average because the market went
   up. "Coin flip loses money" is a synthetic-data fact, not a harness
   invariant. The gate is restated as a paired cost test plus an
   exposure-matched benchmark (§5, Rule 2).

Also: the PDT rule really was eliminated on 4 Jun 2026 (verified, and Alpaca has
it in production), the fee rates in §2 are now real numbers with dates, and the
repository at revision 2 contained **none of the code described in §4** — see §0.

**Revision 3 (2026-09-05).** First code lands: a **paper-only simulator with an
operator console** (`sim/`, §3a). It runs the §3 cadence end to end against
synthetic bars — proposals, single-use approval tokens, TTL expiry, the double
risk gate, drift resize/STALE, idempotent DAY orders, bot-managed exits, the
date-indexed cost model, kill switch, drawdown pause, shadow book and audit
log — and shows history, trades, orders and the audit trail in a browser. It
also has an **Auto** switch that approves every proposal without a human and
keeps advancing days on its own, for unattended soak tests. 31 tests cover the
invariants in §5. What the simulator is *not*: evidence about the strategy
(Rule 7). Its P&L is noise by construction.

---

## 0. Status: a plan plus a simulator

Committed at revision 3, under `sim/`:

| Module | What it is | Plan section |
|---|---|---|
| `sim/approval.py` | Proposal state machine, single-use tokens, deterministic `client_order_id` | §3, §4 |
| `sim/costs.py` | `CostModel` with dated §31 / TAF / CAT schedules, round-up-to-cent, paper-equivalent mode | §2, Rule 8 |
| `sim/broker.py` | Stand-in broker: 422 on duplicate `client_order_id`, fractional ⇒ DAY + simple only, fills at next open | §3, Rules 4, 10 |
| `sim/market.py` | Deterministic synthetic daily bars for an 8-ETF universe | Rule 7 |
| `sim/engine.py` | The daily cadence: risk gate ×2, drift handling, exits, kill/pause, shadow book, audit | §3, §5 |
| `sim/store.py` | SQLite snapshot + browseable tables | §3 |
| `sim/server.py`, `sim/static/` | Stdlib HTTP server, Auto loop, single-page console | §3a |
| `tests/` | 31 tests: state machine, tokens, idempotency, costs, no-lookahead, TTL, stale, resize, paired cost test, turnover-scaled cost check, kill switch, persistence | §5 |

Still **not** here: `backtest.py` (walk-forward splitter, `load_bars()`), and
`sim_ttl.py`. The simulator's engine *is* a daily-bar engine with the §5
invariants tested, so it is the natural base for Phase 1 rather than a second
engine. **Phase 0, step 1 is now: keep the suite green in CI and replace
`sim/market.py` with real bars through `load_bars()` (§7).** Every §4 claim
that the simulator does not cover remains a claim.

---

## 1. Objective

Build a semi-automated, human-in-the-loop trading system for a **personal
account**, and find out — cheaply and fast — whether a real edge exists before
committing meaningful capital.

**Starting capital: $1,000.** This is a tuition budget, not an income source.
The deliverables that matter are a validated (or invalidated) strategy and
reusable infrastructure.

**Expectancy sanity check, kept here on purpose.** Risk per trade is 1% = $10 =
1R. A "+$200 great year" is therefore +20R. At ~50 trades/year that requires
+0.4R average expectancy *net of costs*, which is well above what robust,
published systematic swing systems deliver (roughly 0.1–0.25R). A realistic
great year is +5–12% ($50–$120); a bad year is −15–30%. Neither is financially
material. If the numbers ever look much better than this in a backtest, the
first hypothesis is that the backtest is wrong.

**Honest framing.** Academic base rates for retail active trading are poor. The
Taiwan study covering every trade on the exchange for 15 years (1992–2006) found
fewer than 1% of day traders predictably earned profits net of fees (Barber,
Lee, Liu, Odean & Zhang, *Learning, Fast or Slow*, 2020). The Brazilian
equity-futures study found 97% of individuals who persisted past 300 sessions
lost money (Chague, De-Losso & Giovannetti, *Day Trading for a Living?*, 2020).
Both citations verified. The plan below is structured to find out which side of
that line we're on while risking as little time and money as possible. It is
not structured on the assumption that we're in the 1%.

**A second honest point that revision 1 missed.** With ten years of daily data,
the t-statistic on a strategy's Sharpe ratio is roughly SR × √years. A true
annualised Sharpe of 0.5 gives t ≈ 1.6 over ten years; you need SR ≈ 1.0 to
reach t ≈ 3, the threshold the multiple-testing literature argues for. Six
months of live trading gives t ≈ 0.7 × SR — **live P&L over the horizon of this
plan cannot validate or invalidate the edge.** Live trading validates
operations and cost assumptions. The edge decision is made in Phase 2, on the
backtest, and nowhere else. See `RESEARCH.md` §5.

---

## 2. Hard constraints

These are not preferences. They follow from the account size and from what the
broker actually offers. Each row was verified against a primary source
(`RESEARCH.md` §1–§3).

| Constraint | Fact | Consequence |
|---|---|---|
| Account type | Alpaca opens **all** individual accounts as margin accounts. No cash-account option exists (confirmed by Alpaca staff, Jan 2026; "no definite timeline"). Under $2,000 equity the account is limited to 1x buying power. | We hold a margin account and enforce "cash-like" behaviour ourselves: `max_margin_multiplier="1"`, `no_shorting=true` via the account-configurations API, plus a risk-gate invariant that `cash ≥ 0` and `Σ(open buy notional) ≤ cash`. |
| Leverage / shorting | 1x buying power under $2k; shorting requires ≥ $2k; fractional sells are always marked long. | **Long-only, unlevered.** Enforced three ways (broker floor, account config, risk gate). |
| Settlement | T+1 (since 28 May 2024). At Alpaca, limited margin covers the float: sale proceeds are spendable immediately and GFVs do not apply to margin accounts. | Settlement is **not** a live constraint at Alpaca. The backtest keeps `settlement_days=1` as a conservative default and a portability guard (a true cash account elsewhere would bind), but no live rule depends on it. |
| PDT rule | Eliminated. SEC approved FINRA's Rule 4210 amendments 14 Apr 2026; effective 4 Jun 2026; Alpaca live in production, legacy fields removed 6 Jul 2026. Under the new intraday-margin standard an account at 1x cannot create an intraday margin deficit. | Day trading is no longer *legally* blocked. It is excluded for the reasons in §3 (approval latency, cost per unit of edge, statistical power), not by regulation. |
| Order types with fractional qty | Market, limit, stop, stop-limit only. `time_in_force=day` only. **No bracket / OCO / OTO.** No GTC. | Exits are managed by the bot, not by resting broker-side brackets. Any disaster stop is a DAY stop re-armed every morning. |
| Cost ceiling | Alpaca "Basic" data plan is free: SIP historical bars since 2016 (anything older than 15 minutes), IEX real-time, 200 req/min. | **$0/mo data.** Budget for everything else is $0–15/mo. No paid data. |
| Regulatory fees (sells only, pass-through) | SEC §31: **$20.60 per $1M** (effective 4 Apr 2026; was $0.00 from 14 May 2025 to 3 Apr 2026). FINRA TAF: **$0.000195/share, max $9.79/trade** (2026; rises to $0.000232 / $11.61 on 1 Jan 2027). CAT: $0.000003/share, buys and sells. Alpaca rounds each fee **up to the cent**. | On a $200 sale the true fees are ≈ $0.004 + $0.0001, but rounding makes it $0.02–0.03 ≈ 1–1.5 bp. Spread and slippage (≈ 5–20 bp per round trip on liquid ETFs) dominate; fees are a rounding term. Model both; calibrate slippage in Phase 3. |
| Position sizing | 1% risk = $10/trade. A 5% stop implies a $200 position; SPY has traded above $500/share since 2024. | **Fractional shares are mandatory.** Universe restricted to `fractionable=true` assets. Max 25% of equity per position, max 4 concurrent positions. |

**Everything above still pushes to the same place: long-only, multi-day swing
holds, liquid fractionable ETFs, daily bars.** Intraday is out. Human approval
latency is free at this horizon because the whole decision happens overnight
(§3).

**Broker: Alpaca (primary).** Reasons that survived verification: free paper
environment on the same API, free SIP daily history since 2016, fractional
shares, `trade_updates` stream (Alpaca's docs do call it the recommended way to
maintain order state), deterministic `client_order_id` support, account-level
`max_margin_multiplier` / `no_shorting` switches. Reasons that did **not**
survive: "native brackets" (not for fractional) and "cash account" (does not
exist).

**Fallback: Public.com API.** The only broker found that offers a true cash
account + fractional shares (≥ $5 notional) + commission-free API + client-side
idempotent order IDs. Drawbacks: no sandbox/paper environment, no push stream
for order events (poll), younger API. Interactive Brokers is ruled out (Lite has
no API access; the API does not accept fractional stock orders). Because no
broker offers fractional + brackets, the bot-managed exit design in §3 is
broker-independent, which is the point.

---

## 3. Architecture

Human sits at **approve-to-execute**: the bot builds the complete trade plan
(symbol, notional, entry, exit rules, reason, risk numbers) and requests
approval. Human taps approve or reject. Bot handles everything after that,
including exits — **exits are pre-approved as part of the plan and never wait on
a human.**

Chosen over the alternatives (signal dashboard, bot-enters/human-manages,
conviction tiers, human-sets-regime) because it forces the full trade to be
specified before it can be approved, and because the reject log becomes the
dataset for deciding what to automate later.

### Daily cadence (all times ET, trading days only)

```
16:00  market close
16:20  fetch completed daily bars (feed=sip, adjustment=all; end ≥ 15 min old
       so the free tier serves SIP) → data QA → strategy signals
16:25  for each signal: validate → risk gate → PENDING proposal + single-use
       token → Telegram message with Approve / Reject buttons
       TTL = 09:15 next trading day
09:15  for each APPROVED: risk gate AGAIN (kill switch, exposure, cash)
       → drift check vs. pre-market quote → resize or STALE
       → submit DAY market (or marketable limit) order,
         deterministic client_order_id, orders queue for the 09:30 open
09:45  reconcile fills (poll orders by client_order_id; trade_updates if the
       process is long-running) → positions table → audit log
16:20  (next days) exit rules evaluated on the completed bar per approved plan
       → exit orders submitted for next open, no approval needed
```

Two cron-style jobs plus a reconciliation step. State in SQLite. No process
needs to stay up, which removes the "bot crashed, positions unmanaged" failure
class for entries — but not for exits, so:

- **Dead-man check.** If the 16:20 job has not written a heartbeat by 16:45 on a
  trading day, the human is paged. Every open position is also visible in the
  Alpaca dashboard with its planned exit in the message thread.
- **Optional disaster stop.** A fractional DAY stop order at the plan's
  catastrophe level, re-armed each morning after the open. If used it **must**
  be modelled in the backtest (intraday low ≤ stop → fill at stop, gap-through
  → fill at open). Default: off in Phase 1–2, decide in Phase 3 from observed
  intraday ranges.

### Why this matches the backtest better than brackets would have

The engine fills at the next open on a close-based signal. A broker-side
intraday stop fills at the stop price during the day — a different fill model
that the engine never simulated. Close-evaluated exits executed at the next open
are exactly what the engine does. The fractional-order limitation forced the
right design.

### Parity rule

**Every live filter exists in the backtest, with the same parameters.** Drift
handling (resize to keep risk = 1% at the actual open; void only if
|gap| > `max_gap`), the exposure caps, the drawdown pause, the entry cap per day
— all of it. If it isn't in `backtest.py`, it doesn't run live. Otherwise
Phase 3 compares two different systems and learns nothing.

### Idempotency, concretely

`client_order_id` is derived deterministically from the proposal id. A retried
submit after a network timeout gets HTTP 422 `client_order_id must be unique`
from Alpaca. **That is the success path**: on 422, `GET
/v2/orders:by_client_order_id` and adopt the existing order. Never generate a
new id on retry.

### Human filtering is a bias

If the human approves some signals and rejects others, the live track record
measures *human + system*, not the system, and is no longer comparable to the
backtest. Mitigation: a **shadow book** records every proposal as if approved,
at the modelled fill. Reject reasons are categorised (operational / data
problem / discretion). Discretionary rejects are expected to trend toward zero;
if they don't, the strategy is not one the operator trusts and that is itself a
finding.

```
strategy signal (close of day t)
  → validate (structure)          ← inverted stops, bad side, non-fractionable
  → risk gate                     ← size, R:R, exposure, drawdown pause, kill switch
  → PENDING + approval token      ← Telegram, TTL = 09:15 t+1
  → human decides
  → risk gate AGAIN + drift check ← resize, or STALE if |gap| > max_gap
  → submit (idempotent, DAY)      ← queued for open
  → broker fill at open t+1
  → exits evaluated at each close per approved plan → next open, no approval
```

---

## 3a. Operator console (the simulator)

The same loop, runnable on a laptop with no broker, no keys and no schedule.
One click ("Step 1 day") plays one full trading day of the §3 cadence against
synthetic bars; the browser shows what the operator would see over Telegram
plus everything the audit log knows.

```
python -m sim.server            # http://127.0.0.1:8000, state in ./sim.db
python -m unittest discover -s tests
```

**What it shows.**

| Panel | Contents |
|---|---|
| Header | sim date/day, PAUSED / KILL SWITCH badges, **Auto** switch, Step 1/5/20, Resume entries, Reset |
| KPIs | equity, cash, invested, P&L, drawdown vs. high-water, trades / win rate / avg R, friction paid, positions vs. cap, pending count, shadow-book equity and real−shadow gap, discretionary reject rate |
| Equity chart | real book, shadow book, high-water mark, −15% pause line |
| Pending approval | the full trade plan per §3 (symbol, notional, qty, signal close, stop, target with R multiple, max hold, fill instruction, reason); **Approve** / **Reject** with a mandatory reject category (operational / data / discretion) |
| Universe | last close, day change, 40-bar sparkline, HELD marker |
| Positions | qty, entry, last, value, unrealised, stop, target, days held / max, queued exit and its reason |
| Trades | every closed round trip: gross on printed opens, slippage, fees, net, R, hold, exit reason; totals row |
| Orders & fills | every order with `client_order_id`, TIF/class, reference open vs. fill, slippage, fees |
| Proposals | every proposal ever created with status, decided-by, drift %, fill, block/reject note |
| Shadow book | positions and trades as if every PENDING proposal had been approved |
| Audit log | append-only: boot, proposal, approved/rejected/expired, submitted, fill, exit_queued, blocked, stale, PAUSE, RESUME, KILL, heartbeat |

**How a "day" maps to the cadence.** `Simulator.step()` generates the next
completed bar, then in order: TTL sweep (undecided ⇒ `expired`); for each
`approved` proposal the risk gate *again*, then the drift check against the
open (resize to keep risk = 1%, or `stale` if |gap| > `max_gap`), then an
idempotent DAY market order; all open orders fill at the open with the cost
model applied; the shadow book fills what it would have; positions are marked
at the close; exit rules run (stop / target / time / kill) and queue sell orders
for the next open; drawdown pause and kill switch are checked; new signals are
validated, gated and become `pending`. Then, if Auto is on, every `pending`
proposal is approved on the spot.

**Auto mode — what it is and what it is for.** Auto is a switch in the header.
While it is on, (1) every proposal is approved the moment it is created, with
`decided_by = "auto"`, and (2) a background loop advances one trading day every
`auto_interval_s` (1.5 s default). It stays on until turned off — including
across process restarts, because the flag is persisted with the rest of the
state in SQLite. Its purpose is a **soak test of the machinery with the human
removed**: leave it running and check that nothing wedges, cash never goes
negative, the position cap holds, exits always fire, the audit log stays
coherent and the store keeps up. Two properties fall out of the design and are
asserted in tests:

- With Auto on, **the real book equals the shadow book exactly** (same trades,
  same equity to the cent), because the shadow book is defined as
  "approve everything at the modelled fill". This is the zero point for the
  human-filtering bias in §3; any real−shadow gap that appears once Auto is
  off is the operator's doing.
- Auto goes through the **same** `approve()` path as a human tap: same token,
  same single-use check, same audit row. There is no back door around the
  state machine.

What Auto is **not**: a live trading mode. Turning it on live would delete
the approve-to-execute architecture in §3 and turn the system into a fully
automated one, which is a different project with a different risk profile.
The simulator and the paper account are the only places it may be used
(Rule 13). Auto does not bypass the risk gate, the drawdown pause or the kill
switch — under a pause it keeps stepping days but nothing new is proposed,
which is the intended behaviour; "Resume entries" is the manual review.

**Synthetic data.** `sim/market.py` produces deterministic drifting random
walks with a small mean-reverting pull and a plausible intraday range. It is
enough to exercise every code path (stops, targets, time exits, gaps beyond
`max_gap`, the position cap, the pause). It says nothing about any strategy.
Rule 7 applies: the console's P&L, win rate and R numbers are diagnostics of
the plumbing, not evidence. Phase 1 replaces this module with `load_bars()`
(§7) and the rest of the console keeps working.

---

## 4. What was claimed at revision 1 (partly superseded — see §0)

**34 tests passing** (claimed at rev 1; the simulator now has 31 committed
tests covering much of the same ground).

### `approval.py` + `test_approval.py` (21 tests)
State machine `proposed → pending → approved → submitted → live`, plus terminal
`rejected / expired / stale / blocked / failed`. Illegal transitions raise.

Protections, each with a test:
- **Single-use approval token** — double-tap, replayed webhook, retried
  notification cannot fire twice.
- **TTL expiry**, enforced by a sweep and at decision time.
- **Price drift check** — to be amended per §3: resize inside `max_gap`, STALE
  beyond it.
- **Risk re-checked at submit** — a kill switch flipped after approval blocks.
- **Deterministic `client_order_id`** — to be amended per §3: 422 on retry is
  adopted, not treated as failure.
- **Audit trail** records the did-nothing cases.

### `backtest.py` + `test_backtest.py` (13 tests)
Daily-bar engine. Three properties:
1. **No lookahead.** Signal on day *t* close → fill at day *t+1* open.
2. **Settlement modelled**, `settlement_days` configurable, default 1.
3. **Costs charged on every fill**: slippage (bp/side), §31 fee, TAF, CAT, each
   rounded up to the cent per Alpaca's practice.

Also: walk-forward splitter with disjoint out-of-sample windows.

### `sim_ttl.py`
Monte Carlo on approval latency vs. drift tolerance. Historically motivated the
swing horizon. Now moot: approval happens overnight and drift is handled by
resizing at the open, so the sim is documentation rather than a live component.

---

## 5. Traps already hit, and traps found in review — do not re-introduce

**Bug found in the engine (rev 1).** `if pending_orders:` treated an empty dict
as "no instruction." An empty dict means **go flat**. Every exit was silently
discarded, converting every strategy into buy-and-hold. Regression test:
`test_empty_dict_means_go_flat_not_no_op`.

**The null distribution on synthetic data (rev 1).** Coin-flip entries on
zero-drift synthetic paths, 200 paths, two years:

```
mean -10.6%   median -17.5%   std 29.7%
share losing: 75.5%   t-stat -5.05
```

(Internally consistent: 10.6 / (29.7/√200) = 5.05.) A quarter of pure
coin-flip strategies showed a profit. That is the base rate for "backtests
well, is actually noise." One clean backtest is close to zero evidence.

**The null gate on real data (found in review).** Real 2016–2026 equity data
has strong positive drift. Random long-only entries **will show profits**, and
often beat a mediocre strategy. Rule 2 of rev 1 ("if a coin flip becomes
profitable, the harness is broken") is therefore false on real data and would
have sent someone debugging a working harness. The correct invariants are:

1. *Paired cost test:* same random paths, costs on vs. off; costs-on must be
   lower on every path, and the mean difference must equal the modelled cost
   times turnover to within rounding.
2. *Exposure-matched benchmark:* a candidate strategy is compared against (a)
   random entries with the same number of trades and holding-period
   distribution and (b) buy-and-hold of the same universe scaled to the same
   average time-in-market. "Profit" is not evidence; profit **in excess of
   both** is.
3. *Turnover-scaled cost check:* strategy cost drag ≈ round-trips × modelled
   per-trip cost. If it isn't, fills or fees are being skipped.

**Forward-filled corpses (found in review).** When a symbol is delisted from an
exchange, Alpaca's bars stop and are forward-filled flat. A flat series has zero
volatility and looks like free money to a mean-reversion rule. Data QA flags any
run of ≥ 3 identical OHLC bars and any symbol whose `exchange` is `OTC`.

**Adjusted-data quality (found in review).** `adjustment=all` has had
corporate-action errors reported on Alpaca's forum over the years. QA
cross-checks a sample of adjusted closes against a second free source (Stooq
CSV) and flags > 0.1% disagreement.

**Paper fills are optimistic (found in review).** Alpaca's paper engine fills at
NBBO with no market impact, no regulatory fees, no dividends, 10% random partial
fills. Paper results are therefore an *upper bound*; the backtest must be run
with paper-equivalent settings (zero fees, zero slippage) when comparing to
paper, and with live settings when comparing to live.

**Sample-period bias (found in review).** Alpaca history starts 2016-01-04. The
sample is dominated by a rising market with short, sharp drawdowns (2018Q4,
2020, 2022). A long-only strategy that is mostly invested will look good for
reasons unrelated to skill. Hence the exposure-matched benchmark above, and
mandatory reporting of results in the drawdown sub-periods separately.

---

## 6. Plan, with kill gates

Phases are ordered by cost-to-learn. Durations are not estimated; each phase
ends when its gate is met or fails.

| Phase | Gate to pass | Kill condition |
|---|---|---|
| **0. Scope lock** | (a) `sim/` suite green in CI; an Auto soak run of ≥ 2,000 simulated sessions completes with cash ≥ 0 throughout, no unhandled exception, real == shadow to the cent. (b) Alpaca live account open and funded; `max_margin_multiplier="1"`, `no_shorting=true`, `fractional_trading=true` confirmed via `GET /v2/account/configurations`. (c) Paper account created at $1,000 with the same configuration. (d) With the live keys: `GET /v2/stocks/bars?feed=sip&timeframe=1Day&adjustment=all` returns 2016+ data for a test symbol at zero cost. (e) Candidate universe filtered to `fractionable=true`, `tradable=true`, exchange ≠ OTC. (f) Telegram bot receives a message and a button callback round-trips. | Any of (b)–(d) false ⇒ re-evaluate broker (Public.com) before writing more code. |
| **1. Data + harness** | Real daily bars through `load_bars()` with parquet cache and QA (calendar gaps, flat runs, cross-source check). Engine passes: paired cost test, turnover-scaled cost check, no-lookahead test, go-flat test, settlement test. Exposure-matched benchmark implemented. Cost model uses the §2 rates with effective dates. | Harness invariants cannot be made to hold on real data ⇒ stop and fix; do not proceed to strategies. |
| **2. Edge hunt — KILL GATE** | **Pre-register** ≤ 5 hypotheses, each with a one-paragraph economic rationale and fixed parameter grid, *before* running them (commit the list). Log every backtest run (trial count is an input to the deflated Sharpe ratio). A hypothesis passes only if, on walk-forward out-of-sample windows: (1) it beats both exposure-matched benchmarks net of costs, (2) deflated Sharpe > 0 after accounting for trials, (3) it is not carried by a single year or a single symbol (drop-one tests), (4) performance is stable across the pre-registered parameter neighbourhood, (5) it survives 2× the modelled slippage. | Nothing passes ⇒ **stop the project.** Write up what was learned. This outcome is the expected one and is a success. |
| **3. Paper trading** | Approval layer wired to Alpaca paper with §3 cadence. Gate is **reconciliation, not P&L** (a few weeks of paper cannot measure returns — §1): (a) live signal generator reproduces the backtest's signals on the same bars, every day, zero discrepancies; (b) every fill within a pre-set tolerance of the engine's modelled fill (paper-equivalent settings); (c) zero unhandled operational failures (missed job, stuck order, duplicate submit) over ≥ 20 round trips; (d) shadow book and audit log complete. Calibrate `slippage_bps` from paper fill vs. official open. | Signal mismatch that cannot be explained and fixed ⇒ the backtest was fiction; back to Phase 1. |
| **4. Live** | The $1,000. Kill switch at −30% of starting equity (liquidate, halt). Drawdown pause at −15% from high-water mark (no new entries until manual review). Gate to remain live: same reconciliation checks as Phase 3 against real fills; realised cost per round trip within 1.5× the model; no risk-gate breaches. **Live P&L is reported but is not a gate** — the sample is too small to mean anything. | Any risk-gate breach or an unexplained fill/cost divergence ⇒ halt and investigate. Kill switch ⇒ stop. |
| **5. Scale** | Only after Phase 4 has run through at least one drawdown regime with operations clean, and the Phase 2 evidence has been re-run with the additional out-of-sample data appended and still passes. Then revisit capital. | — |

Phase 2 is the point of the whole ordering. Total spend to reach it is time; the
data is free. Failing there is the cheap answer and the likely one.

---

## 7. Next task

Do not start until Phase 0 (a)–(d) are done, because (d) decides whether
Alpaca remains the broker.

Then: write `load_bars()` and make it the simulator's data source in place of
`sim/market.py` (same `Bar` interface: day, date, OHLCV per symbol), so the
console, the risk gate, the exits and the audit log run unchanged on real
history:

- `GET /v2/stocks/bars`, `feed=sip`, `timeframe=1Day`, `adjustment=all`,
  `start=2016-01-04`, `end` ≥ 15 min in the past, paginate on
  `next_page_token`, respect 200 req/min.
- Parquet cache per symbol with a `last_bar` watermark; incremental refresh.
- QA layer from §5: calendar gaps (Alpaca `GET /v2/calendar` is free), flat
  runs, OTC exclusion, cross-source spot-check.
- Universe: ~20–40 liquid fractionable ETFs (broad index, sector SPDRs,
  Treasuries, gold, international), point-in-time list committed to the repo,
  liquidity floor ADV > $50M. ETFs chosen over single names to minimise
  survivorship and event risk; the residual bias of choosing names that exist
  today is acknowledged in the write-up.
- Re-run the suite. The paired cost test and turnover-scaled cost check must
  hold on real data. The coin flip is **expected to be profitable** on this
  data; that is not a failure (§5).

---

## 8. Rules for any agent working on this

1. **Do not remove settlement modelling.** It is a conservative default and a
   portability guard. It is *not* a live constraint at Alpaca; do not add live
   logic that depends on it either.
2. **Do not loosen the null gate — and do not misread it.** The invariants are
   the paired cost test, the turnover-scaled cost check and the exposure-matched
   benchmark (§5). "The coin flip made money on real data" is expected, not a
   bug. "Costs-on beat costs-off" or "the strategy beat random entries by less
   than its cost drag" are bugs.
3. **Do not remove the double risk check** in `approval.py`. The check at
   submit is the one that matters.
4. **Do not make `client_order_id` random,** and treat Alpaca's 422
   `client_order_id must be unique` on retry as success-by-lookup, not failure.
5. **Do not add same-day round trips.** Legal now, but a same-day round trip
   pays a full round trip of friction for a few hours of exposure, and it lives
   outside the daily-bar engine, so nothing in the harness can evaluate it.
6. **Do not assume paid data.** Alpaca Basic, `feed=sip` for history.
7. **Never evaluate a strategy on synthetic data.** Synthetic data tests the
   engine only.
8. **Fee rates have effective dates.** §31 changes each April (and sometimes
   mid-year); TAF changes each 1 January (schedule through 2029 is published).
   `CostModel` takes a date and looks the rate up; never a single constant.
9. **Parity.** Every live filter exists in the backtest with the same
   parameters (§3). A live rule with no backtest twin is a bug.
10. **Never place a fractional order with anything but `time_in_force=day`,**
    and never use `order_class` other than simple with a fractional quantity.
    Both are rejected by Alpaca; both indicate the design in §3 has been
    forgotten.
11. **Pre-register before you run.** No strategy is backtested until its
    hypothesis, rationale and parameter grid are committed. Every run is
    logged; the trial count feeds the deflated Sharpe ratio.
12. When a test fails, **check whether the test is wrong** before changing the
    code. That already happened once here (the 17-of-20 assertion in rev 1).
13. **Auto mode is a test harness, not a trading mode.** It exists in the
    simulator and may be used against the paper account. It must never be
    wired to live keys; a live bot that approves its own proposals is a
    different architecture from §3 and would need its own plan. Auto must go
    through `approve()` like a human does — no path that skips the token, the
    second risk gate or the audit row.
14. **Do not read the simulator's P&L as evidence** (restating Rule 7 for the
    console, because it now shows a green number). Synthetic bars exercise
    code paths. Only Phase 2 on real data speaks to the edge.

---

## 9. Decisions

### Decided in revision 3

- **Operator console:** a local single-page web app served by a stdlib Python
  HTTP server, state in SQLite, zero third-party dependencies. Chosen over a
  Telegram-only view because history, trades, orders and the audit log need
  tables and a chart, and over a framework-based app because there is nothing
  to install and nothing to keep patched.
- **Auto switch:** approves every proposal and advances days on a timer; flag
  persisted so it stays on across restarts. For soak-testing the machinery and
  as the zero point of the human-filter metric (real == shadow when on).
  Simulator and paper only (Rule 13).
- **One engine, not two.** The simulator's daily-step engine carries the §5
  invariants as tests. Phase 1 swaps its data source rather than writing a
  separate `backtest.py`; the walk-forward splitter is added on top of it.
- **Reject categories are mandatory** on the Reject button (operational /
  data / discretion), so the discretionary-reject rate in §3 is measured from
  day one rather than reconstructed later.

### Decided in revision 2

- **Notification transport:** Telegram bot (free; inline Approve/Reject buttons;
  `callback_data` carries the single-use token; chat-id allow-list). SMS and
  push services either cost money or lack a reply channel.
- **TTL:** proposals expire at 09:15 ET on the next trading day. Approval is
  an evening task, not a real-time one.
- **Drift handling:** resize at the actual open to keep risk = 1%; void only if
  |gap| > `max_gap` (pre-registered, mirrored in the backtest).
- **Signal source:** hand-written rules first. Fewer degrees of freedom, lower
  overfitting risk, interpretable reject reasons. Model-driven signals are not
  considered until a rules-based edge has passed Phase 2.
- **Exit management:** bot-managed, close-evaluated, next-open execution.
  Disaster stop off by default (§3).
- **Hosting:** two scheduled jobs on any always-on machine or free-tier VM;
  SQLite state; no long-running process required.

### Still open

- Exact universe list and the liquidity floor (Phase 0 (e) produces the
  candidate list).
- The ≤ 5 pre-registered hypotheses for Phase 2. Candidates with a plausible
  economic story for long-only daily ETF swing: short-horizon mean reversion in
  broad indices, trend filters as drawdown control rather than return
  enhancement, calendar effects. All are well known and may be arbitraged away;
  that is what Phase 2 is for.
- Whether to run the disaster stop in Phase 3 (needs intraday-range data, which
  the free tier provides as 1-minute SIP bars if wanted).
- Tax handling. Short-term gains are ordinary income; wash-sale rules will be
  triggered constantly by repeated trades in the same ETF and are a bookkeeping
  chore, not a strategy input, at this size. Alpaca issues the 1099-B; keep an
  independent trade log for reconciliation.
