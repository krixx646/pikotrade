from datetime import datetime
from socket import timeout as SocketTimeout
from time import sleep
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from fx_annotation.candles import Candle
from fx_annotation.config import OandaConfig


class OandaClient:
    def __init__(self, config: OandaConfig) -> None:
        self.config = config

    def fetch_candles(
        self,
        instrument: str,
        granularity: str,
        count: int = 300,
        price: str = "M",
    ) -> list[Candle]:
        query = urlencode(
            {
                "granularity": granularity,
                "count": str(count),
                "price": price,
            }
        )
        url = f"{self.config.base_url}/v3/instruments/{instrument}/candles?{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.config.api_token}",
                "Accept-Datetime-Format": "RFC3339",
            },
            method="GET",
        )

        payload = _read_json_with_retries(request, timeout=30)

        return [self._parse_candle(item) for item in payload.get("candles", [])]

    def fetch_candles_range(
        self,
        instrument: str,
        granularity: str,
        from_time: datetime,
        to_time: datetime,
        price: str = "M",
    ) -> list[Candle]:
        query = urlencode(
            {
                "granularity": granularity,
                "from": _format_oanda_time(from_time),
                "to": _format_oanda_time(to_time),
                "price": price,
            }
        )
        url = f"{self.config.base_url}/v3/instruments/{instrument}/candles?{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.config.api_token}",
                "Accept-Datetime-Format": "RFC3339",
            },
            method="GET",
        )

        payload = _read_json_with_retries(request, timeout=60)

        return [self._parse_candle(item) for item in payload.get("candles", [])]

    @staticmethod
    def _parse_candle(item: dict[str, Any]) -> Candle:
        mid = item["mid"]
        return Candle(
            time=_parse_oanda_time(item["time"]),
            open=float(mid["o"]),
            high=float(mid["h"]),
            low=float(mid["l"]),
            close=float(mid["c"]),
            volume=int(item["volume"]),
            complete=bool(item["complete"]),
        )


def _parse_oanda_time(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    timestamp, _, offset = normalized.partition("+")
    if "." in timestamp:
        date_part, fraction = timestamp.split(".", 1)
        timestamp = f"{date_part}.{fraction[:6]}"

    if offset:
        return datetime.fromisoformat(f"{timestamp}+{offset}")
    return datetime.fromisoformat(timestamp)


def _format_oanda_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _read_json_with_retries(request: Request, timeout: int, attempts: int = 4) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, SocketTimeout, URLError) as error:
            last_error = error
            if attempt == attempts - 1:
                break
            sleep(2 ** attempt)
    if last_error is not None:
        raise last_error
    return {}
