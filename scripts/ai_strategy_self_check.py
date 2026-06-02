import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import GeminiConfig, load_deepseek_config, load_gemma_reviewer_config
from fx_annotation.deepseek_client import call_deepseek_text
from fx_annotation.gemini_client import call_gemini_text
from fx_annotation.ollama_client import call_ollama_text


OUTPUT_PATH = PROJECT_ROOT / "outputs" / "reviews" / "ai_strategy_self_check.md"


def main() -> int:
    prompt = _prompt()
    sections: list[tuple[str, str]] = []

    deepseek_config = load_deepseek_config()
    if deepseek_config is not None:
        try:
            sections.append(("DeepSeek", call_deepseek_text(deepseek_config, prompt, json_mode=True)))
        except Exception as error:
            sections.append(("DeepSeek", json.dumps({"error": str(error)}, indent=2)))
    else:
        sections.append(("DeepSeek", json.dumps({"error": "DeepSeek config missing"}, indent=2)))

    try:
        reviewer = load_gemma_reviewer_config()
        if isinstance(reviewer, GeminiConfig):
            sections.append(("Gemma", call_gemini_text(reviewer, prompt)))
        else:
            sections.append(("Gemma", call_ollama_text(reviewer, prompt)))
    except Exception as error:
        sections.append(("Gemma", json.dumps({"error": str(error)}, indent=2)))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(_render_markdown(sections), encoding="utf-8")
    print(OUTPUT_PATH)
    return 0


def _prompt() -> str:
    return f"""
Return exactly one compact JSON object and nothing else.
Do not give trading advice. This is a self-check of strategy understanding.

Required keys:
- "understanding": one sentence, max 45 words
- "timeframes": one sentence, max 35 words
- "entry_sequence": one sentence, max 45 words
- "ma200": one sentence, max 25 words
- "must_not_do": one sentence, max 35 words

Current strategy facts:
- Goal: annotate possible forex entry zones only; no live execution, no lot size, no live SL/TP advice.
- H4 builds the main narrative; H1 refines when H4 is broad/noisy; M15 is only for execution confirmation.
- Trend-following gate: bullish HTF means BUY/demand only; bearish HTF means SELL/supply only; neutral means no actionable buy/sell unless refinement resolves it.
- Active story starts from the first-occurring HH/LL anchor. Older candles are background only, not zone/entry sources.
- Demand/supply zones must be compact 1-3 candle base/order-block clusters before meaningful displacement/BOS.
- Entry sequence: HTF/H1 zone test/respect, M15 liquidity sweep, M15 Market Shift/BOS, then pullback into the LTF base that caused the shift.
- 200-period moving average is supporting trend context: price above 200 MA supports bullish context, below supports bearish context; it is not a standalone signal.
- No setup when choppy, middle of range, late, stale, no clear sweep, weak BOS, failed zone, unclear zone, or contradictory evidence.
"""


def _render_markdown(sections: list[tuple[str, str]]) -> str:
    lines = ["# AI Strategy Self Check", ""]
    for label, raw in sections:
        lines.extend([f"## {label}", "", "```json", _pretty_json(raw), "```", ""])
    return "\n".join(lines)


def _pretty_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), indent=2, sort_keys=True)
    except json.JSONDecodeError:
        return raw


if __name__ == "__main__":
    raise SystemExit(main())
