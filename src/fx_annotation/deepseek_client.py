from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request, urlopen
from pathlib import Path
import base64
import json

from fx_annotation.config import DeepSeekConfig


def call_deepseek_text(
    config: DeepSeekConfig,
    prompt: str,
    json_mode: bool = False,
    image_paths: list[Path] | None = None,
) -> str:
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": "You are a concise forex chart-monitoring review assistant.",
            },
            {
                "role": "user",
                "content": _user_content(prompt, image_paths or []),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 1800 if json_mode else 900,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = Request(
        f"{config.base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API error {error.code}: {body}") from error
    except (TimeoutError, URLError, ConnectionError) as error:
        raise RuntimeError(f"DeepSeek request failed or timed out: {error}") from error

    return _extract_chat_text(data, json_mode=json_mode)


def _user_content(prompt: str, image_paths: list[Path]) -> object:
    if not image_paths:
        return prompt
    content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    for path in image_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _data_url(path)},
            }
        )
    return content


def _data_url(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def _extract_chat_text(data: dict[str, object], json_mode: bool = False) -> str:
    choices = data.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        if len(text) > 3:
            return text
    if json_mode:
        return ""

    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str):
        text = reasoning_content.strip()
        if text:
            return text

    return content.strip() if isinstance(content, str) else ""
