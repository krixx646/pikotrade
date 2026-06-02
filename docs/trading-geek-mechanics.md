# Trading Geek / Market Mechanics Specification

This document records the strategy rules extracted from the specific Trading Geek 10-hour course shared by the user:
`https://youtu.be/1F7rFzRSsqY`.

## Two Strategy Modes

The course describes two related but different models. The agent must not mix them.

### Simplified 15M Trade Plan

Use one execution timeframe, usually `M15`.

1. Identify M15 structure and trend.
2. After BOS, mark the swing high and swing low that define the current range.
3. Draw premium/discount across that swing range.
4. In a downtrend, wait for pullback into premium; in an uptrend, wait for pullback into discount.
   - Current backtest evidence favors stricter range location: buys in the lower 40-45% of the active range and sells in the upper 55-60%.
   - The default 50% midpoint is useful as a broad rule, but it still admits too many weak middle-range zones.
   - Current regime evidence favors pullback-phase entries over continuation-near-extreme entries; the directional-efficiency chop filter is not reliable enough to be a default gate.
5. Identify available liquidity:
   - In a downtrend, liquidity above swing highs.
   - In an uptrend, liquidity below swing lows.
6. Identify high-probability supply/demand:
   - Zone caused BOS.
   - Zone swept liquidity.
   - Zone caused opposing zone failure / flip.
   - Zone is extreme, not middle of range.
7. Enter with a limit order at the edge of the refined zone:
   - Buy at the upper edge of demand.
   - Sell at the lower edge of supply.
8. Stop goes beyond the zone/sweep.
9. Take profit is set-and-forget:
   - Fixed 3R, or
   - nearest opposing zone / weak structure if it is the cleaner target.
10. No default break-even, no default trailing, no default partials.

### Multi-Timeframe Sniper Model

Use three timeframe layers:

1. Higher timeframe: Daily / H4.
   - Defines swing structure and main narrative.
   - Identifies strong lows / strong highs and weak highs / weak lows.
   - Determines continuation phase or pullback phase.
2. Medium timeframe: H4 / H1.
   - Reads internal structure inside the HTF swing range.
   - Locates internal POIs and confirms whether the pullback is ending.
3. Lower timeframe: M15 / M5 / M1.
   - Finds entry model only.
   - Refines broad zones.
   - Does not create a trade against HTF order flow.

The key rule is: a run on the higher timeframe is a trend on the lower timeframe. Pullbacks create temporary opposite order flow; the trade is valid only when lower-timeframe order flow shifts back into alignment with the higher-timeframe swing structure.

## Market Structure Rules

- Bullish structure: higher highs and higher lows.
- Bearish structure: lower highs and lower lows.
- Strong low: low that caused a bullish BOS.
- Weak high: high expected to be taken in bullish order flow.
- Strong high: high that caused a bearish BOS.
- Weak low: low expected to be taken in bearish order flow.
- Trade from strong structure and target weak structure.
- Do not confuse a lower-timeframe pullback with a full higher-timeframe reversal.

## Entry Model

For a buy:

1. HTF structure is bullish or price is reacting from valid HTF demand.
2. Price pulls back into discount / demand / strong low.
3. Sell-side liquidity is swept.
4. Internal structure shifts bullish.
5. A lower-timeframe demand zone causes the market shift.
6. Entry is the demand-zone edge; SL beyond demand/sweep.
7. TP is 3R or weak high / opposing supply.

For a sell:

1. HTF structure is bearish or price is reacting from valid HTF supply.
2. Price pulls back into premium / supply / strong high.
3. Buy-side liquidity is swept.
4. Internal structure shifts bearish.
5. A lower-timeframe supply zone causes the market shift.
6. Entry is the supply-zone edge; SL beyond supply/sweep.
7. TP is 3R or weak low / opposing demand.

## Trading Geek Confluence Checklist

A setup is not verified because one pattern appears. It is verified only when the story checks out in order.

### Required Before Looking For Entry

1. Structure is readable.
   - Simplified model: M15 structure is readable.
   - Sniper model: HTF swing structure is readable first, then MTF/LTF structure is read inside it.
2. Price is in the correct phase.
   - For continuation buys, price has pulled back into a discount / demand area.
   - For continuation sells, price has pulled back into a premium / supply area.
3. Liquidity exists where retail stops are likely sitting.
   - Buy setup: sell-side liquidity below a low is available and swept.
   - Sell setup: buy-side liquidity above a high is available and swept.
4. The sweep is followed by a real market shift.
   - Buy setup: bullish BOS / shift after sell-side sweep.
   - Sell setup: bearish BOS / shift after buy-side sweep.
5. The entry zone is the zone that caused the shift.
   - Buy setup: demand/base immediately responsible for bullish shift.
   - Sell setup: supply/base immediately responsible for bearish shift.
6. The zone is high quality.
   - It is at an extreme of the relevant range, not in the middle.
   - It created displacement.
   - It is not already over-mitigated.
   - Stronger if it caused an opposing supply/demand failure or flip.
7. Entry is a pending limit at the zone edge after BOS.
   - Do not wait until price is already reacting deep inside the zone and call that the planned entry.
8. Stop goes beyond the zone and sweep.
9. Target is decided before entry.
   - Fixed 3R is valid.
   - Nearest opposing zone / weak structure is valid if cleaner.
10. Trade is set-and-forget unless a separate tested management plan is active.

### Filters To Test Separately

These confluences should be tested one at a time before stacking them:

- Premium/discount location.
- Entry reaction candle.
- HTF POI touched before setup.
- HTF POI touched now.
- H1 alignment.
- M5 refined entry.
- One trade per HTF zone.
- Zone quality score.
- BOS age.
- Timeout window.

If stacking all filters returns zero trades, that is not proof that the strategy has no trades. It means at least one filter is too strict, misapplied to the wrong mode, or implemented differently from the Trading Geek checklist.

## What The Agent Must Stop Doing

- Do not call 1R or break-even a successful trade objective.
- Do not trail by default.
- Do not enter from midpoint when the model uses zone-edge limit entries.
- Do not use M5/M1 to invent trades; use them only to refine a valid HTF/M15 idea.
- Do not target a far swing if a nearer opposing zone is the better set-and-forget objective.
- Do not mix simplified M15-only and multi-timeframe sniper rules inside the same backtest mode.
- Do not accept middle-of-range zones.
- Do not accept compression into a zone as immediate entry.

## Implementation Implication

The agent needs explicit strategy modes:

- `m15_simplified`: M15 structure, premium/discount, liquidity sweep, high-probability zone, edge entry, fixed 3R or opposing zone.
- `mtf_sniper`: HTF swing narrative, MTF alignment, lower-timeframe market shift, refined edge entry, dynamic target.

Backtests must report results separately per mode.
