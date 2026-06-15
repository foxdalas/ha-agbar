"""Shared base entity for the Agbar (Veolia) Water integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AgbarCoordinator


class AgbarEntity(CoordinatorEntity[AgbarCoordinator]):
    """Base entity: one device per contract, enriched from portal metadata."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AgbarCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        data = coordinator.data or {}
        self._contract = data.get("contract") or entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._contract)},
            name=f"Agbar {data.get('supply_address') or self._contract}",
            manufacturer=MANUFACTURER,
            model="Smart water meter" if data.get("smart_metering") else "Water meter",
            serial_number=data.get("meter_serial"),
            configuration_url="https://agbar.veolia.cat/es/group/sgab/inicio",
        )
