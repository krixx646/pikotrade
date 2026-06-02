# Strategy Knowledge Base

This document is the first written knowledge base for the chart annotation agent. It is based on the user's notebook images and project direction.

The goal is for the AI layer to understand the trading style, not only follow rigid code branches. The rule engine should collect evidence from OANDA candles, while the AI should reason over this knowledge base, the chart evidence, and the user's fundamental notes.

## Strategy Identity

The strategy is a discretionary forex chart-reading approach focused on:

- Higher-timeframe direction.
- Market structure.
- Liquidity sweep.
- Break of structure.
- Pullback into a meaningful zone.
- Demand and supply.
- Support and resistance.
- Order blocks.
- Fair value gaps or imbalance.
- Session timing.
- Fundamental bias.

The agent should mark possible entry points only. It should not control stop loss, take profit, lot size, or execution.

## Core Workflow

The preferred workflow follows The Trading Geek / Market Mechanics model:

1. Identify structure and order flow first: bullish, bearish, or range.
2. Define the active swing range: strong low to weak high for bullish conditions, strong high to weak low for bearish conditions.
3. In bullish conditions, buy only from strong lows / demand in discount. In bearish conditions, sell only from strong highs / supply in premium.
4. Mark available liquidity around clean highs/lows, equal highs/lows, trendline liquidity, and obvious retail stop locations.
5. Select high-probability supply/demand zones only: zones that caused BOS, caused a flip/failure of the opposing zone, swept liquidity, or sit at the extreme/last line of defense.
6. Wait for price to pull back into premium/discount and mitigate a valid zone. Avoid middle-of-range zones.
7. Look for liquidity sweep and market shift/BOS. The sweep must clear liquidity; the BOS must be a meaningful shift, not a tiny internal break.
8. Enter at the edge of the refined zone, not the midpoint: buy from the upper edge of demand as price returns down; sell from the lower edge of supply as price returns up.
9. Place the test stop beyond the zone/sweep. Do not trail by default.
10. Target dynamically: full TP at 3R when that is the clean objective, or at the opposing zone / weak structure / swing high-low when the trade context supports 4R, 5R, or more.
11. Warn if the setup is unclear, late, compressing into a trap, in the middle 40 percent of a range, or fighting HTF order flow.

## Fundamental Bias

Fundamentals should influence the direction the agent prefers.

Examples:

- If the user's fundamental analysis favors USD strength, the agent should be more cautious with setups that sell USD.
- If fundamentals are unclear, the agent can mark chart candidates as watchlist setups instead of strong candidates.
- If a high-impact event is nearby, the agent should warn that technical entries may be less reliable.

The AI should not blindly reject a chart setup because of fundamentals, but it should clearly state when chart and fundamentals conflict.

## Higher-Timeframe Analysis

The higher timeframe is used to understand context, not to enter directly.

Important higher-timeframe questions:

- Is price trending or ranging?
- Is price near supply or demand?
- Is price near support or resistance?
- Has price recently swept liquidity?
- Is price pushing toward a major high or low?
- Is the current move extended?

Default higher timeframe process:

- Use H4 first for the main narrative.
- Use H1 only when H4 is noisy, broad, or not specific enough.
- Use 15M for execution only.

Possible broad-context timeframe:

- Daily.

## Active Story Window

Old candles are useful at first because they help define the higher-timeframe direction and the important range anchors.

After the highest high and lowest low are found:

- Compare which anchor came first from left to right.
- The current active story starts from that first anchor.
- Candles before that first anchor are old context.
- Old context must not be used to choose current demand/supply zones, liquidity targets, entry zones, alerts, or chart annotations.
- Demand/supply/order-block zones should be selected only from the active story window onward.
- The active story window is still bounded by the HH/LL range and the effective direction.

## Zone Ladder Monitoring

The agent should not assume the first demand/supply zone will hold.

It should build a zone ladder:

- In bullish conditions, watch demand/order-block zones below or around price.
- In bearish conditions, watch supply/order-block zones above or around price.
- In unclear conditions, avoid marking actionable buy/sell zones. If H4 is unclear, use H1 refinement; if H1 still does not resolve direction, return no setup.
- Opposite-side zones can exist on the chart, but the strategy ignores them because it rides the higher-timeframe direction instead of trading against it.

Each zone can be:

- Untouched: price has not reached it yet.
- Approaching: price is near the zone.
- Inside: price is currently testing the zone.
- Respected: price tested the zone and reacted in the expected direction.
- Failed: price disrespected or broke through the zone.

If a zone fails, discard it and monitor the next valid zone in the ladder. If a zone is respected, move attention to the 15M execution sequence.

Zones in the middle of a range are low quality. In a consolidation, focus only on the top 30 percent for supply and bottom 30 percent for demand. Avoid the middle 40 percent unless price has already broken out and is retesting with clear structure.

The execution sequence is:

1. Price tests/respects HTF or refined 1H demand/supply.
2. 15M liquidity sweep happens first.
3. 15M Market Shift / BOS happens second.
4. Price pulls back to the LTF zone that caused the shift.
5. The agent marks the possible buy/sell entry zone.

For the simplified 15M plan, the same sequence can be applied on M15 alone:

1. Identify M15 structure and trend.
2. Draw premium/discount from the latest swing high to swing low.
3. Identify available liquidity above swing highs in a downtrend or below swing lows in an uptrend.
4. Enter only at a high-probability zone that swept liquidity or caused a meaningful break.
5. Full TP is normally 3R, or the next weak structure/opposing zone when that is the cleaner objective.

## Lower-Timeframe Entry

The lower timeframe is used to find the entry area.

Default lower timeframe for the MVP:

- M15.

Possible future lower timeframes:

- M5.
- M1.

The lower timeframe should be used to confirm:

- Sweep of local highs/lows.
- Break of structure.
- Pullback into a valid zone.
- Cleaner risk location for the user to decide.

Lower timeframes refine entries; they do not create independent trade ideas against HTF order flow. M5/M1 refinement is allowed only to tighten the zone and improve R after the M15/H1/H4 story is already valid.

## Liquidity Sweep

A liquidity sweep happens when price takes a previous high or low and then rejects.

Buy-side liquidity sweep:

- Price moves above a previous high.
- Price fails to hold above it.
- Price closes back below the swept high.
- This can prepare a sell setup if followed by bearish structure break.

Sell-side liquidity sweep:

- Price moves below a previous low.
- Price fails to hold below it.
- Price closes back above the swept low.
- This can prepare a buy setup if followed by bullish structure break.

The AI should understand that a sweep alone is not an entry. The sweep is only the first warning that liquidity may have been taken.

## Break Of Structure

Break of structure confirms that price has shifted after the sweep.

Bullish BOS:

- After a sell-side liquidity sweep, price breaks a meaningful swing high.
- This supports a possible buy setup.

Bearish BOS:

- After a buy-side liquidity sweep, price breaks a meaningful swing low.
- This supports a possible sell setup.

The AI should check whether the broken level was meaningful. Tiny internal breaks in noise should be treated cautiously.

## Pullback Entry

The preferred entry is usually not the first breakout candle.

The agent should look for a pullback into:

- Demand zone for buys.
- Supply zone for sells.
- Order block.
- Fair value gap.
- Retest of broken structure.
- Reasonable retracement zone.

The agent should mark the edge of the refined zone where the limit-style entry would be considered:

- Buy entry: upper edge of the demand/refined pivot zone.
- Sell entry: lower edge of the supply/refined pivot zone.

The agent should not force an entry if price is already too far gone.

## Demand And Supply

Demand zone:

- Area where buy orders previously showed control and launched price upward.
- Usually a compact 1-3 candle base before expansion, not a random candle or wide chop.
- Common patterns are drop-base-rally and rally-base-rally.
- The base should sit before a meaningful bullish impulse, BOS, or displacement.
- Used as possible buy entry after bullish confirmation.

Supply zone:

- Area where sell orders previously showed control and drove price downward.
- Usually a compact 1-3 candle base before expansion, not a random candle or wide chop.
- Common patterns are rally-base-drop and drop-base-drop.
- The base should sit before a meaningful bearish impulse, BOS, or displacement.
- Used as possible sell entry after bearish confirmation.

The AI should prefer clean base-before-impulse zones that caused meaningful displacement. It should reject zones that are merely middle-of-range candles without a clear base, impulse, sweep, BOS, or structure role.

Fresh or lightly tested zones are strongest. Repeatedly mitigated/respected zones can remain as context or ladder zones, but they should not be treated as fresh entries without new LTF sweep and BOS confirmation.

High-probability zones should satisfy at least one major institutional reason:

- The zone caused a meaningful BOS.
- The zone caused an opposing zone to fail, creating a flip zone.
- The zone swept liquidity before displacement.
- The zone is an extreme zone / last line of defense in the active swing range.

Low-probability zones:

- Middle-of-range zones.
- Zones approached through slow compression.
- Zones that only broke tiny internal structure.
- Wide chop zones without displacement.

## Order Blocks

An order block is a narrower candle-level refinement inside or near a broader supply/demand base. It is often the last opposing candle before a strong move, but it is only meaningful when that candle belongs to a base that caused displacement or structure break.

Bullish order block:

- Last bearish candle or bearish cluster inside a demand base before strong bullish displacement.
- Can be used as a possible buy zone.

Bearish order block:

- Last bullish candle or bullish cluster inside a supply base before strong bearish displacement.
- Can be used as a possible sell zone.

The AI should not treat every candle as an order block. The candle should be tied to a base, displacement, sweep, BOS, or a meaningful zone.

## Fair Value Gap And Imbalance

A fair value gap or imbalance shows inefficient price movement.

The agent can use it as a possible pullback target when:

- It appears during displacement.
- It aligns with the expected direction.
- It is near another meaningful zone.

The AI should treat FVG as supporting evidence, not a standalone signal.

## Support And Resistance

Support and resistance are important reaction areas.

The agent should identify:

- Previous swing highs.
- Previous swing lows.
- Repeated reaction levels.
- Range highs and range lows.
- Major zones from higher timeframe.

The AI should be cautious when price is in the middle of a range with no clear edge.

## Moving Average

Moving average can be used as supporting context.

Possible uses:

- Trend direction.
- Dynamic support or resistance.
- Avoiding trades directly against strong momentum.
- 200-period moving average trend context:
  - Price above the 200-period moving average supports bullish/uptrend context.
  - Price below the 200-period moving average supports bearish/downtrend context.

Moving average should not be the main signal. The strategy still requires HTF narrative, demand/supply, liquidity sweep, Market Shift/BOS, and pullback entry logic.

## Fibonacci And Retracement

The notes mention pullbacks and retracement areas.

The agent may use retracement as supporting context:

- Pullback into 50 percent to 70 percent of the displacement.
- Pullback into a zone that overlaps with order block, demand/supply, or structure.

Retracement alone should not be treated as a complete setup.

For trend continuation, premium/discount should be drawn on the current swing range:

- In bullish conditions, look for demand in discount.
- In bearish conditions, look for supply in premium.
- In ranges, only top 30 percent supply and bottom 30 percent demand are valid areas.

## Targets And Trade Management

The strategy does not treat 1R as a profit objective. A move to 1R may be useful information, but the Trading Geek simplified plan is set-and-forget:

- Place entry.
- Place SL beyond the zone/sweep.
- Place full TP.
- Do not trail SL by default.
- Do not take partials by default.

Target selection is dynamic:

- Minimum acceptable objective is usually 3R.
- If the nearest opposing zone or weak structure gives only 1R-2R, skip the trade.
- If the opposing zone / weak structure gives 4R, 5R, or more, the target can be set there.
- For buys, target weak highs, swing highs, buy-side liquidity, or opposing supply.
- For sells, target weak lows, swing lows, sell-side liquidity, or opposing demand.

## Session Timing

The notes mention active trading windows and avoiding slow markets.

Preferred sessions:

- London.
- New York.
- London/New York overlap.

The agent should be cautious during low-liquidity periods unless the setup is very clear.

## No-Trade Conditions

The AI should be comfortable saying "no clear entry".

No-trade or weak conditions:

- Choppy range.
- Middle of range.
- No clear sweep.
- No meaningful BOS.
- Price already moved far away from entry zone.
- Fundamentals conflict strongly with the setup.
- High-impact news is too close.
- Zone is unclear or too wide.
- Multiple signals contradict each other.

## AI Reasoning Role

The AI should not simply obey hardcoded steps.

The AI should:

- Read the strategy knowledge base.
- Read the user's fundamental notes.
- Review OANDA-derived chart evidence.
- Compare the evidence against the strategy.
- Explain why a setup is strong, weak, or invalid.
- Notice when the rule engine produced a technically valid but low-quality setup.
- Avoid pretending certainty.

The rule engine should provide evidence. The AI should judge the evidence.

## Example AI Judgment

Strong candidate:

- Fundamentals support the direction.
- Higher timeframe supports the direction.
- Price swept liquidity.
- Price broke meaningful structure.
- Pullback zone is clear.
- Entry is not late.

Watchlist:

- Technical setup exists.
- Fundamentals are neutral or unclear.
- Bias is mixed.
- Pullback has not happened yet.

Weak or invalid:

- Technical setup conflicts with fundamentals.
- Price is in the middle of a range.
- Sweep is tiny or unclear.
- BOS is not meaningful.
- Price already ran too far.

## Current MVP Direction

Build a local GUI first:

- User selects instrument and timeframes.
- User pastes fundamental analysis.
- App fetches OANDA candles.
- Rule engine extracts chart evidence.
- AI reviews strategy knowledge, fundamentals, and chart evidence.
- App shows chart, entry zone, and AI explanation.

TradingView overlay comes later after the local GUI and AI review loop are useful.
