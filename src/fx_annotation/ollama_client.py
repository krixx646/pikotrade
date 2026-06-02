from urllib import error, request
import json

from fx_annotation.config import OllamaConfig


def call_ollama_text(config: OllamaConfig, prompt: str) -> str:
    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_ctx": 4096,
            "num_predict": 500,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{config.base_url}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    parsed = json.loads(body)
    text = parsed.get("response", "")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Ollama returned an empty response.")
    return text
