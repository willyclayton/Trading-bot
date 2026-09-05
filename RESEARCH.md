# Research & Stress-Test Log — revision 2 of `TRADING BOT PLAN.md`

Date: 2026-09-05. Purpose: verify every factual claim in the plan against a
primary source before any further code is written, and stress-test the
methodology. Each item records the claim, the finding, the impact on the plan,
and the source.

Legend: **✅ verified** · **⚠️ partly right / needs qualification** ·
**❌ wrong** · **❔ unverifiable from here**

---

## 1. Regulatory claims

| # | Claim (rev 1) | Status | Finding |
|---|---|---|---|
| 1.1 | "$1,000 < $2,000 FINRA margin minimum ⇒ cash account only" | ⚠️ | The $2,000 minimum equity for margin (Reg T / FINRA 4210) is real, and Alpaca applies it: under $2,000 the account is restricted to 1x buying power and cannot short. But the *consequence* is wrong — Alpaca does not offer cash accounts (see 2.1). The substance (no leverage, no shorting) holds; the account type does not. |
| 1.2 | T+1 settlement | ✅ | US equities settle T+1 since 28 May 2024. |
| 1.3 | "Buying with unsettled funds risks GFVs; 3–4 in 12 months ⇒ 90-day restriction" | ⚠️ | Correct description of *cash-account* mechanics (GFV thresholds are broker policy, not statute; Reg T §220.8 technically permits a 90-day freeze after one). **Irrelevant at Alpaca**: margin/limited-margin accounts cover the float, unsettled proceeds are spendable immediately, GFVs do not apply. Kept in the plan only as a note for the Public.com fallback (Public: 4th GFV ⇒ settled-funds-only, 5th ⇒ sell-only 90 days). |
| 1.4 | "PDT rule not applicable; $25k floor eliminated 4 Jun 2026" | ✅ | SEC Release 34-105226 (14 Apr 2026) approved SR-FINRA-2025-017; FINRA Regulatory Notice 26-10 set the effective date 4 Jun 2026 with a phase-in window to 20 Oct 2027. Alpaca implemented in production and removed `pattern_day_trader`, `daytrade_count`, `daytrading_buying_power`, `dtbp_check`, `pdt_check` from the API on 6 Jul 2026. Nuance: the rev-1 reasoning ("only ever applied to margin accounts, we have a cash account") was wrong — our Alpaca account *is* a margin account and PDT *would* have applied before June 2026. The conclusion survives on the new facts. Under the intraday-margin standard, an account at 1x buying power cannot generate an intraday margin deficit. |

Sources: [SEC 34-105226](https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf) ·
[FINRA RN 26-10](https://www.finra.org/rules-guidance/notices/26-10) ·
[Alpaca blog: FINRA retires PDT](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/) ·
[Alpaca changelog 2026-06-03](https://docs.alpaca.markets/us/changelog/2026-06-03-pdt-651df23) ·
[Alpaca: understanding the intraday margin rule](https://docs.alpaca.markets/docs/understanding-finras-new-intraday-margin-rule-and-the-end-of-pdt)

---

## 2. Alpaca claims

| # | Claim (rev 1) | Status | Finding |
|---|---|---|---|
| 2.1 | "Cash account" at Alpaca | ❌ | Alpaca staff (Dan Whitnable, forum, 26 Jan 2026): "Currently all Alpaca accounts are margin accounts… Cash accounts will first be available as IRA accounts. Individual cash accounts will be rolled out after that but there is no definite timeline." Broker-API docs say the same. Mitigation available: `PATCH /v2/account/configurations` with `max_margin_multiplier="1"` (allowed values "1","2","4") and `no_shorting=true`; the API then rejects orders exceeding cash. |
| 2.2 | "Native bracket/OCO/OTO order classes" | ⚠️ | True for whole-share orders. **False for fractional/notional orders**: the Fractional Trading doc lists market, limit, stop, stop-limit with `time_in_force=day` only; the errors guide documents 422 rejections ("Fractional orders must be DAY orders"); forum threads from 2023–2025 confirm brackets are rejected for fractional qty. Rev 1 noted a comparison site claiming "Alpaca lacks native brackets" and dismissed it — for this account size the site was effectively right. |
| 2.3 | `trade_updates` websocket is the recommended way to maintain order state | ✅ | Orders doc: "Updates on open orders are also delivered through the streaming interface, which is the recommended method for maintaining order state." For a cron-style bot, polling `GET /v2/orders:by_client_order_id` is sufficient and simpler; the stream is optional. |
| 2.4 | Fractional shares available | ✅ | Default-on for live and paper; `fractionable=true` per asset; qty/notional to 9 decimals; minimum $1. Fractional sells are always marked long (no shorting possible). Fills are principal/riskless-principal at the NBBO at submission — expect fills marginally worse than the printed open; calibrate in Phase 3. |
| 2.5 | Free paper trading on the same API | ✅ | `paper-api.alpaca.markets`, same spec. Caveats documented by Alpaca: no market impact, no regulatory fees, no dividends, fills at NBBO, 10% random partial fills, order quantity not checked against displayed liquidity. Paper is an upper bound on fill quality. Default balance $100k — reset to $1,000 and apply the same account configuration. |
| 2.6 | "Free IEX data tier" | ⚠️ | Correct but understated. The free Basic plan gives **SIP (consolidated) historical bars since 2016-01-04** for any query whose `end` is ≥ 15 minutes old, 200 req/min, plus IEX-only real-time. Daily bars must be requested with `feed=sip`; IEX-only daily bars carry ~2.5% of volume and are wrong for backtesting. `adjustment=all` gives split+dividend adjusted prices. One ambiguity: the paper-trading page says *paper-only* (no brokerage) accounts get IEX data only — Phase 0 (d) verifies SIP history with the live keys. |
| 2.7 | Deterministic `client_order_id` dedupes retries | ⚠️ | Alpaca enforces uniqueness among active orders and returns HTTP 422 `{"code":40010001,"message":"client_order_id must be unique"}` on a duplicate. So a retried submit is *rejected*, not silently deduped — the bot must treat 422 as "look up the existing order and adopt it". Plan §3 and Rule 4 updated. |
| 2.8 | Orders can be placed overnight for the open | ✅ | "Orders not eligible for extended hours submitted after 4:00pm ET will be queued up for release the next trading day"; DAY orders submitted pre-open are released at 09:30. True MOO (`opg`) is restricted to Elite Smart Router users, so the bot uses queued DAY market or marketable-limit orders. |
| 2.9 | Regulatory fee pass-through | ✅ | Fee schedule (1 Sep 2026): SEC §31 $0.0000206 × trade value (sells), TAF $0.000195/share max $9.79 (sells), CAT $0.000003 per share (buys and sells). Docs: fees accrued intraday, charged EOD, **rounded up to the cent**. |
| 2.10 | Delisted-symbol data | ⚠️ (new) | Alpaca provides bars only for exchange-listed symbols. After a delisting to OTC the series is forward-filled flat (staff confirmation, Dec 2025). Backtests must exclude `exchange=OTC` and flag flat runs. Historical adjusted data has had corporate-action errors reported over the years; cross-check a sample against a second source. |

Sources: [Margin & short selling](https://docs.alpaca.markets/docs/margin-and-short-selling) ·
[Forum: cash-only account option (Jan 2026)](https://forum.alpaca.markets/t/dan-wheres-the-cash-only-account-option/18353) ·
[Unsettled funds](https://alpaca.markets/learn/understanding-unsettled-funds) ·
[Fractional trading](https://docs.alpaca.markets/docs/fractional-trading) ·
[Orders at Alpaca](https://docs.alpaca.markets/docs/orders-at-alpaca) ·
[Common API errors](https://alpaca.markets/learn/how-to-fix-common-trading-api-errors-at-alpaca) ·
[Account configurations](https://docs.alpaca.markets/reference/patchaccountconfig-1) ·
[Paper trading](https://docs.alpaca.markets/docs/paper-trading) ·
[About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api) ·
[Market data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) ·
[Historical stock data](https://docs.alpaca.markets/us/docs/historical-stock-data-1) ·
[Forum: delisted symbol forward-filled](https://forum.alpaca.markets/t/same-daily-prices-for-stock-although-it-keeps-trading/18218) ·
[Brokerage fee schedule (PDF)](https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf) ·
[Regulatory fees](https://docs.alpaca.markets/us/docs/regulatory-fees)

---

## 3. Fee rates for `CostModel` (with effective dates)

| Fee | Applies to | Rate | Effective | Source |
|---|---|---|---|---|
| SEC §31 | sells | $0.00 per $1M | 14 May 2025 – 3 Apr 2026 | SEC Fee Rate Advisory FY2026 |
| SEC §31 | sells | **$20.60 per $1M** | 4 Apr 2026 – (60 days after FY2027 appropriation) | [SEC advisory](https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2), [Order 34-104909](https://www.sec.gov/files/rules/other/2026/34-104909.pdf) |
| FINRA TAF (equities) | sells | $0.000166/share, max $8.30 | 2024–2025 | [FINRA fee schedule](https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule) |
| FINRA TAF (equities) | sells | **$0.000195/share, max $9.79** | 1 Jan 2026 | same |
| FINRA TAF (equities) | sells | $0.000232/share, max $11.61 | 1 Jan 2027 | same |
| FINRA TAF (equities) | sells | $0.000240/share, max $12.05 | 1 Jan 2028 | same |
| FINRA TAF (equities) | sells | $0.000249/share, max $12.50 | 1 Jan 2029 | same |
| FINRA CAT | buys and sells | $0.000003/share | current | Alpaca fee schedule |
| Broker rounding | each | round **up** to $0.01 | — | Alpaca regulatory-fees doc |

`CostModel` must take the trade date and look the rate up. The historical §31
schedule for backtests (2016–2025) should be loaded from the SEC fee-rate
advisories page rather than hard-coded; the rate has ranged from roughly $5 to
$28 per $1M over 2016–2024 and was $0 from 14 May 2025 to 3 Apr 2026. For the sizes involved, rounding dominates
the true rate anyway (see §7).

---

## 4. Academic citations

| Claim | Status | Detail |
|---|---|---|
| "Taiwan study … 15 years … fewer than 1% of day traders earned reliable profits net of fees" | ✅ | Barber, Lee, Liu, Odean & Zhang, *Learning, Fast or Slow*, Review of Asset Pricing Studies (2020); data 1992–2006, all trades on the Taiwan Stock Exchange. Related: Barber, Lee, Liu & Odean, *The Cross-Section of Speculator Skill*, J. Financial Markets (2014). |
| "Brazilian futures study … 97% of traders who persisted past 300 sessions lost money" | ✅ | Chague, De-Losso & Giovannetti, *Day Trading for a Living?* (SSRN 3423101, 2020); Brazilian equity-index futures, cohorts starting 2013–2015; 1,551 persisted ≥ 300 days, 97% lost, 1.1% earned above minimum wage, no evidence of learning. |

---

## 5. Methodology stress test

### 5.1 Statistical power (new, and the most important finding)

For daily returns the standard error of an annualised Sharpe ratio estimate is
≈ 1/√T with T in years (Lo 2002; the SR² correction is negligible at daily
frequency). Hence **t ≈ SR × √T**.

| True annual SR | t over 10 yrs (backtest) | t over 0.5 yr (Phase 4) | t over 1 month (Phase 3) |
|---|---|---|---|
| 0.25 | 0.8 | 0.18 | 0.07 |
| 0.50 | 1.6 | 0.35 | 0.14 |
| 0.75 | 2.4 | 0.53 | 0.22 |
| 1.00 | 3.2 | 0.71 | 0.29 |

Harvey, Liu & Zhu (2016) argue that, given the number of strategies the
industry has tried, a new anomaly should clear t ≈ 3. Over Alpaca's ten years
of free history that means SR ≈ 1.0 for a single-instrument strategy — an
extraordinary claim for retail long-only swing trading. Consequences adopted in
the plan:

- Phase 3/4 gates are **reconciliation gates**, not P&L gates. Live P&L is
  reported, never used to decide.
- Phase 2 should look for *breadth* (many symbols, many independent bets) to
  raise power, and must pre-register hypotheses to keep the trial count small.
- The plan says so explicitly (§1) so nobody later "validates" the edge with
  six months of live returns.

### 5.2 The null gate on real data

Rev 1's synthetic null (zero drift) gives the coin flip a negative expectation
equal to cost drag. Real 2016–2026 US equity data has positive drift, so
random long-only entries earn a share of the equity premium. The rev-1 gate
("coin flip must lose") and Rule 2 ("if a coin flip becomes profitable the
harness is broken") would misfire on real data. Replaced with three invariants
that hold regardless of drift: paired cost test (costs-on < costs-off on every
path, difference = turnover × per-trip cost), turnover-scaled cost check, and an
exposure-matched benchmark (random entries with matched trade count and
holding-period distribution; buy-and-hold scaled to matched time-in-market).

### 5.3 Multiple testing and backtest overfitting

- Pre-register ≤ 5 hypotheses with economic rationale and a fixed parameter
  grid before running (commit the list).
- Log every backtest run; the number of trials is an input to the **Deflated
  Sharpe Ratio** (Bailey & López de Prado 2014) and to Harvey & Liu's (2015)
  haircut.
- Walk-forward with disjoint out-of-sample windows (already in the engine per
  rev 1); parameters chosen on in-sample only.
- Robustness: drop-one-year, drop-one-symbol, parameter-neighbourhood
  stability, 2× slippage. A result that survives only at one grid point or in
  one year is noise.
- References: Bailey, Borwein, López de Prado & Zhu, *Pseudo-Mathematics and
  Financial Charlatanism* (Notices AMS 2014); White, *A Reality Check for Data
  Snooping* (Econometrica 2000); Hansen, *A Test for Superior Predictive
  Ability* (JBES 2005).

### 5.4 Walk-forward on ten years of daily swing data

At ~50 trades/year a hypothesis produces ~500 trades over the sample; a
walk-forward with, say, 3-year in-sample / 1-year out-of-sample rolling windows
gives ~7 OOS years. That is enough to *reject* bad ideas, not enough to confirm
good ones with confidence (5.1). The plan's kill gate is asymmetric on purpose.

### 5.5 Survivorship and selection

- Choosing symbols that are liquid *today* is a survivorship filter. ETFs
  reduce but do not remove it. The universe is fixed point-in-time and
  committed; the bias is acknowledged in the write-up rather than pretended
  away.
- Human approval is a selection filter on the live track record (5.6).

### 5.6 Human-in-the-loop as a bias source (new)

Selective approval makes live results measure *operator + system*. Mitigation
adopted: shadow book of all proposals at modelled fills; categorised reject
reasons; discretionary reject rate tracked as a metric expected to trend to
zero.

### 5.7 Live/backtest parity (new)

Every live filter (drift resize / `max_gap` void, exposure caps, drawdown
pause, per-day entry cap, optional disaster stop) must exist in the engine with
identical parameters. Without this, Phase 3's "paper within tolerance of
backtest" compares two different systems. Adopted as Rule 9.

### 5.8 Exit design vs. engine fill model (new)

The engine fills at next open on close-based signals. A broker-side intraday
stop fills at the stop price intraday — a fill model the engine never
simulated. Close-evaluated exits executed at next open (forced on us by the
fractional-order limitation) are exactly what the engine does. Net effect: the
constraint improved parity.

---

## 6. Broker alternatives (for the record)

| Broker | Cash account | Fractional via API | Brackets w/ fractional | Paper env | Order-event push | Notes |
|---|---|---|---|---|---|---|
| **Alpaca** (primary) | No (margin only; 1x under $2k) | Yes, DAY only, mkt/lmt/stop/stop-lmt | No | Yes | Yes (`trade_updates`) | Free SIP history since 2016. `max_margin_multiplier="1"`, `no_shorting=true`. |
| **Public.com** (fallback) | Yes (`useMargin=false`) | Yes, ≥ $5 notional, mkt/lmt/stop/stop-lmt | No | **No** | **No** (poll) | Client-generated UUID `orderId` is idempotent on retry. Historical data endpoints (`TEN_YEARS`). Younger API; personal-use programme. |
| Interactive Brokers | Yes | **No** per IBKR support replies (fractional via TWS GUI / FIX only; API rejects cash-qty for stocks) | — | Yes | Yes | IBKR Lite has no API access; Pro has commissions. Ruled out; re-verify with IBKR if ever reconsidered. |
| Tradier | Yes | No | — | Yes | Yes | No fractional. Ruled out at $1,000. |

No broker offers fractional + broker-side brackets, so the bot-managed exit
design is required regardless of broker.

Sources: [Public API docs](https://public.com/api/docs) ·
[Public: place order](https://public.com/api/docs/resources/order-placement/place-order) ·
[Public: order limits](https://public.com/api/docs/order-limits) ·
[IBKR Campus: fractional shares (staff reply: "supported via FIX/CTCI but not via API")](https://www.interactivebrokers.com/campus/trading-lessons/fractional-shares/)

---

## 7. Worked cost example (one $200 round trip in a liquid ETF)

| Component | Buy | Sell | Note |
|---|---|---|---|
| Half-spread (≈1–3 bp on SPY/QQQ-class ETFs) | $0.02–0.06 | $0.02–0.06 | wider in the first minutes after the open |
| Slippage vs. printed open (assume 5 bp/side default) | $0.10 | $0.10 | calibrate in Phase 3 |
| SEC §31 ($20.60/M) | — | $0.0041 → **$0.01** | rounded up |
| TAF (0.35 sh × $0.000195) | — | $0.00007 → **$0.01** | rounded up |
| CAT (0.35 sh × $0.000003) | ≈$0 → **$0.01**? | ≈$0 | Alpaca rounds the *daily total* up; treat as $0.01 |
| **Total** | | **≈ $0.30–0.40 ≈ 15–20 bp** | per round trip |

At 50 round trips/year × $200 = $10,000 turnover, friction is ≈ $15–20/year =
1.5–2% of the account. Against a realistic "great year" of +5–12%, that is
material but not dominant; trade frequency is the lever. Regulatory fees are
< 15% of the total; slippage assumptions matter far more than fee precision.

---

## 8. Findings, ranked by severity, with resolution

| Sev | Finding | Resolution in plan |
|---|---|---|
| **Blocker** | Code described in §4 is not in the repository. | §0: commit first; Phase 0 (a). |
| **High** | Fractional + bracket incompatible at Alpaca. | §3 redesigned: bot-managed close-evaluated exits, DAY orders, optional re-armed disaster stop; Rule 10. |
| **High** | No cash account at Alpaca; account is margin. | §2 rewritten; enforce 1x via config + risk gate; settlement modelling kept as conservative default, not live rule; Rule 1 reworded. |
| **High** | Null gate / Rule 2 false on real (drifting) data. | §5 and Rule 2 restated as paired cost test + turnover check + exposure-matched benchmark. |
| **High** | Live/paper P&L cannot validate the edge at this horizon (power). | §1 honest framing; Phases 3–4 gates are reconciliation, not P&L. |
| Medium | `client_order_id` retry returns 422, not a silent dedupe. | §3 idempotency section; Rule 4. |
| Medium | Human approval filters the live record. | Shadow book + categorised rejects. |
| Medium | Live filters (drift, caps) absent from backtest ⇒ no parity. | Parity rule (Rule 9); drift handled by resizing with mirrored `max_gap`. |
| Medium | Paper fills optimistic (no fees, NBBO, no impact). | Compare paper to paper-equivalent backtest settings; calibrate slippage. |
| Medium | Delisted symbols forward-filled; adjusted-data errors. | Data QA layer in Phase 1 / §7 of plan. |
| Low | Fee rates were placeholders. | §2 / §3 here: real rates with effective dates; `CostModel` date-indexed. |
| Low | "Free IEX tier" undersells free SIP history; IEX daily bars would be wrong. | `feed=sip` mandated. |
| Low | `sim_ttl.py` premise (minutes of latency) moot under overnight approval. | Reclassified as documentation. |
| Info | PDT elimination date correct; reasoning corrected. | §2. |

---

## 9. Not verifiable from here

- The 34 tests, the engine bug, the null-distribution numbers and the
  `sim_ttl.py` results: no code in the repo. The null numbers are at least
  internally consistent (t = 10.6 / (29.7/√200) = 5.05).
- Whether a Paper-Only (non-brokerage) Alpaca account returns SIP history on
  the free plan — the docs are ambiguous. Phase 0 (d) tests it with the live
  keys, which is the configuration that matters.
- Actual pre-market quote quality on IEX for the 09:15 drift check. Best
  effort; the resize-not-void policy makes it low-stakes.
