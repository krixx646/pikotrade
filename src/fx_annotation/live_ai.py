from datetime import datetime, timezone

from fx_annotation.config import DeepSeekConfig
from fx_annotation.deepseek_client import call_deepseek_text
from fx_annotation.live_memory import MemoryUpdate
from fx_annotation.market_watch import InstrumentState


def review_live_schedule_with_ai(
    config: DeepSeekConfig,
    states: list[InstrumentState],
    updates: list[MemoryUpdate],
) -> str:
    prompt = build_live_schedule_prompt(states, updates)
    return call_deepseek_text(config, prompt)


def build_live_schedule_prompt(
    states: list[InstrumentState],
    updates: list[MemoryUpdate],
) -> str:
    current_time = datetime.now(timezone.utc).isoformat()
    return f"""You are the live scheduling brain for a forex chart annotation agent.

Your job:
- Review the current market states.
- Decide whether each instrument should be checked sooner, later, alerted, ignored, or watched.
- Do not give trade instructions, stop loss, take profit, lot size, or execution advice.
- Focus only on monitoring intelligence: when to come back, what to watch, and why.
- Use human-like chart-monitoring behavior.
- Current UTC time is {current_time}. Judge next-check timestamps relative to this time.

Current scan:
{_states_text(states)}

Current rule-based schedule:
{_updates_text(updates)}

Return a concise review with:
1. Instruments needing immediate attention.
2. Instruments to revisit soon and suggested interval.
3. Instruments that are stale/low priority.
4. Any issue with the current rule-based schedule.
"""


def _states_text(states: list[InstrumentState]) -> str:
    lines: list[str] = []
    for state in states:
        lines.append(f"- {state.instrument}: {state.status} | {state.action}")
        if state.bias is not None:
            lines.append(f"  Bias: {state.bias.direction} ({state.bias.reason})")
        if state.primary_setup is not None:
            setup = state.primary_setup
            lines.append(
                "  Setup: "
                f"{setup.side.upper()}, {setup.status}, {setup.current_state}, "
                f"Q{setup.quality_score}, zone {setup.entry_zone.low:.5f}-{setup.entry_zone.high:.5f}"
            )
    return "\n".join(lines)


def _updates_text(updates: list[MemoryUpdate]) -> str:
    lines: list[str] = []
    for update in updates:
        distance = (
            "unknown"
            if update.distance_in_ranges is None
            else f"{update.distance_in_ranges:.2f} avg ranges"
        )
        lines.append(
            f"- {update.instrument}: next {update.next_check_time.isoformat()}, "
            f"session={update.session}, distance={distance}, reason={update.reason}, alert={update.alert or 'none'}"
        )
    return "\n".join(lines)
