from dataclasses import dataclass

from fx_annotation.candles import Candle
from fx_annotation.narrative import HtfNarrative
from fx_annotation.setups import SetupCandidate
from fx_annotation.structure import average_range, detect_swings


@dataclass(frozen=True)
class ConfluenceConfig:
    min_score: int = 5
    premium_discount_edge: float = 0.45
    require_pullback_phase: bool = True
    min_range_atr: float = 6.0
    min_sweep_atr: float = 0.15
    min_displacement_atr: float = 1.8
    min_bos_close_atr: float = 0.25
    max_zone_atr: float = 1.6
    min_target_r: float = 3.0


@dataclass(frozen=True)
class ConfluenceGrade:
    score: int
    max_score: int
    passed: bool
    reasons: tuple[str, ...]
    failures: tuple[str, ...]
    metrics: dict[str, float | str]


def grade_setup_confluence(
    setup: SetupCandidate | None,
    candles: list[Candle],
    narrative: HtfNarrative | None,
    side: str,
    target_r: float | None,
    config: ConfluenceConfig = ConfluenceConfig(),
) -> ConfluenceGrade:
    if setup is None:
        return _grade(0, ["No setup candidate."], ["no_setup"], {})
    if not candles:
        return _grade(0, ["No entry candles."], ["no_entry_candles"], {})

    reasons: list[str] = []
    failures: list[str] = []
    metrics: dict[str, float | str] = {}
    score = 0
    max_score = 8

    reference_range = average_range(candles[max(0, setup.sweep.index - 30) : setup.bos.index + 1], period=30)
    if reference_range <= 0:
        reference_range = average_range(candles, period=30)
    if reference_range <= 0:
        return _grade(0, ["Invalid reference range."], ["invalid_reference_range"], {})

    if setup.quality_score >= 2:
        score += 1
        reasons.append("Base setup passes at least two of three structure-quality filters (BOS depth, displacement, range-edge).")
    else:
        failures.append(f"base_quality_{setup.quality_score}")

    pd_position = _premium_discount_position(setup, narrative)
    if pd_position is None:
        failures.append("no_active_range")
    else:
        metrics["premium_discount_position"] = round(max(0.0, min(1.0, pd_position)), 4)
        buy_limit = max(0.0, min(0.5, config.premium_discount_edge))
        sell_limit = 1.0 - buy_limit
        if side.upper() == "BUY" and pd_position <= buy_limit:
            score += 1
            reasons.append("Buy zone is deep enough in discount.")
        elif side.upper() == "SELL" and pd_position >= sell_limit:
            score += 1
            reasons.append("Sell zone is deep enough in premium.")
        else:
            failures.append(f"weak_premium_discount_{pd_position:.2f}")

    if narrative is None:
        failures.append("no_narrative")
    else:
        metrics["phase"] = narrative.phase
        if not config.require_pullback_phase or narrative.phase == "pullback_into_range":
            score += 1
            reasons.append("Setup is in pullback phase, not chasing an extended move.")
        else:
            failures.append(f"bad_phase_{narrative.phase}")

        active_range = narrative.range_high - narrative.range_low
        range_atr = active_range / reference_range if reference_range > 0 else 0.0
        metrics["range_atr"] = round(range_atr, 4)
        if range_atr >= config.min_range_atr:
            score += 1
            reasons.append("Active range is large enough to avoid tiny chop.")
        else:
            failures.append(f"compressed_range_{range_atr:.1f}")

    sweep_strength = _sweep_strength_atr(setup, candles, reference_range)
    metrics["sweep_atr"] = round(sweep_strength, 4)
    if sweep_strength >= config.min_sweep_atr:
        score += 1
        reasons.append("Liquidity sweep is visible relative to recent range.")
    else:
        failures.append(f"weak_sweep_{sweep_strength:.2f}")

    displacement = _displacement_atr(setup, candles, reference_range)
    bos_close = _bos_close_atr(setup, candles, reference_range)
    metrics["displacement_atr"] = round(displacement, 4)
    metrics["bos_close_atr"] = round(bos_close, 4)
    if displacement >= config.min_displacement_atr and bos_close >= config.min_bos_close_atr:
        score += 1
        reasons.append("Sweep-to-BOS move shows meaningful displacement and close-through.")
    else:
        failures.append(f"weak_displacement_{displacement:.1f}_{bos_close:.2f}")

    zone_width = (setup.entry_zone.high - setup.entry_zone.low) / reference_range
    metrics["zone_width_atr"] = round(zone_width, 4)
    if setup.entry_zone.source != "50-70 percent impulse retracement" and zone_width <= config.max_zone_atr:
        score += 1
        reasons.append("Entry POI is a compact supply/demand base, not a generic retracement.")
    else:
        failures.append(f"weak_poi_{zone_width:.1f}")

    target_value = float(target_r or 0.0)
    metrics["target_r"] = round(target_value, 4)
    if target_value >= config.min_target_r:
        score += 1
        reasons.append("Target has at least 3R of clean room.")
    else:
        failures.append(f"target_r_{target_value:.1f}")

    metrics["recent_structure"] = "clean" if _structure_is_clean(setup, candles) else "mixed"

    passed = score >= config.min_score and not _critical_failure(failures)
    return ConfluenceGrade(
        score=score,
        max_score=max_score,
        passed=passed,
        reasons=tuple(reasons),
        failures=tuple(failures),
        metrics=metrics,
    )


def confluence_snapshot(grade: ConfluenceGrade | None) -> dict[str, object] | None:
    if grade is None:
        return None
    return {
        "score": grade.score,
        "max_score": grade.max_score,
        "passed": grade.passed,
        "reasons": list(grade.reasons),
        "failures": list(grade.failures),
        "metrics": grade.metrics,
    }


def _grade(
    score: int,
    reasons: list[str],
    failures: list[str],
    metrics: dict[str, float | str],
) -> ConfluenceGrade:
    return ConfluenceGrade(
        score=score,
        max_score=8,
        passed=False,
        reasons=tuple(reasons),
        failures=tuple(failures),
        metrics=metrics,
    )


def _premium_discount_position(setup: SetupCandidate, narrative: HtfNarrative | None) -> float | None:
    if narrative is None:
        return None
    active_range = narrative.range_high - narrative.range_low
    if active_range <= 0:
        return None
    zone_mid = (setup.entry_zone.low + setup.entry_zone.high) / 2
    return (zone_mid - narrative.range_low) / active_range


def _sweep_strength_atr(setup: SetupCandidate, candles: list[Candle], reference_range: float) -> float:
    if setup.sweep.index < 0 or setup.sweep.index >= len(candles) or reference_range <= 0:
        return 0.0
    candle = candles[setup.sweep.index]
    if setup.sweep.kind == "buy_side_liquidity":
        return max(0.0, candle.high - setup.sweep.swept_price) / reference_range
    return max(0.0, setup.sweep.swept_price - candle.low) / reference_range


def _displacement_atr(setup: SetupCandidate, candles: list[Candle], reference_range: float) -> float:
    if reference_range <= 0:
        return 0.0
    if not (0 <= setup.sweep.index < len(candles)) or not (0 <= setup.bos.index < len(candles)):
        return 0.0
    sweep_candle = candles[setup.sweep.index]
    bos_close = candles[setup.bos.index].close
    if setup.side == "buy":
        directional_move = bos_close - sweep_candle.low
    else:
        directional_move = sweep_candle.high - bos_close
    return max(0.0, directional_move) / reference_range


def _bos_close_atr(setup: SetupCandidate, candles: list[Candle], reference_range: float) -> float:
    if setup.bos.index < 0 or setup.bos.index >= len(candles) or reference_range <= 0:
        return 0.0
    candle = candles[setup.bos.index]
    return abs(candle.close - setup.bos.broken_price) / reference_range


def _structure_is_clean(setup: SetupCandidate, candles: list[Candle]) -> bool:
    swings = [swing for swing in detect_swings(candles, window=2) if swing.index <= setup.bos.index]
    highs = [swing for swing in swings if swing.kind == "high"]
    lows = [swing for swing in swings if swing.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return True
    if setup.side == "buy":
        return highs[-1].price >= highs[-2].price or lows[-1].price >= lows[-2].price
    return highs[-1].price <= highs[-2].price or lows[-1].price <= lows[-2].price


def _critical_failure(failures: list[str]) -> bool:
    return any(
        failure.startswith(prefix)
        for failure in failures
        for prefix in (
            "no_setup",
            "no_entry_candles",
            "invalid_reference_range",
            "no_active_range",
            "no_narrative",
            "target_r_",
            "weak_poi_",
            "weak_premium_discount_",
            "bad_phase_",
            "compressed_range_",
        )
    )
