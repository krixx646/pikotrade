import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.candles import Candle
from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate DeepSeek AI route entry zones separately from the rule engine."
    )
    parser.add_argument(
        "--memory",
        default=str(PROJECT_ROOT / "outputs" / "ai_memory.json"),
    )
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--candle-count", type=int, default=500)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "outputs" / "ai_validation"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    memory = _load_memory(Path(args.memory))
    client = OandaClient(load_oanda_config())
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, str]] = []
    for instrument, analysis in memory.items():
        if not isinstance(analysis, dict):
            continue
        records.extend(
            validate_ai_analysis(
                client=client,
                instrument=instrument,
                analysis=analysis,
                granularity=args.entry_granularity,
                candle_count=args.candle_count,
            )
        )

    csv_path = output_dir / "ai_outcomes.csv"
    report_path = output_dir / "ai_outcomes.md"
    write_csv(csv_path, records)
    report_path.write_text(render_report(records), encoding="utf-8")

    print(f"AI outcome records: {len(records)}")
    print(f"CSV: {csv_path}")
    print(f"Report: {report_path}")
    print(summary_line(records))
    return 0


def validate_ai_analysis(
    client: OandaClient,
    instrument: str,
    analysis: dict[str, object],
    granularity: str,
    candle_count: int,
) -> list[dict[str, str]]:
    side = str(analysis.get("side", "")).lower()
    if side not in {"buy", "sell"}:
        return []

    zone_low = _float_or_none(analysis.get("entry_zone_low"))
    zone_high = _float_or_none(analysis.get("entry_zone_high"))
    updated_at = _parse_time(str(analysis.get("updated_at", "")))
    if zone_low is None or zone_high is None or updated_at is None:
        return []

    candles = [
        candle
        for candle in client.fetch_candles(instrument, granularity, count=candle_count)
        if candle.complete and candle.time >= updated_at
    ]
    entry_index = _first_touch_index(candles, zone_low, zone_high)
    records: list[dict[str, str]] = []
    for rr in (2.0, 3.0):
        records.append(
            _record(
                instrument=instrument,
                analysis=analysis,
                candles=candles,
                entry_index=entry_index,
                rr=rr,
                zone_low=min(zone_low, zone_high),
                zone_high=max(zone_low, zone_high),
                side=side,
            )
        )
    return records


def _record(
    instrument: str,
    analysis: dict[str, object],
    candles: list[Candle],
    entry_index: int | None,
    rr: float,
    zone_low: float,
    zone_high: float,
    side: str,
) -> dict[str, str]:
    entry_price = (zone_low + zone_high) / 2
    zone_size = max(zone_high - zone_low, entry_price * 0.0002)
    if side == "buy":
        stop_loss = zone_low - zone_size
        take_profit = entry_price + (entry_price - stop_loss) * rr
    else:
        stop_loss = zone_high + zone_size
        take_profit = entry_price - (stop_loss - entry_price) * rr

    result, exit_index, reason = _outcome(candles, entry_index, side, stop_loss, take_profit)
    return {
        "instrument": instrument,
        "ai_side": side,
        "ai_status": str(analysis.get("status", "")),
        "ai_confidence": str(analysis.get("confidence", "")),
        "rr": f"{rr:.1f}",
        "result": result,
        "entry_time": "" if entry_index is None else candles[entry_index].time.isoformat(),
        "exit_time": "" if exit_index is None else candles[exit_index].time.isoformat(),
        "entry_price": f"{entry_price:.5f}",
        "stop_loss": f"{stop_loss:.5f}",
        "take_profit": f"{take_profit:.5f}",
        "entry_zone_low": f"{zone_low:.5f}",
        "entry_zone_high": f"{zone_high:.5f}",
        "reason": reason,
    }


def _outcome(
    candles: list[Candle],
    entry_index: int | None,
    side: str,
    stop_loss: float,
    take_profit: float,
) -> tuple[str, int | None, str]:
    if entry_index is None:
        return "not_triggered", None, "AI entry zone was not touched after analysis time."

    for index in range(entry_index, len(candles)):
        candle = candles[index]
        hit_stop = candle.low <= stop_loss if side == "buy" else candle.high >= stop_loss
        hit_target = candle.high >= take_profit if side == "buy" else candle.low <= take_profit
        if hit_stop and hit_target:
            return "ambiguous_same_candle", index, "Stop and target were both touched in the same candle."
        if hit_target:
            return "tp_hit", index, "Test-only target was hit first."
        if hit_stop:
            return "sl_hit", index, "Test-only stop was hit first."

    return "pending", None, "AI entry triggered, but no test-only outcome is known yet."


def _first_touch_index(candles: list[Candle], zone_low: float, zone_high: float) -> int | None:
    low = min(zone_low, zone_high)
    high = max(zone_low, zone_high)
    for index, candle in enumerate(candles):
        if candle.low <= high and candle.high >= low:
            return index
    return None


def render_report(records: list[dict[str, str]]) -> str:
    lines = [
        "# DeepSeek AI Outcome Validation",
        "",
        "This validates AI route entry zones separately from rule-engine candidates.",
        "",
        summary_line(records),
        "",
    ]
    if not records:
        lines.append("No AI analyses with concrete buy/sell entry zones were available.")
        return "\n".join(lines) + "\n"

    for record in records:
        lines.extend(
            [
                f"## {record['instrument']} {record['ai_side'].upper()} {record['rr']}R",
                "",
                f"- AI status: {record['ai_status']}",
                f"- AI confidence: {record['ai_confidence']}",
                f"- Result: {record['result']}",
                f"- Entry time: {record['entry_time'] or 'none'}",
                f"- Exit time: {record['exit_time'] or 'none'}",
                f"- Entry zone: {record['entry_zone_low']} - {record['entry_zone_high']}",
                f"- Reason: {record['reason']}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def summary_line(records: list[dict[str, str]]) -> str:
    if not records:
        return "Summary: no AI outcome records."
    tp = sum(1 for record in records if record["result"] == "tp_hit")
    sl = sum(1 for record in records if record["result"] == "sl_hit")
    pending = sum(1 for record in records if record["result"] == "pending")
    not_triggered = sum(1 for record in records if record["result"] == "not_triggered")
    return (
        f"Summary: {len(records)} checks, {tp} TP hit, {sl} SL hit, "
        f"{pending} pending, {not_triggered} not triggered."
    )


def write_csv(path: Path, records: list[dict[str, str]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def _load_memory(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
