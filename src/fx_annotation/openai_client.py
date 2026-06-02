from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

from fx_annotation.config import OpenAiConfig


def call_openai_text(config: OpenAiConfig, prompt: str) -> str:
    payload = {
        "model": config.model,
        "input": prompt,
    }
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {error.code}: {body}") from error

    if "output_text" in data:
        return str(data["output_text"])

    return _extract_response_text(data)


def _extract_response_text(data: dict[str, object]) -> str:
    chunks: list[str] = []
    for output in data.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()
