from fx_annotation.ai_strategy import AiStrategyAnalysis, ai_alert_for_analysis
from fx_annotation.bias import Bias
from fx_annotation.candles import Candle
from fx_annotation.setups import SetupCandidate
from fx_annotation.structure import Sweep, SwingPoint


def render_setup_report(
    instrument: str,
    bias_granularity: str,
    entry_granularity: str,
    bias: Bias,
    entry_candles: list[Candle],
    swings: list[SwingPoint],
    sweeps: list[Sweep],
    setup: SetupCandidate | None,
    recent_setups: list[SetupCandidate],
    chart_path: str,
    ai_analysis: AiStrategyAnalysis | None = None,
) -> str:
    lines = [
        f"# {instrument} Chart Annotation Report",
        "",
        "## Inputs",
        "",
        f"- Higher timeframe: {bias_granularity}",
        f"- Entry timeframe: {entry_granularity}",
        f"- Entry candles analyzed: {len(entry_candles)}",
        f"- Swing points detected: {len(swings)}",
        f"- Liquidity sweeps detected: {len(sweeps)}",
        f"- Recent setup candidates: {len(recent_setups)}",
        f"- Chart file: `{chart_path}`",
        "",
        *_ai_report_lines(ai_analysis),
        "## Higher-Timeframe Bias",
        "",
        f"- Bias: {bias.direction}",
        f"- Reason: {bias.reason}",
        "",
    ]

    if setup is None:
        lines.extend(
            [
                "## Setup",
                "",
                "No complete liquidity sweep plus BOS setup was found in the current sample.",
                "",
                "## Decision",
                "",
                "No entry zone should be marked from this run.",
            ]
        )
        return "\n".join(lines) + "\n"

    sweep_candle = entry_candles[setup.sweep.index]
    bos_candle = entry_candles[setup.bos.index]
    pullback_state = (
        "Price has already touched the proposed entry zone after BOS."
        if setup.entry_zone.touched_after_bos
        else "Price has not yet pulled back into the proposed entry zone."
    )

    lines.extend(
        [
            "## Primary Setup",
            "",
            f"- Side: {setup.side.upper()}",
            f"- Status: {setup.status}",
            f"- Reason: {setup.reason}",
            f"- Current state: {setup.current_state}",
            f"- Quality score: {setup.quality_score}",
            f"- Liquidity sweep: {setup.sweep.kind}",
            f"- Sweep time: {sweep_candle.time.isoformat()}",
            f"- Swept price: {setup.sweep.swept_price:.5f}",
            f"- BOS direction: {setup.bos.direction}",
            f"- BOS time: {bos_candle.time.isoformat()}",
            f"- Broken structure price: {setup.bos.broken_price:.5f}",
            "",
            "## Entry Zone",
            "",
            f"- Zone low: {setup.entry_zone.low:.5f}",
            f"- Zone high: {setup.entry_zone.high:.5f}",
            f"- Source: {setup.entry_zone.source}",
            f"- Pullback status: {pullback_state}",
            "",
            "## Decision",
            "",
            _decision_text(setup),
            "",
            "## Reminder",
            "",
            "This tool marks possible entry areas only. The user is responsible for SL, TP, position size, and whether to take the trade.",
            "",
            "## Quality Notes",
            "",
            *[f"- {note}" for note in setup.quality_notes],
        ]
    )

    lines.extend(_recent_setup_lines(entry_candles, recent_setups))

    return "\n".join(lines) + "\n"


def _decision_text(setup: SetupCandidate) -> str:
    if setup.status == "candidate":
        return "This is a bias-aligned candidate setup, but it still requires user confirmation."
    if setup.status == "watchlist":
        return "This is a watchlist setup because the higher-timeframe bias is neutral."
    if setup.status == "expired":
        return "This setup is expired because too many candles have passed since BOS."
    return "This setup conflicts with higher-timeframe bias and should be treated cautiously."


def _recent_setup_lines(
    entry_candles: list[Candle],
    recent_setups: list[SetupCandidate],
) -> list[str]:
    if not recent_setups:
        return []

    lines = [
        "",
        "## Recent Setup Candidates",
        "",
        "These are the recent sweep plus BOS patterns ranked by status, pullback, and recency.",
        "",
    ]

    for position, setup in enumerate(recent_setups, start=1):
        sweep_candle = entry_candles[setup.sweep.index]
        bos_candle = entry_candles[setup.bos.index]
        touched = "touched" if setup.entry_zone.touched_after_bos else "not touched"
        lines.extend(
            [
                f"### Candidate {position}",
                "",
                f"- Side: {setup.side.upper()}",
                f"- Status: {setup.status}",
                f"- Sweep time: {sweep_candle.time.isoformat()}",
                f"- Swept price: {setup.sweep.swept_price:.5f}",
                f"- BOS time: {bos_candle.time.isoformat()}",
                f"- Broken price: {setup.bos.broken_price:.5f}",
                f"- Entry zone: {setup.entry_zone.low:.5f} - {setup.entry_zone.high:.5f}",
                f"- Pullback: {touched}",
                f"- Current state: {setup.current_state}",
                f"- Quality score: {setup.quality_score}",
                f"- Reason: {setup.reason}",
                "",
            ]
        )

    return lines


def _ai_report_lines(ai_analysis: AiStrategyAnalysis | None) -> list[str]:
    if ai_analysis is None:
        return []

    alert = ai_alert_for_analysis(ai_analysis)
    zone = "none"
    if ai_analysis.entry_zone_low is not None and ai_analysis.entry_zone_high is not None:
        zone = f"{ai_analysis.entry_zone_low:.5f} - {ai_analysis.entry_zone_high:.5f}"

    return [
        "## DeepSeek AI Route",
        "",
        "This section is the independent AI route output. It does not approve, reject, or merge with the rule-engine route.",
        "",
        f"- AI side: {ai_analysis.side}",
        f"- AI status: {ai_analysis.status}",
        f"- AI entry zone: {zone}",
        f"- AI confidence: {ai_analysis.confidence}",
        f"- AI next action: {ai_analysis.next_action}",
        f"- AI alert: {alert or 'none'}",
        "",
        "### AI Reasoning",
        "",
        ai_analysis.reasoning or "No AI reasoning returned.",
        "",
        "### AI Chart Notes",
        "",
        ai_analysis.chart_notes or "No AI chart notes returned.",
        "",
        "## Rule Engine Route",
        "",
    ]
