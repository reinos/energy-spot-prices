from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from ..const import DEFAULT_PERIOD
from ..utils import average_to_interval, get_interval_minutes

_LOGGER = logging.getLogger(__name__)


class PriceProviderError(Exception):
    """Raised when a provider fails to retrieve or parse price data."""


class PriceProvider(ABC):
    """Base class for a dynamic electricity price data source."""

    provider_id: ClassVar[str]
    name: ClassVar[str]
    requires_api_key: ClassVar[bool] = False
    # ENTSO-E area codes (as stored in AREA_INFO) this provider can serve.
    # None means every area is supported.
    supported_areas: ClassVar[frozenset[str] | None] = None

    def __init__(self, period: str = DEFAULT_PERIOD) -> None:
        self.configuration_period = period

    @classmethod
    def supports_area(cls, area: str) -> bool:
        return cls.supported_areas is None or area in cls.supported_areas

    @abstractmethod
    async def query_day_ahead_prices(
        self, area: str, start: datetime, end: datetime
    ) -> dict[datetime, float]:
        """
        Return day-ahead prices for the given area and time range.

        The result maps tz-aware, period-start timestamps to the raw market
        price in EUR/MWh, before energy-scale conversion, VAT or the user's
        price modifier template are applied.
        """

    def _to_configured_interval(
        self, result: dict[datetime, float]
    ) -> dict[datetime, float]:
        """Average the result into the configured period if the API returned a finer resolution."""
        result = dict(sorted(result.items()))
        timestamps = list(result)
        if len(timestamps) < 2:
            return result

        expected_interval = get_interval_minutes(self.configuration_period)
        actual_interval = int((timestamps[1] - timestamps[0]).total_seconds() / 60)
        if actual_interval == expected_interval:
            return result

        _LOGGER.debug(
            f"{self.name} returned {actual_interval}m intervals, "
            f"averaging into {expected_interval}m buckets."
        )
        return average_to_interval(result, expected_interval=expected_interval)
