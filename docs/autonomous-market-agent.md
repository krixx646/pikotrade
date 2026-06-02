# Autonomous Market Agent Architecture

This replaces the discarded manual form GUI direction.

The product should behave like a tireless market-watching analyst, not like a chatbot form or a rigid automation script.

## Core Idea

The agent should continuously understand the current position of multiple markets.

It should:

- Scan many instruments.
- Know the current state of each chart.
- Identify immediate entry candidates.
- Identify setups that are not ready yet but may become valid soon.
- Wait for price to reach important areas.
- Revisit charts when conditions change.
- Alert the user when attention is needed.
- Explain why a pair is ready, waiting, weak, invalid, or no trade.

## Human-Like Behaviour To Mimic

A human trader does not only ask one chart one question.

A human trader:

- Checks multiple pairs.
- Reads the current chart position.
- Forms directional expectations.
- Notices when a setup is developing.
- Waits for second confirmation.
- Sets mental reminders or alerts.
- Returns later when price reaches a zone.
- Abandons setups when the reason disappears.

The agent should do this better than a human by using:

- Always-on monitoring.
- Better memory.
- More instruments at once.
- Exact OANDA price figures.
- Strategy knowledge base.
- AI reasoning over chart evidence and fundamentals.

## Required Components

### 1. Market Watcher

Scans a default watchlist:

- `EUR_USD`
- `GBP_USD`
- `USD_JPY`
- `USD_CAD`
- `AUD_USD`
- `NZD_USD`
- `EUR_JPY`
- `GBP_JPY`
- `XAU_USD`

The watchlist should be configurable later.

### 2. Chart State Engine

For each instrument, it should track:

- Higher-timeframe bias.
- Current lower-timeframe structure.
- Key highs/lows.
- Recent liquidity sweeps.
- BOS or CHOCH.
- Pullback zones.
- Whether price has reached the zone.
- Whether the setup is immediate, forming, invalid, or no trade.

### 3. Setup Lifecycle

Every setup should have a lifecycle:

- `no_clear_state`
- `potential_future_setup`
- `wait_for_sweep`
- `wait_for_bos`
- `wait_for_pullback`
- `entry_candidate_now`
- `watchlist`
- `conflict`
- `invalidated`
- `expired`

The lifecycle lets the agent monitor setups that are not ready yet.

### 4. AI Strategy Brain

The AI should not merely obey hardcoded rules.

The rule engine gathers evidence. The AI reviews:

- Strategy knowledge base.
- User's fundamental analysis.
- OANDA chart evidence.
- Current setup lifecycle.
- Conflicts and uncertainty.

The AI should decide whether the setup is:

- Strong.
- Watchlist.
- Weak.
- Invalid.
- No trade.

### 5. Memory

The agent needs memory so it can revisit setups.

Memory should store:

- Instrument.
- Current state.
- Setup reason.
- Entry zone being watched.
- Last seen price.
- Next revisit time.
- Invalidation reason.
- AI notes.

### 6. Alerts

The agent should alert only when useful:

- Price reaches watched zone.
- Second confirmation appears.
- Setup becomes invalid.
- New strong candidate appears.
- High-impact news is close.

Alerts can be added later through desktop notification, Telegram, email, or OpenClaw/OpenWork.

### 7. TradingView Overlay

TradingView overlay is a later step.

The agent should first become useful as an autonomous scanner. After that, TradingView can be used as the visual surface where the agent draws:

- Entry zone.
- Swept level.
- BOS level.
- Setup state.
- AI explanation.

## Platform Direction

OpenClaw/OpenWork may be useful as the orchestration layer, but the core agent should be platform-neutral:

- OANDA client.
- Market watcher.
- State memory.
- AI review layer.
- Alert system.

Then OpenClaw/OpenWork can host or coordinate the agent if available.

## Current Implementation

Implemented:

- OANDA candle fetching.
- Basic structure detection.
- Basic setup detection.
- Strategy knowledge base.
- AI review context builder.
- Multi-pair market watcher script.

Discarded:

- Manual local GUI form.

Next:

- Connect the market watcher to a live AI model.
- Add persistent memory for watched setups.
- Add revisit scheduling.
- Add alerts.
- Later add TradingView overlay.
