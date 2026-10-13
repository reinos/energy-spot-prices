from __future__ import annotations

from .base import PriceProvider
from .dap_xadi import DapXadiProvider
from .energy_charts import EnergyChartsProvider
from .entsoe import EntsoeProvider
from .nordpool import NordPoolProvider

# Insertion order is the fallback priority: the coordinator walks the providers
# in this order and stops as soon as the combined data is sufficient.
PROVIDER_REGISTRY: dict[str, type[PriceProvider]] = {
    EntsoeProvider.provider_id: EntsoeProvider,
    NordPoolProvider.provider_id: NordPoolProvider,
    EnergyChartsProvider.provider_id: EnergyChartsProvider,
    DapXadiProvider.provider_id: DapXadiProvider,
}


def build_providers(
    provider_ids: list[str], provider_config: dict, period: str, area: str
) -> list[PriceProvider]:
    """Instantiate the configured providers, in order, from stored config.

    Providers that require credentials are skipped when their entry is absent
    from provider_config (i.e. the user left the API key blank during setup).
    Providers that do not serve the given area (ENTSO-E area code) are skipped.
    Providers that need no credentials are otherwise always included.
    """
    providers = []
    for provider_id in provider_ids:
        provider_cls = PROVIDER_REGISTRY[provider_id]
        if provider_cls.requires_api_key and provider_id not in provider_config:
            continue
        if not provider_cls.supports_area(area):
            continue
        providers.append(
            provider_cls(period=period, **provider_config.get(provider_id, {}))
        )
    return providers
