from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fx_annotation.candles import Candle
from fx_annotation.narrative import HtfNarrative
from fx_annotation.poi import ZoneLadderItem
from fx_annotation.setups import SetupCandidate
from fx_annotation.structure import SwingPoint


def render_chart_image(
    candles: list[Candle],
    output_path: Path,
    title: str,
    zones: list[ZoneLadderItem] | None = None,
    swings: list[SwingPoint] | None = None,
    narrative: HtfNarrative | None = None,
    setups: list[SetupCandidate] | None = None,
    width: int = 1280,
    height: int = 720,
    lookback: int = 140,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    visible = candles[-lookback:] if len(candles) > lookback else candles
    if not visible:
        raise ValueError("No candles available to render")

    zones = zones or []
    swings = swings or []
    setups = setups or []
    margin_left = 72
    margin_right = 92
    margin_top = 54
    margin_bottom = 58
    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom

    min_price = min(candle.low for candle in visible)
    max_price = max(candle.high for candle in visible)
    for zone in zones:
        min_price = min(min_price, zone.low)
        max_price = max(max_price, zone.high)
    for setup in setups:
        min_price = min(min_price, setup.entry_zone.low, setup.sweep.swept_price, setup.bos.broken_price)
        max_price = max(max_price, setup.entry_zone.high, setup.sweep.swept_price, setup.bos.broken_price)
    if narrative is not None:
        min_price = min(min_price, narrative.lowest_low)
        max_price = max(max_price, narrative.highest_high)
    padding = (max_price - min_price) * 0.08 or max_price * 0.001
    min_price -= padding
    max_price += padding

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(14)
    small_font = _font(11)
    title_font = _font(18)

    _draw_grid(draw, plot_left, plot_top, plot_right, plot_bottom)
    _draw_zones(
        draw,
        zones,
        len(candles) - len(visible),
        len(visible),
        plot_left,
        plot_right,
        _price_to_y,
        min_price,
        max_price,
        plot_top,
        plot_bottom,
        small_font,
    )
    _draw_setups(
        draw,
        setups,
        len(candles) - len(visible),
        len(visible),
        plot_left,
        plot_right,
        _price_to_y,
        min_price,
        max_price,
        plot_top,
        plot_bottom,
        small_font,
    )
    _draw_narrative(draw, narrative, len(candles) - len(visible), len(visible), plot_left, plot_right, plot_top, plot_bottom, min_price, max_price, font)
    _draw_candles(draw, visible, plot_left, plot_right, plot_top, plot_bottom, min_price, max_price)
    _draw_swings(draw, visible, swings, len(candles) - len(visible), plot_left, plot_right, plot_top, plot_bottom, min_price, max_price, small_font)
    _draw_price_axis(draw, min_price, max_price, plot_right, plot_top, plot_bottom, small_font)

    latest = visible[-1]
    latest_y = _price_to_y(latest.close, min_price, max_price, plot_top, plot_bottom)
    draw.line((plot_left, latest_y, plot_right, latest_y), fill=(40, 40, 40, 120), width=1)
    draw.text((plot_right + 6, latest_y - 8), f"{latest.close:.5f}", fill=(0, 0, 0, 255), font=small_font)
    draw.text((plot_left, 18), title, fill=(0, 0, 0, 255), font=title_font)
    draw.text(
        (plot_left, height - 34),
        f"{visible[0].time.isoformat()} -> {visible[-1].time.isoformat()} | OANDA-rendered fallback chart",
        fill=(80, 80, 80, 255),
        font=font,
    )

    image.save(output_path)


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    for step in range(0, 11):
        x = left + (right - left) * step / 10
        draw.line((x, top, x, bottom), fill=(230, 230, 230, 255), width=1)
        y = top + (bottom - top) * step / 10
        draw.line((left, y, right, y), fill=(230, 230, 230, 255), width=1)
    draw.rectangle((left, top, right, bottom), outline=(180, 180, 180, 255), width=1)


def _draw_zones(
    draw: ImageDraw.ImageDraw,
    zones: list[ZoneLadderItem],
    offset: int,
    visible_count: int,
    left: int,
    right: int,
    price_to_y: object,
    min_price: float,
    max_price: float,
    plot_top: int,
    plot_bottom: int,
    font: ImageFont.ImageFont,
) -> None:
    visible_zones = [
        zone
        for zone in zones
        if zone.state not in {"failed", "below_untouched", "above_untouched"}
    ]
    for zone in visible_zones[:10]:
        slot = (right - left) / max(1, visible_count)
        local_index = zone.index - offset
        if local_index >= visible_count:
            continue
        zone_left = left if local_index < 0 else left + slot * local_index
        zone_right = min(right, zone_left + max(180, (right - left) * 0.36))
        y1 = price_to_y(zone.high, min_price, max_price, plot_top, plot_bottom)
        y2 = price_to_y(zone.low, min_price, max_price, plot_top, plot_bottom)
        fill = (39, 174, 96, 55) if zone.side == "demand" else (231, 76, 60, 55)
        border = (39, 174, 96, 180) if zone.side == "demand" else (231, 76, 60, 180)
        draw.rectangle((zone_left, y1, zone_right, y2), fill=fill, outline=border, width=1)
        label = f"{zone.timeframe} {zone.side} {zone.state} {zone.low:.5f}-{zone.high:.5f}"
        draw.text((zone_left + 6, y1 + 3), label, fill=border, font=font)


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
    body_width = max(2, int(slot * 0.58))
    for index, candle in enumerate(candles):
        x = left + slot * index + slot / 2
        high_y = _price_to_y(candle.high, min_price, max_price, top, bottom)
        low_y = _price_to_y(candle.low, min_price, max_price, top, bottom)
        open_y = _price_to_y(candle.open, min_price, max_price, top, bottom)
        close_y = _price_to_y(candle.close, min_price, max_price, top, bottom)
        color = (0, 150, 136, 255) if candle.close >= candle.open else (30, 30, 30, 255)
        draw.line((x, high_y, x, low_y), fill=color, width=1)
        y1 = min(open_y, close_y)
        y2 = max(open_y, close_y)
        if y2 - y1 < 1:
            y2 = y1 + 1
        draw.rectangle((x - body_width / 2, y1, x + body_width / 2, y2), fill=color)


def _draw_setups(
    draw: ImageDraw.ImageDraw,
    setups: list[SetupCandidate],
    offset: int,
    visible_count: int,
    left: int,
    right: int,
    price_to_y: object,
    min_price: float,
    max_price: float,
    plot_top: int,
    plot_bottom: int,
    font: ImageFont.ImageFont,
) -> None:
    slot = (right - left) / max(1, visible_count)
    for setup in setups[:3]:
        bos_local = setup.bos.index - offset
        sweep_local = setup.sweep.index - offset
        if bos_local < 0 and sweep_local < 0:
            continue

        zone_left = left if bos_local < 0 else left + slot * bos_local
        zone_right = min(right, zone_left + max(180, (right - left) * 0.28))
        y1 = price_to_y(setup.entry_zone.high, min_price, max_price, plot_top, plot_bottom)
        y2 = price_to_y(setup.entry_zone.low, min_price, max_price, plot_top, plot_bottom)
        fill = (241, 196, 15, 70) if setup.side == "buy" else (231, 76, 60, 70)
        border = (180, 130, 0, 220) if setup.side == "buy" else (190, 40, 40, 220)
        draw.rectangle((zone_left, y1, zone_right, y2), fill=fill, outline=border, width=2)
        draw.text(
            (zone_left + 6, y1 + 3),
            f"M15 {setup.side.upper()} entry base {setup.entry_zone.low:.5f}-{setup.entry_zone.high:.5f}",
            fill=border,
            font=font,
        )

        _draw_event_marker(
            draw,
            sweep_local,
            setup.sweep.swept_price,
            "Liquidity sweep",
            (46, 134, 193, 230),
            slot,
            left,
            right,
            plot_top,
            plot_bottom,
            min_price,
            max_price,
            font,
        )
        _draw_event_marker(
            draw,
            bos_local,
            setup.bos.broken_price,
            "Market Shift / BOS",
            (142, 68, 173, 230),
            slot,
            left,
            right,
            plot_top,
            plot_bottom,
            min_price,
            max_price,
            font,
        )


def _draw_event_marker(
    draw: ImageDraw.ImageDraw,
    local_index: int,
    price: float,
    label: str,
    color: tuple[int, int, int, int],
    slot: float,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    font: ImageFont.ImageFont,
) -> None:
    if local_index < 0:
        return
    x = left + slot * local_index + slot / 2
    if x < left or x > right:
        return
    y = _price_to_y(price, min_price, max_price, top, bottom)
    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color)
    draw.text((x + 8, y - 10), f"{label} {price:.5f}", fill=color, font=font)


def _draw_swings(
    draw: ImageDraw.ImageDraw,
    candles: list[Candle],
    swings: list[SwingPoint],
    offset: int,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    font: ImageFont.ImageFont,
) -> None:
    slot = (right - left) / max(1, len(candles))
    for swing in swings:
        local_index = swing.index - offset
        if local_index < 0 or local_index >= len(candles):
            continue
        x = left + slot * local_index + slot / 2
        y = _price_to_y(swing.price, min_price, max_price, top, bottom)
        color = (46, 134, 193, 180) if swing.kind == "high" else (142, 68, 173, 180)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
        label = "LQ high" if swing.kind == "high" else "LQ low"
        draw.text((x + 4, y - 9), label, fill=color, font=font)


def _draw_narrative(
    draw: ImageDraw.ImageDraw,
    narrative: HtfNarrative | None,
    offset: int,
    visible_count: int,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    font: ImageFont.ImageFont,
) -> None:
    if narrative is None:
        return
    high_y = _price_to_y(narrative.highest_high, min_price, max_price, top, bottom)
    low_y = _price_to_y(narrative.lowest_low, min_price, max_price, top, bottom)
    draw.line((left, high_y, right, high_y), fill=(231, 76, 60, 190), width=2)
    draw.line((left, low_y, right, low_y), fill=(39, 174, 96, 190), width=2)
    draw.text((left + 8, high_y - 18), f"HH {narrative.highest_high:.5f}", fill=(180, 40, 40, 255), font=font)
    draw.text((left + 8, low_y + 4), f"LL {narrative.lowest_low:.5f}", fill=(20, 130, 70, 255), font=font)
    _draw_liquidity_pools(draw, narrative, left, right, top, bottom, min_price, max_price, font)

    slot = (right - left) / max(1, visible_count)
    active_local_index = narrative.active_from_index - offset
    if 0 <= active_local_index < visible_count:
        active_x = left + slot * active_local_index + slot / 2
        draw.line((active_x, top, active_x, bottom), fill=(120, 80, 200, 170), width=2)
        draw.text(
            (active_x + 6, top + 8),
            f"active story starts: {narrative.active_from_anchor}",
            fill=(90, 50, 160, 255),
            font=font,
        )
    for anchor_index, price, label, color in (
        (narrative.highest_high_index, narrative.highest_high, "highest high", (231, 76, 60, 255)),
        (narrative.lowest_low_index, narrative.lowest_low, "lowest low", (39, 174, 96, 255)),
    ):
        local_index = anchor_index - offset
        if local_index < 0 or local_index >= visible_count:
            continue
        x = left + slot * local_index + slot / 2
        y = _price_to_y(price, min_price, max_price, top, bottom)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=color, width=3)
        draw.text((x + 8, y - 10), label, fill=color, font=font)


def _draw_liquidity_pools(
    draw: ImageDraw.ImageDraw,
    narrative: HtfNarrative,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    font: ImageFont.ImageFont,
) -> None:
    for pool in narrative.liquidity_pools[2:7]:
        if pool.kind == "equal_lows":
            color = (142, 68, 173, 120)
            label = "equal lows LQ"
        elif pool.kind == "equal_highs":
            color = (46, 134, 193, 120)
            label = "equal highs LQ"
        else:
            continue
        y = _price_to_y(pool.price, min_price, max_price, top, bottom)
        _draw_dashed_line(draw, left, y, right, color)
        draw.text((right - 112, y - 10), label, fill=color, font=font)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    left: int,
    y: float,
    right: int,
    color: tuple[int, int, int, int],
    dash_width: int = 10,
    gap_width: int = 8,
) -> None:
    x = left
    while x < right:
        draw.line((x, y, min(x + dash_width, right), y), fill=color, width=1)
        x += dash_width + gap_width


def _draw_price_axis(
    draw: ImageDraw.ImageDraw,
    min_price: float,
    max_price: float,
    right: int,
    top: int,
    bottom: int,
    font: ImageFont.ImageFont,
) -> None:
    for step in range(0, 6):
        price = min_price + (max_price - min_price) * step / 5
        y = _price_to_y(price, min_price, max_price, top, bottom)
        draw.text((right + 6, y - 8), f"{price:.5f}", fill=(70, 70, 70, 255), font=font)


def _price_to_y(
    price: float,
    min_price: float,
    max_price: float,
    top: int,
    bottom: int,
) -> float:
    if max_price <= min_price:
        return float(bottom)
    return bottom - ((price - min_price) / (max_price - min_price)) * (bottom - top)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()
