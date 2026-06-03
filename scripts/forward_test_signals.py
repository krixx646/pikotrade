import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.forward_testing import (
    DEFAULT_OPENCLAW_TESTS_PATH,
    DEFAULT_TESTS_MD_PATH,
    DEFAULT_TESTS_PATH,
    run_forward_testing,
)
from fx_annotation.oanda_client import OandaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Forward-test live forex agent signals like paper trades."
    )
    parser.add_argument("--tests-output", default=str(DEFAULT_TESTS_PATH))
    parser.add_argument("--markdown-output", default=str(DEFAULT_TESTS_MD_PATH))
    parser.add_argument("--openclaw-output", default=str(DEFAULT_OPENCLAW_TESTS_PATH))
    parser.add_argument(
        "--rr",
        default="3",
        help="Comma-separated R targets to track. Default is 3R.",
    )
    parser.add_argument(
        "--timeout-bars",
        type=int,
        default=48,
        help="M15 candles before an active forward test times out. Default is 48 bars / 12 hours to keep tests intraday (no overnight holds). On timeout the runner is marked to market.",
    )
    parser.add_argument(
        "--max-signal-age-minutes",
        type=int,
        default=30,
        help="Only open new tests from signals updated within this many minutes.",
    )
    parser.add_argument(
        "--no-m5-variant",
        action="store_true",
        help="Disable the parallel {route}_M5 paper trade (same entry, tighter M5 stop) "
        "that is tracked alongside each M15 signal for an M15-vs-M5 live comparison.",
    )
    parser.add_argument(
        "--momentum-cooldown-minutes",
        type=int,
        default=90,
        help="Minimum minutes between new MOMENTUM entries on the same pair+side, so a "
        "trending move can't stack many near-identical signals. 0 disables the cooldown.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_oanda_config()
    client = OandaClient(config)
    tests = run_forward_testing(
        client=client,
        tests_path=Path(args.tests_output),
        markdown_path=Path(args.markdown_output),
        openclaw_path=Path(args.openclaw_output),
        rr_values=_rr_values(args.rr),
        timeout_bars=args.timeout_bars,
        max_signal_age_minutes=args.max_signal_age_minutes,
        track_m5_variant=not args.no_m5_variant,
        momentum_cooldown_minutes=args.momentum_cooldown_minutes,
    )
    print(f"Forward tests tracked: {len(tests)}")
    print(f"JSON: {args.tests_output}")
    print(f"Markdown: {args.markdown_output}")
    print(f"OpenClaw: {args.openclaw_output}")
    return 0


def _rr_values(value: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not values:
        raise ValueError("--rr must include at least one numeric R value")
    return values


if __name__ == "__main__":
    raise SystemExit(main())
