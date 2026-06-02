import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.ai_strategy import (
    analyze_state_with_ai,
    render_ai_alerts_markdown,
    render_ai_strategy_report,
    select_ai_review_states,
    update_ai_memory,
)
from fx_annotation.config import load_deepseek_config, load_oanda_config
from fx_annotation.live_memory import render_memory_updates, update_live_memory
from fx_annotation.market_watch import (
    DEFAULT_WATCHLIST,
    render_market_watch_report,
    scan_market,
)
from fx_annotation.oanda_client import OandaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Autonomously scan multiple OANDA instruments for setup states."
    )
    parser.add_argument(
        "--instruments",
        default=",".join(DEFAULT_WATCHLIST),
        help="Comma-separated OANDA instruments.",
    )
    parser.add_argument("--bias-granularity", default="H4")
    parser.add_argument("--refinement-granularity", default="H1")
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--setup-limit", type=int, default=5)
    parser.add_argument("--fundamentals-file", default="")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "outputs" / "market_watch.md"))
    parser.add_argument(
        "--memory-output",
        default=str(PROJECT_ROOT / "outputs" / "live_memory.json"),
    )
    parser.add_argument(
        "--alerts-output",
        default=str(PROJECT_ROOT / "outputs" / "alerts.json"),
    )
    parser.add_argument(
        "--ai-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_strategy_analysis.md"),
    )
    parser.add_argument(
        "--ai-memory-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_memory.json"),
    )
    parser.add_argument(
        "--ai-alerts-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_alerts.json"),
    )
    parser.add_argument(
        "--ai-alerts-md-output",
        default=str(PROJECT_ROOT / "outputs" / "ai_alerts.md"),
    )
    parser.add_argument("--use-ai", action="store_true")
    parser.add_argument(
        "--ai-limit",
        type=int,
        default=3,
        help="Maximum number of instruments DeepSeek should analyze per run. Use 0 for all.",
    )
    parser.add_argument(
        "--loop-seconds",
        type=int,
        default=0,
        help="If greater than 0, keep rescanning on this interval.",
    )
    return parser.parse_args()


def main() -> int:
    _configure_stdout()
    args = parse_args()
    config = load_oanda_config()
    client = OandaClient(config)
    instruments = [
        item.strip()
        for item in args.instruments.split(",")
        if item.strip()
    ]

    while True:
        report = run_once(args, client, instruments)
        print(report)

        if args.loop_seconds <= 0:
            return 0

        time.sleep(args.loop_seconds)


def run_once(
    args: argparse.Namespace,
    client: OandaClient,
    instruments: list[str],
) -> str:
    fundamentals = _read_fundamentals(args.fundamentals_file)
    states = scan_market(
        client=client,
        instruments=instruments,
        fundamentals=fundamentals,
        bias_granularity=args.bias_granularity,
        refinement_granularity=args.refinement_granularity,
        entry_granularity=args.entry_granularity,
        setup_limit=args.setup_limit,
    )
    updates = update_live_memory(
        states,
        path=Path(args.memory_output),
        alerts_path=Path(args.alerts_output),
    )
    report = f"{render_market_watch_report(states)}\n{render_memory_updates(updates)}"
    if args.use_ai:
        report = f"{report}\n{run_ai_review(args, states, updates)}"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    return report


def run_ai_review(
    args: argparse.Namespace,
    states: object,
    updates: object,
) -> str:
    config = load_deepseek_config()
    if config is None:
        return (
            "## DeepSeek AI Route\n\n"
            "DeepSeek key is not configured. Add it to `.env.deepseek` to enable live v4pro review.\n"
        )

    del updates
    fundamentals = _read_fundamentals(args.fundamentals_file)
    analyses = []
    try:
        for state in select_ai_review_states(states, args.ai_limit):
            analyses.append(analyze_state_with_ai(config, state, fundamentals=fundamentals))
    except Exception as error:
        review = f"# DeepSeek AI Strategy Analysis\n\nDeepSeek live strategy analysis failed: {error}\n"
        ai_output = Path(args.ai_output)
        ai_output.parent.mkdir(parents=True, exist_ok=True)
        ai_output.write_text(review, encoding="utf-8")
        return review

    update_ai_memory(
        analyses,
        memory_path=Path(args.ai_memory_output),
        alerts_path=Path(args.ai_alerts_output),
    )
    ai_alerts_md_output = Path(args.ai_alerts_md_output)
    ai_alerts_md_output.parent.mkdir(parents=True, exist_ok=True)
    ai_alerts_md_output.write_text(
        render_ai_alerts_markdown(Path(args.ai_alerts_output)),
        encoding="utf-8",
    )
    review = render_ai_strategy_report(analyses)
    ai_output = Path(args.ai_output)
    ai_output.parent.mkdir(parents=True, exist_ok=True)
    ai_output.write_text(review, encoding="utf-8")
    return f"## AI Schedule Review\n\n{review}\n"


def _read_fundamentals(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
