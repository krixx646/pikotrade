import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.candles import Candle
from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render forward-test review images with entry, SL, TP, and 200 SMA."
    )
    parser.add_argument(
        "--tests",
        default=str(PROJECT_ROOT / "outputs" / "forward_tests.json"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "outputs" / "reviews" / "forward_tests"),
    )
    parser.add_argument(
        "--status",
        default="timeout,sl_hit,sl_hit_ambiguous",
        help="Comma-separated target statuses to render.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tests = _load_json(Path(args.tests))
    statuses = {item.strip() for item in args.status.split(",") if item.strip()}
    client = OandaClient(load_oanda_config())
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[Path] = []
    for key, test in tests.items():
        if not isinstance(test, dict) or not _should_render(test, statuses):
            continue
        candles = [
            candle
            for candle in client.fetch_candles(str(test.get("instrument", "")), "M15", count=500)
            if candle.complete
        ]
        if not candles:
            continue
        output = output_dir / f"{_safe_name(key)}.png"
        render_trade_review(candles, str(key), test, output)
        rendered.append(output)

    for path in rendered:
        print(path)
    if not rendered:
        print("No matching forward-test scenarios rendered.")
    return 0


def render_trade_review(
    candles: list[Candle],
    key: str,
    test: dict[str, object],
    output: Path,
    width: int = 1400,
    height: int = 820,
) -> None:
    entry_time = _parse_time(str(test.get("entry_time") or test.get("created_at") or ""))
    exit_time = _parse_time(str(test.get("exit_time") or test.get("last_checked_at") or ""))
    if entry_time is None:
        entry_time = candles[max(0, len(candles) - 120)].time
    if exit_time is None:
        exit_time = candles[-1].time

    visible = _visible_window(candles, entry_time, exit_time)
    min_price = min(candle.low for candle in visible)
    max_price = max(candle.high for candle in visible)
    entry_price = float(test.get("entry_price", 0.0))
    stop_loss = float(test.get("stop_loss", 0.0))
    target_price, target_status = _target_info(test)
    entry_low = float(test.get("entry_low", entry_price))
    entry_high = float(test.get("entry_high", entry_price))

    for price in (entry_price, stop_loss, target_price, entry_low, entry_high):
        if price:
            min_price = min(min_price, price)
            max_price = max(max_price, price)
    padding = (max_price - min_price) * 0.08 or max_price * 0.001
    min_price -= padding
    max_price += padding

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(14)
    small_font = _font(12)
    title_font = _font(19)

    left, right = 78, width - 120
    top, bottom = 74, height - 86
    _draw_grid(draw, left, top, right, bottom)
    _draw_candles(draw, visible, left, right, top, bottom, min_price, max_price)
    _draw_sma(draw, visible, 200, left, right, top, bottom, min_price, max_price)

    _draw_zone(draw, left, right, top, bottom, min_price, max_price, entry_low, entry_high, small_font)
    _draw_price_line(draw, left, right, top, bottom, min_price, max_price, entry_price, "ENTRY", (30, 110, 220, 240), small_font)
    _draw_price_line(draw, left, right, top, bottom, min_price, max_price, stop_loss, "SL", (210, 40, 40, 240), small_font)
    _draw_price_line(draw, left, right, top, bottom, min_price, max_price, target_price, "3R TP", (20, 150, 80, 240), small_font)
    _draw_time_marker(draw, visible, entry_time, "entry", left, right, top, bottom, (30, 110, 220, 210), small_font)
    _draw_time_marker(draw, visible, exit_time, target_status or "exit/check", left, right, top, bottom, (110, 70, 170, 210), small_font)
    _draw_price_axis(draw, min_price, max_price, right, top, bottom, small_font)

    instrument = test.get("instrument", "")
    route = test.get("route", "")
    side = test.get("side", "")
    status = test.get("status", "")
    title = f"{instrument} {route} {side} forward-test review | {status} | {target_status}"
    draw.text((left, 22), title, fill=(0, 0, 0, 255), font=title_font)
    subtitle = (
        f"{key} | entry {entry_price:g}, SL {stop_loss:g}, TP {target_price:g} | "
        f"{visible[0].time.isoformat()} -> {visible[-1].time.isoformat()}"
    )
    draw.text((left, height - 44), subtitle, fill=(60, 60, 60, 255), font=font)
    draw.text((left, height - 24), "Orange line = 200 SMA on visible/fetched M15 context.", fill=(90, 90, 90, 255), font=small_font)
    image.save(output)


def _visible_window(candles: list[Candle], entry_time: datetime, exit_time: datetime) -> list[Candle]:
    start = entry_time - timedelta(hours=10)
    end = exit_time + timedelta(hours=4)
    visible = [candle for candle in candles if start <= candle.time <= end]
    if len(visible) < 80:
        entry_index = _first_index_at_or_after(candles, entry_time)
        start_index = max(0, entry_index - 80)
        end_index = min(len(candles), entry_index + 140)
        visible = candles[start_index:end_index]
    return visible or candles[-160:]


def _should_render(test: dict[str, object], statuses: set[str]) -> bool:
    targets = test.get("targets", {})
    if not isinstance(targets, dict):
        return False
    return any(
        isinstance(target, dict) and str(target.get("status", "")) in statuses
        for target in targets.values()
    )


def _target_info(test: dict[str, object]) -> tuple[float, str]:
    targets = test.get("targets", {})
    if isinstance(targets, dict):
        target = targets.get("3R")
        if isinstance(target, dict):
            return float(target.get("price", 0.0)), str(target.get("status", ""))
    return 0.0, ""


def _draw_grid(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
    for step in range(11):
        x = left + (right - left) * step / 10
        y = top + (bottom - top) * step / 10
        draw.line((x, top, x, bottom), fill=(232, 232, 232, 255), width=1)
        draw.line((left, y, right, y), fill=(232, 232, 232, 255), width=1)
    draw.rectangle((left, top, right, bottom), outline=(180, 180, 180, 255), width=1)


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
    points = []
    closes = [candle.close for candle in candles]
    for index in range(period - 1, len(candles)):
        value = sum(closes[index - period + 1 : index + 1]) / period
        x = left + slot * index + slot / 2
        y = _price_to_y(value, min_price, max_price, top, bottom)
        points.append((x, y))
    if len(points) >= 2:
        draw.line(points, fill=(230, 126, 34, 220), width=2)


def _draw_zone(
    draw: ImageDraw.ImageDraw,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    low: float,
    high: float,
    font: ImageFont.ImageFont,
) -> None:
    y1 = _price_to_y(high, min_price, max_price, top, bottom)
    y2 = _price_to_y(low, min_price, max_price, top, bottom)
    draw.rectangle((left, y1, right, y2), fill=(241, 196, 15, 45), outline=(180, 130, 0, 190), width=1)
    draw.text((left + 8, y1 + 3), f"entry zone {low:g}-{high:g}", fill=(120, 85, 0, 255), font=font)


def _draw_price_line(
    draw: ImageDraw.ImageDraw,
    left: int,
    right: int,
    top: int,
    bottom: int,
    min_price: float,
    max_price: float,
    price: float,
    label: str,
    color: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    if not price:
        return
    y = _price_to_y(price, min_price, max_price, top, bottom)
    draw.line((left, y, right, y), fill=color, width=2)
    draw.text((right + 8, y - 8), f"{label} {price:g}", fill=color, font=font)


def _draw_time_marker(
    draw: ImageDraw.ImageDraw,
    candles: list[Candle],
    event_time: datetime,
    label: str,
    left: int,
    right: int,
    top: int,
    bottom: int,
    color: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    index = _first_index_at_or_after(candles, event_time)
    if index >= len(candles):
        index = len(candles) - 1
    slot = (right - left) / max(1, len(candles))
    x = left + slot * index + slot / 2
    draw.line((x, top, x, bottom), fill=color, width=2)
    draw.text((x + 5, top + 8), label, fill=color, font=font)


def _draw_price_axis(
    draw: ImageDraw.ImageDraw,
    min_price: float,
    max_price: float,
    right: int,
    top: int,
    bottom: int,
    font: ImageFont.ImageFont,
) -> None:
    for step in range(6):
        price = min_price + (max_price - min_price) * step / 5
        y = _price_to_y(price, min_price, max_price, top, bottom)
        draw.text((right + 8, y - 7), f"{price:g}", fill=(40, 40, 40, 255), font=font)


def _price_to_y(price: float, min_price: float, max_price: float, top: int, bottom: int) -> float:
    return bottom - (price - min_price) / (max_price - min_price) * (bottom - top)


def _first_index_at_or_after(candles: list[Candle], value: datetime) -> int:
    for index, candle in enumerate(candles):
        if candle.time >= value:
            return index
    return len(candles)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
