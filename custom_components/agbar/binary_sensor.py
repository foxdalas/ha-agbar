"""Binary sensors for the Agbar (Veolia) Water integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AgbarCoordinator
from .entity import AgbarEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the leak binary sensor from a config entry."""
    coordinator: AgbarCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AgbarLeakSensor(coordinator, entry)])


class AgbarLeakSensor(AgbarEntity, BinarySensorEntity):
    """On when the portal reports an active WATER LEAK alarm."""

    _attr_translation_key = "water_leak"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: AgbarCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._contract}_water_leak"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("leak_active"))

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        return {
            "last_alarm_type": data.get("last_alarm_type"),
            "last_alarm_start": data.get("last_alarm_start"),
            "last_alarm_days": data.get("last_alarm_days"),
        }
