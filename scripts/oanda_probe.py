import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient
from fx_annotation.structure import (
    detect_break_after_sweep,
    detect_liquidity_sweeps,
    detect_swings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch OANDA candles and run the first structure checks."
    )
    parser.add_argument("--instrument", default="EUR_USD")
    parser.add_argument("--granularity", default="M15")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--swing-window", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_oanda_config()
    client = OandaClient(config)

    candles = client.fetch_candles(
        instrument=args.instrument,
        granularity=args.granularity,
        count=args.count,
    )
    completed = [candle for candle in candles if candle.complete]

    swings = detect_swings(completed, window=args.swing_window)
    sweeps = detect_liquidity_sweeps(completed, swings)
    latest_sweep = sweeps[-1] if sweeps else None
    latest_bos = (
        detect_break_after_sweep(completed, swings, latest_sweep)
        if latest_sweep
        else None
    )

    print(f"Instrument: {args.instrument}")
    print(f"Granularity: {args.granularity}")
    print(f"Completed candles: {len(completed)}")
    print(f"Swing points: {len(swings)}")
    print(f"Liquidity sweeps: {len(sweeps)}")

    if completed:
        latest = completed[-1]
        print(
            "Latest candle: "
            f"{latest.time.isoformat()} "
            f"O={latest.open} H={latest.high} L={latest.low} C={latest.close}"
        )

    if latest_sweep:
        sweep_candle = completed[latest_sweep.index]
        print(
            "Latest sweep: "
            f"{latest_sweep.kind} at {sweep_candle.time.isoformat()} "
            f"swept {latest_sweep.swept_price}"
        )
    else:
        print("Latest sweep: none detected")

    if latest_bos:
        bos_candle = completed[latest_bos.index]
        print(
            "Break of structure after latest sweep: "
            f"{latest_bos.direction} at {bos_candle.time.isoformat()} "
            f"broke {latest_bos.broken_price}"
        )
    else:
        print("Break of structure after latest sweep: none detected")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
