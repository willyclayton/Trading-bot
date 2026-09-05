# Trading Bot — Project Plan

Self-contained handoff doc. Everything decided so far, the constraints that drive
it, and the guardrails that must not be removed.

---

## 1. Objective

Build a semi-automated, human-in-the-loop trading system for a **personal
account**, and find out — cheaply and fast — whether a real edge exists before
committing meaningful capital.

**Starting capital: $1,000.** This is a tuition budget, not an income source. A
great year is roughly +$200; a bad year is roughly −$300. Neither is financially
material. The deliverables that matter are a validated (or invalidated) strategy
and reusable infrastructure.

**Honest framing, kept here on purpose.** Academic base rates for retail active
trading are poor: the Taiwan study covering every trade on a national exchange
for 15 years found fewer than 1% of day traders earned reliable profits net of
fees; a Brazilian futures study found 97% of traders who persisted past 300
sessions lost money. The plan below is structured to find out which side of that
line we're on while risking as little time and money as possible. It is not
structured on the assumption that we're in the 1%.

---

## 2. Hard constraints

These are not preferences. They follow from the account size and account type.

| Constraint | Consequence |
|---|---|
| $1,000 < $2,000 FINRA margin minimum | **Cash account only.** No margin. |
| Cash account | **No shorting.** Long-only. |
| T+1 settlement | Sale proceeds unusable until next business day. ~1 full-capital round trip per day. |
| Good faith violations | Buying with unsettled funds risks GFVs; enough in 12 months (3–4, broker-dependent) triggers a 90-day settled-cash restriction. |
| PDT rule | **Not applicable.** It only ever applied to margin accounts. The $25k floor was eliminated 4 Jun 2026 regardless. |
| Cost ceiling | $50/mo of data = 60% of the account per year. Budget is **$0–15/mo**. Free tier data only. |
| Position sizing | 1% risk = $10/trade. **Fractional shares are mandatory.** |

**Everything above pushes to the same place: long-only, multi-day swing holds,
liquid names, daily bars.** Intraday is ruled out. This is convenient — it also
means human approval latency is nearly free (see §4).

**Broker: Alpaca.** Native bracket/OCO/OTO order classes, a `trade_updates`
websocket that Alpaca's own docs call the recommended way to maintain order
state, fractional shares, free paper trading on the same API surface, and a free
IEX data tier. (Note: broker comparison sites are largely affiliate-funded and
contradict each other — one claims Alpaca lacks native brackets, which the
official docs contradict. Trust the docs.)

---

## 3. Architecture

Human sits at **approve-to-execute**: the bot builds the complete order (symbol,
side, qty, entry, stop, target, reason) and requests approval. Human taps
approve or reject. Bot handles the rest.

Chosen over the alternatives (signal dashboard, bot-enters/human-manages,
conviction tiers, human-sets-regime) because it forces the full trade to be
specified before it can be approved, and because the reject log becomes the
dataset for deciding what to automate later.

```
strategy signal
  → validate (structure)        ← inverted stops, bad direction
  → risk gate                   ← size, R:R, daily loss, kill switch
  → PENDING + approval token    ← human notified
  → human decides (TTL-bound)
  → risk gate AGAIN + drift check
  → submit (idempotent)
  → broker
```

---

## 4. What is already built and tested

**34 tests passing.**

### `approval.py` + `test_approval.py` (21 tests)
The gate between signal and live order. State machine:
`proposed → pending → approved → submitted → live`, plus terminal
`rejected / expired / stale / blocked / failed`. Illegal transitions raise.

Protections, each with a test:
- **Single-use approval token** — double-tap, replayed webhook, or retried
  notification cannot fire twice.
- **TTL expiry**, enforced both by a sweep timer and at decision time (handles
  the race where the tap arrives after the deadline but before the sweep runs).
- **Price drift check** — approved at 100, market at 103 → `STALE`, no order.
- **Risk re-checked at submit** — a kill switch flipped after approval still blocks.
- **Deterministic `client_order_id`** derived from the proposal, so a network
  retry gets deduped by the broker instead of doubling the position.
- **Audit trail** records the did-nothing cases too (bad token, ignored submit).

### `backtest.py` + `test_backtest.py` (13 tests)
Daily-bar engine. Three non-negotiable properties:
1. **No lookahead.** Signal on day *t* close → fill at day *t+1* open. Tested.
2. **Settlement modeled.** Proceeds sit in a pending bucket until T+1. Most
   retail backtests assume infinite same-day buying power, which flatters any
   strategy that recycles capital quickly.
3. **Costs charged on every fill.** Slippage, SEC Section 31 fee, FINRA TAF.

Also: walk-forward splitter with disjoint out-of-sample windows.

### `sim_ttl.py`
Monte Carlo on approval latency vs. drift tolerance. At 60% annual vol, a
5-minute response with 0.5% tolerance voids ~24% of signals; 15 minutes voids
~50%. Irrelevant at multi-day horizons — which is why §2 landing on swing trading
matters.

---

## 5. Traps already hit — do not re-introduce

**Bug found in the engine.** `if pending_orders:` treated an empty dict as "no
instruction." An empty dict means **go flat**. Every exit signal was silently
discarded, quietly converting every strategy into buy-and-hold — it bought once
in 200 days and never sold. A harness with this bug makes mediocre strategies
look good. Regression test: `test_empty_dict_means_go_flat_not_no_op`.

**The null distribution.** Coin-flip entries on zero-drift synthetic data, 200
paths:

```
mean -10.6%   median -17.5%   std 29.7%
share losing: 75.5%   t-stat -5.05
```

**A quarter of pure coin-flip strategies showed a profit over two years.** That
is the base rate for "backtests well, is actually noise." One clean backtest is
close to zero evidence. This is the single most important number in this
document.

Consequence: the null gate is a **paired** test (same price path, costs on vs.
off) plus a t-stat on the mean — not a per-path win count. An earlier version
asserted 17-of-20 individual losers, which was statistically impossible given
30% path dispersion. The harness was right; the test was wrong.

---

## 6. Plan, with kill gates

| Phase | Duration | Gate to pass |
|---|---|---|
| **0. Scope lock** | 1 wk | Alpaca paper + cash account open. Fractional + free data tier confirmed. |
| **1. Data + harness** | 4 wks | Real daily bars flowing through `load_bars()`. Coin-flip null gate still fails. |
| **2. Edge hunt** | 6–8 wks | **KILL GATE.** Something survives costs out-of-sample on walk-forward *and* has a plausible economic reason to exist. If nothing does, **stop the project.** |
| **3. Paper trading** | 4 wks | Approval layer wired to Alpaca paper. Paper results within tolerance of backtest. Divergence ⇒ the backtest was fiction. |
| **4. Live** | 6 mo | The $1,000. Kill switch at −30%. Live results match paper. |
| **5. Scale** | — | Only after Phase 4 holds across differing market conditions. Then revisit capital. |

Phase 2 is the point of the whole ordering. Total spend to reach it is time plus
maybe a few hundred dollars of data. Failing there is a **success** — it's the
cheap answer.

---

## 7. Next task

Wire the Alpaca free-tier daily-bar loader into `backtest.py::load_bars()`, with
local caching (parquet or sqlite) so the same bars aren't re-fetched. Universe:
liquid US equities/ETFs. Then re-run the full test suite — the null gate must
still fail on real data.

---

## 8. Rules for any agent working on this

1. **Do not remove settlement modeling** to "simplify" the engine. It is the
   account's actual constraint.
2. **Do not loosen or delete the null gate.** If a coin flip becomes profitable
   in the harness, the harness is broken. Debug the harness, not the test.
3. **Do not remove the double risk check** in `approval.py`. The check at submit
   is the one that matters; conditions change while a human thinks.
4. **Do not make `client_order_id` random.** It is deterministic on purpose —
   that is what makes retries safe.
5. **Do not add same-day round trips** to any strategy. Cash account, T+1.
6. **Do not assume paid data.** Cost ceiling is $15/mo.
7. **Never evaluate a strategy on synthetic data.** Synthetic data tests the
   engine only.
8. **Verify SEC Section 31 and FINRA TAF rates** before trusting cost output.
   The defaults in `CostModel` are placeholders and these rates get reset.
9. When a test fails, **check whether the test is wrong** before changing the
   code. That already happened once here.

---

## 9. Open decisions

- Universe definition (which ETFs / how many names, liquidity floor).
- Notification transport for approvals (push, SMS, Telegram) and the TTL value.
- Where the signal comes from: hand-written rules vs. anything model-driven.
  Large architecture fork, not yet decided.
- Tax handling. Short-term gains are ordinary income and wash-sale rules apply
  even at this size. Broker issues a 1099-B.
