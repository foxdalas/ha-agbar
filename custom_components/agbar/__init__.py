"""The Agbar (Veolia) Water integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .api import AgbarApiClient
from .const import DOMAIN, HISTORY_DAYS, PAGE_SIZE
from .coordinator import AgbarCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Agbar from a config entry."""
    client = AgbarApiClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        history_days=HISTORY_DAYS,
        page_size=PAGE_SIZE,
    )
    coordinator = AgbarCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
