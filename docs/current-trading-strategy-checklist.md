# Current Trading Strategy Checklist

This document describes the strategy the forex chart annotation agent is currently being built around.

The goal is not prediction, automated trading, TP, SL, or lot sizing. The goal is to mark possible buy/sell entry areas on a chart and explain why they may matter.

## 1. Main Strategy Idea

- This is a mechanical SMC multi-timeframe strategy.
- It is trend-following when the higher-timeframe narrative is clear.
- It is designed to capitalize on institutional liquidity hunts.
- Trade from market structure, not from prediction.
- Use higher-timeframe context to understand the likely direction or environment.
- Use lower-timeframe structure to locate possible entries.
- Look for liquidity being taken before considering an entry.
- Require a structure shift after liquidity is taken.
- Mark a pullback zone as the possible entry area.
- If the chart is unclear, stale, late, or conflicted, mark it as watchlist/weak/no setup.

## 2. Data Used

- OANDA candle data is the main source of truth.
- Candle data is used for chart geometry, not machine-learning prediction.
- The system uses OANDA candles to calculate:
  - Swing highs.
  - Swing lows.
  - Liquidity sweeps.
  - Break of structure.
  - Pullback zones.
  - Order-block-style zones.
  - Fair value gaps.
  - Distance from current price to zone.
  - Whether a setup is current, stale, or expired.

## 3. Timeframes

- Higher timeframe: `H4` by default.
- Refinement timeframe: `H1` when `H4` is noisy, too broad, or neutral.
- Lower/entry timeframe: `M15` by default.
- H4 is used for context.
- H1 is used as the working narrative only when H4 does not give a usable direction.
- M15 is used to find the actual possible entry area.
- The OCR rules also allow `Daily` as higher-timeframe context.
- The named mechanical entry workflow is the `15m TF Step by Step Rules`.

Future possible timeframes:

- Daily for broader bias.
- M5/M1 for sharper entry detail.

## 4. Higher-Timeframe Context

The agent first asks:

- Is the higher timeframe bullish, bearish, neutral, or unclear?
- Are swing highs and swing lows rising or falling?
- Is price near a major supply or demand area?
- Is price near support or resistance?
- Is price extended or in the middle of a range?
- Is the market trending or ranging?
- Has price pulled back into a major higher-timeframe point of interest?

Current rule-engine bias logic:

- Bullish bias: latest meaningful swing high and swing low are both higher.
- Bearish bias: latest meaningful swing high and swing low are both lower.
- Neutral bias: structure is mixed or there are not enough clear swings.
- Fallback rule: if `H4` is neutral/noisy but `H1` has a clear direction, the rule route uses `H1` as the effective HTF narrative.
- Directional gate applies to rule and AI routes:
  - Bullish effective direction = demand/BUY only.
  - Bearish effective direction = supply/SELL only.
  - Neutral/unclear effective direction = no actionable buy/sell zones.
- Active-story gate applies to rule and AI routes:
  - Old candles can help decide direction and find HH/LL.
  - After HH/LL are known, the active story starts from whichever anchor came first from left to right.
  - Candles before that active story start are old context only.
  - Demand/supply zones, liquidity targets, alerts, and chart annotations must come from the active story window onward.

## 4A. Higher-Timeframe POI Requirement

The OCR rules are stricter than a simple trend check.

The intended workflow is:

- Use `H4` or `Daily` to build the market narrative.
- Identify major points of interest before looking for entries.
- Main HTF POIs are:
  - Major supply zones.
  - Major demand zones.
  - Higher-timeframe order blocks.
- Wait for price to pull back into, mitigate, or react from one of these HTF POIs.
- Only after price reaches an HTF POI should the agent drop to M15 for entry confirmation.

Implementation status:

- Current system has basic H4 bias.
- Current system has lower-timeframe zones.
- Full HTF POI mitigation detection is not complete yet and should be added.

## 5. Liquidity Sweep

A liquidity sweep is the first warning sign.

Buy-side liquidity sweep:

- Price moves above a previous high.
- This can include a previous swing high, equal high, or double top.
- Price fails to hold above that high.
- Price closes back below the swept high.
- This can prepare a sell setup if bearish structure follows.

Sell-side liquidity sweep:

- Price moves below a previous low.
- This can include a previous swing low, equal low, or double bottom.
- Price fails to hold below that low.
- Price closes back above the swept low.
- This can prepare a buy setup if bullish structure follows.

Important rule:

- A sweep alone is not an entry.
- A sweep only says liquidity may have been taken.

## 6. Break Of Structure

After the sweep, the agent looks for a structure shift.

Bullish BOS:

- Happens after a sell-side liquidity sweep.
- Price breaks a meaningful swing high.
- This supports a possible buy setup.

Bearish BOS:

- Happens after a buy-side liquidity sweep.
- Price breaks a meaningful swing low.
- This supports a possible sell setup.

Important rule:

- Tiny internal breaks are weak.
- The broken level should be meaningful.
- Stronger BOS has displacement beyond the broken structure.

## 7. Entry Zone

The preferred entry is not the breakout candle itself.

The preferred entry is a pullback into a meaningful zone after sweep and BOS.

According to the OCR rules, after Market Shift/BOS the agent should identify the new unmitigated M15 demand/supply zone or FVG that caused the shift.

Possible buy entry zones:

- Demand zone.
- Last bearish candle before bullish displacement.
- Bullish order block.
- Bullish fair value gap.
- Retest of broken structure.
- Reasonable retracement zone only when supported by other evidence.

Possible sell entry zones:

- Supply zone.
- Last bullish candle before bearish displacement.
- Bearish order block.
- Bearish fair value gap.
- Retest of broken structure.
- Reasonable retracement zone only when supported by other evidence.

Important rule:

- Retracement alone is weak.
- The current validation showed retracement-only zones caused many timeouts, so the rule engine now penalizes them.
- The preferred zone should be unmitigated when possible.
- The preferred zone should be the zone that caused the market shift, not a random zone nearby.
- The original trade plan says entry is normally placed at the edge of the new LTF zone. In this project, the agent marks the zone only; the user decides whether/how to enter.

## 8. Order Block Logic

Bullish order-block-style zone:

- Usually a compact 1-3 candle bearish/base cluster before strong bullish displacement, with the last bearish candle acting as a narrower refinement inside that base.
- Also includes the active lowest-low origin base when bullish direction starts from that low and equal-low/sell-side liquidity is resting there.
- More useful when it appears after sell-side sweep and bullish BOS.

Bearish order-block-style zone:

- Usually a compact 1-3 candle bullish/base cluster before strong bearish displacement, with the last bullish candle acting as a narrower refinement inside that base.
- Also includes the active highest-high origin base when bearish direction starts from that high and equal-high/buy-side liquidity is resting there.
- More useful when it appears after buy-side sweep and bearish BOS.

Important rule:

- Not every opposite candle is a valid order block.
- It should belong to a tight base and be tied to displacement, sweep, BOS, or another meaningful zone.

## 9. Fair Value Gap Logic

Fair value gap means price moved inefficiently and left an imbalance.

The agent treats FVG as supporting evidence when:

- It appears during displacement.
- It aligns with the expected direction.
- It overlaps or sits near another meaningful zone.
- It supports the pullback area.

Important rule:

- FVG alone is not a full setup.
- It is supporting evidence, not the whole trade idea.

## 10. Setup States

The agent classifies setups into states.

`entry_candidate_now`:

- Price is at the proposed zone now.
- Setup is aligned enough to deserve attention.

`wait_for_pullback`:

- Sweep and BOS exist.
- Entry zone exists.
- Price has not pulled back into the zone yet.
- This matches the strategy stage where a limit order area may be prepared, but this project only marks the zone.

`potential_future_setup`:

- There is structure worth watching.
- It is not currently actionable.

`watchlist`:

- Pattern exists, but higher-timeframe bias is neutral or unclear.

`conflict`:

- Pattern exists, but it conflicts with higher-timeframe bias.

`low_quality`:

- Pattern exists, but quality filters are weak.

`expired`:

- Pattern existed, but too many candles passed after BOS.
- The agent should wait for fresh structure.

`no_clear_state`:

- No clean setup or market state.

## 11. Quality Filters

The rule engine gives a quality score based on:

- Whether BOS closes meaningfully beyond structure.
- Whether sweep-to-BOS move has displacement.
- Whether the zone is near a useful range edge.
- Whether the zone source is strong or weak.

Current penalty:

- 50-70 percent retracement-only zones are penalized because validation showed many timeouts.

## 12. AI Route Responsibility

DeepSeek and Gemma are independent analysts.

They should:

- Read the strategy knowledge base.
- Read OANDA-derived chart facts.
- Read fundamentals if available.
- Analyze the chart independently.
- Return their own side, status, zone, confidence, reasoning, and alert.

They should not:

- Approve the rule engine.
- Reject the rule engine.
- Merge results with the rule engine.
- Act as a backup filter.

The user sees each route separately and decides what to trust.

## 13. Rule Route Responsibility

The rule engine is an evidence extractor and mechanical route.

It should:

- Detect swings.
- Detect sweeps.
- Detect BOS.
- Detect FVGs.
- Build zones.
- Score quality.
- Track state.
- Store memory.
- Export zones to TradingView Pine Script.

It should not:

- Pretend to be the final human-like analyst.
- Override AI routes.
- Place trades.

## 14. Fundamentals

Fundamental notes are optional context.

They can come from:

- User-written notes.
- RSS/Atom headline fetcher.
- Future economic calendar integration.

Fundamentals should influence caution and confidence.

Examples:

- If fundamentals support USD strength, be cautious with setups that sell USD.
- If fundamentals conflict with the chart, say so.
- If fundamentals are unclear, the setup may stay watchlist.

## 15. No-Trade Conditions

The agent should be comfortable saying no setup.

No-trade or weak conditions:

- No clear sweep.
- No meaningful BOS.
- Price is in the middle of a range.
- Price already ran too far from the zone.
- Pullback already happened long ago.
- Setup is expired.
- Higher-timeframe bias is unclear.
- Technical setup conflicts with fundamentals.
- Zone is too wide or unclear.
- Multiple signals contradict each other.
- High-impact news is too close.

## 16. Original Trade Management Notes

The OCR rules include trade management rules:

- Stop loss behind the entry supply/demand zone or absolute swing high/low.
- Add a 5-10 pip buffer.
- Take profit at opposing HTF liquidity or use strict `+3R`.
- Set and forget.
- No partial profit-taking.
- No trailing stop.

Project boundary:

- These rules are documented for strategy completeness.
- The agent should not instruct live SL, TP, lot size, or execution.
- SL/TP can be used only for historical validation and measurement.
- The user remains responsible for all trade management.

## 17. What Gets Displayed

Rule route:

- Displays exact mechanical zones when they exist.
- Exports to TradingView Pine Script.

DeepSeek route:

- Displays its own independent analysis.
- Exports zones only if it gives exact `entry_zone_low` and `entry_zone_high`.

Gemma route:

- Displays its own independent analysis.
- Exports zones only if it gives exact `entry_zone_low` and `entry_zone_high`.

Important rule:

- Vague comments like "watch for BOS" are not exported as TradingView zones.
- Only concrete price zones are exported as drawable TradingView zones.

## 18. Visual Context Workflow

Preferred visual input:

- Use OpenClaw/OpenWork to capture real TradingView screenshots when available.
- Capture the timeframe the AI needs for the current story phase:
  - `4H` for narrative.
  - `1H` when 4H is broad/noisy and needs refinement.
  - `15M` when price is testing/respecting a zone and execution confirmation is needed.

Fallback visual input:

- Render local chart images from OANDA candles.
- These images are not pixel-identical TradingView screenshots.
- They must remain truthful to OANDA OHLC data and display the same zones, ladder states, liquidity areas, sweeps, and Market Shift/BOS evidence used by the rule engine.

Important rule:

- TradingView screenshots are preferred for visual context.
- OANDA-rendered charts are the reliable fallback and debug view.
- OANDA numbers remain the source of truth for rule-engine calculations.

## 19. Current Implementation Status

Implemented:

- H4 bias detection from higher-timeframe swing structure.
- H1 fallback bias when H4 is neutral/noisy.
- H4/H1 POI and zone-ladder detection from base-before-impulse demand/supply logic:
  - Demand = compact base/order-block cluster before bullish displacement/BOS.
  - Supply = compact base/order-block cluster before bearish displacement/BOS.
- Directional filtering:
  - Bullish HTF direction marks demand/buy zones only.
  - Bearish HTF direction marks supply/sell zones only.
  - Neutral/unclear HTF direction marks no rule-route buy/sell zones.
- HTF POI mitigation check that records whether current price is inside the relevant POI or how far away price is.
- Rule-engine sequence enforcement: `HTF POI mitigated -> M15 sweep -> M15 BOS`.
- M15 liquidity sweep detection.
- M15 BOS / Market Shift detection after the sweep.
- M15 entry zone detection from the compact base that caused BOS, with retracement fallback only when no base is found.
- Live scanner memory, revisit timing, alerts, independent DeepSeek/Gemma routes, and TradingView Pine export.

Still approximate:

- HTF POIs now require displacement, but they are still a rule-engine approximation of order blocks and should continue to be improved with validation.
- Daily POI enforcement is not yet combined with H4 POI enforcement.
- The final LTF zone freshness rule is still approximate, so the sequence is not yet a perfect `fresh unmitigated M15 zone` model.
- LTF entry zones do not yet fully distinguish every valid order block from every fair value gap candidate.

## 20. Current Strategy In One Line

Build the H4/Daily narrative, wait for price to reach an HTF POI, drop to M15, wait for a liquidity sweep, confirm Market Shift/BOS, then mark the new unmitigated LTF demand/supply/FVG pullback zone while ignoring setups that are unclear, stale, conflicted, or late.
