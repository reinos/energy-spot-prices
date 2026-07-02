from __future__ import annotations

from .base import PriceProvider
from .dap_xadi import DapXadiProvider
from .entsoe import EntsoeProvider

PROVIDER_REGISTRY: dict[str, type[PriceProvider]] = {
    EntsoeProvider.provider_id: EntsoeProvider,
    DapXadiProvider.provider_id: DapXadiProvider,
}


def build_providers(
    provider_ids: list[str], provider_config: dict, period: str
) -> list[PriceProvider]:
    """Instantiate the configured providers, in order, from stored config.

    Providers that require credentials are skipped when their entry is absent
    from provider_config (i.e. the user left the API key blank during setup).
    Providers that need no credentials are always included.
    """
    providers = []
    for provider_id in provider_ids:
        provider_cls = PROVIDER_REGISTRY[provider_id]
        if provider_cls.requires_api_key and provider_id not in provider_config:
            continue
        providers.append(
            provider_cls(period=period, **provider_config.get(provider_id, {}))
        )
    return providers
