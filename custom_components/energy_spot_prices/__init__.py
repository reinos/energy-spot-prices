"""Energy Spot Prices component."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CALCULATION_MODE,
    CONF_AREA,
    CONF_ENERGY_SCALE,
    CONF_CALCULATION_MODE,
    CONF_MODIFYER,
    CONF_PROVIDER_CONFIG,
    CONF_VAT_VALUE,
    DEFAULT_MODIFYER,
    DEFAULT_ENERGY_SCALE,
    DOMAIN,
    CONF_PERIOD,
)
from .coordinator import EntsoeCoordinator
from .providers.registry import PROVIDER_REGISTRY, build_providers

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Energy Spot Prices component from a config entry."""

    # Initialise the coordinator and save it as domain-data
    area = entry.options[CONF_AREA]
    period = entry.options.get(CONF_PERIOD, "PT60M")
    energy_scale = entry.options.get(CONF_ENERGY_SCALE, DEFAULT_ENERGY_SCALE)
    modifyer = entry.options.get(CONF_MODIFYER, DEFAULT_MODIFYER)
    vat = entry.options.get(CONF_VAT_VALUE, 0)
    calculation_mode = entry.options.get(
        CONF_CALCULATION_MODE, CALCULATION_MODE["default"]
    )
    providers = build_providers(
        provider_ids=list(PROVIDER_REGISTRY.keys()),
        provider_config=entry.options.get(CONF_PROVIDER_CONFIG, {}),
        period=period,
    )
    entsoe_coordinator = EntsoeCoordinator(
        hass,
        providers=providers,
        area=area,
        period=period,
        energy_scale=energy_scale,
        modifyer=modifyer,
        calculation_mode=calculation_mode,
        VAT=vat,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entsoe_coordinator

    # Attempt an initial fetch, but don't block entity creation if it fails — the
    # coordinator retries internally (see fetch_prices) and entities should still
    # show up (as unavailable) rather than leaving the config entry stuck retrying.
    await entsoe_coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)
