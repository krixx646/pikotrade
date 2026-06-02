from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class OandaConfig:
    api_token: str
    account_id: str
    environment: str

    @property
    def base_url(self) -> str:
        if self.environment == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"


@dataclass(frozen=True)
class OpenAiConfig:
    api_key: str
    model: str


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    model: str
    base_url: str


@dataclass(frozen=True)
class OllamaConfig:
    model: str
    base_url: str
    timeout_seconds: int


@dataclass(frozen=True)
class GeminiConfig:
    api_keys: tuple[str, ...]
    model: str
    base_url: str
    timeout_seconds: int


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def load_oanda_config(env_path: Path | None = None) -> OandaConfig:
    values = load_dotenv(env_path or PROJECT_ROOT / ".env")

    api_token = values.get("OANDA_API_TOKEN", "")
    account_id = values.get("OANDA_ACCOUNT_ID", "")
    environment = values.get("OANDA_ENV", "practice").lower()

    missing = [
        name
        for name, value in (
            ("OANDA_API_TOKEN", api_token),
            ("OANDA_ACCOUNT_ID", account_id),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing OANDA config values: {', '.join(missing)}")

    if environment not in {"practice", "live"}:
        raise ValueError("OANDA_ENV must be either 'practice' or 'live'")

    return OandaConfig(
        api_token=api_token,
        account_id=account_id,
        environment=environment,
    )


def load_openai_config(env_path: Path | None = None) -> OpenAiConfig | None:
    values = load_dotenv(env_path or PROJECT_ROOT / ".env.openai")

    api_key = values.get("OPENAI_API_KEY", "")
    model = values.get("OPENAI_MODEL", "gpt-5.5")

    if not api_key or api_key == "replace_with_your_openai_api_key":
        return None

    return OpenAiConfig(api_key=api_key, model=model)


def load_deepseek_config(env_path: Path | None = None) -> DeepSeekConfig | None:
    values = load_dotenv(env_path or PROJECT_ROOT / ".env.deepseek")

    api_key = values.get("DEEPSEEK_API_KEY", "")
    model = values.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
    base_url = values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    if not api_key or api_key == "replace_with_your_deepseek_api_key":
        return None

    return DeepSeekConfig(
        api_key=api_key,
        model=model,
        base_url=base_url.rstrip("/"),
    )


def load_ollama_config(env_path: Path | None = None) -> OllamaConfig:
    values = load_dotenv(env_path or PROJECT_ROOT / ".env.ollama")
    model = values.get("OLLAMA_MODEL", "gemma3:4b")
    base_url = values.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    timeout_value = values.get("OLLAMA_TIMEOUT_SECONDS", "300")

    try:
        timeout_seconds = int(timeout_value)
    except ValueError:
        timeout_seconds = 300

    return OllamaConfig(
        model=model,
        base_url=base_url.rstrip("/"),
        timeout_seconds=timeout_seconds,
    )


def load_gemini_config(env_path: Path | None = None) -> GeminiConfig | None:
    """Free Gemini API config (gemini-3.1-flash-lite is free-tier).

    Supports multiple keys for rotation/failover: a comma-separated GEMINI_API_KEY
    and/or GEMINI_API_KEY_2..GEMINI_API_KEY_5. Returns None if no usable key.
    """
    values = load_dotenv(env_path or PROJECT_ROOT / ".env.gemini")
    model = values.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    base_url = values.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    timeout_value = values.get("GEMINI_TIMEOUT_SECONDS", "60")

    raw_keys: list[str] = list(values.get("GEMINI_API_KEY", "").split(","))
    for index in range(2, 6):
        raw_keys.append(values.get(f"GEMINI_API_KEY_{index}", ""))

    placeholder = "replace_with_your_gemini_api_key"
    seen: set[str] = set()
    api_keys: list[str] = []
    for raw in raw_keys:
        key = raw.strip()
        if not key or key == placeholder or key in seen:
            continue
        seen.add(key)
        api_keys.append(key)

    if not api_keys:
        return None

    try:
        timeout_seconds = int(timeout_value)
    except ValueError:
        timeout_seconds = 60

    return GeminiConfig(
        api_keys=tuple(api_keys),
        model=model,
        base_url=base_url.rstrip("/"),
        timeout_seconds=timeout_seconds,
    )


def load_gemma_reviewer_config(env_path: Path | None = None) -> "GeminiConfig | OllamaConfig":
    """The local-AI-reviewer ("Gemma") slot.

    Prefers the free Gemini API when GEMINI_API_KEY is set (cloud / 1GB-box friendly,
    no local model needed); falls back to local Ollama for offline dev.
    """
    gemini = load_gemini_config()
    if gemini is not None:
        return gemini
    return load_ollama_config(env_path)
