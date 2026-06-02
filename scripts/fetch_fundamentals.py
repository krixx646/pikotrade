import argparse
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch RSS/Atom headlines into a fundamentals file for the AI route."
    )
    parser.add_argument(
        "--feeds",
        default=str(PROJECT_ROOT / "fundamental_feeds.txt"),
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs" / "fundamentals" / "latest.md"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    feeds = load_feeds(Path(args.feeds))
    headlines: list[dict[str, str]] = []

    for feed in feeds:
        try:
            headlines.extend(fetch_feed(feed))
        except (URLError, TimeoutError, ET.ParseError) as error:
            headlines.append(
                {
                    "title": f"Failed to fetch feed: {feed}",
                    "published": "",
                    "link": "",
                    "source": str(error),
                }
            )

    headlines = sorted(headlines, key=lambda item: item.get("published", ""), reverse=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_headlines(headlines[: args.limit]), encoding="utf-8")
    print(f"Fundamentals written: {output}")
    print(f"Headlines: {len(headlines[: args.limit])}")
    return 0


def load_feeds(path: Path) -> list[str]:
    if not path.exists():
        return []
    feeds: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            feeds.append(line)
    return feeds


def fetch_feed(url: str) -> list[dict[str, str]]:
    with urlopen(url, timeout=30) as response:
        content = response.read()

    root = ET.fromstring(content)
    items = root.findall(".//item")
    if items:
        return [_rss_item(item, url) for item in items]

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//atom:entry", namespace)
    return [_atom_entry(entry, url, namespace) for entry in entries]


def _rss_item(item: ET.Element, source: str) -> dict[str, str]:
    title = _child_text(item, "title")
    published = _normalize_date(_child_text(item, "pubDate"))
    link = _child_text(item, "link")
    return {"title": title, "published": published, "link": link, "source": source}


def _atom_entry(
    entry: ET.Element,
    source: str,
    namespace: dict[str, str],
) -> dict[str, str]:
    title = _child_text(entry, "atom:title", namespace)
    published = _normalize_date(
        _child_text(entry, "atom:updated", namespace)
        or _child_text(entry, "atom:published", namespace)
    )
    link_element = entry.find("atom:link", namespace)
    link = "" if link_element is None else str(link_element.attrib.get("href", ""))
    return {"title": title, "published": published, "link": link, "source": source}


def render_headlines(headlines: list[dict[str, str]]) -> str:
    lines = [
        "# Fundamental Headlines",
        "",
        "These headlines are context for DeepSeek analysis, not trade instructions.",
        "",
    ]
    if not headlines:
        lines.append("No feeds configured. Copy `fundamental_feeds.txt.example` to `fundamental_feeds.txt` and add RSS/Atom feed URLs.")
        return "\n".join(lines) + "\n"

    for headline in headlines:
        lines.extend(
            [
                f"## {headline['title']}",
                "",
                f"- Published: {headline['published'] or 'unknown'}",
                f"- Source: {headline['source']}",
                f"- Link: {headline['link'] or 'none'}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _child_text(
    element: ET.Element,
    tag: str,
    namespace: dict[str, str] | None = None,
) -> str:
    child = element.find(tag, namespace or {})
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value


if __name__ == "__main__":
    raise SystemExit(main())
