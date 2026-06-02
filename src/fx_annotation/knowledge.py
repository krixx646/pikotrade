from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_FILES = [
    PROJECT_ROOT / "docs" / "strategy-knowledge-base.md",
    PROJECT_ROOT / "docs" / "forex-chart-annotation-agent.md",
]


def load_strategy_knowledge(paths: list[Path] | None = None) -> str:
    documents: list[str] = []

    for path in paths or DEFAULT_KNOWLEDGE_FILES:
        if not path.exists():
            continue
        documents.append(f"# Source: {path.name}\n\n{path.read_text(encoding='utf-8')}")

    return "\n\n---\n\n".join(documents)
