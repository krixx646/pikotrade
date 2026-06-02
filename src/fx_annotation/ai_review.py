from dataclasses import dataclass

from fx_annotation.bias import Bias
from fx_annotation.candles import Candle
from fx_annotation.knowledge import load_strategy_knowledge
from fx_annotation.setups import SetupCandidate
from fx_annotation.structure import Sweep, SwingPoint
from fx_annotation.trade_targets import TradeTarget


@dataclass(frozen=True)
class AiReviewInput:
    instrument: str
    bias_granularity: str
    entry_granularity: str
    fundamentals: str
    bias: Bias
    entry_candles: list[Candle]
    swings: list[SwingPoint]
    sweeps: list[Sweep]
    primary_setup: SetupCandidate | None
    recent_setups: list[SetupCandidate]
    trade_target: TradeTarget | None = None
    available_r: float | None = None


def build_ai_review_prompt(review_input: AiReviewInput) -> str:
    knowledge = load_strategy_knowledge()
    evidence = _chart_evidence(review_input)

    return f"""You are a forex chart annotation assistant.

Your job is to review possible entry areas using the user's strategy knowledge, the user's fundamental analysis, and OANDA-derived chart evidence.

Important boundaries:
- Do not place trades.
- Do not provide stop loss, take profit, lot size, or execution instructions.
- Do not promise win rate or certainty.
- Mark only possible buy/sell entry areas and explain the reasoning.
- Be willing to say there is no clean entry.

Strategy knowledge:
{knowledge}

User fundamental analysis:
{review_input.fundamentals or "No fundamental notes provided."}

OANDA chart evidence:
{evidence}

Return:
1. Direction preference: BUY, SELL, NEUTRAL, or NO TRADE.
2. Best entry area if one exists.
3. Whether fundamentals support or conflict with the chart.
4. Why the setup is strong, watchlist, weak, or invalid.
5. What the user should manually confirm before taking any trade.
"""


def _chart_evidence(review_input: AiReviewInput) -> str:
    lines = [
        f"Instrument: {review_input.instrument}",
        f"Higher timeframe: {review_input.bias_granularity}",
        f"Entry timeframe: {review_input.entry_granularity}",
        f"Higher-timeframe bias: {review_input.bias.direction}",
        f"Bias reason: {review_input.bias.reason}",
        f"Entry candles analyzed: {len(review_input.entry_candles)}",
        f"Swing points detected: {len(review_input.swings)}",
        f"Liquidity sweeps detected: {len(review_input.sweeps)}",
        f"Recent setup candidates: {len(review_input.recent_setups)}",
        "",
    ]

    if review_input.primary_setup is None:
        lines.append("Primary setup: none")
        return "\n".join(lines)
    if review_input.trade_target is not None:
        lines.extend(
            [
                "Trade target:",
                f"- Target price: {review_input.trade_target.price:.5f}",
                f"- Target timeframe: {review_input.trade_target.timeframe}",
                f"- Target swing: {review_input.trade_target.swing_kind} at {review_input.trade_target.candle_time.isoformat()}",
                f"- Available R to target: {review_input.available_r}",
                f"- Target reason: {review_input.trade_target.reason}",
                "",
            ]
        )

    lines.extend(
        [
            "Primary setup:",
            _setup_summary(review_input.entry_candles, review_input.primary_setup),
            "",
            "Recent setups:",
        ]
    )

    for index, setup in enumerate(review_input.recent_setups, start=1):
        lines.append(f"Candidate {index}:")
        lines.append(_setup_summary(review_input.entry_candles, setup))

    return "\n".join(lines)


def _setup_summary(candles: list[Candle], setup: SetupCandidate) -> str:
    sweep_candle = candles[setup.sweep.index]
    bos_candle = candles[setup.bos.index]
    pullback = "touched" if setup.entry_zone.touched_after_bos else "not touched"

    return "\n".join(
        [
            f"- Side: {setup.side.upper()}",
            f"- Status: {setup.status}",
            f"- Reason: {setup.reason}",
            f"- Sweep kind: {setup.sweep.kind}",
            f"- Sweep time: {sweep_candle.time.isoformat()}",
            f"- Swept price: {setup.sweep.swept_price:.5f}",
            f"- BOS direction: {setup.bos.direction}",
            f"- BOS time: {bos_candle.time.isoformat()}",
            f"- Broken price: {setup.bos.broken_price:.5f}",
            f"- Entry zone: {setup.entry_zone.low:.5f} - {setup.entry_zone.high:.5f}",
            f"- Entry source: {setup.entry_zone.source}",
            f"- Pullback: {pullback}",
            f"- Current state: {setup.current_state}",
            f"- Quality score: {setup.quality_score}",
            f"- Quality notes: {'; '.join(setup.quality_notes)}",
        ]
    )
