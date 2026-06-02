from pathlib import Path

from fx_annotation.live_memory import DEFAULT_ALERTS_PATH, load_memory


def render_alerts_markdown(path: Path = DEFAULT_ALERTS_PATH) -> str:
    alerts = load_memory(path)
    lines = [
        "# Alert Log",
        "",
    ]

    if not alerts:
        lines.append("No alerts recorded.")
        return "\n".join(lines) + "\n"

    for key, alert in sorted(alerts.items(), reverse=True):
        if not isinstance(alert, dict):
            continue
        lines.extend(
            [
                f"## {alert.get('instrument', key)}",
                "",
                f"- Created at: {alert.get('created_at', '')}",
                f"- Status: {alert.get('status', '')}",
                f"- Pair value: {alert.get('pair_value_label', 'UNVALIDATED PAIR')}",
                f"- Pair note: {alert.get('pair_value_note', '')}",
                f"- Alert: {alert.get('alert', '')}",
                f"- Latest price: {alert.get('latest_price', '')}",
                f"- Entry zone: {alert.get('entry_zone_low', '')} - {alert.get('entry_zone_high', '')}",
                "",
            ]
        )

    return "\n".join(lines) + "\n"
