# OpenClaw: Read This First

You are working on the wrong system if you mention any of these:

- `trading_system.py`
- `trading_system_v2.py`
- `Quant Trading System V2`
- `Quant Trading System V3`
- Hyperliquid
- BTC/ETH/SOL/XRP auto-trading
- randomized RSI/MACD
- Flask dashboard at `http://localhost:5000`
- opening positions
- account balance
- 5% SL / 10% TP auto-managed

Stop immediately if you are in that system. That is not the user's requested project.

## Correct Project

The correct project is:

```text
C:\Users\ADMIN\Desktop\signal
```

The correct agent is the OANDA forex chart annotation and forward-testing agent.

It does not place trades. It does not open positions. It does not manage a live or paper account balance. It does not trade BTC/ETH/SOL/XRP through Hyperliquid.

Its job is to:

- Fetch OANDA candle data.
- Scan forex pairs, gold, and optional `BTC_USD` as an OANDA instrument.
- Detect technical entries using the Trading Geek-style rule engine.
- Label entries with pair-value context.
- Produce alerts and reports.
- Run paper-only forward tests at 3R.
- Track whether forward-test setups hit TP, SL, timeout, or remain pending.
- Do not call DeepSeek. DeepSeek is disabled to stop API spend.
- Track `M15_SIMPLE` separately for Trading Geek-style M15 setups that do not require the full H4/H1 sniper POI sequence.
- Track `DYNAMIC_SCORE` separately for the experimental weighted scoring route.
- Track `REGIME_RANGE` separately for the experimental regime-gated range-reversal route.
- All new forward tests use the partial-then-trail exit model: bank 50% at 1.5R, runner trails 1R behind peak (uncapped), breakeven after the partial. Results are judged by realized-R expectancy, not TP/SL counts. Active-test timeout is 48 M15 bars (12h) to stay intraday with no overnight holds; the runner is marked to market at timeout.

## Correct Main Command

Run this from PowerShell:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\run_always_on.py" --once --use-gemma --gemma-limit 1
```

If OpenClaw is scheduling the agent, run that command every 5 minutes.

## Cron Job Was Deleted: Create A New One

The old cron job has been deleted. OpenClaw must create exactly one new scheduled job for this project only.

Job name:

```text
Trading Agent - OANDA Forex Forward Testing
```

Schedule:

```text
Every 5 minutes
```

Working directory:

```text
C:\Users\ADMIN\Desktop\signal
```

Command:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\run_always_on.py" --once --use-gemma --gemma-limit 1
```

Rules:

- Create only one scheduled job.
- Do not create or run any crypto/Hyperliquid/Quant V2/V3 job.
- Do not create a dashboard job.
- Do not run multiple copies of this forex agent at the same time.
- If a matching job already exists, reuse/update it instead of creating duplicates.
- After each run, read the output files below and report only meaningful changes.

## Chat Trigger

When the user says:

```text
Trading Agent
```

OpenClaw should interpret that as:

```text
Start or resume chat updates for the OANDA forex chart annotation agent.
```

It must then:

1. Confirm the scheduled job exists and is using the correct command above.
2. Run one immediate cycle if the latest output is stale.
3. Read the latest forex agent outputs.
4. Continue updating the user in chat with important changes only.

Important changes include:

- New `entry_candidate_now`.
- Price close to a watched entry zone.
- Forward test opened.
- Forward test entered.
- 3R TP hit.
- SL hit.
- Timeout.
- API/agent failure.
- Pair-value warning on any setup.
- New `M15_SIMPLE` forward test or `M15_SIMPLE` blocker.
- New `DYNAMIC_SCORE` forward test or score-route blocker.
- New `REGIME_RANGE` forward test or regime-route blocker.

Do not report random crypto signals, account balances, opened positions, dashboard status, or V2/V3 strategy names.

## Correct Forward-Test-Only Command

If the user specifically says to continue forward testing only, run:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\forward_test_signals.py" --rr 3 --timeout-bars 48 --max-signal-age-minutes 30
```

The forward-test script has multiple separate paper-test routes:

- `Rule`: strict mechanical rule-engine entries.
- `RULE_STALE_BOS`: rule entry where price is active now but BOS age is older than the strict 6-hour rule; tracked separately.
- `AI_CONSENSUS_OVERRIDE`: disabled because DeepSeek is disabled. Treat old records as historical only.
- `M15_SIMPLE`: separate simplified M15 route using M15 trend, premium/discount location, liquidity sweep, BOS, the base zone that caused BOS, price return/near-return, 3R paper target, and a clean/ugly chart filter. It does not require the full H4/H1 sniper POI sequence.
- `DYNAMIC_SCORE`: experimental weighted scoring route. It scores trend continuation, breakout continuation, and range reversal modules using factors such as EMA slope, RSI, candle quality, volatility, tick volume, recent structure, compression, and range location. It is paper-only and unproven.
- `REGIME_RANGE`: experimental regime-gated range route. It first classifies the M15 regime (trending/ranging/high-volatility/unclear from ATR-vs-baseline volatility, EMA50/EMA200 stack, and directional efficiency) and only opens a range-reversal fade when the regime is `ranging`. It is suppressed in trends and volatility spikes, trades both edges of a confirmed range, applies a soft session weight (slightly stricter score minimum during the London/NY overlap, never a hard block), and is paper-only and unproven.
- `GEMMA_SMC_RAG_OPPORTUNITY`: re-enabled for observation under the new partial-then-trail exit model (it had near-winners previously killed by the breakeven/timeout rules). `DEEPSEEK_SMC_RAG_OPPORTUNITY` stays historical only because DeepSeek is disabled.
- `GEMMA_M15_MECHANICAL_OPPORTUNITY` and `DEEPSEEK_M15_MECHANICAL_OPPORTUNITY`: split AI route for the simplified M15 mechanical workflow.
- `*_OPPORTUNITY`: historical paper route when price is at a valid zone but the strict reaction candle gate has not confirmed yet. New `Rule_OPPORTUNITY`, `RULE_STALE_BOS_OPPORTUNITY`, and AI-consensus opportunity tests are disabled after poor results.
- `GEMMA_HIGH_VALUE_OPPORTUNITY`: disabled and purged from forward-test reporting after poor results.
- `DEEPSEEK_HIGH_VALUE_OPPORTUNITY`: disabled and purged from forward-test reporting.

Consensus timing is historical only while DeepSeek is disabled:

- DeepSeek and Gemma do not need to run in the exact same minute.
- Both AI opinions must be within the last 240 minutes.
- The time gap between the two AI opinions must be 240 minutes or less.
- The zones can exactly overlap or nearly miss within 3 pips; near matches are still valid consensus candidates.
- The newest AI timestamp is used for the normal fresh-signal check.
- Current M15 price must still be at the consensus zone and show a reaction candle before a forward test opens.

Do not mix these stats together when summarizing performance. Report each route separately when present.

M15 simplified route rules:

- It is a paper-test route named `M15_SIMPLE`.
- It scans the M15 chart directly and does not force H4/H1 POI sequencing.
- It still requires a real M15 directional bias, liquidity sweep, BOS, compact demand/supply base, premium/discount location, and reasonable 3R paper target.
- It rejects ugly/compressed setups where the base is too wide or the sweep-to-BOS displacement is too weak.
- It rejects the setup if there is already an active opposite-side forward test on the same instrument.
- Report `M15_SIMPLE` separately from strict `Rule`, stale-BOS, AI consensus, and AI opportunity routes.

Experimental route conflict rule:

- Non-`Rule` routes do not open a new opposite-side paper test if the same pair already has an active forward test.
- This applies to `M15_SIMPLE`, `DYNAMIC_SCORE`, `REGIME_RANGE`, stale-BOS, AI consensus, and AI opportunity routes.
- `DYNAMIC_SCORE` and `REGIME_RANGE` also have a same-side duplicate guard, so they should not stack near-identical active tests on the same pair.
- The strict `Rule` route remains the anchor and can still open independently.

Split AI route rules:

- Gemma can produce independent route verdicts.
- DeepSeek is disabled and must not be called for new route verdicts.
- `smc_rag` is the original SMC/RAG sniper route: HTF narrative, H4/H1 POI ladder, sweep, BOS, reaction confirmation, and target room.
- `m15_mechanical` is the simplified M15 route: M15 trend/range, premium/discount, liquidity sweep, BOS, compact base zone, price return/near-return, and clean/ugly chart filter.
- `GEMMA_SMC_RAG_OPPORTUNITY` is re-enabled for observation under the new exit model; re-judge it on realized-R, not the old 3R TP/SL count.
- OpenClaw must report route families separately. Do not combine `SMC_RAG` performance with `M15_MECHANICAL` performance.
- The goal is to identify whether poor results come from the original SMC/RAG interpretation, the M15 simplified interpretation, a specific AI model, or the mechanical algo.

Current open/pending format to report (new partial-then-trail model):

```text
<PAIR> <ROUTE> <SIDE> <status> | entry <entry> SL <stop> | <partial state> | realized <R> (<outcome>) | <pair value>
```

Example:

```text
USD_JPY DYNAMIC_SCORE SELL active | entry 159.22 SL 159.34 | partial@1.5R booked | realized +0.75R (runner_win) | HIGH-VALUE PAIR
```

`outcome` is one of `loss`, `partial_only`, `runner_win`, or `timeout`. Judge routes by realized-R expectancy, not TP/SL counts. Legacy pre-redesign tests still show the old `3R: <status>` format in the Legacy block.

OpenClaw must report historical `*_OPPORTUNITY` records as paper-test opportunities, not strict confirmed Rule trades and not real broker trades. Do not request new `Rule_OPPORTUNITY` or `RULE_STALE_BOS_OPPORTUNITY` tests unless the route is explicitly re-enabled.

For split AI and legacy AI opportunities:

- Report them as paper opportunities.
- Include the source route, side, entry, SL, 3R status, and pair value.
- Do not mix them into strict `Rule` stats.
- Treat active/pending split AI opportunities as meaningful route-comparison activity for the day.

## Decision Dashboard

OpenClaw must read the `Decision Dashboard` section in:

```text
C:\Users\ADMIN\Desktop\signal\outputs\orchestration\market_agent_state.md
```

This dashboard explains why each pair is or is not actionable. Possible statuses include:

- `ENTRY_NOW`
- `NEAR_ENTRY`
- `WAITING_FOR_SWEEP`
- `WAITING_FOR_BOS`
- `WAITING_FOR_PULLBACK`
- `BLOCKED_BY_HTF_BIAS`
- `BLOCKED_BY_POI_SEQUENCE`
- `LOW_QUALITY`
- `EXPIRED`
- `WATCHING`

When the user asks why there are no trades, OpenClaw should summarize this dashboard instead of saying the agent saw nothing.

## Alert Delivery State

Alert delivery is stateful now.

Delivered alert IDs are stored here:

```text
C:\Users\ADMIN\Desktop\signal\outputs\delivered_alerts.json
```

OpenClaw should not repeatedly deliver historical alerts every cycle. `scripts\deliver_alerts.py` now sends only new alert IDs that are not already in `delivered_alerts.json`.

If the first run after this change sends many old alerts, treat that as a one-time catch-up. Future runs should only send new alerts.

## What To Read After Running

Read and summarize these files:

```text
C:\Users\ADMIN\Desktop\signal\outputs\orchestration\market_agent_state.md
C:\Users\ADMIN\Desktop\signal\outputs\forward_tests.md
C:\Users\ADMIN\Desktop\signal\outputs\alerts.md
C:\Users\ADMIN\Desktop\signal\outputs\ai_alerts.md
C:\Users\ADMIN\Desktop\signal\outputs\gemma_alerts.md
```

OpenClaw memory export:

```text
C:\Users\ADMIN\.openclaw\workspace\memory\forex-chart-agent-state.md
C:\Users\ADMIN\.openclaw\workspace\memory\forex-forward-tests.md
```

## Required Alert Context

When reporting a setup, include the pair-value label:

- `HIGH-VALUE PAIR`
- `LOW-VALUE PAIR - CAUTION`
- `UNVALIDATED PAIR`

Example:

```text
USD_CAD BUY entry spotted [HIGH-VALUE PAIR]. Rule route sees entry zone active now. Forward test opened at 3R.
```

Example caution:

```text
EUR_USD SELL entry spotted [LOW-VALUE PAIR - CAUTION]. Technical setup is valid, but Q1 tested edge was weak on this pair.
```

## Strict Prohibitions

Do not:

- Run `trading_system.py`.
- Run `trading_system_v2.py`.
- Run any Hyperliquid bot.
- Open BTC/ETH/SOL/XRP positions.
- Mention paper account balance as if this agent manages capital.
- Merge V2 data code into V3.
- Start or depend on `http://localhost:5000`.
- Replace this project with a dashboard auto-trader.
- Use randomized RSI/MACD signals.
- Place real or simulated broker orders.

If you accidentally run the wrong system, stop, report the mistake, and return to:

```text
C:\Users\ADMIN\Desktop\signal
```

Then run the correct command from this document.

## One-Sentence User Intent

The user wants the OANDA forex chart annotation agent to continue scanning and forward-testing Trading Geek-style forex entries, not a crypto auto-trading dashboard.
