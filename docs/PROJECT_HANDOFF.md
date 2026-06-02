# Forex Chart Agent Handoff

This document explains the project from beginning to end for a new developer, trader, or operator.

## Current Verdict

This project is an OANDA forex chart annotation and paper-forward-testing agent. It does not place trades and should not be treated as a profitable trading bot.

The latest forward test is weak:

- Overall 3R result: `3` TP, `9` SL, `25.0%` win rate.
- Strict `Rule` route: `2` TP, `1` SL, plus `3` timeouts.
- `M15_SIMPLE`: `1` TP, `1` SL, plus breakevens/timeouts.
- AI opportunity routes are not proving useful so far.

Practical recommendation:

- Keep studying only `Rule` and maybe `M15_SIMPLE`.
- `GEMMA_HIGH_VALUE_OPPORTUNITY` has been removed after poor forward-test results. Do not restore it without fresh evidence.
- Do not trust `GEMMA_SMC_RAG_OPPORTUNITY` or opportunity routes for trade decisions.
- Treat every route as paper-only until long forward testing proves otherwise.
- Treat `DYNAMIC_SCORE` and `REGIME_RANGE` as new experiments, not as evidence of profitability yet.

## Exit Model (Partial-Then-Trail)

As of the exit-model redesign, every new forward test uses one shared trade-management model instead of an all-or-nothing 3R target:

- Risk is treated as 1 unit split in half.
- At `1.5R`, half the position is banked (locks `+0.75R` that cannot be lost).
- The runner half then moves to breakeven and trails `1R` behind the running peak, with no upper cap, so it can realize 4R/5R/8R or more.
- If the original stop is hit before 1.5R, the trade is a full `-1R`.
- `realized_r = 0.75 + 0.5 * runner_exit_r`. Results are judged by realized-R expectancy (average realized R per closed trade), not a binary TP/SL count.
- Active-test timeout is 48 M15 bars (12h) to keep tests intraday with no overnight holds; on timeout the runner is marked to market (its current R is booked, not discarded).

Old closed tests created before this change use the legacy 3R model and are reported in a separate "Legacy" block; their TP/SL counts are not comparable to the new realized-R stats.

## What This Agent Is

The agent scans OANDA candles and tries to identify Trading Geek-style entry zones from market structure.
It also has experimental paper routes that run side-by-side with the original rule engine: `DYNAMIC_SCORE` (weighted scoring of broader market conditions) and `REGIME_RANGE` (regime-gated range fades that only fire when the market is genuinely ranging).

It is meant to answer:

> Is there a technically valid buy or sell entry zone on this chart?

It is not meant to:

- Place real trades.
- Open broker positions.
- Manage account balance.
- Promise a fixed win rate.
- Trade Hyperliquid or random crypto systems.
- Use RSI/MACD/random indicator signals as standalone trade rules.

## Strategy Model

The strategy is based on market structure, not indicators.

Core ingredients:

- Higher-timeframe direction or active range.
- Premium/discount location.
- Liquidity sweep.
- Break of Structure / Market Shift.
- Supply or demand base that caused the break.
- Price returning to the zone.
- Enough room for a 3R paper target.
- Clean chart conditions.

The detailed strategy reference is in:

```text
docs/trading-geek-complete-strategy-guide.md
docs/strategy-knowledge-base.md
docs/trading-geek-mechanics.md
```

## Main Pipeline

The normal loop is run by:

```text
scripts/run_always_on.py
```

One cycle does this:

1. `scripts/fetch_fundamentals.py`
2. `scripts/live_monitor.py`
3. `scripts/deliver_alerts.py`
4. `scripts/forward_test_signals.py`
5. `scripts/export_orchestration_state.py`
6. `scripts/export_tradingview_pine.py`

The correct PowerShell command is:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\run_always_on.py" --once --use-gemma --gemma-limit 1
```

For scheduled monitoring, run that every 5 minutes. Do not run multiple copies at the same time.

## Data Flow

1. OANDA candles are fetched for the watchlist.
2. The market watcher builds state for each pair.
3. The rule engine detects swings, sweeps, BOS, zones, and setup state.
4. Optional local AI review uses Gemma to inspect the market state. DeepSeek is disabled to stop API spend.
5. Memory files store the latest rule/AI opinions.
6. Alerts are generated only for meaningful changes.
7. Forward testing opens paper-only tests for allowed routes.
8. Orchestration files are exported for OpenClaw or another operator.

Important source files:

```text
src/fx_annotation/market_watch.py
src/fx_annotation/setups.py
src/fx_annotation/structure.py
src/fx_annotation/forward_testing.py
src/fx_annotation/ai_strategy.py
src/fx_annotation/live_memory.py
scripts/run_always_on.py
scripts/live_monitor.py
scripts/forward_test_signals.py
scripts/export_orchestration_state.py
```

## Watchlist

The default watchlist includes:

```text
EUR_USD
GBP_USD
USD_JPY
USD_CAD
AUD_USD
NZD_USD
EUR_JPY
GBP_JPY
XAU_USD
BTC_USD
```

`BTC_USD` is an OANDA instrument here. It is not Hyperliquid crypto trading.

## Pair Value Labels

The agent labels pairs based on prior forward/backtest observations:

- `HIGH-VALUE PAIR`: stronger tested edge so far.
- `LOW-VALUE PAIR - CAUTION`: technically valid setups can exist, but tested edge was weak.
- `UNVALIDATED PAIR`: not enough evidence.

Pair value labels do not make a setup valid by themselves. They only warn the user about historical quality.

## Route Glossary

### Rule

Strict mechanical route. This is the main route worth further study.

It requires the rule engine to see a currently active entry candidate, valid sequence, enough quality, and enough 3R room.

Current result: best route so far, but sample size is still tiny.

### M15_SIMPLE

Mechanical simplified M15 route.

It uses M15 trend/range, premium/discount, liquidity sweep, BOS, compact base zone, price return or near-return, and clean/ugly filtering. It does not force the full H4/H1 sniper POI sequence.

Current result: not proven, but not clearly dead.

### DYNAMIC_SCORE

Experimental weighted scoring route. It does not replace the Trading Geek rule engine.

It scores the best current setup from three non-SMC modules: trend continuation, breakout continuation, and range reversal. Factors include EMA trend/slope, RSI momentum or exhaustion, candle body/wick quality, volatility, tick-volume expansion, recent swing structure, compression, and range-edge location.

Signals only become paper forward tests when the score is at least `5.8/10`. Results must be reported separately from `Rule`, `M15_SIMPLE`, AI routes, and opportunity routes.

Current result: brand new. No edge proven yet.

The route has a duplicate guard: it should not open another same-side `DYNAMIC_SCORE` test on the same instrument when an active near-identical entry already exists.

### REGIME_RANGE

Experimental regime-gated range route, added to lift quality trade frequency without loosening the strict rule funnel.

It first classifies the M15 regime using ATR-relative volatility (recent vs its own baseline), the EMA50/EMA200 stack, the price location relative to those EMAs, and directional efficiency (net move divided by total path). The regime is one of `trending_up`, `trending_down`, `ranging`, `high_volatility`, or `unclear`.

The route only opens a range-reversal fade (buy the lower edge, sell the upper edge) when the regime is `ranging`. It is fully suppressed in trends (so it never fades a strong trend) and during volatility spikes (where range behaviour is unreliable). This is the "trade both sides of a confirmed range" idea, isolated so its performance is measured on its own.

Session is a soft weight, not a hard gate: during the London/New York overlap (the most breakout-prone window) the score minimum is raised slightly; it is never blocked by session alone, because hard session gating would only reduce trade count.

Signals become paper forward tests when the regime range score is at least `4.8/10`. It shares the same-side duplicate guard and the cross-route opposite-side conflict rule. Results must be reported separately from every other route.

Current result: brand new. No edge proven yet.

### RULE_STALE_BOS

Rule route where BOS is older than the strict freshness window.

Current result: poor. Do not trust.

### `*_OPPORTUNITY`

Paper route for setups blocked by strict confirmation, often missing reaction candle or freshness.

Current result: poor. Do not trust.

### GEMMA_HIGH_VALUE_OPPORTUNITY / DEEPSEEK_HIGH_VALUE_OPPORTUNITY

Disabled routes. These were added to increase activity from high-confidence AI opinions, but forward testing showed no TP and mostly timeout/SL/breakeven outcomes.

Current result: dead. They have been removed from future candidate generation and purged from forward-test reporting.

### GEMMA_SMC_RAG_OPPORTUNITY / DEEPSEEK_SMC_RAG_OPPORTUNITY

Split AI route for the original SMC/RAG sniper workflow.

Current result: unproven to poor so far.

DeepSeek variants are historical only. Do not call DeepSeek for new analysis.

### GEMMA_M15_MECHANICAL_OPPORTUNITY / DEEPSEEK_M15_MECHANICAL_OPPORTUNITY

Split AI route for simplified M15 AI interpretation.

Current result: not enough evidence.

DeepSeek variants are historical only. Do not call DeepSeek for new analysis.

### AI_CONSENSUS_OVERRIDE

Disabled historical route where DeepSeek and Gemma agreed on side and zone. DeepSeek is no longer called, so this route should not produce new tests.

Current result: not enough useful evidence.

## Important Output Files

Read these after a run:

```text
outputs/live_monitor.md
outputs/live_memory.json
outputs/alerts.md
outputs/forward_tests.md
outputs/forward_tests.json
outputs/forward_test_diagnostics.json
outputs/orchestration/market_agent_state.md
outputs/orchestration/market_agent_state.json
outputs/ai_strategy_analysis.md
outputs/ai_memory.json
outputs/gemma_strategy_analysis.md
outputs/gemma_memory.json
```

OpenClaw memory exports:

```text
C:\Users\ADMIN\.openclaw\workspace\memory\forex-chart-agent-state.md
C:\Users\ADMIN\.openclaw\workspace\memory\forex-forward-tests.md
```

## How To Judge Results

The most important file is:

```text
outputs/forward_tests.md
```

Look at:

- Overall 3R TP/SL.
- Route-level performance.
- Open/pending tests.
- Candidate diagnostics.
- Closed tests.

Do not mix route stats together. A bad AI route should not be allowed to hide behind a better strict rule route.

Useful questions:

- Which route opened the test?
- Was it strict `Rule`, `M15_SIMPLE`, AI, stale BOS, or opportunity?
- Did it hit 3R, SL, timeout, or breakeven?
- Was it on a high-value pair or low-value caution pair?
- Was the setup actually confirmed or only forming/waiting?

## Current Route Verdict

Based on the latest forward test:

Keep testing:

- `Rule`
- `M15_SIMPLE`

Keep watching, but do not trust yet:

- `RULE_STALE_BOS`
- `DYNAMIC_SCORE`
- `REGIME_RANGE` (new regime-gated range route; fades range edges only in a ranging regime)
- `GEMMA_SMC_RAG_OPPORTUNITY` (re-enabled for observation under the new exit model; it had several near-winners previously killed by the breakeven/timeout rules)
- `GEMMA_M15_MECHANICAL_OPPORTUNITY`
- `AI_CONSENSUS_OVERRIDE`

Disabled/frozen from new tests:

- `RULE_STALE_BOS_OPPORTUNITY`
- `Rule_OPPORTUNITY`

Note: `GEMMA_SMC_RAG_OPPORTUNITY` is re-enabled specifically to re-judge it under the partial-then-trail exit model. Re-evaluate all routes on realized-R once enough new-model trades have closed.

## Backtesting

Rule-only backtesting is documented in:

```text
docs/rule-backtesting.md
```

Example:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\backtest_rules.py" --strategy-mode m15_simplified --instruments EUR_USD,GBP_USD,USD_JPY,USD_CAD,AUD_USD,NZD_USD,EUR_JPY,GBP_JPY,XAU_USD,BTC_USD --start 2025-01-01 --end 2025-04-01 --timeout-bars 192 --min-room-to-active-extreme-r 3.0 --premium-discount-edge 0.45 --require-entry-reaction-candle --require-market-regime --require-a-grade-confluence --a-grade-min-score 5 --json-output outputs/backtests/a_grade_best_filters_q1_2025.json --markdown-output outputs/backtests/a_grade_best_filters_q1_2025.md
```

Backtesting does not call DeepSeek or Gemma.

## OpenClaw Instructions

OpenClaw must read:

```text
docs/OPENCLAW_READ_THIS_FIRST.md
docs/openclaw-agent-cron-instructions.md
```

OpenClaw must not run any unrelated trading system.

Wrong system warning signs:

- `trading_system.py`
- `trading_system_v2.py`
- Quant Trading System V2/V3
- Hyperliquid
- BTC/ETH/SOL/XRP auto-trading
- randomized RSI/MACD
- Flask dashboard at `localhost:5000`

If any of those appear, stop and return to:

```text
C:\Users\ADMIN\Desktop\signal
```

## Known Problems

- Overall forward-test quality is currently poor.
- AI routes added more activity but did not add reliable quality.
- Many setups are only forming/waiting, but forward testing sometimes still opened paper tests for experimental routes.
- The strict rule route may be too rare, but loosening it damaged performance.
- The project has not proven a stable edge.

## Recommended Next Decision

If continuing this project:

1. Disable all weak experimental AI/opportunity paper routes.
2. Keep only `Rule` and `M15_SIMPLE` for forward testing.
3. Collect a larger sample.
4. Review every winner and loser visually.
5. Only re-enable AI routes if they first prove useful as commentary, not as entry triggers.

If pausing or dumping the project:

1. Preserve this handoff document.
2. Preserve `outputs/forward_tests.md` and `outputs/forward_tests.json`.
3. Preserve strategy docs.
4. Do not let another agent restart from the old optimistic assumptions.
