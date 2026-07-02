from __future__ import annotations

import re
import logging
from datetime import datetime, timezone

from aiohttp import ClientSession

from ..utils import average_to_interval, get_interval_minutes
from .base import PriceProvider, PriceProviderError

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://dap.xadi.eu/api"

# Map ENTSO-E area codes (as stored in AREA_INFO) to DAP Xadi country codes.
# Multi-zone countries (DK, SE, NO) are aggregated to a single country price
# by DAP Xadi; prices may differ from the specific ENTSO-E bidding zone.
AREA_MAP: dict[str, str] = {
    "NL": "nl",
    "BE": "be",
    "FR": "fr",
    "AT": "at",
    "CH": "ch",
    "DE_LU": "de",
    "DE": "de",
    "DK_1": "dk",
    "DK_2": "dk",
    "SE_1": "se",
    "SE_2": "se",
    "SE_3": "se",
    "SE_4": "se",
    "NO_1": "no",
    "NO_2": "no",
    "NO_3": "no",
    "NO_4": "no",
    "NO_5": "no",
}


class DapXadiProvider(PriceProvider):
    """Day-ahead electricity prices from dap.xadi.eu (ENTSO-E backed, no API key required).

    Returns priceMwh directly in EUR/MWh — no unit conversion needed.
    Two calls are made per fetch: /today (all 24h of today) and /next/48
    (next 48h from current UTC hour), merged into one result dict.
    """

    provider_id = "dap_xadi"
    name = "DAP Xadi"
    requires_api_key = False

    async def query_day_ahead_prices(
        self, area: str, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        country = AREA_MAP.get(area)
        if not country:
            raise PriceProviderError(
                f"DAP Xadi does not support area '{area}'. "
                f"Supported areas: {', '.join(sorted(AREA_MAP))}."
            )

        # Convert "PT60M" → "60M", "PT15M" → "15M" for the ?interval= param.
        interval = re.sub(r"^PT", "", self.configuration_period)
        params = {"interval": interval}
        expected_interval = get_interval_minutes(self.configuration_period)

        result: dict[datetime, float] = {}
        async with ClientSession() as session:
            for endpoint in ("today", "next/48"):
                url = f"{BASE_URL}/{country}/{endpoint}"
                _LOGGER.debug(f"Fetching DAP Xadi prices from {url} (interval={interval})")
                async with session.get(url, params=params, raise_for_status=True) as resp:
                    payload = await resp.json()

                for point in payload.get("data", []):
                    ts = datetime.fromisoformat(point["time"].replace("Z", "+00:00"))
                    result[ts] = point["priceMwh"]

        if not result:
            return result

        # Defensive averaging: if the API returned a different resolution than configured,
        # average into the expected buckets (mirrors the pattern in EntsoeProvider).
        timestamps = sorted(result.keys())
        if len(timestamps) >= 2:
            actual_interval = int(
                (timestamps[1] - timestamps[0]).total_seconds() / 60
            )
            if actual_interval != expected_interval:
                _LOGGER.debug(
                    f"DAP Xadi returned {actual_interval}m intervals, "
                    f"averaging into {expected_interval}m buckets."
                )
                result = average_to_interval(result, expected_interval=expected_interval)

        return result
