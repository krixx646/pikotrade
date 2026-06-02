import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.bias import Bias, detect_bias
from fx_annotation.candles import Candle
from fx_annotation.config import load_oanda_config
from fx_annotation.narrative import HtfNarrative, build_htf_narrative
from fx_annotation.oanda_client import OandaClient
from fx_annotation.poi import ZoneLadderItem, detect_htf_pois, detect_zone_ladder, nearest_relevant_poi
from fx_annotation.setups import SetupCandidate, find_recent_setups
from fx_annotation.structure import SwingPoint, detect_swings


TARGET_KEYS = {
    "Rule:BTC_USD:BUY:81181.50000-81419.50000",
    "Rule:EUR_JPY:BUY:184.44400-185.46200",
    "Rule:EUR_JPY:BUY:184.67800-184.76800",
    "Rule:USD_JPY:BUY:156.72600-157.74700",
}


@dataclass(frozen=True)
class AuditContext:
    h4_candles: list[Candle]
    h1_candles: list[Candle]
    m15_candles: list[Candle]
    h4_bias: Bias
    h1_bias: Bias
    effective_bias: Bias
    narrative: HtfNarrative | None
    zone_ladder: list[ZoneLadderItem]
    setup: SetupCandidate | None
    swings: list[SwingPoint]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render full decision-audit images for forward tests.")
    parser.add_argument("--tests", default=str(PROJECT_ROOT / "outputs" / "forward_tests.json"))
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "outputs" / "reviews" / "forward_test_audits"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tests = _load_json(Path(args.tests))
    client = OandaClient(load_oanda_config())
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[Path] = []
    for key in sorted(TARGET_KEYS):
        test = tests.get(key)
        if not isinstance(test, dict):
            continue
        signal_time = _parse_time(str(test.get("signal_time") or test.get("created_at") or ""))
        if signal_time is None:
            continue
        context = _build_context(client, str(test.get("instrument", "")), signal_time, test)
        output = output_dir / f"{_safe_name(key)}_audit.png"
        render_audit_image(key, test, context, output)
        rendered.append(output)

    for path in rendered:
        print(path)
    return 0


def _build_context(
    client: OandaClient,
    instrument: str,
    signal_time: datetime,
    test: dict[str, object],
) -> AuditContext:
    h4 = _history_until(client, instrument, "H4", signal_time, 500)
    h1 = _history_until(client, instrument, "H1", signal_time, 500)
    m15 = _history_until(client, instrument, "M15", signal_time + timedelta(days=2), 500)
    decision_m15 = [candle for candle in m15 if candle.time <= signal_time]

    h4_bias = detect_bias(h4)
    h1_bias = detect_bias(h1)
    effective_bias = h4_bias if h4_bias.direction != "neutral" else h1_bias
    latest_price = decision_m15[-1].close if decision_m15 else (h1[-1].close if h1 else 0.0)
    preliminary = build_htf_narrative(
        h1 if h4_bias.direction == "neutral" and h1_bias.direction != "neutral" else h4,
        zones=[],
        direction=effective_bias.direction,
        timeframe="H1" if h4_bias.direction == "neutral" and h1_bias.direction != "neutral" else "H4",
    )
    active_from = preliminary.active_from_time if preliminary else None
    zone_ladder = detect_zone_ladder(
        h4_candles=h4,
        h1_candles=h1,
        current_price=latest_price,
        bias_direction=effective_bias.direction,
        active_from_time=active_from,
    )
    narrative = build_htf_narrative(
        h1 if h4_bias.direction == "neutral" and h1_bias.direction != "neutral" else h4,
        zones=zone_ladder,
        direction=effective_bias.direction,
        timeframe="H1" if h4_bias.direction == "neutral" and h1_bias.direction != "neutral" else "H4",
    )

    pois = detect_htf_pois(
        h1 if h4_bias.direction == "neutral" and h1_bias.direction != "neutral" else h4,
        latest_price,
        active_from_time=active_from,
        bias_direction=effective_bias.direction,
    )
    setups, swings, _sweeps = find_recent_setups(decision_m15, effective_bias, limit=50)
    setup = _match_setup(test, setups, decision_m15)
    if setup is not None:
        _ = nearest_relevant_poi(pois, setup.side, latest_price)

    return AuditContext(
        h4_candles=h4,
        h1_candles=h1,
        m15_candles=m15,
        h4_bias=h4_bias,
        h1_bias=h1_bias,
        effective_bias=effective_bias,
        narrative=narrative,
        zone_ladder=zone_ladder,
        setup=setup,
        swings=swings,
    )


def render_audit_image(
    key: str,
    test: dict[str, object],
    context: AuditContext,
    output: Path,
    width: int = 1800,
    height: int = 1320,
) -> None:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(16)
    small = _font(13)
    tiny = _font(11)
    title_font = _font(24)
    bold = _font(18)

    chart_left = 64
    chart_right = 1220
    text_left = 1260
    top = 78
    panel_h = 330
    gap = 38

    title = f"{test.get('instrument')} {test.get('route')} {test.get('side')} decision audit"
    draw.text((chart_left, 24), title, fill=(0, 0, 0, 255), font=title_font)
    draw.text((text_left, 28), "Decision explanation", fill=(0, 0, 0, 255), font=title_font)

    _draw_chart_panel(
        draw,
        "H4 narrative at signal time",
        context.h4_candles,
        chart_left,
        top,
        chart_right,
        top + panel_h,
        zones=[],
        narrative=context.narrative if context.narrative and context.narrative.timeframe == "H4" else None,
        setup=None,
        test=None,
        font=small,
        tiny=tiny,
        lookback=90,
    )
    _draw_chart_panel(
        draw,
        "H1 refinement / zone ladder at signal time",
        context.h1_candles,
        chart_left,
        top + panel_h + gap,
        chart_right,
        top + panel_h * 2 + gap,
        zones=context.zone_ladder,
        narrative=context.narrative if context.narrative and context.narrative.timeframe == "H1" else None,
        setup=None,
        test=None,
        font=small,
        tiny=tiny,
        lookback=130,
    )
    _draw_chart_panel(
        draw,
        "M15 execution and forward-test result",
        context.m15_candles,
        chart_left,
        top + (panel_h + gap) * 2,
        chart_right,
        top + (panel_h + gap) * 2 + panel_h,
        zones=[],
        narrative=None,
        setup=context.setup,
        test=test,
        font=small,
        tiny=tiny,
        lookback=180,
    )

    y = 78
    for line, color, size in _explanation_lines(key, test, context):
        wrapped = _wrap(line, 58)
        for wrapped_line in wrapped:
            active_font = bold if size == "bold" else font
            draw.text((text_left, y), wrapped_line, fill=color, font=active_font)
            y += 24 if size == "bold" else 21
        y += 8

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def _draw_chart_panel(
    draw: ImageDraw.ImageDraw,
    title: str,
    candles: list[Candle],
    left: int,
    top: int,
    right: int,
    bottom: int,
    zones: list[ZoneLadderItem],
    narrative: HtfNarrative | None,
    setup: SetupCandidate | None,
    test: dict[str, object] | None,
    font: ImageFont.ImageFont,
    tiny: ImageFont.ImageFont,
    lookback: int,
) -> None:
    visible = candles[-lookback:] if len(candles) > lookback else candles
    if not visible:
        return
    offset = len(candles) - len(visible)
    min_price = min(candle.low for candle in visible)
    max_price = max(candle.high for candle in visible)
    for zone in zones[:8]:
        if zone.index >= offset:
            min_price = min(min_price, zone.low)
            max_price = max(max_price, zone.high)
    if setup is not None:
        min_price = min(min_price, setup.entry_zone.low, setup.sweep.swept_price, setup.bos.broken_price)
        max_price = max(max_price, setup.entry_zone.high, setup.sweep.swept_price, setup.bos.broken_price)
    if test is not None:
        target_price, _status = _target_info(test)
        for value in (
            _float(test.get("entry_low")),
            _float(test.get("entry_high")),
            _float(test.get("entry_price")),
            _float(test.get("stop_loss")),
            _float(test.get("trade_target_price")),
            _float(test.get("final_target_price")),
            target_price,
        ):
            if value is not None:
                min_price = min(min_price, value)
                max_price = max(max_price, value)
    pad = (max_price - min_price) * 0.08 or max_price * 0.001
    min_price -= pad
    max_price += pad

    draw.rectangle((left, top, right, bottom), outline=(180, 180, 180, 255), width=1)
    draw.text((left + 8, top + 8), title, fill=(0, 0, 0, 255), font=font)
    plot_top = top + 36
    plot_bottom = bottom - 24
    _draw_grid(draw, left, plot_top, right, plot_bottom)
    _draw_candles(draw, visible, left, right, plot_top, plot_bottom, min_price, max_price)
    _draw_sma(draw, visible, 200, left, right, plot_top, plot_bottom, min_price, max_price)

    for zone in zones[:8]:
        if zone.state == "failed":
            continue
        _draw_zone(draw, zone.low, zone.high, f"{zone.timeframe} {zone.side} {zone.state}", left, right, plot_top, plot_bottom, min_price, max_price, tiny)

    if narrative is not None:
        _draw_price_line(draw, narrative.highest_high, "HH", left, right, plot_top, plot_bottom, min_price, max_price, (210, 55, 55, 210), tiny)
        _draw_price_line(draw, narrative.lowest_low, "LL", left, right, plot_top, plot_bottom, min_price, max_price, (20, 140, 80, 210), tiny)

    if setup is not None:
        _draw_zone(draw, setup.entry_zone.low, setup.entry_zone.high, "M15 base that caused BOS", left, right, plot_top, plot_bottom, min_price, max_price, tiny)
        _draw_event(draw, candles, visible, setup.sweep.index, setup.sweep.swept_price, "sweep", left, right, plot_top, plot_bottom, min_price, max_price, (30, 110, 220, 230), tiny)
        _draw_event(draw, candles, visible, setup.bos.index, setup.bos.broken_price, "BOS", left, right, plot_top, plot_bottom, min_price, max_price, (120, 50, 170, 230), tiny)

    if test is not None:
        target_price, target_status = _target_info(test)
        _draw_price_line(draw, _float(test.get("entry_price")), "entry", left, right, plot_top, plot_bottom, min_price, max_price, (30, 110, 220, 235), tiny)
        _draw_price_line(draw, _float(test.get("stop_loss")), "SL", left, right, plot_top, plot_bottom, min_price, max_price, (210, 40, 40, 235), tiny)
        trade_target = _float(test.get("trade_target_price")) or _float(test.get("final_target_price")) or target_price
        target_label = f"{test.get('trade_target_timeframe') or 'target'} {target_status}"
        _draw_price_line(draw, trade_target, target_label, left, right, plot_top, plot_bottom, min_price, max_price, (20, 150, 80, 235), tiny)
        entry_time = _parse_time(str(test.get("entry_time") or ""))
        exit_time = _parse_time(str(test.get("exit_time") or test.get("last_checked_at") or ""))
        if entry_time:
            _draw_time_marker(draw, visible, entry_time, "entry time", left, right, plot_top, plot_bottom, (30, 110, 220, 200), tiny)
        if exit_time:
            _draw_time_marker(draw, visible, exit_time, "exit/check", left, right, plot_top, plot_bottom, (120, 50, 170, 200), tiny)


def _explanation_lines(
    key: str,
    test: dict[str, object],
    context: AuditContext,
) -> list[tuple[str, tuple[int, int, int, int], str]]:
    target_price, target_status = _target_info(test)
    trade_target = _float(test.get("trade_target_price")) or _float(test.get("final_target_price")) or target_price
    metrics = _trade_metrics(test, context.m15_candles, trade_target)
    signal_time = _parse_time(str(test.get("signal_time") or ""))
    bos_time = _parse_time(str(test.get("bos_time") or ""))
    bos_age = ""
    if signal_time and bos_time:
        bos_age = f"{(signal_time - bos_time).total_seconds() / 3600:.1f}h"
    setup = context.setup
    red = (190, 35, 35, 255)
    green = (20, 130, 70, 255)
    black = (20, 20, 20, 255)
    orange = (170, 95, 0, 255)
    blue = (25, 90, 180, 255)

    lines: list[tuple[str, tuple[int, int, int, int], str]] = [
        ("Stored decision facts", black, "bold"),
        ("Audit note: forward_tests.json did not store the full original confluence snapshot; H4/H1/M15 context below is reconstructed from OANDA candles using current code.", orange, "normal"),
        (f"Key: {key}", black, "normal"),
        (f"Route: {test.get('route')} route, side {test.get('side')}, status opened from stored forward-test record.", black, "normal"),
        (f"Source stored: {test.get('source')}.", black, "normal"),
        (f"Notes stored: {test.get('notes')}", black, "normal"),
        ("Recomputed confluence at signal time", black, "bold"),
        (f"H4 bias: {context.h4_bias.direction}. {context.h4_bias.reason}", blue, "normal"),
        (f"H1 bias: {context.h1_bias.direction}. {context.h1_bias.reason}", blue, "normal"),
        (f"Effective bias used for audit: {context.effective_bias.direction}.", blue, "normal"),
    ]
    stored_side = str(test.get("side", "")).upper()
    allowed_side = "BUY" if context.effective_bias.direction == "bullish" else "SELL" if context.effective_bias.direction == "bearish" else ""
    if allowed_side and stored_side != allowed_side:
        lines.append(
            (
                f"Direction conflict: stored trade was {stored_side}, but current reconstructed effective bias allows {allowed_side}. Current logic would block/question this entry.",
                red,
                "normal",
            )
        )
    if context.narrative is not None:
        lines.append((f"Active story: {context.narrative.summary}", blue, "normal"))
    if context.zone_ladder:
        first = context.zone_ladder[0]
        lines.append((f"Nearest ladder zone: {first.timeframe} {first.side} {first.low:g}-{first.high:g}, state {first.state}.", blue, "normal"))
    if setup is None:
        lines.append(("M15 setup could not be reconstructed exactly from the current candle fetch; only stored test facts are certain.", red, "normal"))
    else:
        sweep_time = context.m15_candles[setup.sweep.index].time.isoformat()
        bos_time_text = context.m15_candles[setup.bos.index].time.isoformat()
        lines.extend(
            [
                (f"M15 sweep: {setup.sweep.kind} at {sweep_time}, swept price {setup.sweep.swept_price:g}.", green, "normal"),
                (f"M15 BOS: {setup.bos.direction} at {bos_time_text}, broke {setup.bos.broken_price:g}.", green, "normal"),
                (f"M15 entry base: {setup.entry_zone.low:g}-{setup.entry_zone.high:g}; state {setup.current_state}; quality {setup.quality_score}/3.", green if setup.quality_score >= 3 else orange, "normal"),
                ("Quality notes: " + " ".join(setup.quality_notes), green if setup.quality_score >= 3 else orange, "normal"),
            ]
        )
    lines.extend(
        [
            ("Forward-test result", black, "bold"),
            (f"Entry TF {test.get('entry_timeframe') or 'M15'}; entry {test.get('entry_price')}, SL {test.get('stop_loss')}.", black, "normal"),
            (f"HTF/H1 target {trade_target:g} from {test.get('trade_target_timeframe') or 'stored target'}; available R {test.get('available_r')}.", green if _float(test.get("available_r")) and _float(test.get("available_r")) >= 3 else red, "normal"),
            (f"Target reason: {test.get('trade_target_reason') or test.get('target_reason') or 'not stored'}. Result {target_status}.", black, "normal"),
            (f"Max favourable move before exit: {metrics['mfe_r']:.2f}R. Closest distance to TP: {metrics['tp_gap_r']:.2f}R.", green if metrics["mfe_r"] >= 2 else orange, "normal"),
            (f"Max adverse move before exit: {metrics['mae_r']:.2f}R. Duration: {metrics['hours_open']:.1f}h.", red if metrics["mae_r"] >= 1 else black, "normal"),
        ]
    )
    if bos_age:
        color = red if float(bos_age[:-1]) > 12 else black
        lines.append((f"BOS age at signal: {bos_age}. Current safety rule rejects entries older than 12h.", color, "normal"))
    if target_status == "timeout":
        lines.append(("Interpretation: price did not nearly hit 3R in this window; it ranged and timed out before TP/SL.", orange, "normal"))
    if target_status == "sl_hit":
        lines.append(("Interpretation: price invalidated the SL before reaching 3R.", red, "normal"))
    return lines


def _trade_metrics(test: dict[str, object], candles: list[Candle], target_price: float) -> dict[str, float]:
    side = str(test.get("side", "")).upper()
    entry = float(test.get("entry_price", 0.0))
    risk = abs(entry - float(test.get("stop_loss", entry))) or 1.0
    entry_time = _parse_time(str(test.get("entry_time") or test.get("created_at") or ""))
    exit_time = _parse_time(str(test.get("exit_time") or test.get("last_checked_at") or ""))
    sample = [
        candle
        for candle in candles
        if (entry_time is None or candle.time >= entry_time)
        and (exit_time is None or candle.time <= exit_time)
    ]
    if not sample:
        sample = candles
    if side == "BUY":
        favourable = max(candle.high for candle in sample) - entry
        adverse = entry - min(candle.low for candle in sample)
        tp_gap = max(0.0, target_price - max(candle.high for candle in sample))
    else:
        favourable = entry - min(candle.low for candle in sample)
        adverse = max(candle.high for candle in sample) - entry
        tp_gap = max(0.0, min(candle.low for candle in sample) - target_price)
    hours_open = 0.0
    if entry_time and exit_time:
        hours_open = (exit_time - entry_time).total_seconds() / 3600
    return {
        "mfe_r": favourable / risk,
        "mae_r": adverse / risk,
        "tp_gap_r": tp_gap / risk,
        "hours_open": hours_open,
    }


def _history_until(
    client: OandaClient,
    instrument: str,
    granularity: str,
    end_time: datetime,
    count: int,
) -> list[Candle]:
    candles = [candle for candle in client.fetch_candles(instrument, granularity, count=count) if candle.complete]
    return [candle for candle in candles if candle.time <= end_time]


def _match_setup(
    test: dict[str, object],
    setups: list[SetupCandidate],
    candles: list[Candle],
) -> SetupCandidate | None:
    bos_time = _parse_time(str(test.get("bos_time") or ""))
    side = str(test.get("side", "")).lower()
    entry_low = float(test.get("entry_low", 0.0))
    entry_high = float(test.get("entry_high", 0.0))
    best: tuple[int, SetupCandidate] | None = None
    for setup in setups:
        if setup.side != side:
            continue
        score = 0
        if bos_time is not None and 0 <= setup.bos.index < len(candles):
            delta = abs((candles[setup.bos.index].time - bos_time).total_seconds())
            if delta <= 15 * 60:
                score += 4
            elif delta <= 60 * 60:
                score += 2
        if _zones_overlap(entry_low, entry_high, setup.entry_zone.low, setup.entry_zone.high):
            score += 3
        if best is None or score > best[0]:
            best = (score, setup)
    if best is None or best[0] <= 0:
        return None
    return best[1]


def _zones_overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _target_info(test: dict[str, object]) -> tuple[float, str]:
    stored = _float(test.get("trade_target_price")) or _float(test.get("final_target_price"))
    status = str(test.get("result") or "")
    if stored is not None:
        return stored, status
    targets = test.get("targets", {})
    if isinstance(targets, dict):
        target = targets.get("3R")
        if isinstance(target, dict):
            return float(target.get("price", 0.0)), str(target.get("status", ""))
    return 0.0, ""


def _draw_grid(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
    for step in range(6):
        y = top + (bottom - top) * step / 5
        draw.line((left, y, right, y), fill=(234, 234, 234, 255), width=1)
    for step in range(10):
        x = left + (right - left) * step / 9
        draw.line((x, top, x, bottom), fill=(240, 240, 240, 255), width=1)


def _draw_candles(
    draw: ImageDraw.ImageDraw,
    candles: list[Candle],
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
) -> None:
    slot = (right - left) / max(1, len(candles))
    body_width = max(2, int(slot * 0.55))
    for index, candle in enumerate(candles):
        x = left + slot * index + slot / 2
        high_y = _price_to_y(candle.high, min_price, max_price, top, bottom)
        low_y = _price_to_y(candle.low, min_price, max_price, top, bottom)
        open_y = _price_to_y(candle.open, min_price, max_price, top, bottom)
        close_y = _price_to_y(candle.close, min_price, max_price, top, bottom)
        color = (0, 145, 120, 255) if candle.close >= candle.open else (25, 25, 25, 255)
        draw.line((x, high_y, x, low_y), fill=color, width=1)
        y1 = min(open_y, close_y)
        y2 = max(open_y, close_y)
        if y2 - y1 < 1:
            y2 = y1 + 1
        draw.rectangle((x - body_width / 2, y1, x + body_width / 2, y2), fill=color)


def _draw_sma(
    draw: ImageDraw.ImageDraw,
    candles: list[Candle],
    period: int,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
) -> None:
    if len(candles) < period:
        return
    slot = (right - left) / max(1, len(candles))
    closes = [candle.close for candle in candles]
    points = []
    for index in range(period - 1, len(candles)):
        value = sum(closes[index - period + 1 : index + 1]) / period
        points.append((left + slot * index + slot / 2, _price_to_y(value, min_price, max_price, top, bottom)))
    if len(points) > 1:
        draw.line(points, fill=(230, 126, 34, 220), width=2)


def _draw_zone(
    draw: ImageDraw.ImageDraw,
    low: float,
    high: float,
    label: str,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    font: ImageFont.ImageFont,
) -> None:
    y1 = _price_to_y(high, min_price, max_price, top, bottom)
    y2 = _price_to_y(low, min_price, max_price, top, bottom)
    draw.rectangle((left, y1, right, y2), fill=(241, 196, 15, 42), outline=(170, 120, 0, 170), width=1)
    draw.text((left + 6, y1 + 2), f"{label} {low:g}-{high:g}", fill=(110, 80, 0, 255), font=font)


def _draw_price_line(
    draw: ImageDraw.ImageDraw,
    price: float | None,
    label: str,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    color: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    if price is None:
        return
    y = _price_to_y(price, min_price, max_price, top, bottom)
    draw.line((left, y, right, y), fill=color, width=2)
    draw.text((right - 150, y - 14), f"{label} {price:g}", fill=color, font=font)


def _draw_event(
    draw: ImageDraw.ImageDraw,
    all_candles: list[Candle],
    visible: list[Candle],
    index: int,
    price: float,
    label: str,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    color: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    offset = len(all_candles) - len(visible)
    local = index - offset
    if local < 0 or local >= len(visible):
        return
    slot = (right - left) / max(1, len(visible))
    x = left + slot * local + slot / 2
    y = _price_to_y(price, min_price, max_price, top, bottom)
    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color)
    draw.text((x + 7, y - 10), f"{label} {price:g}", fill=color, font=font)


def _draw_time_marker(
    draw: ImageDraw.ImageDraw,
    visible: list[Candle],
    event_time: datetime,
    label: str,
    left: int,
    right: int,
    top: int,
    bottom: int,
    color: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    index = 0
    for idx, candle in enumerate(visible):
        if candle.time >= event_time:
            index = idx
            break
    slot = (right - left) / max(1, len(visible))
    x = left + slot * index + slot / 2
    draw.line((x, top, x, bottom), fill=color, width=2)
    draw.text((x + 5, top + 5), label, fill=color, font=font)


def _price_to_y(price: float, min_price: float, max_price: float, top: int, bottom: int) -> float:
    return bottom - (price - min_price) / (max_price - min_price) * (bottom - top)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(item) + 1 for item in current) + len(word) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
