import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.market_watch import DEFAULT_WATCHLIST, InstrumentState, scan_market
from fx_annotation.oanda_client import OandaClient
from fx_annotation.report import render_setup_report
from fx_annotation.svg_renderer import render_setup_svg


READY_STATUSES = {"entry_candidate_now", "wait_for_pullback", "potential_future_setup"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate review charts and feedback checklist for top candidates."
    )
    parser.add_argument(
        "--instruments",
        default=",".join(DEFAULT_WATCHLIST),
        help="Comma-separated OANDA instruments.",
    )
    parser.add_argument("--bias-granularity", default="H4")
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--setup-limit", type=int, default=5)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "outputs" / "validation"),
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
    candidates = _top_candidates(states, limit=args.top)

    for state in candidates:
        _write_candidate_files(
            state=state,
            bias_granularity=args.bias_granularity,
            entry_granularity=args.entry_granularity,
            output_dir=output_dir,
        )

    checklist = render_validation_checklist(candidates, args)
    checklist_path = output_dir / "review_checklist.md"
    checklist_path.write_text(checklist, encoding="utf-8")

    print(f"Validation candidates: {len(candidates)}")
    print(f"Review checklist: {checklist_path}")
    for state in candidates:
        print(f"- {state.instrument}: {state.status} ({state.action})")

    return 0


def render_validation_checklist(
    candidates: list[InstrumentState],
    args: argparse.Namespace,
) -> str:
    lines = [
        "# Candidate Validation Checklist",
        "",
        "Use this file to manually review whether the scanner is actually marking sensible entries.",
        "",
        "Allowed verdicts:",
        "",
        "- Good",
        "- Weak",
        "- Wrong",
        "- Duplicate",
        "- Needs better rule",
        "",
        "Review questions:",
        "",
        "- Did it correctly identify the liquidity sweep?",
        "- Did it correctly identify meaningful BOS?",
        "- Is the entry zone reasonable?",
        "- Is the setup too late?",
        "- Is price in chop or middle of range?",
        "- Would a human using the notebook strategy agree?",
        "",
        f"Bias timeframe: `{args.bias_granularity}`",
        f"Entry timeframe: `{args.entry_granularity}`",
        "",
    ]

    for index, state in enumerate(candidates, start=1):
        safe_name = _safe_name(state.instrument)
        lines.extend(
            [
                f"## Candidate {index}: {state.instrument}",
                "",
                f"- Status: {state.status}",
                f"- Action: {state.action}",
                f"- Chart: `{safe_name}.svg`",
                f"- Report: `{safe_name}.md`",
                "- Verdict: TODO",
                "- Human notes: TODO",
                "",
            ]
        )

        if state.bias is not None:
            lines.extend(
                [
                    f"- Bias: {state.bias.direction}",
                    f"- Bias reason: {state.bias.reason}",
                    "",
                ]
            )

        if state.primary_setup is not None:
            setup = state.primary_setup
            lines.extend(
                [
                    f"- Side: {setup.side.upper()}",
                    f"- Entry zone: {setup.entry_zone.low:.5f} - {setup.entry_zone.high:.5f}",
                    f"- Zone source: {setup.entry_zone.source}",
                    f"- Pullback touched: {setup.entry_zone.touched_after_bos}",
                    f"- Current state: {setup.current_state}",
                    f"- Quality score: {setup.quality_score}",
                    "",
                ]
            )

    return "\n".join(lines) + "\n"


def _write_candidate_files(
    state: InstrumentState,
    bias_granularity: str,
    entry_granularity: str,
    output_dir: Path,
) -> None:
    safe_name = _safe_name(state.instrument)
    chart_path = output_dir / f"{safe_name}.svg"
    report_path = output_dir / f"{safe_name}.md"

    title = f"{state.instrument} {entry_granularity} validation with {bias_granularity} bias"
    chart_path.write_text(
        render_setup_svg(
            candles=state.entry_candles,
            swings=state.swings,
            setup=state.primary_setup,
            title=title,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        render_setup_report(
            instrument=state.instrument,
            bias_granularity=bias_granularity,
            entry_granularity=entry_granularity,
            bias=state.bias,
            entry_candles=state.entry_candles,
            swings=state.swings,
            sweeps=state.sweeps,
            setup=state.primary_setup,
            recent_setups=state.recent_setups,
            chart_path=str(chart_path),
        ),
        encoding="utf-8",
    )


def _top_candidates(states: list[InstrumentState], limit: int) -> list[InstrumentState]:
    usable = [
        state
        for state in states
        if state.bias is not None and state.entry_candles
    ]
    preferred = [state for state in usable if state.status in READY_STATUSES]
    if len(preferred) >= limit:
        return preferred[:limit]
    remaining = [state for state in usable if state not in preferred]
    return (preferred + remaining)[:limit]


def _safe_name(instrument: str) -> str:
    return instrument.replace("/", "_").replace("\\", "_")


if __name__ == "__main__":
    raise SystemExit(main())
