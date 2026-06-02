import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.chart_pack import render_instrument_chart_pack
from fx_annotation.config import load_oanda_config
from fx_annotation.oanda_client import OandaClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render OANDA-based fallback chart images for AI visual context."
    )
    parser.add_argument("--instrument", default="XAU_USD")
    parser.add_argument("--bias-granularity", default="H4")
    parser.add_argument("--refinement-granularity", default="H1")
    parser.add_argument("--entry-granularity", default="M15")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "outputs" / "charts"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = OandaClient(load_oanda_config())
    paths = render_instrument_chart_pack(
        client=client,
        instrument=args.instrument,
        output_root=Path(args.output_dir),
        bias_granularity=args.bias_granularity,
        refinement_granularity=args.refinement_granularity,
        entry_granularity=args.entry_granularity,
    )

    print(f"Rendered chart pack: {Path(args.output_dir) / args.instrument}")
    for path in paths:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
