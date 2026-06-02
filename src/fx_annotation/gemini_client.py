from urllib import error, request
import json

from fx_annotation.config import GeminiConfig


# HTTP codes that mean "this key is rate/quota limited" -> try the next key.
_ROTATE_CODES = {429, 403}


def call_gemini_text(config: GeminiConfig, prompt: str) -> str:
    """Call the free Gemini API (generateContent) and return the text response.

    Uses only stdlib urllib (no extra deps) so it runs on tiny/free compute.
    Forces JSON output to match the Gemma route's parser. If multiple API keys
    are configured, rotates to the next key on a quota/rate-limit error.
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 800,
            "responseMimeType": "application/json",
        },
    }
    data = json.dumps(payload).encode("utf-8")
    url = f"{config.base_url}/models/{config.model}:generateContent"

    last_error = "no API keys configured"
    total = len(config.api_keys)
    for index, api_key in enumerate(config.api_keys):
        is_last = index == total - 1
        req = request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=config.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
            last_error = f"key #{index + 1} -> HTTP {exc.code}: {detail[:200]}"
            if exc.code in _ROTATE_CODES and not is_last:
                continue
            raise RuntimeError(f"Gemini request failed ({last_error})") from exc
        except error.URLError as exc:
            last_error = f"key #{index + 1} -> {exc}"
            if not is_last:
                continue
            raise RuntimeError(f"Gemini request failed ({last_error})") from exc

        parsed = json.loads(body)
        text = _extract_text(parsed)
        if not text.strip():
            raise RuntimeError(f"Gemini returned an empty response: {body[:300]}")
        return text

    raise RuntimeError(f"All Gemini keys exhausted ({last_error})")


def _extract_text(parsed: dict) -> str:
    for candidate in parsed.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        joined = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if joined.strip():
            return joined
    return ""
