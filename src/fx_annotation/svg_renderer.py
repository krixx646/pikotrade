from html import escape
import re
from typing import Callable

from fx_annotation.ai_strategy import AiStrategyAnalysis
from fx_annotation.candles import Candle
from fx_annotation.setups import SetupCandidate
from fx_annotation.structure import SwingPoint


def render_setup_svg(
    candles: list[Candle],
    swings: list[SwingPoint],
    setup: SetupCandidate | None,
    title: str,
    ai_analysis: AiStrategyAnalysis | None = None,
    max_candles: int = 140,
) -> str:
    visible = candles[-max_candles:]
    offset = len(candles) - len(visible)
    visible_swings = [swing for swing in swings if swing.index >= offset]

    width = 1200
    height = 720
    margin_left = 70
    margin_right = 40
    margin_top = 70
    margin_bottom = 70
    chart_width = width - margin_left - margin_right
    chart_height = height - margin_top - margin_bottom

    highs = [candle.high for candle in visible]
    lows = [candle.low for candle in visible]
    if setup is not None:
        highs.extend([setup.entry_zone.high, setup.sweep.swept_price, setup.bos.broken_price])
        lows.extend([setup.entry_zone.low, setup.sweep.swept_price, setup.bos.broken_price])
    if (
        ai_analysis is not None
        and ai_analysis.entry_zone_low is not None
        and ai_analysis.entry_zone_high is not None
    ):
        highs.append(ai_analysis.entry_zone_high)
        lows.append(ai_analysis.entry_zone_low)
    if ai_analysis is not None:
        for low, high, _label in _ai_note_ranges(ai_analysis.chart_notes):
            highs.append(high)
            lows.append(low)
        for price, _label in _ai_note_levels(ai_analysis.chart_notes):
            highs.append(price)
            lows.append(price)

    price_high = max(highs)
    price_low = min(lows)
    padding = (price_high - price_low) * 0.08 or 0.0001
    price_high += padding
    price_low -= padding

    def x_at(index: int) -> float:
        local_index = index - offset
        if len(visible) == 1:
            return margin_left + chart_width / 2
        return margin_left + (local_index / (len(visible) - 1)) * chart_width

    def y_at(price: float) -> float:
        return margin_top + ((price_high - price) / (price_high - price_low)) * chart_height

    body_width = max(3, chart_width / max(1, len(visible)) * 0.55)
    parts = [
        _svg_header(width, height),
        f'<text x="{margin_left}" y="35" font-size="22" font-weight="700">{escape(title)}</text>',
        _text(margin_left, 58, _subtitle(setup)),
        f'<rect x="{margin_left}" y="{margin_top}" width="{chart_width}" height="{chart_height}" fill="#ffffff" stroke="#d0d7de"/>',
    ]

    for grid_index in range(6):
        ratio = grid_index / 5
        price = price_high - (price_high - price_low) * ratio
        y = margin_top + chart_height * ratio
        parts.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + chart_width}" y2="{y:.2f}" stroke="#eef2f6"/>'
        )
        parts.append(_text(8, y + 4, f"{price:.5f}", size=12))

    if setup is not None:
        parts.extend(_setup_annotations(setup, x_at, y_at, margin_left, chart_width))
    if ai_analysis is not None:
        parts.extend(_ai_annotations(ai_analysis, y_at, margin_left, chart_width))

    for local_index, candle in enumerate(visible):
        index = offset + local_index
        x = x_at(index)
        open_y = y_at(candle.open)
        close_y = y_at(candle.close)
        high_y = y_at(candle.high)
        low_y = y_at(candle.low)
        color = "#1a7f37" if candle.close >= candle.open else "#d1242f"
        body_top = min(open_y, close_y)
        body_height = max(1, abs(open_y - close_y))

        parts.append(
            f'<line x1="{x:.2f}" y1="{high_y:.2f}" x2="{x:.2f}" y2="{low_y:.2f}" stroke="{color}" stroke-width="1"/>'
        )
        parts.append(
            f'<rect x="{x - body_width / 2:.2f}" y="{body_top:.2f}" width="{body_width:.2f}" height="{body_height:.2f}" fill="{color}" opacity="0.85"/>'
        )

    for swing in visible_swings:
        x = x_at(swing.index)
        y = y_at(swing.price)
        marker = "H" if swing.kind == "high" else "L"
        parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#0969da"/>'
        )
        parts.append(_text(x + 6, y - 6, marker, size=11))

    parts.append("</svg>")
    return "\n".join(parts)


def _setup_annotations(
    setup: SetupCandidate,
    x_at: Callable[[int], float],
    y_at: Callable[[float], float],
    margin_left: int,
    chart_width: int,
) -> list[str]:
    zone_y_top = y_at(setup.entry_zone.high)
    zone_y_bottom = y_at(setup.entry_zone.low)
    zone_color = "#1f883d" if setup.side == "buy" else "#cf222e"
    label = "BUY ENTRY" if setup.side == "buy" else "SELL ENTRY"
    sweep_x = x_at(setup.sweep.index)
    sweep_y = y_at(setup.sweep.swept_price)
    bos_x = x_at(setup.bos.index)
    bos_y = y_at(setup.bos.broken_price)

    return [
        f'<rect x="{margin_left}" y="{zone_y_top:.2f}" width="{chart_width}" height="{max(2, zone_y_bottom - zone_y_top):.2f}" fill="{zone_color}" opacity="0.14" stroke="{zone_color}" stroke-dasharray="6 4"/>',
        _text(margin_left + 10, zone_y_top - 8, f"{label} zone: {setup.entry_zone.low:.5f} - {setup.entry_zone.high:.5f}", color=zone_color, weight="700"),
        f'<line x1="{margin_left}" y1="{sweep_y:.2f}" x2="{margin_left + chart_width}" y2="{sweep_y:.2f}" stroke="#8250df" stroke-dasharray="4 4"/>',
        _text(sweep_x + 8, sweep_y - 8, f"Liquidity sweep {setup.sweep.swept_price:.5f}", color="#8250df", weight="700"),
        f'<line x1="{margin_left}" y1="{bos_y:.2f}" x2="{margin_left + chart_width}" y2="{bos_y:.2f}" stroke="#bf8700" stroke-dasharray="4 4"/>',
        _text(bos_x + 8, bos_y + 18, f"BOS {setup.bos.broken_price:.5f}", color="#9a6700", weight="700"),
    ]


def _ai_annotations(
    analysis: AiStrategyAnalysis,
    y_at: Callable[[float], float],
    margin_left: int,
    chart_width: int,
) -> list[str]:
    parts: list[str] = []
    if analysis.entry_zone_low is None or analysis.entry_zone_high is None:
        parts.append(
            _text(
                margin_left + 10,
                95,
                f"AI route: {analysis.side} {analysis.status} - no AI entry zone",
                color="#0969da",
                weight="700",
            )
        )
    else:
        low = min(analysis.entry_zone_low, analysis.entry_zone_high)
        high = max(analysis.entry_zone_low, analysis.entry_zone_high)
        zone_y_top = y_at(high)
        zone_y_bottom = y_at(low)
        label = f"AI {analysis.side} {analysis.status}: {low:.5f} - {high:.5f}"
        parts.extend(
            [
                f'<rect x="{margin_left}" y="{zone_y_top:.2f}" width="{chart_width}" height="{max(2, zone_y_bottom - zone_y_top):.2f}" fill="#0969da" opacity="0.10" stroke="#0969da" stroke-width="2"/>',
                _text(margin_left + 10, zone_y_bottom + 18, label, color="#0969da", weight="700"),
            ]
        )

    for low, high, label in _ai_note_ranges(analysis.chart_notes):
        zone_y_top = y_at(high)
        zone_y_bottom = y_at(low)
        parts.extend(
            [
                f'<rect x="{margin_left}" y="{zone_y_top:.2f}" width="{chart_width}" height="{max(2, zone_y_bottom - zone_y_top):.2f}" fill="#0969da" opacity="0.08" stroke="#0969da" stroke-dasharray="3 3"/>',
                _text(margin_left + 10, zone_y_top - 8, label, color="#0969da", weight="700"),
            ]
        )

    range_prices = {
        round(price, 5)
        for low, high, _label in _ai_note_ranges(analysis.chart_notes)
        for price in (low, high)
    }
    label_offset = 0
    for price, label in _ai_note_levels(analysis.chart_notes):
        if round(price, 5) in range_prices:
            continue
        y = y_at(price)
        parts.extend(
            [
                f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + chart_width}" y2="{y:.2f}" stroke="#0969da" stroke-dasharray="2 5"/>',
                _text(margin_left + 10, y - 8 + label_offset, label, color="#0969da", weight="700"),
            ]
        )
        label_offset += 16

    return parts


def _ai_note_ranges(notes: str) -> list[tuple[float, float, str]]:
    ranges: list[tuple[float, float, str]] = []
    for match in re.finditer(r"(\d+\.\d+)\s*[-–]\s*(\d+\.\d+)", notes):
        first = float(match.group(1))
        second = float(match.group(2))
        low = min(first, second)
        high = max(first, second)
        ranges.append((low, high, f"AI note zone {low:.5f} - {high:.5f}"))
    return ranges[:4]


def _ai_note_levels(notes: str) -> list[tuple[float, str]]:
    levels: list[tuple[float, str]] = []
    for match in re.finditer(r"\d+\.\d+", notes):
        price = float(match.group(0))
        label = _ai_level_label(notes, match.start(), price)
        if all(abs(price - existing_price) > 0.00001 for existing_price, _ in levels):
            levels.append((price, label))
    return levels[:6]


def _ai_level_label(notes: str, position: int, price: float) -> str:
    context = notes[max(0, position - 32) : position].lower()
    if "sweep" in context or "liquidity" in context:
        return f"AI liquidity sweep {price:.5f}"
    if "bos" in context or "break" in context:
        return f"AI BOS watch {price:.5f}"
    if "supply" in context or "demand" in context or "zone" in context:
        return f"AI zone level {price:.5f}"
    return f"AI note level {price:.5f}"


def _subtitle(setup: SetupCandidate | None) -> str:
    if setup is None:
        return "No complete sweep plus BOS setup found in the visible sample."

    touch = "pullback touched zone" if setup.entry_zone.touched_after_bos else "waiting for pullback"
    return (
        f"{setup.side.upper()} {setup.status} | "
        f"Bias: {setup.bias.direction} | "
        f"{setup.entry_zone.source} | {touch} | "
        f"{setup.current_state} | Q{setup.quality_score}"
    )


def _text(
    x: float,
    y: float,
    value: str,
    size: int = 14,
    color: str = "#24292f",
    weight: str = "400",
) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}">{escape(value)}</text>'
    )


def _svg_header(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Arial, sans-serif">'
        '<rect width="100%" height="100%" fill="#f6f8fa"/>'
    )
