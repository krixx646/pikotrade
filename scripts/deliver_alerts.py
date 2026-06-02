import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from fx_annotation.config import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deliver rule and AI alert records.")
    parser.add_argument(
        "--channel",
        choices=["console", "windows-msg", "telegram"],
        default="console",
    )
    parser.add_argument(
        "--rule-alerts",
        default=str(PROJECT_ROOT / "outputs" / "alerts.json"),
    )
    parser.add_argument(
        "--ai-alerts",
        default=str(PROJECT_ROOT / "outputs" / "ai_alerts.json"),
    )
    parser.add_argument(
        "--gemma-alerts",
        default=str(PROJECT_ROOT / "outputs" / "gemma_alerts.json"),
    )
    parser.add_argument(
        "--env",
        default=str(PROJECT_ROOT / ".env.alerts"),
    )
    parser.add_argument(
        "--outbox",
        default=str(PROJECT_ROOT / "outputs" / "notification_outbox.md"),
    )
    parser.add_argument(
        "--delivered-state",
        default=str(PROJECT_ROOT / "outputs" / "delivered_alerts.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    delivered_state_path = Path(args.delivered_state)
    delivered = load_delivered_state(delivered_state_path)
    alert_items = load_alert_messages(Path(args.rule_alerts), "Rule route")
    alert_items.extend(load_alert_messages(Path(args.ai_alerts), "DeepSeek AI route"))
    alert_items.extend(load_alert_messages(Path(args.gemma_alerts), "Gemma AI route"))
    new_alerts = [
        item
        for item in alert_items
        if item["id"] not in delivered
    ]
    alerts = [item["message"] for item in new_alerts]

    outbox = Path(args.outbox)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text(render_outbox(alerts), encoding="utf-8")

    if not alerts:
        print("No alerts to deliver.")
        return 0

    for message in alerts:
        deliver(message, args.channel, Path(args.env))
    for item in new_alerts:
        delivered[str(item["id"])] = str(item["message"])
    save_delivered_state(delivered, delivered_state_path)

    print(f"Delivered {len(alerts)} alert(s) through {args.channel}.")
    print(f"Outbox: {outbox}")
    return 0


def load_alert_messages(path: Path, source: str) -> list[dict[str, str]]:
    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []

    messages: list[dict[str, str]] = []
    for key, alert in data.items():
        if not isinstance(alert, dict):
            continue
        text = str(alert.get("alert", "")).strip()
        instrument = str(alert.get("instrument", "")).strip()
        status = str(alert.get("status", "")).strip()
        pair_value = str(alert.get("pair_value_label", "")).strip()
        if text:
            pair_text = f" [{pair_value}]" if pair_value else ""
            messages.append(
                {
                    "id": f"{source}:{key}",
                    "message": f"{source}: {instrument} {status}{pair_text} - {text}",
                }
            )
    return messages


def load_delivered_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def save_delivered_state(delivered: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(delivered, indent=2, sort_keys=True), encoding="utf-8")


def deliver(message: str, channel: str, env_path: Path) -> None:
    if channel == "console":
        print(message)
        return
    if channel == "windows-msg":
        subprocess.run(["msg", "*", "/TIME:20", message], check=False)
        return
    if channel == "telegram":
        send_telegram(message, env_path)
        return
    raise ValueError(f"Unknown delivery channel: {channel}")


def send_telegram(message: str, env_path: Path) -> None:
    values = load_dotenv(env_path)
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = values.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ValueError("Telegram alerts require TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.alerts")

    payload = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    with urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, timeout=30):
        return


def render_outbox(alerts: list[str]) -> str:
    lines = ["# Notification Outbox", ""]
    if not alerts:
        lines.append("No alerts to deliver.")
        return "\n".join(lines) + "\n"
    for alert in alerts:
        lines.append(f"- {alert}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
