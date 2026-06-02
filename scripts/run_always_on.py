import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
AI_SCHEDULE_PATH = PROJECT_ROOT / "outputs" / "ai_schedule_state.json"
DEEPSEEK_DISABLED = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the forex chart agent as a managed local loop."
    )
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--use-ai", action="store_true", help="Deprecated; DeepSeek is disabled to stop API spend.")
    parser.add_argument("--use-gemma", action="store_true")
    parser.add_argument("--no-chart-images", action="store_true")
    parser.add_argument("--no-forward-test", action="store_true")
    parser.add_argument("--ai-limit", type=int, default=3)
    parser.add_argument("--gemma-limit", type=int, default=1)
    parser.add_argument(
        "--deepseek-minutes",
        type=int,
        default=30,
        help="Minimum minutes between DeepSeek runs when --use-ai is enabled.",
    )
    parser.add_argument("--alert-channel", default="console", choices=["console", "windows-msg", "telegram"])
    parser.add_argument(
        "--whatsapp-push",
        action="store_true",
        help="Push forward-test signal changes to WhatsApp via the PicoClaw token-free /send endpoint. "
        "Also enabled when PICOTRADE_WHATSAPP_PUSH=1.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        run_cycle(args)
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


def run_cycle(args: argparse.Namespace) -> None:
    fundamentals_path = PROJECT_ROOT / "outputs" / "fundamentals" / "latest.md"
    run_command(["scripts/fetch_fundamentals.py"])

    monitor_command = [
        "scripts/live_monitor.py",
        "--fundamentals-file",
        str(fundamentals_path),
        "--ai-limit",
        str(args.ai_limit),
        "--gemma-limit",
        str(args.gemma_limit),
    ]
    deepseek_due = args.use_ai and not DEEPSEEK_DISABLED and _deepseek_due(args.deepseek_minutes)
    if deepseek_due:
        monitor_command.append("--use-ai")
    if args.use_gemma:
        monitor_command.append("--use-gemma")
    if args.no_chart_images:
        monitor_command.append("--no-chart-images")
    run_command(monitor_command)
    if deepseek_due:
        _mark_deepseek_run()

    run_command(["scripts/deliver_alerts.py", "--channel", args.alert_channel])
    if not args.no_forward_test:
        run_command(["scripts/forward_test_signals.py"])
        if args.whatsapp_push or os.environ.get("PICOTRADE_WHATSAPP_PUSH") == "1":
            run_command(["scripts/whatsapp_push.py"])
    run_command(["scripts/export_orchestration_state.py"])
    run_command(["scripts/export_tradingview_pine.py"])


def run_command(command: list[str]) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR)
    full_command = [sys.executable, *command]
    print(f"Running: {' '.join(full_command)}")
    subprocess.run(full_command, cwd=PROJECT_ROOT, env=env, check=False)


def _deepseek_due(minutes: int) -> bool:
    if minutes <= 0:
        return True
    state = _load_schedule_state()
    last_run = _parse_time(str(state.get("last_deepseek_run", "")))
    if last_run is None:
        return True
    return datetime.now(timezone.utc) - last_run >= timedelta(minutes=minutes)


def _mark_deepseek_run() -> None:
    state = _load_schedule_state()
    state["last_deepseek_run"] = datetime.now(timezone.utc).isoformat()
    AI_SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AI_SCHEDULE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _load_schedule_state() -> dict[str, object]:
    if not AI_SCHEDULE_PATH.exists():
        return {}
    try:
        value = json.loads(AI_SCHEDULE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
