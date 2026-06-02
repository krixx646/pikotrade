import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_deepseek_config
from fx_annotation.deepseek_client import call_deepseek_text


DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "backtests" / "validation_multi_rule_backtest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "backtests" / "deepseek_backtest_review.md"


def main() -> int:
    data = json.loads(DEFAULT_INPUT.read_text(encoding="utf-8"))
    config = load_deepseek_config()
    if config is None:
        raise RuntimeError("DeepSeek config missing")
    prompt = _build_prompt(data)
    response = call_deepseek_text(config, prompt, json_mode=True)
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(_render(response), encoding="utf-8")
    print(DEFAULT_OUTPUT)
    return 0


def _build_prompt(data: dict[str, object]) -> str:
    trades = data.get("trades", [])
    compact_trades = []
    for trade in trades[:12] if isinstance(trades, list) else []:
        if not isinstance(trade, dict):
            continue
        snapshot = trade.get("decision_snapshot", {})
        setup = snapshot.get("setup", {}) if isinstance(snapshot, dict) else {}
        poi = snapshot.get("relevant_htf_poi", {}) if isinstance(snapshot, dict) else {}
        narrative = snapshot.get("narrative", {}) if isinstance(snapshot, dict) else {}
        compact_trades.append(
            {
                "instrument": trade.get("instrument"),
                "side": trade.get("side"),
                "result": trade.get("result"),
                "mfe_r": trade.get("max_favorable_r"),
                "mae_r": trade.get("max_adverse_r"),
                "entry": trade.get("entry_price"),
                "sl": trade.get("stop_loss"),
                "tp": trade.get("target_price"),
                "bias": snapshot.get("bias") if isinstance(snapshot, dict) else None,
                "h1_bias": snapshot.get("h1_bias") if isinstance(snapshot, dict) else None,
                "narrative": narrative,
                "htf_poi": poi,
                "setup": setup,
                "zone_ladder": (snapshot.get("zone_ladder") or [])[:2] if isinstance(snapshot, dict) else [],
            }
        )

    return f"""
Return exactly one JSON object.

You are reviewing a rule-only forex strategy backtest. No AI trades were used.
The user's strategy requires trend-following HTF direction, active H4/H1 zone story, M15 liquidity sweep, M15 BOS, then pullback into the LTF base that caused BOS. Minimum target is 3R.

Question:
Why is this validation result so bad, and what are the root algorithm/backtest issues to investigate first?

Backtest summary:
{json.dumps(data.get("summary", {}), indent=2)}

Rejection diagnostics:
{json.dumps(data.get("diagnostics", {}), indent=2)[:5000]}

Compact accepted trades:
{json.dumps(compact_trades, indent=2)[:12000]}

Return JSON keys:
- "plain_english_diagnosis"
- "likely_backtest_bugs"
- "likely_strategy_encoding_errors"
- "filters_to_test_first"
- "do_not_change_yet"
- "next_experiment"
"""


def _render(response: str) -> str:
    try:
        parsed = json.loads(response)
        body = json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        body = response
    return "# DeepSeek Backtest Review\n\n```json\n" + body + "\n```\n"


if __name__ == "__main__":
    raise SystemExit(main())
