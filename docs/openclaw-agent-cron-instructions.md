# OpenClaw Forex Agent Cron Instructions

## Purpose

Run the forex chart annotation agent continuously like a cron job, keep its memory fresh, run live paper forward-testing, and export status files that OpenClaw can read.

This is the correct OpenClaw instruction file for normal monitoring.

Before doing anything, OpenClaw must read:

```text
C:\Users\ADMIN\Desktop\signal\docs\OPENCLAW_READ_THIS_FIRST.md
```

## Wrong-System Guard

If OpenClaw mentions any of the following, it is looking at the wrong project and must stop that path:

- `Quant Trading System V2`
- `Quant Trading System V3`
- Hyperliquid live prices
- `DataAgent`
- `FactorAgent`
- a Flask dashboard at `http://localhost:5000`
- momentum/mean_reversion/breakout/MACD/supertrend strategy merging

Those are not part of this forex chart annotation agent. Do not merge V2 data agents into V3, do not start a dashboard, and do not replace this project with hardcoded demo strategy code.

The user's intended workflow is simple: continue this OANDA-based forex forward-testing agent as usual by running `scripts\run_always_on.py` or `scripts\forward_test_signals.py` from `C:\Users\ADMIN\Desktop\signal`.

## Current Agent Version

This is the advanced rule-plus-review version of the forex entry agent. OpenClaw should not treat it like the older skeleton scanner.

Current capabilities:

- Shared A-grade confluence scoring is used by live scanning and backtesting.
- Entry logic requires the strategy sequence: direction, active range, premium/discount, liquidity sweep, BOS/market shift, pullback zone, target room, and reaction confirmation.
- Forward testing now also scans a separate `M15_SIMPLE` route for simplified M15 Trading Geek-style opportunities without forcing the full H4/H1 sniper POI sequence.
- Backtesting entry fills are aligned with live reaction confirmation when the reaction filter is enabled.
- Alerts include tested pair-value context so technically valid entries on weak pairs are not hidden, but are clearly marked.
- The agent still does not place real trades. It only marks entries, tracks paper tests, and reports context.

Pair value labels:

- `HIGH-VALUE PAIR`: `GBP_USD`, `GBP_JPY`, `USD_CAD`, `USD_JPY`, `XAU_USD`.
- `LOW-VALUE PAIR - CAUTION`: `AUD_USD`, `BTC_USD`, `EUR_USD`, `NZD_USD`.
- `UNVALIDATED PAIR`: any pair not yet validated.

OpenClaw should preserve and show these labels in user-facing summaries. A low-value label does not mean "invalid setup"; it means the entry can be technically valid, but historical Q1 testing showed weaker edge on that pair.

Do not use `docs\openclaw-tradingview-automation-instructions.md` unless the user explicitly asks for TradingView/Pine automation.

This project does not place real trades. It only:

- Fetches OANDA candle data.
- Runs the rule-based strategy route.
- Runs Gemma locally. DeepSeek is disabled to stop API spend.
- Updates alerts, memory, chart overlays, and Pine export.
- Runs paper-only forward tests using 3R targets.
- Records whether each paper test reaches TP, SL, timeout, or remains pending.

OpenClaw should not open TradingView, launch Chrome/CDP, edit Pine Script, or push anything into TradingView during the normal cron workflow.

The default watchlist includes forex majors/crosses, gold, and `BTC_USD`. `BTC_USD` is included so the agent can keep scanning and forward-testing on weekends when most forex pairs are closed.

## Project Root

```text
C:\Users\ADMIN\Desktop\signal
```

Always run commands from this directory.

## Recommended Cron Loop

The old cron job has been deleted. OpenClaw must create exactly one new scheduled job:

```text
Trading Agent - OANDA Forex Forward Testing
```

Schedule it every 5 minutes. Use this command as the main OpenClaw cron job:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\run_always_on.py" --once --use-gemma --gemma-limit 1
```

OpenClaw should run that command every 5 minutes. The rule engine, Gemma, alerts, and forward testing run each cycle. DeepSeek must not be called.

The command is unchanged for the advanced version. The improvements are inside the strategy modules and output fields, so OpenClaw mainly needs to read and report the new pair-value and confluence fields.

Do not create duplicate jobs. If a job with this name already exists, update it to this command instead of creating another one.

## Chat Trigger: Trading Agent

When the user says `Trading Agent`, OpenClaw should start or resume continuous chat updates for this OANDA forex agent.

Required behavior:

1. Confirm the scheduled job `Trading Agent - OANDA Forex Forward Testing` exists.
2. Confirm the job runs from `C:\Users\ADMIN\Desktop\signal`.
3. Confirm the job command is `scripts\run_always_on.py --once --use-gemma --gemma-limit 1`.
4. If outputs are stale, run one immediate cycle.
5. Read `outputs\orchestration\market_agent_state.md`, `outputs\forward_tests.md`, and alert markdown files.
6. Keep updating the user in chat only when meaningful changes occur.

Meaningful chat updates:

- New `entry_candidate_now`.
- Price close to a watched entry zone.
- Forward test opened or entered.
- New `M15_SIMPLE` forward test or blocker.
- 3R TP hit.
- SL hit.
- Timeout.
- API/OANDA/Gemma error.
- Pair value warning: `HIGH-VALUE PAIR`, `LOW-VALUE PAIR - CAUTION`, or `UNVALIDATED PAIR`.

Do not send chat updates about unchanged statuses. Do not mention crypto auto-trading, Hyperliquid, V2/V3, dashboards, opened positions, balance, or random RSI/MACD systems.

Recommended interval:

```text
300 seconds / 5 minutes
```

If OpenClaw itself manages the schedule, use `--once` and let OpenClaw repeat the command every 5 minutes.

If OpenClaw wants Python to manage the loop, run without `--once`:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\run_always_on.py" --use-gemma --gemma-limit 1 --interval-seconds 300
```

Do not run both scheduling modes at the same time. Choose one:

- OpenClaw scheduled cron: use `--once`.
- Python managed loop: omit `--once`.

## Lower Cost Mode

If API usage needs to be reduced, run the rule route plus local Gemma only:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\run_always_on.py" --once --use-gemma --gemma-limit 1 --ai-limit 0
```

This still runs the rule engine, local Gemma review, alerts, forward testing, orchestration export, and Pine export.

## What The Main Loop Does

`scripts\run_always_on.py` runs these steps in order:

1. `scripts\fetch_fundamentals.py`
2. `scripts\live_monitor.py`
3. `scripts\deliver_alerts.py`
4. `scripts\forward_test_signals.py`
5. `scripts\export_orchestration_state.py`
6. `scripts\export_tradingview_pine.py`

The forward-testing script is included by default.

Only disable it if explicitly needed:

```powershell
python "scripts\run_always_on.py" --once --use-gemma --no-forward-test
```

## Forward Testing

Forward testing is not backtesting.

The script:

- Reads fresh live signal memory from the rule and Gemma routes. DeepSeek memory is historical only.
- Scans direct M15 simplified setups as `M15_SIMPLE` without requiring H4/H1 POI sequencing.
- Reads split Gemma route memory for independent `smc_rag` and `m15_mechanical` AI verdicts.
- Opens a paper test only when a signal is fresh.
- Tracks strict `Rule`, stale-rule `RULE_STALE_BOS`, simplified `M15_SIMPLE`, Gemma split AI opportunity routes, and `*_OPPORTUNITY` routes separately.
- `AI_CONSENSUS_OVERRIDE` is disabled while DeepSeek is disabled.
- Opens `*_OPPORTUNITY` paper tests when price is at a valid zone but the strict reaction candle gate blocks the normal route.
- Blocks non-`Rule` routes from opening a new opposite-side paper test if there is already an active forward test on the same pair.
- Reports `GEMMA_SMC_RAG_OPPORTUNITY` and `GEMMA_M15_MECHANICAL_OPPORTUNITY` separately so route performance can be judged independently. DeepSeek routes are historical only.
- Uses the entry zone from the agent.
- Places a test-only SL beyond the sweep/zone boundary with a volatility buffer.
- Tracks 3R outcomes as the primary forward-test score.
- Reports gold (`XAU_USD`) separately from non-gold so gold volatility does not hide the rest of the system's performance.
- Keeps checking future M15 candles until TP, SL, timeout, or pending.

Manual one-off forward test command:

```powershell
cd "C:\Users\ADMIN\Desktop\signal"
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"
python "scripts\forward_test_signals.py" --rr 3 --timeout-bars 48 --max-signal-age-minutes 30
```

Default behavior:

- `--rr 3`: track 3R as the minimum target.
- `--timeout-bars 48`: timeout after 48 M15 candles / about 12 hours, because this is a day-trading forward test, not a swing-trade test.
- `--max-signal-age-minutes 30`: only open new tests from signals updated within the last 30 minutes.

## Files OpenClaw Should Read

Main orchestration state:

```text
C:\Users\ADMIN\Desktop\signal\outputs\orchestration\market_agent_state.md
C:\Users\ADMIN\Desktop\signal\outputs\orchestration\market_agent_state.json
```

OpenClaw memory export:

```text
C:\Users\ADMIN\.openclaw\workspace\memory\forex-chart-agent-state.md
```

Forward test results:

```text
C:\Users\ADMIN\Desktop\signal\outputs\forward_tests.md
C:\Users\ADMIN\Desktop\signal\outputs\forward_tests.json
```

OpenClaw forward-test memory export:

```text
C:\Users\ADMIN\.openclaw\workspace\memory\forex-forward-tests.md
```

Alerts:

```text
C:\Users\ADMIN\Desktop\signal\outputs\alerts.md
C:\Users\ADMIN\Desktop\signal\outputs\ai_alerts.md
C:\Users\ADMIN\Desktop\signal\outputs\gemma_alerts.md
```

Important JSON fields OpenClaw should read when present:

```text
pair_value_tier
pair_value_label
pair_value_note
a_grade_score
a_grade_passed
confluence
available_r
trade_target_price
trade_target_reason
route
status
entry_price
stop_loss
targets
```

Important markdown sections OpenClaw should read:

```text
Decision Dashboard
Forward Testing
Rule Route
DeepSeek AI Route
Gemma AI Route
```

The `Decision Dashboard` explains why each pair is or is not actionable. Use it when the user asks why there are no trades.

Forward-test route meanings:

```text
Rule = strict mechanical route.
RULE_STALE_BOS = rule entry where price is active but BOS is older than strict freshness.
AI_CONSENSUS_OVERRIDE = disabled historical DeepSeek/Gemma consensus route.
*_OPPORTUNITY = paper opportunity when price is at zone but reaction candle has not confirmed.
```

Report these routes separately. Never call `*_OPPORTUNITY` a strict Rule win/loss.

Alert delivery state:

```text
C:\Users\ADMIN\Desktop\signal\outputs\delivered_alerts.json
```

Do not resend historical alerts already recorded in that file.

When reporting an entry, include the pair value label. Example:

```text
EUR_USD SELL entry spotted [LOW-VALUE PAIR - CAUTION] - technically valid setup, but Q1 tested edge was weak on this pair.
```

For high-value pairs:

```text
USD_CAD SELL entry spotted [HIGH-VALUE PAIR] - Q1 validation showed stronger tested edge for this strategy model.
```

Live chart viewer assets:

```text
C:\Users\ADMIN\Desktop\signal\web\live_chart
```

Generated Pine Script:

```text
C:\Users\ADMIN\Desktop\signal\outputs\tradingview\market_agent_zones.pine
```

This file is generated passively. OpenClaw should ignore it during the normal cron workflow unless the user explicitly asks for TradingView/Pine automation.

## How The Agent Works

The rule route follows the strategy mechanically:

1. Fetch OANDA candles.
2. Build HTF narrative from H4.
3. Use H1 only when H4 is noisy or needs refinement.
4. Determine bullish, bearish, or neutral direction.
5. If bullish, focus on demand/buy opportunities only.
6. If bearish, focus on supply/sell opportunities only.
7. Mark the highest swing high and lowest swing low.
8. Start the active story from whichever anchor came first from left to right.
9. Ignore old pre-anchor data for current zone ladder decisions.
10. Build a ladder of valid demand/supply zones inside the active range.
11. Drop failed zones when price decisively closes through them.
12. Wait for price to test a valid HTF zone.
13. On M15, wait for liquidity sweep.
14. Then wait for Market Shift / BOS.
15. Then mark the LTF entry zone that caused the shift.
16. Grade shared A-grade confluence and store score/reasons.
17. Attach pair-value context to reports, alerts, memory, and AI/Gemma prompt context.

The AI routes are independent:

- DeepSeek is disabled and must not be called.
- Gemma runs locally through Ollama as the frequent low-cost reviewer.
- They produce their own side, status, entry zone, confidence, reasoning, and alert data.
- They must obey the same directional gate: bullish means BUY/demand only, bearish means SELL/supply only, neutral means no actionable entry.
- Gemma should not block the rule route. It is a separate route for cheap frequent validation and monitoring notes.

Local Gemma defaults:

```text
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:latest
```

If a different Ollama model name is installed, set it in:

```text
C:\Users\ADMIN\Desktop\signal\.env.ollama
```
- Their output is stored separately from the rule route.

## Important Status Meaning

OpenClaw should treat these as useful:

- `entry_candidate_now`: agent sees an actionable entry zone now.
- `wait_for_pullback`: setup exists but price has not returned to entry zone.
- `potential_future_setup`: future monitoring opportunity.
- `active`: forward test has entered and is monitoring TP/SL.
- `waiting_entry`: forward test is waiting for price to touch entry.
- `closed`: forward test has finished.

Pair-value meaning:

- `high_value`: user can treat the setup as coming from the stronger tested watchlist, while still applying discretion.
- `low_value`: user should be warned that the setup is technically valid but historically weaker on that pair.
- `unvalidated`: user should treat the setup conservatively because the pair has not yet built enough evidence.

OpenClaw should not treat these as trade instructions:

- `no_clear_state`
- `expired`
- `low_quality`
- `NO_SETUP`
- `NEUTRAL`
- `WAIT`

## Safety Rules

OpenClaw must not:

- Place real trades.
- Click broker buy/sell buttons.
- Change OANDA account settings.
- Open TradingView unless explicitly asked.
- Launch or kill Chrome/CDP processes unless explicitly asked.
- Edit or push Pine Script unless explicitly asked.
- Edit `.env`, `.env.deepseek`, `.env.ollama`, or `.env.openai`.
- Delete memory files unless the user explicitly asks.
- Run multiple agent loops at the same time.

OpenClaw may:

- Run the scheduled command.
- Read output and memory files.
- Report current state to the user.
- Restart the loop if it crashed.
- Run one manual cycle if the user asks.
- Open the local chart viewer or TradingView automation if requested.

## Failure Handling

If a command fails:

1. Capture the terminal output.
2. Do not retry more than once immediately.
3. Check whether OANDA/API/auth/network errors occurred.
4. Report the failure and the most relevant output.
5. Keep existing memory files untouched.

If an API key error appears, do not edit secrets. Ask the user to check the relevant `.env` file.

If the loop is already running, do not start another duplicate loop. Either use the running loop or stop it only if the user approves.

## Recommended OpenClaw Checklist

Every 5 minutes:

1. Run one agent cycle with `run_always_on.py --once --use-gemma --gemma-limit 1`.
2. Confirm `outputs\orchestration\market_agent_state.md` was updated.
3. Confirm `outputs\forward_tests.md` was updated.
4. Read the OpenClaw memory exports.
5. Report only important changes:
   - New entry now.
   - New future setup.
   - Forward test activated.
   - TP hit.
   - SL hit.
   - Timeout.
   - API/agent error.
6. For entries and near-zone alerts, always include `pair_value_label` and, when short enough, `pair_value_note`.

Do not spam unchanged statuses.
