# Forex Chart Annotation Agent

This document is the working reference for the new forex project.

The goal is **not** to build another prediction model or trading bot. The goal is to build a chart annotation agent that studies a forex chart and marks possible buy/sell entry areas based on clear trading rules.

The user remains responsible for stop loss, take profit, position size, trade execution, and final judgment.

## Core Principle

The old project tried to answer too many questions at once:

- Will price go up or down?
- Where should we enter?
- Where should stop loss go?
- Where should take profit go?
- Should the bot execute the trade?

This project starts with only one question:

> Where on this chart is a reasonable buy or sell entry area according to our rules?

The current strategy checklist is documented here:

- `docs/current-trading-strategy-checklist.md`

## What The Agent Should Do

The agent should:

- Read forex chart structure.
- Read the user's pasted fundamental analysis.
- Use strategy knowledge from the project knowledge base.
- Combine AI reasoning with OANDA-derived chart evidence.
- Detect obvious market context.
- Mark possible buy or sell entry zones.
- Explain why an entry zone was marked.
- Avoid giving full trade management instructions.
- Avoid pretending to predict the market with certainty.

The agent should not:

- Place trades automatically.
- Promise a fixed win rate.
- Generate TP/SL in the first version.
- Train a black-box prediction model as the main strategy.
- Take a trade just because one indicator says buy or sell.
- Behave like a rigid chatbot that only follows a single hardcoded branch.

## Important Meaning Of Candle Data

If we use candle data such as open, high, low, close, and volume, it is for **chart geometry**, not for training another prediction model.

The candle data helps the system calculate:

- Swing highs and swing lows.
- Support and resistance.
- Break of structure.
- Liquidity sweeps.
- Pullbacks.
- Demand and supply zones.
- Fair value gaps.
- Moving averages.

This is different from the failed model approach, where historical data was used to train a model to predict buy/sell/hold outcomes.

## Planned Data Source

The planned candle-data source is the **OANDA API**.

OANDA will be used to fetch forex candle data that the rule engine can analyze and annotate. The API should provide the real chart values needed to calculate structure, sweeps, zones, and pullbacks.

OANDA data should be used for:

- Fetching forex candles.
- Matching visible chart movement as closely as possible.
- Calculating higher-timeframe and lower-timeframe structure.
- Producing chart annotations from actual price levels.

OANDA data should not be used for:

- Training another prediction model as the first approach.
- Building a fully automatic trading bot.
- Generating guaranteed trade outcomes.

## Agent Platform Paths

Potential orchestration platforms found locally:

- OpenClaw: `C:\Users\ADMIN\.openclaw`
- OpenWork: `C:\Users\ADMIN\AppData\Local\Programs\@openworkdesktop`

The autonomous core should stay platform-neutral first, then connect to OpenClaw/OpenWork once the scanner, memory, AI review, and alerts are stable.

## OpenAI Configuration

OpenAI GPT-5.5 will power the live AI review layer.

Drop the OpenAI API key here:

- `.env.openai`

Use this format:

```text
OPENAI_API_KEY=your_real_openai_api_key_here
OPENAI_MODEL=gpt-5.5
```

The file is ignored by git through `.gitignore`.

## AI And RAG Direction

The chosen direction is an autonomous hybrid system:

- OANDA provides candle figures and price evidence.
- The market watcher scans multiple instruments without requiring manual chart selection.
- The rule engine extracts chart evidence such as swings, sweeps, BOS, setup state, and entry zones.
- The strategy knowledge base stores the user's trading concepts and examples.
- The AI reviews the knowledge base, fundamentals, market state, and chart evidence before giving an entry-zone opinion.
- The agent monitors setups that are not ready yet and revisits them when conditions change.

The rule engine should not be the final decision maker. It should collect evidence. The AI layer should reason over that evidence using the strategy knowledge base, market state, and fundamentals.

## Operating Modes

The system must separate live monitoring from historical validation.

### Live Mode

Live mode uses the latest completed OANDA candles to understand current market state.

Live mode should:

- Scan the watchlist.
- Identify immediate setups only when price is at the entry zone now.
- Store forming or stale setups in memory.
- Decide when to revisit each setup.
- Alert later when price or structure changes.

Live mode should not:

- Use future candles.
- Claim an old setup is actionable now just because it touched the zone in the past.
- Backtest TP/SL as if it were live behavior.

### Backtest/Validation Mode

Backtest/validation mode uses historical candles after an old setup to check whether it played out.

Validation mode should:

- Use test-only SL/TP.
- Record whether TP, SL, timeout, or no trigger happened.
- Create outcome records for improving the detector.

Validation mode should not:

- Be confused with live monitoring.
- Produce user trade instructions.

Initial knowledge base:

- `docs/strategy-knowledge-base.md`
- `docs/autonomous-market-agent.md`

This is the start of the RAG-style memory for the strategy. It should be expanded with corrected examples, screenshots, and user feedback.

## Rule Checklist

Use this checklist as the source of truth for what the agent should learn to mark.

| Rule | Purpose | Status |
| --- | --- | --- |
| Higher timeframe bias | Decide whether the market context favors buys, sells, or no trade. | Partial: simple swing-based H4 bias |
| Lower timeframe confirmation | Confirm entry timing on a smaller timeframe. | Not built |
| Market structure | Detect highs, lows, trend direction, and ranging conditions. | Partial: volatility-filtered swing highs/lows |
| Break of structure | Confirm that price has broken an important structure level. | Partial: BOS check after sweep |
| Change of character | Detect possible shift from bullish to bearish or bearish to bullish. | Not built |
| Liquidity sweep | Detect when price grabs previous highs/lows and rejects. | Partial: rejection-filtered sweep detector |
| Support and resistance | Mark major horizontal levels where price reacts. | Not built |
| Demand zone | Mark possible buy zones from strong bullish displacement. | Not built |
| Supply zone | Mark possible sell zones from strong bearish displacement. | Not built |
| Order block | Mark the last opposing candle before strong displacement. | Not built |
| Fair value gap | Mark imbalance areas price may return to. | Not built |
| Pullback/retest entry | Mark entry areas after price returns to a valid zone. | Partial: opposing-candle or retracement zone |
| Session filter | Prefer London, New York, and overlap sessions. | Not built |
| No-trade filter | Avoid unclear, choppy, or middle-of-range areas. | Not built |
| Entry annotation | Draw buy/sell marker on the chart. | Partial: SVG chart output |
| Explanation output | Explain the reason for each marked entry. | Partial: Markdown report output |
| Confidence/ranking | Rank setup quality without claiming certainty. | Not built |
| Screenshot/chart overlay | Render visible markings on a chart image. | Partial: generated SVG chart |

## First Strategy To Build

The first strategy should be simple and strict:

> Higher timeframe bias + liquidity sweep + break of structure + pullback entry annotation.

### Buy Setup

Conditions:

1. Higher timeframe is bullish or price is near a higher timeframe demand/support zone.
2. Price sweeps a previous low.
3. Price rejects back above that low.
4. Price breaks a recent lower-timeframe swing high.
5. Price pulls back into a demand zone, order block, fair value gap, or retest area.
6. Agent marks a possible `BUY ENTRY` zone.

The agent should explain:

- Which low was swept.
- Which structure level was broken.
- Which zone is being used for the entry.
- Why the setup is valid or weak.

### Sell Setup

Conditions:

1. Higher timeframe is bearish or price is near a higher timeframe supply/resistance zone.
2. Price sweeps a previous high.
3. Price rejects back below that high.
4. Price breaks a recent lower-timeframe swing low.
5. Price pulls back into a supply zone, order block, fair value gap, or retest area.
6. Agent marks a possible `SELL ENTRY` zone.

The agent should explain:

- Which high was swept.
- Which structure level was broken.
- Which zone is being used for the entry.
- Why the setup is valid or weak.

## Suggested MVP Phases

### Phase 1: Rule Definition

Status: Not built

Deliverables:

- Convert notebook rules into clean written rules.
- Define exact conditions for buy and sell entries.
- Decide initial forex pairs and timeframes.
- Define what counts as a valid setup and invalid setup.

### Phase 2: Chart Data Input

Status: Not built

Deliverables:

- Load candle data from OANDA for one pair and timeframe.
- Support at least one higher timeframe and one lower timeframe.
- Avoid training a prediction model.
- Use candle data only for structure and annotation calculations.

### Phase 3: Structure Detection

Status: Not built

Deliverables:

- Detect swing highs and swing lows.
- Detect trend direction.
- Detect ranging/no-trade conditions.
- Mark support and resistance levels.

### Phase 4: Setup Detection

Status: Not built

Deliverables:

- Detect liquidity sweeps.
- Detect break of structure.
- Detect demand/supply zones.
- Detect pullback/retest areas.
- Generate possible buy/sell entry annotations.

### Phase 5: Visual Annotation

Status: Not built

Deliverables:

- Draw zones, arrows, and labels on chart images.
- Make annotations readable and not cluttered.
- Export annotated chart screenshots.

### Phase 6: Review And Scoring

Status: Not built

Deliverables:

- Add setup quality scoring.
- Explain why a setup is strong, weak, or invalid.
- Track missed setups and bad annotations.
- Improve the rules based on examples.

### Phase 7: Optional Agent Interface

Status: Not built

Deliverables:

- Let the user upload chart screenshots or choose a symbol/timeframe.
- Return annotated chart plus explanation.
- Optionally integrate with OpenClaw/OpenWork as the orchestration layer.

## Example Output Format

The agent should produce outputs like this:

```text
Pair: EURUSD
Timeframes: 4H bias, 15m entry
Bias: Bullish

Marked Entry:
- Type: BUY ENTRY
- Reason: Price swept a previous low, rejected, broke minor structure, and pulled back into a demand zone.
- Zone: 1.08320 - 1.08380
- Status: Possible setup, user must confirm before trading.

Warnings:
- Do not use this as automatic trade execution.
- User must decide stop loss, take profit, and risk.
```

## Build Status

| Area | Built? | Notes |
| --- | --- | --- |
| Project reference document | Yes | This document. |
| Trading rule checklist | Yes | Initial version created from notebook notes. |
| Failed model reused | No | The old ML trading bot path should not be reused as the core strategy. |
| OANDA candle data source | Yes | Connected with local `.env` credentials. |
| Candle data loader | Partial | Fetches OANDA candles for higher and lower timeframes. |
| Screenshot input | No | Optional, likely useful for visual chart workflows. |
| Rule engine | Partial | Refined H4 bias, M15 swing, sweep, BOS, entry-zone, and multi-candidate checks exist. |
| Chart renderer | Partial | Generates an SVG chart with candles, swings, sweep, BOS, and entry zone. |
| Strategy knowledge base | Partial | First RAG-style strategy document created from notebook themes. |
| AI review layer | Partial | Builds AI review context from strategy knowledge, fundamentals, and chart evidence. |
| Autonomous market watcher | Partial | Scans multiple OANDA instruments and classifies current setup state. |
| Live setup memory | Partial | Stores current state and next revisit time for each instrument. |
| Smart revisit scheduling | Partial | Uses setup state, distance to zone, volatility, and session context. |
| Alert records | Partial | Writes alert records when a setup is active or price is close to a watched zone. |
| OpenAI live review | Partial | Code is wired behind `--use-ai`; waiting for API key. |
| Manual local GUI | Discarded | Removed because the agent should not require manual form-style workflow. |
| TradingView overlay | No | Planned later after local GUI and AI review are useful. |
| Agent wrapper | No | Can be added through OpenClaw/OpenWork after the autonomous core works. |
| Auto trading | No | Out of scope for now. |

## Current Implementation

The first proof of concept is now available:

- `src/fx_annotation/config.py` loads local OANDA settings from `.env`.
- `src/fx_annotation/oanda_client.py` fetches candles from OANDA.
- `src/fx_annotation/bias.py` detects a first higher-timeframe bias from swing structure.
- `src/fx_annotation/structure.py` detects early swing highs/lows, liquidity sweeps, and BOS after a sweep.
- `src/fx_annotation/setups.py` combines bias, sweep, BOS, and a first pullback entry-zone estimate, then ranks recent setup candidates.
- `src/fx_annotation/svg_renderer.py` renders a basic annotated SVG chart.
- `src/fx_annotation/report.py` renders a Markdown explanation report beside the chart.
- `src/fx_annotation/knowledge.py` loads strategy documents for the AI review layer.
- `src/fx_annotation/ai_review.py` builds the AI review context from fundamentals, strategy knowledge, and chart evidence.
- `src/fx_annotation/market_watch.py` scans multiple instruments and classifies each market state.
- `src/fx_annotation/live_memory.py` stores live setup state and next revisit timing.
- `src/fx_annotation/scheduler.py` calculates next-check timing from distance, volatility, setup state, and session.
- `src/fx_annotation/openai_client.py` calls OpenAI Responses API.
- `src/fx_annotation/live_ai.py` builds and sends live scheduling review prompts to GPT-5.5.
- `scripts/oanda_probe.py` runs the current probe from the command line.
- `scripts/annotate_oanda_setup.py` creates the current OANDA-based annotated setup chart.
- `scripts/market_watch.py` runs the autonomous multi-instrument scanner.

Example command:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\oanda_probe.py" --instrument EUR_USD --granularity M15 --count 300
```

The first successful run fetched `EUR_USD` M15 candles from OANDA and detected the latest sweep/BOS candidate. This is only a proof of concept, not yet a finished trading rule.

Create the first annotated chart:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\annotate_oanda_setup.py" --instrument EUR_USD --bias-granularity H4 --entry-granularity M15 --entry-count 400 --setup-limit 5
```

Current output file:

- `outputs/EUR_USD_H4_M15_annotation.svg`
- `outputs/EUR_USD_H4_M15_annotation.md`

Run the autonomous market watcher:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\market_watch.py"
```

This writes:

```text
outputs/market_watch.md
```

The latest refined run found a `SELL` candidate setup because H4 bias was bearish and the M15 setup aligned with that bias.

Latest refined run:

- H4 bias: bearish.
- Entry timeframe: M15.
- Swing points detected: 35.
- Liquidity sweeps detected: 83.
- Recent setup candidates: 5.
- Setup side: sell.
- Setup status: candidate.
- Entry zone source: last bullish candle before bearish BOS.
- Known issue: some recent candidates are still near-duplicates around the same swept/broken level.

Current autonomous market watcher output:

- `outputs/market_watch.md`
- `outputs/live_memory.json`
- `outputs/alerts.json`
- `outputs/ai_schedule_review.md` once OpenAI key is configured and `--use-ai` is used.
- `outputs/validation/review_checklist.md`

Latest scan marked `EUR_USD`, `AUD_USD`, and `GBP_JPY` as `entry_candidate_now`. These are candidate detections only. They have not yet been manually validated against chart screenshots or TradingView charts, so they should not be treated as proven accurate.

After stricter readiness filters, the scanner no longer marks those as immediate entries. It now separates:

- `entry_candidate_now`: price is at the entry zone now.
- `wait_for_pullback`: setup is aligned but price has not returned yet.
- `potential_future_setup`: setup exists, but the entry touch is stale or not actionable now.
- `watchlist`: technical setup exists, but higher-timeframe bias is unclear.
- `low_quality`: setup exists but quality filters are weak.

Current validation files:

- `outputs/validation/EUR_USD.svg`
- `outputs/validation/EUR_USD.md`
- `outputs/validation/AUD_USD.svg`
- `outputs/validation/AUD_USD.md`
- `outputs/validation/GBP_JPY.svg`
- `outputs/validation/GBP_JPY.md`
- `outputs/validation/GBP_USD.svg`
- `outputs/validation/GBP_USD.md`
- `outputs/validation/USD_JPY.svg`
- `outputs/validation/USD_JPY.md`
- `outputs/validation/review_checklist.md`

## Validation Status

The first strategy is **not done yet**.

Current state:

- OANDA candles are being fetched successfully.
- The scanner can classify multiple instruments.
- The scanner can mark possible buy/sell candidates.
- The scanner can identify watchlist states where a setup exists but bias is unclear.

Still required before calling it accurate:

- Compare detections against TradingView charts.
- Manually review marked entries against the notebook strategy.
- Reduce false positives and duplicate candidates.
- Automatically validate detected entries with test-only SL/TP outcomes.
- Add persistent memory so the agent can wait and revisit forming setups.
- Add AI review connected to a live model, not only prompt/context generation.
- Add alerting when a watched setup becomes ready or invalid.

Current validation process:

1. Run `scripts/validate_candidates.py`.
2. Review the generated `.svg` charts in `outputs/validation`.
3. Open `outputs/validation/review_checklist.md`.
4. Mark each candidate as `Good`, `Weak`, `Wrong`, `Duplicate`, or `Needs better rule`.
5. Use the feedback to adjust the detection logic.

Current automatic validation files:

- `outputs/auto_validation/outcomes.csv`
- `outputs/auto_validation/outcomes.md`

Automatic validation method:

- Entry is the midpoint of the marked entry zone.
- Test-only SL is placed beyond the sweep/zone with a small range buffer.
- Test-only TP is checked at `1:2R` and `1:3R`.
- If SL and TP happen in the same candle, the result is counted conservatively as SL first.
- If neither level hits within 48 M15 candles, the result is `timeout`.

Latest automatic validation result:

- 18 checks.
- 4 TP hits.
- 3 SL hits.
- 11 timeouts.
- 0 not triggered.

Interpretation:

- The detector is producing some valid candidates.
- The detector is not accurate enough yet.
- The stricter scanner reduced immediate-entry false positives.
- More setups now time out, which means stale/future setup handling still needs work.
- Outcome records should now drive the next rule improvements.

Latest live-mode status:

- No pair is currently marked as `entry_candidate_now`.
- `EUR_USD` and `NZD_USD` are currently `potential_future_setup`, not immediate entries.
- Other watched instruments are on the `watchlist` because the higher-timeframe bias is unclear.
- The agent stored next revisit times in `outputs/live_memory.json`.
- Next-check logic now considers distance to the entry zone, average recent candle range, setup state, and active session.
- A due-only live monitor now checks only instruments whose stored `next_check_time` has arrived.
- Alert records are still stored in `outputs/alerts.json`, and a readable alert log is now written to `outputs/alerts.md`.
- DeepSeek v4-pro now runs as a separate AI strategy-analysis route through `.env.deepseek`.
- Gemma can run as the frequent local AI strategy-analysis route through Ollama using `.env.ollama`.
- The rule-engine route, DeepSeek AI route, and Gemma AI route are independent. One does not approve, reject, filter, merge with, or confirm the other.
- Rule route outputs: `outputs/live_memory.json`, `outputs/alerts.json`, `outputs/alerts.md`, and rule chart annotations.
- AI route outputs: `outputs/ai_memory.json`, `outputs/ai_alerts.json`, `outputs/ai_alerts.md`, `outputs/ai_strategy_analysis.md`, and optional AI chart annotations.
- Gemma route outputs: `outputs/gemma_memory.json`, `outputs/gemma_alerts.json`, `outputs/gemma_alerts.md`, and `outputs/gemma_strategy_analysis.md`.
- OpenAI/GPT-5.5 live review is paused.

Run live watcher without AI:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\market_watch.py"
```

Run due-only live monitor:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\live_monitor.py"
```

Force a full monitor pass when memory needs to be initialized or refreshed:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\live_monitor.py" --force-all
```

Run due-only monitor with DeepSeek v4-pro strategy analysis:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\live_monitor.py" --use-ai
```

By default, DeepSeek analyzes only the top 3 due instruments per run to avoid full-watchlist timeouts. Use `--ai-limit 0` only when you intentionally want DeepSeek to review every scanned instrument.

Run due-only monitor with local Gemma through Ollama:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\live_monitor.py" --use-gemma --gemma-limit 1
```

Export current rule/DeepSeek/Gemma zones to TradingView Pine Script:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\export_tradingview_pine.py"
```

The generated file is:

```text
C:\Users\ADMIN\Desktop\signal\outputs\tradingview\market_agent_zones.pine
```

Paste it into TradingView Pine Editor, save it, and add it to the chart. It draws only the zones matching the current chart symbol.

Run a single annotated chart with both independent routes displayed:

```powershell
$env:PYTHONPATH="C:\Users\ADMIN\Desktop\signal\src"; python "scripts\annotate_oanda_setup.py" --instrument EUR_USD --use-ai
```

DeepSeek API key file:

```text
C:\Users\ADMIN\Desktop\signal\.env.deepseek
```

## Decisions

- Start with chart annotation, not automated trading.
- Start with rules, not machine learning.
- Start with entry zones only.
- Leave SL, TP, and execution to the user.
- Keep the first strategy narrow enough to test manually.
- Treat every annotation as a candidate setup, not a guarantee.
- Use OANDA as the first planned candle-data source.
- Discard the manual form GUI direction.
- Build an autonomous market-watching agent first.
- Start with written fundamentals from a file before adding economic calendar/news.
- Use a RAG-style strategy knowledge base so the AI can reason over the user's strategy, not only rigid rules.
- Add OpenClaw/OpenWork orchestration after the autonomous core works.
- Add TradingView display through Pine Script export first, then browser overlay only if needed.

## Open Questions

These should be answered before implementation begins:

1. Where is the user's OpenClaw/OpenWork project located?
2. Confirm the exact DeepSeek v4pro model name and endpoint if the defaults differ from the provider account.
3. Should the default watchlist include gold and which forex pairs?

## Project TODO

1. Draw DeepSeek chart notes like sweeps, supply/demand zones, and BOS levels on SVG charts.
2. Make DeepSeek analyze multiple pairs efficiently without timing out.
3. Give the AI route its own independent revisit scheduler and memory timing.
4. Strengthen AI route alert rules for `ENTRY_NOW`, `FORMING`, `WAIT`, and `NO_SETUP`.
5. Use automatic validation results to reduce rule-engine false positives and stale candidates.
6. Backtest the DeepSeek AI route separately from the rule engine.
7. Add real chart image or richer visual context for AI analysis. Current progress: DeepSeek now receives recent candles plus extracted swing and liquidity-sweep facts.
8. Connect the autonomous core to OpenClaw or OpenWork orchestration.
   - Current progress: `scripts/export_orchestration_state.py` writes state to `outputs/orchestration/` and OpenClaw memory.
9. Add alert delivery beyond local files.
   - Current progress: `scripts/deliver_alerts.py` can deliver rule/AI alerts to console, Windows `msg`, or Telegram via `.env.alerts`.
10. Expand implemented strategy concepts beyond sweep/BOS/zone.
    - Current progress: fair value gap detection is implemented and passed into the AI route as chart context.
11. Add economic calendar or live news/fundamental inputs.
    - Current progress: `scripts/fetch_fundamentals.py` can fetch RSS/Atom headlines into `outputs/fundamentals/latest.md`, which can be passed to monitors with `--fundamentals-file`.
12. Run the monitor as a managed always-on process or service.
    - Current progress: `scripts/run_always_on.py` runs fundamentals fetch, due-only monitor, alert delivery, and orchestration export in a loop.
