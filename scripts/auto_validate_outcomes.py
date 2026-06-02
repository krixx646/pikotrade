import argparse
import csv
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.market_watch import DEFAULT_WATCHLIST, InstrumentState, scan_market
from fx_annotation.oanda_client import OandaClient
from fx_annotation.outcome import OutcomeResult, validate_setup_outcome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically validate detected entries with test-only SL/TP outcomes."
    )
    parser.add_argument(
        "--instruments",
        default=",".join(DEFAULT_WATCHLIST),
        help="Comma-separated OANDA instruments.",
    )
    parser.add_argument("--bias-granularity", default="H4")
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--setup-limit", type=int, default=5)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--timeout-bars", type=int, default=48)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "outputs" / "auto_validation"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_oanda_config()
    client = OandaClient(config)
    instruments = [
        instrument.strip()
        for instrument in args.instruments.split(",")
        if instrument.strip()
    ]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    states = scan_market(
        client=client,
        instruments=instruments,
        bias_granularity=args.bias_granularity,
        entry_granularity=args.entry_granularity,
        setup_limit=args.setup_limit,
    )
    records = build_outcome_records(
        states=states[: args.top],
        timeout_bars=args.timeout_bars,
    )

    csv_path = output_dir / "outcomes.csv"
    report_path = output_dir / "outcomes.md"
    write_csv(csv_path, records)
    report_path.write_text(render_report(records), encoding="utf-8")

    print(f"Outcome records: {len(records)}")
    print(f"CSV: {csv_path}")
    print(f"Report: {report_path}")
    print(summary_line(records))
    return 0


def build_outcome_records(
    states: list[InstrumentState],
    timeout_bars: int,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    for state in states:
        if state.primary_setup is None or not state.entry_candles:
            continue

        outcomes = validate_setup_outcome(
            candles=state.entry_candles,
            setup=state.primary_setup,
            timeout_bars=timeout_bars,
        )

        for outcome in outcomes:
            records.append(record_from_outcome(state, outcome))

    return records


def record_from_outcome(
    state: InstrumentState,
    outcome: OutcomeResult,
) -> dict[str, str]:
    setup = state.primary_setup
    if setup is None:
        raise ValueError("State has no primary setup")

    entry_time = (
        state.entry_candles[outcome.entry_index].time.isoformat()
        if outcome.entry_index is not None
        else ""
    )
    exit_time = (
        state.entry_candles[outcome.exit_index].time.isoformat()
        if outcome.exit_index is not None
        else ""
    )

    return {
        "instrument": state.instrument,
        "market_status": state.status,
        "bias": setup.bias.direction,
        "side": setup.side,
        "setup_status": setup.status,
        "current_state": setup.current_state,
        "quality_score": str(setup.quality_score),
        "rr": f"{outcome.rr:.1f}",
        "result": outcome.result,
        "verdict": outcome.verdict,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "bars_to_result": "" if outcome.bars_to_result is None else str(outcome.bars_to_result),
        "entry_price": f"{outcome.entry_price:.5f}",
        "stop_loss": f"{outcome.stop_loss:.5f}",
        "take_profit": f"{outcome.take_profit:.5f}",
        "risk": f"{outcome.risk:.5f}",
        "entry_zone_low": f"{setup.entry_zone.low:.5f}",
        "entry_zone_high": f"{setup.entry_zone.high:.5f}",
        "zone_source": setup.entry_zone.source,
        "quality_notes": "; ".join(setup.quality_notes),
        "reason": outcome.reason,
    }


def write_csv(path: Path, records: list[dict[str, str]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def render_report(records: list[dict[str, str]]) -> str:
    lines = [
        "# Automatic Outcome Validation",
        "",
        "This uses test-only SL/TP to check whether detected entry zones played out.",
        "",
        "These SL/TP values are for validation only. They are not user trade instructions.",
        "",
        summary_line(records),
        "",
        *breakdown_lines(records),
        "",
        "## Records",
        "",
    ]

    for record in records:
        lines.extend(
            [
                f"### {record['instrument']} {record['side'].upper()} {record['rr']}R",
                "",
                f"- Market status: {record['market_status']}",
                f"- Bias: {record['bias']}",
                f"- Result: {record['result']}",
                f"- Verdict: {record['verdict']}",
                f"- Current state: {record['current_state']}",
                f"- Quality score: {record['quality_score']}",
                f"- Entry time: {record['entry_time']}",
                f"- Exit time: {record['exit_time'] or 'none'}",
                f"- Bars to result: {record['bars_to_result'] or 'none'}",
                f"- Entry: {record['entry_price']}",
                f"- Test SL: {record['stop_loss']}",
                f"- Test TP: {record['take_profit']}",
                f"- Entry zone: {record['entry_zone_low']} - {record['entry_zone_high']}",
                f"- Zone source: {record['zone_source']}",
                f"- Quality notes: {record['quality_notes']}",
                f"- Reason: {record['reason']}",
                "",
            ]
        )

    return "\n".join(lines) + "\n"


def breakdown_lines(records: list[dict[str, str]]) -> list[str]:
    if not records:
        return []

    return [
        "## Breakdowns",
        "",
        "### By Current State",
        "",
        *_group_lines(records, "current_state"),
        "",
        "### By Zone Source",
        "",
        *_group_lines(records, "zone_source"),
        "",
        "### By Market Status",
        "",
        *_group_lines(records, "market_status"),
    ]


def _group_lines(records: list[dict[str, str]], key: str) -> list[str]:
    groups: dict[str, list[dict[str, str]]] = {}
    for record in records:
        groups.setdefault(record[key], []).append(record)

    lines: list[str] = []
    for name, group_records in sorted(groups.items()):
        total = len(group_records)
        tp = sum(1 for record in group_records if record["result"] == "tp_hit")
        sl = sum(1 for record in group_records if record["result"] in {"sl_hit", "ambiguous_sl_first"})
        timeout = sum(1 for record in group_records if record["result"] == "timeout")
        not_triggered = sum(1 for record in group_records if record["result"] == "not_triggered")
        lines.append(
            f"- {name}: {total} checks, {tp} TP, {sl} SL, {timeout} timeout, {not_triggered} not triggered"
        )
    return lines


def summary_line(records: list[dict[str, str]]) -> str:
    if not records:
        return "Summary: no outcome records."

    total = len(records)
    tp = sum(1 for record in records if record["result"] == "tp_hit")
    sl = sum(1 for record in records if record["result"] in {"sl_hit", "ambiguous_sl_first"})
    timeout = sum(1 for record in records if record["result"] == "timeout")
    pending = sum(1 for record in records if record["result"] == "not_triggered")

    return (
        "Summary: "
        f"{total} checks, {tp} TP hit, {sl} SL hit, "
        f"{timeout} timeout, {pending} not triggered."
    )


if __name__ == "__main__":
    raise SystemExit(main())
