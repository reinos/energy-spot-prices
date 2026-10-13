from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiohttp import ClientSession

from .base import PriceProvider, PriceProviderError

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.energy-charts.info/price"

# The start/end query parameters are interpreted as CET/CEST calendar dates.
DELIVERY_TZ = ZoneInfo("Europe/Amsterdam")

# Map ENTSO-E area codes (as stored in AREA_INFO) to Energy-Charts bidding zones.
# Only zones whose data is licensed CC BY 4.0 (Bundesnetzagentur | SMARD.de) are
# listed; data for the other zones is restricted to private and internal use.
AREA_MAP: dict[str, str] = {
    "AT": "AT",
    "BE": "BE",
    "CH": "CH",
    "CZ": "CZ",
    "DE_LU": "DE-LU",
    "DK_1": "DK1",
    "DK_2": "DK2",
    "FR": "FR",
    "HU": "HU",
    "IT_NORD": "IT-North",
    "NL": "NL",
    "NO_2": "NO2",
    "PL": "PL",
    "SE_4": "SE4",
    "SI": "SI",
}


class EnergyChartsProvider(PriceProvider):
    """Day-ahead electricity prices from Energy-Charts by Fraunhofer ISE (no API key required).

    Independent of ENTSO-E: prices are sourced from Bundesnetzagentur / SMARD.de
    and licensed CC BY 4.0. Returned in EUR/MWh at the market resolution
    (15 minutes). A single call covers the whole requested date range.
    Tomorrow's prices tend to appear later than at the exchange, so this provider
    ranks below Nord Pool. The API is rate limited, which is fine for the
    coordinator's fetch cadence.
    """

    provider_id = "energy_charts"
    name = "Energy-Charts"
    requires_api_key = False
    supported_areas = frozenset(AREA_MAP)

    async def query_day_ahead_prices(
        self, area: str, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        bidding_zone = AREA_MAP.get(area)
        if not bidding_zone:
            raise PriceProviderError(f"Energy-Charts does not support area '{area}'.")

        params = {
            "bzn": bidding_zone,
            "start": start.astimezone(DELIVERY_TZ).date().isoformat(),
            "end": end.astimezone(DELIVERY_TZ).date().isoformat(),
        }
        _LOGGER.debug(f"Fetching Energy-Charts prices with params {params}")
        async with ClientSession() as session:
            async with session.get(BASE_URL, params=params) as resp:
                if resp.status == 404:
                    # No prices published (yet) for the requested range.
                    return {}
                resp.raise_for_status()
                payload = await resp.json()

        unit = payload.get("unit", "")
        if unit.replace(" ", "") != "EUR/MWh":
            raise PriceProviderError(f"Energy-Charts returned unexpected unit '{unit}'.")

        result: dict[datetime, float] = {}
        for unix_seconds, price in zip(payload.get("unix_seconds", []), payload.get("price", [])):
            if price is None:
                continue
            result[datetime.fromtimestamp(unix_seconds, tz=timezone.utc)] = price

        return self._to_configured_interval(result)
