from dataclasses import dataclass
from datetime import datetime

from fx_annotation.candles import Candle
from fx_annotation.poi import ZoneLadderItem
from fx_annotation.structure import detect_swings


@dataclass(frozen=True)
class TradeTarget:
    price: float
    side: str
    timeframe: str
    swing_kind: str
    candle_time: datetime
    index: int
    reason: str


def select_trade_target(
    side: str,
    entry_price: float,
    h1_candles: list[Candle],
    h4_candles: list[Candle],
    active_from_time: datetime | None = None,
    risk: float | None = None,
    minimum_r: float = 3.0,
    zones: list[ZoneLadderItem] | None = None,
    fixed_rr: float | None = None,
) -> TradeTarget | None:
    candidates: list[TradeTarget] = []
    candidates.extend(_opposing_zone_targets(side, entry_price, zones or []))
    candidates.extend(_targets_from_candles(side, entry_price, h1_candles, "H1", active_from_time))
    candidates.extend(_targets_from_candles(side, entry_price, h4_candles, "H4", active_from_time))
    if risk is not None and risk > 0 and fixed_rr is not None and fixed_rr > 0:
        candidates.append(_fixed_rr_target(side, entry_price, risk, fixed_rr))
    if not candidates:
        return None
    if risk is not None and risk > 0:
        qualified = [target for target in candidates if available_r(target, entry_price, risk) is not None and available_r(target, entry_price, risk) >= minimum_r]
        if qualified:
            return sorted(qualified, key=lambda target: abs(target.price - entry_price))[0]
    return sorted(candidates, key=lambda target: abs(target.price - entry_price))[0]


def available_r(target: TradeTarget | None, entry_price: float, risk: float) -> float | None:
    if target is None or risk <= 0:
        return None
    return abs(target.price - entry_price) / risk


def target_snapshot(target: TradeTarget | None, entry_price: float, risk: float) -> dict[str, object] | None:
    if target is None:
        return None
    return {
        "price": target.price,
        "side": target.side,
        "timeframe": target.timeframe,
        "swing_kind": target.swing_kind,
        "candle_time": target.candle_time.isoformat(),
        "index": target.index,
        "reason": target.reason,
        "available_r": available_r(target, entry_price, risk),
    }


def _targets_from_candles(
    side: str,
    entry_price: float,
    candles: list[Candle],
    timeframe: str,
    active_from_time: datetime | None,
) -> list[TradeTarget]:
    swings = detect_swings(candles, window=2)
    if active_from_time is not None:
        swings = [swing for swing in swings if candles[swing.index].time >= active_from_time]
    if side.upper() == "BUY":
        candidates = [swing for swing in swings if swing.kind == "high" and swing.price > entry_price]
        swing_kind = "last_swing_high"
        reason = "Weak swing high above entry; buy-side liquidity target."
    elif side.upper() == "SELL":
        candidates = [swing for swing in swings if swing.kind == "low" and swing.price < entry_price]
        swing_kind = "last_swing_low"
        reason = "Weak swing low below entry; sell-side liquidity target."
    else:
        return []
    return [
        TradeTarget(
            price=swing.price,
            side=side.upper(),
            timeframe=timeframe,
            swing_kind=swing_kind,
            candle_time=candles[swing.index].time,
            index=swing.index,
            reason=reason,
        )
        for swing in candidates
    ]


def _opposing_zone_targets(
    side: str,
    entry_price: float,
    zones: list[ZoneLadderItem],
) -> list[TradeTarget]:
    targets: list[TradeTarget] = []
    if side.upper() == "BUY":
        for zone in zones:
            if zone.side == "supply" and zone.low > entry_price and zone.state != "failed":
                targets.append(
                    TradeTarget(
                        price=zone.low,
                        side="BUY",
                        timeframe=zone.timeframe,
                        swing_kind="opposing_supply_zone",
                        candle_time=zone.candle_time,
                        index=zone.index,
                        reason="Nearest opposing supply zone above entry; dynamic take-profit objective.",
                    )
                )
    elif side.upper() == "SELL":
        for zone in zones:
            if zone.side == "demand" and zone.high < entry_price and zone.state != "failed":
                targets.append(
                    TradeTarget(
                        price=zone.high,
                        side="SELL",
                        timeframe=zone.timeframe,
                        swing_kind="opposing_demand_zone",
                        candle_time=zone.candle_time,
                        index=zone.index,
                        reason="Nearest opposing demand zone below entry; dynamic take-profit objective.",
                    )
                )
    return targets


def _fixed_rr_target(side: str, entry_price: float, risk: float, rr: float) -> TradeTarget:
    direction = side.upper()
    price = entry_price + risk * rr if direction == "BUY" else entry_price - risk * rr
    return TradeTarget(
        price=price,
        side=direction,
        timeframe="R",
        swing_kind=f"fixed_{rr:g}r",
        candle_time=datetime.min,
        index=-1,
        reason=f"Fixed {rr:g}R set-and-forget target from the mechanical trade plan.",
    )
