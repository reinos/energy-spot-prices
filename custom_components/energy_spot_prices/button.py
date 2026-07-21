"""Energy Spot Prices — refresh button entity."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import ATTRIBUTION, CONF_ENTITY_NAME, DOMAIN
from .coordinator import EntsoeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Energy Spot Prices button entry."""
    spot_coordinator = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities(
        [
            EntsoeRefreshButton(
                spot_coordinator, config_entry.options[CONF_ENTITY_NAME]
            )
        ]
    )


class EntsoeRefreshButton(ButtonEntity):
    """Button to manually refresh the Energy Spot Prices data."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: EntsoeCoordinator, name: str = "") -> None:
        """Initialize the button."""
        self.coordinator = coordinator

        if name not in (None, ""):
            self.entity_id = f"button.{slugify(name)}_refresh_prices"
            self._attr_unique_id = f"energy_spot_prices.{name}_refresh_prices"
            self._attr_name = f"Refresh prices ({name})"
        else:
            self.entity_id = "button.refresh_prices"
            self._attr_unique_id = "energy_spot_prices.refresh_prices"
            self._attr_name = "Refresh prices"

        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_energy_spot_prices")},
            manufacturer="Energy Spot Prices",
            model="",
            name="Energy Spot Prices" + ((" (" + name + ")") if name != "" else ""),
        )

    async def async_press(self) -> None:
        """Handle the button press by forcing a data refresh."""
        await self.coordinator.async_force_update()
