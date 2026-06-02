from pathlib import Path

from fx_annotation.bias import Bias, detect_bias
from fx_annotation.candles import Candle
from fx_annotation.chart_image_renderer import render_chart_image
from fx_annotation.narrative import build_htf_narrative
from fx_annotation.oanda_client import OandaClient
from fx_annotation.poi import detect_timeframe_zones, detect_zone_ladder
from fx_annotation.setups import find_recent_setups
from fx_annotation.structure import detect_swings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def render_instrument_chart_pack(
    client: OandaClient,
    instrument: str,
    output_root: Path | None = None,
    bias_granularity: str = "H4",
    refinement_granularity: str = "H1",
    entry_granularity: str = "M15",
) -> list[Path]:
    output_dir = (output_root or PROJECT_ROOT / "outputs" / "charts") / instrument

    h4_candles = _completed(client.fetch_candles(instrument, bias_granularity, count=300))
    h1_candles = _completed(client.fetch_candles(instrument, refinement_granularity, count=300))
    m15_candles = _completed(client.fetch_candles(instrument, entry_granularity, count=420))

    latest_price = m15_candles[-1].close if m15_candles else h4_candles[-1].close
    h4_bias = detect_bias(h4_candles)
    h1_bias = detect_bias(h1_candles)
    bias = _effective_bias(h4_bias, h1_bias, refinement_granularity)
    uses_refinement = _uses_refinement_bias(h4_bias, h1_bias)
    narrative_candles = h1_candles if uses_refinement else h4_candles
    narrative_timeframe = refinement_granularity if uses_refinement else bias_granularity
    preliminary_narrative = build_htf_narrative(
        candles=narrative_candles,
        zones=[],
        direction=bias.direction,
        timeframe=narrative_timeframe,
    )
    active_from_time = preliminary_narrative.active_from_time if preliminary_narrative else None
    ladder = detect_zone_ladder(
        h4_candles=h4_candles,
        h1_candles=h1_candles,
        current_price=latest_price,
        bias_direction=bias.direction,
        active_from_time=active_from_time,
    )
    narrative = build_htf_narrative(
        candles=narrative_candles,
        zones=ladder,
        direction=bias.direction,
        timeframe=narrative_timeframe,
    )
    m15_setups, m15_swings, _m15_sweeps = find_recent_setups(m15_candles, bias, limit=3)
    m15_setups = [
        setup
        for setup in m15_setups
        if setup.status in {"candidate", "expired"} and setup.entry_zone.source != "50-70 percent impulse retracement"
    ]
    m15_zones = detect_timeframe_zones(
        candles=m15_candles,
        current_price=latest_price,
        timeframe=entry_granularity,
        bias_direction=bias.direction,
        limit=8,
        active_from_time=active_from_time,
    )

    paths = [
        output_dir / f"{bias_granularity}.png",
        output_dir / f"{refinement_granularity}.png",
        output_dir / f"{entry_granularity}.png",
    ]
    render_chart_image(
        h4_candles,
        paths[0],
        title=f"{instrument} {bias_granularity} narrative | bias={h4_bias.direction}",
        zones=[zone for zone in ladder if zone.timeframe == "H4"],
        swings=detect_swings(h4_candles),
        narrative=None if uses_refinement else narrative,
        lookback=300,
    )
    render_chart_image(
        h1_candles,
        paths[1],
        title=f"{instrument} {refinement_granularity} refinement | bias={h1_bias.direction} | effective={bias.direction}",
        zones=[zone for zone in ladder if zone.timeframe == "H1"],
        swings=detect_swings(h1_candles),
        narrative=narrative if uses_refinement else None,
        lookback=300,
    )
    render_chart_image(
        m15_candles,
        paths[2],
        title=f"{instrument} {entry_granularity} execution confirmation",
        zones=m15_zones,
        swings=m15_swings,
        setups=m15_setups,
        lookback=180,
    )
    return paths


def _completed(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.complete]


def _effective_bias(h4_bias: Bias, h1_bias: Bias, refinement_granularity: str) -> Bias:
    if h4_bias.direction != "neutral":
        return h4_bias
    if h1_bias.direction == "neutral":
        return h4_bias
    return Bias(
        direction=h1_bias.direction,
        reason=f"H4 is neutral/noisy, so using {refinement_granularity} refinement: {h1_bias.reason}",
    )


def _uses_refinement_bias(h4_bias: Bias, h1_bias: Bias) -> bool:
    return h4_bias.direction == "neutral" and h1_bias.direction != "neutral"
