from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import ClientSession

from .base import PriceProvider, PriceProviderError

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices"

# Nord Pool delivery dates are expressed in CET/CEST, regardless of the area.
DELIVERY_TZ = ZoneInfo("Europe/Amsterdam")

# Map ENTSO-E area codes (as stored in AREA_INFO) to Nord Pool delivery areas.
AREA_MAP: dict[str, str] = {
    "AT": "AT",
    "BE": "BE",
    "BG": "BG",
    "DE_LU": "GER",
    "DK_1": "DK1",
    "DK_2": "DK2",
    "EE": "EE",
    "FI": "FI",
    "FR": "FR",
    "HR": "HR",
    "LT": "LT",
    "LV": "LV",
    "NL": "NL",
    "NO_1": "NO1",
    "NO_2": "NO2",
    "NO_3": "NO3",
    "NO_4": "NO4",
    "NO_5": "NO5",
    "PL": "PL",
    "SE_1": "SE1",
    "SE_2": "SE2",
    "SE_3": "SE3",
    "SE_4": "SE4",
}


class NordPoolProvider(PriceProvider):
    """Day-ahead electricity prices from the Nord Pool data portal (no API key required).

    Independent of ENTSO-E: prices come straight from the exchange, in EUR/MWh
    at the market resolution (15 minutes). One call is made per CET delivery
    date; dates that are not published yet return HTTP 204 and are skipped.
    """

    provider_id = "nordpool"
    name = "Nord Pool"
    requires_api_key = False
    supported_areas = frozenset(AREA_MAP)

    async def query_day_ahead_prices(
        self, area: str, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        delivery_area = AREA_MAP.get(area)
        if not delivery_area:
            raise PriceProviderError(f"Nord Pool does not support area '{area}'.")

        first_date = start.astimezone(DELIVERY_TZ).date()
        last_date = end.astimezone(DELIVERY_TZ).date()

        result: dict[datetime, float] = {}
        async with ClientSession() as session:
            delivery_date = first_date
            while delivery_date <= last_date:
                params = {
                    "date": delivery_date.isoformat(),
                    "market": "DayAhead",
                    "deliveryArea": delivery_area,
                    "currency": "EUR",
                }
                _LOGGER.debug(f"Fetching Nord Pool prices with params {params}")
                async with session.get(BASE_URL, params=params, raise_for_status=True) as resp:
                    payload = await resp.json() if resp.status != 204 else None

                for entry in (payload or {}).get("multiAreaEntries", []):
                    price = entry.get("entryPerArea", {}).get(delivery_area)
                    if price is None:
                        continue
                    ts = datetime.fromisoformat(entry["deliveryStart"].replace("Z", "+00:00"))
                    result[ts] = price

                delivery_date += timedelta(days=1)

        return self._to_configured_interval(result)
