from dataclasses import dataclass

from fx_annotation.candles import Candle
from fx_annotation.setups import SetupCandidate
from fx_annotation.structure import average_range


@dataclass(frozen=True)
class OutcomeResult:
    rr: float
    result: str
    entry_index: int | None
    exit_index: int | None
    entry_price: float
    stop_loss: float
    take_profit: float
    risk: float
    bars_to_result: int | None
    verdict: str
    reason: str


def validate_setup_outcome(
    candles: list[Candle],
    setup: SetupCandidate,
    rr_values: tuple[float, ...] = (2.0, 3.0),
    timeout_bars: int = 48,
) -> list[OutcomeResult]:
    entry_index = _first_entry_touch_index(candles, setup)
    entry_price = (setup.entry_zone.low + setup.entry_zone.high) / 2
    stop_loss = _test_stop_loss(candles, setup)
    risk = _risk(entry_price, stop_loss)

    if entry_index is None:
        return [
            OutcomeResult(
                rr=rr,
                result="not_triggered",
                entry_index=None,
                exit_index=None,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=_take_profit(entry_price, stop_loss, setup.side, rr),
                risk=risk,
                bars_to_result=None,
                verdict="Pending",
                reason="Price did not touch the proposed entry zone after BOS.",
            )
            for rr in rr_values
        ]

    if risk <= 0:
        return [
            OutcomeResult(
                rr=rr,
                result="invalid_risk",
                entry_index=entry_index,
                exit_index=None,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=entry_price,
                risk=risk,
                bars_to_result=None,
                verdict="Needs better rule",
                reason="Test-only stop loss creates invalid risk.",
            )
            for rr in rr_values
        ]

    return [
        _validate_rr(
            candles=candles,
            setup=setup,
            rr=rr,
            entry_index=entry_index,
            entry_price=entry_price,
            stop_loss=stop_loss,
            timeout_bars=timeout_bars,
        )
        for rr in rr_values
    ]


def _validate_rr(
    candles: list[Candle],
    setup: SetupCandidate,
    rr: float,
    entry_index: int,
    entry_price: float,
    stop_loss: float,
    timeout_bars: int,
) -> OutcomeResult:
    take_profit = _take_profit(entry_price, stop_loss, setup.side, rr)
    end_index = min(len(candles), entry_index + timeout_bars + 1)

    for index in range(entry_index + 1, end_index):
        candle = candles[index]
        hit_sl = _hit_stop(candle, stop_loss, setup.side)
        hit_tp = _hit_target(candle, take_profit, setup.side)

        # If both are touched inside one candle, choose the conservative outcome.
        if hit_sl and hit_tp:
            return _result(
                rr=rr,
                result="ambiguous_sl_first",
                entry_index=entry_index,
                exit_index=index,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                verdict="Weak",
                reason="Both SL and TP were touched in one candle; counted conservatively as SL first.",
            )

        if hit_sl:
            return _result(
                rr=rr,
                result="sl_hit",
                entry_index=entry_index,
                exit_index=index,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                verdict="Wrong",
                reason="Test-only stop loss was hit before target.",
            )

        if hit_tp:
            return _result(
                rr=rr,
                result="tp_hit",
                entry_index=entry_index,
                exit_index=index,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                verdict="Good",
                reason="Test-only target was hit before stop loss.",
            )

    return _result(
        rr=rr,
        result="timeout",
        entry_index=entry_index,
        exit_index=None,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        verdict="Weak",
        reason=f"Neither SL nor TP was hit within {timeout_bars} candles.",
    )


def _first_entry_touch_index(
    candles: list[Candle],
    setup: SetupCandidate,
) -> int | None:
    for index in range(setup.bos.index + 1, len(candles)):
        candle = candles[index]
        if candle.low <= setup.entry_zone.high and candle.high >= setup.entry_zone.low:
            return index
    return None


def _test_stop_loss(candles: list[Candle], setup: SetupCandidate) -> float:
    sweep_candle = candles[setup.sweep.index]
    buffer = average_range(candles[max(0, setup.sweep.index - 30) : setup.bos.index + 1]) * 0.15

    if setup.side == "buy":
        return min(sweep_candle.low, setup.sweep.swept_price, setup.entry_zone.low) - buffer

    return max(sweep_candle.high, setup.sweep.swept_price, setup.entry_zone.high) + buffer


def _risk(entry_price: float, stop_loss: float) -> float:
    return abs(entry_price - stop_loss)


def _take_profit(entry_price: float, stop_loss: float, side: str, rr: float) -> float:
    risk = _risk(entry_price, stop_loss)
    if side == "buy":
        return entry_price + risk * rr
    return entry_price - risk * rr


def _hit_stop(candle: Candle, stop_loss: float, side: str) -> bool:
    if side == "buy":
        return candle.low <= stop_loss
    return candle.high >= stop_loss


def _hit_target(candle: Candle, take_profit: float, side: str) -> bool:
    if side == "buy":
        return candle.high >= take_profit
    return candle.low <= take_profit


def _result(
    rr: float,
    result: str,
    entry_index: int,
    exit_index: int | None,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    verdict: str,
    reason: str,
) -> OutcomeResult:
    bars_to_result = None if exit_index is None else exit_index - entry_index
    return OutcomeResult(
        rr=rr,
        result=result,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk=abs(entry_price - stop_loss),
        bars_to_result=bars_to_result,
        verdict=verdict,
        reason=reason,
    )
