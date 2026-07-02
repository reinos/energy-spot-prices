from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from ..const import DEFAULT_PERIOD


class PriceProviderError(Exception):
    """Raised when a provider fails to retrieve or parse price data."""


class PriceProvider(ABC):
    """Base class for a dynamic electricity price data source."""

    provider_id: ClassVar[str]
    name: ClassVar[str]
    requires_api_key: ClassVar[bool] = False

    def __init__(self, period: str = DEFAULT_PERIOD) -> None:
        self.configuration_period = period

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
